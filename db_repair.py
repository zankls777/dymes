#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生产环境数据库一键检测修复脚本（大焱资料管理系统）

功能流程：
  1. 环境检查  —— Python 版本、项目根目录、生产配置加载（.env.production 自动识别，
                  不覆盖已有环境变量）、数据库连通性、磁盘空间、生产服务存活探测；
  2. 数据备份  —— 修复前强制完整备份，备份后立即校验完整性并记录 SHA256。
                  · 服务运行中：采用 SQLite online backup API 分页式备份（每批主动让出锁，
                    不长期阻塞生产读写；若并发写入导致反复重启则中止并给出低峰期/停服指引）；
                  · 服务已停止：VACUUM INTO 一致性快照（最快、最干净）；
                  · MySQL：mysqldump --single-transaction 在线一致备份（无 mysqldump 时逻辑导出）；
  3. 异常检测  —— quick_check 快速完整性校验、外键一致性、
                  缺失表 / 缺失列 / 缺失索引（对照 SQLAlchemy 模型元数据）、
                  孤儿外键数据、重复用户名等（检测为只读，轻量，不影响生产）；
  4. 安全修复  —— 仅执行“增量、幂等、可回滚”的结构修复：
                  补建缺失表（create_all）、补列（ALTER TABLE ADD COLUMN，存在性判断）、
                  补索引（checkfirst）；数据类异常仅报告不自动删除；
                  每个修复步骤独立事务，失败自动回滚；
  5. 修复后验证 —— 重新执行完整性校验与结构比对，逐表核对行数与修复前一致；
  6. 审计产物  —— 全程日志 logs/db_repair_<时间戳>.log + 备份清单 JSON（可追踪、可审计）。

用法（部署后在项目根目录直接执行，自动使用生产环境数据库配置，无需改配置）：
  python db_repair.py              # 完整流程：备份+深度检测+修复+验证（修复前需确认）
  python db_repair.py --check      # 快速体检：仅结构元数据检测（只读、不备份、秒级、零生产影响）
  python db_repair.py --check --thorough  # 体检 + 全库完整性扫描（只读、不备份，大库数分钟）
  python db_repair.py --yes        # 跳过交互确认（定时任务/自动化场景）
  python db_repair.py --vacuum     # 修复后对 SQLite 执行 VACUUM 回收空间
  python db_repair.py --deep       # 对备份文件执行完整 integrity_check（比 quick_check 更严格）
  python db_repair.py --force-vacuum  # 服务运行中也强制使用 VACUUM INTO 备份（低峰期使用）
  python db_repair.py --rebuild    # SQLite 严重损坏时：.recover 逻辑重建库（需显式指定）

生产操作建议：修复类操作请在业务低峰期执行；如条件允许，先停服（deploy_start.sh stop /
python manage.py stop）再执行修复，备份与修复速度最快且零并发干扰。

退出码：0=健康或全部修复并验证通过；2=存在未修复问题；3=致命错误（备份失败/严重损坏等）。
"""

import argparse
import hashlib
import importlib
import json
import logging
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import datetime
from pathlib import Path

# ---------------------------------------------------------------- 常量
PROJECT_ROOT = Path(__file__).resolve().parent
BACKUP_DIR = PROJECT_ROOT / 'backups' / 'db_repair'
LOG_DIR = PROJECT_ROOT / 'logs'
RUN_TS = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')

# 修复结果状态
ST_OK = 'OK'          # 正常 / 已修复
ST_FIXED = 'FIXED'    # 发现问题并修复成功
ST_FAIL = 'FAIL'      # 修复失败
ST_MANUAL = 'MANUAL'  # 数据类异常，仅报告，需人工处理


# ---------------------------------------------------------------- 日志
def setup_logger():
    """同时输出到控制台与日志文件，日志文件随备份长期保留用于审计。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f'db_repair_{RUN_TS}.log'
    logger = logging.getLogger('db_repair')
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', '%Y-%m-%d %H:%M:%S')
    fh = logging.FileHandler(log_file, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger, log_file


log = None  # 类型: logging.Logger，main() 中初始化


# ---------------------------------------------------------------- 环境准备
def load_env_file():
    """加载 .env.production（或 ENV_FILE 指定文件），仅填充未设置的变量，
    保证部署后直接使用生产配置，同时真实环境变量优先级最高。"""
    env_path = os.environ.get('ENV_FILE')
    candidates = [Path(env_path)] if env_path else [
        PROJECT_ROOT / '.env.production', PROJECT_ROOT / '.env'
    ]
    for p in candidates:
        if p and p.exists():
            loaded = 0
            with open(p, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or '=' not in line:
                        continue
                    key, val = line.split('=', 1)
                    key, val = key.strip(), val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
                        loaded += 1
            return p, loaded
    return None, 0


def mask_uri(uri: str) -> str:
    """日志中隐藏数据库密码。"""
    try:
        from sqlalchemy.engine.url import make_url
        u = make_url(uri)
        if u.password:
            return uri.replace(':' + u.password + '@', ':******@')
    except Exception:
        pass
    return uri


def probe_service_live():
    """探测生产 Web 服务是否在运行（TCP 连接本机端口，默认 5004）。"""
    port = int(os.environ.get('PORT', os.environ.get('WEB_PORT', 5004)))
    for host in ('127.0.0.1',):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1.5)
        try:
            sock.connect((host, port))
            return True, port
        except OSError:
            pass
        finally:
            sock.close()
    return False, port


def preflight_checks(app):
    """执行前环境检查，返回 (db_type, db_path, service_live)。任何致命问题直接抛异常终止。"""
    log.info('=' * 60)
    log.info('【1/5】执行前环境检查')
    log.info('=' * 60)

    # Python 版本
    py_ver = sys.version.split()[0]
    if sys.version_info < (3, 8):
        raise RuntimeError(f'Python 版本过低: {py_ver}，需要 3.8+')
    log.info('Python 版本: %s（可执行文件: %s）', py_ver, sys.executable)

    # 工作目录切换到项目根，保证相对路径与生产运行一致
    os.chdir(PROJECT_ROOT)
    log.info('项目根目录: %s', PROJECT_ROOT)

    # 数据库配置
    uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
    db_type = 'mysql' if str(uri).lower().startswith('mysql') else 'sqlite'
    log.info('FLASK_CONFIG=%s，数据库类型: %s', os.environ.get('FLASK_CONFIG'), db_type)
    log.info('数据库 URI: %s', mask_uri(uri))

    # 生产服务存活探测（决定备份策略与风险提示）
    service_live, port = probe_service_live()
    if service_live:
        log.warning('⚠ 检测到生产服务正在运行（端口 %s）。检测只读不受影响；'
                    '修复/备份将采用在线方式并主动让出数据库锁，仍建议在业务低峰期执行，'
                    '或先停服（python manage.py stop / deploy_start.sh stop）。', port)
    else:
        log.info('生产服务未运行（端口 %s 无监听）：可执行最快的一致性备份与修复。', port)

    # 连通性
    from sqlalchemy import text
    from db import db
    with app.app_context():
        db.session.execute(text('SELECT 1'))
        db.session.commit()
    log.info('数据库连通性: 正常')

    db_path = None
    if db_type == 'sqlite':
        from sqlalchemy.engine.url import make_url
        db_path = os.path.abspath(make_url(uri).database)
        if not os.path.exists(db_path):
            raise RuntimeError(f'SQLite 数据库文件不存在: {db_path}')
        size_mb = os.path.getsize(db_path) / 1024 / 1024
        wal = Path(db_path + '-wal')
        log.info('数据库文件: %s（%.1f MB）', db_path, size_mb)
        if wal.exists():
            log.info('检测到 WAL 文件: %s（%.1f MB），热备份将自动包含其已提交内容',
                     wal.name, wal.stat().st_size / 1024 / 1024)
        # 磁盘空间：至少预留 2 倍库文件大小
        free_gb = shutil.disk_usage(os.path.dirname(db_path)).free / 1024 ** 3
        need_gb = max(0.5, os.path.getsize(db_path) * 2 / 1024 ** 3)
        log.info('磁盘可用空间: %.2f GB（备份至少需要 %.2f GB）', free_gb, need_gb)
        if free_gb < need_gb:
            raise RuntimeError(f'磁盘空间不足：可用 {free_gb:.2f} GB < 需要 {need_gb:.2f} GB')
    else:
        with app.app_context():
            ver = db.session.execute(text('SELECT VERSION()')).scalar()
        log.info('MySQL 服务版本: %s', ver)
        free_gb = shutil.disk_usage(str(BACKUP_DIR)).free / 1024 ** 3
        log.info('备份目录磁盘可用空间: %.2f GB', free_gb)

    log.info('环境检查通过。')
    return db_type, db_path, service_live


# ---------------------------------------------------------------- 备份
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _vacuum_into_backup(db_path: str, backup_path: Path) -> None:
    """VACUUM INTO 一致性快照：单语句、无并发写入重启问题、产出独立可移植文件。
    注意：执行期间为持续读事务，在大库+活跃服务场景下可能影响服务响应，故仅在
    服务已停止（或显式 --force-vacuum）时使用。"""
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        conn.execute('PRAGMA busy_timeout=120000')
        posix_path = backup_path.as_posix().replace("'", "''")  # 路径为 SQL 字面量
        conn.execute(f"VACUUM INTO '{posix_path}'")
    finally:
        conn.close()


def _online_paged_backup(db_path: str, backup_path: Path) -> None:
    """分页式 online backup API：每复制一批页主动 sleep 让出数据库锁，
    是 SQLite 官方推荐的“服务在线”备份方式。

    生产写入并发时，源库变更会导致备份页复制回退重启；本函数检测进度回退，
    超过阈值或总时长上限即中止，避免长时间挂起拖垮服务。
    """
    import time
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(str(backup_path))
        try:
            src.execute('PRAGMA busy_timeout=120000')
            start_ts = time.monotonic()
            max_done = [0]      # 已复制页数的历史最大值
            restarts = [0]      # 进度回退次数
            last_pct = [-1]

            def _progress(_status, remaining, total):
                done = total - remaining if total else 0
                if done < max_done[0] - 1:
                    restarts[0] += 1   # 源库被写入，复制回退
                max_done[0] = max(max_done[0], done)
                pct = int(done * 100 / total) if total else 100
                if pct >= last_pct[0] + 10:
                    last_pct[0] = pct
                    log.info('在线备份进度: %d%%（并发回退 %d 次）', pct, restarts[0])
                elapsed = time.monotonic() - start_ts
                if restarts[0] > 200 or elapsed > 900:
                    log.error('在线备份在并发写入下反复回退（%d 次，耗时 %.0f 秒），中止备份',
                              restarts[0], elapsed)
                    return 1  # 中止
                return 0

            # pages=4000（约 16MB/批）+ 每批 sleep，批量与让出锁之间平衡
            src.backup(dst, pages=4000, progress=_progress, sleep=0.5)
        finally:
            dst.close()
    finally:
        src.close()


def backup_sqlite(db_path: str, service_live: bool, deep: bool = False,
                  force_vacuum: bool = False) -> dict:
    """SQLite 一致性备份，按服务存活自动选择策略：
      · 服务运行中：分页式 online backup（主动让锁，对生产影响最小）；
      · 服务已停止：VACUUM INTO（最快、最干净）；
      · --force-vacuum：运行中也用 VACUUM INTO（仅建议低峰期）。
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f'{Path(db_path).name}.{RUN_TS}.bak'
    sqlite_ver = tuple(int(x) for x in sqlite3.sqlite_version.split('.'))
    log.info('开始 SQLite 一致性备份: %s（SQLite 引擎 %s）', backup_path.name, sqlite3.sqlite_version)

    use_vacuum = force_vacuum or not service_live
    if use_vacuum and sqlite_ver < (3, 27, 0):
        use_vacuum = False
        log.warning('SQLite 版本低于 3.27，VACUUM INTO 不可用，改用分页式在线备份')

    if use_vacuum:
        if service_live:
            log.warning('服务运行中仍使用 VACUUM INTO（--force-vacuum），备份期间服务响应可能变慢')
        else:
            log.info('服务已停止，采用 VACUUM INTO 一致性快照（最快）')
        _vacuum_into_backup(db_path, backup_path)
        used_method = 'VACUUM INTO'
    else:
        log.info('服务运行中，采用分页式在线备份（每批主动让出数据库锁，最小化生产影响）')
        try:
            _online_paged_backup(db_path, backup_path)
        except sqlite3.Error as e:
            try:
                if backup_path.exists():
                    backup_path.unlink()
            except OSError:
                pass
            raise RuntimeError(
                '在线备份未能在生产写入并发下完成。请在业务低峰期重试，'
                '或先停止服务（python manage.py stop / deploy_start.sh stop）后再执行修复。'
                f'（底层错误: {e}）')
        used_method = 'online backup API'

    # ---- 备份后立即校验（备份为独立文件，不触碰生产库） ----
    if not backup_path.exists() or backup_path.stat().st_size == 0:
        raise RuntimeError(f'备份文件未生成或为空: {backup_path}')
    chk = sqlite3.connect(str(backup_path))
    try:
        chk.execute('PRAGMA busy_timeout=60000')
        pragma = 'PRAGMA integrity_check' if deep else 'PRAGMA quick_check'
        rows = chk.execute(pragma).fetchall()
        ok = (len(rows) == 1 and rows[0][0] == 'ok')
        tbls = [r[0] for r in chk.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()]
        counts = {t: chk.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tbls}
    finally:
        chk.close()
    if not ok:
        raise RuntimeError(f'备份文件 {pragma} 校验失败，备份不可信: {rows[:3]}')
    size = backup_path.stat().st_size
    digest = sha256_file(backup_path)
    log.info('备份完成（方式: %s）: %.1f MB，校验 %s，SHA256=%s...，含 %d 张表',
             used_method, size / 1024 / 1024, pragma.split()[1], digest[:16], len(tbls))
    return {'file': str(backup_path), 'size_bytes': size, 'sha256': digest,
            'check': pragma.split()[1], 'tables': len(tbls), 'row_counts': counts,
            'method': used_method}


def backup_mysql(app) -> dict:
    """MySQL 备份：优先 mysqldump（--single-transaction 在线一致备份）；
    不可用时回退为 SQLAlchemy 逻辑导出（表结构 + 分批数据）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = BACKUP_DIR / f'mysqldump_{RUN_TS}.sql'
    from sqlalchemy.engine.url import make_url
    from sqlalchemy import text
    from db import db
    u = make_url(app.config['SQLALCHEMY_DATABASE_URI'])

    mysqldump = shutil.which('mysqldump')
    if mysqldump:
        log.info('使用 mysqldump 进行在线一致性备份...')
        env = dict(os.environ, MYSQL_PWD=u.password or '')
        cmd = [mysqldump, '-h', u.host or 'localhost', '-P', str(u.port or 3306),
               '-u', u.username or 'root',
               '--single-transaction', '--routines', '--events', '--triggers',
               '--set-gtid-purged=OFF', '--default-character-set=utf8mb4',
               u.database]
        with open(backup_path, 'wb') as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, env=env)
        if r.returncode != 0:
            raise RuntimeError(f'mysqldump 失败: {r.stderr.decode("utf-8", "ignore")[:500]}')
    else:
        from sqlalchemy import inspect as sa_inspect
        log.warning('未找到 mysqldump，回退为 SQLAlchemy 逻辑导出（大库耗时较长）...')
        with app.app_context():
            inspector = sa_inspect(db.engine)
            tables = inspector.get_table_names()
            with open(backup_path, 'w', encoding='utf-8') as f:
                f.write(f'-- BOM3 logical backup {RUN_TS}\nSET FOREIGN_KEY_CHECKS=0;\n')
                for t in tables:
                    create_sql = db.session.execute(text(f'SHOW CREATE TABLE `{t}`')).fetchone()[1]
                    f.write(f'\nDROP TABLE IF EXISTS `{t}`;\n{create_sql};\n\n')
                    cols = [c['name'] for c in inspector.get_columns(t)]
                    collist = ','.join(f'`{c}`' for c in cols)
                    offset, batch = 0, 1000
                    while True:
                        rows = db.session.execute(text(
                            f'SELECT {collist} FROM `{t}` LIMIT {batch} OFFSET {offset}')).fetchall()
                        if not rows:
                            break
                        for row in rows:
                            vals = []
                            for v in row:
                                if v is None:
                                    vals.append('NULL')
                                elif isinstance(v, (int, float)):
                                    vals.append(str(v))
                                elif isinstance(v, bytes):
                                    vals.append("X'" + v.hex() + "'")
                                else:
                                    vals.append("'" + str(v).replace('\\', '\\\\').replace("'", "''") + "'")
                            f.write(f'INSERT INTO `{t}` ({collist}) VALUES ({",".join(vals)});\n')
                        offset += batch
                    log.debug('逻辑导出: %s 完成（%d 行起）', t, offset)
                f.write('\nSET FOREIGN_KEY_CHECKS=1;\n')

    digest = sha256_file(backup_path)
    size = backup_path.stat().st_size
    log.info('备份完成: %.1f MB，SHA256=%s...', size / 1024 / 1024, digest[:16])
    return {'file': str(backup_path), 'size_bytes': size, 'sha256': digest, 'method':
            'mysqldump' if mysqldump else 'logical_export'}


# ---------------------------------------------------------------- 模型元数据
def import_all_models():
    """导入全部模型，确保 db.metadata 覆盖所有表（含动态生成的 finished_product）。"""
    modules = [
        'models.user_info', 'models.permission_info', 'models.vehicle_type',
        'models.file_info', 'models.data_management', 'models.corrugated_location',
        'models.sub_list', 'models.usage_list', 'models.material_summary',
        'models.sheet_calculation_result', 'models.tape_calculation_result',
        'models.tube_calculation_result', 'models.lot_login_info',
        'models.invest_record', 'models.wire_scan_record', 'models.operation_log',
    ]
    for m in modules:
        importlib.import_module(m)
    try:
        from models.finished_product import get_finished_product_model
        get_finished_product_model()
    except Exception as e:
        log.warning('finished_product 动态模型导入失败（不影响其他检查）: %s', e)


# ---------------------------------------------------------------- 检测
def snapshot_row_counts(app, tables):
    """逐表记录行数快照（修复前后必须一致）。"""
    from sqlalchemy import text
    from db import db
    counts = {}
    with app.app_context():
        for t in tables:
            try:
                counts[t] = db.session.execute(text(f'SELECT COUNT(*) FROM "{t}"'
                    if str(app.config['SQLALCHEMY_DATABASE_URI']).startswith('sqlite')
                    else f'SELECT COUNT(*) FROM `{t}`')).scalar()
            except Exception as e:
                counts[t] = None
                log.warning('行数快照失败 %s: %s', t, e)
    return counts


def detect_issues(app, db_type, thorough=False):
    """只读检测，返回问题清单与快照数据。

    thorough=False（默认 --check）：仅做元数据级结构比对（缺表/缺列/缺索引）与
    小表数据检查，秒级完成、几乎不产生 I/O 压力，不影响生产服务。
    thorough=True（修复模式或 --thorough）：追加全库 quick_check / foreign_key_check /
    孤儿外键扫描与逐表行数快照（大库只读扫描，需数分钟，建议低峰期）。
    """
    from sqlalchemy import inspect, text
    from db import db
    issues = []  # {kind, target, detail, severity}

    with app.app_context():
        engine = db.engine
        inspector = inspect(engine)

        # ---------- 1. 完整性校验（仅深度模式；全库扫描，I/O 开销大） ----------
        if thorough:
            if db_type == 'sqlite':
                rows = db.session.execute(text('PRAGMA quick_check')).fetchall()
                messages = [r[0] for r in rows]
                if not (len(messages) == 1 and messages[0] == 'ok'):
                    issues.append({'kind': 'corruption', 'target': 'database',
                                   'detail': f'quick_check 异常: {messages[:5]}',
                                   'severity': 'critical'})
                else:
                    log.info('[检测] quick_check: ok')
                fk_bad = db.session.execute(text('PRAGMA foreign_key_check')).fetchall()
                if fk_bad:
                    grouped = {}
                    for r in fk_bad:
                        grouped[r[0]] = grouped.get(r[0], 0) + 1
                    issues.append({'kind': 'fk_violation', 'target': ','.join(grouped),
                                   'detail': f'外键约束违例 {len(fk_bad)} 行: {grouped}',
                                   'severity': 'warning'})
                else:
                    log.info('[检测] foreign_key_check: 无违例')
            else:
                for t in inspector.get_table_names():
                    try:
                        res = db.session.execute(text(f'CHECK TABLE `{t}`')).fetchall()
                        bad = [r for r in res if r[3] not in ('OK', 'Table is already up to date')]
                        for r in bad:
                            issues.append({'kind': 'corruption', 'target': t,
                                           'detail': f'CHECK TABLE: {r[1]} {r[3]} {r[2]}',
                                           'severity': 'critical' if r[2] == 'Error' else 'warning'})
                    except Exception as e:
                        log.warning('[检测] CHECK TABLE %s 失败: %s', t, e)
        else:
            log.info('[检测] 快速模式：跳过全库完整性扫描（修复模式或加 --thorough 启用）')

        # ---------- 2. 结构差异：缺失表 / 列 / 索引（元数据比对，秒级） ----------
        existing_tables = set(inspector.get_table_names())
        meta_tables = set(db.metadata.tables.keys())
        missing_tables = meta_tables - existing_tables
        for t in sorted(missing_tables):
            issues.append({'kind': 'missing_table', 'target': t,
                           'detail': '模型定义的表在数据库中不存在', 'severity': 'error'})
        if not missing_tables:
            log.info('[检测] 表完整性: %d 张模型表均存在', len(meta_tables))

        missing_cols = []   # (table, column_obj)
        missing_indexes = []  # index 对象
        for tname in sorted(meta_tables & existing_tables):
            meta_tbl = db.metadata.tables[tname]
            actual_cols = {c['name'] for c in inspector.get_columns(tname)}
            for col in meta_tbl.columns:
                if col.name not in actual_cols:
                    missing_cols.append((tname, col))
            actual_idx = {i['name'] for i in inspector.get_indexes(tname)}
            for idx in meta_tbl.indexes:
                if idx.name not in actual_idx:
                    missing_indexes.append(idx)
        for tname, col in missing_cols:
            issues.append({'kind': 'missing_column', 'target': f'{tname}.{col.name}',
                           'detail': f'列类型 {col.type}，nullable={col.nullable}',
                           'severity': 'error'})
        for idx in missing_indexes:
            issues.append({'kind': 'missing_index', 'target': idx.name,
                           'detail': f'表 {idx.table.name} 上的模型索引缺失',
                           'severity': 'warning'})
        log.info('[检测] 结构差异: 缺表 %d、缺列 %d、缺索引 %d',
                 len(missing_tables), len(missing_cols), len(missing_indexes))

        # ---------- 3. 数据类异常（仅报告，不自动修改） ----------
        q = dialect_quote(db_type)
        # 3.1 孤儿外键：模型中指向 user_info 的外键，逐条核对（全表 COUNT/JOIN，仅深度模式）
        if thorough:
            for tname in sorted(meta_tables & existing_tables):
                actual_cols = {c['name'] for c in inspector.get_columns(tname)}
                for fk in db.metadata.tables[tname].foreign_keys:
                    if fk.column.table.name != 'user_info':
                        continue
                    col = fk.parent.name
                    if col not in actual_cols or 'id' not in {c['name'] for c in inspector.get_columns('user_info')}:
                        continue
                    sql = (f'SELECT COUNT(*) FROM {q(tname)} a LEFT JOIN {q("user_info")} b '
                           f'ON a.{q(col)}=b.{q("id")} WHERE a.{q(col)} IS NOT NULL AND b.{q("id")} IS NULL')
                    try:
                        n = db.session.execute(text(sql)).scalar()
                        if n:
                            issues.append({'kind': 'orphan_fk', 'target': f'{tname}.{col}',
                                           'detail': f'{n} 行引用了不存在的用户 ID（需人工处理，不自动删除）',
                                           'severity': 'warning'})
                    except Exception as e:
                        log.warning('[检测] 孤儿外键检查失败 %s.%s: %s', tname, col, e)
        # 3.2 重复用户名 / admin 账户（user_info 为小表，始终检查）
        if 'user_info' in existing_tables:
            dup = db.session.execute(text(
                f'SELECT username, COUNT(*) c FROM {q("user_info")} '
                f'GROUP BY username HAVING c > 1')).fetchall()
            for username, c in dup:
                issues.append({'kind': 'dup_data', 'target': 'user_info.username',
                               'detail': f'用户名 {username!r} 重复 {c} 次（需人工处理）',
                               'severity': 'warning'})
            admin_n = db.session.execute(text(
                f'SELECT COUNT(*) FROM {q("user_info")} WHERE username = :u'),
                {'u': 'admin'}).scalar()
            if not admin_n:
                issues.append({'kind': 'missing_admin', 'target': 'user_info',
                               'detail': '默认管理员 admin 不存在（应用启动时会自动创建）',
                               'severity': 'info'})

        # 行数快照仅在深度模式采集（修复后逐表比对需要）
        row_counts = snapshot_row_counts(app, sorted(existing_tables)) if thorough else {}

    log.info('[检测] 共发现 %d 个问题（详见日志；模式: %s）',
             len(issues), '深度' if thorough else '快速')
    for i in issues:
        log.info('  - [%s/%s] %s: %s', i['severity'], i['kind'], i['target'], i['detail'])
    return issues, row_counts


def dialect_quote(db_type):
    ch = '`' if db_type == 'mysql' else '"'
    return lambda name: f'{ch}{name}{ch}'


# ---------------------------------------------------------------- 修复
def compile_add_column(col, dialect) -> str:
    """根据模型列定义生成 ALTER TABLE ADD COLUMN 语句片段（幂等前置于存在性判断）。"""
    q = lambda n: dialect.identifier_preparer.quote(n)
    type_sql = col.type.compile(dialect=dialect)
    parts = [f'ADD COLUMN {q(col.name)} {type_sql}']
    # 默认值：server_default 优先；Python 端标量 default 次之
    default_sql = None
    if col.server_default is not None:
        arg = col.server_default.arg
        default_sql = str(arg.text if hasattr(arg, 'text') else arg)
    elif col.default is not None and not callable(getattr(col.default, 'arg', None)):
        arg = col.default.arg
        if isinstance(arg, bool):
            default_sql = '1' if arg else '0'
        elif isinstance(arg, (int, float)):
            default_sql = str(arg)
        elif isinstance(arg, str):
            default_sql = "'" + arg.replace("'", "''") + "'"
    if default_sql is not None:
        parts.append(f'DEFAULT {default_sql}')
    # NOT NULL 仅在有默认值时添加，否则放宽为 NULL（安全增量补列，不阻断修复）
    if not col.nullable and default_sql is not None:
        parts.append('NOT NULL')
    return ' '.join(parts)


def repair_issues(app, db_type, issues):
    """执行安全修复。每个步骤独立事务，失败回滚并记录；数据类问题仅报告。"""
    from sqlalchemy import inspect, text
    from db import db
    results = []

    critical = [i for i in issues if i['severity'] == 'critical']
    if critical:
        for i in critical:
            results.append({**i, 'action': '未修复（严重损坏）', 'status': ST_MANUAL})
        log.error('检测到数据库严重损坏，跳过结构修复以防二次损害。请使用备份或 --rebuild 处理。')
        return results

    with app.app_context():
        engine = db.engine
        dialect = engine.dialect

        # ---- 1. 缺失表：create_all 仅创建不存在的表（幂等） ----
        if any(i['kind'] == 'missing_table' for i in issues):
            try:
                db.metadata.create_all(engine)  # checkfirst=True，只补缺表
                db.session.commit()
                fixed = [i['target'] for i in issues if i['kind'] == 'missing_table']
                log.info('[修复] 补建缺失表 %d 张: %s', len(fixed), ', '.join(fixed))
                results.extend([{**i, 'action': 'CREATE TABLE', 'status': ST_FIXED}
                                for i in issues if i['kind'] == 'missing_table'])
            except Exception as e:
                db.session.rollback()
                log.error('[修复] 补建缺失表失败，已回滚: %s', e)
                results.extend([{**i, 'action': 'CREATE TABLE 失败', 'status': ST_FAIL}
                                for i in issues if i['kind'] == 'missing_table'])

        # ---- 2. 缺失列：逐列 ALTER TABLE ADD COLUMN（存在性判断 + 独立事务） ----
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        for tname in sorted(meta_tables_of(issues) & existing_tables):
            meta_tbl = db.metadata.tables[tname]
            actual_cols = {c['name'] for c in inspector.get_columns(tname)}
            q = lambda n: dialect.identifier_preparer.quote(n)
            for col in list(meta_tbl.columns):
                issue = next((i for i in issues
                              if i['kind'] == 'missing_column' and i['target'] == f'{tname}.{col.name}'), None)
                if not issue or col.name in actual_cols:
                    continue
                try:
                    frag = compile_add_column(col, dialect)
                    db.session.execute(text(f'ALTER TABLE {q(tname)} {frag}'))
                    db.session.commit()  # SQLite DDL 事务内可回滚；MySQL DDL 隐式提交但已做存在性判断
                    log.info('[修复] 补列: %s.%s（%s）', tname, col.name, col.type)
                    results.append({**issue, 'action': f'ALTER TABLE {tname} ADD COLUMN {col.name}',
                                    'status': ST_FIXED})
                except Exception as e:
                    db.session.rollback()
                    log.error('[修复] 补列失败 %s.%s，已回滚: %s', tname, col.name, e)
                    results.append({**issue, 'action': 'ADD COLUMN 失败', 'status': ST_FAIL})

        # ---- 3. 缺失索引：模型索引 checkfirst 创建（幂等，失败不影响数据） ----
        for tname in sorted(meta_tables_of(issues) & existing_tables):
            for idx in db.metadata.tables[tname].indexes:
                issue = next((i for i in issues if i['kind'] == 'missing_index'
                              and i['target'] == idx.name), None)
                if not issue:
                    continue
                try:
                    idx.create(bind=engine, checkfirst=True)
                    log.info('[修复] 补索引: %s（表 %s）', idx.name, tname)
                    results.append({**issue, 'action': f'CREATE INDEX {idx.name}',
                                    'status': ST_FIXED})
                except Exception as e:
                    db.session.rollback()
                    log.error('[修复] 补索引失败 %s: %s', idx.name, e)
                    results.append({**issue, 'action': 'CREATE INDEX 失败', 'status': ST_FAIL})

        # ---- 4. 数据类问题：仅登记报告，不自动删除/修改业务数据 ----
        for i in issues:
            if i['kind'] in ('fk_violation', 'orphan_fk', 'dup_data', 'missing_admin'):
                results.append({**i, 'action': '仅报告（需人工核实处理）', 'status': ST_MANUAL})

        # ---- 5. SQLite 收尾：WAL 检查点（可选 VACUUM） ----
        if db_type == 'sqlite':
            try:
                db.session.execute(text('PRAGMA wal_checkpoint(TRUNCATE)'))
                db.session.commit()
                log.info('[修复] WAL 检查点已执行')
            except Exception as e:
                db.session.rollback()
                log.warning('[修复] WAL 检查点失败（非致命）: %s', e)

    return results


def meta_tables_of(issues):
    from db import db
    return set(db.metadata.tables.keys())


def vacuum_sqlite(app, db_path):
    """VACUUM 重建数据库文件回收空间（SQLite 专属，须在事务外执行）。"""
    from db import db
    with app.app_context():
        db.engine.dispose()  # 释放连接池，避免文件锁
    conn = sqlite3.connect(db_path, isolation_level=None)
    try:
        before = os.path.getsize(db_path) / 1024 / 1024
        conn.execute('VACUUM')
        after = os.path.getsize(db_path) / 1024 / 1024
        log.info('[VACUUM] 完成: %.1f MB -> %.1f MB', before, after)
    finally:
        conn.close()


# ---------------------------------------------------------------- 严重损坏重建（显式 --rebuild）
def rebuild_sqlite(db_path: str):
    """使用 sqlite3 CLI .recover 逻辑重建严重损坏的库（最大限度抢救数据）。"""
    sqlite3_cli = shutil.which('sqlite3')
    if not sqlite3_cli:
        raise RuntimeError('未找到 sqlite3 命令行工具，无法执行 .recover 重建；'
                           '请安装 sqlite3 或使用备份文件恢复')
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    recover_sql = BACKUP_DIR / f'recover_{ts}.sql'
    rebuilt_path = Path(db_path + f'.rebuilt-{ts}')

    log.warning('开始 .recover 逻辑重建（抢救模式），恢复 SQL 将保存至: %s', recover_sql)
    with open(recover_sql, 'wb') as f:
        r = subprocess.run([sqlite3_cli, db_path, '.recover'], stdout=f,
                           stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f'.recover 执行失败: {r.stderr.decode("utf-8", "ignore")[:500]}')
    with open(rebuilt_path, 'wb') as f:
        r = subprocess.run([sqlite3_cli, str(rebuilt_path)],
                           stdin=open(recover_sql, 'rb'), stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError(f'重建库导入失败: {r.stderr.decode("utf-8", "ignore")[:500]}')

    chk = sqlite3.connect(str(rebuilt_path))
    try:
        rows = chk.execute('PRAGMA integrity_check').fetchall()
        ok = (len(rows) == 1 and rows[0][0] == 'ok')
    finally:
        chk.close()
    if not ok:
        raise RuntimeError('重建后 integrity_check 仍未通过，请人工介入')

    # 交换：原库改名保留（不删除），新库就位
    corrupt_path = Path(db_path + f'.corrupt-{ts}')
    for suffix in ('-wal', '-shm'):
        p = Path(db_path + suffix)
        if p.exists():
            p.rename(Path(db_path + suffix + f'.corrupt-{ts}'))
    shutil.move(db_path, corrupt_path)
    shutil.move(str(rebuilt_path), db_path)
    log.warning('重建完成。损坏原库保留为: %s（确认无误后可手动删除）', corrupt_path)


# ---------------------------------------------------------------- 修复后验证
def post_verify(app, db_type, issues, row_counts_before):
    """修复后完整性验证：结构差异清零、完整性校验通过、行数与修复前一致。"""
    from sqlalchemy import inspect, text
    from db import db
    log.info('=' * 60)
    log.info('【5/5】修复后完整性验证')
    log.info('=' * 60)
    checks = []

    with app.app_context():
        inspector = inspect(db.engine)

        # 1. 完整性校验（生产库 quick_check，轻量；深度校验对备份文件执行）
        if db_type == 'sqlite':
            rows = db.session.execute(text('PRAGMA quick_check')).fetchall()
            ok = len(rows) == 1 and rows[0][0] == 'ok'
            checks.append(('quick_check', ok, 'ok' if ok else str(rows[:5])))
            fk_bad = db.session.execute(text('PRAGMA foreign_key_check')).fetchall()
            checks.append(('foreign_key_check', len(fk_bad) == 0,
                           '无违例' if not fk_bad else f'{len(fk_bad)} 行违例'))
        else:
            bad_total = 0
            for t in inspector.get_table_names():
                res = db.session.execute(text(f'CHECK TABLE `{t}`')).fetchall()
                bad_total += sum(1 for r in res if r[2] == 'Error')
            checks.append(('CHECK TABLE', bad_total == 0, f'{bad_total} 个错误'))

        # 2. 结构差异应清零
        existing_tables = set(inspector.get_table_names())
        meta_tables = set(db.metadata.tables.keys())
        miss_t = meta_tables - existing_tables
        miss_c, miss_i = 0, 0
        for tname in sorted(meta_tables & existing_tables):
            actual_cols = {c['name'] for c in inspector.get_columns(tname)}
            miss_c += sum(1 for c in db.metadata.tables[tname].columns if c.name not in actual_cols)
            actual_idx = {i['name'] for i in inspector.get_indexes(tname)}
            miss_i += sum(1 for idx in db.metadata.tables[tname].indexes if idx.name not in actual_idx)
        checks.append(('缺失表', not miss_t, f'{len(miss_t)} 张缺失' if miss_t else '无'))
        checks.append(('缺失列', miss_c == 0, f'{miss_c} 列缺失' if miss_c else '无'))
        checks.append(('缺失索引', miss_i == 0, f'{miss_i} 个索引缺失' if miss_i else '无'))

        # 3. 行数一致性（结构修复绝不允许丢数据）
        row_counts_after = snapshot_row_counts(app, sorted(existing_tables))
        mismatches = []
        for t, before in row_counts_before.items():
            after = row_counts_after.get(t)
            if before is not None and after is not None and before != after:
                mismatches.append(f'{t}: {before} -> {after}')
        checks.append(('行数一致性', not mismatches,
                       '所有表行数与修复前一致' if not mismatches else '; '.join(mismatches)))

    all_pass = True
    for name, ok, detail in checks:
        log.info('  验证 [%s] %s: %s', 'PASS' if ok else 'FAIL', name, detail)
        if not ok:
            all_pass = False
    return all_pass, checks


# ---------------------------------------------------------------- 主流程
def main():
    parser = argparse.ArgumentParser(description='生产环境数据库一键检测修复脚本')
    parser.add_argument('--check', action='store_true', help='仅检测（只读、不备份），不执行修复')
    parser.add_argument('--thorough', action='store_true',
                        help='检测时追加全库完整性扫描（quick_check/外键/行数快照，大库数分钟）')
    parser.add_argument('--yes', action='store_true', help='跳过交互确认')
    parser.add_argument('--vacuum', action='store_true', help='修复后执行 VACUUM（SQLite）')
    parser.add_argument('--deep', action='store_true', help='对备份文件执行完整 integrity_check（更慢）')
    parser.add_argument('--force-vacuum', action='store_true',
                        help='服务运行中也强制 VACUUM INTO 备份（低峰期使用）')
    parser.add_argument('--rebuild', action='store_true', help='SQLite 严重损坏时用 .recover 重建')
    parser.add_argument('--skip-backup', action='store_true', help='跳过备份（强烈不建议）')
    args = parser.parse_args()

    global log
    logger, log_file = setup_logger()
    log = logger

    manifest = {'run_at': RUN_TS, 'log_file': str(log_file), 'mode': 'check' if args.check else 'repair'}
    try:
        # 环境变量（生产配置自动识别，不覆盖已有变量）
        env_file, env_loaded = load_env_file()
        os.environ.setdefault('FLASK_CONFIG', 'production')
        if env_file:
            log.info('加载环境配置: %s（%d 个变量，已有环境变量优先）', env_file, env_loaded)

        sys.path.insert(0, str(PROJECT_ROOT))
        from app_factory import create_app
        app = create_app()

        db_type, db_path, service_live = preflight_checks(app)
        manifest['db_type'] = db_type
        manifest['service_live'] = service_live
        manifest['db_uri_masked'] = mask_uri(app.config['SQLALCHEMY_DATABASE_URI'])

        # ---- 备份（--check 只读检测模式不备份，避免对生产产生任何负担） ----
        if args.check:
            log.info('检测模式（--check）：跳过备份（只读检测，不修改任何数据）。')
            manifest['backup'] = None
        else:
            log.info('=' * 60)
            log.info('【2/5】修复前数据备份')
            log.info('=' * 60)
            if args.skip_backup:
                log.warning('已指定 --skip-backup，跳过备份（风险自负！）')
                manifest['backup'] = None
            else:
                if db_type == 'sqlite':
                    manifest['backup'] = backup_sqlite(
                        db_path, service_live=service_live, deep=args.deep,
                        force_vacuum=args.force_vacuum)
                else:
                    manifest['backup'] = backup_mysql(app)

        # ---- 严重损坏重建（仅显式 --rebuild，且非只读检测模式） ----
        if args.rebuild and not args.check:
            if db_type != 'sqlite':
                raise RuntimeError('--rebuild 仅支持 SQLite')
            if not args.yes and not args.check:
                if input('将对数据库执行 .recover 重建，原库会保留为 .corrupt 文件。确认？输入 yes 继续: ') != 'yes':
                    log.info('用户取消，退出。')
                    return 0
            rebuild_sqlite(db_path)
            # 重建后重新创建 app 以获取新连接
            app = create_app()

        # ---- 检测 ----
        # 修复模式默认深度检测（需完整性扫描确认库状态）；--check 默认快速，加 --thorough 升级
        thorough = (not args.check) or args.thorough
        log.info('=' * 60)
        log.info('【3/5】数据库异常检测（只读，%s模式）', '深度' if thorough else '快速')
        log.info('=' * 60)
        with app.app_context():
            import_all_models()
        issues, row_counts_before = detect_issues(app, db_type, thorough=thorough)
        manifest['issues_found'] = len(issues)
        manifest['detect_mode'] = 'thorough' if thorough else 'fast'

        structural = [i for i in issues if i['kind'] in
                      ('missing_table', 'missing_column', 'missing_index')]
        critical = [i for i in issues if i['severity'] == 'critical']

        if args.check:
            log.info('=' * 60)
            log.info('检测模式（--check）结束：结构问题 %d 个，严重问题 %d 个，数据类提示 %d 个',
                     len(structural), len(critical),
                     len(issues) - len(structural) - len(critical))
            write_manifest(manifest)
            return 0 if not critical and not structural else 2

        if critical and not args.rebuild:
            log.error('检测到数据库严重损坏。为避免二次损害，普通修复已中止。')
            log.error('处置建议：1) 优先使用备份 %s 恢复；2) 或确认后使用 --rebuild 抢救重建。',
                      (manifest.get('backup') or {}).get('file', '（无备份）'))
            write_manifest(manifest)
            return 3

        if structural:
            log.info('=' * 60)
            log.info('【4/5】安全修复（仅增量结构修复，数据类异常不自动处理）')
            log.info('=' * 60)
            if not args.yes:
                ans = input(f'检测到 {len(structural)} 个结构问题需要修复，修复前已完成备份。'
                            f'输入 yes 继续: ')
                if ans.strip() != 'yes':
                    log.info('用户取消修复，退出。')
                    write_manifest(manifest)
                    return 0
            results = repair_issues(app, db_type, issues)
            manifest['repair_results'] = results
            failed = [r for r in results if r['status'] == ST_FAIL]
            manual = [r for r in results if r['status'] == ST_MANUAL]
            log.info('修复完成：成功 %d，失败 %d，需人工处理 %d',
                     len([r for r in results if r['status'] == ST_FIXED]), len(failed), len(manual))
        else:
            log.info('未发现需要修复的结构问题。')

        if args.vacuum and db_type == 'sqlite':
            vacuum_sqlite(app, db_path)
            manifest['vacuum'] = True

        # ---- 验证 ----
        ok, checks = post_verify(app, db_type, issues, row_counts_before)
        manifest['verification'] = [{'check': n, 'pass': p, 'detail': d} for n, p, d in checks]

        write_manifest(manifest)
        if ok:
            log.info('=' * 60)
            log.info('全部完成：数据库结构完整、校验通过、行数一致。备份与日志：')
            log.info('  备份: %s', (manifest.get('backup') or {}).get('file', '已跳过'))
            log.info('  日志: %s', log_file)
            return 0
        log.error('验证未全部通过，请检查日志并视情况使用备份恢复。')
        return 2

    except Exception as e:
        log.exception('执行失败: %s', e)
        manifest['fatal_error'] = str(e)
        try:
            write_manifest(manifest)
        except Exception:
            pass
        return 3


def write_manifest(manifest: dict):
    """写入审计清单 JSON（备份文件、校验值、问题与修复结果、验证结论）。"""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    path = BACKUP_DIR / f'repair_{RUN_TS}_manifest.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, default=str)
    log.info('审计清单已写入: %s', path)


if __name__ == '__main__':
    sys.exit(main())
