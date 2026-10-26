from db import db
from datetime import datetime
from flask_login import UserMixin

from models.permission_info import PermissionInfo


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
        if self.is_admin:
            return True

        permission = PermissionInfo.query.filter_by(user_id=self.id).first()
        if not permission:
            return False

        if permission_name == 'upload':
            return permission.can_upload
        elif permission_name == 'delete':
            return permission.can_delete
        elif permission_name == 'batch_delete':
            return permission.can_batch_delete
        elif permission_name == 'page':
            return permission.can_page
        elif permission_name == 'vehicle_types':
            return permission.can_vehicle_types
        elif permission_name == 'products':
            return permission.can_products
        elif permission_name == 'material_download':
            return permission.can_material_download
        elif permission_name == 'file_management':
            return permission.can_file_management
        elif permission_name == 'file_management_upload':
            return permission.can_file_management_upload
        elif permission_name == 'file_management_delete':
            return permission.can_file_management_delete
        elif permission_name == 'file_management_download':
            return permission.can_file_management_download
        elif permission_name == 'file_management_freeze':
            return permission.can_file_management_freeze
        elif permission_name == 'file_management_export_file_list':
            return permission.can_file_management_export_file_list
        elif permission_name == 'file_comparison':
            return permission.can_file_comparison
        elif permission_name == 'sub_list':
            return permission.can_sub_list
        elif permission_name == 'usage_list':
            return permission.can_usage_list
        elif permission_name == 'delete':
            return permission.can_file_management_delete

        # 补线打端子权限
        elif permission_name == 'wire_lot_login':
            return permission.can_wire_lot_login
        elif permission_name == 'wire_scan_entry':
            return permission.can_wire_scan_entry
        elif permission_name == 'wire_data_download':
            return permission.can_wire_data_download
        elif permission_name == 'wire_lot_login_add':
            return permission.can_wire_lot_login_add
        elif permission_name == 'wire_lot_login_upload':
            return permission.can_wire_lot_login_upload
        elif permission_name == 'wire_lot_login_clear':
            return permission.can_wire_lot_login_clear
        elif permission_name == 'wire_lot_login_download':
            return permission.can_wire_lot_login_download
        elif permission_name == 'wire_scan_entry_scan':
            return permission.can_wire_scan_entry_scan
        elif permission_name == 'wire_scan_entry_save':
            return permission.can_wire_scan_entry_save
        elif permission_name == 'wire_scan_entry_edit':
            return permission.can_wire_scan_entry_edit
        elif permission_name == 'wire_scan_entry_delete':
            return permission.can_wire_scan_entry_delete
        elif permission_name == 'wire_scan_entry_download':
            return permission.can_wire_scan_entry_download
        elif permission_name == 'wire_crimp_login':
            return permission.can_wire_crimp_login
        elif permission_name == 'wire_crimp_login_upload':
            return permission.can_wire_crimp_login_upload
        elif permission_name == 'wire_data_download_query':
            return permission.can_wire_data_download_query
        elif permission_name == 'wire_data_download_download':
            return permission.can_wire_data_download_download
        elif permission_name == 'wire_data_download_edit':
            return permission.can_wire_data_download_edit
        elif permission_name == 'wire_data_download_delete':
            return permission.can_wire_data_download_delete

        # 资材入库权限
        elif permission_name == 'material_inbound_daily':
            return permission.can_material_inbound_daily
        elif permission_name == 'material_inbound':
            return permission.can_material_inbound
        elif permission_name == 'material_inbound_daily_upload':
            return permission.can_material_inbound_daily_upload
        elif permission_name == 'material_inbound_daily_add':
            return permission.can_material_inbound_daily_add
        elif permission_name == 'material_inbound_daily_edit':
            return permission.can_material_inbound_daily_edit
        elif permission_name == 'material_inbound_daily_delete':
            return permission.can_material_inbound_daily_delete
        elif permission_name == 'material_inbound_daily_download':
            return permission.can_material_inbound_daily_download
        elif permission_name == 'material_inbound_scan':
            return permission.can_material_inbound_scan
        elif permission_name == 'material_inbound_submit':
            return permission.can_material_inbound_submit
        elif permission_name == 'material_inbound_revoke':
            return permission.can_material_inbound_revoke

        # 其他页面访问权限
        elif permission_name == 'material_calculator':
            return permission.can_material_calculator
        elif permission_name == 'invest_tidy':
            return permission.can_invest_tidy
        elif permission_name == 'invest_summary':
            return permission.can_invest_summary
        elif permission_name == 'data_management':
            return permission.can_data_management
        elif permission_name == 'corrugated_location':
            return permission.can_corrugated_location
        elif permission_name == 'test_sheet':
            return permission.can_test_sheet
        elif permission_name == 'index':
            return permission.can_index

        return False

    def __repr__(self):
        return f'<UserInfo {self.username}>'