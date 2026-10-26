"""
RBAC 权限模型（权限组-权限-用户）

设计说明：
- Permission       : 权限项元数据（code 与现有 permission_info.can_* 字段同名，便于兼容）
- PermissionGroup  : 权限组（权限包），一个权限组打包若干权限项，用户直接分配到组
- GroupPermission  : 权限组-权限关联，effect 取值 allow / deny
- UserGroup        : 用户-权限组关联，一个用户可绑定多个权限组，权限取并集
- UserPermissionOverride : 用户级显式覆盖，优先级最高（allow / deny）

生效优先级（高 → 低）：
    用户覆盖(UserPermissionOverride) > 用户权限组(UserGroup) > 无权限(False)

授权模型：
    管理员直接给用户分配一个或多个「权限组」（如「只读包」「质检包」「生产包」），
    权限组决定用户能访问的页面与操作；如需对个别用户微调，用用户级覆盖(allow/deny)。
"""

from datetime import datetime

from db import db


# 权限作用类型
PERMISSION_TYPE_PAGE = 'page'      # 页面访问
PERMISSION_TYPE_ACTION = 'action'  # 按钮/操作

# 授权效果
EFFECT_ALLOW = 'allow'
EFFECT_DENY = 'deny'


class Permission(db.Model):
    """权限项元数据表"""
    __tablename__ = 'permission'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(64), unique=True, nullable=False, index=True, comment='权限编码，如 can_products_add')
    name = db.Column(db.String(128), nullable=False, comment='权限名称，如 添加成品')
    module = db.Column(db.String(64), nullable=False, index=True, comment='所属模块，如 成品管理')
    module_key = db.Column(db.String(64), nullable=False, index=True, comment='模块标识，如 products')
    permission_type = db.Column(db.String(16), nullable=False, default=PERMISSION_TYPE_ACTION,
                                comment='权限类型：page=页面访问，action=按钮操作')
    sort_order = db.Column(db.Integer, default=0, comment='排序号，越小越靠前')
    is_active = db.Column(db.Boolean, default=True, comment='是否启用')
    remark = db.Column(db.String(255), comment='备注')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'code': self.code,
            'name': self.name,
            'module': self.module,
            'module_key': self.module_key,
            'permission_type': self.permission_type,
            'sort_order': self.sort_order,
            'is_active': self.is_active,
            'remark': self.remark,
        }

    def __repr__(self):
        return f'<Permission {self.code}>'


class UserPermissionOverride(db.Model):
    """用户级权限覆盖表，优先级高于权限组"""
    __tablename__ = 'user_permission_override'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=False, index=True)
    permission_code = db.Column(db.String(64), nullable=False, index=True, comment='权限编码')
    effect = db.Column(db.String(8), nullable=False, default=EFFECT_ALLOW, comment='allow=允许，deny=拒绝')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='修改人ID')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'permission_code', name='uq_user_permission_override'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'permission_code': self.permission_code,
            'effect': self.effect,
        }


class PermissionGroup(db.Model):
    """权限组（权限包），与角色并存；用户可直接绑定权限组获得权限"""
    __tablename__ = 'permission_group'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), unique=True, nullable=False, comment='权限组名称')
    code = db.Column(db.String(64), unique=True, nullable=False, index=True, comment='权限组编码')
    description = db.Column(db.String(255), comment='权限组描述')
    sort_order = db.Column(db.Integer, default=0, comment='排序号')
    is_builtin = db.Column(db.Boolean, default=False, comment='是否内置（内置不可删除）')
    is_active = db.Column(db.Boolean, default=True, comment='是否启用')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'description': self.description,
            'sort_order': self.sort_order,
            'is_builtin': self.is_builtin,
            'is_active': self.is_active,
        }

    def __repr__(self):
        return f'<PermissionGroup {self.code}>'


class GroupPermission(db.Model):
    """权限组-权限关联表"""
    __tablename__ = 'group_permission'

    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('permission_group.id'),
                         nullable=False, index=True)
    permission_code = db.Column(db.String(64), nullable=False, index=True, comment='权限编码')
    effect = db.Column(db.String(8), nullable=False, default=EFFECT_ALLOW, comment='allow=允许，deny=拒绝')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('group_id', 'permission_code', name='uq_group_permission'),
    )

    group = db.relationship('PermissionGroup',
                            backref=db.backref('group_permissions', lazy='select',
                                               cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'group_id': self.group_id,
            'permission_code': self.permission_code,
            'effect': self.effect,
        }


class UserGroup(db.Model):
    """用户-权限组关联表"""
    __tablename__ = 'user_group'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=False, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('permission_group.id'),
                         nullable=False, index=True)
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='分配人ID')

    __table_args__ = (
        db.UniqueConstraint('user_id', 'group_id', name='uq_user_group'),
    )

    group = db.relationship('PermissionGroup',
                            backref=db.backref('user_groups', lazy='select',
                                               cascade='all, delete-orphan'))

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'group_id': self.group_id,
            'group_name': self.group.name if self.group else None,
        }
