# -*- coding: utf-8 -*-
"""波纹管位置 - 服务层

- Excel 上传解析（固定模板列 + 弹性列容错：列名自动收拢为标准字段）
- 官方 Excel 模板导出（openpyxl 生成）
- 全量预览 / 手动编辑（改行、删行、清空）
- 查询页模糊查询（LIKE 关键词跨全部列匹配）+ 分页
- 查询页列配置 CRUD（后台：启用列、显示名、顺序）
"""
import io
import json
import math
import os
import re
from datetime import datetime, date

from db import db
from models.corrugated_location import CorrugatedLocation, CorrugatedColumnConfig

# 标准模板列（与 template/guanwuweizhi.xlsx 表头一致；上传可接受列顺序任意）
STANDARD_COLUMNS = ["品番", "规格", "位置", "车种", "备注"]

# 官方 Excel 模板文件（页面「下载录入模板」直接下发该文件）
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "template", "guanwuweizhi.xlsx")

# 日期提取：优先「日期」列，其次从可识别日期列
_DATE_RE = re.compile(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})")


def _cell_to_text(value):
    """单元格值归一化为文本（NaN 空值转空白，日期保留 YYYY-MM-DD 格式）。"""
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):  # Excel 空单元格 -> 空白
        return ""
    if isinstance(value, datetime) or isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _extract_date(columns, values):
    """优先从名为「日期/updateDate/date」的列提取日期；否则尝试任意列找日期格式。"""
    idx = None
    for i, c in enumerate(columns):
        if c in ("日期", "日期(输入)", "输入日期", "입력날짜", "date"):
            idx = i
            break
    if idx is not None:
        m = _DATE_RE.search(values[idx] if idx < len(values) else "")
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    for v in values:
        m = _DATE_RE.search(v or "")
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return date.today().strftime("%Y-%m-%d")


def parse_excel(file_bytes, filename=""):
    """解析上传的 Excel（.xlsx/.xls），返回 {records, columns, row_count, errors}。

    records: [{keys, values, date, key, row_no}]
    容错：跳过空行；列顺序任意；多余列保留。
    """
    import pandas as pd
    try:
        df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, dtype=object)
    except Exception:
        df = pd.read_excel(io.BytesIO(file_bytes), dtype=object)

    records = []
    errors = []
    columns = []
    row_no = 1
    total_rows = 0
    for sheet_name, sheet in (df.items() if isinstance(df, dict) else [("Sheet1", df)]):
        if sheet is None or sheet.empty:
            continue
        cols = [_cell_to_text(c) for c in sheet.columns]
        if not columns:
            # 兼容模板下发放的标准表头与可能出现的加序号前缀（如「序号」「No」）
            filter_cols = [c for c in cols if c and c not in ("序号", "No", "NO", "no")]
            # 若加过序号列，则以剩余列作为业务列
            use_cols = filter_cols if len(filter_cols) >= 3 else cols
            columns = use_cols
        # to_dict('records') 替代 iterrows：逐行 dict 访问语义一致但省去 Series 构造开销
        for row in sheet.to_dict("records"):
            row_no += 1
            values = [_cell_to_text(row[c]) if c in row else "" for c in columns]
            if not any(values):  # 空行跳过
                continue
            total_rows += 1
            key = values[0] if values else ""
            records.append({
                "keys": columns, "values": values,
                "date": _extract_date(columns, values),
                "key": key, "row_no": row_no,
            })
    return {"records": records, "columns": columns,
            "row_count": total_rows, "errors": errors}


def build_template_workbook():
    """下发表单模板：优先直接读取 template/guanwuweizhi.xlsx，缺失时按标准列动态生成。"""
    if os.path.exists(TEMPLATE_FILE):
        with open(TEMPLATE_FILE, "rb") as f:
            bio = io.BytesIO(f.read())
        bio.seek(0)
        return bio

    from openpyxl import Workbook
    from openpyxl.styles import PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    header_fill = PatternFill("solid", fgColor="FFFF00")
    for i, name in enumerate(STANDARD_COLUMNS, start=1):
        cell = ws.cell(row=1, column=i, value=name)
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[get_column_letter(i)].width = 16
    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


def save_all(records, source_file=""):
    """全量覆盖保存（清除旧数据后写入），返回插入条数。"""
    CorrugatedLocation.query.delete()
    db.session.flush()
    now = datetime.now()
    for rec in records:
        row = CorrugatedLocation(
            record_date=rec["date"],
            record_key=rec.get("key") or "",
            keys_json=json.dumps(rec["keys"], ensure_ascii=False),
            values_json=json.dumps(rec["values"], ensure_ascii=False),
            source_file=source_file,
            row_no=rec.get("row_no"),
            uploaded_at=now,
        )
        db.session.add(row)
    db.session.commit()
    return len(records)


def save_from_array(columns, rows, source_file=""):
    """由前端预览的二维数组全量覆盖保存，并同步列配置与日期。

    columns: 列名列表（预览表头）
    rows:    每行值列表（与列对齐）
    """
    records = []
    row_no = 2  # 模板第1行为表头，数据从第2行起
    for values in rows:
        values = [_cell_to_text(v) for v in values]
        if not any(values):
            continue
        records.append({
            "keys": columns, "values": values,
            "date": _extract_date(columns, values),
            "key": values[0] if values else "",
            "row_no": row_no,
        })
        row_no += 1
    inserted = save_all(records, source_file)
    sync_columns_from_records(records)
    return inserted


def list_all():
    """返回全部记录（预览用），附带动态列。"""
    rows = CorrugatedLocation.query.order_by(CorrugatedLocation.record_date,
                                             CorrugatedLocation.row_no).all()
    columns = _union_columns(rows)
    items = [_row_dict(r, columns) for r in rows]
    return {"items": items, "columns": columns, "total": len(items)}


def query(keyword="", page=1, per_page=20):
    """模糊查询：关键词跨全部列做 LIKE 匹配，支持分页排序。"""
    q = CorrugatedLocation.query
    if keyword.strip():
        like = f"%{keyword.strip()}%"
        q = q.filter(db.or_(
            CorrugatedLocation.keys_json.like(like),
            CorrugatedLocation.values_json.like(like),
        ))
    total = q.count()
    rows = q.order_by(CorrugatedLocation.record_date.desc(),
                      CorrugatedLocation.id.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)
    items = [_row_dict(r, None) for r in rows.items]
    return {"items": items, "columns": None, "total": total,
            "page": rows.page, "pages": rows.pages, "per_page": per_page}


def query_cached(keyword="", page=1, per_page=20):
    """带 5 分钟内存缓存的模糊查询（查询页免登录高频访问时降低 DB 开销）。"""
    from query_cache import query_cache_manager
    key = f"corlg:q:{keyword.strip()}:{page}:{per_page}"
    cached = query_cache_manager.get_list_cache(key)
    if cached is not None:
        return cached
    result = query(keyword, page, per_page)
    query_cache_manager.set_list_cache(key, result)
    return result


def invalidate_query_cache():
    """数据变更后调用，清空查询页缓存。"""
    from query_cache import query_cache_manager
    query_cache_manager.invalidate_list_cache()


def _union_columns(rows):
    seen, cols = set(), []
    for r in rows:
        for c in json.loads(r.keys_json):
            if c and c not in seen:
                seen.add(c)
                cols.append(c)
    return cols


def _clean_value(v):
    """清洗单元格值：'nan'/'None' 等占位文本转空白。"""
    if isinstance(v, str) and v.strip().lower() in ("nan", "none", "null"):
        return ""
    return v


def _row_dict(r, columns=None):
    cols = columns or json.loads(r.keys_json)
    vals = json.loads(r.values_json)
    data = {}
    for i, c in enumerate(cols):
        data[c] = _clean_value(vals[i]) if i < len(vals) else ""
    return {"id": r.id, "record_date": r.record_date, "data": data}


def update_row(row_id, data):
    """单行编辑：更新 values_json（以行 keys 对齐）。"""
    # 防御：空数据会按缺失字段写空字符串，导致整行被清空，直接拒绝
    if not data:
        return False, "没有可更新的字段"
    r = db.session.get(CorrugatedLocation, row_id)
    if not r:
        return False, "记录不存在"
    keys = json.loads(r.keys_json)
    new_vals = [data.get(k, "") if data.get(k) is not None else "" for k in keys]
    r.values_json = json.dumps(new_vals, ensure_ascii=False)
    # 同步更新日期与主键值（若列存在）
    if "日期" in keys:
        idx = keys.index("日期")
        m = _DATE_RE.search(str(new_vals[idx]) if idx < len(new_vals) else "")
        if m:
            r.record_date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    if keys:
        r.record_key = new_vals[0]
    r.updated_at = datetime.now()
    db.session.commit()
    return True, "已保存"


def delete_row(row_id):
    r = db.session.get(CorrugatedLocation, row_id)
    if not r:
        return False, "记录不存在"
    db.session.delete(r)
    db.session.commit()
    return True, "已删除"


def clear_all():
    n = CorrugatedLocation.query.delete()
    db.session.commit()
    return n


# ---------------------------------------------------------------------------
# 列配置（查询页后台可配置）
# ---------------------------------------------------------------------------
def get_column_configs():
    """返回全部列配置（含未收录的字段自动补缺）。"""
    rows = CorrugatedColumnConfig.query.order_by(
        CorrugatedColumnConfig.sort_order, CorrugatedColumnConfig.id).all()
    cfg = [{"field_key": c.field_key, "display_name": c.display_name,
            "is_visible": bool(c.is_visible), "sort_order": c.sort_order,
            "id": c.id} for c in rows]
    return cfg


def save_column_configs(configs):
    """保存列配置（configs: [{field_key, display_name, is_visible, sort_order}]）。"""
    configs = configs or []
    seen = set()
    for item in configs:
        key = (item.get("field_key") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        row = CorrugatedColumnConfig.query.filter_by(field_key=key).first()
        if row:
            row.display_name = item.get("display_name") or key
            row.is_visible = bool(item.get("is_visible", True))
            row.sort_order = item.get("sort_order", 0)
        else:
            db.session.add(CorrugatedColumnConfig(
                field_key=key,
                display_name=item.get("display_name") or key,
                is_visible=bool(item.get("is_visible", True)),
                sort_order=item.get("sort_order", 0),
            ))
    # 删除已移除的配置（前端回传完整列表）
    existing = {c.field_key for c in CorrugatedColumnConfig.query.all()}
    for key in existing - seen:
        CorrugatedColumnConfig.query.filter_by(field_key=key).delete()
    db.session.commit()
    return list(get_column_configs())


def visible_columns():
    """查询页展示列（后台启用的列，按 sort_order 排序）。"""
    configs = CorrugatedColumnConfig.query.filter_by(is_visible=True) \
        .order_by(CorrugatedColumnConfig.sort_order, CorrugatedColumnConfig.id).all()
    return [{"field_key": c.field_key, "display_name": c.display_name or c.field_key}
            for c in configs]


def sync_columns_from_records(records):
    """上传新数据后，自动把新增列纳入列配置（保留已有配置的可见性/顺序）。"""
    existing = {c.field_key: c for c in CorrugatedColumnConfig.query.all()}
    next_order = (db.session.query(db.func.max(CorrugatedColumnConfig.sort_order)).scalar() or 0) + 1
    added = []
    if records and records[0].get("keys"):
        for key in records[0]["keys"]:
            if key and key not in existing:
                db.session.add(CorrugatedColumnConfig(
                    field_key=key, display_name=key, is_visible=True,
                    sort_order=next_order))
                next_order += 1
                added.append(key)
    if added:
        db.session.commit()
        # 上传数据全部入库后，把新增列补进 data 表对应行不需要（动态平铺）


def ensure_default_configs():
    """列配置与标准模板列保持一致：缺列自动补、旧列自动删、顺序按模板刷新。

    保留已有列的显示名与可见性配置，仅同步字段集合与顺序。
    """
    existing = {c.field_key: c for c in CorrugatedColumnConfig.query.all()}
    changed = False
    for i, name in enumerate(STANDARD_COLUMNS, start=1):
        row = existing.get(name)
        if row:
            if not row.display_name:
                row.display_name = name
                changed = True
            if row.sort_order != i:
                row.sort_order = i
                changed = True
        else:
            db.session.add(CorrugatedColumnConfig(
                field_key=name, display_name=name, is_visible=True, sort_order=i))
            changed = True
    # 模板已废弃的列（如旧版「位置编号 / LOT」等）从配置中移除
    for key, row in existing.items():
        if key not in STANDARD_COLUMNS:
            db.session.delete(row)
            changed = True
    if changed:
        db.session.commit()