# -*- coding: utf-8 -*-
"""定期数据库维护：碎片 VACUUM + 可选数据归档清理。

设计要点
--------
1. VACUUM 并非万能：SQLite 只有存在空闲页(freelist)时才可回收文件体积。
   当前业务库 freelist=0，说明空间被真实数据占用，此时 VACUUM 无收益，
   因此按「空闲页占比 + 可回收体积」双阈值判断，避免无谓的全库重建。
2. 数据归档：仅当环境变量 DM_ARCHIVE_DAYS=N（>0）时启用，把 N 天前的
   data_record 行导出为 JSON 快照（instance/archive/）后删除，批次标记
   archived。默认关闭 —— 本系统定位为历史数据保存，删除需显式开启。
3. 后台守护线程每 24h 检查一次；VACUUM 与业务写连接冲突时捕获异常并
   记日志（下次再试），不影响系统正常运行。
"""
import logging
import os
import shutil
import sqlite3
import threading
import time

logger = logging.getLogger('db_maintenance')

VACUUM_FREE_RATIO = 0.20      # 空闲页占比阈值
VACUUM_MIN_RECLAIM_MB = 200   # 最小可回收体积阈值（MB）
VACUUM_BUSY_TIMEOUT_MS = 180000  # VACUUM 等待锁超时（3 分钟），避免瞬时 BUSY 直接失败


def get_db_path():
    """返回当前 SQLite 数据库文件路径。"""
    from data_management_service import get_db_path as _gp
    return _gp()


def _db_pragma(name):
    conn = sqlite3.connect(get_db_path())
    try:
        cur = conn.execute(f"PRAGMA {name}")
        return cur.fetchone()[0]
    finally:
        conn.close()


def db_stats():
    """返回数据库维护状态指标。"""
    path = get_db_path()
    page_size = int(_db_pragma("page_size"))
    page_count = int(_db_pragma("page_count"))
    freelist = int(_db_pragma("freelist_count"))
    size_mb = os.path.getsize(path) / 1024 / 1024
    reclaim_mb = freelist * page_size / 1024 / 1024
    return {
        "path": path,
        "file_mb": round(size_mb, 1),
        "page_size": page_size,
        "page_count": page_count,
        "freelist_count": freelist,
        "reclaimable_mb": round(reclaim_mb, 1),
        "free_ratio": round(freelist / page_count, 4) if page_count else 0,
    }


def vacuum_if_needed(force=False):
    """空闲页占比达阈值时执行 VACUUM 回收文件体积（C-7 低风险加固）。

    加固点：
    1. 显式设置 busy_timeout，避免瞬时写锁导致的 SQLITE_BUSY 直接失败；
    2. 执行前探测数据库是否空闲，若非空闲（有活跃写事务）则本轮跳过，下次再试；
    3. VACUUM 期间保持 WAL 模式不变，缩小对在线读请求的阻塞面。
    """
    stats = db_stats()
    reclaimable = stats["reclaimable_mb"]
    ratio = stats["free_ratio"]

    # 增量模式（auto_vacuum=2，手动回收一次后生效）：空闲页可直接走
    # incremental_vacuum 秒级回收，不再等阈值、不做整库重建
    if stats["freelist_count"] > 0 and _db_pragma("auto_vacuum") == 2:
        conn2 = sqlite3.connect(get_db_path(), isolation_level=None)
        try:
            conn2.execute(f"PRAGMA busy_timeout={VACUUM_BUSY_TIMEOUT_MS}")
            while True:
                conn2.execute("PRAGMA incremental_vacuum(4000)")
                if int(conn2.execute("PRAGMA freelist_count").fetchone()[0]) <= 0:
                    break
            after = os.path.getsize(get_db_path()) / 1024 / 1024
            logger.warning("增量回收完成: %.1fMB -> %.1fMB (回收 %.1fMB)",
                           stats["file_mb"], round(after, 1),
                           round(stats["file_mb"] - after, 1))
            return {"skipped": False, "mode": "incremental",
                    "before_mb": stats["file_mb"], "after_mb": round(after, 1)}
        except Exception as e:
            logger.warning("增量回收失败(稍后重试): %s", e)
            return {"error": str(e), **stats}
        finally:
            conn2.close()

    if not force and not (ratio >= VACUUM_FREE_RATIO
                          and reclaimable >= VACUUM_MIN_RECLAIM_MB):
        logger.info("VACUUM 跳过: freelist=%.2f%% 可回收=%.1fMB "
                    "(阈值 %d%% / %dMB)", ratio * 100, reclaimable,
                    VACUUM_FREE_RATIO * 100, VACUUM_MIN_RECLAIM_MB)
        return {"skipped": True, **stats}

    conn = sqlite3.connect(get_db_path(), isolation_level=None)
    try:
        # 加固 1：给足等待锁的时间，避免瞬时 BUSY 直接判失败
        conn.execute(f"PRAGMA busy_timeout={VACUUM_BUSY_TIMEOUT_MS}")
        # 加固 2：探测是否有其他连接占用（拿不到 IMMEDIATE 写锁说明库正被写）
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("COMMIT")
        except sqlite3.OperationalError as e:
            logger.info("VACUUM 跳过: 数据库正忙(%s)，下一周期重试", e)
            return {"skipped": True, "reason": "db_busy", **stats}

        logger.warning("VACUUM 开始: 文件 %.1fMB, 可回收 %.1fMB",
                       stats["file_mb"], reclaimable)
        conn.execute("VACUUM")
        after = os.path.getsize(get_db_path()) / 1024 / 1024
        logger.warning("VACUUM 完成: %.1fMB -> %.1fMB (回收 %.1fMB)",
                       stats["file_mb"], round(after, 1),
                       round(stats["file_mb"] - after, 1))
        return {"skipped": False, "before_mb": stats["file_mb"],
                "after_mb": round(after, 1), **stats}
    except Exception as e:
        logger.warning("VACUUM 失败(稍后重试): %s", e)
        return {"error": str(e), **stats}
    finally:
        conn.close()


def archive_cleanup(days=None):
    """把 N 天前的 data_record 导出为 JSON 快照后删除（批次标记 archived）。

    days: 来自环境变量 DM_ARCHIVE_DAYS；未开启时直接返回 skipped。
    """
    days = days if days is not None else int(os.environ.get("DM_ARCHIVE_DAYS", "0"))
    if days <= 0:
        return {"skipped": True, "reason": "未启用(DM_ARCHIVE_DAYS<=0)"}
    import json
    import datetime
    import shutil
    from collections import defaultdict
    from sqlalchemy import text
    from db import db
    from models.data_management import DataRecord, DataUploadBatch
    from sqlalchemy import text

    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    arch_dir = os.path.join(os.path.dirname(get_db_path()), "archive")
    os.makedirs(arch_dir, exist_ok=True)

    # M-10：不再一次性 .all() 全量驻留内存，改为 yield_per 流式分批聚合
    base_q = DataRecord.query.filter(DataRecord.record_date < cutoff)
    if base_q.first() is None:
        return {"skipped": True, "reason": f"早于 {cutoff} 无记录", "rows": 0}

    by_batch = defaultdict(list)
    for r in base_q.yield_per(1000):
        by_batch[r.batch_id].append({
            "record_date": r.record_date, "category": r.category,
            "record_key": r.record_key, "keys_json": r.keys_json,
            "values_json": r.values_json, "source_file": r.source_file,
            "row_no": r.row_no, "uploaded_at": r.uploaded_at.isoformat(),
        })

    file_no = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    total = 0
    for batch_id, items in by_batch.items():
        batch = db.session.get(DataUploadBatch, batch_id) if batch_id is not None else None
        snapshot = {
            "batch_id": batch_id,
            "batch_no": batch.batch_no if batch else None,
            "file_name": batch.file_name if batch else None,
            "file_type": batch.file_type if batch else None,
            "category": batch.category if batch else None,
            "archived_at": datetime.datetime.now().isoformat(),
            "row_count": len(items),
            "rows": items,
        }
        fname = os.path.join(arch_dir, f"arch_{file_no}_{batch_id}.json")
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(snapshot, f, ensure_ascii=False)
        db.session.execute(
            text("DELETE FROM data_record WHERE batch_id = :bid"), {"bid": batch_id}
        )
        if batch:
            batch.row_count = 0
            batch.status = "archived"
            batch.error_log = (batch.error_log or "") + \
                f"\n[归档 {file_no}] {len(items)} 行已导出 {os.path.basename(fname)}"
        total += len(items)
    db.session.commit()
    logger.warning("归档完成: %d 天前 %d 行 -> %s(%d 个快照文件)",
                   days, total, arch_dir, len(by_batch))
    return {"skipped": False, "cutoff": cutoff, "rows": total,
            "archives": len(by_batch), "dir": arch_dir}


def maintenance_pass():
    """一次例行维护：先归档清理（可选），后 VACUUM（按阈值）。"""
    result = {"archive": archive_cleanup(), "vacuum": vacuum_if_needed()}
    db_path = get_db_path()
    result["after_mb"] = round(os.path.getsize(db_path) / 1024 / 1024, 1)
    logger.info("例行维护完成: %s", result)
    return result


# ---------------------------------------------------------------------------
# 手动空闲页回收（页面按钮触发，后台线程 + 进度轮询）
# ---------------------------------------------------------------------------
# 本库 auto_vacuum=0，incremental_vacuum 依赖指针映射页，对它是空操作——
# 唯一可靠的回收手段是整库 VACUUM。VACUUM 是单条语句，无法按页报进度，
# 这里用 set_progress_handler 做心跳，前端展示已用时间。
# 顺带在同一次 VACUUM 中切换 auto_vacuum=2（增量模式）：今后空闲页可用
# incremental_vacuum 秒级回收，不再依赖整库重建。
_VAC_LOCK = threading.Lock()
_VACUUM_STATE = {
    'running': False,
    'percent': 0,
    'message': '',
    'success': None,          # None=进行中/未开始, True/False=已结束
    'error': '',
    'started_at': None,
    'finished_at': None,
    'before_mb': None,
    'after_mb': None,
    'reclaimed_mb': None,
    'elapsed_sec': 0,
}


def _set_vacuum_state(**kwargs):
    with _VAC_LOCK:
        _VACUUM_STATE.update(kwargs)


def get_vacuum_progress():
    """返回当前回收进度快照（供前端轮询）。"""
    with _VAC_LOCK:
        return dict(_VACUUM_STATE)


def vacuum_now_with_progress(app):
    """后台执行整库 VACUUM 回收空闲页，进度写入 _VACUUM_STATE。

    流程：统计回收前状态 → 磁盘空间预检 → VACUUM（含切 auto_vacuum=2）→ 汇总。
    与自动维护的判断逻辑解耦：手动触发即强制执行（用户已明确确认）。
    """
    from utils.time_utils import get_utc8_time, format_utc8_time
    t0 = time.time()
    _set_vacuum_state(running=True, percent=0, success=None, error='',
                      started_at=format_utc8_time(get_utc8_time()),
                      finished_at=None, message='正在统计空闲页…',
                      before_mb=None, after_mb=None, reclaimed_mb=None,
                      elapsed_sec=0)
    conn = None
    try:
        with app.app_context():
            stats = db_stats()
            db_path = get_db_path()
        before_mb = stats['file_mb']
        _set_vacuum_state(before_mb=before_mb)
        if stats['freelist_count'] <= 0:
            _set_vacuum_state(running=False, success=True, percent=100,
                              after_mb=before_mb, reclaimed_mb=0,
                              message='没有空闲页，无需回收',
                              finished_at=format_utc8_time(get_utc8_time()))
            return True

        # VACUUM 需要约 1 倍库大小的临时磁盘空间，空间不足时提前失败
        free_disk = shutil.disk_usage(os.path.dirname(db_path)).free
        need = int(stats['file_mb'] * 1024 * 1024 * 1.1)
        if free_disk < need:
            raise RuntimeError(
                f'磁盘剩余空间不足：VACUUM 需要约 {stats["file_mb"]}MB 临时空间，'
                f'当前仅剩 {free_disk / 1024 / 1024:.0f}MB')

        conn = sqlite3.connect(db_path, isolation_level=None)
        # 等待锁 60s：在线业务写入时让出重试窗口，避免瞬时 BUSY 直接失败
        conn.execute("PRAGMA busy_timeout=60000")

        # 心跳：VACUUM 执行期间周期性刷新"已用时间"，证明任务活着
        def _beat():
            _set_vacuum_state(
                elapsed_sec=int(time.time() - t0),
                message=f'正在重建数据库文件… 已用 {int(time.time() - t0)} 秒'
                        f'（大库可能需几分钟，请耐心等待）')
            return 0
        conn.set_progress_handler(_beat, 100000)

        # 同一次重建顺带切换增量回收模式，今后回收空闲页无需整库 VACUUM
        try:
            conn.execute("PRAGMA auto_vacuum=2")
        except sqlite3.Error:
            pass   # 个别环境不支持切换时退回普通回收，不影响本次效果
        _set_vacuum_state(message='正在重建数据库文件…（请耐心等待）')
        conn.execute("VACUUM")
        conn.set_progress_handler(None, 0)
        # VACUUM 后主库可能还挂着 WAL，先把 WAL 落回再测量，回收量才准确
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass

        after_mb = round(os.path.getsize(db_path) / 1024 / 1024, 1)
        reclaimed = round(before_mb - after_mb, 1)
        logger.warning("手动空闲页回收完成: %.1fMB -> %.1fMB (回收 %.1fMB, 用时 %.0fs)",
                       before_mb, after_mb, reclaimed, time.time() - t0)
        _set_vacuum_state(running=False, success=True, percent=100,
                          after_mb=after_mb, reclaimed_mb=reclaimed,
                          elapsed_sec=int(time.time() - t0),
                          message=f'回收完成：{before_mb}MB → {after_mb}MB，'
                                  f'释放 {reclaimed}MB，用时 {int(time.time() - t0)} 秒',
                          finished_at=format_utc8_time(get_utc8_time()))
        return True
    except Exception as e:
        logger.warning("手动空闲页回收失败: %s", e)
        _set_vacuum_state(running=False, success=False,
                          error=f'{type(e).__name__}: {e}',
                          message=f'回收失败：{e}',
                          finished_at=format_utc8_time(get_utc8_time()))
        return False
    finally:
        if conn is not None:
            conn.close()


def start_vacuum_async(app):
    """启动后台回收线程，立即返回。已有任务在跑时抛 RuntimeError。"""
    with _VAC_LOCK:
        if _VACUUM_STATE.get('running'):
            raise RuntimeError('已有回收任务正在执行，请等待完成')

    def _worker():
        vacuum_now_with_progress(app)

    t = threading.Thread(target=_worker, daemon=True, name='db_vacuum')
    t.start()
    return t


def start_maintenance_thread(app=None, interval_hours=24, delay_seconds=600):
    """启动后台守护线程（每 24h 一次例行维护）。"""
    import threading

    def _run():
        try:
            with app.app_context():
                maintenance_pass()
        except Exception as e:
            logger.warning("例行维护异常(下一周期重试): %s", e)

    def _loop():
        import time
        time.sleep(delay_seconds)  # 启动后错峰，避免影响启动预热
        while True:
            try:
                _run()
            finally:
                time.sleep(interval_hours * 3600)

    t = threading.Thread(target=_loop, name="db-maintenance", daemon=True)
    t.start()
    logger.info("后台维护线程已启动(周期 %dh, 首次 %ds 后)",
                interval_hours, delay_seconds)
    return t