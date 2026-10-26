from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class ProductionInbound(db.Model):
    """生产入库主数据

    记录生产完工入库信息：入库日期、品番、入库数量、班组等。
    字段为通用命名，后续业务细化时按需调整。
    """
    __tablename__ = 'production_inbound'
    __table_args__ = (
        Index('idx_prod_inbound_date', 'inbound_date'),
        Index('idx_prod_inbound_pno', 'pno'),
        Index('idx_prod_inbound_lot_no', 'lot_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    inbound_date = db.Column(db.Date, nullable=False)                  # 入库日期
    lot_no = db.Column(db.String(100), nullable=True)                  # LOT NO
    pno = db.Column(db.String(50), nullable=False)                     # 品番
    part_name = db.Column(db.String(100), nullable=True)               # 品名
    vehicle_type = db.Column(db.String(100), nullable=True)            # 车种
    inbound_qty = db.Column(db.Integer, default=0, nullable=False)     # 入库数量
    operator = db.Column(db.String(50), nullable=True)                 # 作业者（默认取登录用户姓名）
    unit_price = db.Column(db.Numeric(12, 4), default=0, nullable=False)  # 单价（按品番拉取）
    remark = db.Column(db.String(500), nullable=True)                  # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('production_inbounds', lazy='dynamic'))

    def __repr__(self):
        return f'<ProductionInbound {self.inbound_date} {self.pno}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'inbound_date': self.inbound_date.strftime('%Y-%m-%d') if self.inbound_date else '',
            'lot_no': self.lot_no or '',
            'pno': self.pno or '',
            'vehicle_type': self.vehicle_type or '',
            'inbound_qty': self.inbound_qty or 0,
            'operator': self.operator or '',
            'unit_price': float(self.unit_price) if self.unit_price is not None else 0,
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
