from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class UnitPrice(db.Model):
    """单价登录主数据

    记录品番对应的单价信息：生效日期、品番、单价、币种、客户等。
    字段为通用命名，后续业务细化时按需调整。
    """
    __tablename__ = 'unit_price'
    __table_args__ = (
        Index('idx_unit_price_date', 'effective_date'),
        Index('idx_unit_price_pno', 'pno'),
        Index('idx_unit_price_pno_date', 'pno', 'effective_date'),
    )

    id = db.Column(db.Integer, primary_key=True)
    effective_date = db.Column(db.Date, nullable=False)               # 生效日期
    pno = db.Column(db.String(50), nullable=False)                    # 品番
    part_name = db.Column(db.String(100), nullable=True)              # 品名
    vehicle_type = db.Column(db.String(100), nullable=True)           # 车种
    unit_price = db.Column(db.Numeric(12, 4), default=0, nullable=False)  # 单价
    currency = db.Column(db.String(10), default='CNY', nullable=False)    # 币种
    customer = db.Column(db.String(100), nullable=True)               # 客户
    remark = db.Column(db.String(500), nullable=True)                 # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('unit_prices', lazy='dynamic'))

    def __repr__(self):
        return f'<UnitPrice {self.pno} {self.unit_price}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'effective_date': self.effective_date.strftime('%Y-%m-%d') if self.effective_date else '',
            'pno': self.pno or '',
            'vehicle_type': self.vehicle_type or '',
            'unit_price': float(self.unit_price) if self.unit_price is not None else 0,
            'currency': self.currency or 'CNY',
            'customer': self.customer or '',
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
