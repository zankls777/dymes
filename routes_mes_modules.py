# -*- coding: utf-8 -*-
"""MES 模块页面蓝图

包含 4 个页面（基础框架 + 标准 CRUD + CSV 导出）：
    - 计划登录      /mes/plan
    - 生产入库      /mes/production
    - 品质检查(包装) /mes/quality
    - 单价登陆      /mes/price

当前阶段：基础框架 + 占位页面（数据模型 / 权限 / 菜单已就绪，
业务逻辑后续按需补充）。字段为通用命名，业务细化时再调整。

权限编码（与 utils/permission_catalog.py 一致）：
    页面访问 can_{key}；操作 can_{key}_add / _edit / _delete / _download
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

mes_mod_bp = Blueprint('mes_mod', __name__, url_prefix='/mes')


# --------------------------------------------------------------------------
# 页面注册表：key -> 配置
# 模型通过延迟导入函数获取，避免模块级循环导入
# --------------------------------------------------------------------------
def _model_plan():
    from models.plan_login import PlanLogin
    return PlanLogin


def _model_production():
    from models.production_inbound import ProductionInbound
    return ProductionInbound


def _model_quality():
    from models.quality_check import QualityCheck
    return QualityCheck


def _model_price():
    from models.unit_price import UnitPrice
    return UnitPrice


# 各页面的配置：
#   route      页面路由（相对蓝图的 /mes 前缀）
#   template   模板文件名
#   model_get  模型获取函数
#   date_field 主日期字段（用于日期区间筛选，None 表示无）
#   keyword_fields 关键字模糊匹配字段
#   bool_fields / int_fields / float_fields 字段类型（用于表单值转换）
PAGES = {
    'plan': {
        'route': '/plan',
        'template': 'mes_plan_login.html',
        'title': '计划登录',
        'model_get': _model_plan,
        'date_field': 'plan_date',
        'keyword_fields': ['lot_no', 'pno', 'vehicle_type', 'customer', 'remark'],
        'int_fields': ['plan_qty'],
        'float_fields': [],
        'str_fields': ['lot_no', 'pno', 'vehicle_type', 'customer', 'status', 'remark'],
        # Excel 导入：中文表头 -> 字段，以及必填字段（中文名用于报错提示）
        'upload_map': {
            '品番': 'pno', 'LOT': 'lot_no', 'LOT NO': 'lot_no', 'LOTNO': 'lot_no',
            '数量': 'plan_qty', '计划数量': 'plan_qty', '客户': 'customer',
            '车种': 'vehicle_type', '计划日期': 'plan_date', '备注': 'remark',
        },
        'upload_required': {'pno': '品番', 'lot_no': 'LOT', 'plan_qty': '数量', 'customer': '客户'},
    },
    'production': {
        'route': '/production',
        'template': 'mes_production_inbound.html',
        'title': '生产入库',
        'model_get': _model_production,
        'date_field': 'inbound_date',
        'keyword_fields': ['lot_no', 'pno', 'vehicle_type', 'operator', 'remark'],
        'int_fields': ['inbound_qty'],
        'float_fields': ['unit_price'],
        'str_fields': ['lot_no', 'pno', 'vehicle_type', 'operator', 'remark'],
        # 列表独立筛选：作业人 / 品番（额外精确匹配，见 _make_list_view）
        'filter_fields': {'operator': 'operator', 'pno': 'pno'},
        # 导出列顺序：日期/LOT/品番/车种/数量/作业者/单价/备注/操作人/创建时间
        'export_headers': [
            ('inbound_date', '入库日期'), ('lot_no', 'LOT NO'), ('pno', '品番'),
            ('vehicle_type', '车种'), ('inbound_qty', '入库数量'), ('operator', '作业者'),
            ('unit_price', '单价'), ('remark', '备注'),
            ('created_by_name', '操作人'), ('created_at', '创建时间'),
            ('updated_at', '更新时间'),
        ],
        # Excel 导入：必填 品番 / LOT / 入库数量；单价由系统按品番自动计算，不收用户填写
        'upload_map': {
            '品番': 'pno', 'LOT': 'lot_no', 'LOT NO': 'lot_no', 'LOTNO': 'lot_no',
            '入库数量': 'inbound_qty', '数量': 'inbound_qty',
            '入库日期': 'inbound_date', '车种': 'vehicle_type',
            '作业者': 'operator', '备注': 'remark',
        },
        'upload_required': {'pno': '品番', 'lot_no': 'LOT', 'inbound_qty': '入库数量'},
    },
    'quality': {
        'route': '/quality',
        'template': 'mes_quality_check.html',
        'title': '品质检查(包装)',
        'model_get': _model_quality,
        'date_field': 'check_date',
        # 包装时间（时间戳）：结算清单按此列做包装时间区间筛选
        'datetime_fields': ['check_at'],
        'keyword_fields': ['lot_no', 'pno', 'vehicle_type', 'checker', 'remark'],
        'int_fields': ['check_qty', 'ok_qty', 'ng_qty'],
        'float_fields': [],
        'str_fields': ['lot_no', 'pno', 'vehicle_type', 'result', 'checker', 'remark'],
        # Excel 导入：必填 品番 / LOT / 检查数量
        'upload_map': {
            '品番': 'pno', 'LOT': 'lot_no', 'LOT NO': 'lot_no', 'LOTNO': 'lot_no',
            '检查数量': 'check_qty', '包装数量': 'check_qty',
            '合格数量': 'ok_qty', '不合格数量': 'ng_qty',
            '检查日期': 'check_date', '包装时间': 'check_at', '车种': 'vehicle_type',
            '检查员': 'checker', '判定结果': 'result', '备注': 'remark',
        },
        'upload_required': {'pno': '品番', 'lot_no': 'LOT', 'check_qty': '检查数量'},
        # 导出：在默认数量列后补充包装时间，方便财务核对包装时段
        'export_headers': [
            ('check_date', '检查日期'), ('check_at', '包装时间'),
            ('pno', '品番'), ('vehicle_type', '车种'), ('lot_no', 'LOT NO'),
            ('check_qty', '检查数量'), ('ok_qty', '合格数量'), ('ng_qty', '不合格数量'),
            ('checker', '检查员'), ('result', '判定结果'),
            ('remark', '备注'), ('created_by_name', '操作人'), ('created_at', '创建时间'),
            ('updated_at', '更新时间'),
        ],
    },
    'price': {
        'route': '/price',
        'template': 'mes_unit_price.html',
        'title': '单价登陆',
        'model_get': _model_price,
        'date_field': 'effective_date',
        'keyword_fields': ['pno', 'vehicle_type', 'customer', 'remark'],
        'int_fields': [],
        'float_fields': ['unit_price'],
        'str_fields': ['pno', 'vehicle_type', 'currency', 'customer', 'remark'],
        # Excel 导入：必填 品番 / 单价（生效日期默认今天、币种默认 CNY）
        'upload_map': {
            '品番': 'pno', '单价': 'unit_price', '币种': 'currency',
            '生效日期': 'effective_date', '车种': 'vehicle_type',
            '客户': 'customer', '备注': 'remark',
        },
        'upload_required': {'pno': '品番', 'unit_price': '单价'},
    },
}


def _perm_codes(key):
    """返回某页面的 5 个权限编码。"""
    base = f'can_mes_{key}'
    return base, f'{base}_add', f'{base}_edit', f'{base}_delete', f'{base}_download'


# 生产入库：查看单价权限（未授权则服务端脱敏，前端不可见）
PERM_PRODUCTION_UNMASK = 'can_mes_production_unmask'


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
    """管理员恒有权限；否则查询 RBAC 有效权限（走统一短 TTL 缓存）。"""
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


def _operator_names(records):
    """批量查询操作人姓名，返回 {user_id: 姓名}。"""
    from models.user_info import UserInfo
    ids = {r.created_by for r in records if getattr(r, 'created_by', None)}
    if not ids:
        return {}
    rows = UserInfo.query.filter(UserInfo.id.in_(ids)).all()
    return {u.id: (getattr(u, 'username', '') or getattr(u, 'real_name', ''))
            for u in rows}


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
    """把时间字符串解析为 datetime；兼容日期/分钟/秒三种精度，失败返回 None。"""
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
# 页面路由
# --------------------------------------------------------------------------
def _make_page_view(key):
    cfg = PAGES[key]
    perm_page, perm_add, perm_edit, perm_delete, perm_download = _perm_codes(key)

    def view():
        if 'user_id' not in session:
            return redirect(url_for('main.login', next=request.url))

        user = _current_user()
        if not _has_perm(user, perm_page):
            flash(f'您没有 {cfg["title"]} 页面的访问权限', 'error')
            from routes import forbidden_page
            return forbidden_page(f'您没有 {cfg["title"]} 页面的访问权限')

        resp = make_response(render_template(
            cfg['template'],
            page_title=cfg['title'],
            api_base=f'/mes/api/{key}',
            can_add=_has_perm(user, perm_add),
            can_edit=_has_perm(user, perm_edit),
            can_delete=_has_perm(user, perm_delete),
            can_download=_has_perm(user, perm_download),
            # 生产入库：单价是否可见（未授权则列表/弹窗均打码）
            can_unmask=_has_perm(user, PERM_PRODUCTION_UNMASK),
            # 生产入库：默认作业者按登录账号预填（其他页面忽略该变量）
            default_operator=_default_operator(user),
        ))
        # 页面含交互逻辑，禁用缓存避免浏览器执行旧版本脚本
        resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return resp

    view.__name__ = f'{key}_page'
    return view


def _apply_filters(query, Model, cfg):
    """统一应用列表/导出的筛选条件：关键字、日期区间、独立字段（作业人/品番等）。

    返回过滤后的 query，供列表与导出复用，保证两者口径一致。
    """
    keyword = (request.args.get('keyword') or '').strip()
    date_from = _to_date(request.args.get('date_from'))
    date_to = _to_date(request.args.get('date_to'))

    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db_or(*[
            getattr(Model, f).like(like) for f in cfg['keyword_fields']
        ]))
    # 独立精确筛选（如生产入库按「作业人」「品番」下拉过滤）
    for arg_name, field in (cfg.get('filter_fields') or {}).items():
        val = (request.args.get(arg_name) or '').strip()
        if val:
            query = query.filter(getattr(Model, field) == val)
    if cfg['date_field']:
        date_col = getattr(Model, cfg['date_field'])
        if date_from:
            query = query.filter(date_col >= date_from)
        if date_to:
            query = query.filter(date_col <= date_to)
    return query


def _make_list_view(key):
    cfg = PAGES[key]
    perm_page, _, _, _, _ = _perm_codes(key)

    def view():
        user, err = _require_perm(perm_page)
        if err:
            return err

        Model = cfg['model_get']()
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 200)

        query = _apply_filters(Model.query, Model, cfg)

        order_col = getattr(Model, cfg['date_field']) if cfg['date_field'] else Model.id
        pagination = query.order_by(order_col.desc(), Model.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        names = _operator_names(pagination.items)

        # 生产入库：无「查看单价」权限时服务端直接抹去单价，防止仅靠前端打码被绕过
        items = [r.to_dict(names) for r in pagination.items]
        can_unmask = _has_perm(user, PERM_PRODUCTION_UNMASK)
        if key == 'production' and not can_unmask:
            for it in items:
                it['unit_price'] = '***'  # 无权限：返回打码值而非空值

        return jsonify({
            'success': True,
            'items': items,
            'total': pagination.total,
            'page': pagination.page,
            'pages': pagination.pages,
            'can_unmask': can_unmask,
        })

    view.__name__ = f'{key}_list'
    return view


def _apply_payload(record, cfg, data):
    """把请求数据写入模型实例（用于新增/编辑）。"""
    for f in cfg['str_fields']:
        setattr(record, f, (data.get(f) or '').strip() or None)
    for f in cfg['int_fields']:
        try:
            setattr(record, f, int(data.get(f) or 0))
        except (TypeError, ValueError):
            setattr(record, f, 0)
    for f in cfg['float_fields']:
        # 生产入库单价为系统计算值，禁止前端直接写入（见下方 _apply_prod_unit_price）
        if cfg is PAGES.get('production') and f == 'unit_price':
            continue
        try:
            setattr(record, f, float(data.get(f) or 0))
        except (TypeError, ValueError):
            setattr(record, f, 0)
    if cfg['date_field']:
        setattr(record, cfg['date_field'], _to_date(data.get(cfg['date_field'])))
    # 包装时间等时间戳字段：兼容 'YYYY-MM-DD HH:MM:SS' / 'YYYY-MM-DDTHH:MM'
    for f in cfg.get('datetime_fields') or []:
        dt = _to_datetime(data.get(f))
        # 品质检查的包装时间：前端留空时默认记录当前时间，避免结算清单缺时间
        if dt is None and f == 'check_at':
            dt = datetime.now()
        setattr(record, f, dt)


def _apply_prod_unit_price(record, cfg, data):
    """生产入库：服务端按「品番生效单价 × 倍数」重算单价，前端提交值一律忽略。

    - 有权限：按品番+入库日期取生效单价，再按 作业者+日期(+品番) 匹配倍数相乘；
    - 无权限：不计算真实单价，保留记录原值（编辑）或 0（新增），避免越权写入/读取。
    返回是否写入了真实单价。
    """
    if cfg is not PAGES.get('production'):
        return True  # 非生产入库页面不处理

    from app_factory import db  # noqa: F401 保证 session 可用
    user = _current_user()
    if not _has_perm(user, PERM_PRODUCTION_UNMASK):
        # 保持原值：编辑时不动，新增时为默认 0
        if record.unit_price is None:
            record.unit_price = 0
        return False

    pno = (data.get('pno') or '').strip()
    on_date = _to_date(data.get(cfg['date_field'])) or datetime.now().date()
    price, _eff, _cur = _lot_unit_price(pno, on_date)
    base = float(price) if price is not None else 0.0
    operator = (data.get('operator') or '').strip()
    mult, _rule = _match_price_multiplier(operator, on_date, pno)
    record.unit_price = round(base * (mult if mult is not None else 1), 4)
    return True


def _validate_payload(cfg, data):
    """表单必填校验：基于 upload_required（字段->中文名），返回错误信息或 None。

    计划登录：品番/LOT/数量/客户 必填；品质检查：品番/LOT/检查数量 必填；
    单价登陆：品番/单价 必填。日期字段另行校验格式。
    """
    for field, cn in (cfg.get('upload_required') or {}).items():
        val = data.get(field)
        if val is None or str(val).strip() == '':
            return f'{cn}为必填项'
    return None


def _make_create_view(key):
    cfg = PAGES[key]
    _, perm_add, _, _, _ = _perm_codes(key)

    def view():
        user, err = _require_perm(perm_add)
        if err:
            return err

        from app_factory import db
        Model = cfg['model_get']()

        data = request.get_json(silent=True) or {}
        if not (data.get('pno') or '').strip():
            return jsonify({'success': False, 'message': '品番为必填项'}), 400
        msg = _validate_payload(cfg, data)
        if msg:
            return jsonify({'success': False, 'message': msg}), 400
        if cfg['date_field'] and not _to_date(data.get(cfg['date_field'])):
            return jsonify({'success': False, 'message': '日期格式不正确'}), 400

        # 生产入库：入库数量不得超过计划登录数量（未登录计划则按入荷数量）
        err = _check_prod_inbound_qty(None, cfg, data)
        if err:
            return err

        # 品质检查：包装(检查)数量不得超过计划登录数量
        err = _check_quality_qty(None, cfg, data)
        if err:
            return err

        record = Model()
        _apply_payload(record, cfg, data)
        # 生产入库：单价由服务端按 品番生效单价 × 倍数 重算，忽略前端提交值
        _apply_prod_unit_price(record, cfg, data)
        record.created_by = user.id
        # 冗余保存操作人姓名快照，账号删除后记录仍可显示文本
        record.created_by_name = getattr(user, 'username', None) or getattr(user, 'full_name', None)
        db.session.add(record)
        # 品质检查新增：按包装进度联动计划登录状态（包满即「完成」）
        if cfg is PAGES.get('quality'):
            _sync_plan_status(record.lot_no)
        db.session.commit()
        item = record.to_dict()
        # 无「查看单价」权限时，回包同样打码
        if cfg is PAGES.get('production') and not _has_perm(user, PERM_PRODUCTION_UNMASK):
            item['unit_price'] = '***'
        return jsonify({'success': True, 'item': item})

    view.__name__ = f'{key}_create'
    return view


def _make_update_view(key):
    cfg = PAGES[key]
    _, _, perm_edit, _, _ = _perm_codes(key)

    def view(record_id):
        user, err = _require_perm(perm_edit)
        if err:
            return err

        from app_factory import db
        Model = cfg['model_get']()

        record = db.session.get(Model, record_id)
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404

        data = request.get_json(silent=True) or {}
        if not (data.get('pno') or '').strip():
            return jsonify({'success': False, 'message': '品番为必填项'}), 400
        msg = _validate_payload(cfg, data)
        if msg:
            return jsonify({'success': False, 'message': msg}), 400
        if cfg['date_field'] and not _to_date(data.get(cfg['date_field'])):
            return jsonify({'success': False, 'message': '日期格式不正确'}), 400

        # 生产入库：入库数量校验（编辑时排除本记录自身已占用量）
        err = _check_prod_inbound_qty(record, cfg, data, exclude_id=record_id)
        if err:
            return err

        # 品质检查：包装(检查)数量校验（编辑时排除本记录自身已占用量）
        err = _check_quality_qty(record, cfg, data, exclude_id=record_id)
        if err:
            return err

        _apply_payload(record, cfg, data)
        # 生产入库：单价由服务端重算，禁止前端修改
        _apply_prod_unit_price(record, cfg, data)
        # 品质检查编辑：按包装进度联动计划登录状态（包满即「完成」，撤回则复原）
        if cfg is PAGES.get('quality'):
            _sync_plan_status(record.lot_no)
        db.session.commit()
        item = record.to_dict()
        # 无「查看单价」权限时，回包同样打码
        if cfg is PAGES.get('production') and not _has_perm(user, PERM_PRODUCTION_UNMASK):
            item['unit_price'] = '***'
        return jsonify({'success': True, 'item': item})

    view.__name__ = f'{key}_update'
    return view


def _make_delete_view(key):
    cfg = PAGES[key]
    _, _, _, perm_delete, _ = _perm_codes(key)

    def view(record_id):
        user, err = _require_perm(perm_delete)
        if err:
            return err

        from app_factory import db
        Model = cfg['model_get']()

        record = db.session.get(Model, record_id)
        if not record:
            return jsonify({'success': False, 'message': '记录不存在'}), 404

        # 工序顺序保护：下游工序已开始则禁止删除上游记录
        if key == 'plan':
            # 计划登录（上工序）：完成状态或已有生产入库/包装记录时禁删
            if (record.status or '') == 'done':
                return jsonify({'success': False,
                                'message': '该计划已「完成」，禁止删除。'}), 400
            if _lot_has_production(record.lot_no) or _lot_has_quality(record.lot_no):
                return jsonify({'success': False,
                                'message': '该 LOT 已进入生产入库/品质检查工序，禁止删除计划登录。'}), 400
        elif key == 'production':
            # 生产入库（中工序）：已进入品质检查(包装)时禁删
            if _lot_has_quality(record.lot_no):
                return jsonify({'success': False,
                                'message': '该 LOT 已进入品质检查(包装)工序，禁止删除生产入库。'}), 400

        # 品质检查删除后需回算计划状态（可能由「完成」复原为「未完成」）
        lot_no = record.lot_no
        db.session.delete(record)
        if key == 'quality':
            db.session.flush()          # 先落地删除，再汇总剩余包装数量
            _sync_plan_status(lot_no)
        db.session.commit()
        return jsonify({'success': True})

    view.__name__ = f'{key}_delete'
    return view


def _make_export_view(key):
    cfg = PAGES[key]
    _, _, _, _, perm_download = _perm_codes(key)

    def view():
        user, err = _require_perm(perm_download)
        if err:
            return err

        Model = cfg['model_get']()
        query = _apply_filters(Model.query, Model, cfg)

        order_col = getattr(Model, cfg['date_field']) if cfg['date_field'] else Model.id
        records = query.order_by(order_col.desc(), Model.id.desc()).all()
        names = _operator_names(records)

        # 导出表头：模型 to_dict 的键 -> 中文名
        headers = cfg.get('export_headers') or _default_headers(cfg)
        # 生产入库：无「查看单价」权限时导出同样脱敏
        mask_price = key == 'production' and not _has_perm(user, PERM_PRODUCTION_UNMASK)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([label for _k, label in headers])
        for r in records:
            d = r.to_dict(names)
            if mask_price:
                d['unit_price'] = '***'  # 无权限导出：单价列打码
            writer.writerow(['' if d.get(k) is None else d.get(k) for k, _label in headers])

        content = '\ufeff' + buf.getvalue()  # UTF-8 BOM 保证 Excel 正确识别中文
        resp = make_response(content)
        resp.headers['Content-Type'] = 'text/csv; charset=utf-8'
        resp.headers['Content-Disposition'] = f'attachment; filename=mes_{key}.csv'
        return resp

    view.__name__ = f'{key}_export'
    return view


# --------------------------------------------------------------------------
# Excel 导入：按中文表头名识别列（列顺序无关），按 upload_required 校验必填
# --------------------------------------------------------------------------
def _cell_text(v):
    """单元格值转字符串：去掉首尾空白，NaN/None 归一为空串。"""
    if v is None:
        return ''
    # pandas 空值（NaN）判断，避免依赖 numpy 类型
    if isinstance(v, float) and v != v:
        return ''
    s = str(v).strip()
    return '' if s.lower() in ('nan', 'nat', 'none') else s


def _parse_rows(file_bytes):
    """解析上传的 Excel，返回 [(行号, {表头: 值}), ...]。

    只读取第一个工作表；跳过完全空行；列名去除首尾空白。
    """
    import pandas as pd
    df = pd.read_excel(io.BytesIO(file_bytes), sheet_name=0, dtype=object)
    if df is None or df.empty:
        return []
    # 表头去空白，便于按中文名精确匹配（列顺序任意）
    headers = [_cell_text(c) for c in df.columns]
    rows = []
    for i, row in df.iterrows():
        cells = {headers[j]: _cell_text(row.iloc[j]) for j in range(len(headers))}
        if any(cells.values()):  # 跳过整行空值
            rows.append((i + 2, cells))  # +2：Excel 行号（含表头行）
    return rows


def _map_columns(headers, upload_map):
    """把中文表头映射为模型字段：返回 {字段: 表头中文名}。

    同名多列（如「数量」「计划数量」都映射 plan_qty）时保留先出现的表头。
    """
    field_to_header = {}
    for header in headers:
        field = upload_map.get(header)
        if field and field not in field_to_header:
            field_to_header[field] = header
    return field_to_header


def _make_template_view(key):
    """通用模板下载视图工厂：GET /mes/api/{key}/template。

    依据 upload_map / upload_required 动态生成带标准中文表头的 Excel 模板，
    必填列以红色表头标注，并附一行示例 + 「填写说明」工作表。
    """
    cfg = PAGES[key]

    def view():
        user, err = _require_perm(_perm_codes(key)[0])
        if err:
            return err

        from flask import send_file
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment
        except ImportError:
            return jsonify({'success': False, 'message': '服务器缺少 openpyxl 依赖'}), 500

        # 表头顺序：按 upload_map 出现顺序去重字段，再取其中文名
        upload_map = cfg.get('upload_map') or {}
        required = cfg.get('upload_required') or {}
        headers, seen = [], set()
        for cn, field in upload_map.items():
            if field in seen:
                continue
            seen.add(field)
            headers.append(cn)

        wb = Workbook()
        ws = wb.active
        ws.title = cfg['title']
        ws.append(headers)

        fill_optional = PatternFill('solid', fgColor='4A6FA5')  # 普通列
        fill_required = PatternFill('solid', fgColor='C00000')  # 必填列（红色提示）
        font_white = Font(color='FFFFFF', bold=True)
        center = Alignment(horizontal='center', vertical='center')
        for idx, cn in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=idx)
            field = upload_map.get(cn)
            cell.fill = fill_required if field in required else fill_optional
            cell.font = font_white
            cell.alignment = center

        # 示例行：按字段类型给出合理占位值，提示用户删除后填写
        samples = {
            'pno': 'EXAMPLE-001', 'lot_no': 'LOT-EXAMPLE-001',
            'plan_qty': 100, 'inbound_qty': 100, 'check_qty': 100,
            'ok_qty': 100, 'ng_qty': 0, 'unit_price': 9.5,
            'currency': 'CNY', 'customer': '示例客户', 'vehicle_type': '示例车种',
            'operator': '示例作业者', 'checker': '示例检查员', 'result': '合格',
            'remark': '示例行，请删除',
            'plan_date': datetime.now().strftime('%Y-%m-%d'),
            'inbound_date': datetime.now().strftime('%Y-%m-%d'),
            'check_date': datetime.now().strftime('%Y-%m-%d'),
            'check_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'effective_date': datetime.now().strftime('%Y-%m-%d'),
        }
        sample_row = []
        for cn in headers:
            field = upload_map.get(cn)
            sample_row.append(samples.get(field, ''))
        ws.append(sample_row)

        for i, _cn in enumerate(headers, start=1):
            ws.column_dimensions[chr(64 + i)].width = 18

        # 填写说明工作表：逐字段标注必填与含义
        tips = wb.create_sheet('填写说明')
        tips.append(['列名', '是否必填', '说明'])
        for cn in headers:
            field = upload_map.get(cn)
            is_req = '是' if field in required else '否'
            note = _TEMPLATE_NOTES.get(field, '')
            tips.append([cn, is_req, note])
        tips.append(['', '', ''])
        tips.append(['提示', '', '示例行请删除后再填写实际数据；日期格式 YYYY-MM-DD。'])
        for i, w in enumerate([18, 10, 46], start=1):
            tips.column_dimensions[chr(64 + i)].width = w

        bio = io.BytesIO()
        wb.save(bio)
        bio.seek(0)
        return send_file(
            bio,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'{cfg["title"]}导入模板.xlsx',
        )

    view.__name__ = f'{key}_template'
    return view


# 模板「填写说明」中各字段的说明文本
_TEMPLATE_NOTES = {
    'pno': '品番，系统内用于关联单价、计划等数据',
    'lot_no': 'LOT NO，建议与扫码/LOT 登录保持一致',
    'plan_qty': '计划数量，整数',
    'inbound_qty': '入库数量，整数，不得超过计划/入荷数量',
    'check_qty': '检查（包装）数量，整数，不得超过计划数量',
    'ok_qty': '合格数量，整数',
    'ng_qty': '不合格数量，整数',
    'unit_price': '单价，数字；生产入库单价由系统自动计算，无需填写',
    'currency': '币种，如 CNY / JPY / USD，缺省 CNY',
    'customer': '客户名称，需与客户主数据一致',
    'vehicle_type': '车种',
    'operator': '作业者姓名',
    'checker': '检查员姓名',
    'result': '判定结果，如 合格/不合格',
    'remark': '备注',
    'plan_date': '计划日期，格式 YYYY-MM-DD',
    'inbound_date': '入库日期，格式 YYYY-MM-DD，缺省今天',
    'check_date': '检查日期，格式 YYYY-MM-DD，缺省今天',
    'check_at': '包装时间，格式 YYYY-MM-DD HH:MM:SS，缺省取当前时间',
    'effective_date': '生效日期，格式 YYYY-MM-DD，缺省今天',
}


def _make_upload_view(key):
    """通用 Excel 导入视图工厂：POST /mes/api/{key}/upload（multipart 文件域 file）。"""
    cfg = PAGES[key]
    _, perm_add, _, _, _ = _perm_codes(key)

    def view():
        user, err = _require_perm(perm_add)
        if err:
            return err

        file = request.files.get('file')
        if file is None or not file.filename:
            return jsonify({'success': False, 'message': '请选择要上传的 Excel 文件'}), 400

        try:
            file_bytes = file.read()
            rows = _parse_rows(file_bytes)
        except Exception as exc:  # 文件损坏/格式不支持
            logger.warning('MES %s Excel 解析失败: %s', key, exc)
            return jsonify({'success': False, 'message': '文件解析失败，请确认是有效的 Excel 文件'}), 400

        if not rows:
            return jsonify({'success': False, 'message': '未读取到有效数据行'}), 400

        field_to_header = _map_columns(list(rows[0][1].keys()), cfg.get('upload_map') or {})
        # 校验必填列是否存在于表头中
        required = cfg.get('upload_required') or {}
        missing_cols = [cn for field, cn in required.items() if field not in field_to_header]
        if missing_cols:
            return jsonify({'success': False,
                            'message': f'Excel 缺少必需列：{"、".join(missing_cols)}'}), 400

        success, errors = _import_rows(user, cfg, rows, field_to_header, required)
        return jsonify({
            'success': True,
            'imported': len(success),
            'failed': len(errors),
            'errors': errors[:100],       # 最多回传 100 条错误明细
            'error_total': len(errors),
        })

    view.__name__ = f'{key}_upload'
    return view


def _import_rows(user, cfg, rows, field_to_header, required):
    """逐行导入：返回 (成功写入的 dict 列表, 错误明细列表)。"""
    from app_factory import db
    Model = cfg['model_get']()
    success, errors = [], []
    # 同一批次内按 LOT 累加已导入数量：commit 前 SUM 查询看不到未提交行，
    # 需自行累计，避免同文件多行合计超量却逐行校验通过。
    batch_lot_qty = {}
    can_unmask = _has_perm(user, PERM_PRODUCTION_UNMASK)

    for row_no, cells in rows:
        data = {field: cells.get(header, '') for field, header in field_to_header.items()}

        # 行级必填校验：逐字段收集缺失项，附行号便于用户定位
        blanks = [cn for field, cn in required.items() if not _cell_text(data.get(field))]
        if blanks:
            errors.append({'row': row_no, 'message': f'缺少必填：{"、".join(blanks)}'})
            continue

        # 品质检查：包装(检查)数量不得超过计划登录数量（Excel 导入同样校验）
        if cfg is PAGES.get('quality'):
            limit, source = _quality_qty_limit(data.get('lot_no'))
            if limit is not None:
                try:
                    row_qty = int(float(data.get('check_qty') or 0))
                except (TypeError, ValueError):
                    row_qty = 0
                if row_qty > limit:
                    errors.append({'row': row_no,
                                   'message': f'包装数量（{row_qty}）超出「{source}」可包装上限（{limit}）'})
                    continue

        # 生产入库：入库数量不得超过计划/入荷数量（扣除本批次已占用部分）
        if cfg is PAGES.get('production'):
            lot_no = (data.get('lot_no') or '').strip()
            try:
                row_qty = int(float(data.get('inbound_qty') or 0))
            except (TypeError, ValueError):
                row_qty = 0
            limit, source = _prod_inbound_qty_limit(lot_no)
            if limit is not None and row_qty > max(limit - batch_lot_qty.get(lot_no, 0), 0):
                remain = max(limit - batch_lot_qty.get(lot_no, 0), 0)
                errors.append({'row': row_no,
                               'message': f'入库数量（{row_qty}）超出「{source}」可入库上限（{remain}）'})
                continue

        try:
            record = Model()
            _apply_payload(record, cfg, data)
            _fill_upload_defaults(key=cfg['route'], record=record, data=data)
            record.created_by = user.id
            record.created_by_name = getattr(user, 'username', None) or getattr(user, 'full_name', None)
            # 生产入库：单价由服务端按 品番生效单价 × 倍数 重算；
            # 无「查看单价」权限时不写入真实单价（保持 0），避免越权读取。
            if cfg is PAGES.get('production'):
                if can_unmask:
                    _apply_prod_unit_price(record, cfg, data)
                else:
                    record.unit_price = 0
                lot_no = (data.get('lot_no') or '').strip()
                batch_lot_qty[lot_no] = batch_lot_qty.get(lot_no, 0) + max(
                    int(float(data.get('inbound_qty') or 0) or 0), 0)
            db.session.add(record)
            success.append(record)
        except Exception as exc:  # 单行失败不影响其它行
            db.session.rollback()
            logger.warning('MES %s 第 %s 行导入失败: %s', cfg['route'], row_no, exc)
            errors.append({'row': row_no, 'message': '数据格式不正确'})

    if success:
        db.session.commit()
    return success, errors


def _fill_upload_defaults(key, record, data):
    """Excel 导入缺省值：日期缺省为今天、币种缺省为 CNY。"""
    from datetime import date as _date
    # 各页面主日期缺省为今天（inbound_date 为 NOT NULL，缺失时必须补默认值）
    for date_field in ('plan_date', 'inbound_date', 'check_date', 'effective_date'):
        if hasattr(record, date_field) and not getattr(record, date_field):
            setattr(record, date_field, _date.today())
    if hasattr(record, 'currency') and not (record.currency or '').strip():
        record.currency = 'CNY'
    # 包装时间：导入未填时默认取当前时间，保证结算清单包装时间有值
    if hasattr(record, 'check_at') and not getattr(record, 'check_at'):
        record.check_at = datetime.now()


def _default_headers(cfg):
    """由字段配置推导导出表头。"""
    date_label = {
        'plan_date': '计划日期', 'inbound_date': '入库日期',
        'check_date': '检查日期', 'effective_date': '生效日期',
    }
    cols = []
    if cfg['date_field']:
        cols.append((cfg['date_field'], date_label.get(cfg['date_field'], '日期')))
    cols.extend([
        ('pno', '品番'), ('vehicle_type', '车种'),
    ])
    for f in cfg['int_fields']:
        cols.append((f, _CN_LABELS.get(f, f)))
    for f in cfg['float_fields']:
        cols.append((f, _CN_LABELS.get(f, f)))
    cols.append(('remark', '备注'))
    cols.append(('created_by_name', '操作人'))
    cols.append(('created_at', '创建时间'))
    cols.append(('updated_at', '更新时间'))
    return cols


_CN_LABELS = {
    'plan_qty': '计划数量', 'inbound_qty': '入库数量',
    'check_qty': '检查数量', 'ok_qty': '合格数量', 'ng_qty': '不合格数量',
    'unit_price': '单价', 'currency': '币种', 'customer': '客户',
    'checker': '检查员', 'result': '判定结果',
}


def db_or(*conditions):
    """延迟导入 or_，避免模块级循环导入。"""
    from sqlalchemy import or_ as _or
    return _or(*conditions)


# --------------------------------------------------------------------------
# LOT 反查：按 LOT NO 从 lot_login_info 带出品番/车种/数量
# 计划登录、生产入库共用
# --------------------------------------------------------------------------
def _query_lot_info(lot_no):
    """按 LOT NO 查询登录资料，返回 dict 或 None。"""
    from models.lot_login_info import LotLoginInfo
    li = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
    if not li:
        return None

    return {
        'lot_no': li.lot_no or '',
        'pno': li.part_no or '',
        'vehicle_type': li.vehicle_type or '',
        'item_name': li.item_name or '',
        'qty': li.quantity or 0,
    }


def _prod_inbound_qty_limit(lot_no, exclude_id=None):
    """生产入库数量上限校验。

    口径：优先取「计划登录」的计划数量；该 LOT 未登录计划时，回退取
    「LOT 登录页面」的入荷数量（lot_login_info.quantity）。

    返回 (上限, 上限来源说明) 或 (None, '') 表示查不到、不限制。
    exclude_id：编辑场景下排除当前记录本身已占用的入库数量，避免误判超量。
    """
    from app_factory import db

    lot_no = (lot_no or '').strip()
    if not lot_no:
        return None, ''

    limit, source = None, ''
    # 1) 优先计划登录数量
    from models.plan_login import PlanLogin
    plan = PlanLogin.query.filter_by(lot_no=lot_no).first()
    if plan is not None and plan.plan_qty is not None:
        limit, source = int(plan.plan_qty or 0), '计划登录数量'
    else:
        # 2) 未登录计划时，读取 LOT 登录页面的入荷数量
        from models.lot_login_info import LotLoginInfo
        li = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
        if li is not None and li.quantity is not None:
            limit, source = int(li.quantity or 0), '入荷数量'

    if limit is None:
        return None, ''

    # 该 LOT 已入库总量（编辑时排除当前记录），防止多笔累计超量
    from models.production_inbound import ProductionInbound
    used_q = db.session.query(db.func.coalesce(db.func.sum(ProductionInbound.inbound_qty), 0)).filter(
        ProductionInbound.lot_no == lot_no)
    if exclude_id is not None:
        used_q = used_q.filter(ProductionInbound.id != exclude_id)
    used = int(used_q.scalar() or 0)
    return max(limit - used, 0), source


def _check_prod_inbound_qty(record, cfg, data, exclude_id=None):
    """生产入库保存前校验：入库数量不得超过计划登录数量（未登录计划则按入荷数量）。

    校验不通过时返回 jsonify 响应，通过返回 None。
    """
    if cfg is not PAGES.get('production'):
        return None
    lot_no = (data.get('lot_no') or '').strip()
    try:
        qty = int(data.get('inbound_qty') or 0)
    except (TypeError, ValueError):
        qty = 0

    limit, source = _prod_inbound_qty_limit(lot_no, exclude_id)
    if limit is None:
        # LOT 未登录计划、也无入荷记录：无依据，不限制
        return None
    if qty > limit:
        return jsonify({'success': False, 'message': f'入库数量（{qty}）超出「{source}」可入库上限（{limit}）。'}), 400
    return None


def _quality_qty_limit(lot_no, exclude_id=None):
    """品质检查(包装)数量上限。

    口径：取「计划登录」该 LOT 的计划数量作为上限；同一 LOT 可能存在多笔
    计划记录时按合计处理。未登录计划的 LOT 不做限制。

    exclude_id：编辑场景下排除当前记录自身已占用的检查数量，避免误判超量。
    返回 (上限, 上限来源说明) 或 (None, '') 表示查不到、不限制。
    """
    from app_factory import db

    lot_no = (lot_no or '').strip()
    if not lot_no:
        return None, ''

    # 计划登录数量（同 LOT 多笔计划按合计，与结算口径一致）
    from models.plan_login import PlanLogin
    plan_total = db.session.query(
        db.func.coalesce(db.func.sum(PlanLogin.plan_qty), 0)
    ).filter(PlanLogin.lot_no == lot_no).scalar()
    limit = int(plan_total or 0)
    if limit <= 0:
        return None, ''

    # 该 LOT 已登记的包装数量（编辑时排除当前记录），防止多笔累计超量
    from models.quality_check import QualityCheck
    used_q = db.session.query(
        db.func.coalesce(db.func.sum(QualityCheck.check_qty), 0)
    ).filter(QualityCheck.lot_no == lot_no)
    if exclude_id is not None:
        used_q = used_q.filter(QualityCheck.id != exclude_id)
    used = int(used_q.scalar() or 0)
    return max(limit - used, 0), '计划登录数量'


def _check_quality_qty(record, cfg, data, exclude_id=None):
    """品质检查(包装)保存前校验：检查(包装)数量不得超过计划登录数量。

    校验不通过时返回 jsonify 响应，通过返回 None。
    """
    if cfg is not PAGES.get('quality'):
        return None
    lot_no = (data.get('lot_no') or '').strip()
    try:
        qty = int(float(data.get('check_qty') or 0))
    except (TypeError, ValueError):
        qty = 0

    limit, source = _quality_qty_limit(lot_no, exclude_id)
    if limit is None:
        # 该 LOT 未登录计划：无依据，不限制
        return None
    if qty > limit:
        return jsonify({'success': False,
                        'message': f'包装数量（{qty}）超出「{source}」可包装上限（{limit}）。'}), 400
    return None


def _lot_has_production(lot_no):
    """该 LOT 是否存在生产入库记录（判断计划登录是否已有下游工序）。"""
    from app_factory import db
    from models.production_inbound import ProductionInbound
    lot_no = (lot_no or '').strip()
    if not lot_no:
        return False
    return db.session.query(ProductionInbound.id).filter(
        ProductionInbound.lot_no == lot_no).first() is not None


def _lot_has_quality(lot_no):
    """该 LOT 是否存在品质检查(包装)记录（判断上游工序是否已有下游）。"""
    from app_factory import db
    from models.quality_check import QualityCheck
    lot_no = (lot_no or '').strip()
    if not lot_no:
        return False
    return db.session.query(QualityCheck.id).filter(
        QualityCheck.lot_no == lot_no).first() is not None


def _sync_plan_status(lot_no):
    """按包装进度同步计划登录状态：包装数量合计 ≥ 计划数量合计则置为 done，否则 pending。

    同一 LOT 可能有多笔计划记录，状态统一联动（与结算口径一致，按 LOT 汇总）。
    仅在能查到计划数量（>0）时才变更，避免误置。
    """
    from app_factory import db
    from models.plan_login import PlanLogin
    from models.quality_check import QualityCheck

    lot_no = (lot_no or '').strip()
    if not lot_no:
        return

    plan_total = db.session.query(
        db.func.coalesce(db.func.sum(PlanLogin.plan_qty), 0)
    ).filter(PlanLogin.lot_no == lot_no).scalar()
    plan_total = int(plan_total or 0)
    if plan_total <= 0:
        return  # 未登录计划或无计划数量：不联动状态

    pack_total = db.session.query(
        db.func.coalesce(db.func.sum(QualityCheck.check_qty), 0)
    ).filter(QualityCheck.lot_no == lot_no).scalar()
    new_status = 'done' if int(pack_total or 0) >= plan_total else 'pending'

    # 批量更新该 LOT 下所有计划记录的状态（仅在需要变更时写库）
    db.session.query(PlanLogin).filter(
        PlanLogin.lot_no == lot_no, PlanLogin.status != new_status
    ).update({'status': new_status}, synchronize_session=False)


def _lot_unit_price(pno, on_date):
    """按品番取生效日期 <= on_date 的最新单价；无生效日期约束时取最新一条。

    返回 (单价float, 生效日期str, 币种) 或 (None, '', '')。
    """
    if not pno:
        return None, '', ''
    from models.unit_price import UnitPrice
    q = UnitPrice.query.filter_by(pno=pno)
    if on_date:
        # 只取不晚于入库日期的单价，保证历史单价可复现
        q = q.filter(UnitPrice.effective_date <= on_date)
    row = q.order_by(UnitPrice.effective_date.desc(), UnitPrice.id.desc()).first()
    if not row:
        return None, '', ''
    return (float(row.unit_price) if row.unit_price is not None else 0,
            row.effective_date.strftime('%Y-%m-%d') if row.effective_date else '',
            row.currency or '')


def _default_operator(user):
    """登录账号默认作业者：优先姓名 full_name，退化为 username。"""
    if user is None:
        return ''
    return (getattr(user, 'full_name', '') or getattr(user, 'username', '') or '').strip()


def _match_price_multiplier(operator, on_date, pno=None):
    """按 作业者+日期(+品番) 匹配单价倍数规则。

    匹配规则：
      1) 日期落在 [start_date, end_date] 区间内；
      2) 作业者匹配：优先精确匹配该作业者，其次通用规则(operator 为空)；
      3) 品番：优先匹配指定品番，其次通用规则(pno 为空)。
    优先级：作业者精确 > 通用；同优先级下品番精确 > 通用；再按开始日期最新、id 最大。
    返回 (倍数float, 规则dict) 或 (None, None) 表示无匹配。
    """
    if not on_date:
        return None, None
    from models.price_multiplier import PriceMultiplier
    q = PriceMultiplier.query.filter(
        PriceMultiplier.start_date <= on_date,
        PriceMultiplier.end_date >= on_date,
    )
    # 作业者候选：指定作业者 + 通用(空)，用 OR 兼顾
    op_cond = [PriceMultiplier.operator == operator, PriceMultiplier.operator.is_(None),
               PriceMultiplier.operator == '']
    if operator:
        q = q.filter(db_or(*op_cond))
    # 品番候选同理
    if pno:
        q = q.filter(db_or(PriceMultiplier.pno == pno, PriceMultiplier.pno.is_(None),
                           PriceMultiplier.pno == ''))

    rows = q.all()
    if not rows:
        return None, None

    def _rank(r):
        # 作业者精确匹配记 0（优先），通用记 1；品番精确记 0，通用记 1
        op_score = 0 if (operator and r.operator == operator) else 1
        pno_score = 0 if (pno and r.pno == pno) else 1
        return (op_score, pno_score, -r.start_date.toordinal(), -r.id)

    row = sorted(rows, key=_rank)[0]
    return (float(row.multiplier) if row.multiplier is not None else 1), row.to_dict()


def _make_lot_lookup_view(perm_code, with_operator=False, with_price=False,
                          with_price_check=False):
    def view():
        user, err = _require_perm(perm_code)
        if err:
            return err

        lot_no = (request.args.get('lot_no') or '').strip()
        if not lot_no:
            return jsonify({'success': False, 'message': 'LOT NO 不能为空'}), 400

        data = _query_lot_info(lot_no)
        if not data:
            return jsonify({'success': False, 'found': False,
                            'message': f'未找到 LOT「{lot_no}」的登录资料'})

        # 生产入库专用：附加默认作业者（按账号绑定）与按品番拉取的单价
        if with_operator:
            data['operator'] = _default_operator(user)
        # 计划登录专用：仅判断该品番是否已登录单价，未登录时前端提示去登录
        if with_price_check:
            on_date = _to_date(request.args.get('date')) or datetime.now().date()
            price, eff, currency = _lot_unit_price(data.get('pno'), on_date)
            data['price_registered'] = price is not None
            data['price_effective_date'] = eff
            data['price_currency'] = currency
        if with_price:
            on_date = _to_date(request.args.get('date')) or datetime.now().date()
            # 无「查看单价」权限时不回传真实单价（倍数规则仍返回，但单价置空）
            can_unmask = _has_perm(user, PERM_PRODUCTION_UNMASK)
            price, eff, currency = _lot_unit_price(data.get('pno'), on_date)
            data['unit_price'] = (price if price is not None else '') if can_unmask else ''
            data['price_effective_date'] = eff if can_unmask else ''
            data['currency'] = currency if can_unmask else ''
            data['price_masked'] = not can_unmask
            # 按 作业者+入库日期(+品番) 匹配单价倍数规则，供前端自动带出倍数
            op = (request.args.get('operator') or data.get('operator') or '').strip()
            mult, rule = _match_price_multiplier(op, on_date, data.get('pno'))
            data['multiplier'] = mult if mult is not None else 1
            data['multiplier_matched'] = mult is not None
            data['multiplier_rule'] = rule
            # 可入库上限：计划登录数量优先，未登录计划则入荷数量（供前端提交前提示）
            limit, source = _prod_inbound_qty_limit(lot_no)
            data['qty_limit'] = limit
            data['qty_limit_source'] = source

        return jsonify({'success': True, 'found': True, 'data': data})

    return view


def plan_lot_lookup():
    """GET /mes/api/plan/lot_lookup?lot_no=xxx 计划登录用 LOT 反查。

    额外返回该品番是否已登录单价（price_registered），未登录时前端提示补登录。
    """
    return _make_lot_lookup_view('can_mes_plan', with_price_check=True)()


def production_lot_lookup():
    """GET /mes/api/production/lot_lookup?lot_no=xxx&date=YYYY-MM-DD 生产入库用 LOT 反查。

    额外返回默认作业者（按登录账号）与按品番匹配的最新生效单价，供前端自动带出。
    """
    return _make_lot_lookup_view('can_mes_production', with_operator=True, with_price=True)()


def quality_lot_lookup():
    """GET /mes/api/quality/lot_lookup?lot_no=xxx 品质检查用 LOT 反查。

    自动带出：品番/车种（来自 LOT 登录资料）、计划数量（plan_login）、
    已生产入库数量（production_inbound 汇总），以及可包装上限
    （计划数量 − 已登记包装数量），供前端提示与提交前校验。
    """
    user, err = _require_perm('can_mes_quality')
    if err:
        return err

    lot_no = (request.args.get('lot_no') or '').strip()
    if not lot_no:
        return jsonify({'success': False, 'message': 'LOT NO 不能为空'}), 400

    from app_factory import db
    from models.plan_login import PlanLogin
    from models.production_inbound import ProductionInbound

    # LOT 登录资料（可能不存在，此时以计划登录/入库数据兜底品番）
    data = _query_lot_info(lot_no) or {'lot_no': lot_no, 'pno': '', 'vehicle_type': '', 'item_name': '', 'qty': 0}

    plan = PlanLogin.query.filter_by(lot_no=lot_no).first()
    if plan:
        data['pno'] = data.get('pno') or (plan.pno or '')
        data['plan_qty'] = int(plan.plan_qty or 0)
        data['customer'] = plan.customer or ''
    else:
        data['plan_qty'] = 0
        data['customer'] = ''

    inbound = db.session.query(
        db.func.coalesce(db.func.sum(ProductionInbound.inbound_qty), 0)
    ).filter(ProductionInbound.lot_no == lot_no).scalar()
    data['inbound_qty'] = int(inbound or 0)

    # 可包装上限 = 计划数量 − 已登记包装数量（已含其他批次占用）
    limit, source = _quality_qty_limit(lot_no)
    data['pack_qty_limit'] = limit
    data['pack_qty_limit_source'] = source

    if not data.get('pno') and data['plan_qty'] == 0 and data['inbound_qty'] == 0:
        return jsonify({'success': False, 'found': False,
                        'message': f'未找到 LOT「{lot_no}」的计划/入库记录'})

    return jsonify({'success': True, 'found': True, 'data': data})


def production_operators():
    """GET /mes/api/production/operators 作业者候选列表。

    仅返回被标记为「作业者」(is_operator=True) 的账号，避免把全部账号列出；
    附带其部门(作为默认班组)，供前端「作业者」下拉选择后自动带出班组。
    """
    user, err = _require_perm('can_mes_production')
    if err:
        return err

    from models.user_info import UserInfo
    rows = (UserInfo.query
            .filter(UserInfo.is_operator.is_(True), UserInfo.is_active.is_(True))
            .order_by(UserInfo.full_name.asc()).all())
    seen, operators = set(), []
    for u in rows:
        name = (getattr(u, 'full_name', '') or getattr(u, 'username', '') or '').strip()
        if not name or name in seen:
            continue
        seen.add(name)
        operators.append({'name': name, 'team': (u.department or '').strip()})
    return jsonify({'success': True, 'items': operators})


# --------------------------------------------------------------------------
# 客户主数据接口：下拉列表
# --------------------------------------------------------------------------
def customer_list():
    """GET /mes/api/customers 供计划登录/单价登陆客户下拉使用。"""
    user, err = _require_perm('can_mes_plan')
    if err:
        return err

    from models.customer import Customer
    rows = (Customer.query.filter_by(is_active=True)
            .order_by(Customer.sort_no.asc(), Customer.id.asc()).all())
    return jsonify({
        'success': True,
        'items': [{'cust_code': r.cust_code, 'cust_name': r.cust_name} for r in rows],
    })


# --------------------------------------------------------------------------
# 客户主数据维护：列表 / 新增 / 编辑 / 删除（复用页面访问权限）
# --------------------------------------------------------------------------
def _customer_require(extra_perm=None):
    """客户维护权限：页面访问权限即可，新增/编辑/删除沿用同一权限。"""
    return _require_perm(extra_perm or 'can_mes_plan')


def customer_admin_list():
    """GET /mes/api/customers/admin 客户维护列表（含停用）。"""
    user, err = _customer_require()
    if err:
        return err

    from models.customer import Customer
    rows = Customer.query.order_by(Customer.sort_no.asc(), Customer.id.asc()).all()
    return jsonify({'success': True, 'items': [r.to_dict() for r in rows]})


def customer_admin_save():
    """POST /mes/api/customers/admin 新增或更新客户（带 id 则更新）。"""
    user, err = _customer_require()
    if err:
        return err

    from app_factory import db
    from models.customer import Customer

    data = request.get_json(silent=True) or {}
    cust_code = (data.get('cust_code') or '').strip()
    cust_name = (data.get('cust_name') or '').strip()
    if not cust_code or not cust_name:
        return jsonify({'success': False, 'message': '客户代码与名称均为必填'}), 400

    rec_id = data.get('id')
    if rec_id:
        record = db.session.get(Customer, int(rec_id))
        if not record:
            return jsonify({'success': False, 'message': '客户不存在'}), 404
    else:
        record = Customer()
        record.created_by = user.id
        # 操作人姓名快照：账号删除后记录仍可显示文本
        record.created_by_name = getattr(user, 'username', None) or getattr(user, 'full_name', None)
        if Customer.query.filter_by(cust_code=cust_code).first():
            return jsonify({'success': False, 'message': f'客户代码「{cust_code}」已存在'}), 400

    dupe = Customer.query.filter(Customer.cust_code == cust_code,
                                 Customer.id != (record.id or 0)).first()
    if dupe:
        return jsonify({'success': False, 'message': f'客户代码「{cust_code}」已存在'}), 400

    record.cust_code = cust_code
    record.cust_name = cust_name
    try:
        record.sort_no = int(data.get('sort_no') or 0)
    except (TypeError, ValueError):
        record.sort_no = 0
    record.is_active = bool(data.get('is_active', True))
    record.remark = (data.get('remark') or '').strip() or None

    db.session.add(record)
    db.session.commit()
    return jsonify({'success': True, 'item': record.to_dict()})


def customer_admin_delete(record_id):
    """DELETE /mes/api/customers/admin/<id> 删除客户。"""
    user, err = _customer_require()
    if err:
        return err

    from app_factory import db
    from models.customer import Customer

    record = db.session.get(Customer, record_id)
    if not record:
        return jsonify({'success': False, 'message': '客户不存在'}), 404
    db.session.delete(record)
    db.session.commit()
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# 单价倍数规则维护：列表 / 新增 / 编辑 / 删除（复用单价登陆页面权限）
# --------------------------------------------------------------------------
def price_multiplier_list():
    """GET /mes/api/price/multipliers 倍数规则列表（支持区间/作业者筛选）。"""
    user, err = _require_perm('can_mes_price')
    if err:
        return err

    from models.price_multiplier import PriceMultiplier
    q = PriceMultiplier.query
    op = (request.args.get('operator') or '').strip()
    if op:
        q = q.filter(PriceMultiplier.operator == op)
    rows = q.order_by(PriceMultiplier.start_date.desc(), PriceMultiplier.id.desc()).all()
    return jsonify({'success': True, 'items': [r.to_dict() for r in rows]})


def price_multiplier_save():
    """POST /mes/api/price/multipliers 新增或更新倍数规则（带 id 则更新）。"""
    user, err = _require_perm('can_mes_price')
    if err:
        return err

    from app_factory import db
    from models.price_multiplier import PriceMultiplier

    data = request.get_json(silent=True) or {}
    start_date = _to_date(data.get('start_date'))
    end_date = _to_date(data.get('end_date'))
    if not start_date or not end_date:
        return jsonify({'success': False, 'message': '生效开始/结束日期为必填'}), 400
    if end_date < start_date:
        return jsonify({'success': False, 'message': '结束日期不能早于开始日期'}), 400
    try:
        multiplier = float(data.get('multiplier') or 0)
    except (TypeError, ValueError):
        multiplier = 0
    if multiplier <= 0:
        return jsonify({'success': False, 'message': '倍数必须大于 0'}), 400

    rec_id = data.get('id')
    if rec_id:
        record = db.session.get(PriceMultiplier, int(rec_id))
        if not record:
            return jsonify({'success': False, 'message': '倍数规则不存在'}), 404
    else:
        record = PriceMultiplier()
        record.created_by = user.id
        record.created_by_name = getattr(user, 'username', None) or getattr(user, 'full_name', None)

    record.start_date = start_date
    record.end_date = end_date
    record.operator = (data.get('operator') or '').strip() or None
    record.multiplier = multiplier
    record.pno = (data.get('pno') or '').strip() or None
    record.remark = (data.get('remark') or '').strip() or None

    if not rec_id:
        db.session.add(record)
    db.session.commit()
    return jsonify({'success': True, 'item': record.to_dict()})


def price_multiplier_delete(record_id):
    """DELETE /mes/api/price/multipliers/<id> 删除倍数规则。"""
    user, err = _require_perm('can_mes_price')
    if err:
        return err

    from app_factory import db
    from models.price_multiplier import PriceMultiplier

    record = db.session.get(PriceMultiplier, record_id)
    if not record:
        return jsonify({'success': False, 'message': '倍数规则不存在'}), 404
    db.session.delete(record)
    db.session.commit()
    return jsonify({'success': True})


# --------------------------------------------------------------------------
# 注册 4 个页面的路由
# --------------------------------------------------------------------------
for _key, _cfg in PAGES.items():
    mes_mod_bp.add_url_rule(
        _cfg['route'], endpoint=f'{_key}_page', view_func=_make_page_view(_key))
    mes_mod_bp.add_url_rule(
        f'/api/{_key}', endpoint=f'{_key}_list', view_func=_make_list_view(_key),
        methods=['GET'])
    mes_mod_bp.add_url_rule(
        f'/api/{_key}', endpoint=f'{_key}_create', view_func=_make_create_view(_key),
        methods=['POST'])
    mes_mod_bp.add_url_rule(
        f'/api/{_key}/<int:record_id>', endpoint=f'{_key}_update',
        view_func=_make_update_view(_key), methods=['PUT'])
    mes_mod_bp.add_url_rule(
        f'/api/{_key}/<int:record_id>', endpoint=f'{_key}_delete',
        view_func=_make_delete_view(_key), methods=['DELETE'])
    mes_mod_bp.add_url_rule(
        f'/api/{_key}/export', endpoint=f'{_key}_export',
        view_func=_make_export_view(_key), methods=['GET'])
    # Excel 导入（仅配置了 upload_map 的页面注册）
    if _cfg.get('upload_map'):
        mes_mod_bp.add_url_rule(
            f'/api/{_key}/upload', endpoint=f'{_key}_upload',
            view_func=_make_upload_view(_key), methods=['POST'])
        # 模板下载（与导入共用 upload_map，保证模板列头与解析规则一致）
        mes_mod_bp.add_url_rule(
            f'/api/{_key}/template', endpoint=f'{_key}_template',
            view_func=_make_template_view(_key), methods=['GET'])

# 计划登录专属：LOT 反查 + 客户主数据
mes_mod_bp.add_url_rule(
    '/api/plan/lot_lookup', endpoint='plan_lot_lookup',
    view_func=plan_lot_lookup, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/production/lot_lookup', endpoint='production_lot_lookup',
    view_func=production_lot_lookup, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/quality/lot_lookup', endpoint='quality_lot_lookup',
    view_func=quality_lot_lookup, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/production/operators', endpoint='production_operators',
    view_func=production_operators, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/customers', endpoint='customer_list',
    view_func=customer_list, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/customers/admin', endpoint='customer_admin_list',
    view_func=customer_admin_list, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/customers/admin', endpoint='customer_admin_save',
    view_func=customer_admin_save, methods=['POST'])
mes_mod_bp.add_url_rule(
    '/api/customers/admin/<int:record_id>', endpoint='customer_admin_delete',
    view_func=customer_admin_delete, methods=['DELETE'])

# 单价倍数规则（单价登陆页维护）
mes_mod_bp.add_url_rule(
    '/api/price/multipliers', endpoint='price_multiplier_list',
    view_func=price_multiplier_list, methods=['GET'])
mes_mod_bp.add_url_rule(
    '/api/price/multipliers', endpoint='price_multiplier_save',
    view_func=price_multiplier_save, methods=['POST'])
mes_mod_bp.add_url_rule(
    '/api/price/multipliers/<int:record_id>', endpoint='price_multiplier_delete',
    view_func=price_multiplier_delete, methods=['DELETE'])
