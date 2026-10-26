from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class QualityCheck(db.Model):
    """品质检查（包装）主数据

    记录包装环节的品质检查结果：检查日期、品番、检查数量、
    合格/不合格数量、判定结果等。字段为通用命名，后续按需调整。
    """
    __tablename__ = 'quality_check'
    __table_args__ = (
        Index('idx_quality_check_date', 'check_date'),
        Index('idx_quality_check_pno', 'pno'),
        Index('idx_quality_check_lot_no', 'lot_no'),
        Index('idx_quality_check_result', 'result'),
    )

    id = db.Column(db.Integer, primary_key=True)
    check_date = db.Column(db.Date, nullable=False)                   # 检查日期
    # 包装时间：精确到秒的作业时间戳（结算清单「包装时间」列与区间筛选使用）
    check_at = db.Column(db.DateTime, nullable=True, index=True)
    lot_no = db.Column(db.String(100), nullable=True)                 # LOT NO（包装/检查归属）
    pno = db.Column(db.String(50), nullable=False)                    # 品番
    part_name = db.Column(db.String(100), nullable=True)              # 品名
    vehicle_type = db.Column(db.String(100), nullable=True)           # 车种
    check_qty = db.Column(db.Integer, default=0, nullable=False)      # 检查数量
    ok_qty = db.Column(db.Integer, default=0, nullable=False)         # 合格数量
    ng_qty = db.Column(db.Integer, default=0, nullable=False)         # 不合格数量
    result = db.Column(db.String(20), default='pending', nullable=False)  # 判定：pending/ok/ng
    checker = db.Column(db.String(50), nullable=True)                 # 检查员
    remark = db.Column(db.String(500), nullable=True)                 # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('quality_checks', lazy='dynamic'))

    def __repr__(self):
        return f'<QualityCheck {self.check_date} {self.pno}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'check_date': self.check_date.strftime('%Y-%m-%d') if self.check_date else '',
            'check_at': format_utc8_time(self.check_at) if self.check_at else '',
            'lot_no': self.lot_no or '',
            'pno': self.pno or '',
            'vehicle_type': self.vehicle_type or '',
            'check_qty': self.check_qty or 0,
            'ok_qty': self.ok_qty or 0,
            'ng_qty': self.ng_qty or 0,
            'result': self.result or 'pending',
            'checker': self.checker or '',
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
