# -*- coding: utf-8 -*-
"""
数据冻结/解冻功能测试脚本

测试场景覆盖：
1. 三种用户角色权限验证（管理员、有冻结权限、无冻结权限、未登录）
2. 单个文件冻结/解冻
3. 批量冻结/解冻
4. 数据完整性验证（冻结字段、操作人、原因）
5. 边界条件（已冻结文件、不存在文件、空列表）
6. 文件管理页面权限传递

运行方式：
    cd F:\TA\bom3
    python tests/test_freeze_thaw.py
"""

import os
import sys
import json
import unittest
from datetime import datetime

# 确保项目根目录在 path 中
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, PROJECT_ROOT)

# 设置测试环境变量（使用内存数据库）
os.environ['FLASK_CONFIG'] = 'testing'
os.environ['DATABASE_TYPE'] = 'sqlite'

from app_factory import create_app, db
from models.user_info import UserInfo
from models.permission_info import PermissionInfo
from models.file_info import FileInfo
from werkzeug.security import generate_password_hash


class FreezeThawTestBase(unittest.TestCase):
    """测试基类：初始化测试应用和测试数据"""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config['TESTING'] = True
        cls.app.config['WTF_CSRF_ENABLED'] = False
        cls.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
        cls.client = cls.app.test_client()

    def setUp(self):
        """每个测试方法前重建数据库表和测试数据"""
        with self.app.app_context():
            db.create_all()
            self._seed_test_data()

    def tearDown(self):
        """每个测试方法后清理数据库"""
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def _seed_test_data(self):
        """创建测试数据：3种角色用户 + 测试文件"""
        # 清理 create_app() 自动创建的默认数据，确保测试环境干净
        FileInfo.query.delete()
        PermissionInfo.query.delete()
        UserInfo.query.delete()
        db.session.commit()

        # 1. 管理员
        admin = UserInfo(
            username='admin',
            password=generate_password_hash('admin123!'),
            is_admin=True
        )
        db.session.add(admin)
        db.session.commit()

        admin_perm = PermissionInfo(
            user_id=admin.id,
            can_file_management=True,
            can_file_management_freeze=True,
            can_file_management_download=True,
            can_file_management_delete=True,
        )
        db.session.add(admin_perm)

        # 2. 有冻结权限的普通用户
        freeze_user = UserInfo(
            username='freeze_user',
            password=generate_password_hash('pass123!'),
            is_admin=False
        )
        db.session.add(freeze_user)
        db.session.commit()

        freeze_perm = PermissionInfo(
            user_id=freeze_user.id,
            can_file_management=True,
            can_file_management_freeze=True,
            can_file_management_download=True,
        )
        db.session.add(freeze_perm)

        # 3. 无冻结权限的普通用户
        no_freeze_user = UserInfo(
            username='no_freeze_user',
            password=generate_password_hash('pass123!'),
            is_admin=False
        )
        db.session.add(no_freeze_user)
        db.session.commit()

        no_freeze_perm = PermissionInfo(
            user_id=no_freeze_user.id,
            can_file_management=True,
            can_file_management_freeze=False,
            can_file_management_download=True,
        )
        db.session.add(no_freeze_perm)

        # 4. 创建测试文件
        # 活跃文件（未冻结）
        for i in range(1, 6):
            file = FileInfo(
                filename=f'test_file_{i}.xlsx',
                file_type='SubList',
                filesize=1024 * i,
                eo=f'a{i}',
                vehicle_type=f'JA_PE2_FRDR_LH',
                first_subpno='12345',
                is_frozen=False,
                created_by=admin.id,
            )
            db.session.add(file)

        # 已冻结文件
        frozen_file = FileInfo(
            filename='frozen_file_1.xlsx',
            file_type='SubList',
            filesize=2048,
            eo='b1',
            vehicle_type='JA_PE2_FRDR_LH',
            first_subpno='12345',
            is_frozen=True,
            frozen_at=datetime.utcnow(),
            frozen_by=admin.id,
            frozen_reason='测试冻结',
            created_by=admin.id,
        )
        db.session.add(frozen_file)

        db.session.commit()

    # ============ 辅助方法 ============

    def _login(self, username, password):
        """通过登录接口模拟登录"""
        return self.client.post('/login', data={
            'username': username,
            'password': password
        }, follow_redirects=False)

    def _login_as(self, role):
        """按角色快捷登录并返回 client（已携带 session）"""
        creds = {
            'admin': ('admin', 'admin123!'),
            'freeze_user': ('freeze_user', 'pass123!'),
            'no_freeze_user': ('no_freeze_user', 'pass123!'),
        }
        username, password = creds[role]
        self._login(username, password)
        return self.client

    def _logout(self):
        """退出登录"""
        return self.client.get('/logout', follow_redirects=False)

    def _get_file_id(self, filename):
        """根据文件名获取文件ID"""
        with self.app.app_context():
            f = FileInfo.query.filter_by(filename=filename).first()
            return f.id if f else None

    def _get_file(self, file_id):
        """获取文件记录"""
        with self.app.app_context():
            return db.session.get(FileInfo, file_id)

    def _api_freeze(self, file_id, reason='测试冻结'):
        """调用单个冻结API"""
        return self.client.post(
            f'/api/files/freeze/{file_id}',
            data=json.dumps({'reason': reason}),
            content_type='application/json'
        )

    def _api_thaw(self, file_id):
        """调用单个解冻API"""
        return self.client.post(
            f'/api/files/thaw/{file_id}',
            content_type='application/json'
        )

    def _api_batch_freeze(self, file_ids, reason='批量冻结'):
        """调用批量冻结API"""
        return self.client.post(
            '/api/files/batch_freeze',
            data=json.dumps({'file_ids': file_ids, 'reason': reason}),
            content_type='application/json'
        )

    def _api_batch_thaw(self, file_ids):
        """调用批量解冻API"""
        return self.client.post(
            '/api/files/batch_thaw',
            data=json.dumps({'file_ids': file_ids}),
            content_type='application/json'
        )


# ============================================================
# A. 单个文件冻结测试
# ============================================================

class SingleFreezeTest(FreezeThawTestBase):
    """单个文件冻结功能测试"""

    def test_A1_admin_freeze_success(self):
        """A1: 管理员冻结文件 → 成功"""
        self._login_as('admin')
        file_id = self._get_file_id('test_file_1.xlsx')

        resp = self._api_freeze(file_id, '管理员冻结测试')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('已冻结', data['message'])

        # 验证数据库状态
        file = self._get_file(file_id)
        self.assertTrue(file.is_frozen)
        self.assertIsNotNone(file.frozen_at)
        self.assertEqual(file.frozen_reason, '管理员冻结测试')

    def test_A2_freeze_user_freeze_success(self):
        """A2: 有权限用户冻结文件 → 成功"""
        self._login_as('freeze_user')
        file_id = self._get_file_id('test_file_2.xlsx')

        resp = self._api_freeze(file_id, '有权限用户冻结')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])

        file = self._get_file(file_id)
        self.assertTrue(file.is_frozen)

    def test_A3_no_freeze_user_forbidden(self):
        """A3: 无权限用户冻结文件 → 403"""
        self._login_as('no_freeze_user')
        file_id = self._get_file_id('test_file_3.xlsx')

        resp = self._api_freeze(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])
        self.assertIn('权限', data['error'])

        # 验证文件未被冻结
        file = self._get_file(file_id)
        self.assertFalse(file.is_frozen)

    def test_A4_unauthenticated_rejected(self):
        """A4: 未登录用户冻结文件 → 被拒绝 (401/403/302)"""
        # 确保未登录
        file_id = self._get_file_id('test_file_4.xlsx')

        resp = self._api_freeze(file_id)

        # 未登录用户应被拒绝：
        # - 302: @login_required 重定向到登录页
        # - 401: API 内部 session 检查
        # - 403: CSRF/未授权处理器
        self.assertIn(resp.status_code, (302, 401, 403),
                      f'未登录用户应被拒绝，实际状态码: {resp.status_code}')
        if resp.status_code == 302:
            self.assertIn('/login', resp.headers.get('Location', ''))

        # 验证文件未被冻结
        file = self._get_file(file_id)
        self.assertFalse(file.is_frozen)

    def test_A5_freeze_already_frozen(self):
        """A5: 冻结已冻结文件 → 400"""
        self._login_as('admin')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_freeze(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])
        self.assertIn('已被冻结', data['error'])

    def test_A6_freeze_nonexistent_file(self):
        """A6: 冻结不存在的文件 → 404"""
        self._login_as('admin')

        resp = self._api_freeze(99999)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(data['success'])
        self.assertIn('不存在', data['error'])

    def test_A7_freeze_fields_integrity(self):
        """A7: 验证冻结字段完整保存"""
        self._login_as('admin')
        file_id = self._get_file_id('test_file_5.xlsx')

        before = self._get_file(file_id)
        self.assertFalse(before.is_frozen)
        self.assertIsNone(before.frozen_at)
        self.assertIsNone(before.frozen_by)
        self.assertIsNone(before.frozen_reason)

        self._api_freeze(file_id, '数据完整性测试')

        after = self._get_file(file_id)
        self.assertTrue(after.is_frozen)
        self.assertIsNotNone(after.frozen_at)
        self.assertEqual(after.frozen_by, 1)  # admin id
        self.assertEqual(after.frozen_reason, '数据完整性测试')


# ============================================================
# B. 单个文件解冻测试
# ============================================================

class SingleThawTest(FreezeThawTestBase):
    """单个文件解冻功能测试"""

    def test_B1_admin_thaw_success(self):
        """B1: 管理员解冻文件 → 成功"""
        self._login_as('admin')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_thaw(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertIn('已解冻', data['message'])

        file = self._get_file(file_id)
        self.assertFalse(file.is_frozen)
        self.assertIsNone(file.frozen_at)
        self.assertIsNone(file.frozen_by)
        self.assertIsNone(file.frozen_reason)

    def test_B2_freeze_user_thaw_success(self):
        """B2: 有权限用户解冻文件 → 成功"""
        self._login_as('freeze_user')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_thaw(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])

        file = self._get_file(file_id)
        self.assertFalse(file.is_frozen)

    def test_B3_no_freeze_user_forbidden(self):
        """B3: 无权限用户解冻文件 → 403"""
        self._login_as('no_freeze_user')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_thaw(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])
        self.assertIn('权限', data['error'])

        # 验证文件仍处于冻结状态
        file = self._get_file(file_id)
        self.assertTrue(file.is_frozen)

    def test_B4_thaw_unfrozen_file(self):
        """B4: 解冻未冻结文件 → 400"""
        self._login_as('admin')
        file_id = self._get_file_id('test_file_1.xlsx')  # 活跃文件

        resp = self._api_thaw(file_id)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])
        self.assertIn('未被冻结', data['error'])

    def test_B5_thaw_nonexistent_file(self):
        """B5: 解冻不存在的文件 → 404"""
        self._login_as('admin')

        resp = self._api_thaw(99999)
        data = resp.get_json()

        self.assertEqual(resp.status_code, 404)
        self.assertFalse(data['success'])
        self.assertIn('不存在', data['error'])

    def test_B6_thaw_clears_all_fields(self):
        """B6: 验证解冻后所有冻结字段被清空"""
        self._login_as('admin')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        before = self._get_file(file_id)
        self.assertTrue(before.is_frozen)
        self.assertIsNotNone(before.frozen_at)
        self.assertIsNotNone(before.frozen_by)
        self.assertIsNotNone(before.frozen_reason)

        self._api_thaw(file_id)

        after = self._get_file(file_id)
        self.assertFalse(after.is_frozen)
        self.assertIsNone(after.frozen_at)
        self.assertIsNone(after.frozen_by)
        self.assertIsNone(after.frozen_reason)


# ============================================================
# C. 批量冻结测试
# ============================================================

class BatchFreezeTest(FreezeThawTestBase):
    """批量冻结功能测试"""

    def test_C1_admin_batch_freeze_success(self):
        """C1: 管理员批量冻结 → 成功"""
        self._login_as('admin')
        id1 = self._get_file_id('test_file_1.xlsx')
        id2 = self._get_file_id('test_file_2.xlsx')

        resp = self._api_batch_freeze([id1, id2], '管理员批量冻结')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['frozen_count'], 2)

        self.assertTrue(self._get_file(id1).is_frozen)
        self.assertTrue(self._get_file(id2).is_frozen)
        self.assertEqual(self._get_file(id1).frozen_reason, '管理员批量冻结')

    def test_C2_freeze_user_batch_freeze_success(self):
        """C2: 有权限用户批量冻结 → 成功"""
        self._login_as('freeze_user')
        id1 = self._get_file_id('test_file_1.xlsx')
        id2 = self._get_file_id('test_file_2.xlsx')

        resp = self._api_batch_freeze([id1, id2])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['frozen_count'], 2)

    def test_C3_no_freeze_user_batch_forbidden(self):
        """C3: 无权限用户批量冻结 → 403"""
        self._login_as('no_freeze_user')
        id1 = self._get_file_id('test_file_1.xlsx')

        resp = self._api_batch_freeze([id1])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])

        # 验证文件未被冻结
        self.assertFalse(self._get_file(id1).is_frozen)

    def test_C4_empty_file_ids(self):
        """C4: 空ID列表 → 400"""
        self._login_as('admin')

        resp = self._api_batch_freeze([])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])
        self.assertIn('选择', data['error'])

    def test_C5_partial_already_frozen(self):
        """C5: 部分文件已冻结，只冻结未冻结的"""
        self._login_as('admin')
        active_id = self._get_file_id('test_file_1.xlsx')
        frozen_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_batch_freeze([active_id, frozen_id])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        # 只冻结了1个（已冻结的跳过）
        self.assertEqual(data['frozen_count'], 1)

        self.assertTrue(self._get_file(active_id).is_frozen)
        self.assertTrue(self._get_file(frozen_id).is_frozen)


# ============================================================
# D. 批量解冻测试
# ============================================================

class BatchThawTest(FreezeThawTestBase):
    """批量解冻功能测试"""

    def test_D1_admin_batch_thaw_success(self):
        """D1: 管理员批量解冻 → 成功"""
        self._login_as('admin')
        # 先冻结两个文件
        id1 = self._get_file_id('test_file_1.xlsx')
        id2 = self._get_file_id('test_file_2.xlsx')
        self._api_batch_freeze([id1, id2])

        # 批量解冻
        resp = self._api_batch_thaw([id1, id2])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['thawed_count'], 2)

        self.assertFalse(self._get_file(id1).is_frozen)
        self.assertFalse(self._get_file(id2).is_frozen)

    def test_D2_freeze_user_batch_thaw_success(self):
        """D2: 有权限用户批量解冻 → 成功"""
        self._login_as('freeze_user')
        frozen_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_batch_thaw([frozen_id])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertEqual(data['thawed_count'], 1)

    def test_D3_no_freeze_user_batch_thaw_forbidden(self):
        """D3: 无权限用户批量解冻 → 403"""
        self._login_as('no_freeze_user')
        frozen_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self._api_batch_thaw([frozen_id])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 403)
        self.assertFalse(data['success'])

        # 验证文件仍冻结
        self.assertTrue(self._get_file(frozen_id).is_frozen)

    def test_D4_empty_file_ids(self):
        """D4: 空ID列表 → 400"""
        self._login_as('admin')

        resp = self._api_batch_thaw([])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 400)
        self.assertFalse(data['success'])
        self.assertIn('选择', data['error'])

    def test_D5_partial_unfrozen(self):
        """D5: 部分文件未冻结，只解冻已冻结的"""
        self._login_as('admin')
        frozen_id = self._get_file_id('frozen_file_1.xlsx')
        active_id = self._get_file_id('test_file_1.xlsx')

        resp = self._api_batch_thaw([frozen_id, active_id])
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        # 只解冻了1个（未冻结的跳过）
        self.assertEqual(data['thawed_count'], 1)

        self.assertFalse(self._get_file(frozen_id).is_frozen)
        self.assertFalse(self._get_file(active_id).is_frozen)


# ============================================================
# E. 冻结文件列表和文件详情测试
# ============================================================

class FileListAndDetailTest(FreezeThawTestBase):
    """冻结文件列表和文件详情API测试"""

    def test_E1_get_frozen_files_list(self):
        """E1: 获取冻结文件列表"""
        self._login_as('admin')

        resp = self.client.get('/api/files/frozen')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertGreaterEqual(data['total'], 1)

        # 验证返回的文件都是冻结的
        for f in data['files']:
            self.assertTrue(f['is_frozen'])

    def test_E2_get_file_detail(self):
        """E2: 获取文件详情（含冻结信息）"""
        self._login_as('admin')
        file_id = self._get_file_id('frozen_file_1.xlsx')

        resp = self.client.get(f'/api/files/{file_id}')
        data = resp.get_json()

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(data['success'])
        self.assertTrue(data['file']['is_frozen'])
        self.assertEqual(data['file']['frozen_reason'], '测试冻结')
        self.assertEqual(data['file']['frozen_by_name'], 'admin')


# ============================================================
# F. 文件管理页面权限传递测试
# ============================================================

class FileManagementPageTest(FreezeThawTestBase):
    """文件管理页面权限传递测试"""

    def test_F1_admin_page_permissions(self):
        """F1: 管理员访问页面，权限正确传递"""
        self._login_as('admin')

        resp = self.client.get('/file_management')
        self.assertEqual(resp.status_code, 200)

        # 管理员应该能看到冻结按钮（模板渲染）
        html = resp.data.decode('utf-8')
        import re
        # 匹配按钮标签中的 freeze-btn class（模板渲染的按钮）
        freeze_button_pattern = r'<button[^>]*class="[^"]*freeze-btn[^"]*"[^>]*>'
        freeze_buttons = re.findall(freeze_button_pattern, html)
        self.assertGreater(len(freeze_buttons), 0, '管理员应看到冻结按钮')
        # 批量冻结按钮也应存在
        self.assertIn('batchFreezeBtn', html)

    def test_F2_freeze_user_page_permissions(self):
        """F2: 有冻结权限用户访问页面，冻结按钮显示"""
        self._login_as('freeze_user')

        resp = self.client.get('/file_management')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        import re
        freeze_button_pattern = r'<button[^>]*class="[^"]*freeze-btn[^"]*"[^>]*>'
        freeze_buttons = re.findall(freeze_button_pattern, html)
        self.assertGreater(len(freeze_buttons), 0, '有权限用户应看到冻结按钮')

    def test_F3_no_freeze_user_page_permissions(self):
        """F3: 无冻结权限用户访问页面，冻结按钮不显示"""
        self._login_as('no_freeze_user')

        resp = self.client.get('/file_management')
        self.assertEqual(resp.status_code, 200)

        html = resp.data.decode('utf-8')
        # 检查模板渲染的按钮HTML（而非JS中的字符串引用）
        # 模板中渲染的冻结按钮包含 data-bs-target="#freezeModal"
        # 无权限时该按钮HTML不会被渲染
        import re
        # 匹配按钮标签中的 freeze-btn class（模板渲染的按钮）
        freeze_button_pattern = r'<button[^>]*class="[^"]*freeze-btn[^"]*"[^>]*>'
        freeze_buttons = re.findall(freeze_button_pattern, html)
        self.assertEqual(len(freeze_buttons), 0, '无权限用户不应看到冻结按钮')

    def test_F4_show_frozen_filter(self):
        """F4: 冻结文件视图过滤正确"""
        self._login_as('admin')

        # 显示冻结文件
        resp = self.client.get('/file_management?show_frozen=true')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')
        self.assertIn('frozen_file_1.xlsx', html)
        self.assertNotIn('test_file_1.xlsx', html)

        # 显示活跃文件（默认）
        resp = self.client.get('/file_management?show_frozen=')
        self.assertEqual(resp.status_code, 200)
        html = resp.data.decode('utf-8')
        self.assertIn('test_file_1.xlsx', html)
        self.assertNotIn('frozen_file_1.xlsx', html)


# ============================================================
# G. 冻结→解冻完整流程测试
# ============================================================

class FullCycleTest(FreezeThawTestBase):
    """冻结→解冻完整流程测试"""

    def test_G1_freeze_thaw_cycle(self):
        """G1: 冻结→解冻完整循环，验证状态一致性"""
        self._login_as('admin')
        file_id = self._get_file_id('test_file_1.xlsx')

        # 初始状态：活跃
        self.assertFalse(self._get_file(file_id).is_frozen)

        # 冻结
        resp = self._api_freeze(file_id, '循环测试冻结')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._get_file(file_id).is_frozen)
        self.assertEqual(self._get_file(file_id).frozen_reason, '循环测试冻结')

        # 解冻
        resp = self._api_thaw(file_id)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(self._get_file(file_id).is_frozen)
        self.assertIsNone(self._get_file(file_id).frozen_reason)

        # 再次冻结（验证可重复操作）
        resp = self._api_freeze(file_id, '再次冻结')
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self._get_file(file_id).is_frozen)
        self.assertEqual(self._get_file(file_id).frozen_reason, '再次冻结')

    def test_G2_batch_freeze_thaw_cycle(self):
        """G2: 批量冻结→批量解冻完整循环"""
        self._login_as('admin')
        ids = [
            self._get_file_id(f'test_file_{i}.xlsx')
            for i in range(1, 4)
        ]

        # 批量冻结
        resp = self._api_batch_freeze(ids, '批量循环测试')
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['frozen_count'], 3)

        for fid in ids:
            self.assertTrue(self._get_file(fid).is_frozen)

        # 批量解冻
        resp = self._api_batch_thaw(ids)
        data = resp.get_json()
        self.assertTrue(data['success'])
        self.assertEqual(data['thawed_count'], 3)

        for fid in ids:
            self.assertFalse(self._get_file(fid).is_frozen)


# ============================================================
# 测试运行主程序
# ============================================================

def run_tests_with_report():
    """运行所有测试并生成中文报告"""
    import io
    from unittest import TextTestRunner

    # 测试套件
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    test_classes = [
        SingleFreezeTest,
        SingleThawTest,
        BatchFreezeTest,
        BatchThawTest,
        FileListAndDetailTest,
        FileManagementPageTest,
        FullCycleTest,
    ]

    for cls in test_classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    # 运行测试
    print('=' * 70)
    print('  数据冻结/解冻功能测试报告')
    print('=' * 70)
    print()

    runner = TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print()
    print('=' * 70)
    print('  测试汇总')
    print('=' * 70)
    print(f'  测试用例总数: {result.testsRun}')
    print(f'  成功: {result.testsRun - len(result.failures) - len(result.errors)}')
    print(f'  失败: {len(result.failures)}')
    print(f'  错误: {len(result.errors)}')
    print('=' * 70)

    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests_with_report()
    sys.exit(0 if success else 1)
