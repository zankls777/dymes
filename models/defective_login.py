from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


# 废弃原因固定选项（与前端保持一致，落库存文本快照）
DEFECTIVE_REASONS = ('破损', '过期', '资材不良', '京信需求资材，不返回', '其他')


class DefectiveLogin(db.Model):
    """不良品登录记录（盘点查询 - 不良品登录移动端）

    现场按班组登记废弃资材：登记日期、登记人、班组、资材品番、废弃数量、
    废弃原因、备注。资材清单与盘点实查共用同一数据源（品番字典表）。
    """
    __tablename__ = 'defective_login'
    __table_args__ = (
        Index('idx_defective_login_date', 'login_date'),
        Index('idx_defective_login_team', 'team_id'),
        Index('idx_defective_login_ksc', 'ksc_pno'),
        Index('idx_defective_login_date_team', 'login_date', 'team_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    login_date = db.Column(db.Date, nullable=False)                        # 登记日期
    reporter_name = db.Column(db.String(50), nullable=False)              # 登记人姓名
    team_id = db.Column(db.Integer, db.ForeignKey('inventory_team.id'), nullable=False)
    team_name = db.Column(db.String(50), nullable=False)                  # 班组名称快照
    ksc_pno = db.Column(db.String(50), nullable=False)                    # 资材品番（来源 UsageList.KscPno）
    part_name = db.Column(db.String(100), nullable=True)                  # 品名（选择时带出）
    scrap_qty = db.Column(db.Integer, default=0, nullable=False)          # 废弃数量
    reason = db.Column(db.String(50), nullable=True)                      # 废弃原因
    remark = db.Column(db.String(500), nullable=True)                     # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)            # 提交时间（UTC+8）
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 登记人姓名快照：账号删除后记录仍可显示文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='登记人姓名快照')

    team = db.relationship('InventoryTeam', foreign_keys=[team_id],
                           backref=db.backref('defectives', lazy='dynamic'))
    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('defective_logins', lazy='dynamic'))

    def __repr__(self):
        return f'<DefectiveLogin {self.login_date} {self.ksc_pno} x{self.scrap_qty}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'login_date': self.login_date.strftime('%Y-%m-%d') if self.login_date else None,
            'reporter_name': self.reporter_name or '',
            'team_id': self.team_id,
            'team_name': self.team_name,
            'ksc_pno': self.ksc_pno,
            'part_name': self.part_name or '',
            'scrap_qty': self.scrap_qty or 0,
            'reason': self.reason or '',
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'created_by': self.created_by,
            'created_by_name': names.get(self.created_by, '') if self.created_by else (self.created_by_name or ''),
        }
