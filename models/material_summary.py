from datetime import datetime
from db import db

class MaterialSummary(db.Model):
    """资材量汇总数据模型"""
    __tablename__ = 'material_summary'
    
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    vehicle_name = db.Column(db.String(100), nullable=False, comment='车种')
    part_number = db.Column(db.String(100), nullable=False, comment='成品品番')
    sub_pno = db.Column(db.String(100), nullable=False, comment='资材品番(SubPno)')
    eo_ksc = db.Column(db.String(100), comment='EO编号')
    comp_kind = db.Column(db.String(100), comment='组件类型')
    detail_kind = db.Column(db.String(100), comment='详细类型')
    ksc_pno = db.Column(db.String(100), comment='KSC品番')
    part_name = db.Column(db.String(200), comment='资材名称')
    usage = db.Column(db.String(100), comment='用途')
    length = db.Column(db.String(100), comment='长度')
    total_quantity = db.Column(db.Integer, default=0, comment='总数量')
    unit = db.Column(db.String(50), default='个', comment='单位')
    vehicle_details = db.Column(db.Text, comment='车种详细用量备注')
    remark = db.Column(db.Text, comment='成品换算备注')
    last_update_time = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now, comment='最后更新时间')
    create_time = db.Column(db.DateTime, default=datetime.now, comment='创建时间')
    
    # 创建索引以提高查询性能
    __table_args__ = (
        db.Index('idx_vehicle_part', 'vehicle_name', 'part_number'),
        db.UniqueConstraint('sub_pno', name='uq_sub_pno'),
        db.Index('idx_last_update', 'last_update_time'),
    )
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'vehicle_name': self.vehicle_name,
            'part_number': self.part_number,
            'sub_pno': self.sub_pno,
            'eo_ksc': self.eo_ksc,
            'comp_kind': self.comp_kind,
            'detail_kind': self.detail_kind,
            'ksc_pno': self.ksc_pno,
            'part_name': self.part_name,
            'usage': self.usage,
            'length': self.length,
            'total_quantity': self.total_quantity,
            'unit': self.unit,
            'vehicle_details': self.vehicle_details,
            'remark': self.remark,
            'last_update_time': self.last_update_time.strftime('%Y-%m-%d %H:%M:%S') if self.last_update_time else None,
            'create_time': self.create_time.strftime('%Y-%m-%d %H:%M:%S') if self.create_time else None
        }
    
    def __repr__(self):
        return f'<MaterialSummary {self.sub_pno} for {self.part_number}>'

