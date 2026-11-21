# -*- coding: utf-8 -*-
"""数据库导出工具：按功能模块导出相关表 / 指定表导出。

设计要点
--------
1. 功能模块 ↔ 数据表映射（FUNCTION_TABLES）：以「页面功能」为粒度列出其涉及的
   数据表，便于按功能整体备份/迁移，无需理解底层表名。
2. 导出格式：
   - db  ：SQLite 在线快照（只含所选表，结构与数据完整，可直接被本系统恢复/查看）
   - xlsx：每张表一个 Sheet 的 Excel（人可读，便于核对与共享）
   - csv ：zip 包，每张表一个 UTF-8-SIG 的 csv（兼容 Excel 直接打开）
3. 仅支持 SQLite（本系统使用 SQLite），按表名做白名单校验，杜绝 SQL 注入。
4. 完整备份请使用「数据库备份与恢复」中的整库快照；本模块用于按需的局部导出。

兼容性：Windows 7/10 与 Linux 均可运行，仅依赖标准库 sqlite3 / zipfile 与 openpyxl。
"""
import csv
import os
import sqlite3
import zipfile

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:  # pragma: no cover - 运行环境缺失时给出明确报错
    Workbook = None

# ---------------------------------------------------------------------------
# 导出配置
# ---------------------------------------------------------------------------
EXPORT_FORMATS = {
    'db': '数据库快照(.db)',
    'xlsx': 'Excel工作簿(.xlsx)',
    'csv': 'CSV压缩包(.zip)',
}

# 单表导出最大行数保护（避免超大表耗尽内存）
MAX_ROWS_XLSX = 200000
MAX_CELL_CHARS = 32000        # Excel 单元格上限 32767，留出余量

# ---------------------------------------------------------------------------
# 功能模块 ↔ 数据表映射
# 说明：仅列出该功能产生的「业务数据表」，系统表(user_info/
#       operation_logs 等)不放入功能模块，可通过「指定表导出」单独选择。
# ---------------------------------------------------------------------------
FUNCTION_TABLES = {
    '数据管理': {
        'desc': '批量上传批次与通用数据记录',
        'tables': ['data_upload_batch', 'data_record'],
    },
    '文件管理 / 数据对比': {
        'desc': '上传文件台账与文件自身对比结果',
        'tables': ['file_info'],
    },
    '成品管理': {
        'desc': '成品台账',
        'tables': ['finished_product'],
    },
    '车种管理': {
        'desc': '车种基础资料',
        'tables': ['vehicle_type'],
    },
    '投入计划书 / 投入明细': {
        'desc': '投入计划书解析出的投入明细记录',
        'tables': ['investment_record'],
    },
    '资材量下载': {
        'desc': '用料清单、子件清单、盘点品番字典',
        'tables': ['usage_list', 'sub_list', 'inventory_pno_dict'],
    },
    '资材入库': {
        'desc': '资材每日入库与资材汇总',
        'tables': ['material_inbound_daily', 'material_summary'],
    },
    '汽车厂装车实际': {
        'desc': '汽车厂装车实际记录',
        'tables': ['auto_loading_record'],
    },
    'WMS：资材换算': {
        'desc': '板材/管材/胶带换算结果',
        'tables': ['sheet_calculation_result', 'tube_calculation_result',
                   'tape_calculation_result'],
    },
    '波纹管位置': {
        'desc': '波纹管位置数据与列配置',
        'tables': ['corrugated_location', 'corrugated_column_config'],
    },
    '补线打端子': {
        'desc': 'LOT登录、扫描录入记录与明细',
        'tables': ['lot_login_info', 'wire_scan_record', 'wire_scan_detail'],
    },
    '盘点管理': {
        'desc': '盘点班组与盘点实查记录',
        'tables': ['inventory_team', 'inventory_check'],
    },
    '系统设置与用户': {
        'desc': '系统设置、用户与权限、车辆类型等基础配置',
        'tables': ['system_setting', 'user_info', 'permission',
                   'permission_group', 'group_permission', 'user_group',
                   'user_permission_override'],
    },
    '操作日志': {
        'desc': '系统操作审计日志',
        'tables': ['operation_logs'],
    },
}


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _db_path():
    """获取当前 SQLite 数据库文件路径。"""
    from data_management_service import get_db_path
    return get_db_path()


def list_tables(db_path=None):
    """列出数据库中所有用户表（按名称排序，排除 sqlite_ 系统表）。"""
    path = db_path or _db_path()
    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _valid_tables(requested, db_path=None):
    """过滤出真实存在的表名（白名单），保证后续 SQL 安全。"""
    if isinstance(requested, str):
        requested = [requested]
    exist = set(list_tables(db_path))
    return [t for t in (requested or []) if t in exist]


def _safe_sheet_name(name, used):
    """生成合法且不重名的 Excel Sheet 名（≤31 字符，去除非法字符）。"""
    for ch in r'[]:*?/\\':
        name = name.replace(ch, '_')
    name = (name or 'sheet')[:31]
    base, i = name, 1
    while name in used:
        suffix = f'_{i}'
        name = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(name)
    return name


def _escape_csv_cell(value):
    """CSV 防注入：以 =+-@ 开头的文本前面加单引号。"""
    if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'):
        return "'" + value
    return value


def _iter_rows(conn, table):
    """以流式方式读取表数据，返回 (列名, 行游标)。"""
    cur = conn.execute(f'SELECT * FROM "{table}"')
    headers = [d[0] for d in cur.description]
    return headers, cur


# ---------------------------------------------------------------------------
# 导出：SQLite 快照（仅所选表）
# ---------------------------------------------------------------------------
def export_db(tables, target_path, db_path=None, progress_cb=None):
    """将所选表导出为新的 SQLite 数据库文件（结构与数据完整）。

    progress_cb(percent, message) 可选：按「已处理表数/总表数」上报进度。
    """
    tables = _valid_tables(tables, db_path)
    if not tables:
        raise ValueError('未选择任何有效的表')
    src_path = db_path or _db_path()
    total = len(tables)
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(target_path)
    try:
        dst.execute('BEGIN')
        for i, t in enumerate(tables, start=1):
            row = src.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (t,)).fetchone()
            if not row or not row[0]:
                continue
            ddl = row[0].replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1)
            dst.execute(ddl)
            # 复制索引（保留原索引名，失败时忽略）
            for (idx_sql,) in src.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index' "
                    "AND tbl_name=? AND sql IS NOT NULL", (t,)).fetchall():
                try:
                    dst.execute(idx_sql)
                except sqlite3.Error:
                    pass
            rows = src.execute(f'SELECT * FROM "{t}"').fetchall()
            if rows:
                placeholders = ','.join('?' * len(rows[0]))
                dst.executemany(
                    f'INSERT INTO "{t}" VALUES ({placeholders})', rows)
            if progress_cb:
                pct = int(i / total * 90)   # 留 10% 给提交/收尾
                progress_cb(pct, f"正在导出表 {t}（{i}/{total}）")
        dst.commit()
    finally:
        src.close()
        dst.close()
    if progress_cb:
        progress_cb(100, '数据库快照导出完成')
    return {'tables': tables, 'path': target_path}


# ---------------------------------------------------------------------------
# 导出：Excel（每表一个 Sheet）
# ---------------------------------------------------------------------------
def export_xlsx(tables, target_path, db_path=None, progress_cb=None):
    """将所选表导出为 Excel 工作簿（每张表一个 Sheet）。

    progress_cb(percent, message) 可选：按「已处理表数/总表数」上报进度。
    """
    if Workbook is None:
        raise RuntimeError('缺少 openpyxl，无法导出 Excel')
    tables = _valid_tables(tables, db_path)
    if not tables:
        raise ValueError('未选择任何有效的表')
    total = len(tables)
    src = sqlite3.connect(db_path or _db_path())
    head_font = Font(bold=True)
    head_fill = PatternFill('solid', fgColor='D9EAF7')
    used_names, summary = set(), []
    try:
        wb = Workbook()
        wb.remove(wb.active)
        for i, t in enumerate(tables, start=1):
            ws = wb.create_sheet(title=_safe_sheet_name(t, used_names))
            headers, cur = _iter_rows(src, t)
            for c, h in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=c, value=h)
                cell.font = head_font
                cell.fill = head_fill
                cell.alignment = Alignment(horizontal='center')
            count = 0
            for r, row in enumerate(cur, start=2):
                if r - 1 > MAX_ROWS_XLSX:
                    break
                for c, v in enumerate(row, start=1):
                    if isinstance(v, str) and len(v) > MAX_CELL_CHARS:
                        v = v[:MAX_CELL_CHARS]
                    ws.cell(row=r, column=c, value=v)
                count += 1
            ws.freeze_panes = 'A2'
            if headers:
                ws.auto_filter.ref = ws.dimensions
            summary.append({'table': t, 'rows': count})
            if progress_cb:
                pct = int(i / total * 85)   # 留 15% 给 wb.save 落盘
                progress_cb(pct, f"正在导出表 {t}（{i}/{total}，{count} 行）")
        if not wb.worksheets:
            wb.create_sheet(title='空')
        if progress_cb:
            progress_cb(90, '正在写入 Excel 文件…')
        wb.save(target_path)
    finally:
        src.close()
    if progress_cb:
        progress_cb(100, 'Excel 导出完成')
    return {'tables': tables, 'detail': summary, 'path': target_path}


# ---------------------------------------------------------------------------
# 导出：CSV（zip 包，每表一个 csv）
# ---------------------------------------------------------------------------
def export_csv_zip(tables, target_path, db_path=None, progress_cb=None):
    """将所选表导出为 zip（每张表一个 UTF-8-SIG 编码的 csv）。

    progress_cb(percent, message) 可选：按「已处理表数/总表数」上报进度。
    """
    tables = _valid_tables(tables, db_path)
    if not tables:
        raise ValueError('未选择任何有效的表')
    total = len(tables)
    src = sqlite3.connect(db_path or _db_path())
    summary = []
    try:
        with zipfile.ZipFile(target_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, t in enumerate(tables, start=1):
                headers, cur = _iter_rows(src, t)
                tmp_csv = target_path + f'.{t}.csv'
                count = 0
                with open(tmp_csv, 'w', encoding='utf-8-sig', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow(headers)
                    for row in cur:
                        writer.writerow([_escape_csv_cell(v) for v in row])
                        count += 1
                zf.write(tmp_csv, arcname=f'{t}.csv')
                try:
                    os.remove(tmp_csv)
                except OSError:
                    pass
                summary.append({'table': t, 'rows': count})
                if progress_cb:
                    pct = int(i / total * 100)
                    progress_cb(pct, f"正在导出表 {t}（{i}/{total}，{count} 行）")
    finally:
        src.close()
    if progress_cb:
        progress_cb(100, 'CSV 压缩包导出完成')
    return {'tables': tables, 'detail': summary, 'path': target_path}


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------
EXPORTERS = {'db': export_db, 'xlsx': export_xlsx, 'csv': export_csv_zip}


def export_tables(tables, fmt, target_path, db_path=None, progress_cb=None):
    """按格式导出所选表，返回统计信息 dict。"""
    fmt = (fmt or 'db').lower()
    if fmt not in EXPORTERS:
        raise ValueError(f'不支持的导出格式: {fmt}')
    return EXPORTERS[fmt](tables, target_path, db_path, progress_cb=progress_cb)


def function_tables(name):
    """返回指定功能模块涉及的表名列表；模块不存在返回空列表。"""
    cfg = FUNCTION_TABLES.get(name)
    return list(cfg['tables']) if cfg else []


def table_summary(name, db_path=None):
    """返回功能模块各表的行数摘要，供页面提示。"""
    from sqlalchemy import text
    from db import db
    tables = _valid_tables(function_tables(name), db_path)
    result = []
    for t in tables:
        try:
            cnt = db.session.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
        except Exception:
            db.session.rollback()
            cnt = None
        result.append({'table': t, 'rows': cnt})
    return result
