# -*- coding: utf-8 -*-
"""
生产投入计划书自动汇总引擎
==========================
解析每日上传的「YYYY년M월D일 투입계획서.xlsx」（多Sheet投入计划文件），
自动识别 A/B/J/7/G 批次明细Sheet，清洗打标后输出四类汇总：
  1. 批次维度明细汇总（当日全量投入总表）
  2. 品番维度聚合汇总
  3. 车种+品目维度聚合汇总
  4. 团队维度统计汇总（절압팀 生产1部 / A-BAG팀）
并提供异常数据清单与 Excel 导出。

规则基于实际业务文件《26년8월26일 투입계획서.xlsx》的字段定义，
对异构表头（韩/中/英混排、列位置偏移）自适应识别。
"""
import json
import os
import re
import tempfile
from calendar import monthrange
from collections import OrderedDict
from datetime import date, timedelta

from db import db

try:
    from openpyxl import load_workbook, Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter
except ImportError:  # 供单元测试引用
    load_workbook = Workbook = None

try:
    from openpyxl.cell import WriteOnlyCell
except ImportError:
    WriteOnlyCell = None

# ---------------------------------------------------------------------------
# 业务常量
# ---------------------------------------------------------------------------
BATCHES = ["A", "B", "J", "7", "G"]          # 五类投入批次
TEAM_1_BATCHES = ["A", "B", "7", "J"]        # 절압팀（生产1部）涉及批次
TEAM_2_BATCH = ["G"]                          # A-BAG팀 涉及批次（G次）

# 投入源文件保存目录：上传的原始 Excel 在此保留，便于下载原始文件
INVEST_SOURCE_DIR = os.path.join(os.path.dirname(__file__),
                                 "static", "uploads", "invest_source")


def get_invest_source_path(filename):
    """返回投入源文件在磁盘上的绝对路径（文件名安全处理）。"""
    os.makedirs(INVEST_SOURCE_DIR, exist_ok=True)
    # 仅取文件名，防止路径穿越
    safe_name = os.path.basename(filename)
    return os.path.join(INVEST_SOURCE_DIR, safe_name)


def save_invest_source(source_tmp_path, filename):
    """将上传的临时源文件复制到投入源文件目录（保留原始文件名）。"""
    import shutil
    dst = get_invest_source_path(filename)
    shutil.copy2(source_tmp_path, dst)
    return dst


def delete_invest_source(filename):
    """删除投入源文件目录中的指定文件，返回是否删除成功。"""
    dst = get_invest_source_path(filename)
    if os.path.exists(dst):
        try:
            os.remove(dst)
            return True
        except OSError:
            return False
    return False


SHEET_KEYWORDS = ["投入计划书", "투입계획서", "투입계획"]
SKIP_SHEET_KEYWORDS = ["합계", "合计", "통계", "统计", "팀", "团队",
                       "供应商", "인수", "汇总", "说明", "SHEET"]

# 标准字段
F_BATCH = "批次"
F_VEHICLE = "车种"
F_ITEM = "品目"
F_PART = "品番"
F_LOT = "LOT/NO"
F_QTY = "投入数量"
F_CIRCUITS = "回路数"
F_TOTAL_CIRCUITS = "合计回路数"
F_REMARK = "备注"

STANDARD_FIELDS = [F_BATCH, F_VEHICLE, F_ITEM, F_PART, F_LOT,
                   F_QTY, F_CIRCUITS, F_TOTAL_CIRCUITS, F_REMARK]

# 列别名 -> 标准字段（覆盖韩/中/英多种表头写法）
COLUMN_ALIASES = {
    F_VEHICLE: ["차종", "车种", "車種", "차종/비고", "CHAJONG", "모델", "车型"],
    F_ITEM: ["품목", "品目", "品名", "ITEM", "품명", "物料", "품목/ITEM"],
    F_PART: ["品番", "품번", "P/NO", "PNO", "P/N", "품번(P/NO)", "番号", "품번/品番"],
    F_LOT: ["LOT/NO", "LOT", "로트", "LOT번호", "로트번호", "LOT NO"],
    F_QTY: ["투입수량", "投入数量", "수량", "数量", "투입량", "投入量", "수량/투입"],
    F_CIRCUITS: ["회로수", "回路数", "回数", "회로", "回路"],
    F_REMARK: ["비고", "备注", "備考", "REMARK", "비고(특이사항)", "특이사항", "备注/비고"],
}

# TOTAL 合计行关键字（任意文本单元格命中即视为合计行）
TOTAL_ROW_KEYWORDS = ["TOTAL", "합계", "합計", "合計", "合计", "計", "計/"]

# 特殊属性打标规则（子串匹配，可叠加）
TAG_RULES = OrderedDict([
    ("紧急", ["紧急", "긴급"]),
    ("配对", ["配对", "짝", "ペア"]),
    ("AS", ["AS"]),
    ("副线不投入", ["副线", "副線", "서브라인"]),
    ("海外RHD", ["RHD", "海外"]),
    ("临沂大焱", ["临沂大焱", "大焱"]),
    ("青岛京信", ["青岛京信", "京信", "칭다오"]),
    ("头高", ["头高", "두고", "頭高"]),
])

# ---------------------------------------------------------------------------
# 日期与数据库持久化
# ---------------------------------------------------------------------------
_FILENAME_DATE_RE = re.compile(
    r"((?:20\d{2})[年./\-]?(?:\d{1,2})[월월./\-]?(?:\d{1,2})[일日]?)")


# LOT编号格式：Q826G10Q
#   首字母  Q=2026年（R=2027年，按字母顺序依次类推；Q..Z 有效）
#   826     = 8月26日（10月用X、11月用Y、12月用Z 表示月份；后两位为日）
#   G       = 投入批次（1-9 / A-Z）
#   10      = 顺序号
#   末位 Q   = 工厂代码（青岛京信）
_LOT_RE = re.compile(r"^([A-Z])([1-9XYZ]\d{2})([A-Z0-9])(\d{2})([A-Z])$")


# MES678 指示数量表格识别（表名包含 MES678 的 Sheet 作为「指示数量」数据源）
_INDICATOR_SHEET_RE = re.compile(r"MES678", re.I)


def _load_workbook_any(file_path):
    """加载 Excel 工作簿：.xlsx / .xlsm 走 openpyxl；.xls（旧版二进制）经 xlrd 全量读出后
    重建为内存工作簿，后续解析逻辑与 .xlsx 完全一致。无法读取时抛出明确异常。"""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.xlsx', '.xlsm'):
        return load_workbook(file_path, read_only=False, data_only=True)
    if ext == '.xls':
        try:
            import xlrd
        except ImportError:
            raise RuntimeError('读取 .xls 需要 xlrd：pip install xlrd')
        book = xlrd.open_workbook(file_path)
        wb = Workbook()
        wb.remove(wb.active)
        for sh in book.sheets():
            ws = wb.create_sheet(title=sh.name)
            for r_idx in range(sh.nrows):
                for c_idx in range(sh.ncols):
                    v = sh.cell_value(r_idx, c_idx)
                    if v is not None and _norm_text(v) != '':
                        ws.cell(row=r_idx + 1, column=c_idx + 1, value=v)
        return wb
    raise ValueError(f'不支持的文件格式：{ext}（仅支持 .xlsx / .xlsm / .xls）')


def parse_lot(lot_text):
    """解析LOT编号，返回 dict(year/month/day/date/batch/seq/plant)；无法解析返回 None。"""
    t = _norm_text(lot_text).upper()
    if not t:
        return None
    m = _LOT_RE.match(t)
    if not m:
        return None
    ych, d3, batch_ch, seq, plant = m.groups()
    if not ("Q" <= ych <= "Z"):      # 年份仅支持 2026( Q ) 起的字母
        return None
    year = 2026 + (ord(ych) - ord("Q"))
    first = d3[0]
    if first.isdigit():
        month = int(first)
    else:
        month = {"X": 10, "Y": 11, "Z": 12}[first]
    day = int(d3[1:])
    try:
        max_day = monthrange(year, month)[1]
    except ValueError:
        return None
    if not (1 <= day <= max_day):
        return None
    batch = batch_ch if batch_ch != "0" else None   # 0 不视为批次
    return {
        "year": year, "month": month, "day": day,
        "date": f"{year:04d}-{month:02d}-{day:02d}",
        "batch": batch, "seq": seq, "plant": plant,
    }


def extract_date_from_filename(filename):
    """从文件名提取业务日期。

    支持：26년8월26일 / 2026-08-26 / 2026年8月26日 / 2026.8.26 / 8月26日（无年份时按当年）
    示例：26년8월26일 투입계획서.xlsx -> 2026-08-26
    """
    text = _norm_text(filename or "")
    # 归一化韩文日期字为中文等价，简化后续匹配
    t = text.replace("년", "年").replace("월", "月").replace("일", "日")
    # 年[月][日]：年 2~4 位，要求「年」与「月」有分隔符（年./- 空格 或 年月日字）
    m = re.search(r"(\d{2,4})[年./\- ]?\s*(\d{1,2})[月./\- ]?\s*(\d{1,2})[日]?", t)
    if m:
        yy = int(m.group(1))
        if yy < 50:          # 2 位年份 → 20xx（如 26 → 2026）
            yy = 2000 + yy
        elif yy < 100:       # 2 位年份 50-99 → 19xx（防御性）
            yy = 1900 + yy
        return f"{yy:04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 仅 月/日（如 8月26일），按当前年份
    m2 = re.search(r"(\d{1,2})[月./\- ]?\s*(\d{1,2})[日]?", t)
    if m2:
        return f"{date.today().year}-{int(m2.group(1)):02d}-{int(m2.group(2)):02d}"
    return date.today().strftime("%Y-%m-%d")


def save_parsed_to_db(parsed):
    """将解析结果存入 investment_record 表（长期保存，数据直接入库）。

    同名文件重复上传时覆盖该文件旧数据（先删除再插入），其他文件历史保留。
    仅保存「有投入数量」（qty > 0）的记录，无数量的记录不入库。
    LOT 编号在系统中唯一且不可重复：
      - 文件内同一 LOT 只保留首条；
      - 与库中其它文件已存在的 LOT 重复时跳过并记录提醒。
    返回 dict：record_date / inserted(入库条数) / skipped_dup(因重复跳过条数) /
    dup_lots(与库内重复的LOT) / in_dup_count(文件内重复LOT数) / indicator_total(MES678指示数量合计) /
    input_total(投入数量合计) 。
    """
    from models.invest_record import InvestmentRecord
    src = parsed.get("source_file") or ""
    record_date = extract_date_from_filename(src)
    InvestmentRecord.query.filter_by(source_file=src).delete()
    db.session.flush()          # 使删除立即生效，避免与自身文件 LOT 误判重复
    rows = []
    dup_lots, in_dup = [], set()
    seen_in_file = set()

    # 性能优化：先收集本批所有 LOT，一次性查库建立「已存在的 LOT 集合」，
    # 避免在循环内逐条 InvestmentRecord.query.filter(...).first() 产生 N+1 查询。
    pending_records = [rec for rec in (parsed.get("records") or []) if rec.get(F_QTY, 0) > 0]
    lot_candidates = {(rec.get(F_LOT) or "").strip().upper() for rec in pending_records}
    lot_candidates.discard("")
    existing_lots = set()
    if lot_candidates:
        existing_lots = {
            (row[0] or "").strip().upper()
            for row in db.session.query(db.func.upper(InvestmentRecord.lot)).filter(
                db.func.upper(InvestmentRecord.lot).in_(list(lot_candidates))
            ).all()
        }

    for rec in pending_records:
        lot_text = (rec.get(F_LOT) or "").strip()
        if lot_text:
            top = lot_text.upper()
            if top in seen_in_file:            # 文件内重复 LOT → 只保留首条
                in_dup.add(top)
                continue
            seen_in_file.add(top)
            if top in existing_lots:            # 库中其它文件已存在该 LOT
                dup_lots.append(lot_text)
                continue
        rows.append(InvestmentRecord(
            record_date=record_date,
            batch=rec.get(F_BATCH),
            vehicle=rec.get(F_VEHICLE),
            item=rec.get(F_ITEM),
            part_no=rec.get(F_PART),
            lot=rec.get(F_LOT),
            qty=rec.get(F_QTY, 0),
            circuits=rec.get(F_CIRCUITS, 0),
            total_circuits=rec.get(F_TOTAL_CIRCUITS, 0),
            remark=rec.get(F_REMARK) or "",
            tags_json=json.dumps(rec.get("标签", []), ensure_ascii=False),
            source_file=src,
            row_no=rec.get("行号"),
        ))
    if rows:
        db.session.add_all(rows)
    db.session.commit()
    return {
        "record_date": record_date,
        "inserted": len(rows),
        "skipped_dup": len(dup_lots) + len(in_dup),
        "dup_lots": dup_lots,
        "in_dup_count": len(in_dup),
        "indicator_total": parsed.get("indicator_total") or 0,
        "input_total": sum(r.get(F_QTY, 0) for r in (parsed.get("records") or [])),
    }


def query_records(date_from="", date_to="", vehicle="", batch="", keyword="", source_file=None):
    """按条件从数据库筛选投入明细（长期保存数据）。

    source_file 可传单字符串（精确匹配）或文件名列表（IN 查询），
    批量/单文件下载时由数据库过滤，避免 Python 端全表分组。
    """
    from models.invest_record import InvestmentRecord
    q = InvestmentRecord.query
    if source_file:
        if isinstance(source_file, (list, tuple)):
            if source_file:
                q = q.filter(InvestmentRecord.source_file.in_(list(source_file)))
        else:
            q = q.filter(InvestmentRecord.source_file == source_file)
    if date_from:
        q = q.filter(InvestmentRecord.record_date >= date_from)
    if date_to:
        q = q.filter(InvestmentRecord.record_date <= date_to)
    # 性能优化（H-4）：strip 归一化，避免纯空白关键词触发无效全表 LIKE。
    vehicle = (vehicle or "").strip()
    keyword = (keyword or "").strip()
    if vehicle:
        q = q.filter(InvestmentRecord.vehicle.like(f"%{vehicle}%"))
    if batch:
        q = q.filter(InvestmentRecord.batch == batch)
    if keyword:
        k = f"%{keyword}%"
        q = q.filter(db.or_(
            InvestmentRecord.part_no.like(k),
            InvestmentRecord.lot.like(k),
            InvestmentRecord.remark.like(k),
            InvestmentRecord.vehicle.like(k),
        ))
    rows = q.order_by(InvestmentRecord.record_date,
                      InvestmentRecord.id).all()
    records = []
    for r in rows:
        try:
            tags = json.loads(r.tags_json or "[]")
        except (ValueError, TypeError):
            tags = []
        records.append({
            F_BATCH: r.batch,
            "记录日期": r.record_date,
            F_VEHICLE: r.vehicle or "",
            F_ITEM: r.item or "",
            F_PART: r.part_no or "",
            F_LOT: r.lot or "",
            F_QTY: r.qty or 0,
            F_CIRCUITS: r.circuits or 0,
            F_TOTAL_CIRCUITS: r.total_circuits or 0,
            F_REMARK: r.remark or "",
            "标签": tags,
            "来源Sheet": r.source_file or "",
            "来源文件": r.source_file or "",
            "行号": r.row_no or 0,
        })
    return records


def db_file_rows():
    """按文件名从数据库汇总已上传文件列表（不依赖会话Cookie）。

    返回每文件：source_file / record_date / record_len(明细行数) /
    parts_len(有量品番数) / detail_sheets(批次徽章列表)。
    """
    from models.invest_record import InvestmentRecord
    rows = (InvestmentRecord.query
            .order_by(InvestmentRecord.record_date.desc(),
                      InvestmentRecord.source_file)
            .all())
    files = {}
    for r in rows:
        key = r.source_file or "(未知文件)"
        f = files.setdefault(key, {
            "source_file": key, "record_date": r.record_date,
            "rows": 0, "parts": set(), "batches": set(),
        })
        f["rows"] += 1
        if r.part_no:
            f["parts"].add(r.part_no)
        if r.batch:
            f["batches"].add(r.batch)
    out = []
    for f in files.values():
        batches = sorted(f["batches"],
                         key=lambda b: BATCHES.index(b) if b in BATCHES else 99)
        out.append({
            "source_file": f["source_file"],
            "record_date": f["record_date"],
            "record_len": f["rows"],
            "parts_len": len(f["parts"]),
            "detail_sheets": [{"name": b, "batch": b} for b in batches],
        })
    return out


def db_totals():
    """数据库全部已入库文件的总览合计（文件数/明细行数/有量品番数/总数量/总回路）。"""
    from models.invest_record import InvestmentRecord
    files = db_file_rows()
    parts = (InvestmentRecord.query
             .with_entities(InvestmentRecord.part_no).distinct().count())
    qty = (InvestmentRecord.query
           .with_entities(db.func.coalesce(
               db.func.sum(InvestmentRecord.qty), 0)).scalar() or 0)
    circuits = (InvestmentRecord.query
                .with_entities(db.func.coalesce(
                    db.func.sum(InvestmentRecord.total_circuits), 0)).scalar() or 0)
    return {
        "files": len(files),
        "rows": sum(f["record_len"] for f in files),
        "parts": parts,
        "qty": qty,
        "circuits": circuits,
    }


def db_batch_options():
    """返回数据库中的批次/车种候选项（用于筛选表单下拉）。"""
    from models.invest_record import InvestmentRecord
    batches = [r[0] for r in
               db.session.query(InvestmentRecord.batch).distinct().all()
               if r[0]]
    vehicles = [r[0] for r in
                db.session.query(InvestmentRecord.vehicle).distinct().all()
                if r[0]]
    return {"batches": batches, "vehicles": vehicles}


def available_months():
    """返回数据库中所有出现过的「年-月」列表（倒序），并确保包含当前月（供筛选/日历下拉）。"""
    from models.invest_record import InvestmentRecord
    dates = [r[0] for r in InvestmentRecord.query.with_entities(
        InvestmentRecord.record_date).distinct().all() if r[0]]
    months = sorted({d[:7] for d in dates}, reverse=True)
    now = date.today()
    cur = f"{now.year:04d}-{now.month:02d}"
    if cur not in months:
        months.insert(0, cur)
    return [{"value": m, "label": f"{m[:4]}年{int(m[5:7])}月"} for m in months]


def _month_range(ym):
    """'YYYY-MM' -> (date_from, date_to)。"""
    y, m = int(ym[:4]), int(ym[5:7])
    last = monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def resolve_period(scope="", month="", quarter=""):
    """把 周期快捷筛选 解析为 (date_from, date_to)；无法解析时返回 None。

    scope=month 且 month=YYYY-MM → 该自然月
    scope=quarter 且 quarter=YYYY-Qn（n=1..4）→ 该自然季度
    """
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
            y = int(m.group(1))
            q = int(m.group(2))
            first_month = (q - 1) * 3 + 1
            first = f"{y:04d}-{first_month:02d}-01"
            last_month = first_month + 2
            last = monthrange(y, last_month)[1]
            return first, f"{y:04d}-{last_month:02d}-{last:02d}"
    return None


def upload_calendar(year=None, month=None):
    """上传日历：某月每天是否已上传/是否今天/是否过去（用于确认哪天遗漏上传）。

    year/month 缺省取当前月。返回 dict：label/label_short/first_weekday/days/uploaded_count。
    """
    from models.invest_record import InvestmentRecord
    today = date.today()
    y = int(year) if year else today.year
    m = int(month) if month else today.month
    dates = {r[0] for r in InvestmentRecord.query.with_entities(
        InvestmentRecord.record_date).distinct().all()}
    prefix = f"{y:04d}-{m:02d}"
    # 各日期 -> 文件名（用于日历双击跳转到对应文件入口）
    rows = InvestmentRecord.query.with_entities(
        InvestmentRecord.record_date, InvestmentRecord.source_file).all()
    files_by_date = {}
    for rd, sf in rows:
        files_by_date.setdefault(rd, [])
        if sf and sf not in files_by_date[rd]:
            files_by_date[rd].append(sf)
    uploaded_days = sorted({int(d[8:10]) for d in dates if d.startswith(prefix)})
    first_wd, ndays = monthrange(y, m)          # 0=周一 ... 6=周日
    days = []
    for d in range(1, ndays + 1):
        day_date = date(y, m, d)
        key = f"{y:04d}-{m:02d}-{d:02d}"
        days.append({
            "day": d,
            "date": key,
            "uploaded": d in uploaded_days,
            "files": files_by_date.get(key, []),
            "is_today": (y, m, d) == (today.year, today.month, today.day),
            "is_past": day_date < today,
        })
    # 用于月导航链接
    prev_m = m - 1
    prev_y = y
    if prev_m == 0:
        prev_m, prev_y = 12, y - 1
    next_m = m + 1
    next_y = y
    if next_m == 13:
        next_m, next_y = 1, y + 1
    return {
        "year": y, "month": m,
        "label": f"{y}年{m}月",
        "label_short": f"{y}-{m:02d}",
        "first_weekday": first_wd,
        "days": days,
        "uploaded_days": uploaded_days,
        "uploaded_count": len(uploaded_days),
        "prev": {"year": prev_y, "month": prev_m},
        "next": {"year": next_y, "month": next_m},
    }


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------
def _norm_text(value):
    """单元格值归一化：去空白、转字符串、去特殊空格。"""
    if value is None:
        return ""
    text = str(value)
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return text.strip()


def _upper_norm(value):
    return _norm_text(value).upper()


# ---------------------------------------------------------------------------
# Sheet 识别
# ---------------------------------------------------------------------------
def _detect_batch(sheet_name):
    """从 Sheet 名中识别批次号。

    支持单字符批次：0-9 数字与 A-Z 字母（如 A/B/J/7/G/6/8/D/F 等）。
    识别失败返回 None。

    匹配优先级：
      1. 「字符 + 批次后缀(次/회/차/호/批次)」明确标记，如 6차、B次、D회、7호
      2. 「字符 + 投入/계획」相关词，如 A투입、6投入
      3. 名称开头为单字符批次且紧邻后缀/投入词
    关键：用 (?<![0-9]) 负向后顾排除日期数字（如 09.07 中的 7 不误判为 7 次）。
    """
    name = _upper_norm(sheet_name)
    # 1) 明确批次标记：单字符(0-9/A-Z) + 批次后缀，字符前不能是数字（避免 07→7）
    m = re.search(r"(?<![0-9])([0-9A-Z])(?:次|회|차|호|批次)", name)
    if m:
        return m.group(1)
    # 2) 单字符 + 投入/计划相关词，字符前不能是数字
    m2 = re.search(r"(?<![0-9])([0-9A-Z])\s*(?:투입계획서|투입계획|투입|投入计划书|投入计划|投入)", name)
    if m2:
        return m2.group(1)
    # 3) 名称开头为单字符批次且紧邻后缀/投入词
    m3 = re.match(r"^([0-9A-Z])(?:次|회|차|호|批次)?\s*(?:투입|投入|계획|计划)", name)
    if m3:
        return m3.group(1)
    return None


def _is_detail_sheet(sheet_name):
    """判断是否为业务明细Sheet（含投入计划书关键字或批次标记）。"""
    name = _norm_text(sheet_name)
    if not name:
        return False
    has_keyword = any(k in name for k in SHEET_KEYWORDS)
    batch = _detect_batch(name)
    return has_keyword or batch is not None


def _is_skip_sheet(sheet_name):
    """判断是否为应跳过的统计/供应商等非明细Sheet。"""
    name = _norm_text(sheet_name)
    if not name:
        return True
    return any(k in name for k in SKIP_SHEET_KEYWORDS)


def _is_total_row(row_values):
    """判断一行是否为 TOTAL 合计行（文本单元格命中关键字）。"""
    for v in row_values:
        text = _upper_norm(v)
        if not text:
            continue
        for kw in TOTAL_ROW_KEYWORDS:
            if kw in text:
                # 仅当整行没有明细特征（无品番/LOT）时才视为合计行，避免误伤备注含“合计”的明细
                has_detail = any(_looks_like_part_or_lot(_norm_text(x)) for x in row_values)
                if not has_detail:
                    return True
    return False


def _looks_like_part_or_lot(text):
    if not text:
        return False
    return re.search(r"（?P/NO|PNO", text.upper(), re.I) is not None or \
        re.match(r"^Q[A-Z0-9]{5,}Q$", _upper_norm(text)) is not None


# ---------------------------------------------------------------------------
# 表头定位与列映射
# ---------------------------------------------------------------------------
def _find_header_row_and_columns(ws, max_header_scan=12):
    """扫描前若干行定位表头，返回 (表头行索引, 列映射{标准字段: 列索引})。

    异构表头适配：对每行建立『列名 -> 标准字段』映射，取命中字段数量最多的行。
    """
    best = (None, {})
    best_score = 0
    for r_idx in range(1, min(ws.max_row, max_header_scan) + 1):
        mapping = {}
        score = 0
        for c_idx in range(1, min(ws.max_column, 30) + 1):
            header_text = _upper_norm(ws.cell(row=r_idx, column=c_idx).value)
            if not header_text:
                continue
            std_field = _match_column(header_text)
            if std_field and std_field not in mapping:
                mapping[std_field] = c_idx
                score += 1
        if score > best_score:
            best_score = score
            best = (r_idx, mapping)
    # 表头必须能识别出核心字段（品番 或 LOT，且至少一个数值列）
    if best_score >= 3 and (F_PART in best[1] or F_LOT in best[1]):
        return best
    return (None, {})


def _match_column(header_text):
    """将归一化表头文本匹配到标准字段，未命中返回 None。"""
    for std_field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            # 精确匹配或整体包含（如 “품번(P/NO)” 与 “품번(P/NO)”）
            if alias == header_text or header_text == _upper_norm(alias):
                return std_field
            # 兼容 “P/NO(品番)” 等组合写法：别名位于文本首或尾
            if header_text.startswith(_upper_norm(alias)):
                return std_field
    return None


# ---------------------------------------------------------------------------
# 数值解析
# ---------------------------------------------------------------------------
def _sum_indicator_qty(ws):
    """定位 MES678 Sheet 的「指示数量」列并求合计；未找到该列返回 0。"""
    for r_idx in range(1, min(ws.max_row, 12) + 1):
        for c_idx in range(1, min(ws.max_column, 30) + 1):
            h = _upper_norm(ws.cell(row=r_idx, column=c_idx).value)
            if not h:
                continue
            if h == "指示数量" or h.startswith("指示数量") or h in ("지시수량", "指示수량"):
                total = 0
                for r in range(r_idx + 1, ws.max_row + 1):
                    v = _parse_number(ws.cell(row=r, column=c_idx).value)
                    if v:
                        total += v
                return total
    return 0


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
        if f.is_integer():
            return int(f)
        return f
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# 打标
# ---------------------------------------------------------------------------
def _tag_remark(remark_text):
    """识别备注中的特殊属性，返回标签列表。"""
    text = _upper_norm(remark_text)
    tags = []
    for tag, keywords in TAG_RULES.items():
        for kw in keywords:
            if kw in text:
                tags.append(tag)
                break
    return tags


# ---------------------------------------------------------------------------
# 主解析
# ---------------------------------------------------------------------------
def parse_workbook(file_path):
    """解析投入计划书工作簿。

    返回 dict:
      source_file      文件名
      detail_sheets    [{'name','batch','header_row','rows'}]
      records          [{'批次','车种','品目','品番','LOT/NO','投入数量','回路数',
                         '合计回路数','备注','标签':[], '来源Sheet','行号'}]
      skipped_sheets   [sheet名] 非明细Sheet
      errors           [{ 'sheet', 'row', 'level', 'message', 'value' }]
      batch_stats      {批次: 明细行数}
    """
    result = {
        "source_file": os.path.basename(file_path),
        "detail_sheets": [],
        "records": [],
        "skipped_sheets": [],
        "errors": [],
        "batch_stats": OrderedDict((b, 0) for b in BATCHES),
        "indicator_entries": [],     # MES678 指示数量明细（表名/指示数量合计）
        "indicator_total": 0,        # MES678 指示数量合计
    }

    wb = _load_workbook_any(file_path)
    for ws in wb.worksheets:
        sheet_name = ws.title
        # MES678 指示数量表：单独提取「指示数量」列合计，不作为投入批次明细
        if _INDICATOR_SHEET_RE.search(sheet_name):
            indi_total = _sum_indicator_qty(ws)
            result["indicator_entries"].append({
                "sheet": sheet_name, "indicator_total": indi_total,
            })
            result["indicator_total"] += indi_total
            if ws.max_row or ws.max_column:
                result["skipped_sheets"].append(sheet_name)
            continue
        if _is_skip_sheet(sheet_name) or not _is_detail_sheet(sheet_name):
            if ws.max_row or ws.max_column:
                result["skipped_sheets"].append(sheet_name)
                # 记录跳过原因，便于追踪批次识别异常（如 6次/D次 未识别等）
                skip_reason = '含跳过关键字' if _is_skip_sheet(sheet_name) else '未识别为投入明细Sheet（无投入关键词且未识别出批次标记）'
                result["errors"].append({
                    "sheet": sheet_name, "row": 0, "level": "INFO",
                    "message": f"Sheet已跳过（{skip_reason}）", "value": sheet_name
                })
            continue

        batch = _detect_batch(sheet_name)
        header_row, col_map = _find_header_row_and_columns(ws)
        if header_row is None:
            result["errors"].append({
                "sheet": sheet_name, "row": 0, "level": "WARN",
                "message": "未识别出有效表头，Sheet已跳过", "value": ""
            })
            continue

        sheet_records = []
        for r_idx in range(header_row + 1, ws.max_row + 1):
            values = {f: ws.cell(row=r_idx, column=c).value for f, c in col_map.items()}

            # 跳过全空行
            if not any(_norm_text(v) for v in values.values()):
                continue

            # 跳过 TOTAL 合计行
            if _is_total_row(list(values.values())):
                continue

            row_values = [ws.cell(row=r_idx, column=c).value for c in
                          range(1, min(ws.max_column, 30) + 1)]
            record = _build_record(values, batch, sheet_name, r_idx, row_values,
                                   result["errors"], qty_col=col_map.get(F_QTY),
                                   circuits_col=col_map.get(F_CIRCUITS))
            if record is None:
                continue
            sheet_records.append(record)
            result["records"].append(record)
            batch_key = record[F_BATCH] or ("-" if batch is None else batch)
            result["batch_stats"][batch_key] = result["batch_stats"].get(batch_key, 0) + 1

        result["detail_sheets"].append({
            "name": sheet_name, "batch": batch, "header_row": header_row,
            "rows": sheet_records,
        })
    wb.close()
    return result


def _build_record(values, batch, sheet_name, row_idx, row_values, errors, qty_col=None, circuits_col=None):
    """由原始单元格构造标准记录；批次优先取 LOT 解析结果，其次用 Sheet 名批次。

    回路数（单件）取值规则——按列布局判别：
      布局A（数量 | 单件回路数[表头空] | 回路数=合计）：
          数量列右侧紧邻列即为单件回路数（表头常为空，未被映射为「回路数」）。
      布局B（投入数量 | 回路数=合计 | 合计回路数）：
          「回路数」列紧邻数量列右侧，该列实际存的是合计值（=数量×单件），
          此时单件回路数 = 回路数 ÷ 数量。
      判别：若「回路数」列(circuits_col)恰好等于数量列右侧紧邻列(qty_col+1)，
          则按布局B处理（合计÷数量）；否则按布局A取紧邻列作为单件。
      紧邻列无有效数值时回退到「回路数」列原值（兼容无数量列等其它布局）。
    """
    rec = {
        F_BATCH: batch,
        F_VEHICLE: _norm_text(values.get(F_VEHICLE)),
        F_ITEM: _norm_text(values.get(F_ITEM)),
        F_PART: _norm_text(values.get(F_PART)),
        F_LOT: _norm_text(values.get(F_LOT)),
        F_QTY: 0,
        F_CIRCUITS: 0,
        F_TOTAL_CIRCUITS: 0,
        F_REMARK: _norm_text(values.get(F_REMARK)),
        "标签": [],
        "来源Sheet": sheet_name,
        "行号": row_idx,
    }

    # 明细行判定：必须至少存在 品番/LOT/车种 之一，否则视为杂行
    if not (rec[F_PART] or rec[F_LOT] or rec[F_VEHICLE] or rec[F_ITEM]):
        return None

    # 批次优先取 LOT 解析结果（Q826G10Q → G）；LOT 无法解析时保留 Sheet 名批次
    lot_info = parse_lot(rec[F_LOT])
    if lot_info and lot_info.get("batch"):
        rec[F_BATCH] = lot_info["batch"]

    qty = _parse_number(values.get(F_QTY))

    # 数量列右侧紧邻列下标（row_values 为 0 基，qty_col 为 1 基，
    # 数量列对应下标 qty_col-1，其右侧紧邻列对应下标 qty_col）
    right_neighbor_idx = qty_col if qty_col else None

    circuits = None
    circuits_raw = None

    if qty_col and right_neighbor_idx is not None and right_neighbor_idx < len(row_values):
        # 布局判别：「回路数」列是否紧邻数量列右侧
        if circuits_col == qty_col + 1:
            # 布局B：紧邻列为「回路数」合计值，单件 = 合计 ÷ 数量
            circuits_raw = row_values[right_neighbor_idx]
            total_val = _parse_number(circuits_raw)
            if total_val is not None and qty:
                circuits = total_val // qty
            else:
                circuits = total_val
        else:
            # 布局A：紧邻列为单件回路数（表头常为空，未被映射）
            circuits_raw = row_values[right_neighbor_idx]
            circuits = _parse_number(circuits_raw)

    # 紧邻列无有效数值时，回退到表头映射的「回路数」列原值（兼容其它模板布局）
    if circuits is None:
        circuits_raw = values.get(F_CIRCUITS)
        circuits = _parse_number(circuits_raw)

    if qty is None and _norm_text(values.get(F_QTY)):
        errors.append({
            "sheet": sheet_name, "row": row_idx, "level": "ERROR",
            "message": "投入数量含非数值内容", "value": _norm_text(values.get(F_QTY)),
        })
        qty = 0
    if circuits is None and _norm_text(circuits_raw):
        errors.append({
            "sheet": sheet_name, "row": row_idx, "level": "ERROR",
            "message": "回路数含非数值内容", "value": _norm_text(circuits_raw),
        })
        circuits = 0
    if qty is None:
        qty = 0
    if circuits is None:
        circuits = 0

    rec[F_QTY] = qty
    rec[F_CIRCUITS] = circuits
    rec[F_TOTAL_CIRCUITS] = qty * circuits
    rec["标签"] = _tag_remark(rec[F_REMARK])

    if not rec[F_PART] and not rec[F_LOT]:
        errors.append({
            "sheet": sheet_name, "row": row_idx, "level": "WARN",
            "message": "缺少品番与LOT，已按普通行保留", "value": ""
        })
    return rec


# ---------------------------------------------------------------------------
# 四类汇总
# ---------------------------------------------------------------------------
def summarize(records):
    """基于清洗后的记录计算四类汇总。

    返回 dict:
      batch_detail    全量明细（含批次列）
      part_summary    品番维度聚合
      vehicle_item    车种+品目维度聚合
      team_stats      절압팀 / A-BAG팀 团队统计（公共回路暂不输出）
    """
    batch_detail = list(records)

    # 品番维度（只整理有数量且在品番列有值的记录，"无数量的无需整理"）
    part_map = OrderedDict()
    for rec in records:
        if not rec.get(F_PART) or rec.get(F_QTY, 0) <= 0:
            continue
        key = rec.get(F_PART)
        item = part_map.setdefault(key, {
            F_PART: rec.get(F_PART),
            F_VEHICLE: rec.get(F_VEHICLE),
            F_ITEM: rec.get(F_ITEM),
            F_QTY: 0,
            F_TOTAL_CIRCUITS: 0,
            "涉及批次": [],
        })
        item[F_QTY] += rec.get(F_QTY, 0)
        item[F_TOTAL_CIRCUITS] += rec.get(F_TOTAL_CIRCUITS, 0)
        if rec.get(F_BATCH) and rec[F_BATCH] not in item["涉及批次"]:
            item["涉及批次"].append(rec[F_BATCH])

    part_summary = [dict(v) for v in part_map.values()]
    part_summary.sort(key=lambda r: (r[F_PART] or ""))

    # 车种+品目维度（同样只统计有数量的记录）
    vi_map = OrderedDict()
    for rec in records:
        if rec.get(F_QTY, 0) <= 0:
            continue
        key = (rec.get(F_VEHICLE) or "(未标注车种)", rec.get(F_ITEM) or "(未标注品目)")
        item = vi_map.setdefault(key, {
            F_VEHICLE: key[0], F_ITEM: key[1],
            F_QTY: 0, F_TOTAL_CIRCUITS: 0, "涉及批次": [],
        })
        item[F_QTY] += rec.get(F_QTY, 0)
        item[F_TOTAL_CIRCUITS] += rec.get(F_TOTAL_CIRCUITS, 0)
        if rec.get(F_BATCH) and rec[F_BATCH] not in item["涉及批次"]:
            item["涉及批次"].append(rec[F_BATCH])

    vehicle_item = [dict(v) for v in vi_map.values()]
    vehicle_item.sort(key=lambda r: (r[F_VEHICLE] or "", r[F_ITEM] or ""))

    # 团队维度
    team_1_total = {"A": 0, "B": 0, "7": 0, "J": 0}
    team_1_sum = 0
    team_2_g = 0
    for rec in records:
        b = rec.get(F_BATCH)
        circuits = rec.get(F_TOTAL_CIRCUITS, 0)
        if b in team_1_total:
            team_1_total[b] += circuits
            team_1_sum += circuits
        elif b == "G":
            team_2_g += circuits

    team_rows = []
    for b in TEAM_1_BATCHES:
        if team_1_total[b] <= 0:
            continue  # 未投入的批次不展示（空值不显示 0）
        ratio = (team_1_total[b] / team_1_sum * 100) if team_1_sum else 0
        team_rows.append({
            "批次": b, "总回路数": team_1_total[b],
            "占比%": round(ratio, 2), "团队": "절압팀(生产1部)",
        })
    if not team_rows:
        team_rows = []
    team_rows.append({
        "批次": "G", "总回路数": team_2_g,
        "占比%": None, "团队": "A-BAG팀",
    })
    if team_2_g == 0 and not team_rows[:-1]:
        team_rows = [team_rows[-1]]

    team_stats = {
        "절압팀": {"批次": TEAM_1_BATCHES, "汇总": team_rows[:-1],
                   "总回路": team_1_sum},
        "A-BAG팀": {"批次": ["G"], "总回路": team_2_g,
                    "G次回路": team_2_g},
    }

    return {
        "batch_detail": batch_detail,
        "part_summary": part_summary,
        "vehicle_item": vehicle_item,
        "team_stats": team_stats,
        "totals": {
            "明细行数": len(records),
            "总投入数量": sum(r.get(F_QTY, 0) for r in records),
            "总回路数": sum(r.get(F_TOTAL_CIRCUITS, 0) for r in records),
        },
        "tag_counts": _count_tags(records),
    }


def _count_tags(records):
    counts = OrderedDict()
    for rec in records:
        for tag in rec["标签"]:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Excel 导出（write_only 流式，避免普通模式逐格样式开销）
# ---------------------------------------------------------------------------
def _build_write_sheet(ws, title_row, rows, widths=None, warn_indices=None):
    """write_only 模式写表：标题行 + 数据行，带表头样式和警告行高亮。

    样式对象只创建一次（预解析 _style），单元格通过 WriteOnlyCell(ws) + _style 赋值。
    write_only 模式不支持冻结窗格和自动筛选（openpyxl 限制），但可设列宽。
    """
    warn_indices = warn_indices or set()

    # 共享样式（只建一次）
    thin = Side(style='thin', color='CCCCCC')
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill('solid', fgColor='D9EAF7')
    warn_fill = PatternFill('solid', fgColor='FFF3CD')
    header_align = Alignment(horizontal='center', vertical='center')
    wrap_align = Alignment(vertical='center')

    # 预解析三种 StyleArray
    _hdr_tmpl = WriteOnlyCell(ws, value='x')
    _hdr_tmpl.font = Font(bold=True)
    _hdr_tmpl.fill = header_fill
    _hdr_tmpl.alignment = header_align
    _hdr_tmpl.border = border
    header_style = _hdr_tmpl._style

    _warn_tmpl = WriteOnlyCell(ws, value='x')
    _warn_tmpl.fill = warn_fill
    _warn_tmpl.alignment = wrap_align
    _warn_tmpl.border = border
    warn_style = _warn_tmpl._style

    _plain_tmpl = WriteOnlyCell(ws, value='x')
    _plain_tmpl.alignment = wrap_align
    _plain_tmpl.border = border
    plain_style = _plain_tmpl._style

    # 写标题行
    header_cells = []
    for h in title_row:
        c = WriteOnlyCell(ws, value=h)
        c._style = header_style
        header_cells.append(c)
    ws.append(header_cells)

    # 写数据行（纯值 tuple，普通行零样式开销）
    for idx, row in enumerate(rows, start=0):
        if idx in warn_indices:
            cells = []
            for v in row:
                c = WriteOnlyCell(ws, value=v)
                c._style = warn_style
                cells.append(c)
            ws.append(cells)
        else:
            ws.append(tuple(row))

    if widths:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w


def export_excel(parsed, summary, output_path):
    """生成汇总 Excel（Sheet：批次明细 / 品番汇总 / 车种品目汇总 / 团队统计 / 异常清单）。

    使用 write_only 流式写出 + 共享 _style，大文件（万行级）内存占用下降一个数量级。
    write_only 不支持 freeze_panes / auto_filter，因此已移除。
    """
    wb = Workbook(write_only=True)

    # 1. 批次明细
    ws = wb.create_sheet("批次明细")
    record_date = (parsed.get("record_date")
                   or extract_date_from_filename(parsed.get("source_file") or ""))
    headers = ["投入日期", F_BATCH, F_VEHICLE, F_ITEM, F_PART, F_LOT,
               F_QTY, F_CIRCUITS, F_TOTAL_CIRCUITS, F_REMARK, "标签"]
    records = parsed["records"] or []
    rows = [[r.get("投入日期") or r.get("记录日期") or record_date, r.get(F_BATCH),
             r.get(F_VEHICLE), r.get(F_ITEM), r.get(F_PART),
             r.get(F_LOT), r.get(F_QTY), r.get(F_CIRCUITS),
             r.get(F_TOTAL_CIRCUITS), r.get(F_REMARK), "/".join(r["标签"])]
            for r in records]
    if not rows:
        rows = [[""] * len(headers)]
    _build_write_sheet(ws, headers, rows, widths=[12, 6, 12, 12, 16, 14, 10, 8, 12, 24, 14])

    # 2. 品番汇总
    ws2 = wb.create_sheet("品番汇总")
    headers2 = [F_PART, F_VEHICLE, F_ITEM, "累计投入数量", "累计合计回路数", "涉及批次"]
    rows2 = [[r[F_PART], r[F_VEHICLE], r[F_ITEM], r[F_QTY],
              r[F_TOTAL_CIRCUITS], "/".join(r["涉及批次"])]
             for r in summary["part_summary"]]
    _build_write_sheet(ws2, headers2, rows2, widths=[16, 12, 12, 14, 14, 14])

    # 3. 车种+品目汇总
    ws3 = wb.create_sheet("车种品目汇总")
    headers3 = [F_VEHICLE, F_ITEM, "总投入数量", "总回路数", "涉及批次"]
    rows3 = [[r[F_VEHICLE], r[F_ITEM], r[F_QTY], r[F_TOTAL_CIRCUITS],
              "/".join(r["涉及批次"])] for r in summary["vehicle_item"]]
    _build_write_sheet(ws3, headers3, rows3, widths=[12, 12, 14, 14, 14])

    # 4. 团队统计
    ws4 = wb.create_sheet("团队统计")
    team_rows = summary["team_stats"]["절압팀"]["汇总"] + [{
        "批次": "G", "总回路数": summary["team_stats"]["A-BAG팀"]["总回路"],
        "占比%": None, "团队": "A-BAG팀",
    }]
    headers4 = ["团队", "批次", "总回路数", "占比%"]
    rows4 = [[r["团队"], r["批次"], r["总回路数"], r["占比%"] if r["占比%"] is not None else "-"]
             for r in team_rows]
    _build_write_sheet(ws4, headers4, rows4, widths=[16, 8, 12, 10])
    # write_only 不支持 ws.cell(row=i, column=c)，直接 append 追加
    summary4 = [["절압팀(生产1部) 总回路数", summary["team_stats"]["절압팀"]["总回路"]],
                ["A-BAG팀(G次) 总回路数", summary["team_stats"]["A-BAG팀"]["总回路"]]]
    for row in summary4:
        ws4.append(row)

    # 5. 异常清单（ERROR 行高亮）
    ws5 = wb.create_sheet("异常清单")
    headers5 = ["Sheet", "行号", "级别", "说明", "原始值"]
    rows5 = [[e["sheet"], e["row"], e["level"], e["message"], e["value"]]
             for e in parsed["errors"]]
    warn_idx = {i for i, r in enumerate(rows5) if r[2] == "ERROR"}
    if not rows5:
        rows5 = [[""] * len(headers5)]
    _build_write_sheet(ws5, headers5, rows5, widths=[20, 8, 8, 40, 20],
                       warn_indices=warn_idx)

    wb.save(output_path)
    return output_path


def build_batch_detail_xlsx(date_from="", date_to="", vehicle="", batch="",
                            keyword="", dest=None):
    """生成「批次明细」单表 Excel 到 dest（默认临时文件），返回 (路径, 文件名)。

    用 openpyxl write_only 模式：逐行 append、不保留单元格对象，
    内存占用恒定，超长时间筛选（百万行级）也能稳定导出。
    """
    from models.invest_record import InvestmentRecord
    from datetime import datetime as _dt

    headers = ["投入日期", F_BATCH, F_VEHICLE, F_ITEM, F_PART, F_LOT,
               F_QTY, F_CIRCUITS, F_TOTAL_CIRCUITS, F_REMARK]

    if dest is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx',
                                          prefix='invest_batch_')
        dest = tmp.name
        tmp.close()

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("批次明细")
    ws.append(headers)
    # 按 投入日期 + 主键 依次排序；yield_per 分批游标，避免一次性加载
    q = _apply_filters(InvestmentRecord.query, date_from, date_to,
                       vehicle, batch, keyword)
    q = q.order_by(InvestmentRecord.record_date, InvestmentRecord.id)
    for r in q.yield_per(500):
        ws.append([r.record_date, r.batch, r.vehicle or "", r.item or "",
                   r.part_no or "", r.lot or "", r.qty or 0, r.circuits or 0,
                   r.total_circuits or 0, r.remark or ""])
    wb.save(dest)
    name = f'投入计划书_批次明细_{_dt.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
    return dest, name


def _apply_filters(q, date_from="", date_to="", vehicle="", batch="",
                   keyword=""):
    """为查询附加 日期/车种/批次/关键词 过滤条件（共用，避免重复代码）。"""
    from models.invest_record import InvestmentRecord as R
    if date_from:
        q = q.filter(R.record_date >= date_from)
    if date_to:
        q = q.filter(R.record_date <= date_to)
    if vehicle:
        q = q.filter(R.vehicle.like(f"%{vehicle}%"))
    if batch:
        q = q.filter(R.batch == batch)
    if keyword:
        k = f"%{keyword}%"
        q = q.filter(db.or_(R.part_no.like(k), R.lot.like(k),
                            R.remark.like(k), R.vehicle.like(k)))
    return q


def count_records(date_from="", date_to="", vehicle="", batch="", keyword=""):
    """DB 层 COUNT 命中条数，不加载明细（页面筛选用，零内存压力）。"""
    from models.invest_record import InvestmentRecord
    q = _apply_filters(InvestmentRecord.query, date_from, date_to,
                       vehicle, batch, keyword)
    return q.count()


def batch_detail_export_url(date_from="", date_to="", vehicle="", batch="",
                            keyword=""):
    """生成「批次明细」单表导出链接（带筛选参数）。"""
    from urllib.parse import urlencode
    params = {'filtered': 1}
    for k, v in (('date_from', date_from), ('date_to', date_to),
                 ('vehicle', vehicle), ('batch', batch), ('keyword', keyword)):
        if v:
            params[k] = v
    return '/invest_tidy/download?' + urlencode(params)


def build_result_for_display(parsed, summary):
    """将解析+汇总结果组织为模板渲染所需的展示结构。"""
    record_date = extract_date_from_filename(parsed.get("source_file") or "")
    for rec in summary["batch_detail"]:
        rec.setdefault("投入日期", record_date)
    return {
        "source_file": parsed["source_file"],
        "record_date": record_date,
        "detail_sheets": parsed["detail_sheets"],
        "records": summary["batch_detail"],
        "part_summary": summary["part_summary"],
        "vehicle_item": summary["vehicle_item"],
        "team_stats": summary["team_stats"],
        "totals": summary["totals"],
        "tag_counts": summary["tag_counts"],
        "errors": parsed["errors"],
        "skipped_sheets": parsed["skipped_sheets"],
    }


# ---------------------------------------------------------------------------
# LOT 明细总表导出（跨文件 / 跨日期合并成一张带样式、冻结、筛选的明细表）
# ---------------------------------------------------------------------------
def export_lot_detail_master(records, output_path):
    """生成「LOT明细总表」单 Sheet Excel。

    列结构（与你提供的编程逻辑一致）：
        日期 / 批次Sheet / 车种 / 品目 / P/NO / LOT/NO / 数量 / 回路数 / 备注
    特性：
        - 表头深蓝底白字 + 边框 + 居中
        - 数量/回路数转 int（Excel 浮点 50.0 → 50 显示更干净）
        - 冻结首行 + 开启自动筛选
        - 中间明细列（车种/品目/备注）左对齐换行，其余居中

    records 来自 query_records()：每条含 记录日期/批次/车种/品目/品番/LOT·NO/
    投入数量/回路数/合计回路数/备注/来源文件 等字段。
    """
    # 列别名（与 query_records 返回字段对应）
    C_DATE = "记录日期"
    C_SHEET = "来源文件"
    C_VEHICLE = F_VEHICLE
    C_ITEM = F_ITEM
    C_PART = F_PART
    C_LOT = F_LOT
    C_QTY = F_QTY
    C_CIRCUITS = F_CIRCUITS
    C_REMARK = F_REMARK

    HDR_FILL = PatternFill("solid", fgColor="1F4E79")
    HDR_FONT = Font(bold=True, color="FFFFFF", size=11)
    THIN = Side(style="thin", color="BFBFBF")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    CEN = Alignment(horizontal="center", vertical="center")
    WRAP = Alignment(horizontal="left", vertical="center", wrap_text=True)

    cols = ["日期", "批次Sheet", "车种", "品目", "P/NO", "LOT/NO",
            "数量", "回路数", "备注"]
    # 各列对齐：中间明细列（车种/品目/备注）左对齐，其余居中
    center_cols = {1, 2, 5, 6, 7, 8}  # 1基列号（日期/批次Sheet/P·NO/LOT·NO/数量/回路数）

    # 性能优化（C-10）：改用 write_only 流式工作簿，逐行 append，避免整表常驻内存。
    # 样式通过 WriteOnlyCell 在写入时绑定（write_only 模式不支持事后 ws.cell() 访问）。
    wb_out = Workbook(write_only=True)
    ws = wb_out.create_sheet("LOT明细总表")

    # 表头行（深蓝底白字 + 边框 + 居中）
    header_row = []
    for c_idx in range(1, len(cols) + 1):
        cell = WriteOnlyCell(ws, value=cols[c_idx - 1])
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = CEN
        cell.border = BORDER
        header_row.append(cell)
    ws.append(header_row)

    # 明细行（按记录日期、LOT 排序，便于跨文件时间顺序查看）
    def _sort_key(r):
        return (str(r.get(C_DATE) or ""), str(r.get(C_LOT) or ""))
    ordered = sorted(records, key=_sort_key)

    for r in ordered:
        date_val = r.get(C_DATE) or ""
        sheet_val = r.get(C_SHEET) or ""
        lot = r.get(C_LOT) or ""
        qty = r.get(C_QTY) or 0
        cir = r.get(C_CIRCUITS) or 0
        row_vals = [
            date_val,
            sheet_val,
            r.get(C_VEHICLE) or "",
            r.get(C_ITEM) or "",
            r.get(C_PART) or "",
            lot,
            int(qty) if qty else 0,
            int(cir) if cir else 0,
            r.get(C_REMARK) or "",
        ]
        # 逐格绑定边框/对齐（write_only 下必须写入时设定）
        line = []
        for c_idx, val in enumerate(row_vals, start=1):
            cell = WriteOnlyCell(ws, value=val)
            cell.border = BORDER
            cell.alignment = CEN if c_idx in center_cols else WRAP
            line.append(cell)
        ws.append(line)

    # 列宽
    widths = [12, 22, 12, 28, 18, 14, 8, 8, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    # 冻结首行 + 自动筛选（write_only 支持 freeze_panes / auto_filter）
    ws.freeze_panes = "A2"
    last_col_letter = get_column_letter(len(cols))
    ws.auto_filter.ref = "A1:%s%d" % (last_col_letter, len(ordered) + 1)

    wb_out.save(output_path)
    return output_path