from db import db
from datetime import datetime

class VehicleType(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    eo = db.Column(db.String(100), nullable=True, comment='EO编号')
    part_number = db.Column(db.String(100), nullable=False, unique=True, comment='品番')
    vehicle_name = db.Column(db.String(200), comment='车种')
    vehicle_code = db.Column(db.String(50), comment='车种代码')
    item = db.Column(db.String(100), comment='ITEAM')
    vehicle_item_system_version = db.Column(db.String(200), comment='车种_ITEAM__系统版')
    description = db.Column(db.Text, comment='描述')
    create_time = db.Column(db.DateTime, default=datetime.utcnow)
    update_time = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    source = db.Column(db.String(20), default='manual', comment='数据来源：manual-手动添加，excel-Excel导入')
    eo_override = db.Column(db.Boolean, default=False, comment='EO是否被强制覆盖')
    usage_eo = db.Column(db.String(100), nullable=True, comment='UsageList中的原始EO编号')
    needs_eo_attention = db.Column(db.Boolean, default=False, comment='是否需要EO注意（UsageList中有EO但用户未输入）')
    # 操作人相关字段
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='创建者ID')
    updated_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), comment='修改者ID')
    # 操作人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_by_name = db.Column(db.String(80), nullable=True, comment='修改人姓名快照')
    
    # 关联用户信息
    creator = db.relationship('UserInfo', foreign_keys=[created_by], backref=db.backref('created_vehicle_types', lazy='dynamic'))
    modifier = db.relationship('UserInfo', foreign_keys=[updated_by], backref=db.backref('modified_vehicle_types', lazy='dynamic'))
    
    def __repr__(self):
        return f'<VehicleType {self.eo}-{self.part_number}>'
    
    def to_dict(self):
        return {
            'id': self.id,
            'eo': self.eo,
            'part_number': self.part_number,
            'vehicle_name': self.vehicle_name,
            'vehicle_code': self.vehicle_code,
            'item': self.item,
            'vehicle_item_system_version': self.vehicle_item_system_version,
            'description': self.description,
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S') if self.create_time else None,
            'update_time': self.update_time.strftime('%Y-%m-%d %H:%M:%S') if self.update_time else None,
            'source': self.source,
            'eo_override': self.eo_override,
            'usage_eo': self.usage_eo,
            'needs_eo_attention': self.needs_eo_attention,
            'created_by': self.created_by,
            'updated_by': self.updated_by,
            # 优先运行时关联用户名；账号已删除时回退写入时快照
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name or '未知',
            'updated_by_name': (self.modifier.username if self.modifier else None) or self.updated_by_name or '未知'
        }
    
    def get_display_eo(self):
        """获取显示的EO编号，如果EO被强制覆盖，则显示为 '输入EO(UsageList EO)' 格式"""
        if self.eo_override and self.usage_eo:
            return f"{self.eo}({self.usage_eo})"
        elif self.eo_override and not self.usage_eo:
            return f"{self.eo}(无)"
        elif self.needs_eo_attention and self.usage_eo:
            return f"需要EO({self.usage_eo})"
        else:
            return self.eo or '-'
