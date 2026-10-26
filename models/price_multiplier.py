from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class PriceMultiplier(db.Model):
    """单价倍数规则

    按「时间区间 + 作业者」维护单价调整倍数（例如 1.5 倍）。
    生产入库保存时，按入库日期落在 [start_date, end_date] 且作业者匹配，
    取优先级最高的一条规则，将 单价 × 倍数 作为调整后单价。

    作业者为空表示「所有作业者」通用规则（优先级低于指定作业者的规则）。
    """
    __tablename__ = 'price_multiplier'
    __table_args__ = (
        Index('idx_price_multiplier_range', 'start_date', 'end_date'),
        Index('idx_price_multiplier_operator', 'operator'),
    )

    id = db.Column(db.Integer, primary_key=True)
    start_date = db.Column(db.Date, nullable=False)                    # 生效开始日期
    end_date = db.Column(db.Date, nullable=False)                      # 生效结束日期
    operator = db.Column(db.String(50), nullable=True)                 # 作业者（空=所有作业者）
    multiplier = db.Column(db.Numeric(8, 4), default=1, nullable=False)  # 倍数（如 1.5）
    pno = db.Column(db.String(50), nullable=True)                      # 品番（空=所有品番）
    remark = db.Column(db.String(500), nullable=True)                  # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('price_multipliers', lazy='dynamic'))

    def __repr__(self):
        return f'<PriceMultiplier {self.start_date}~{self.end_date} {self.operator} x{self.multiplier}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'start_date': self.start_date.strftime('%Y-%m-%d') if self.start_date else '',
            'end_date': self.end_date.strftime('%Y-%m-%d') if self.end_date else '',
            'operator': self.operator or '',
            'multiplier': float(self.multiplier) if self.multiplier is not None else 1,
            'pno': self.pno or '',
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
