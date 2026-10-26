"""
RBAC 权限组与权限管理 API

提供权限组的增删改查、权限组权限配置、用户权限组绑定、用户权限覆盖接口。
页面路由统一挂载在 /rbac 前缀下，与既有 routes.py 解耦，便于维护。

权限约定：
    - 所有接口仅管理员或拥有 can_system_management 权限的用户可访问
    - 前端通过 GET /rbac/api/catalog 获取权限目录并渲染权限树
"""

from flask import Blueprint, jsonify, render_template, request, session

from app_factory import db
from utils.permission_utils import check_permission
from utils import rbac_service

rbac_bp = Blueprint('rbac', __name__, url_prefix='/rbac')


def _guard():
    """统一访问校验：未登录返回 401，无权限返回 403"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session['user_id'], 'can_system_management'):
        return jsonify({'success': False, 'error': '权限不足'}), 403
    return None


def _log_rbac(operation_type, operation_desc, target_type, target_id=None, target_name=None):
    """RBAC 操作埋点：失败不影响主流程"""
    try:
        from utils.operation_logger import log_action
        log_action(operation_type, operation_desc, target_type, target_id, target_name)
    except Exception:
        pass


# ============================================================
# 页面
# ============================================================
@rbac_bp.route('/roles', methods=['GET'])
def role_management_page():
    """权限组与权限管理页面"""
    from flask import redirect, url_for
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session['user_id'], 'can_system_management'):
        return '<h3>403 无权访问</h3><p>您没有权限组管理页面的访问权限。</p>', 403
    return render_template('role_management.html')


# ============================================================
# 权限目录
# ============================================================
@rbac_bp.route('/api/catalog', methods=['GET'])
def api_catalog():
    """返回按模块聚合的权限目录，供前端渲染权限树"""
    err = _guard()
    if err:
        return err
    return jsonify({'success': True, 'tree': rbac_service.build_permission_tree()})


# ============================================================
# 权限组管理
# ============================================================
@rbac_bp.route('/api/groups', methods=['GET'])
def api_list_groups():
    err = _guard()
    if err:
        return err
    groups = rbac_service.list_groups()
    return jsonify({'success': True, 'groups': [g.to_dict() for g in groups]})


@rbac_bp.route('/api/groups', methods=['POST'])
def api_create_group():
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg, group = rbac_service.create_group(
        name=(data.get('name') or '').strip(),
        code=(data.get('code') or '').strip(),
        description=data.get('description'),
        sort_order=int(data.get('sort_order') or 0),
    )
    if ok:
        from models.operation_log import OperationType, TargetType
        _log_rbac(OperationType.ADD_GROUP,
                  f"新增权限组: {(data.get('name') or '').strip()}",
                  TargetType.GROUP, group.id if group else None,
                  (data.get('name') or '').strip())
    return jsonify({'success': ok, 'message': msg,
                    'group': group.to_dict() if group else None}), (200 if ok else 400)


@rbac_bp.route('/api/groups/<int:group_id>', methods=['PUT'])
def api_update_group(group_id):
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg = rbac_service.update_group(
        group_id,
        name=data.get('name'),
        description=data.get('description'),
        sort_order=data.get('sort_order'),
        is_active=data.get('is_active'),
    )
    if ok:
        from models.operation_log import OperationType, TargetType
        _log_rbac(OperationType.UPDATE_GROUP,
                  f"更新权限组: {(data.get('name') or group_id)}",
                  TargetType.GROUP, group_id, str(data.get('name') or group_id))
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


@rbac_bp.route('/api/groups/<int:group_id>', methods=['DELETE'])
def api_delete_group(group_id):
    err = _guard()
    if err:
        return err
    ok, msg = rbac_service.delete_group(group_id)
    if ok:
        from models.operation_log import OperationType, TargetType
        _log_rbac(OperationType.DELETE_GROUP, f"删除权限组: ID={group_id}",
                  TargetType.GROUP, group_id)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


# ============================================================
# 权限组权限
# ============================================================
@rbac_bp.route('/api/groups/<int:group_id>/permissions', methods=['GET'])
def api_get_group_permissions(group_id):
    err = _guard()
    if err:
        return err
    return jsonify({'success': True,
                    'data': rbac_service.get_group_permission_map(group_id)})


@rbac_bp.route('/api/groups/<int:group_id>/permissions', methods=['POST'])
def api_set_group_permissions(group_id):
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg = rbac_service.set_group_permissions(
        group_id,
        allows=data.get('allows') or [],
        denies=data.get('denies') or [],
    )
    if ok:
        from models.operation_log import OperationType, TargetType
        allows = data.get('allows') or []
        denies = data.get('denies') or []
        _log_rbac(OperationType.SET_GROUP_PERMISSIONS,
                  f"设置权限组权限: 权限组ID={group_id}（允许{len(allows)}项/拒绝{len(denies)}项）",
                  TargetType.GROUP, group_id, str(group_id))
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


# ============================================================
# 权限组成员
# ============================================================
@rbac_bp.route('/api/groups/<int:group_id>/members', methods=['GET'])
def api_get_group_members(group_id):
    err = _guard()
    if err:
        return err
    return jsonify({'success': True,
                    'user_ids': rbac_service.get_group_member_ids(group_id)})


@rbac_bp.route('/api/groups/<int:group_id>/members', methods=['POST'])
def api_set_group_members(group_id):
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg = rbac_service.set_group_members(
        group_id, data.get('user_ids') or [], operator_id=session['user_id'])
    if ok:
        from models.operation_log import OperationType, TargetType
        _log_rbac(OperationType.SET_USER_GROUPS,
                  f"设置权限组成员: 权限组ID={group_id}（{len(data.get('user_ids') or [])}个成员）",
                  TargetType.GROUP, group_id, str(group_id))
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


# ============================================================
# 用户权限组与覆盖
# ============================================================
@rbac_bp.route('/api/users/<int:user_id>/groups', methods=['GET'])
def api_get_user_groups(user_id):
    err = _guard()
    if err:
        return err
    return jsonify({'success': True,
                    'group_ids': rbac_service.get_user_group_ids(user_id)})


@rbac_bp.route('/api/users/<int:user_id>/groups', methods=['POST'])
def api_set_user_groups(user_id):
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg = rbac_service.set_user_groups(
        user_id, data.get('group_ids') or [], operator_id=session['user_id'])
    if ok:
        from models.operation_log import OperationType, TargetType
        from models.user_info import UserInfo
        u = db.session.get(UserInfo, user_id)
        uname = u.username if u else str(user_id)
        _log_rbac(OperationType.SET_USER_GROUPS,
                  f"设置用户权限组: {uname}（{len(data.get('group_ids') or [])}个权限组）",
                  TargetType.USER, user_id, uname)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


@rbac_bp.route('/api/users/<int:user_id>/overrides', methods=['GET'])
def api_get_user_overrides(user_id):
    err = _guard()
    if err:
        return err
    return jsonify({'success': True,
                    'overrides': rbac_service.get_user_overrides(user_id),
                    'group_ids': rbac_service.get_user_group_ids(user_id)})


@rbac_bp.route('/api/users/<int:user_id>/overrides', methods=['POST'])
def api_set_user_overrides(user_id):
    err = _guard()
    if err:
        return err
    data = request.get_json() or {}
    ok, msg = rbac_service.set_user_overrides(
        user_id, data.get('overrides') or {}, operator_id=session['user_id'])
    if ok:
        from models.operation_log import OperationType, TargetType
        from models.user_info import UserInfo
        u = db.session.get(UserInfo, user_id)
        uname = u.username if u else str(user_id)
        _log_rbac(OperationType.SET_USER_OVERRIDES,
                  f"设置用户权限覆盖: {uname}（{len(data.get('overrides') or {})}项）",
                  TargetType.PERMISSION, user_id, uname)
    return jsonify({'success': ok, 'message': msg}), (200 if ok else 400)


@rbac_bp.route('/api/users/<int:user_id>/effective', methods=['GET'])
def api_get_effective_permissions(user_id):
    """查看用户最终生效的权限（用于界面预览）"""
    err = _guard()
    if err:
        return err
    perms = rbac_service.compute_effective_permissions(user_id, use_cache=False)
    return jsonify({'success': True, 'permissions': perms})
