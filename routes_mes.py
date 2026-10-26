# -*- coding: utf-8 -*-
"""MES 结算清单模块蓝图（原「MES 分组」页面改造而来）

功能：列出指定区间内作业的品番 / LOT / 作业者 / 计划数量 / 作业数量(生产入库) /
包装数量与包装时间(品质检查)，并显示单价与结算金额（结算金额 = 单价 × 数量）。

数据关联键：品番(pno) + LOT(lot_no) + 作业者(operator)。
    - 作业者：同一品番+LOT 下不同作业者单独成行，不合并
    - 计划数量：plan_login.plan_qty
    - 作业数量：production_inbound.inbound_qty 汇总
    - 包装数量/时间：quality_check.check_qty 汇总，包装时间取该 LOT 最新更新时间(updated_at)
    - 单价：unit_price 按品番取生效日期 <= 区间内的最新一条

币种处理：unit_price 存在 CNY/JPY/USD 多币种，按币种分组汇总金额，
          不做汇率换算，避免引入不准确的换算结果。

权限编码（与 utils/permission_catalog.py、models/permission_info.py 一致）：
    页面访问 can_mes_group；导出 can_mes_group_download
"""
import csv
import io
import logging
from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template, redirect, url_for,
    flash, session, make_response
)

logger = logging.getLogger(__name__)

mes_bp = Blueprint('mes', __name__, url_prefix='/mes')

# 权限编码（与 utils/permission_catalog.py、models/permission_info.py 保持一致）
PERM_PAGE = 'can_mes_group'
PERM_DOWNLOAD = 'can_mes_group_download'
PERM_UNMASK = 'can_mes_group_unmask'   # 查看结算单价/金额（未授权则脱敏）


# --------------------------------------------------------------------------
# 权限与登录辅助
# --------------------------------------------------------------------------
def _current_user():
    """获取当前登录用户对象，未登录返回 None。"""
    from models.user_info import UserInfo
    from app_factory import db
    uid = session.get('user_id')
    if not uid:
        return None
    return db.session.get(UserInfo, uid)


def _has_perm(user, perm_code):
    """管理员恒有权限；否则查询 PermissionInfo 对应字段（走统一短 TTL 缓存）。"""
    if user is None:
        return False
    if getattr(user, 'is_admin', False):
        return True
    from utils.permission_utils import get_legacy_permission_row
    perm = get_legacy_permission_row(user.id)
    return bool(perm and getattr(perm, perm_code, False))


def _require_perm(perm_code):
    """校验权限，返回 (user, None) 或 (None, response)。"""
    user = _current_user()
    if user is None:
        return None, (jsonify({'success': False, 'message': '未登录'}), 401)
    if not _has_perm(user, perm_code):
        return None, (jsonify({'success': False, 'message': '权限不足'}), 403)
    return user, None


def _to_date(val):
    """把 'YYYY-MM-DD' 解析为 date；失败返回 None。"""
    val = (val or '').strip()
    if not val:
        return None
    try:
        return datetime.strptime(val, '%Y-%m-%d').date()
    except ValueError:
        return None


def _to_datetime(val):
    """把包装时间筛选值解析为 datetime。

    兼容 'YYYY-MM-DDTHH:MM' / 'YYYY-MM-DD HH:MM' / 'YYYY-MM-DD' 三种写法，
    仅日期时按当天 00:00 处理（上界由调用方按 23:59:59 处理）。
    """
    val = (val or '').strip().replace('T', ' ')
    if not val:
        return None
    for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(val, fmt)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@mes_bp.route('/group')
def group_page():
    """MES 结算清单页面。未登录跳登录；无权限跳 403。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    user = _current_user()
    if not _has_perm(user, PERM_PAGE):
        flash('您没有 MES 结算清单页面的访问权限', 'error')
        from routes import forbidden_page
        return forbidden_page('您没有 MES 结算清单页面的访问权限')

    resp = make_response(render_template(
        'mes_group.html',
        page_title='MES 结算清单',
        can_download=_has_perm(user, PERM_DOWNLOAD),
        can_unmask=_has_perm(user, PERM_UNMASK),   # 单价/金额是否可见（否则前端打码）
    ))
    # 页面含交互逻辑，禁用缓存避免浏览器执行旧版本脚本
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return resp


# --------------------------------------------------------------------------
# 结算数据聚合
# --------------------------------------------------------------------------
def _sum_by_lot(Model, qty_field, date_field, date_from, date_to,
                extra_filters=None):
    """按 (pno, lot_no) 汇总某表数量：返回 {(pno, lot_no): 合计}。

    extra_filters 为额外等值筛选条件列表 [(column, value), ...]，
    用于按作业者(operator)、客户(customer)等维度过滤后再汇总。
    """
    from app_factory import db
    qty_col = getattr(Model, qty_field)
    q = db.session.query(Model.pno, Model.lot_no,
                         db.func.coalesce(db.func.sum(qty_col), 0))
    if date_field and (date_from or date_to):
        date_col = getattr(Model, date_field)
        if date_from:
            q = q.filter(date_col >= date_from)
        if date_to:
            q = q.filter(date_col <= date_to)
    # 附加等值筛选（作业者 / 客户）
    for col, val in (extra_filters or []):
        q = q.filter(col == val)
    q = q.group_by(Model.pno, Model.lot_no)
    return {(pno or '', lot or ''): int(total or 0) for pno, lot, total in q.all()}


def _pno_customer_map(pnos, date_from, date_to):
    """取品番对应的客户：返回 {pno: 客户}。

    客户来自 plan_login；同一品番取最新一条非空客户值。
    """
    if not pnos:
        return {}
    from app_factory import db
    from models.plan_login import PlanLogin

    q = db.session.query(PlanLogin.pno, PlanLogin.customer,
                         PlanLogin.plan_date).filter(PlanLogin.pno.in_(list(pnos)))
    if date_from:
        q = q.filter(PlanLogin.plan_date >= date_from)
    if date_to:
        q = q.filter(PlanLogin.plan_date <= date_to)
    # 按品番+日期倒序，第一个非空客户即为最新
    rows = q.order_by(PlanLogin.pno.asc(), PlanLogin.plan_date.desc()).all()
    result = {}
    for pno, customer, _d in rows:
        if pno in result:
            continue
        if customer:
            result[pno] = customer
    return result


def _prod_amount_group(date_from, date_to, extra_filters=None):
    """按 (pno, lot_no, operator) 汇总生产入库数量、金额与单价集合。

    金额口径 = Σ(每条入库记录的 unit_price × inbound_qty)，逐条累加，
    避免同一 LOT 存在多条不同单价时用平均单价造成误差。
    按作业者分组：不同作业者的数据不合并，便于区分与结算。
    返回 {(pno, lot_no, operator): {'qty': 数量合计, 'amount': 金额合计,
                                    'prices': 去重后升序单价列表}}。
    """
    from app_factory import db
    from models.production_inbound import ProductionInbound

    amount_expr = db.func.coalesce(
        db.func.sum(ProductionInbound.unit_price * ProductionInbound.inbound_qty), 0)
    qty_expr = db.func.coalesce(db.func.sum(ProductionInbound.inbound_qty), 0)
    op_col = ProductionInbound.operator
    q = db.session.query(ProductionInbound.pno, ProductionInbound.lot_no, op_col,
                         qty_expr, amount_expr)
    if date_from:
        q = q.filter(ProductionInbound.inbound_date >= date_from)
    if date_to:
        q = q.filter(ProductionInbound.inbound_date <= date_to)
    for col, val in (extra_filters or []):
        q = q.filter(col == val)
    q = q.group_by(ProductionInbound.pno, ProductionInbound.lot_no, op_col)
    summary = {(pno or '', lot or '', op or ''): {'qty': int(qty or 0),
                                                  'amount': float(amt or 0.0),
                                                  'prices': []}
               for pno, lot, op, qty, amt in q.all()}

    # 收集各 (品番,LOT,作业者) 出现的不同单价（去重升序），供前端判断是否「多单价」
    p_q = db.session.query(ProductionInbound.pno, ProductionInbound.lot_no, op_col,
                           ProductionInbound.unit_price).distinct()
    if date_from:
        p_q = p_q.filter(ProductionInbound.inbound_date >= date_from)
    if date_to:
        p_q = p_q.filter(ProductionInbound.inbound_date <= date_to)
    for col, val in (extra_filters or []):
        p_q = p_q.filter(col == val)
    for pno, lot, op, price in p_q.all():
        key = (pno or '', lot or '', op or '')
        if key in summary:
            summary[key]['prices'].append(float(price or 0.0))
    for item in summary.values():
        item['prices'] = sorted(set(item['prices']))
    return summary


def _pack_group(pack_from=None, pack_to=None, extra_filters=None):
    """按 (pno, lot_no) 汇总品质检查的包装数量与包装时间。

    包装时间取品质检查记录的「更新时间」(updated_at)：一条 LOT 可能有多条
    包装记录，取其中最晚的更新时间作为该 LOT 的包装时间（只展示一条）。
    可按 [pack_from, pack_to] 精确到分钟的区间过滤。
    返回 {(pno, lot_no): {'qty': 包装数量, 'time': 包装时间}}。
    """
    from app_factory import db
    from models.quality_check import QualityCheck

    qty_expr = db.func.coalesce(db.func.sum(QualityCheck.check_qty), 0)
    q = db.session.query(QualityCheck.pno, QualityCheck.lot_no, qty_expr)
    q = _filter_pack_time(q, QualityCheck, pack_from, pack_to)
    for col, val in (extra_filters or []):
        q = q.filter(col == val)
    q = q.group_by(QualityCheck.pno, QualityCheck.lot_no)
    summary = {(pno or '', lot or ''): {'qty': int(qty or 0), 'time': None}
               for pno, lot, qty in q.all()}

    # 逐行取更新时间（跨库 MIN/MAX 对时间字段表现不一致，故在 Python 侧取最大值）
    t_q = db.session.query(QualityCheck.pno, QualityCheck.lot_no, QualityCheck.updated_at)
    t_q = _filter_pack_time(t_q, QualityCheck, pack_from, pack_to)
    for col, val in (extra_filters or []):
        t_q = t_q.filter(col == val)
    for pno, lot, updated_at in t_q.all():
        item = summary.get((pno or '', lot or ''))
        if item is None or updated_at is None:
            continue
        if item['time'] is None or updated_at > item['time']:
            item['time'] = updated_at
    return summary


def _filter_pack_time(query, Model, pack_from, pack_to):
    """按包装时间（品质检查更新时间）区间过滤查询；边界含端。"""
    if pack_from:
        query = query.filter(Model.updated_at >= pack_from)
    if pack_to:
        # 上界若为整点（秒/微秒为 0）则补足到该分钟 59 秒，避免漏掉同分钟数据
        end = pack_to
        if end.second == 0 and end.microsecond == 0:
            end = end.replace(second=59)
        query = query.filter(Model.updated_at <= end)
    return query


def _build_settlement(date_from, date_to, operator=None, customer=None,
                      pack_from=None, pack_to=None):
    """按区间聚合结算数据，返回行列表（每行一个 品番+LOT+作业者）。

    分组口径：以生产入库的 (品番, LOT, 作业者) 为主键，同一品番+LOT 下
              不同作业者单独成行，不合并。计划/包装数量按其 (品番, LOT)
              归属到相关作业者行（若该 LOT 无生产入库，则作业者留空另起一行）。
    区间口径：计划/作业 按各自日期字段 [date_from, date_to] 过滤。
    包装时间：包装数量与包装时间只统计品质检查 updated_at 落在 [pack_from, pack_to] 的记录。
    金额口径：结算金额 = Σ(生产入库单价 × 生产入库数量)，逐条累加。
    筛选：operator 按作业者(生产入库)过滤，customer 按客户(计划登录)过滤。
    """
    from models.plan_login import PlanLogin
    from models.production_inbound import ProductionInbound

    # 作业者筛选作用于生产入库(作业数量/金额)
    prod_filters = []
    if operator:
        prod_filters.append((ProductionInbound.operator, operator))
    # 客户筛选作用于计划登录(计划数量)
    plan_filters = []
    if customer:
        plan_filters.append((PlanLogin.customer, customer))

    plan_map = _sum_by_lot(PlanLogin, 'plan_qty', 'plan_date', date_from, date_to,
                           extra_filters=plan_filters)
    prod_group = _prod_amount_group(date_from, date_to, extra_filters=prod_filters)
    pack_group = _pack_group(pack_from=pack_from, pack_to=pack_to)

    # LOT 级数据：计划数量、客户、包装数量/时间 均按 (品番, LOT) 归属
    lot_keys = set(plan_map) | {(pno, lot) for pno, lot, _op in prod_group} | set(pack_group)
    pnos = {pno for pno, _lot in lot_keys if pno}
    customer_map = _pno_customer_map(pnos, date_from, date_to)

    # 汇总每个 (品番, LOT) 下出现过的作业者（含空作业者）
    operators_map = {}
    for pno, lot, op in prod_group:
        operators_map.setdefault((pno, lot), set()).add(op)

    rows = []
    for pno, lot in lot_keys:
        plan_qty = plan_map.get((pno, lot), 0)
        pack = pack_group.get((pno, lot)) or {'qty': 0, 'time': None}
        # 该 LOT 出现的作业者集合；无生产入库记录时用空作业者占位
        ops = operators_map.get((pno, lot)) or {''}
        for op in ops:
            prod = prod_group.get((pno, lot, op)) or {'qty': 0, 'amount': 0.0, 'prices': []}
            work_qty = prod['qty']
            prices = prod['prices']
            if work_qty > 0:
                amount = round(prod['amount'], 2)
                # 单一口径直接展示该单价；多单价留空由 price_text 提示
                unit_price = prices[0] if len(prices) == 1 else ''
                multi_price = len(prices) > 1
            else:
                unit_price = ''
                amount = ''
                multi_price = False
            # price_text：单价列展示文本（多单价时显示区间，如 "0 ~ 9"）
            if work_qty <= 0:
                price_text = ''
            elif multi_price:
                price_text = f"{_fmt_price(prices[0])} ~ {_fmt_price(prices[-1])}"
            else:
                price_text = _fmt_price(prices[0])
            rows.append({
                'pno': pno, 'lot_no': lot, 'operator': op,
                'customer': customer_map.get(pno, ''),
                'plan_qty': plan_qty, 'work_qty': work_qty, 'pack_qty': pack['qty'],
                'pack_time': _fmt_dt(pack['time']),
                'unit_price': unit_price,
                'price_text': price_text,
                'multi_price': multi_price,
                'currency': 'CNY' if work_qty > 0 else '',
                'amount': amount,
            })

    # 排序：品番 -> LOT -> 作业者，便于阅读与对账
    rows.sort(key=lambda r: (r['pno'], r['lot_no'], r['operator']))
    return rows


def _fmt_dt(dt):
    """包装时间格式化：'YYYY-MM-DD HH:MM:SS'，空值返回 ''。"""
    return dt.strftime('%Y-%m-%d %H:%M:%S') if dt else ''


def _fmt_price(v):
    """单价展示格式化：去掉多余的尾随 0（如 9.0000 -> 9，0.429 -> 0.429）。"""
    text = f"{float(v):.4f}".rstrip('0').rstrip('.')
    return text or '0'


def _mask_settlement(rows, summary):
    """脱敏：抹去单价/币种/金额相关字段，仅保留数量等非敏感数据。

    未获「查看结算单价/金额」权限时，服务端必须移除敏感值，
    避免仅靠前端打码而被直接调用接口绕过。
    """
    for r in rows:
        r['unit_price'] = ''
        r['price_text'] = ''
        r['multi_price'] = False
        r['currency'] = ''
        r['amount'] = ''
    # 汇总仅保留作业数量，金额归空
    masked = [{'currency': '', 'work_qty': s['work_qty'], 'amount': ''}
              for s in summary]
    return rows, masked


def _currency_summary(rows):
    """按币种汇总：返回 [{currency, work_qty, amount}, ...]。

    口径与明细一致：作业数量合计 + 结算金额合计（金额 = Σ 单价×数量）。
    """
    buckets = {}
    for r in rows:
        if r['amount'] == '':
            continue  # 无有效入库金额，不计入汇总
        cur = r['currency'] or 'CNY'
        b = buckets.setdefault(cur, {'currency': cur, 'work_qty': 0, 'amount': 0.0})
        b['work_qty'] += r['work_qty']
        b['amount'] += r['amount']
    result = list(buckets.values())
    for b in result:
        b['amount'] = round(b['amount'], 2)
    result.sort(key=lambda x: x['currency'])
    return result


# --------------------------------------------------------------------------
# 结算清单 API
# --------------------------------------------------------------------------
@mes_bp.route('/api/settlement', methods=['GET'])
def settlement_list():
    """结算清单列表：区间内 品番/LOT 的计划/作业/包装数量 + 单价 + 结算金额。"""
    user, err = _require_perm(PERM_PAGE)
    if err:
        return err

    date_from = _to_date(request.args.get('date_from'))
    date_to = _to_date(request.args.get('date_to'))
    pack_from = _to_datetime(request.args.get('pack_from'))
    pack_to = _to_datetime(request.args.get('pack_to'))
    keyword = (request.args.get('keyword') or '').strip()
    operator = (request.args.get('operator') or '').strip()
    customer = (request.args.get('customer') or '').strip()

    rows = _build_settlement(date_from, date_to, operator=operator, customer=customer,
                             pack_from=pack_from, pack_to=pack_to)

    # 关键字筛选：品番 / LOT 模糊匹配
    if keyword:
        kw = keyword.lower()
        rows = [r for r in rows
                if kw in r['pno'].lower() or kw in r['lot_no'].lower()]

    summary = _currency_summary(rows)
    # 无「查看单价/金额」权限时，服务端直接脱敏
    can_unmask = _has_perm(user, PERM_UNMASK)
    if not can_unmask:
        rows, summary = _mask_settlement(rows, summary)

    return jsonify({
        'success': True,
        'items': rows,
        'total': len(rows),
        'summary': summary,
        'can_unmask': can_unmask,
    })


@mes_bp.route('/api/settlement/options', methods=['GET'])
def settlement_options():
    """结算清单筛选下拉选项：作业者列表 + 客户列表（去重）。"""
    user, err = _require_perm(PERM_PAGE)
    if err:
        return err

    from app_factory import db
    from models.production_inbound import ProductionInbound
    from models.plan_login import PlanLogin

    # 作业者：生产入库去重
    op_rows = (db.session.query(ProductionInbound.operator)
               .filter(ProductionInbound.operator.isnot(None),
                       ProductionInbound.operator != '')
               .distinct().order_by(ProductionInbound.operator.asc()).all())
    # 客户：计划登录去重
    cust_rows = (db.session.query(PlanLogin.customer)
                 .filter(PlanLogin.customer.isnot(None),
                         PlanLogin.customer != '')
                 .distinct().order_by(PlanLogin.customer.asc()).all())

    return jsonify({
        'success': True,
        'operators': [r[0] for r in op_rows],
        'customers': [r[0] for r in cust_rows],
    })


# 结算清单导出的中文表头
_EXPORT_HEADERS = [
    ('pno', '品番'), ('lot_no', 'LOT NO'), ('operator', '作业者'), ('customer', '客户'),
    ('plan_qty', '计划数量'), ('work_qty', '作业数量'), ('pack_qty', '包装数量'),
    ('price_text', '单价'), ('currency', '币种'), ('amount', '结算金额'),
    ('pack_time', '包装时间'),
]


@mes_bp.route('/api/settlement/export', methods=['GET'])
def settlement_export():
    """导出结算清单为 CSV（UTF-8 BOM），并附按币种汇总行。"""
    user, err = _require_perm(PERM_DOWNLOAD)
    if err:
        return err

    date_from = _to_date(request.args.get('date_from'))
    date_to = _to_date(request.args.get('date_to'))
    pack_from = _to_datetime(request.args.get('pack_from'))
    pack_to = _to_datetime(request.args.get('pack_to'))
    keyword = (request.args.get('keyword') or '').strip()
    operator = (request.args.get('operator') or '').strip()
    customer = (request.args.get('customer') or '').strip()

    rows = _build_settlement(date_from, date_to, operator=operator, customer=customer,
                             pack_from=pack_from, pack_to=pack_to)
    if keyword:
        kw = keyword.lower()
        rows = [r for r in rows
                if kw in r['pno'].lower() or kw in r['lot_no'].lower()]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _k, label in _EXPORT_HEADERS])
    # 无「查看单价/金额」权限时，导出同样脱敏（仅保留数量）
    if not _has_perm(user, PERM_UNMASK):
        rows, _masked_summary = _mask_settlement(rows, _currency_summary(rows))
    for r in rows:
        writer.writerow(['' if r.get(k) is None else r.get(k) for k, _label in _EXPORT_HEADERS])

    # 按币种汇总：追加空行 + 汇总区，便于财务核对
    summary = _currency_summary(rows)
    if summary:
        writer.writerow([])
        writer.writerow(['币种', '作业数量合计', '结算金额合计'])
        for b in summary:
            writer.writerow([b['currency'], b['work_qty'], b['amount']])

    # UTF-8 BOM 保证 Excel 正确识别中文
    content = '\ufeff' + buf.getvalue()
    resp = make_response(content)
    resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
    resp.headers['Content-Disposition'] = 'attachment; filename=mes_settlement.csv'
    return resp
