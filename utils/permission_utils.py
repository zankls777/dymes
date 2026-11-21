"""
权限检查工具模块
兼容MySQL和SQLite数据库切换

权限来源（唯一）：RBAC
    用户有效权限 = 权限组权限 ∪ 用户级覆盖，详见 utils/rbac_service.py
    旧权限表 permission_info 已废弃并删除，不再作为任何权限来源或回退。
"""

from flask import current_app
from db import db
from models.user_info import UserInfo
from utils.permission_catalog import PERMISSION_CODES


class EffectivePermissionView:
    """轻量权限视图：把 RBAC 有效权限包装成带 can_* 属性的对象。

    兼容历史代码 `permission.can_xxx` 的读取方式，避免逐处改写判断逻辑。
    对象恒为真（bool 恒 True），无权限的字段一律返回 False。
    """

    __slots__ = ('_permissions',)

    def __init__(self, permissions):
        # 仅保留 can_* 布尔字段，统一转为 bool
        self._permissions = {code: bool(value)
                             for code, value in (permissions or {}).items()
                             if code.startswith('can_')}

    def __getattr__(self, name):
        # 只有 can_* 字段走默认 False，其他属性正常抛 AttributeError
        if name.startswith('can_'):
            return self._permissions.get(name, False)
        raise AttributeError(name)

    def to_dict(self):
        return dict(self._permissions)


def get_effective_permission_view(user_id):
    """返回用户有效权限视图（带 can_* 属性），供页面/接口做按钮级判断。

    账号不存在或已禁用时返回「全 False」视图，调用方拿到的对象始终非空，
    `if not permission` 的判断不会误触发。
    """
    user = db.session.get(UserInfo, user_id)
    if user is None or not getattr(user, 'is_active', True):
        return EffectivePermissionView({})
    # 管理员恒拥有全部权限（与 check_permission 的管理员放行保持一致）
    if user.is_admin:
        return EffectivePermissionView(_all_permissions_true())
    from utils.rbac_service import compute_effective_permissions
    return EffectivePermissionView(compute_effective_permissions(user_id))


def _all_permissions_true():
    """返回全部权限码为 True 的字典（管理员使用）"""
    return {code: True for code in PERMISSION_CODES}


def check_permission(user_id, permission_name, allow_admin=True):
    """
    检查用户权限 - 兼容MySQL和SQLite

    内部走 RBAC 有效权限解析（权限组 + 用户覆盖），函数签名保持不变，
    现有调用点无需修改。

    Args:
        user_id: 用户ID
        permission_name: 权限名称（对应 can_* 编码）
        allow_admin: 是否允许管理员自动通过

    Returns:
        bool: 是否有权限
    """
    try:
        user = db.session.get(UserInfo, user_id)
        if not user:
            return False

        # 账号已禁用：一律视为无权限
        if not getattr(user, 'is_active', True):
            return False

        # 管理员自动通过
        if allow_admin and user.is_admin:
            return True

        from utils.rbac_service import compute_effective_permissions
        permissions = compute_effective_permissions(user_id)
        return bool(permissions.get(permission_name, False))

    except Exception as e:
        # 如果权限检查失败（可能是字段不存在），记录错误
        current_app.logger.warning(
            f"权限检查失败 - 用户ID: {user_id}, 权限: {permission_name}, 错误: {str(e)}")

        # 如果是管理员，在权限检查失败时仍然允许通过
        if allow_admin:
            try:
                user = db.session.get(UserInfo, user_id)
                return user and user.is_admin
            except Exception:
                pass

        return False


def check_multiple_permissions(user_id, permission_names, allow_admin=True, require_all=False):
    """
    检查多个权限 - 兼容MySQL和SQLite

    Args:
        user_id: 用户ID
        permission_names: 权限名称列表
        allow_admin: 是否允许管理员自动通过
        require_all: 是否需要所有权限都满足（True为AND，False为OR）

    Returns:
        bool: 是否有权限
    """
    try:
        user = db.session.get(UserInfo, user_id)
        if not user:
            return False

        # 账号已禁用：一律视为无权限
        if not getattr(user, 'is_active', True):
            return False

        # 管理员自动通过
        if allow_admin and user.is_admin:
            return True

        from utils.rbac_service import compute_effective_permissions
        permissions = compute_effective_permissions(user_id)

        results = [bool(permissions.get(name, False)) for name in permission_names]

        # 根据require_all参数决定返回逻辑
        if require_all:
            return all(results)  # 所有权限都必须满足
        return any(results)      # 任一权限满足即可

    except Exception as e:
        # 如果权限检查失败，记录错误
        current_app.logger.warning(
            f"多权限检查失败 - 用户ID: {user_id}, 权限: {permission_names}, 错误: {str(e)}")

        # 如果是管理员，在权限检查失败时仍然允许通过
        if allow_admin:
            try:
                user = db.session.get(UserInfo, user_id)
                return user and user.is_admin
            except Exception:
                pass

        return False


def get_user_permissions(user_id):
    """
    获取用户所有权限 - 兼容MySQL和SQLite

    Args:
        user_id: 用户ID

    Returns:
        dict: 权限字典 {can_*: bool}，字段不存在时返回False
    """
    try:
        user = db.session.get(UserInfo, user_id)
        if not user:
            return {}

        # 账号已禁用：不返回任何权限
        if not getattr(user, 'is_active', True):
            return {}

        # 管理员拥有所有权限
        if user.is_admin:
            return _all_permissions_true()

        from utils.rbac_service import compute_effective_permissions
        permissions = compute_effective_permissions(user_id)
        return {code: bool(value) for code, value in permissions.items()
                if code.startswith('can_')}

    except Exception as e:
        current_app.logger.warning(f"获取用户权限失败 - 用户ID: {user_id}, 错误: {str(e)}")
        return {}
