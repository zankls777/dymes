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
import sqlite3

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