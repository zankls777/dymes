# -*- coding: utf-8 -*-
"""自动备份到网盘 - 服务层

功能：
  1. 配置持久化（instance/auto_backup_config.json）：
     enabled / target_dir（网盘目录，支持 UNC 与盘符） / mode(full|incr|both) /
     full_time(每日全量时刻 HH:MM) / incr_interval(增量间隔分钟) /
     keep_full / keep_incr(保留份数)
  2. 全量备份：SQLite 在线快照 → 网盘目录 full_YYYYMMDD_HHMMSS.db
  3. 增量备份：自上次成功备份以来新增的投入记录 + 数据管理上传批次/明细
     → 导出为 incr_YYYYMMDD_HHMMSS.xlsx（多 Sheet）
  4. 后台调度线程：每 60 秒检查是否到达定时点（全量每日时刻 / 增量间隔）
  5. 备份历史（instance/auto_backup_history.json，保留最近 500 条）

依赖：data_management_service.backup_database（已验证存在）。
"""
import json
import os
import threading
import time
from datetime import datetime
from utils.time_utils import get_utc8_time, format_utc8_time
from datetime import datetime

from db import db
from data_management_service import backup_database, get_db_path

try:
    from openpyxl import Workbook, load_workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
except ImportError:
    Workbook = load_workbook = None

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
_INSTANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
CONFIG_FILE = os.path.join(_INSTANCE_DIR, 'auto_backup_config.json')
HISTORY_FILE = os.path.join(_INSTANCE_DIR, 'auto_backup_history.json')

CHECK_INTERVAL = 60                     # 调度检查间隔（秒）
HISTORY_LIMIT = 500                     # 历史保留条数

DEFAULT_CONFIG = {
    'enabled': False,
    'target_dir': '',                    # 网盘备份目录（UNC/盘符/本地目录）
    'mode': 'both',                      # full / incr / both
    'full_time': '21:30',                # 每日全量时刻 HH:MM
    'incr_interval': 60,                 # 增量备份间隔（分钟）
    'keep_full': 30,                     # 保留全量份数
    'keep_incr': 90,                     # 保留增量份数
}


# ---------------------------------------------------------------------------
# JSON 读写
# ---------------------------------------------------------------------------
def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return default


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(_read_json(CONFIG_FILE, {}) or {})
    return cfg


def save_config(cfg):
    clean = {k: cfg.get(k, DEFAULT_CONFIG[k]) for k in DEFAULT_CONFIG}
    _write_json(CONFIG_FILE, clean)
    return clean


# ---------------------------------------------------------------------------
# 历史
# ---------------------------------------------------------------------------
def load_history():
    return _read_json(HISTORY_FILE, []) or []


def append_history(entry):
    hist = load_history()
    hist.insert(0, entry)
    _write_json(HISTORY_FILE, hist[:HISTORY_LIMIT])


def last_success_time():
    """最近一次成功备份的时间字符串（YYYY-MM-DD HH:MM:SS），无则 None。"""
    for h in load_history():
        if h.get('status') == 'SUCCESS':
            return h.get('time')
    return None


def format_size(num):
    if not num:
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if num < 1024:
            return f'{num:.1f} {unit}'
        num /= 1024
    return f'{num:.1f} TB'


# ---------------------------------------------------------------------------
# 全量备份
# ---------------------------------------------------------------------------
def run_full_backup():
    """执行一次全量备份，返回历史条目；失败抛出异常并记录 FAIL。"""
    cfg = load_config()
    tdir = (cfg.get('target_dir') or '').strip()
    if not tdir:
        raise RuntimeError('未配置网盘备份目录（target_dir）')
    ts = get_utc8_time()
    name = f"full_{ts:%Y%m%d_%H%M%S}.db"
    path = os.path.join(tdir, name)
    entry = {'time': format_utc8_time(ts), 'type': 'full',
             'file': name, 'size': 0, 'target': tdir, 'status': 'SUCCESS',
             'error': ''}
    try:
        os.makedirs(tdir, exist_ok=True)
        backup_database(path)
        entry['size'] = os.path.getsize(path)
    except Exception as e:
        entry['status'] = 'FAIL'
        entry['error'] = str(e)
        append_history(entry)
        raise
    append_history(entry)
    _prune(tdir, 'full_', int(cfg.get('keep_full', 30)))
    return entry


# ---------------------------------------------------------------------------
# 增量备份
# ---------------------------------------------------------------------------
def _collect_incr_since(last_time):
    """收集自 last_time（字符串或 None）以来新增的业务数据。

    C-8：改用 yield_per(500) 流式分片，避免一次性 .all() 把上万行
    对象全部载入内存；三个集合各自独立游标，峰值内存由「行数」降为「分片」。
    """
    from models.invest_record import InvestmentRecord
    from models.data_management import DataRecord, DataUploadBatch

    since = None
    if last_time:
        try:
            since = datetime.strptime(last_time, '%Y-%m-%d %H:%M:%S')
        except Exception:
            since = None

    incr = {'投入明细(InvestmentRecord)': [], '上传批次(DataUploadBatch)': [],
            '数据记录(DataRecord)': []}

    qi = InvestmentRecord.query
    qb = DataUploadBatch.query
    qd = DataRecord.query
    if since:
        qi = qi.filter(InvestmentRecord.uploaded_at > since)
        qb = qb.filter(DataUploadBatch.uploaded_at > since)
        qd = qd.filter(DataRecord.uploaded_at > since)

    for r in qi.order_by(InvestmentRecord.uploaded_at).limit(20000) \
            .yield_per(500):
        incr['投入明细(InvestmentRecord)'].append({
            '投入日期': r.record_date, '批次': r.batch or '', '车种': r.vehicle or '',
            '品目': r.item or '', '品番': r.part_no or '', 'LOT/NO': r.lot or '',
            '投入数量': r.qty or 0, '回路数': r.circuits or 0,
            '合计回路数': r.total_circuits or 0, '备注': r.remark or '',
            '来源文件': r.source_file or '', '入库时间':
            r.uploaded_at.strftime('%Y-%m-%d %H:%M:%S') if r.uploaded_at else '',
        })
    for b in qb.order_by(DataUploadBatch.uploaded_at).limit(10000) \
            .yield_per(500):
        incr['上传批次(DataUploadBatch)'].append({
            '批次号': b.batch_no or '', '文件名': b.file_name or '',
            '类型': b.file_type or '', '类别': b.category or '',
            '记录数': b.row_count or 0, '错误数': b.error_count or 0,
            '状态': b.status or '', '上传时间':
            b.uploaded_at.strftime('%Y-%m-%d %H:%M:%S') if b.uploaded_at else '',
        })
    for d in qd.order_by(DataRecord.uploaded_at).limit(50000) \
            .yield_per(500):
        incr['数据记录(DataRecord)'].append({
            '业务日期': d.record_date, '类别': d.category or '',
            '标识': d.record_key or '', '来源文件': d.source_file or '',
            '行号': d.row_no or '', '入库时间':
            d.uploaded_at.strftime('%Y-%m-%d %H:%M:%S') if d.uploaded_at else '',
        })
    return incr


def _write_incr_excel(incr, path):
    if Workbook is None:
        raise RuntimeError('缺少 openpyxl，无法生成增量备份文件')
    wb = Workbook()
    wb.remove(wb.active)
    head_font = Font(bold=True)
    head_fill = PatternFill('solid', fgColor='D9EAF7')
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for sheet_name, rows in incr.items():
        ws = wb.create_sheet(title=sheet_name[:31])
        if not rows:
            ws.append(['（本表无增量数据）'])
            continue
        headers = list(rows[0].keys())
        for c, h in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=c, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.border = border
            cell.alignment = Alignment(horizontal='center', vertical='center')
        for i, row in enumerate(rows, start=2):
            for c, k in enumerate(headers, start=1):
                cell = ws.cell(row=i, column=c, value=row.get(k))
                cell.border = border
        ws.freeze_panes = 'A2'
        ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def run_incremental_backup():
    """执行一次增量备份（自上次成功起新增数据 → xlsx），返回历史条目。"""
    cfg = load_config()
    tdir = (cfg.get('target_dir') or '').strip()
    if not tdir:
        raise RuntimeError('未配置网盘备份目录（target_dir）')
    ts = get_utc8_time()
    name = f"incr_{ts:%Y%m%d_%H%M%S}.xlsx"
    path = os.path.join(tdir, name)
    entry = {'time': format_utc8_time(ts), 'type': 'incr',
             'file': name, 'size': 0, 'target': tdir, 'status': 'SUCCESS',
             'error': ''}
    try:
        os.makedirs(tdir, exist_ok=True)
        incr = _collect_incr_since(last_success_time())
        _write_incr_excel(incr, path)
        entry['size'] = os.path.getsize(path)
    except Exception as e:
        entry['status'] = 'FAIL'
        entry['error'] = str(e)
        append_history(entry)
        raise
    append_history(entry)
    _prune(tdir, 'incr_', int(cfg.get('keep_incr', 90)))
    return entry


# ---------------------------------------------------------------------------
# 增量恢复（从 incr_*.xlsx 重新导入数据库）
# ---------------------------------------------------------------------------
_SHEET_INVEST = '投入明细(InvestmentRecord)'
_SHEET_BATCH = '上传批次(DataUploadBatch)'
_SHEET_RECORD = '数据记录(DataRecord)'
_SHEET_INVEST_MARK = 'InvestmentRecord'
_SHEET_BATCH_MARK = 'DataUploadBatch'
_SHEET_RECORD_MARK = 'DataRecord'


def _sheet_rows(ws):
    """读取 Sheet 为 [{列名: 值}, ...]；跳过（本表无增量数据）提示行。"""
    rows = []
    headers = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(c).strip() if c is not None else '' for c in row]
            continue
        vals = [c for c in row]
        if not any(v is not None and str(v).strip() for v in vals):
            continue
        if headers and headers[0] and '本表无增量数据' in ' '.join(headers):
            continue
        rec = {}
        for h, c in zip(headers, vals):
            if h:
                rec[h] = c if c is not None else ''
        if rec:
            rows.append(rec)
    return rows


def _to_dt(text):
    """备用入库时间字符串 → datetime；解析失败返回 None。"""
    if not text:
        return None
    if isinstance(text, datetime):
        return text
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M'):
        try:
            return datetime.strptime(str(text).strip(), fmt)
        except Exception:
            continue
    return None


def restore_incr_file(file_path):
    """从增量备份 xlsx 恢复数据（三 Sheet：投入明细 / 上传批次 / 数据记录）。

    去重规则（避免重复入库）：
      - 投入明细：LOT 全局唯一（不区分大小写）；无 LOT 时按 来源文件+行号 判重
      - 上传批次：batch_no 全局唯一
      - 数据记录：按 来源文件+行号 判重；批次缺失时自动补建上传批次
    返回统计 dict：invest_added/invest_skipped/batch_added/batch_skipped/
    record_added/record_skipped/error。
    """
    from models.invest_record import InvestmentRecord
    from models.data_management import DataUploadBatch, DataRecord
    if Workbook is None:
        raise RuntimeError('缺少 openpyxl，无法读取增量备份文件')
    wb = load_workbook(file_path, read_only=False, data_only=True)
    stat = {'invest_added': 0, 'invest_skipped': 0,
            'batch_added': 0, 'batch_skipped': 0,
            'record_added': 0, 'record_skipped': 0}

    invest_rows = []   # (rec, back_time)
    for ws in wb.worksheets:
        if _SHEET_INVEST_MARK in ws.title:
            invest_rows = _sheet_rows(ws)

    batch_rows = []
    for ws in wb.worksheets:
        if _SHEET_BATCH_MARK in ws.title:
            batch_rows = _sheet_rows(ws)

    record_rows = []
    for ws in wb.worksheets:
        if _SHEET_RECORD_MARK in ws.title:
            record_rows = _sheet_rows(ws)

    # 1) 投入明细：LOT 唯一；无 LOT 按 (来源文件, 行号) 判重
    seen_lot_upper = set()
    seen_src_row = set()
    new_invest = []
    for rec in invest_rows:
        lot_text = str(rec.get('LOT/NO') or '').strip()
        src = str(rec.get('来源文件') or '').strip()
        row_no = rec.get('行号')
        back = _to_dt(rec.get('入库时间'))
        dup = False
        if lot_text:
            key = lot_text.upper()
            if key in seen_lot_upper:
                dup = True
            else:
                exist = InvestmentRecord.query.filter(
                    db.func.upper(InvestmentRecord.lot) == key).first()
                if exist:
                    dup = True
                else:
                    seen_lot_upper.add(key)
        else:
            skey = (src, str(row_no or ''))
            if skey in seen_src_row:
                dup = True
            else:
                exist = (InvestmentRecord.query
                         .filter_by(source_file=src or None,
                                    row_no=row_no or None).first())
                if exist:
                    dup = True
                else:
                    seen_src_row.add(skey)
        if dup:
            stat['invest_skipped'] += 1
            continue
        try:
            qty = int(float(rec.get('投入数量') or 0))
        except Exception:
            qty = 0
        try:
            cir = int(float(rec.get('回路数') or 0))
        except Exception:
            cir = 0
        try:
            tc = int(float(rec.get('合计回路数') or 0))
        except Exception:
            tc = 0
        new_invest.append(InvestmentRecord(
            record_date=str(rec.get('投入日期') or '').strip() or None,
            batch=str(rec.get('批次') or '').strip() or None,
            vehicle=str(rec.get('车种') or '').strip() or None,
            item=str(rec.get('品目') or '').strip() or None,
            part_no=str(rec.get('品番') or '').strip() or None,
            lot=lot_text or None,
            qty=qty, circuits=cir, total_circuits=tc,
            remark=str(rec.get('备注') or '').strip() or '',
            source_file=src or None, row_no=row_no or None,
            uploaded_at=back or get_utc8_time(),
        ))
        stat['invest_added'] += 1
    if new_invest:
        db.session.add_all(new_invest)

    # 2) 上传批次：batch_no 唯一
    new_batches = []
    for rec in batch_rows:
        bno = str(rec.get('批次号') or '').strip()
        if not bno:
            stat['batch_skipped'] += 1
            continue
        if DataUploadBatch.query.filter_by(batch_no=bno).first():
            stat['batch_skipped'] += 1
            continue
        try:
            rc = int(float(rec.get('记录数') or 0))
        except Exception:
            rc = 0
        try:
            ec = int(float(rec.get('错误数') or 0))
        except Exception:
            ec = 0
        new_batches.append(DataUploadBatch(
            batch_no=bno,
            file_name=str(rec.get('文件名') or '').strip() or bno,
            file_type=str(rec.get('类型') or '').strip() or 'excel',
            category=str(rec.get('类别') or '').strip() or None,
            row_count=rc, error_count=ec,
            status=str(rec.get('状态') or '').strip() or 'success',
            uploaded_at=_to_dt(rec.get('上传时间')) or get_utc8_time(),
        ))
        stat['batch_added'] += 1
    if new_batches:
        db.session.add_all(new_batches)
    db.session.flush()

    # 3) 数据记录：按 (来源文件, 行号) 判重；批次按文件名匹配，缺失则补建
    batch_by_file = {}
    new_records = []
    seen_rc = set()
    for rec in record_rows:
        src = str(rec.get('来源文件') or '').strip()
        row_no = rec.get('行号')
        skey = (src, str(row_no or ''))
        if skey in seen_rc:
            stat['record_skipped'] += 1
            continue
        exist = (DataRecord.query
                 .filter_by(source_file=src or None, row_no=row_no or None).first())
        if exist:
            stat['record_skipped'] += 1
            continue
        seen_rc.add(skey)
        b = batch_by_file.get(src)
        if b is None:
            b = (DataUploadBatch.query
                 .filter(DataUploadBatch.file_name == (src or '')).first())
        if b is None:
            new_bno = f"restore_{get_utc8_time():%Y%m%d%H%M%S}_{len(new_records):03d}"
            b = DataUploadBatch(
                batch_no=new_bno, file_name=src or '(未知文件)',
                file_type='excel',
                category=str(rec.get('类别') or '').strip() or None,
                row_count=0, error_count=0, status='success',
                summary='由增量备份恢复自动补建',
                uploaded_at=get_utc8_time(),
            )
            db.session.add(b)
            db.session.flush()
        batch_by_file[src] = b
        new_records.append(DataRecord(
            batch_id=b.id,
            record_date=str(rec.get('业务日期') or '').strip() or None,
            category=str(rec.get('类别') or '').strip() or None,
            record_key=str(rec.get('标识') or '').strip() or None,
            keys_json=json.dumps(['业务日期', '类别', '标识', '来源文件', '行号'],
                                 ensure_ascii=False),
            values_json=json.dumps([
                str(rec.get('业务日期') or ''), str(rec.get('类别') or ''),
                str(rec.get('标识') or ''), src, row_no or ''],
                ensure_ascii=False),
            source_file=src or '(未知文件)', row_no=row_no or None,
            uploaded_at=_to_dt(rec.get('入库时间')) or get_utc8_time(),
        ))
        stat['record_added'] += 1
    if new_records:
        db.session.add_all(new_records)

    db.session.commit()
    return stat


def _prune(tdir, prefix, keep):
    """清理目标目录中旧备份，仅保留最近 keep 份。"""
    try:
        if not os.path.isdir(tdir):
            return
        files = sorted(
            f for f in os.listdir(tdir)
            if f.startswith(prefix) and os.path.isfile(os.path.join(tdir, f)))
        for f in files[:-keep] if keep > 0 else files:
            try:
                os.remove(os.path.join(tdir, f))
            except OSError:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 调度线程
# ---------------------------------------------------------------------------
def _full_done_today():
    today = get_utc8_time().strftime('%Y-%m-%d')
    for h in load_history():
        if h.get('status') == 'SUCCESS' and h.get('type') == 'full' \
                and (h.get('time') or '').startswith(today):
            return True
    return False


def _incr_due(cfg):
    interval = int(cfg.get('incr_interval') or 60)
    if interval <= 0:
        return False
    last = last_success_time()
    if not last:
        return True
    try:
        diff = (get_utc8_time() - datetime.strptime(last, '%Y-%m-%d %H:%M:%S')).total_seconds()
    except Exception:
        return True
    return diff >= interval * 60


def check_and_run():
    """检查当前是否到达备份时刻，到达则执行。供调度线程与「立即执行」共用。"""
    cfg = load_config()
    if not cfg.get('enabled'):
        return
    now = get_utc8_time()
    mode = cfg.get('mode') or 'both'
    if mode in ('full', 'both') and now.strftime('%H:%M') >= (cfg.get('full_time') or '21:30') \
            and not _full_done_today():
        run_full_backup()
    if mode in ('incr', 'both') and _incr_due(cfg):
        run_incremental_backup()


def start_scheduler(app):
    """启动 daemon 后台调度线程（每 CHECK_INTERVAL 秒检查一次）。

    app 由调用方（create_app）显式传入：
    后台线程若改为 `from app import app`，当 app.py 以 __main__ 运行时
    会被以模块名 app 二次导入，导致 app.py 顶层 `create_app()` 再执行一遍
    （重复预热线程 / 重复 schema 检查 / 日志重复），故统一改为注入。
    """
    t = threading.Thread(target=_scheduler_loop, args=(app,), daemon=True,
                         name='auto_backup_scheduler')
    t.start()
    return t


def _scheduler_loop(app):
    """调度主循环：后台线程无 Flask 上下文，执行前先进入 app_context。

    app 由 start_scheduler 注入，避免 `from app import app` 触发模块二次导入。
    """
    while True:
        try:
            with app.app_context():
                check_and_run()
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL)