"""
RBAC 权限服务（权限组模型）

核心职责：
    1. 权限解析：计算用户的「有效权限」= 用户权限组权限 ∪ 用户级覆盖
    2. 权限组管理：创建/更新/删除权限组，维护权限组-权限关联
    3. 权限分配：用户-权限组绑定、用户级覆盖的读写
    4. 缓存：带 TTL 的用户有效权限缓存，权限变更时按用户失效

生效优先级（高 → 低）：
    用户覆盖(user_permission_override) > 用户权限组(user_group) > 无权限(False)

说明：
    旧权限表 permission_info 已在升级时备份并删除，RBAC 为唯一权威来源。
"""

from flask import current_app

from db import db
from models.rbac import (
    PermissionGroup, GroupPermission, UserGroup, UserPermissionOverride, Permission,
    EFFECT_ALLOW, EFFECT_DENY,
)
from models.user_info import UserInfo
from utils.permission_catalog import (
    PERMISSION_CATALOG, PERMISSION_CODES, PAGE_PERMISSION_CODES,
    MODULE_NAME_MAP, MODULE_SORT_MAP,
)

# 用户有效权限缓存：统一复用 query_cache_manager（M-15）
# 不再维护独立 _PERM_CACHE，避免三套权限缓存各自为政、失效不同步。
_PERM_TTL = 300  # 秒


def _get_perm_cache():
    """返回统一的权限缓存实例（惰性导入，避免循环依赖）。"""
    from query_cache import query_cache_manager
    return query_cache_manager


# ============================================================
# 权限目录同步
# ============================================================
def sync_permission_catalog():
    """将权限目录同步进 permission 表（幂等，可重复执行）"""
    try:
        existing = {p.code: p for p in Permission.query.all()}
        created, updated = 0, 0

        for item in PERMISSION_CATALOG:
            perm = existing.get(item['code'])
            if perm is None:
                db.session.add(Permission(
                    code=item['code'],
                    name=item['name'],
                    module=item['module'],
                    module_key=item['module_key'],
                    permission_type=item['permission_type'],
                    sort_order=item['sort_order'],
                    remark=item.get('remark'),
                    is_active=True,
                ))
                created += 1
            else:
                changed = (
                    perm.name != item['name']
                    or perm.module != item['module']
                    or perm.module_key != item['module_key']
                    or perm.permission_type != item['permission_type']
                    or perm.sort_order != item['sort_order']
                )
                if changed:
                    perm.name = item['name']
                    perm.module = item['module']
                    perm.module_key = item['module_key']
                    perm.permission_type = item['permission_type']
                    perm.sort_order = item['sort_order']
                    updated += 1

        db.session.commit()
        if created or updated:
            current_app.logger.info(f"权限目录同步完成: 新增 {created} 项, 更新 {updated} 项")
        return True
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"权限目录同步失败: {e}")
        return False


# 内置权限组定义：(名称, 编码, 描述, 授权类型)
#   'all'  -> 全部权限
#   'page' -> 仅页面访问权限
_BUILTIN_GROUPS = [
    ('管理员组', 'admin_group', '拥有全部权限', 'all'),
    ('只读组', 'viewer_group', '仅可查看各页面，无操作权限', 'page'),
]


def _ensure_builtin_groups():
    """创建内置权限组（幂等）"""
    for name, code, desc, grant_type in _BUILTIN_GROUPS:
        group = PermissionGroup.query.filter_by(code=code).first()
        if group is None:
            group = PermissionGroup(name=name, code=code, description=desc,
                                    is_builtin=True,
                                    sort_order=0 if grant_type == 'all' else 10)
            db.session.add(group)
            db.session.flush()  # 获取 group.id
        if grant_type == 'all':
            target_codes = PERMISSION_CODES
        else:
            target_codes = PAGE_PERMISSION_CODES
        existing = {gp.permission_code for gp in
                    GroupPermission.query.filter_by(group_id=group.id).all()}
        for pcode in target_codes - existing:
            db.session.add(GroupPermission(group_id=group.id,
                                           permission_code=pcode,
                                           effect=EFFECT_ALLOW))
    db.session.commit()


def init_rbac_defaults():
    """启动时初始化：同步权限目录 → 创建内置权限组"""
    sync_permission_catalog()
    try:
        _ensure_builtin_groups()
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建内置权限组失败: {e}")
    return True


# ============================================================
# 有效权限解析
# ============================================================
def _collect_group_permissions(group_id):
    """
    收集某权限组的权限映射（停用组返回空）。
    Returns: {permission_code: effect}
    """
    group = db.session.get(PermissionGroup, group_id)
    if group is None or not group.is_active:
        return {}
    return {gp.permission_code: gp.effect
            for gp in GroupPermission.query.filter_by(group_id=group_id).all()}


def _collect_group_permissions_batch(group_ids):
    """批量收集多个权限组的权限映射（M-9：一次 in_() 查询替代循环 N+1）。

    Returns: {group_id: {permission_code: effect}}；停用组不在结果中（等同空）。
    注意：为保持与 _collect_group_permissions 一致的语义，仍会过滤停用组。
    """
    ids = [gid for gid in group_ids if gid is not None]
    if not ids:
        return {}
    # 一次性取出启用中的组 ID，避免逐组查询 PermissionGroup
    active_ids = {
        g.id for g in PermissionGroup.query.filter(
            PermissionGroup.id.in_(ids), PermissionGroup.is_active.is_(True)
        ).all()
    }
    result = {}
    if not active_ids:
        return result
    # 一次 in_() 取回所有组权限明细，再在内存中按 group_id 归组
    rows = GroupPermission.query.filter(GroupPermission.group_id.in_(active_ids)).all()
    for gp in rows:
        result.setdefault(gp.group_id, {})[gp.permission_code] = gp.effect
    return result


def compute_effective_permissions(user_id, use_cache=True):
    """
    计算用户的有效权限。

    Returns:
        dict: {permission_code: bool}
    """
    if use_cache:
        cached = _get_perm_cache().get_permission(user_id)
        if cached is not None:
            return dict(cached)

    try:
        user = db.session.get(UserInfo, user_id)
        if not user:
            return {}

        # 管理员拥有全部权限
        if user.is_admin:
            result = {code: True for code in PERMISSION_CODES}
            _write_cache(user_id, result)
            return result

        # 1) 权限组权限（多组取并集：任一 allow 即允许；仅当无 allow 时才看 deny）
        group_perms = {}
        user_groups = UserGroup.query.filter_by(user_id=user_id).all()
        # M-9：批量取回所有相关权限组明细，避免逐组 N+1 查询
        group_perms_map = _collect_group_permissions_batch([ug.group_id for ug in user_groups])
        for ug in user_groups:
            for code, effect in group_perms_map.get(ug.group_id, {}).items():
                if code not in group_perms or effect == EFFECT_ALLOW:
                    group_perms[code] = effect

        # 2) 用户级覆盖
        overrides = {}
        for ov in UserPermissionOverride.query.filter_by(user_id=user_id).all():
            overrides[ov.permission_code] = ov.effect

        # 3) 合并，计算最终布尔值
        result = {}
        for code in PERMISSION_CODES:
            if code in overrides:
                result[code] = (overrides[code] == EFFECT_ALLOW)
            elif code in group_perms:
                result[code] = (group_perms[code] == EFFECT_ALLOW)
            else:
                result[code] = False

        _write_cache(user_id, result)
        return dict(result)

    except Exception as e:
        current_app.logger.error(f"计算用户有效权限失败 - 用户ID {user_id}: {e}")
        return {}


def _write_cache(user_id, result):
    """写入统一权限缓存（M-15）。"""
    _get_perm_cache().set_permission(user_id, dict(result))


def invalidate_user_permission_cache(user_id=None):
    """失效用户权限缓存；user_id 为空时清空全部"""
    cache = _get_perm_cache()
    if user_id is None:
        cache.invalidate_all()
    else:
        cache.invalidate_user_cache(user_id)


def invalidate_group_permission_cache(group_id):
    """失效所有绑定该权限组的用户缓存（权限组权限变更后调用）"""
    try:
        # M-9：一次 IN 查询取回组内用户，避免多次遍历
        user_ids = [ug.user_id for ug in UserGroup.query.filter_by(group_id=group_id).all()]
        if not user_ids:
            return
        cache = _get_perm_cache()
        for uid in user_ids:
            cache.invalidate_user_cache(uid)
    except Exception as e:
        current_app.logger.warning(f"失效权限组缓存失败 - 权限组ID {group_id}: {e}")


# ============================================================
# 权限组管理
# ============================================================
def list_groups():
    """返回全部权限组（按排序号、名称）"""
    return PermissionGroup.query.order_by(PermissionGroup.sort_order,
                                          PermissionGroup.name).all()


def create_group(name, code, description=None, sort_order=0):
    """创建权限组，返回 (成功, 消息, 权限组对象)"""
    try:
        if PermissionGroup.query.filter_by(code=code).first():
            return False, '权限组编码已存在', None
        if PermissionGroup.query.filter_by(name=name).first():
            return False, '权限组名称已存在', None

        group = PermissionGroup(name=name, code=code, description=description,
                                sort_order=sort_order)
        db.session.add(group)
        db.session.commit()
        return True, '权限组创建成功', group
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"创建权限组失败: {e}")
        return False, f'创建权限组失败: {e}', None


def update_group(group_id, name=None, description=None, sort_order=None, is_active=None):
    """更新权限组信息"""
    try:
        group = db.session.get(PermissionGroup, group_id)
        if not group:
            return False, '权限组不存在'

        if name and name != group.name:
            if PermissionGroup.query.filter(PermissionGroup.name == name,
                                            PermissionGroup.id != group_id).first():
                return False, '权限组名称已存在'
            group.name = name

        if description is not None:
            group.description = description
        if sort_order is not None:
            group.sort_order = sort_order
        if is_active is not None:
            group.is_active = is_active

        db.session.commit()
        invalidate_group_permission_cache(group_id)
        return True, '权限组更新成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"更新权限组失败: {e}")
        return False, f'更新权限组失败: {e}'


def delete_group(group_id):
    """删除权限组（内置权限组与仍有用户绑定的权限组不可删除）"""
    try:
        group = db.session.get(PermissionGroup, group_id)
        if not group:
            return False, '权限组不存在'
        if group.is_builtin:
            return False, '内置权限组不允许删除'
        if UserGroup.query.filter_by(group_id=group_id).count() > 0:
            return False, '该权限组已分配给用户，请先解除绑定'

        GroupPermission.query.filter_by(group_id=group_id).delete()
        db.session.delete(group)
        db.session.commit()
        invalidate_group_permission_cache(group_id)
        return True, '权限组删除成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除权限组失败: {e}")
        return False, f'删除权限组失败: {e}'


# ============================================================
# 权限组权限分配
# ============================================================
def get_group_permission_map(group_id):
    """
    获取权限组权限映射。

    Returns:
        dict: {
            'own': {code: 'allow'|'deny'},   # 本权限组显式配置
            'effective': {code: bool}        # 合并后的最终结果
        }
    """
    group = db.session.get(PermissionGroup, group_id)
    if not group:
        return {'own': {}, 'effective': {}}

    own = {gp.permission_code: gp.effect
           for gp in GroupPermission.query.filter_by(group_id=group_id).all()}

    effective = {}
    for code in PERMISSION_CODES:
        effective[code] = (own.get(code) == EFFECT_ALLOW)

    return {'own': own, 'effective': effective}


def set_group_permissions(group_id, allows=None, denies=None):
    """
    全量设置权限组权限：allows 中的编码置为 allow，denies 中的置为 deny，
    其余编码清除（回到默认拒绝）。
    """
    try:
        group = db.session.get(PermissionGroup, group_id)
        if not group:
            return False, '权限组不存在'

        allows = set(allows or [])
        denies = set(denies or [])
        # 同一编码同时出现在两处时，deny 优先
        allows -= denies

        invalid_codes = (allows | denies) - PERMISSION_CODES
        if invalid_codes:
            return False, f'存在无效权限编码: {", ".join(sorted(invalid_codes))}'

        GroupPermission.query.filter_by(group_id=group_id).delete()

        for code in allows:
            db.session.add(GroupPermission(group_id=group_id, permission_code=code,
                                           effect=EFFECT_ALLOW))
        for code in denies:
            db.session.add(GroupPermission(group_id=group_id, permission_code=code,
                                           effect=EFFECT_DENY))

        db.session.commit()
        invalidate_group_permission_cache(group_id)
        return True, '权限组权限保存成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存权限组权限失败: {e}")
        return False, f'保存权限组权限失败: {e}'


# ============================================================
# 用户-权限组与用户覆盖
# ============================================================
def get_user_group_ids(user_id):
    return [ug.group_id for ug in UserGroup.query.filter_by(user_id=user_id).all()]


def get_group_member_ids(group_id):
    """返回某权限组的成员用户ID列表"""
    return [ug.user_id for ug in UserGroup.query.filter_by(group_id=group_id).all()]


def set_group_members(group_id, user_ids, operator_id=None):
    """全量设置权限组成员（以 group_id 为维度）"""
    try:
        group = db.session.get(PermissionGroup, group_id)
        if not group:
            return False, '权限组不存在'

        user_ids = set(int(u) for u in (user_ids or []))
        existing = {u.id for u in UserInfo.query.filter(UserInfo.id.in_(user_ids)).all()} \
            if user_ids else set()
        invalid = user_ids - existing
        if invalid:
            return False, f'存在无效用户ID: {sorted(invalid)}'

        UserGroup.query.filter_by(group_id=group_id).delete()
        for uid in user_ids:
            db.session.add(UserGroup(user_id=uid, group_id=group_id, created_by=operator_id))

        # 分配权限组即视为以权限组为准：清除这些成员的历史覆盖记录
        for uid in user_ids:
            UserPermissionOverride.query.filter_by(user_id=uid).delete()

        db.session.commit()
        for uid in user_ids:
            invalidate_user_permission_cache(uid)
        return True, '权限组成员保存成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存权限组成员失败: {e}")
        return False, f'保存权限组成员失败: {e}'


def set_user_groups(user_id, group_ids, operator_id=None):
    """全量设置用户权限组"""
    try:
        if not db.session.get(UserInfo, user_id):
            return False, '用户不存在'

        group_ids = set(int(g) for g in (group_ids or []))
        existing = {g.id for g in PermissionGroup.query.all()}
        invalid = group_ids - existing
        if invalid:
            return False, f'存在无效权限组ID: {sorted(invalid)}'

        UserGroup.query.filter_by(user_id=user_id).delete()
        for gid in group_ids:
            db.session.add(UserGroup(user_id=user_id, group_id=gid, created_by=operator_id))

        # 分配权限组即视为以权限组为准：清除该用户的历史覆盖记录
        # （避免历史覆盖项压过权限组导致分组不生效）。
        # 如需个性化微调，请在「批量分配权限」中重新设置覆盖。
        if group_ids:
            UserPermissionOverride.query.filter_by(user_id=user_id).delete()

        db.session.commit()
        invalidate_user_permission_cache(user_id)
        return True, '用户权限组保存成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存用户权限组失败: {e}")
        return False, f'保存用户权限组失败: {e}'


def get_user_overrides(user_id):
    """返回 {code: effect}"""
    return {ov.permission_code: ov.effect
            for ov in UserPermissionOverride.query.filter_by(user_id=user_id).all()}


def sync_overrides_from_selected(user_id, selected_codes, operator_id=None):
    """
    以「勾选集合」为准全量同步用户 RBAC 覆盖。

    旧权限设置接口（勾选框模式）的结果写入 RBAC 覆盖表：
    勾选的置 allow、未勾选的置 deny，使 RBAC 成为唯一权威来源，
    保证 check_permission 等读取路径立即与设置界面一致。

    Args:
        selected_codes: 勾选的权限码集合（can_* 编码）
        operator_id: 操作人用户ID
    """
    selected = {c for c in (selected_codes or set()) if c in PERMISSION_CODES}
    overrides = {code: (EFFECT_ALLOW if code in selected else EFFECT_DENY)
                 for code in PERMISSION_CODES}
    return set_user_overrides(user_id, overrides, operator_id=operator_id)


def set_user_overrides(user_id, overrides, operator_id=None):
    """
    全量设置用户权限覆盖。

    Args:
        overrides: dict {permission_code: 'allow'|'deny'|'inherit'}
                   'inherit' 表示移除覆盖，回落到权限组权限
    """
    try:
        if not db.session.get(UserInfo, user_id):
            return False, '用户不存在'

        valid_codes = PERMISSION_CODES
        cleaned = {}
        for code, effect in (overrides or {}).items():
            if code not in valid_codes:
                return False, f'存在无效权限编码: {code}'
            if effect not in (EFFECT_ALLOW, EFFECT_DENY):
                continue  # inherit / 空值为移除覆盖
            cleaned[code] = effect

        UserPermissionOverride.query.filter_by(user_id=user_id).delete()
        for code, effect in cleaned.items():
            db.session.add(UserPermissionOverride(
                user_id=user_id, permission_code=code, effect=effect,
                updated_by=operator_id,
            ))

        db.session.commit()
        invalidate_user_permission_cache(user_id)
        return True, '用户权限覆盖保存成功'
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存用户权限覆盖失败: {e}")
        return False, f'保存用户权限覆盖失败: {e}'


# ============================================================
# 管理界面数据组装
# ============================================================
def build_permission_tree():
    """按模块聚合权限项，供管理界面分组渲染"""
    grouped = {}
    for item in PERMISSION_CATALOG:
        grouped.setdefault(item['module_key'], []).append(item)

    tree = []
    for module_key, items in grouped.items():
        tree.append({
            'module_key': module_key,
            'module_name': MODULE_NAME_MAP.get(module_key, module_key),
            'sort_order': MODULE_SORT_MAP.get(module_key, 999),
            'permissions': sorted(items, key=lambda x: x['sort_order']),
        })
    return sorted(tree, key=lambda x: x['sort_order'])
