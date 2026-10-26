from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class PlanLogin(db.Model):
    """计划登录主数据

    记录生产计划的登录信息：计划日期、品番、车种、计划数量等。
    字段为通用命名，后续业务细化时按需调整。
    """
    __tablename__ = 'plan_login'
    __table_args__ = (
        Index('idx_plan_login_date', 'plan_date'),
        Index('idx_plan_login_pno', 'pno'),
        Index('idx_plan_login_lot_no', 'lot_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    plan_date = db.Column(db.Date, nullable=False)                   # 计划日期
    lot_no = db.Column(db.String(100), nullable=True)                # LOT NO（扫码登录）
    pno = db.Column(db.String(50), nullable=False)                   # 品番
    part_name = db.Column(db.String(100), nullable=True)             # 品名
    vehicle_type = db.Column(db.String(100), nullable=True)          # 车种
    plan_qty = db.Column(db.Integer, default=0, nullable=False)      # 计划数量
    customer = db.Column(db.String(100), nullable=True)              # 客户/客户代码
    status = db.Column(db.String(20), default='pending', nullable=False)  # 状态：pending/done
    remark = db.Column(db.String(500), nullable=True)                # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('plan_logins', lazy='dynamic'))

    def __repr__(self):
        return f'<PlanLogin {self.plan_date} {self.pno}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'plan_date': self.plan_date.strftime('%Y-%m-%d') if self.plan_date else '',
            'lot_no': self.lot_no or '',
            'pno': self.pno or '',
            'vehicle_type': self.vehicle_type or '',
            'plan_qty': self.plan_qty or 0,
            'customer': self.customer or '',
            'status': self.status or 'pending',
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
