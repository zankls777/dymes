# -*- coding: utf-8 -*-
"""
汽车厂装车实际 解析引擎
======================
解析每日上传的 SAP 导出 Excel（样例：1.260927.xlsx）中的「SAP 원본」Sheet，
提取「고객 전일실적（客户前日实绩）」列作为装车实际数量，明细直接入库长期保存，
并支持按 日期 / 车种 / 品目 筛选、汇总与 Excel 导出。

SAP 원본 表结构（样例 1.260927.xlsx）：
  第 1 行：标题（#SAP 서열 원본）
  第 2 行：从第 27 列起为日期（DAY / D+1 ...）
  第 3 行：表头（HKMC 공장 / 차종 / 생관 아이템 / P/NO / 회로수 ... 고객 전일실적 ...）
  第 4 行起：数据行
"""
import os
import re
import tempfile
import time
from calendar import monthrange
from collections import OrderedDict
from datetime import date, datetime, timedelta

from db import db

try:
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:  # 供单元测试引用
    load_workbook = Workbook = None

# ---------------------------------------------------------------------------
# 业务常量
# ---------------------------------------------------------------------------
SHEET_NAME = "SAP 원본"                      # 数据源 Sheet 名（后缀模糊匹配）
SHEET_KEY_RE = re.compile(r"SAP\s*원본", re.I)

# 源文件保存目录：上传的原始 Excel 在此保留，便于下载原始文件
AUTO_LOADING_SOURCE_DIR = os.path.join(
    os.path.dirname(__file__), "static", "uploads", "auto_loading_source")

# 标准字段（韩文表头 -> 中文字段名）
F_PLANT = "工厂"
F_LEGACY = "Legacy车种"
F_VEHICLE = "车种"
F_ITEM = "品目"
F_ALC = "ALC"
F_REGION = "地区"
F_SPEC = "사양"
F_DTYPE = "D/TYPE"
F_PART = "品番"
F_CIRCUITS = "回路数"
F_QTY = "装车实际"
F_DATE = "装车日期"

# 表头别名（韩/中/英混排，兼容空格/换行差异）-> 标准字段
COLUMN_ALIASES = OrderedDict([
    (F_PLANT, ["HKMC 공장", "HKMC공장", "工厂", "공장"]),
    (F_LEGACY, ["Legacy 차종", "Legacy차종"]),
    (F_VEHICLE, ["차종", "车种", "車種", "车型"]),
    (F_ITEM, ["생관 아이템", "생관아이템", "品目", "品名"]),
    (F_ALC, ["ALC"]),
    (F_REGION, ["지역", "地区"]),
    (F_SPEC, ["사양", "仕样", "规格"]),
    (F_DTYPE, ["D/TYPE", "DTYPE"]),
    (F_PART, ["P/NO", "PNO", "品番", "품번"]),
    (F_CIRCUITS, ["회로수", "回路数", "回数"]),
    (F_QTY, ["고객 전일실적", "고객전일실적", "客户前日实绩", "装车实际", "전일실적"]),
])

TOTAL_ROW_KEYWORDS = ["TOTAL", "합계", "合計", "合计", "소계"]


# ---------------------------------------------------------------------------
# 源文件管理
# ---------------------------------------------------------------------------
def get_source_path(filename):
    """返回源文件在磁盘上的绝对路径（文件名安全处理）。"""
    os.makedirs(AUTO_LOADING_SOURCE_DIR, exist_ok=True)
    return os.path.join(AUTO_LOADING_SOURCE_DIR, os.path.basename(filename))


def save_source(source_tmp_path, filename):
    """将上传的临时源文件复制到源文件目录（保留原始文件名）。"""
    import shutil
    dst = get_source_path(filename)
    shutil.copy2(source_tmp_path, dst)
    return dst


def delete_source(filename):
    """删除源文件目录中的指定文件，返回是否删除成功。"""
    dst = get_source_path(filename)
    if os.path.exists(dst):
        try:
            os.remove(dst)
            return True
        except OSError:
            return False
    return False


# ---------------------------------------------------------------------------
# 文本 / 数值工具
# ---------------------------------------------------------------------------
def _norm_text(value):
    """单元格值归一化：去空白、转字符串、去特殊空格与换行。"""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u3000", " ").replace("\xa0", " ").replace("\n", " ")
    return " ".join(text.split())


def _upper_norm(value):
    return _norm_text(value).upper()


def _parse_number(value):
    """解析数值：支持千分位、去空格；非数值返回 None。"""
    text = _norm_text(value)
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "")
    try:
        return int(cleaned)
    except ValueError:
        pass
    try:
        f = float(cleaned)
        return int(f) if f.is_integer() else f
    except ValueError:
        return None


def _load_workbook_any(file_path):
    """加载 Excel 工作簿（只读流式，避免整簿载入内存）。

    .xlsx / .xlsm：openpyxl read_only + data_only，仅按需读取目标 Sheet 行；
    .xls：xlrd 逐行读取，仅重建目标 Sheet（跳过其他 Sheet）为内存工作簿。
    返回 (workbook, elapsed_ms)。
    """
    t0 = time.perf_counter()
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xlsm'):
        wb = load_workbook(file_path, read_only=True, data_only=True)
        return wb, (time.perf_counter() - t0) * 1000
    if ext == '.xls':
        try:
            import xlrd
        except ImportError:
            raise RuntimeError('读取 .xls 需要 xlrd：pip install xlrd')
        book = xlrd.open_workbook(file_path, on_demand=True)
        # 仅定位并重建目标 Sheet，其他 Sheet 完全跳过
        target = None
        for sh in book.sheets():
            if _norm_text(sh.name) == SHEET_NAME or SHEET_KEY_RE.search(_norm_text(sh.name)):
                target = sh
                break
        wb = Workbook()
        wb.remove(wb.active)
        if target is not None:
            ws = wb.create_sheet(title=target.name)
            for r_idx in range(target.nrows):
                row_vals = []
                for c_idx in range(target.ncols):
                    v = target.cell_value(r_idx, c_idx)
                    row_vals.append(v if v is not None and _norm_text(v) != '' else None)
                if any(v is not None for v in row_vals):
                    ws.append(row_vals)
        book.release_resources()
        return wb, (time.perf_counter() - t0) * 1000
    raise ValueError(f'不支持的文件格式：{ext}（仅支持 .xlsx / .xlsm / .xls）')


# ---------------------------------------------------------------------------
# 日期解析
# ---------------------------------------------------------------------------
def extract_date_from_filename(filename):
    """从文件名提取业务日期（实际装车日期 = 文件名日期 - 1 天）。

    支持：1.260927.xlsx（260927 → 2026-09-26）/ 26년9월27일 / 2026-09-27 / 2026年9月27日。
    文件名日期为 SAP 导出日（客户前日实绩），故实际日期取其前一天。
    """
    text = _norm_text(filename or "")
    t = text.replace("년", "年").replace("월", "月").replace("일", "日")
    base = None
    # 形如 260927（6 位紧凑日期），要求前后非数字
    m = re.search(r"(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)", t)
    if m:
        yy, mm, dd = (int(x) for x in m.groups())
        if 1 <= mm <= 12 and 1 <= dd <= 31:
            base = date(2000 + yy, mm, dd)
    if base is None:
        m2 = re.search(r"(\d{2,4})[年./\- ]?\s*(\d{1,2})[月./\- ]?\s*(\d{1,2})[日]?", t)
        if m2:
            yy = int(m2.group(1))
            if yy < 50:
                yy += 2000
            elif yy < 100:
                yy += 1900
            try:
                base = date(yy, int(m2.group(2)), int(m2.group(3)))
            except ValueError:
                base = None
    if base is None:
        return date.today().strftime("%Y-%m-%d")
    return (base - timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 表头定位
# ---------------------------------------------------------------------------
def _match_column(header_text):
    """将表头文本匹配到标准字段，未命中返回 None。"""
    if not header_text:
        return None
    t = _upper_norm(header_text)
    for std_field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            a = _upper_norm(alias)
            if t == a or t.startswith(a):
                return std_field
    return None


def _find_sap_sheet(wb):
    """定位「SAP 원본」Sheet：优先精确名，其次关键字模糊匹配。

    只返回目标 worksheet 对象，不读取其他 Sheet 的数据（read_only 下
    遍历 wb.worksheets 仅取标题，不加载单元格）。
    """
    for ws in wb.worksheets:
        if _norm_text(ws.title) == SHEET_NAME:
            return ws
    for ws in wb.worksheets:
        if SHEET_KEY_RE.search(_norm_text(ws.title)):
            return ws
    return None


# ---------------------------------------------------------------------------
# 主解析
# ---------------------------------------------------------------------------
def parse_workbook(file_path):
    """解析 SAP 导出工作簿（仅解析目标 Sheet），返回 dict。

      source_file    文件名
      sheet          数据源 Sheet 名
      header_row     表头行号
      records        [{工厂, Legacy车种, 车种, 品目, ALC, 地区, 사양, D/TYPE,
                       品番, 回路数, 装车实际, 来源Sheet, 行号}]
      errors         [{'sheet','row','level','message','value'}]
      totals         {'明细行数','总装车实际'}
      elapsed_ms     处理耗时（毫秒）
    """
    t_start = time.perf_counter()
    result = {
        "source_file": os.path.basename(file_path),
        "sheet": None,
        "header_row": None,
        "records": [],
        "errors": [],
        "totals": {"明细行数": 0, "总装车实际": 0},
        "elapsed_ms": 0.0,
    }
    wb, load_ms = _load_workbook_any(file_path)
    try:
        # 1) 定位目标 Sheet（其他 Sheet 不解析、不读取）
        ws = _find_sap_sheet(wb)
        if ws is None:
            result["errors"].append({
                "sheet": SHEET_NAME, "row": 0, "level": "ERROR",
                "message": f"未找到数据源 Sheet「{SHEET_NAME}」", "value": "",
            })
            return result

        # 2) 仅对目标 Sheet 做流式行迭代（read_only 逐行，内存占用低）
        rows_cache = list(ws.iter_rows(values_only=True))
        if not rows_cache:
            result["errors"].append({
                "sheet": ws.title, "row": 0, "level": "ERROR",
                "message": "目标 Sheet 无数据", "value": "",
            })
            return result

        header_row = _find_header_row_rows(rows_cache)
        if header_row is None:
            result["errors"].append({
                "sheet": ws.title, "row": 0, "level": "ERROR",
                "message": "未识别出有效表头（需含 P/NO 及品目等列）", "value": "",
            })
            return result
        col_map = _build_col_map_rows(rows_cache[header_row - 1])
        if F_PART not in col_map and F_ITEM not in col_map:
            result["errors"].append({
                "sheet": ws.title, "row": header_row, "level": "ERROR",
                "message": "表头缺少 P/NO 与品目列，无法解析", "value": "",
            })
            return result

        result["sheet"] = ws.title
        result["header_row"] = header_row
        total_qty = 0
        records = result["records"]
        for r in range(header_row, len(rows_cache)):
            rec = _build_record_row(rows_cache[r], col_map, r + 1)
            if rec is None:
                continue
            records.append(rec)
            total_qty += rec.get(F_QTY, 0)
        result["totals"] = {"明细行数": len(records), "总装车实际": total_qty}
    finally:
        wb.close()
    result["elapsed_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
    result["load_ms"] = round(load_ms, 1)
    return result


def _find_header_row_rows(rows, max_scan=10):
    """在已读取的行数据中定位表头行（命中标准字段最多，需含品番或品目）。"""
    best_row, best_score = None, 0
    for idx in range(min(len(rows), max_scan)):
        best_score_row = 0
        for v in rows[idx][:40]:
            if _match_column(_norm_text(v)):
                best_score_row += 1
        if best_score_row > best_score:
            best_row, best_score = idx + 1, best_score_row
    return best_row if best_score >= 4 else None


def _build_col_map_rows(header_vals):
    """由表头行值列表建立 标准字段 -> 列索引(1 基) 映射。"""
    col_map = {}
    for i, v in enumerate(header_vals, start=1):
        field = _match_column(_norm_text(v))
        if field and field not in col_map:
            col_map[field] = i
    return col_map


def _build_record_row(vals, col_map, r):
    """由一行值列表构造标准记录；非明细行返回 None。"""
    def cell(field):
        c = col_map.get(field)
        return vals[c - 1] if c and c - 1 < len(vals) else None

    part = _norm_text(cell(F_PART))
    item = _norm_text(cell(F_ITEM))
    vehicle = _norm_text(cell(F_VEHICLE))
    if not (part or item or vehicle):
        return None
    # 合计行判断：无品番且任一列文本命中 TOTAL/합계 等关键字
    if not part:
        for v in vals:
            t = _upper_norm(v)
            if t and any(k in t for k in TOTAL_ROW_KEYWORDS):
                return None

    circuits = _parse_number(cell(F_CIRCUITS)) or 0
    qty = _parse_number(cell(F_QTY))
    if qty is None:
        qty = 0

    return {
        F_PLANT: _norm_text(cell(F_PLANT)),
        F_LEGACY: _norm_text(cell(F_LEGACY)),
        F_VEHICLE: vehicle,
        F_ITEM: item,
        F_ALC: _norm_text(cell(F_ALC)),
        F_REGION: _norm_text(cell(F_REGION)),
        F_SPEC: _norm_text(cell(F_SPEC)),
        F_DTYPE: _norm_text(cell(F_DTYPE)),
        F_PART: part,
        F_CIRCUITS: circuits,
        F_QTY: qty,
        "行号": r,
    }


# ---------------------------------------------------------------------------
# 数据库持久化
# ---------------------------------------------------------------------------
def save_parsed_to_db(parsed):
    """将解析结果存入 auto_loading_record 表（长期保存）。

    同名文件重复上传时覆盖该文件旧数据（先删除再插入），其他文件历史保留。
    返回 dict：record_date / inserted / total_qty。
    """
    from models.auto_loading import AutoLoadingRecord
    src = parsed.get("source_file") or ""
    record_date = parsed.get("record_date") or extract_date_from_filename(src)
    AutoLoadingRecord.query.filter_by(source_file=src).delete()
    db.session.flush()
    rows = []
    for rec in parsed.get("records") or []:
        rows.append(AutoLoadingRecord(
            record_date=record_date,
            plant=rec.get(F_PLANT),
            legacy_vehicle=rec.get(F_LEGACY),
            vehicle=rec.get(F_VEHICLE),
            item=rec.get(F_ITEM),
            alc=rec.get(F_ALC),
            region=rec.get(F_REGION),
            spec=rec.get(F_SPEC),
            d_type=rec.get(F_DTYPE),
            part_no=rec.get(F_PART),
            circuits=rec.get(F_CIRCUITS, 0),
            qty=rec.get(F_QTY, 0),
            source_file=src,
            row_no=rec.get("行号"),
        ))
    if rows:
        db.session.add_all(rows)
    db.session.commit()
    return {
        "record_date": record_date,
        "inserted": len(rows),
        "total_qty": sum(r.get(F_QTY, 0) for r in (parsed.get("records") or [])),
    }


# ---------------------------------------------------------------------------
# 查询与汇总
# ---------------------------------------------------------------------------
def _apply_filters(q, date_from="", date_to="", vehicle="", item="", keyword="",
                   source_file=None):
    """为查询附加 日期/车种/品目/关键词/文件 过滤条件（共用）。"""
    from models.auto_loading import AutoLoadingRecord as R
    if source_file:
        if isinstance(source_file, (list, tuple)):
            if source_file:
                q = q.filter(R.source_file.in_(list(source_file)))
        else:
            q = q.filter(R.source_file == source_file)
    if date_from:
        q = q.filter(R.record_date >= date_from)
    if date_to:
        q = q.filter(R.record_date <= date_to)
    # 性能优化（H-4）：先做 strip 归一化，避免纯空白关键词触发无效全表 LIKE 扫描。
    vehicle = (vehicle or "").strip()
    item = (item or "").strip()
    keyword = (keyword or "").strip()
    if vehicle:
        q = q.filter(R.vehicle.like(f"%{vehicle}%"))
    if item:
        q = q.filter(R.item.like(f"%{item}%"))
    if keyword:
        k = f"%{keyword}%"
        q = q.filter(db.or_(R.part_no.like(k), R.vehicle.like(k),
                            R.item.like(k), R.plant.like(k)))
    return q


def count_records(date_from="", date_to="", vehicle="", item="", keyword=""):
    """DB 层 COUNT 命中条数（页面筛选用，不加载明细）。"""
    from models.auto_loading import AutoLoadingRecord
    return _apply_filters(AutoLoadingRecord.query, date_from, date_to,
                          vehicle, item, keyword).count()


def query_records(date_from="", date_to="", vehicle="", item="", keyword="",
                  source_file=None):
    """按条件从数据库筛选装车实际明细。"""
    from models.auto_loading import AutoLoadingRecord
    q = _apply_filters(AutoLoadingRecord.query, date_from, date_to,
                       vehicle, item, keyword, source_file)
    rows = q.order_by(AutoLoadingRecord.record_date, AutoLoadingRecord.id).all()
    out = []
    for r in rows:
        out.append({
            F_DATE: r.record_date,
            F_PLANT: r.plant or "",
            F_LEGACY: r.legacy_vehicle or "",
            F_VEHICLE: r.vehicle or "",
            F_ITEM: r.item or "",
            F_ALC: r.alc or "",
            F_REGION: r.region or "",
            F_SPEC: r.spec or "",
            F_DTYPE: r.d_type or "",
            F_PART: r.part_no or "",
            F_CIRCUITS: r.circuits or 0,
            F_QTY: r.qty or 0,
            "来源文件": r.source_file or "",
            "行号": r.row_no or 0,
        })
    return out


def summarize(records):
    """按 日期 + 车种 + 品目 维度汇总装车实际数量。"""
    summary_map = OrderedDict()
    for rec in records:
        key = (rec.get(F_DATE) or "", rec.get(F_VEHICLE) or "(未标注车种)",
               rec.get(F_ITEM) or "(未标注品目)")
        item = summary_map.setdefault(key, {
            F_DATE: key[0], F_VEHICLE: key[1], F_ITEM: key[2],
            "装车实际合计": 0, "明细行数": 0, "品番数": set(),
        })
        item["装车实际合计"] += rec.get(F_QTY, 0)
        item["明细行数"] += 1
        if rec.get(F_PART):
            item["品番数"].add(rec[F_PART])
    out = []
    for v in summary_map.values():
        v["品番数"] = len(v["品番数"])
        out.append(v)
    out.sort(key=lambda r: (r[F_DATE], r[F_VEHICLE], r[F_ITEM]))
    return out


def db_file_rows():
    """按文件名从数据库汇总已上传文件列表（C-9：SQL 聚合下推，避免全表加载）。"""
    from models.auto_loading import AutoLoadingRecord as R
    # 性能优化（C-9）：原先 .all() 加载全部明细后在 Python 内分组聚合，
    # 现改由 SQL 直接 GROUP BY 完成计数/求和/去重计数，仅回传每文件的汇总行。
    rows = (db.session.query(
                R.source_file,
                db.func.max(R.record_date).label('record_date'),
                db.func.count().label('rows'),
                db.func.coalesce(db.func.sum(R.qty), 0).label('qty'),
                db.func.count(db.func.distinct(R.part_no)).label('parts_len'),
                db.func.count(db.func.distinct(R.vehicle)).label('vehicles_len'),
            )
            .group_by(R.source_file)
            .order_by(db.func.max(R.record_date).desc(), R.source_file)
            .all())
    return [{
        "source_file": sf or "(未知文件)",
        "record_date": rd,
        "record_len": cnt,
        "qty": qty,
        "parts_len": parts_len,
        "vehicles_len": vehicles_len,
    } for sf, rd, cnt, qty, parts_len, vehicles_len in rows]


def db_totals():
    """数据库全部已入库文件的总览合计（C-9：单次 SQL 聚合，消除重复全表加载）。"""
    from models.auto_loading import AutoLoadingRecord as R
    # 性能优化（C-9）：原先调用 db_file_rows() 全量加载后再二次聚合，
    # 现改为一次 SQL 聚合直接得到 files/rows/parts/qty 四项合计。
    row = (db.session.query(
                db.func.count(db.func.distinct(R.source_file)),
                db.func.count(),
                db.func.count(db.func.distinct(R.part_no)),
                db.func.coalesce(db.func.sum(R.qty), 0),
            ).one())
    return {
        "files": row[0] or 0,
        "rows": row[1] or 0,
        "parts": row[2] or 0,
        "qty": row[3] or 0,
    }


def db_filter_options():
    """返回数据库中的车种/品目候选项（用于筛选表单下拉）。"""
    from models.auto_loading import AutoLoadingRecord
    vehicles = [r[0] for r in
                db.session.query(AutoLoadingRecord.vehicle).distinct().all() if r[0]]
    items = [r[0] for r in
             db.session.query(AutoLoadingRecord.item).distinct().all() if r[0]]
    return {"vehicles": vehicles, "items": items}


def available_months():
    """返回数据库中所有出现过的「年-月」列表（倒序），并确保包含当前月。"""
    from models.auto_loading import AutoLoadingRecord
    dates = [r[0] for r in AutoLoadingRecord.query.with_entities(
        AutoLoadingRecord.record_date).distinct().all() if r[0]]
    months = sorted({d[:7] for d in dates}, reverse=True)
    now = date.today()
    cur = f"{now.year:04d}-{now.month:02d}"
    if cur not in months:
        months.insert(0, cur)
    return [{"value": m, "label": f"{m[:4]}年{int(m[5:7])}月"} for m in months]


def _month_range(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    last = monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def resolve_period(scope="", month="", quarter=""):
    """把 周期快捷筛选 解析为 (date_from, date_to)；无法解析返回 None。"""
    if scope == "7d":
        today = date.today()
        return ((today - timedelta(days=6)).strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"))
    if scope == "30d":
        today = date.today()
        return ((today - timedelta(days=29)).strftime("%Y-%m-%d"),
                today.strftime("%Y-%m-%d"))
    if scope == "month" and month and len(month) >= 7:
        try:
            return _month_range(month)
        except ValueError:
            return None
    if scope == "quarter" and quarter:
        m = re.match(r"^(\d{4})-Q([1-4])$", quarter)
        if m:
            y, q = int(m.group(1)), int(m.group(2))
            first_month = (q - 1) * 3 + 1
            last_month = first_month + 2
            return (f"{y:04d}-{first_month:02d}-01",
                    f"{y:04d}-{last_month:02d}-{monthrange(y, last_month)[1]:02d}")
    return None


def upload_calendar(year=None, month=None):
    """上传日历：某月每天是否已上传（用于确认哪天遗漏上传）。"""
    from models.auto_loading import AutoLoadingRecord as R
    today = date.today()
    y = int(year) if year else today.year
    m = int(month) if month else today.month
    # 性能优化（C-9）：原先 with_entities(...).all() 加载全部(日期,文件)对后在 Python
    # 内去重；现改由 SQL distinct 去重，仅回传「日期→文件」唯一组合，数据量大幅下降。
    rows = (db.session.query(R.record_date, R.source_file)
            .distinct().all())
    dates = {rd for rd, _ in rows if rd}
    files_by_date = {}
    for rd, sf in rows:
        if not sf:
            continue
        lst = files_by_date.setdefault(rd, [])
        if sf not in lst:
            lst.append(sf)
    prefix = f"{y:04d}-{m:02d}"
    uploaded_days = sorted({int(d[8:10]) for d in dates if d.startswith(prefix)})
    first_wd, ndays = monthrange(y, m)
    days = []
    for d in range(1, ndays + 1):
        key = f"{y:04d}-{m:02d}-{d:02d}"
        days.append({
            "day": d, "date": key,
            "uploaded": d in uploaded_days,
            "files": files_by_date.get(key, []),
            "is_today": (y, m, d) == (today.year, today.month, today.day),
            "is_past": date(y, m, d) < today,
        })
    prev_m, prev_y = (12, y - 1) if m == 1 else (m - 1, y)
    next_m, next_y = (1, y + 1) if m == 12 else (m + 1, y)
    return {
        "year": y, "month": m,
        "label": f"{y}年{m}月",
        "first_weekday": first_wd,
        "days": days,
        "uploaded_count": len(uploaded_days),
        "prev": {"year": prev_y, "month": prev_m},
        "next": {"year": next_y, "month": next_m},
    }


# ---------------------------------------------------------------------------
# Excel 导出
# ---------------------------------------------------------------------------
DETAIL_HEADERS = ["装车日期", F_PLANT, F_LEGACY, F_VEHICLE, F_ITEM, F_ALC,
                  F_REGION, F_SPEC, F_DTYPE, F_PART, F_CIRCUITS, F_QTY]
SUMMARY_HEADERS = ["装车日期", F_VEHICLE, F_ITEM, "装车实际合计", "明细行数", "品番数"]


def build_detail_xlsx(date_from="", date_to="", vehicle="", item="", keyword="",
                      dest=None):
    """生成「装车实际明细」单表 Excel（write_only 流式），返回 (路径, 文件名)。"""
    from models.auto_loading import AutoLoadingRecord
    if dest is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx',
                                          prefix='autoload_detail_')
        dest = tmp.name
        tmp.close()
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("装车实际明细")
    for i, w in enumerate([12, 10, 12, 12, 16, 8, 8, 10, 8, 16, 8, 10], start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    q = _apply_filters(AutoLoadingRecord.query, date_from, date_to,
                       vehicle, item, keyword)
    q = q.order_by(AutoLoadingRecord.record_date, AutoLoadingRecord.id)
    rows = ([r.record_date, r.plant or "", r.legacy_vehicle or "", r.vehicle or "",
             r.item or "", r.alc or "", r.region or "", r.spec or "",
             r.d_type or "", r.part_no or "", r.circuits or 0, r.qty or 0]
            for r in q.yield_per(500))
    _build_write_sheet(ws, DETAIL_HEADERS, rows)
    wb.save(dest)
    name = f'汽车厂装车实际_明细_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return dest, name


PIVOT_HEADERS = ["차종", "생관 아이템", "P/NO", "회로수", "합계"]


def build_pivot_xlsx(date_from="", date_to="", vehicle="", item="", keyword="",
                     dest=None):
    """生成「装车实际横表」：行=品番(车种+品目+P/NO+回路数)，列=各日期。

    「합계」列为该行各日期数量之和；日期列取筛选范围内有数据的日期（升序）。
    返回 (路径, 文件名)。
    """
    from models.auto_loading import AutoLoadingRecord
    q = _apply_filters(AutoLoadingRecord.query, date_from, date_to,
                       vehicle, item, keyword)
    q = q.order_by(AutoLoadingRecord.record_date, AutoLoadingRecord.id)

    # 聚合：{(车种,品目,P/NO,回路数): {日期: 数量}}，并收集日期集合
    rows_map = OrderedDict()
    dates = set()
    for r in q.yield_per(500):
        d = r.record_date or ""
        if d:
            dates.add(d)
        key = (r.vehicle or "", r.item or "", r.part_no or "", r.circuits or 0)
        by_date = rows_map.setdefault(key, {})
        by_date[d] = by_date.get(d, 0) + (r.qty or 0)

    date_cols = sorted(dates)
    headers = PIVOT_HEADERS + date_cols
    data_rows = []
    for (veh, itm, part, circ), by_date in rows_map.items():
        vals = [by_date.get(d, 0) for d in date_cols]
        data_rows.append([veh, itm, part, circ, sum(vals)] + vals)

    if dest is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx',
                                          prefix='autoload_pivot_')
        dest = tmp.name
        tmp.close()
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("装车实际横表")
    fixed_widths = [12, 20, 16, 8, 10]
    for i, w in enumerate(fixed_widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for i in range(len(date_cols)):
        ws.column_dimensions[get_column_letter(len(fixed_widths) + 1 + i)].width = 12
    _build_write_sheet(ws, headers, data_rows)
    wb.save(dest)
    name = f'汽车厂装车实际_横表_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return dest, name


def _build_write_sheet(ws, title_row, rows):
    """write_only 模式写表：标题行 + 数据行，带表头样式。"""
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill('solid', fgColor='D9EAF7')
    header_align = Alignment(horizontal='center', vertical='center')

    from openpyxl.cell import WriteOnlyCell
    tmpl = WriteOnlyCell(ws, value='x')
    tmpl.font = Font(bold=True)
    tmpl.fill = header_fill
    tmpl.alignment = header_align
    tmpl.border = border
    header_style = tmpl._style

    header_cells = []
    for h in title_row:
        c = WriteOnlyCell(ws, value=h)
        c._style = header_style
        header_cells.append(c)
    ws.append(header_cells)
    for row in rows:
        ws.append(tuple(row))


def export_excel(records, output_path):
    """生成汇总 Excel（Sheet：装车实际明细 / 车种品目汇总）。

    records 可为 dict 列表（引擎查询结果）或 ORM 记录列表。
    使用 write_only 流式写出，大数据量下内存占用低。
    """
    if records and not isinstance(records[0], dict):
        src = {(r.record_date, r.plant or "", r.legacy_vehicle or "", r.vehicle or "",
                r.item or "", r.alc or "", r.region or "", r.spec or "",
                r.d_type or "", r.part_no or "", r.circuits or 0, r.qty or 0)
               for r in records}
    else:
        src = _build_from_records(records)
    wb = Workbook(write_only=True)
    _build_write_sheet(wb.create_sheet("装车实际明细"), DETAIL_HEADERS, src)
    _build_write_sheet(wb.create_sheet("车种品目汇总"), SUMMARY_HEADERS,
                       _summary_rows(sorted(src)))
    wb.save(output_path)
    return output_path


def _build_from_records(records):
    """把 dict 列表映射为 12 列元组集合。"""
    out = []
    for r in records:
        out.append((r.get(F_DATE, ""), r.get(F_PLANT, ""), r.get(F_LEGACY, ""),
                    r.get(F_VEHICLE, ""), r.get(F_ITEM, ""), r.get(F_ALC, ""),
                    r.get(F_REGION, ""), r.get(F_SPEC, ""), r.get(F_DTYPE, ""),
                    r.get(F_PART, ""), r.get(F_CIRCUITS, 0), r.get(F_QTY, 0)))
    return out


def _summary_rows(rows):
    """按 (日期,车种,品目) 聚合已排序的 12 列元组，输出汇总 6 列。"""
    agg = OrderedDict()
    for (d, _p, _l, veh, item, _a, _rg, _sp, _dt, part, _ci, qty) in rows:
        k = (d, veh or "(未标注车种)", item or "(未标注品目)")
        s = agg.setdefault(k, [0, 0, set()])
        s[0] += qty or 0
        s[1] += 1
        if part:
            s[2].add(part)
    return [[d, veh, item, qty, cnt, len(parts)]
            for (d, veh, item), (qty, cnt, parts) in agg.items()]


def export_url(date_from="", date_to="", vehicle="", item="", keyword="",
               fmt=""):
    """生成筛选导出链接（带筛选参数，fmt='pivot' 导出横表）。"""
    from urllib.parse import urlencode
    params = {'filtered': 1}
    for k, v in (('date_from', date_from), ('date_to', date_to),
                 ('vehicle', vehicle), ('item', item), ('keyword', keyword)):
        if v:
            params[k] = v
    if fmt:
        params['format'] = fmt
    return '/auto_loading/download?' + urlencode(params)
