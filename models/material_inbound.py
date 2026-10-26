from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class MaterialInboundDaily(db.Model):
    """资材每日入库明细

    每天上传/录入的资材待入库清单：京信PNO、数量、位置、备注。
    页面2「资材入库」按 P/N 扫描/搜索定位到当天明细并提交，回写入库状态、
    入库时间与操作人员，形成「计划 → 实绩」的可追溯闭环。
    """
    __tablename__ = 'material_inbound_daily'
    __table_args__ = (
        Index('idx_mib_plan_date', 'plan_date'),
        Index('idx_mib_part_no', 'part_no'),
        Index('idx_mib_plan_part', 'plan_date', 'part_no'),
        Index('idx_mib_status', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    plan_date = db.Column(db.Date, nullable=False, index=True)      # 入库计划日（当天清单归属日）
    part_no = db.Column(db.String(100), nullable=False)             # 京信PNO
    quantity = db.Column(db.Integer, default=0, nullable=False)     # 计划入库数量
    location = db.Column(db.String(200), nullable=True)             # 位置（自由文本，如货架号）
    remark = db.Column(db.String(500), nullable=True)               # 备注

    # 入库状态：pending=未入库，short=入库不足（实收<应入库，可继续补录），done=已入库
    status = db.Column(db.String(20), default='pending', nullable=False, index=True)
    inbound_qty = db.Column(db.Integer, default=0)                  # 实际入库数量（可累计补录）
    inbound_date = db.Column(db.Date, nullable=True)                # 入库日期（操作员登录时填写）
    inbound_at = db.Column(db.DateTime, nullable=True)              # 入库时间（UTC+8）
    inbound_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 操作人员ID
    # 操作人员姓名快照：账号删除后仍可显示历史文本
    inbound_by_name = db.Column(db.String(80), nullable=True, comment='操作人员姓名快照')

    created_at = db.Column(db.DateTime, default=get_utc8_time)      # 明细创建时间（UTC+8）
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 明细创建人ID

    inbounder = db.relationship('UserInfo', foreign_keys=[inbound_by],
                                backref=db.backref('material_inbound_ops', lazy='dynamic'))
    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('material_inbound_created', lazy='dynamic'))

    def __repr__(self):
        return f'<MaterialInboundDaily {self.plan_date} {self.part_no} {self.status}>'

    def to_dict(self, operator_names=None):
        """转换为字典；operator_names 可传入 {user_id: username} 避免逐行查库"""
        names = operator_names or {}
        return {
            'id': self.id,
            'plan_date': self.plan_date.strftime('%Y-%m-%d') if self.plan_date else None,
            'part_no': self.part_no,
            'quantity': self.quantity,
            'location': self.location or '',
            'remark': self.remark or '',
            'status': self.status,
            'inbound_qty': self.inbound_qty or 0,
            'inbound_date': self.inbound_date.strftime('%Y-%m-%d') if self.inbound_date else '',
            'inbound_at': format_utc8_time(self.inbound_at) if self.inbound_at else None,
            'inbound_by': self.inbound_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'inbound_by_name': (names.get(self.inbound_by) if self.inbound_by else None) or self.inbound_by_name or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
        }
