# -*- coding: utf-8 -*-
"""
用户与权限管理 - 扩展 API 蓝图

与既有 routes.py 的 ``/user_management`` 页面解耦，单独提供：
    1. 用户列表查询      GET  /api/user-mgmt/list          支持分页 / 排序 / 多条件筛选
    2. 元数据            GET  /api/user-mgmt/meta          部门 / 权限组 / 权限目录（供筛选与批量操作）
    3. Excel 导入模板     GET  /api/user-mgmt/import-template
    4. Excel 批量导入     POST /api/user-mgmt/import         数据校验 + 逐行结果反馈
    5. 批量分配权限组     POST /api/user-mgmt/batch-groups
    6. 批量设置权限覆盖   POST /api/user-mgmt/batch-permissions
    7. 单个用户信息更新   POST /api/user-mgmt/<id>/profile   姓名 / 部门

权限约定（与既有 user_management 页面保持一致）：
    - 页面访问：can_system_management
    - 新增用户：can_user_mgmt_add_user
    - 管理权限：can_user_mgmt_manage_permissions

CSRF：本蓝图通过 app_factory 整体豁免（JSON + fetch 调用策略，与 rbac_bp / mes_bp 一致）。
"""
import io
import re

from flask import Blueprint, jsonify, request, session, send_file
from sqlalchemy import or_, func

from db import db
from models.user_info import UserInfo
from models.rbac import UserGroup, UserPermissionOverride
from utils.permission_utils import check_permission
from utils import rbac_service

user_mgmt_bp = Blueprint('user_mgmt', __name__, url_prefix='/api/user-mgmt')

# 用户名规则：3-32 位，字母/数字/下划线/点/连字符
_USERNAME_RE = re.compile(r'^[A-Za-z0-9_.\-]{3,32}$')
# 密码最短长度
_MIN_PASSWORD_LEN = 6

# 允许排序的字段白名单（防止注入）
_SORTABLE = {
    'id': UserInfo.id,
    'username': UserInfo.username,
    'full_name': UserInfo.full_name,
    'department': UserInfo.department,
    'is_admin': UserInfo.is_admin,
    'is_active': UserInfo.is_active,
    'is_operator': UserInfo.is_operator,
    'create_time': UserInfo.create_time,
}


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------
def _guard(permission='can_system_management'):
    """统一访问校验：未登录 401 / 无权限 403 / 通过返回 None"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session['user_id'], permission):
        return jsonify({'success': False, 'error': '权限不足'}), 403
    return None


def _log(operation_type, desc, target_type=None, target_id=None, target_name=None):
    """写入操作日志。operation_type 必填（operation_logs.operation_type NOT NULL）。"""
    try:
        from utils.operation_logger import log_action
        from models.operation_log import OperationType
        log_action(operation_type or OperationType.ADD_USER, desc,
                   target_type, target_id, target_name)
    except Exception:
        pass


def _user_to_dict(u, effective=None, group_names=None):
    return {
        'id': u.id,
        'username': u.username,
        'full_name': u.full_name or '',
        'department': u.department or '',
        'is_admin': bool(u.is_admin),
        'is_active': bool(u.is_active),
        'is_operator': bool(u.is_operator),
        'create_time': u.create_time.strftime('%Y-%m-%d %H:%M:%S') if u.create_time else '',
        'update_time': u.update_time.strftime('%Y-%m-%d %H:%M:%S') if u.update_time else '',
        'group_names': group_names or [],
        'permission_count': len(effective) if effective else 0,
        'is_self': u.id == session.get('user_id'),
    }


def _collect_group_names(user_ids):
    """批量获取用户权限组名：{user_id: [group_name, ...]}"""
    if not user_ids:
        return {}
    from models.rbac import PermissionGroup
    rows = (db.session.query(UserGroup.user_id, PermissionGroup.name)
            .join(PermissionGroup, PermissionGroup.id == UserGroup.group_id)
            .filter(UserGroup.user_id.in_(list(user_ids)))
            .all())
    result = {}
    for uid, gname in rows:
        result.setdefault(uid, []).append(gname)
    return result


def _collect_permission_counts(user_ids):
    """批量获取有效权限数量：{user_id: count}"""
    if not user_ids:
        return {}
    return {uid: len(p) for uid, p in
            ((u, rbac_service.compute_effective_permissions(u, use_cache=True))
             for u in user_ids)}


# ---------------------------------------------------------------------------
# 1) 用户列表：分页 / 排序 / 多条件筛选
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/list', methods=['GET'])
def api_user_list():
    err = _guard()
    if err:
        return err

    args = request.args
    try:
        page = max(1, int(args.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        size = int(args.get('size', 20))
    except (TypeError, ValueError):
        size = 20
    size = min(max(size, 5), 200)

    query = UserInfo.query

    # 多条件筛选：用户名 / 姓名 / 部门 关键字
    keyword = (args.get('keyword') or '').strip()
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(or_(
            UserInfo.username.ilike(like),
            UserInfo.full_name.ilike(like),
            UserInfo.department.ilike(like),
        ))

    department = (args.get('department') or '').strip()
    if department:
        query = query.filter(UserInfo.department == department)

    group_filter = (args.get('group') or '').strip()
    if group_filter:
        try:
            gid = int(group_filter)
            sub = db.session.query(UserGroup.user_id).filter(UserGroup.group_id == gid)
            query = query.filter(UserInfo.id.in_(sub))
        except ValueError:
            pass

    status = (args.get('status') or '').strip()
    if status in ('active', 'disabled'):
        query = query.filter(UserInfo.is_active == (status == 'active'))

    is_admin = (args.get('is_admin') or '').strip()
    if is_admin in ('1', '0'):
        query = query.filter(UserInfo.is_admin == (is_admin == '1'))

    # 是否作业者筛选
    is_operator = (args.get('is_operator') or '').strip()
    if is_operator in ('1', '0'):
        query = query.filter(UserInfo.is_operator == (is_operator == '1'))

    total = query.count()

    # 排序
    sort_by = args.get('sort_by', 'id')
    sort_dir = args.get('sort_dir', 'asc').lower()
    col = _SORTABLE.get(sort_by, UserInfo.id)
    query = query.order_by(col.desc() if sort_dir == 'desc' else col.asc())

    items = query.offset((page - 1) * size).limit(size).all()
    user_ids = [u.id for u in items]
    group_map = _collect_group_names(user_ids)
    perm_map = _collect_permission_counts(user_ids)

    data = [_user_to_dict(u, group_names=group_map.get(u.id, [])) for u in items]
    for d in data:
        d['permission_count'] = perm_map.get(d['id'], 0)

    return jsonify({
        'success': True,
        'data': data,
        'total': total,
        'page': page,
        'size': size,
        'pages': (total + size - 1) // size,
    })


# ---------------------------------------------------------------------------
# 2) 元数据：部门 / 权限组 / 权限目录
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/meta', methods=['GET'])
def api_meta():
    err = _guard()
    if err:
        return err

    departments = [d for (d,) in
                   db.session.query(UserInfo.department)
                   .filter(UserInfo.department.isnot(None), UserInfo.department != '')
                   .distinct().order_by(UserInfo.department).all()]

    groups = [{'id': g.id, 'name': g.name, 'code': g.code}
              for g in rbac_service.list_groups()]

    return jsonify({
        'success': True,
        'departments': departments,
        'groups': groups,
        'permission_tree': rbac_service.build_permission_tree(),
    })


# ---------------------------------------------------------------------------
# 3) Excel 导入模板下载
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/import-template', methods=['GET'])
def api_import_template():
    err = _guard('can_user_mgmt_add_user')
    if err:
        return err

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
    except ImportError:
        return jsonify({'success': False, 'error': '服务器缺少 openpyxl 依赖'}), 500

    wb = Workbook()
    ws = wb.active
    ws.title = '用户导入模板'

    headers = ['用户名*', '密码*', '姓名', '部门', '权限组', '是否管理员']
    ws.append(headers)

    header_fill = PatternFill('solid', fgColor='4A6FA5')
    header_font = Font(color='FFFFFF', bold=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal='center', vertical='center')

    # 示例行（提示用，导入时会被忽略或按实际解析）
    ws.append(['zhangsan', 'abc12345', '张三', '生产部', '只读组', '否'])
    ws.append(['lisi', 'abc12345', '李四', '品质部', '只读组', '否'])

    widths = [18, 16, 14, 16, 18, 12]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    # 说明工作表
    tips = wb.create_sheet('填写说明')
    notes = [
        ['列名', '必填', '说明'],
        ['用户名', '是', '3-32位，字母/数字/下划线/点/连字符，系统内唯一'],
        ['密码', '是', f'长度不少于{_MIN_PASSWORD_LEN}位'],
        ['姓名', '否', '用户真实姓名'],
        ['部门', '否', '所属部门，自由文本'],
        ['权限组', '否', '权限组名称（多个用英文逗号分隔），需与系统权限组一致'],
        ['是否管理员', '否', '填「是/Y/true/1」表示管理员，其他为普通用户'],
        ['', '', ''],
        ['注意', '', '示例行（张三/李四）请在导入前删除，或直接在下方追加数据'],
    ]
    for row in notes:
        tips.append(row)
    for i, w in enumerate([14, 8, 60], start=1):
        tips.column_dimensions[chr(64 + i)].width = w

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)
    return send_file(
        bio,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='用户导入模板.xlsx',
    )


# ---------------------------------------------------------------------------
# 4) Excel 批量导入
# ---------------------------------------------------------------------------
def _parse_bool(v):
    return str(v).strip().lower() in ('是', 'y', 'yes', 'true', '1', 'admin', '管理员')


@user_mgmt_bp.route('/import', methods=['POST'])
def api_import_users():
    err = _guard('can_user_mgmt_add_user')
    if err:
        return err

    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': '未选择文件'}), 400
    if not file.filename.lower().endswith(('.xlsx', '.xlsm')):
        return jsonify({'success': False, 'error': '仅支持 .xlsx 格式，请使用下载的模板'}), 400

    try:
        from openpyxl import load_workbook
        wb = load_workbook(file, data_only=True, read_only=True)
        ws = wb.active
    except Exception as e:
        return jsonify({'success': False, 'error': f'无法解析 Excel 文件: {e}'}), 400

    # 权限组名 -> id 映射
    group_map = {g.name: g.id for g in rbac_service.list_groups()}
    # 现有用户名集合
    existing = {u for (u,) in db.session.query(UserInfo.username).all()}

    operator_id = session.get('user_id')
    results = []
    seen_in_file = set()
    success_count = 0

    rows = ws.iter_rows(min_row=2, values_only=True)
    for idx, row in enumerate(rows, start=2):
        # 跳过完全空行
        if row is None or all(c is None or str(c).strip() == '' for c in row):
            continue

        def cell(i):
            return str(row[i]).strip() if i < len(row) and row[i] is not None else ''

        username = cell(0)
        password = cell(1)
        full_name = cell(2)
        department = cell(3)
        group_str = cell(4)
        is_admin_raw = cell(5)

        row_result = {'row': idx, 'username': username, 'ok': False, 'error': ''}

        # --- 校验 ---
        if not username:
            row_result['error'] = '用户名不能为空'
            results.append(row_result)
            continue
        if not _USERNAME_RE.match(username):
            row_result['error'] = '用户名格式不合法（3-32位字母/数字/_.-）'
            results.append(row_result)
            continue
        if username in existing:
            row_result['error'] = '用户名已存在'
            results.append(row_result)
            continue
        if username in seen_in_file:
            row_result['error'] = '文件内用户名重复'
            results.append(row_result)
            continue
        if not password:
            row_result['error'] = '密码不能为空'
            results.append(row_result)
            continue
        if len(password) < _MIN_PASSWORD_LEN:
            row_result['error'] = f'密码长度少于{_MIN_PASSWORD_LEN}位'
            results.append(row_result)
            continue

        # 权限组解析
        group_ids, invalid_groups = [], []
        if group_str:
            for gn in re.split(r'[,，;；]', group_str):
                gn = gn.strip()
                if not gn:
                    continue
                if gn in group_map:
                    group_ids.append(group_map[gn])
                else:
                    invalid_groups.append(gn)

        is_admin = _parse_bool(is_admin_raw)

        # --- 入库（逐行提交，保证单行失败不影响其他行）---
        try:
            from werkzeug.security import generate_password_hash
            new_user = UserInfo(
                username=username,
                password=generate_password_hash(password),
                full_name=full_name or None,
                department=department or None,
                is_admin=is_admin,
                created_by=operator_id,
                updated_by=operator_id,
            )
            db.session.add(new_user)
            db.session.flush()  # 获取 id

            # 默认权限（RBAC 用户覆盖：仅文件管理可见，与既有 add_user 行为一致）
            db.session.add(UserPermissionOverride(
                user_id=new_user.id,
                permission_code='can_file_management',
                effect='allow',
                updated_by=operator_id,
            ))

            # 权限组绑定
            for gid in group_ids:
                db.session.add(UserGroup(user_id=new_user.id, group_id=gid, created_by=operator_id))

            db.session.commit()
            existing.add(username)
            seen_in_file.add(username)
            success_count += 1
            row_result['ok'] = True
            if invalid_groups:
                row_result['error'] = f'导入成功，但权限组不存在已忽略: {",".join(invalid_groups)}'
            row_result['group_ids'] = group_ids
        except Exception as e:
            db.session.rollback()
            row_result['error'] = f'写入失败: {e}'

        results.append(row_result)

    failed = [r for r in results if not r['ok']]
    from models.operation_log import OperationType
    _log(OperationType.ADD_USER, f'Excel批量导入用户: 成功{success_count}条，失败{len(failed)}条')

    return jsonify({
        'success': True,
        'total': len(results),
        'success_count': success_count,
        'failed_count': len(failed),
        'results': results,
    })


# ---------------------------------------------------------------------------
# 5) 批量分配权限组
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/batch-groups', methods=['POST'])
def api_batch_groups():
    err = _guard('can_user_mgmt_manage_permissions')
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user_ids = data.get('user_ids') or []
    group_ids = data.get('group_ids') or []
    mode = data.get('mode', 'add')  # add=追加 / replace=覆盖

    if not user_ids:
        return jsonify({'success': False, 'error': '未选择用户'}), 400
    if mode not in ('add', 'replace'):
        return jsonify({'success': False, 'error': '模式不合法'}), 400

    valid_groups = {g.id for g in rbac_service.list_groups()}
    group_ids = [int(g) for g in group_ids]
    invalid = set(group_ids) - valid_groups
    if invalid:
        return jsonify({'success': False, 'error': f'无效权限组: {sorted(invalid)}'}), 400

    operator_id = session.get('user_id')
    changed = 0
    errors = []
    for uid in user_ids:
        try:
            uid = int(uid)
            user = db.session.get(UserInfo, uid)
            if not user:
                errors.append(f'用户ID {uid} 不存在')
                continue
            if user.is_admin:
                errors.append(f'{user.username} 为管理员，已跳过')
                continue
            current = set(rbac_service.get_user_group_ids(uid))
            target = set(group_ids) if mode == 'replace' else current | set(group_ids)
            ok, msg = rbac_service.set_user_groups(uid, target, operator_id=operator_id)
            if ok:
                changed += 1
            else:
                errors.append(f'{user.username}: {msg}')
        except Exception as e:
            errors.append(f'ID {uid}: {e}')

    from models.operation_log import OperationType
    _log(OperationType.SET_USER_GROUPS, f'批量分配权限组: 成功{changed}人，失败{len(errors)}人')
    return jsonify({
        'success': True,
        'changed': changed,
        'errors': errors,
    })


# ---------------------------------------------------------------------------
# 6) 批量设置权限覆盖
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/batch-permissions', methods=['POST'])
def api_batch_permissions():
    err = _guard('can_user_mgmt_manage_permissions')
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user_ids = data.get('user_ids') or []
    overrides = data.get('overrides') or {}  # {code: 'allow'|'deny'|'inherit'}
    mode = data.get('mode', 'merge')  # merge=合并 / replace=覆盖

    if not user_ids:
        return jsonify({'success': False, 'error': '未选择用户'}), 400
    if not isinstance(overrides, dict):
        return jsonify({'success': False, 'error': '权限数据格式不合法'}), 400

    operator_id = session.get('user_id')
    changed = 0
    errors = []
    for uid in user_ids:
        try:
            uid = int(uid)
            user = db.session.get(UserInfo, uid)
            if not user:
                errors.append(f'用户ID {uid} 不存在')
                continue
            if user.is_admin:
                errors.append(f'{user.username} 为管理员，已跳过')
                continue
            if mode == 'replace':
                final = dict(overrides)
            else:
                final = rbac_service.get_user_overrides(uid)
                for code, effect in overrides.items():
                    if effect == 'inherit':
                        final.pop(code, None)
                    else:
                        final[code] = effect
            ok, msg = rbac_service.set_user_overrides(uid, final, operator_id=operator_id)
            if ok:
                changed += 1
            else:
                errors.append(f'{user.username}: {msg}')
        except Exception as e:
            errors.append(f'ID {uid}: {e}')

    from models.operation_log import OperationType
    _log(OperationType.SET_USER_OVERRIDES, f'批量设置权限: 成功{changed}人，失败{len(errors)}人')
    return jsonify({
        'success': True,
        'changed': changed,
        'errors': errors,
    })


# ---------------------------------------------------------------------------
# 7) 单个用户信息更新（姓名 / 部门）
# ---------------------------------------------------------------------------
@user_mgmt_bp.route('/<int:user_id>/profile', methods=['POST'])
def api_update_profile(user_id):
    err = _guard('can_user_mgmt_add_user')
    if err:
        return err

    data = request.get_json(silent=True) or {}
    user = db.session.get(UserInfo, user_id)
    if not user:
        return jsonify({'success': False, 'error': '用户不存在'}), 404

    if 'full_name' in data:
        user.full_name = (data.get('full_name') or '').strip() or None
    if 'department' in data:
        user.department = (data.get('department') or '').strip() or None
    if 'is_operator' in data:
        user.is_operator = bool(data.get('is_operator'))
    user.updated_by = session.get('user_id')
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': f'保存失败: {e}'}), 500

    from models.operation_log import OperationType
    _log(OperationType.UPDATE_USER, f'更新用户信息: {user.username}')
    return jsonify({'success': True, 'user': _user_to_dict(user)})
