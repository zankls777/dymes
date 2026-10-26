"""
权限检查工具模块
兼容MySQL和SQLite数据库切换

权限来源（RBAC）：
    用户有效权限 = 权限组权限 ∪ 用户级覆盖，详见 utils/rbac_service.py
    当用户既无权限组也无覆盖记录时，回退读取旧表 PermissionInfo，保证升级兼容。
"""

from flask import current_app
from db import db
from models.user_info import UserInfo
from models.permission_info import PermissionInfo

# 旧权限表查询缓存：统一复用 query_cache_manager（M-15）
# 键形如 'legacy_row:<user_id>'，TTL 10s（页面渲染时 get_user_permissions
# 会被多次调用，短 TTL 避免重复查库，同时保证权限变更后快速生效）。
_LEGACY_ROW_TTL = 10  # 秒


def _legacy_cache():
    """返回统一的查询缓存实例（惰性导入，避免循环依赖）。"""
    from query_cache import query_cache_manager
    return query_cache_manager


def _get_legacy_permission_row(user_id):
    """带短 TTL 缓存的旧权限记录查询，降低页面渲染时的重复查询开销"""
    cache = _legacy_cache()
    key = f'legacy_row:{user_id}'
    cached = cache.get_list_cache(key)
    if cached is not None:
        return cached or None
    row = PermissionInfo.query.filter_by(user_id=user_id).first()
    # 用空字典占位「查无记录」，避免 None 与「未命中」语义混淆
    cache.set_list_cache(key, row if row is not None else {}, _LEGACY_ROW_TTL)
    return row


def get_legacy_permission_row(user_id):
    """对外暴露的旧权限记录读取（走短 TTL 缓存）。

    供 routes_mes / routes_mes_modules / routes_nas / routes_cad 等模块统一调用，
    替代各处直接 `PermissionInfo.query.filter_by(user_id=...).first()`，
    降低同一请求内的重复查库（L-5：权限查询统一走缓存）。
    """
    return _get_legacy_permission_row(user_id)


def invalidate_legacy_permission_cache(user_id=None):
    """权限数据变更后失效旧权限查询缓存

    统一缓存无法按键前缀批量删除，user_id 为空时退化为整体失效列表缓存，
    确保权限变更后不会读到陈旧数据。
    """
    cache = _legacy_cache()
    if user_id is None:
        cache.invalidate_list_cache()
    else:
        cache.delete_list_cache(f'legacy_row:{user_id}')


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
        # 获取用户信息
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
        if not permissions:
            # 有效权限为空时回退旧表，避免权限体系尚未初始化导致全员失权
            permission = _get_legacy_permission_row(user_id)
            if not permission:
                return False
            return getattr(permission, permission_name, False)

        return bool(permissions.get(permission_name, False))
        
    except Exception as e:
        # 如果权限检查失败（可能是字段不存在），记录错误
        current_app.logger.warning(f"权限检查失败 - 用户ID: {user_id}, 权限: {permission_name}, 错误: {str(e)}")
        
        # 如果是管理员，在权限检查失败时仍然允许通过
        if allow_admin:
            try:
                user = db.session.get(UserInfo, user_id)
                return user and user.is_admin
            except:
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
        # 获取用户信息
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

        # 有效权限为空时回退旧表，与 check_permission 行为保持一致
        if not permissions:
            legacy = _get_legacy_permission_row(user_id)
            if not legacy:
                return False
            permissions = {name: bool(getattr(legacy, name, False))
                           for name in permission_names}

        # 检查多个权限
        results = [bool(permissions.get(name, False)) for name in permission_names]
        
        # 根据require_all参数决定返回逻辑
        if require_all:
            return all(results)  # 所有权限都必须满足
        else:
            return any(results)  # 任一权限满足即可
        
    except Exception as e:
        # 如果权限检查失败，记录错误
        current_app.logger.warning(f"多权限检查失败 - 用户ID: {user_id}, 权限: {permission_names}, 错误: {str(e)}")
        
        # 如果是管理员，在权限检查失败时仍然允许通过
        if allow_admin:
            try:
                user = db.session.get(UserInfo, user_id)
                return user and user.is_admin
            except:
                pass
        
        return False


def get_user_permissions(user_id):
    """
    获取用户所有权限 - 兼容MySQL和SQLite
    
    Args:
        user_id: 用户ID
    
    Returns:
        dict: 权限字典，字段不存在时返回False
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
            return {field: True for field in PermissionInfo.__table__.columns.keys() 
                   if field not in ['id', 'user_id']}
        
        from utils.rbac_service import compute_effective_permissions
        permissions = compute_effective_permissions(user_id)
        if permissions:
            return permissions

        # 回退旧表
        permission = _get_legacy_permission_row(user_id)
        if not permission:
            return {}
        
        # 获取所有权限字段
        result = {}
        for column in PermissionInfo.__table__.columns:
            if column.name not in ['id', 'user_id']:
                result[column.name] = getattr(permission, column.name, False)
        
        return result
        
    except Exception as e:
        current_app.logger.warning(f"获取用户权限失败 - 用户ID: {user_id}, 错误: {str(e)}")
        return {}


def ensure_permission_fields():
    """
    确保权限表包含所有必要字段 - 用于数据库迁移后的兼容性检查
    """
    try:
        # 检查权限表是否存在所有必要字段
        from sqlalchemy import inspect
        from app_factory import db
        
        inspector = inspect(db.engine)
        columns = inspector.get_columns('permission_info')
        existing_columns = {col['name'] for col in columns}
        
        # 定义所有必要的权限字段
        required_fields = {
            'can_upload', 'can_delete', 'can_batch_delete', 'can_page',
            'can_vehicle_types', 'can_products', 'can_material_download',
            'can_file_management', 'can_file_comparison', 'can_sub_list', 'can_usage_list',
            'can_sub_list_show_differences', 'can_products_add', 'can_products_edit',
            'can_products_delete', 'can_products_download', 'can_products_import',
            'can_vehicle_types_add', 'can_vehicle_types_edit', 'can_vehicle_types_delete',
            'can_vehicle_types_download', 'can_vehicle_types_batch_add',
            'can_vehicle_types_import_excel', 'can_material_download_download',
            'can_material_download_update', 'can_file_management_upload',
            'can_file_management_delete', 'can_file_management_download',
            'can_file_management_export_file_list',
            'can_file_comparison_compare', 'can_file_comparison_download',
            'can_sub_list_add', 'can_sub_list_edit', 'can_sub_list_delete',
            'can_sub_list_download', 'can_usage_list_add', 'can_usage_list_edit',
            'can_usage_list_delete', 'can_usage_list_download',
            'can_vehicle_types_batch_delete', 'can_products_batch_delete',
            'can_products_batch_add', 'can_user_mgmt_add_user', 'can_user_mgmt_change_password',
            'can_user_mgmt_reset_password', 'can_user_mgmt_manage_permissions',
            'can_user_mgmt_delete_user', 'can_system_management',
            'can_material_calculator', 'can_invest_tidy', 'can_invest_summary',
            'can_auto_loading', 'can_auto_loading_upload',
            'can_auto_loading_delete', 'can_auto_loading_export',
            'can_data_management', 'can_corrugated_location', 'can_test_sheet', 'can_index',
            # 补线打端子权限
            'can_wire_lot_login', 'can_wire_scan_entry', 'can_wire_data_download',
            'can_wire_lot_login_add', 'can_wire_lot_login_upload', 'can_wire_lot_login_clear',
            'can_wire_lot_login_download',
            'can_wire_scan_entry_scan', 'can_wire_scan_entry_save', 'can_wire_scan_entry_edit',
            'can_wire_scan_entry_delete', 'can_wire_scan_entry_download',
            'can_wire_crimp_login', 'can_wire_crimp_login_upload',
            'can_wire_data_download_query', 'can_wire_data_download_download',
            'can_wire_data_download_edit', 'can_wire_data_download_delete',
            # 资材入库权限
            'can_material_inbound_daily', 'can_material_inbound',
            'can_material_inbound_daily_upload', 'can_material_inbound_daily_add',
            'can_material_inbound_daily_edit', 'can_material_inbound_daily_delete',
            'can_material_inbound_daily_download',
            'can_material_inbound_scan', 'can_material_inbound_submit',
            'can_material_inbound_revoke',
            # 盘点管理权限
            'can_inventory_login', 'can_inventory_check', 'can_inventory_summary',
            'can_inventory_login_edit', 'can_inventory_check_submit',
            'can_inventory_summary_delete',
            # MES分组权限
            'can_mes_group', 'can_mes_group_add', 'can_mes_group_edit',
            'can_mes_group_delete', 'can_mes_group_download', 'can_mes_group_unmask',
            # MES模块权限（计划登录 / 生产入库 / 品质检查(包装) / 单价登陆）
            'can_mes_plan', 'can_mes_plan_add', 'can_mes_plan_edit',
            'can_mes_plan_delete', 'can_mes_plan_download',
            'can_mes_production', 'can_mes_production_add', 'can_mes_production_edit',
            'can_mes_production_delete', 'can_mes_production_download',
            'can_mes_production_unmask',
            'can_mes_quality', 'can_mes_quality_add', 'can_mes_quality_edit',
            'can_mes_quality_delete', 'can_mes_quality_download',
            'can_mes_price', 'can_mes_price_add', 'can_mes_price_edit',
            'can_mes_price_delete', 'can_mes_price_download'
        }
        
        missing_fields = required_fields - existing_columns
        if missing_fields:
            current_app.logger.warning(f"权限表缺少字段: {missing_fields}")
            return False
        
        return True
        
    except Exception as e:
        current_app.logger.error(f"检查权限字段失败: {str(e)}")
        return False
