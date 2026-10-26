# -*- coding: utf-8 -*-
"""数据管理 - 服务层

批量上传（CSV/Excel/JSON）解析 → 按业务日期逐日归档入库 → 日期范围/类别查询
→ 多格式导出（CSV/Excel/JSON/PDF）→ 数据库备份与恢复。
"""
import csv
import io
import json
import os
import threading
from datetime import datetime
from utils.time_utils import get_utc8_time, format_utc8_time
import re
import sqlite3
import tempfile
from collections import OrderedDict

from db import db
from models.data_management import DataUploadBatch, DataRecord

try:
    import pandas as pd
except ImportError:
    pd = None

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
CSV_ENCODINGS = ["utf-8-sig", "utf-8", "gbk", "gb18030"]
DATE_COLUMN_KEYWORDS = ["日期", "date", "날짜", "日付", "时间"]
CATEGORY_COLUMN_KEYWORDS = ["类别", "分类", "category", "구분"]

MAX_ERROR_LOG_LINES = 60          # 错误日志保留的最大行数
BATCH_FLUSH_SIZE = 500            # 入库批量 flush 行数
COLUMN_LIMIT = 100                # 单文件最大列数保护


# ---------------------------------------------------------------------------
# 文件解析（纯逻辑）
# ---------------------------------------------------------------------------
def parse_file(file_path, file_type):
    """解析文件，返回 (records, report)。

    records: list of {'keys': [...列名], 'values': [...行值], 'date': 'YYYY-MM-DD',
                      'category': ..., 'key': 行标识, 'row_no': 行号}
    """
    if file_type == "csv":
        return _parse_csv(file_path)
    if file_type == "excel":
        return _parse_excel(file_path)
    if file_type == "json":
        return _parse_json(file_path)
    raise ValueError(f"不支持的文件类型: {file_type}")


def detect_file_type(filename):
    """按扩展名判断文件类型。返回 csv / excel / json / None。"""
    name = (filename or "").lower()
    if name.endswith((".csv", ".txt")):
        return "csv"
    if name.endswith((".xlsx", ".xls", ".xlsm")):
        return "excel"
    if name.endswith((".json", ".ndjson", ".jsonl")):
        return "json"
    return None


def _read_csv_df(file_path):
    """尝试多种编码读取 CSV，返回 DataFrame。"""
    if pd is None:
        raise RuntimeError("pandas 未安装，无法解析 CSV")
    last_err = None
    for enc in CSV_ENCODINGS:
        try:
            return pd.read_csv(file_path, encoding=enc, dtype=str)
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"CSV 解析失败（尝试了 {CSV_ENCODINGS}）: {last_err}")


def _parse_csv(file_path):
    df = _read_csv_df(file_path)
    errors = []
    if df is None or df.empty:
        return [], {"empty": True, "errors": ["文件无数据行"], "columns": []}
    frames = _normalize_frames({"csv": df})
    return _records_from_frames(frames, errors, fallback_category=None)[0:2]


def _parse_excel(file_path):
    """读取 Excel（支持多 Sheet），每 Sheet 作为一组。"""
    if pd is None:
        raise RuntimeError("pandas 未安装，无法解析 Excel")
    try:
        sheets = pd.read_excel(file_path, sheet_name=None, dtype=str)
    except Exception:
        # 部分旧版 xls 用 xlrd；尝试单 sheet 读取
        sheets = {"Sheet1": pd.read_excel(file_path, dtype=str)}
    errors = []
    frames = _normalize_frames(sheets)
    return _records_from_frames(frames, errors, fallback_category=None)


def _parse_json(file_path):
    """JSON：支持对象数组、多行 JSON（ndjson）、{列: [值]}。"""
    text = None
    for enc in CSV_ENCODINGS:
        try:
            with open(file_path, "r", encoding=enc) as f:
                text = f.read()
            break
        except Exception:
            continue
    if text is None:
        raise RuntimeError("JSON 文件读取失败（编码不识别）")

    text = text.strip()
    rows = []
    if text.startswith("["):
        rows = json.loads(text)
    elif text.startswith("{"):
        data = json.loads(text)
        # 兼容 {columns: [...], data: [[...], ...]} 或 {col: [vals]}
        if isinstance(data, dict):
            if "data" in data and isinstance(data["data"], list):
                cols = data.get("columns") or (data.get("keys") or [])
                for item in data["data"]:
                    if isinstance(item, dict):
                        rows.append(item)
                    elif isinstance(item, list) and cols:
                        rows.append(dict(zip(cols, item)))
            else:
                # 面向列字典 → 转面向行
                first = next(iter(data.values()), None)
                n = len(first) if isinstance(first, list) else 0
                cols = list(data.keys())
                rows = [dict(zip(cols, (data[c][i] if i < len(data[c]) else "" for c in cols)))
                        for i in range(n)]
    else:
        # ndjson：每行一个 JSON 对象
        for line in text.splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    errors = []
    dict_rows = [r for r in rows if isinstance(r, dict)]
    frames = {"json": pd.DataFrame(dict_rows) if dict_rows else None}
    return _records_from_frames(frames, errors, fallback_category=None)


def _normalize_frames(sheets):
    """清洗各 DataFrame：列名去空格、去空列、NaN 填充空串。"""
    frames = OrderedDict()
    for name, df in sheets.items():
        if df is None or df.empty:
            continue
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        df = df.dropna(how="all")
        df = df.fillna("")
        # 列名重复时去重（保留首列）
        seen = set()
        new_cols = []
        for c in df.columns:
            if c in seen:
                new_cols.append(f"{c}_2")
            else:
                seen.add(c)
                new_cols.append(c)
        df.columns = new_cols
        if len(df.columns) > COLUMN_LIMIT:
            df = df.iloc[:, :COLUMN_LIMIT]
        frames[name] = df
    return frames


def _records_from_frames(frames, errors, fallback_category=None):
    """将已清洗的 DataFrame 集合转为记录；同时提取类别（sheet 名）与日期推断。"""
    records = []
    columns_order = []
    for sheet_name, df in frames.items():
        if df is None or df.empty:
            continue
        keys = list(df.columns)
        for col in keys:
            if col not in columns_order:
                columns_order.append(col)
        # 类别：sheet 名（Excel 多 Sheet）或 fallback
        category_hint = sheet_name if sheet_name not in ("csv", "json") else fallback_category
        date_col = _find_date_column(df)
        cat_col = _find_category_column(df)
        # 用 to_dict('records') 替代 iterrows：避免逐行生成 Series（iterrows 每行构造开销大且会做类型统一），
        # slice 保持按行顺序，语义与 iterrows 兼容（row[c] / c in row 均可用）
        row_dicts = df.to_dict("records")
        for idx, row in enumerate(row_dicts, start=2):
            values = [str(row[c]) if str(row[c]) not in ("nan", "None") else "" for c in keys]
            if not any(values):
                continue  # 跳过空行
            row_date = _extract_date(row, keys, date_col)
            row_category = None
            if cat_col and str(row[cat_col]).strip():
                row_category = str(row[cat_col]).strip()
            elif category_hint:
                row_category = category_hint
            rec = {
                "keys": keys,
                "values": values,
                "date": row_date,
                "category": row_category,
                "key": values[0] if values else "",
                "row_no": idx,
            }
            records.append(rec)
    return records, {"columns": columns_order, "errors": errors, "empty": not bool(records)}


def _find_date_column(df):
    for col in df.columns:
        text = str(col).lower()
        if any(kw in text for kw in DATE_COLUMN_KEYWORDS):
            return col
    return None


def _find_category_column(df):
    for col in df.columns:
        text = str(col).lower()
        if any(kw in text for kw in CATEGORY_COLUMN_KEYWORDS):
            return col
    return None


DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")

def _extract_date(row, keys, date_col, fallback=None):
    """从日期列取值推断 YYYY-MM-DD；无则用 fallback。"""
    import datetime as _dt
    if date_col and date_col in row:
        raw = str(row[date_col]).strip()
        m = DATE_RE.search(raw)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return f"{y:04d}-{mo:02d}-{d:02d}"
            except ValueError:
                pass
        # 尝试 Excel 序列日期 / pandas datetime
        try:
            val = row[date_col]
            if hasattr(pd, "to_datetime") and val and str(val) != "nan":
                ts = pd.to_datetime(val)
                if not pd.isna(ts):
                    return ts.strftime("%Y-%m-%d")
        except Exception:
            pass
    if fallback:
        return fallback
    return _dt.date.today().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 入库
# ---------------------------------------------------------------------------
def save_upload(records, file_name, file_type, fallback_date=None, fallback_category=None,
                uploaded_by=None, uploaded_by_name=None):
    """批量入库（一个文件一个批次）。返回 (batch, error_log_lines)。

    fallback_date: 页面指定的业务日期，用于无日期列的数据行。
    uploaded_by / uploaded_by_name: 上传人ID与姓名快照（账号删除后仍可显示文本）。
    """
    import datetime as _dt
    import uuid
    now = get_utc8_time()
    batch_no = (f"DM{now.strftime('%Y%m%d%H%M%S')}"
                f"{os.getpid()%100:02d}{uuid.uuid4().hex[:4].upper()}")
    batch = DataUploadBatch(
        batch_no=batch_no,
        file_name=file_name,
        file_type=file_type,
        category=fallback_category,
        row_count=0,
        error_count=0,
        status="success",
        uploaded_at=now,
        uploaded_by=uploaded_by,
        uploaded_by_name=uploaded_by_name,
    )
    db.session.add(batch)
    db.session.flush()

    inserted = 0
    error_lines = []
    for rec in records:
        rdate = rec.get("date") or fallback_date or _dt.date.today().strftime("%Y-%m-%d")
        rcat = rec.get("category") or fallback_category
        try:
            row = DataRecord(
                batch_id=batch.id,
                record_date=rdate,
                category=rcat,
                record_key=(rec.get("key") or "")[:200],
                keys_json=json.dumps(rec["keys"], ensure_ascii=False),
                values_json=json.dumps(rec["values"], ensure_ascii=False),
                source_file=file_name,
                row_no=rec.get("row_no"),
                uploaded_at=now,
            )
            db.session.add(row)
            inserted += 1
            if inserted % BATCH_FLUSH_SIZE == 0:
                db.session.flush()
        except Exception as e:
            error_lines.append(f"行{rec.get('row_no')} 入库失败: {e}")
            batch.error_count += 1

    batch.row_count = inserted
    batch.status = "partial" if error_lines else "success" if inserted else "failed"
    db.session.commit()
    return batch, error_lines


def build_upload_report(batch, records, parsed_report, error_lines):
    """生成上传校验报告（用于页面通知与批次 summary）。"""
    sample = []
    for rec in records[:5]:
        sample.append(dict(zip(rec["keys"], rec["values"])))
    summary = {
        "columns": parsed_report.get("columns", []),
        "sheet_count": parsed_report.get("count", 1),
        "sample": sample,
        "error_sample": error_lines[:MAX_ERROR_LOG_LINES],
    }
    batch.summary = json.dumps(summary, ensure_ascii=False)
    batch.error_log = "\n".join(error_lines[-MAX_ERROR_LOG_LINES:]) if error_lines else ""
    db.session.commit()
    return {
        "batch_no": batch.batch_no,
        "file_name": batch.file_name,
        "file_type": batch.file_type,
        "inserted": batch.row_count,
        "errors": batch.error_count,
        "status": batch.status,
        "columns": parsed_report.get("columns", []),
        "sample": sample,
        "error_log": batch.error_log,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# 查询与导出
# ---------------------------------------------------------------------------
def query_records(start_date=None, end_date=None, category=None, keyword=None,
                  page=1, per_page=10, limit=None):
    """按条件查询记录。返回 (items, total, page, per_page, columns)。

    items: list of dict {'id','record_date','category','record_key','source_file',
                         'row_no','data': {列: 值}}
    """
    q = DataRecord.query
    if start_date:
        q = q.filter(DataRecord.record_date >= start_date)
    if end_date:
        q = q.filter(DataRecord.record_date <= end_date)
    if category:
        q = q.filter(DataRecord.category == category)
    if keyword:
        q = q.filter(DataRecord.record_key.like(f"%{keyword}%"))
    q = q.order_by(DataRecord.record_date.desc(), DataRecord.id.desc())

    total = q.count()
    if limit is not None:
        rows = q.limit(limit).all()
    else:
        rows = q.offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for r in rows:
        try:
            keys = json.loads(r.keys_json or "[]")
            values = json.loads(r.values_json or "[]")
            data = dict(zip(keys, values))
        except Exception:
            keys, values, data = [], [], {}
        items.append({
            "id": r.id,
            "record_date": r.record_date,
            "category": r.category or "",
            "record_key": r.record_key or "",
            "source_file": r.source_file,
            "row_no": r.row_no,
            "data": data,
        })

    # 动态列：当前页数据的列并集（保持出现顺序）
    columns = OrderedDict()
    for it in items:
        for c in it["data"]:
            if c not in columns:
                columns[c] = None
    return items, total, page, per_page, list(columns.keys())


def list_categories():
    """返回已有类别清单（供筛选下拉）。"""
    rows = db.session.query(DataRecord.category).distinct().order_by(DataRecord.category).all()
    return [r[0] for r in rows if r[0]]


def daily_summary(start_date=None, end_date=None):
    """日期范围内每日记录数聚合。"""
    q = db.session.query(DataRecord.record_date, db.func.count(DataRecord.id))
    if start_date:
        q = q.filter(DataRecord.record_date >= start_date)
    if end_date:
        q = q.filter(DataRecord.record_date <= end_date)
    q = q.group_by(DataRecord.record_date).order_by(DataRecord.record_date.desc())
    return [{"date": r[0], "count": r[1]} for r in q.all()]


def _all_matching(start_date, end_date, category, keyword):
    q = DataRecord.query
    if start_date:
        q = q.filter(DataRecord.record_date >= start_date)
    if end_date:
        q = q.filter(DataRecord.record_date <= end_date)
    if category:
        q = q.filter(DataRecord.category == category)
    if keyword:
        q = q.filter(DataRecord.record_key.like(f"%{keyword}%"))
    return q.order_by(DataRecord.record_date, DataRecord.id)


def _rows_data(rows):
    """将 ORM 行转为 (data_dict, meta) 列表，并汇总列集合。"""
    out = []
    columns = OrderedDict()
    for r in rows:
        try:
            keys = json.loads(r.keys_json or "[]")
            values = json.loads(r.values_json or "[]")
            data = dict(zip(keys, values))
        except Exception:
            data = {}
        for c in data:
            if c not in columns:
                columns[c] = None
        out.append((data, {"record_date": r.record_date, "category": r.category or "",
                           "record_key": r.record_key or "", "source_file": r.source_file}))
    return out, list(columns.keys())


def export_csv(start_date, end_date, category, keyword, filepath):
    rows = _all_matching(start_date, end_date, category, keyword).all()
    data_rows, columns = _rows_data(rows)
    with open(filepath, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["记录日期", "类别", "标识"] + columns)
        for data, meta in data_rows:
            writer.writerow([meta["record_date"], meta["category"], meta["record_key"]] +
                            [data.get(c, "") for c in columns])
    return {"lines": len(data_rows)}


def export_excel(start_date, end_date, category, keyword, filepath):
    rows = _all_matching(start_date, end_date, category, keyword).all()
    data_rows, columns = _rows_data(rows)
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    wb = Workbook()
    ws = wb.active
    ws.title = "数据记录"
    headers = ["记录日期", "类别", "标识"] + columns
    fill = PatternFill("solid", fgColor="D9EAF7")
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = Font(bold=True)
        cell.fill = fill
        cell.alignment = Alignment(horizontal="center")
    thin = Side(style="thin", color="CCCCCC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for r, (data, meta) in enumerate(data_rows, start=2):
        vals = [meta["record_date"], meta["category"], meta["record_key"]] + \
               [data.get(c, "") for c in columns]
        for c, v in enumerate(vals, start=1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.border = border
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    wb.save(filepath)
    return {"lines": len(data_rows)}


def export_json(start_date, end_date, category, keyword, filepath):
    rows = _all_matching(start_date, end_date, category, keyword).all()
    data_rows, _ = _rows_data(rows)
    payload = {
        "exported_at": format_utc8_time(get_utc8_time()),
        "filters": {"start": start_date, "end": end_date, "category": category,
                    "keyword": keyword},
        "count": len(data_rows),
        "records": [
            {"record_date": meta["record_date"], "category": meta["category"],
             "record_key": meta["record_key"], "source_file": meta["source_file"],
             "data": data}
            for data, meta in data_rows
        ],
    }
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return {"lines": len(data_rows)}


def export_pdf(start_date, end_date, category, keyword, filepath):
    """PDF 导出：优先 fpdf2，缺失时生成可打印的 HTML（浏览器打印即 PDF）。"""
    try:
        from fpdf import FPDF
    except ImportError:
        html_path = filepath.rsplit(".", 1)[0] + "_print.html"
        _export_print_html(start_date, end_date, category, keyword, html_path)
        return {"lines": -1, "html_path": html_path, "fallback": True}

    rows = _all_matching(start_date, end_date, category, keyword).all()
    data_rows, columns = _rows_data(rows)

    class PdfTable(FPDF):
        pass

    pdf = PdfTable()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("helvetica", "B", 13)
    pdf.cell(0, 8, "数据记录导出报表", ln=1, align="C")
    pdf.set_font("helvetica", "", 8)
    pdf.cell(0, 5, f"日期范围: {start_date or '全部'} ~ {end_date or '全部'}  "
                    f"类别: {category or '全部'}  记录数: {len(data_rows)}", ln=1, align="C")
    pdf.ln(4)

    headers = ["日期", "类别", "标识"] + columns[:40]
    for h in headers:
        pdf.set_font("helvetica", "B", 6)
        pdf.cell(_col_w(h), 6, str(h), border=1, align="C")
    pdf.ln()
    for data, meta in data_rows:
        vals = [meta["record_date"], meta["category"], meta["record_key"]] + \
               [data.get(c, "") for c in columns[:40]]
        for v in vals:
            pdf.set_font("helvetica", "", 6)
            pdf.cell(_col_w(v), 6, str(v)[:30], border=1)
        pdf.ln()
    pdf.output(filepath)
    return {"lines": len(data_rows)}


def _col_w(v):
    text = str(v)
    if any(ord(ch) > 0x2E80 for ch in text):
        return 26
    return max(16, min(40, 10 + len(text) * 2))


def _export_print_html(start_date, end_date, category, keyword, filepath):
    rows = _all_matching(start_date, end_date, category, keyword).all()
    data_rows, columns = _rows_data(rows)
    headers = ["记录日期", "类别", "标识"] + columns[:80]
    lines = ["<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
             "<title>数据记录导出报表</title>",
             "<style>body{font-family:monospace;font-size:12px}table{border-collapse:collapse}"
             "th,td{border:1px solid #999;padding:2px 6px;white-space:nowrap}th{background:#eee}</style>",
             "</head><body><h3>数据记录导出报表</h3>",
             f"<p>日期范围: {start_date or '全部'} ~ {end_date or '全部'} | 类别: {category or '全部'} | 记录数: {len(data_rows)}</p>",
             "<table><tr>"]
    for h in headers:
        lines.append(f"<th>{h}</th>")
    lines.append("</tr>")
    for data, meta in data_rows:
        lines.append("<tr>")
        vals = [meta["record_date"], meta["category"], meta["record_key"]] + \
               [data.get(c, "") for c in columns[:80]]
        for v in vals:
            lines.append(f"<td>{str(v).replace('<', '&lt;')}</td>")
        lines.append("</tr>")
    lines.append("</table></body></html>")
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------------------------------
# 备份与恢复（SQLite 在线快照）
# ---------------------------------------------------------------------------
def get_db_path():
    """返回当前 SQLite 数据库文件的绝对路径。

    SQLite URI 的通用形式为「sqlite: + 三个斜杠 + 绝对路径」，即
    sqlite:////abs/path（POSIX）或 sqlite:///C:/path（Windows）。
    解析后先去掉「协议三斜杠」，剩下的路径：
      - POSIX 以 "/" 开头（绝对路径），必须原样保留，否则会被
        工作目录拼接成错误路径（如群晖 Docker 工作目录为
        /usr/syno/synoman/webapi，误拼后触发「数据库文件不存在」）；
      - Windows 会残留盘符前导斜杠，形如 "/C:/path"，须去掉该斜杠
        以恢复盘符（否则得到 "\\C:\\path" 非法路径）。
    统一为绝对路径后再返回，避免工作目录变化导致路径漂移。
    """
    from flask import current_app
    uri = current_app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not uri or not uri.startswith("sqlite:"):
        raise RuntimeError("当前数据库非 SQLite，备份功能仅支持 SQLite")

    # 去掉 "sqlite:" 协议，得到形如 ///C:/path/app.db 或 ////abs/path 的路径
    raw = uri[len("sqlite:"):]
    # 形如 :memory: 或空路径的内存库无文件可操作
    if raw in ("", ":memory:") or ":memory:" in raw:
        raise RuntimeError("内存数据库无物理文件，不支持备份/维护操作")

    # 去掉 "//" + 一个斜杠（共三个斜杠，SQLite URI 约定的绝对路径前缀）
    if raw.startswith("///"):
        candidate = raw[3:]
    else:
        candidate = raw.lstrip("/")
    if not candidate:
        raise RuntimeError("无法解析 SQLite 数据库文件路径")

    # Windows 盘符形式 "/C:/path" → "C:/path"；POSIX 的 "/volume1/..."
    # 不以 "/X:" 形式出现，保持原样
    if len(candidate) >= 3 and candidate[0] == "/" and candidate[2] == ":":
        candidate = candidate[1:]

    # 统一为绝对路径并规范分隔符
    db_path = os.path.abspath(candidate)
    if not os.path.exists(db_path):
        raise RuntimeError(f"数据库文件不存在: {db_path}")
    return db_path


def backup_dir():
    """返回备份/导出临时文件应放置的目录（与数据库同盘，避免系统盘写满）。

    生产环境系统盘（Windows C 盘）经常空间紧张，把 2GB+ 的快照写到
    tempfile 默认目录会导致 "database or disk is full"。这里统一改为
    数据库所在目录下的 backups_tmp/ 子目录：同盘既有足够空间，也便于清理。
    """
    try:
        db_path = get_db_path()
        d = os.path.join(os.path.dirname(db_path), 'backups_tmp')
    except Exception:
        # 拿不到数据库路径（如无应用上下文）时退回工作目录下的兜底目录
        d = os.path.join(os.path.abspath('.'), 'backups_tmp')
    os.makedirs(d, exist_ok=True)
    return d


def new_backup_path(suffix='.db'):
    """在备份目录下生成一个安全的临时文件路径（供导出/备份使用）。"""
    import uuid
    return os.path.join(backup_dir(), f"tmp_{uuid.uuid4().hex}{suffix}")


def _ensure_free_space(path, need_bytes):
    """写入前预检目标盘剩余空间，不足时抛出可读性强的错误。

    need_bytes 为预计写入大小（通常等于源库大小）；要求剩余空间至少是其
    1.15 倍（覆盖 WAL/journal 与页对齐开销），避免写到一半才报 SQLITE_FULL。
    """
    try:
        import shutil
        target_dir = os.path.dirname(os.path.abspath(path)) or '.'
        free = shutil.disk_usage(target_dir).free
        if free < need_bytes * 1.15:
            raise RuntimeError(
                f"目标磁盘剩余空间不足：需要约 {need_bytes / 1024 / 1024:.0f}MB，"
                f"当前仅剩 {free / 1024 / 1024:.0f}MB。请清理磁盘后重试，"
                f"或改用其他存储位置。")
    except OSError:
        # 无法探测磁盘信息时不阻断（交由后续写入阶段自行报错）
        pass


def backup_database(target_path, pages=4000, sleep=0.5, progress_cb=None):
    """在线备份：将当前 SQLite 数据库分页快照到 target_path（C-8）。

    分页备份要点：
    1. 每次只拷贝 pages 页，随后释放 SQLite 写锁并让出 CPU（sleep），
       避免长时间独占源库导致在线写请求阻塞/超时；
    2. 大库（2GB+）全量 src.backup() 会连续持有读事务且内存/IO 峰值高，
       分页后写请求可在页间隙插入，业务抖动显著降低；
    3. 若并发写入过快导致进度回退，则小幅增加等待，保持最终一致；
    4. progress_cb(percent, message) 可选：把 0-100 的进度回调给调用方
       （前端进度条据此展示），避免长时间"卡住无反应"。
    """
    src_path = get_db_path()
    src = sqlite3.connect(src_path)
    # 写入前预检剩余空间，给出明确提示而非写到一半失败
    _ensure_free_space(target_path, os.path.getsize(src_path))
    dst = sqlite3.connect(target_path)
    state = {"last": 0, "stall": 0, "total": 0}

    def _progress(status, remaining, total):
        # 并发写会让剩余页数反弹，检测到回退时累计停滞次数
        if remaining > state["last"]:
            state["stall"] += 1
        state["last"] = remaining
        state["total"] = total
        if progress_cb and total:
            # 已拷贝页数占比即为快照进度
            pct = int((total - remaining) / total * 100)
            progress_cb(pct, f"正在生成快照 {pct}%（共 {total} 页）")

    try:
        with dst:
            src.backup(dst, pages=pages, progress=_progress, sleep=sleep)
    except sqlite3.OperationalError as e:
        # 把 SQLite 的 "database or disk is full" 翻译成可操作的中文提示
        if 'disk is full' in str(e).lower() or 'database or disk' in str(e).lower():
            raise RuntimeError(
                f"数据库或磁盘已满，无法完成备份：{e}。"
                f"请清理数据库所在磁盘空间后重试。")
        raise
    finally:
        src.close()
        dst.close()
    if progress_cb:
        progress_cb(100, "快照生成完成")
    return target_path


_RESTORE_LOCK = threading.Lock()
_RESTORE_STATE = {
    'running': False,
    'stage': 'idle',
    'percent': 0,
    'message': '',
    'success': None,
    'error': '',
    'started_at': None,
    'finished_at': None,
}

# 恢复阶段：阶段标识 -> 展示文案
_RESTORE_STAGES = OrderedDict([
    ('uploading', '接收上传的快照文件'),
    ('validating', '校验快照文件'),
    ('backing_up', '备份当前数据库（回滚保险）'),
    ('restoring', '覆盖恢复业务数据'),
    ('upgrading', '升级数据库结构（建表/补列/索引）'),
    ('init_data', '初始化默认数据（权限/系统设置）'),
    ('done', '恢复完成'),
])

# 各阶段对应的整体进度百分比
_RESTORE_PERCENT = {
    'uploading': 5,
    'validating': 15,
    'backing_up': 30,
    'restoring': 60,
    'upgrading': 80,
    'init_data': 92,
    'done': 100,
}


def _set_restore_state(**kwargs):
    """线程安全地更新恢复进度状态。"""
    with _RESTORE_LOCK:
        _RESTORE_STATE.update(kwargs)


def get_restore_progress():
    """返回当前恢复进度快照（供前端轮询）。"""
    with _RESTORE_LOCK:
        state = dict(_RESTORE_STATE)
    state['stages'] = list(_RESTORE_STAGES.values())
    return state


def _set_stage(stage, message=None):
    """进入指定阶段并更新进度百分比。"""
    _set_restore_state(
        stage=stage,
        percent=_RESTORE_PERCENT.get(stage, _RESTORE_STATE.get('percent', 0)),
        message=message or _RESTORE_STAGES.get(stage, ''),
    )


def restore_database(snapshot_path):
    """恢复：将上传的快照文件内容恢复到当前数据库（不校验，内部使用）。"""
    src = sqlite3.connect(snapshot_path)
    dst = sqlite3.connect(get_db_path())
    try:
        with dst:
            src.backup(dst)
    finally:
        src.close()
        dst.close()
    return get_db_path()


def _validate_snapshot(snapshot_path):
    """校验快照文件是否为有效的 SQLite 数据库（含核心业务表）。"""
    if not os.path.exists(snapshot_path) or os.path.getsize(snapshot_path) == 0:
        raise RuntimeError('快照文件为空或不存在')
    conn = sqlite3.connect(snapshot_path)
    try:
        # 校验 SQLite 文件头
        header = conn.execute('PRAGMA schema_version').fetchone()
        if header is None:
            raise RuntimeError('快照文件不是有效的 SQLite 数据库')
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        conn.close()
    if not names:
        raise RuntimeError('快照中未包含任何数据表，可能不是本系统的备份文件')
    # 至少应包含一个核心业务表，避免误传无关 .db
    core_tables = {'user_info', 'sub_list', 'usage_list', 'data_record'}
    if not (names & core_tables):
        raise RuntimeError('快照中未包含本系统核心数据表，请确认备份来源')


def restore_database_with_progress(snapshot_path):
    """带进度的恢复主流程（供后台线程调用）。

    流程：校验 → 备份原库（回滚保险）→ 覆盖恢复 → 结构升级 → 默认数据 → 完成。
    全过程写入 _RESTORE_STATE，前端轮询 get_restore_progress() 展示进度。
    """
    _set_restore_state(running=True, success=None, error='',
                       started_at=format_utc8_time(get_utc8_time()),
                       finished_at=None)
    try:
        # 1) 校验快照
        _set_stage('validating')
        _validate_snapshot(snapshot_path)

        # 2) 恢复前先备份当前数据库，出问题时可在 instance/ 找到回滚点
        _set_stage('backing_up')
        db_path = get_db_path()
        rollback_path = os.path.join(
            os.path.dirname(db_path),
            f"恢复前自动备份_{get_utc8_time():%Y%m%d_%H%M%S}.db")
        try:
            backup_database(rollback_path)
        except Exception as e:  # 备份失败不阻断恢复，仅记录
            _set_restore_state(message=f"{_RESTORE_STAGES['backing_up']}（警告：{e}）")

        # 3) 覆盖恢复业务数据（SQLite 在线 backup，原子替换页面数据）
        _set_stage('restoring')
        restore_database(snapshot_path)

        # 4) 结构升级：补建新功能的表 / 字段 / 索引，避免旧快照缺表缺列
        _set_stage('upgrading')
        from utils.schema_upgrade import upgrade_schema
        schema_stat = upgrade_schema()

        # 5) 默认数据初始化：权限目录 / 内置角色 / 系统设置
        _set_stage('init_data')
        from utils.schema_upgrade import init_default_data
        init_default_data()

        _set_restore_state(
            running=False, success=True, stage='done', percent=100,
            message=_RESTORE_STAGES['done'],
            finished_at=format_utc8_time(get_utc8_time()),
            schema_stat=schema_stat,
        )
        return True
    except Exception as e:
        _set_restore_state(
            running=False, success=False, stage='error',
            message=f'恢复失败：{e}', error=f'{type(e).__name__}: {e}',
            finished_at=format_utc8_time(get_utc8_time()),
        )
        return False


def start_restore_async(snapshot_path):
    """启动后台恢复线程（避免长请求阻塞/超时），立即返回。

    调用前应确保无其他恢复任务在运行，否则抛出 RuntimeError。
    """
    with _RESTORE_LOCK:
        if _RESTORE_STATE.get('running'):
            raise RuntimeError('已有恢复任务正在执行，请等待完成')
    # 后台线程脱离请求上下文，需显式携带 Flask 应用以便升级/初始化使用 current_app
    from flask import current_app
    app = current_app._get_current_object()

    def _worker():
        with app.app_context():
            restore_database_with_progress(snapshot_path)

    t = threading.Thread(target=_worker, daemon=True, name='db_restore')
    t.start()
    return t