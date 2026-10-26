from datetime import datetime
from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time

class LotLoginInfo(db.Model):
    """补线打端子 - LOT登录记录（支持手动登记与 Excel 批量导入）"""
    __tablename__ = 'lot_login_info'
    __table_args__ = (
        db.UniqueConstraint('lot_no', name='uq_lot_login_lot_no'),
        Index('idx_lot_login_lot_no', 'lot_no'),
        Index('idx_lot_login_part_no', 'part_no'),
        Index('idx_lot_login_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    vehicle_type = db.Column(db.String(100), nullable=True)   # 车种
    item_name = db.Column(db.String(100), nullable=True)       # ITEM名
    part_no = db.Column(db.String(100), nullable=True)         # 品番
    jx_eo = db.Column(db.String(50), nullable=True)            # 京信EO
    lot_no = db.Column(db.String(100), nullable=False)         # LOT NO
    quantity = db.Column(db.Integer, default=0)                # 入荷数量
    operator = db.Column(db.String(50), nullable=True)         # 操作员/登记人
    source = db.Column(db.String(20), default='manual')        # 来源: manual / excel
    remark = db.Column(db.String(200), nullable=True)          # 备注
    created_at = db.Column(db.DateTime, default=get_utc8_time)  # 登记时间（UTC+8）
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 登记人ID
    # 登记人姓名快照：账号删除后记录仍可显示文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='登记人姓名快照')

    creator = db.relationship('UserInfo', foreign_keys=[created_by], backref=db.backref('lot_logins', lazy='dynamic'))

    def __repr__(self):
        return f'<LotLoginInfo {self.lot_no}>'

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'vehicle_type': self.vehicle_type,
            'item_name': self.item_name,
            'part_no': self.part_no,
            'jx_eo': self.jx_eo,
            'lot_no': self.lot_no,
            'quantity': self.quantity,
            'operator': self.operator,
            'source': self.source,
            'remark': self.remark,
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'created_by': self.created_by,
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name or '',
        }
