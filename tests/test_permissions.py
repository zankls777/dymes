# -*- coding: utf-8 -*-
"""
权限分配 / 校验 / 回收 / 跨页面隔离 测试

覆盖场景：
1. NAS 文件搜索页权限：分配 → 持久化生效 → 回收
2. 跨页面权限隔离：只授予 NAS 权限不会连带其它页面
3. 权限组授权、用户覆盖优先级（覆盖 > 权限组）及「回落继承」回收
4. 管理员恒有全部权限；禁用账号一律无权限；停用权限组失效
5. 权限目录完整性（can_* 编码唯一、页面权限均在目录内）

运行方式（项目根目录）：
    python -m unittest tests.test_permissions -v
    或
    python tests/test_permissions.py
"""

import os
import sys
import unittest

# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

from flask import Flask  # noqa: E402

from db import db  # noqa: E402
from models.user_info import UserInfo  # noqa: E402
from models.rbac import UserPermissionOverride  # noqa: E402
from utils.permission_catalog import (  # noqa: E402
    PERMISSION_CODES, PAGE_PERMISSION_CODES,
)
from utils import rbac_service  # noqa: E402
from utils.permission_utils import (  # noqa: E402
    check_permission, check_multiple_permissions, get_user_permissions,
    get_effective_permission_view,
)

NAS_CODES = (
    'can_nas_file_search',
    'can_nas_file_search_config',
    'can_nas_file_search_search',
    'can_nas_file_search_import',
)


class PermissionTestCase(unittest.TestCase):
    """基于内存 SQLite 的权限服务测试基类"""

    @classmethod
    def setUpClass(cls):
        cls.app = Flask(__name__)
        cls.app.config.update(
            TESTING=True,
            SECRET_KEY='permission-test',
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(cls.app)
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()

    def setUp(self):
        # 每个用例使用干净的表与权限缓存，避免相互污染
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        rbac_service.invalidate_user_permission_cache(None)

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    # ---------- 辅助方法 ----------
    def create_user(self, username, is_admin=False, is_active=True):
        user = UserInfo(username=username, password='x',
                        is_admin=is_admin, is_active=is_active)
        db.session.add(user)
        db.session.commit()
        return user

    def grant_only(self, user_id, codes):
        """按「勾选集合」全量设置用户权限（模拟前端保存行为）"""
        ok, msg = rbac_service.sync_overrides_from_selected(user_id, set(codes))
        self.assertTrue(ok, msg)

    # ---------- 用例 ----------
    def test_nas_assign_persist_and_revoke(self):
        """NAS 页面权限：分配后可校验 → 重算（模拟重启）仍生效 → 可回收"""
        user = self.create_user('nas_user')

        # 分配前：无任何 NAS 权限
        for code in NAS_CODES:
            self.assertFalse(check_permission(user.id, code), code)

        # 分配 4 项 NAS 权限
        self.grant_only(user.id, NAS_CODES)
        for code in NAS_CODES:
            self.assertTrue(check_permission(user.id, code), code)

        # 权限已落库（覆盖表 allow），而非仅内存生效
        rows = UserPermissionOverride.query.filter_by(user_id=user.id).all()
        effect_map = {r.permission_code: r.effect for r in rows}
        for code in NAS_CODES:
            self.assertEqual(effect_map.get(code), 'allow', code)

        # 清空缓存后重新计算（等价于服务重启后的首次读取），权限仍然生效
        rbac_service.invalidate_user_permission_cache(None)
        for code in NAS_CODES:
            self.assertTrue(check_permission(user.id, code), code)

        # 回收：只勾选页面访问，其余 NAS 操作权限全部撤销
        self.grant_only(user.id, ['can_nas_file_search'])
        self.assertTrue(check_permission(user.id, 'can_nas_file_search'))
        for code in NAS_CODES[1:]:
            self.assertFalse(check_permission(user.id, code), code)

        # 完全回收：勾选集合为空
        self.grant_only(user.id, [])
        for code in NAS_CODES:
            self.assertFalse(check_permission(user.id, code), code)

    def test_cross_page_isolation(self):
        """只授予 NAS 页面权限，不应连带获得其它页面权限"""
        user = self.create_user('isolated_user')
        self.grant_only(user.id, ['can_nas_file_search'])

        self.assertTrue(check_permission(user.id, 'can_nas_file_search'))
        for other in ('can_file_management', 'can_mes_plan', 'can_products',
                      'can_corrugated_location', 'can_user_mgmt_manage_permissions'):
            self.assertFalse(check_permission(user.id, other), other)

        perms = get_user_permissions(user.id)
        self.assertTrue(perms.get('can_nas_file_search'))
        self.assertFalse(perms.get('can_mes_plan'))
        # 多权限 OR / AND 校验
        self.assertTrue(check_multiple_permissions(
            user.id, ['can_nas_file_search', 'can_mes_plan']))
        self.assertFalse(check_multiple_permissions(
            user.id, ['can_nas_file_search', 'can_mes_plan'], require_all=True))

    def test_group_grant_and_override_priority(self):
        """权限组授权生效；用户级 deny 覆盖组 allow；回落继承后恢复"""
        user = self.create_user('group_user')
        ok, msg, group = rbac_service.create_group('测试组', 'test_group')
        self.assertTrue(ok, msg)
        group_id = group.id
        ok, msg = rbac_service.set_group_permissions(
            group_id, allows={'can_mes_plan'})
        self.assertTrue(ok, msg)
        ok, msg = rbac_service.set_user_groups(user.id, [group_id])
        self.assertTrue(ok, msg)

        # 组授权生效
        self.assertTrue(check_permission(user.id, 'can_mes_plan'))

        # 用户级 deny 优先级高于权限组
        ok, msg = rbac_service.set_user_overrides(
            user.id, {'can_mes_plan': 'deny'})
        self.assertTrue(ok, msg)
        self.assertFalse(check_permission(user.id, 'can_mes_plan'))

        # 回落继承（移除覆盖）后重新使用权限组授权
        ok, msg = rbac_service.set_user_overrides(
            user.id, {'can_mes_plan': 'inherit'})
        self.assertTrue(ok, msg)
        self.assertTrue(check_permission(user.id, 'can_mes_plan'))

    def test_inactive_group_grants_nothing(self):
        """停用权限组后，组内授权立即失效"""
        user = self.create_user('inactive_group_user')
        ok, msg, group = rbac_service.create_group('临时组', 'tmp_group')
        self.assertTrue(ok, msg)
        group_id = group.id
        rbac_service.set_group_permissions(group_id, allows={'can_test_sheet'})
        rbac_service.set_user_groups(user.id, [group_id])
        self.assertTrue(check_permission(user.id, 'can_test_sheet'))

        ok, msg = rbac_service.update_group(group_id, is_active=False)
        self.assertTrue(ok, msg)
        rbac_service.invalidate_user_permission_cache(user.id)
        self.assertFalse(check_permission(user.id, 'can_test_sheet'))

    def test_admin_always_allowed(self):
        """管理员恒有全部权限（含 NAS）"""
        admin = self.create_user('admin_user', is_admin=True)
        for code in NAS_CODES + ('can_mes_plan', 'can_system_management'):
            self.assertTrue(check_permission(admin.id, code), code)

        perms = get_user_permissions(admin.id)
        self.assertTrue(perms.get('can_nas_file_search'))

        view = get_effective_permission_view(admin.id)
        self.assertTrue(view.can_nas_file_search)

    def test_disabled_user_denied(self):
        """账号禁用后即使配置了权限也一律视为无权限"""
        user = self.create_user('disabled_user')
        self.grant_only(user.id, NAS_CODES)
        self.assertTrue(check_permission(user.id, 'can_nas_file_search'))

        user.is_active = False
        db.session.commit()
        rbac_service.invalidate_user_permission_cache(user.id)

        self.assertFalse(check_permission(user.id, 'can_nas_file_search'))
        self.assertEqual(get_user_permissions(user.id), {})
        view = get_effective_permission_view(user.id)
        self.assertFalse(view.can_nas_file_search)

    def test_invalid_permission_code_rejected(self):
        """非法权限编码不得写入覆盖表"""
        user = self.create_user('invalid_code_user')
        ok, _ = rbac_service.set_user_overrides(
            user.id, {'can_not_exist_permission': 'allow'})
        self.assertFalse(ok)
        self.assertEqual(
            UserPermissionOverride.query.filter_by(user_id=user.id).count(), 0)

    def test_permission_view_defaults_false(self):
        """权限视图对未授予的 can_* 字段默认 False，非 can_* 属性正常抛错"""
        user = self.create_user('view_user')
        self.grant_only(user.id, ['can_nas_file_search'])
        view = get_effective_permission_view(user.id)
        self.assertTrue(view.can_nas_file_search)
        self.assertFalse(view.can_mes_plan)
        with self.assertRaises(AttributeError):
            _ = view.not_a_permission

    def test_catalog_integrity(self):
        """权限目录完整性：编码唯一，NAS 权限均在目录内且为页面/操作权限"""
        codes = list(PERMISSION_CODES)
        self.assertEqual(len(codes), len(set(codes)), '权限编码存在重复')
        for code in NAS_CODES:
            self.assertIn(code, PERMISSION_CODES, code)
        self.assertIn('can_nas_file_search', PAGE_PERMISSION_CODES)
        for code in PAGE_PERMISSION_CODES:
            self.assertIn(code, PERMISSION_CODES, code)


if __name__ == '__main__':
    unittest.main(verbosity=2)
