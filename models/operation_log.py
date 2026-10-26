"""
操作记录模型
记录用户的各种操作行为
"""

from app_factory import db
from datetime import datetime
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None


def china_now() -> datetime:
    """Return current time in Asia/Shanghai as naive datetime (wall-clock)."""
    try:
        if ZoneInfo is not None:
            return datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    except Exception:
        pass
    # Fallback: system local time
    return datetime.now()


class OperationLog(db.Model):
    """操作记录表"""
    __tablename__ = 'operation_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    # 操作用户ID：允许为空——用户账号删除后仅置空关联，保留日志与姓名快照
    user_id = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True, index=True, comment='操作用户ID')
    # 操作用户名快照：写入时冗余保存，账号删除后历史记录仍可显示文本
    username = db.Column(db.String(80), nullable=True, comment='操作用户名快照')
    operation_type = db.Column(db.String(50), nullable=False, index=True, comment='操作类型')
    operation_desc = db.Column(db.String(500), nullable=False, comment='操作描述')
    target_type = db.Column(db.String(50), index=True, comment='目标类型（文件、用户、车种等）')
    target_id = db.Column(db.Integer, index=True, comment='目标ID')
    target_name = db.Column(db.String(200), comment='目标名称')
    file_path = db.Column(db.String(500), comment='文件路径')
    ip_address = db.Column(db.String(50), comment='IP地址')
    user_agent = db.Column(db.String(500), comment='用户代理')
    # 直接按中国时区时间入库（无 tz），避免二次换算
    created_at = db.Column(db.DateTime, default=china_now, index=True, comment='创建时间')
    
    # 关联用户信息
    user = db.relationship('UserInfo', backref='operation_logs', lazy='joined')
    
    def __repr__(self):
        return f'<OperationLog {self.id}: {self.operation_type} by user {self.user_id}>'
    
    def to_dict(self):
        """转换为字典格式（已按中国时区入库，直接格式化）"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            # 优先取运行时关联用户名；账号已删除（关联为空）时回退到写入时快照
            'username': (self.user.username if self.user else None) or self.username or '未知用户',
            'operation_type': self.operation_type,
            'operation_desc': self.operation_desc,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'target_name': self.target_name,
            'file_path': self.file_path,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None
        }


# 操作类型常量
class OperationType:
    """操作类型常量"""
    UPLOAD_FILE = 'upload_file'  # 上传文件
    DELETE_FILE = 'delete_file'  # 删除文件
    BATCH_DELETE_FILE = 'batch_delete_file'  # 批量删除文件
    DOWNLOAD_FILE = 'download_file'  # 下载文件
    DOWNLOAD_RESULT = 'download_result'  # 下载对比结果
    ADD_USER = 'add_user'  # 添加用户
    DELETE_USER = 'delete_user'  # 删除用户
    UPDATE_USER = 'update_user'  # 更新用户
    ADD_VEHICLE_TYPE = 'add_vehicle_type'  # 添加车种
    DELETE_VEHICLE_TYPE = 'delete_vehicle_type'  # 删除车种
    UPDATE_VEHICLE_TYPE = 'update_vehicle_type'  # 更新车种
    ADD_PRODUCT = 'add_product'  # 添加成品
    DELETE_PRODUCT = 'delete_product'  # 删除成品
    UPDATE_PRODUCT = 'update_product'  # 更新成品
    BATCH_ADD_PRODUCT = 'batch_add_product'  # 批量添加成品
    BATCH_DELETE_PRODUCT = 'batch_delete_product'  # 批量删除成品
    FILE_COMPARE = 'file_compare'  # 文件对比
    LOGIN = 'login'  # 登录
    LOGOUT = 'logout'  # 登出
    CHANGE_PASSWORD = 'change_password'  # 修改密码
    RESET_PASSWORD = 'reset_password'  # 重置密码
    UPDATE_PERMISSIONS = 'update_permissions'  # 更新权限
    # 用户管理补充
    ENABLE_USER = 'enable_user'  # 启用账号
    DISABLE_USER = 'disable_user'  # 禁用账号
    DELETE_PERMISSION = 'delete_permission'  # 删除用户权限记录
    # RBAC 权限组与权限
    ADD_GROUP = 'add_group'  # 新增权限组
    UPDATE_GROUP = 'update_group'  # 更新权限组
    DELETE_GROUP = 'delete_group'  # 删除权限组
    SET_GROUP_PERMISSIONS = 'set_group_permissions'  # 设置权限组权限
    SET_USER_GROUPS = 'set_user_groups'  # 设置用户权限组
    SET_USER_OVERRIDES = 'set_user_overrides'  # 设置用户权限覆盖
    # 车种补充
    BATCH_ADD_VEHICLE_TYPE = 'batch_add_vehicle_type'  # 批量新增车种
    BATCH_DELETE_VEHICLE_TYPE = 'batch_delete_vehicle_type'  # 批量删除车种
    IMPORT_VEHICLE_TYPE = 'import_vehicle_type'  # 导入车种
    EXPORT_VEHICLE_TYPE = 'export_vehicle_type'  # 导出车种
    # 文件冻结/解冻
    FREEZE_FILE = 'freeze_file'  # 冻结文件
    THAW_FILE = 'thaw_file'  # 解冻文件
    BATCH_FREEZE_FILE = 'batch_freeze_file'  # 批量冻结文件
    BATCH_THAW_FILE = 'batch_thaw_file'  # 批量解冻文件
    FREEZE_OLD_VERSIONS = 'freeze_old_versions'  # 冻结旧版本
    # 资材入库
    UPLOAD_INBOUND = 'upload_inbound'  # 上传入库清单
    SAVE_INBOUND = 'save_inbound'  # 保存入库明细
    DELETE_INBOUND = 'delete_inbound'  # 删除入库明细
    SUBMIT_INBOUND = 'submit_inbound'  # 提交入库
    REVOKE_INBOUND = 'revoke_inbound'  # 撤销入库
    EXPORT_INBOUND = 'export_inbound'  # 导出入库清单
    # 盘点
    SAVE_INVENTORY_TEAM = 'save_inventory_team'  # 保存盘点班组
    DELETE_INVENTORY_TEAM = 'delete_inventory_team'  # 删除盘点班组
    SYNC_PNO = 'sync_pno'  # 同步品番字典
    SUBMIT_INVENTORY_CHECK = 'submit_inventory_check'  # 提交盘点
    DELETE_INVENTORY_CHECK = 'delete_inventory_check'  # 删除盘点记录
    # 不良品
    SET_DEFECTIVE_ACCESS_MODE = 'set_defective_access_mode'  # 设置不良品免登录开关
    SUBMIT_DEFECTIVE = 'submit_defective'  # 提交不良品登记
    DELETE_DEFECTIVE = 'delete_defective'  # 删除不良品登记
    EXPORT_DEFECTIVE = 'export_defective'  # 导出不良品数据


# 目标类型常量
class TargetType:
    """目标类型常量"""
    FILE = 'file'  # 文件
    USER = 'user'  # 用户
    VEHICLE_TYPE = 'vehicle_type'  # 车种
    PRODUCT = 'product'  # 成品
    PERMISSION = 'permission'  # 权限
    SYSTEM = 'system'  # 系统
    GROUP = 'group'  # 权限组
    INBOUND = 'inbound'  # 资材入库
    INVENTORY = 'inventory'  # 盘点
    DEFECTIVE = 'defective'  # 不良品
