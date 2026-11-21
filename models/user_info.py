from db import db
from datetime import datetime
from flask_login import UserMixin


class UserInfo(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(120), nullable=False)
    # 用户基本信息扩展：姓名、部门
    full_name = db.Column(db.String(80), nullable=True, comment='姓名')
    department = db.Column(db.String(80), nullable=True, comment='部门')
    is_admin = db.Column(db.Boolean, default=False)
    # 是否作业者：仅勾选的账号才会出现在生产入库等页面的「作业者」下拉中
    is_operator = db.Column(db.Boolean, default=False, nullable=False, comment='是否作业者')
    # 账号启用状态：False 表示已禁用，禁止登录及一切权限校验
    is_active = db.Column(db.Boolean, default=True, nullable=False, comment='账号是否启用')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # 操作人相关字段
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='创建者ID')
    updated_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='修改者ID')
    
    # 关联用户信息
    creator = db.relationship('UserInfo', foreign_keys=[created_by], remote_side=[id], backref=db.backref('created_users', lazy='dynamic'))
    modifier = db.relationship('UserInfo', foreign_keys=[updated_by], remote_side=[id], backref=db.backref('modified_users', lazy='dynamic'))

    def has_permission(self, permission_name):
        """判断用户是否拥有指定权限（RBAC 唯一来源）。

        参数兼容历史写法：可传 'upload' 或 'can_upload'，统一按 can_* 编码解析。
        """
        if self.is_admin:
            return True

        if not getattr(self, 'is_active', True):
            return False

        code = permission_name if permission_name.startswith('can_') \
            else f'can_{permission_name}'
        from utils.permission_utils import check_permission
        return check_permission(self.id, code)

    def __repr__(self):
        return f'<UserInfo {self.username}>'