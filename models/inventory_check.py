from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class InventoryTeam(db.Model):
    """盘点班组

    由「盘点数据登录」页面维护（页面2提供快捷编辑入口），页面2「盘点实查」
    的班组下拉从此表读取。
    """
    __tablename__ = 'inventory_team'
    __table_args__ = (
        Index('idx_inventory_team_sort', 'sort_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)   # 班组名称
    sort_no = db.Column(db.Integer, default=0, nullable=False)     # 排序号（越小越靠前）
    remark = db.Column(db.String(200), nullable=True)              # 备注
    is_active = db.Column(db.Boolean, default=True, nullable=False)  # 是否启用
    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    def __repr__(self):
        return f'<InventoryTeam {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'sort_no': self.sort_no or 0,
            'remark': self.remark or '',
            'is_active': bool(self.is_active),
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
        }


class InventoryCheck(db.Model):
    """盘点实查记录

    页面2「盘点实查」提交的现场盘点结果：盘点班组、资材品番(KscPno)、
    盘点数量（现场）、抽查数量（仓库）、差异（自动=盘点数量-抽查数量）、备注。
    为页面3「数据汇总」大屏提供明细数据。
    """
    __tablename__ = 'inventory_check'
    __table_args__ = (
        Index('idx_inventory_check_date', 'check_date'),
        Index('idx_inventory_check_team', 'team_id'),
        Index('idx_inventory_check_ksc', 'ksc_pno'),
        Index('idx_inventory_check_date_team', 'check_date', 'team_id'),
    )

    id = db.Column(db.Integer, primary_key=True)
    check_date = db.Column(db.Date, nullable=False, index=True)          # 盘点日期
    team_id = db.Column(db.Integer, db.ForeignKey('inventory_team.id'), nullable=False)
    team_name = db.Column(db.String(50), nullable=False)                 # 班组名称快照（班组改名后历史记录仍可追溯）
    ksc_pno = db.Column(db.String(50), nullable=False)                   # 资材品番（来源 UsageList.KscPno）
    part_name = db.Column(db.String(100), nullable=True)                 # 品名（选择品番时带出）
    check_qty = db.Column(db.Integer, default=0, nullable=False)         # 盘点数量（现场盘点结果）
    sample_qty = db.Column(db.Integer, default=0, nullable=False)        # 抽查数量（仓库实查数量）
    diff_qty = db.Column(db.Integer, default=0, nullable=False)          # 差异 = 盘点数量 - 抽查数量
    remark = db.Column(db.String(500), nullable=True)                    # 备注

    created_at = db.Column(db.DateTime, default=get_utc8_time)           # 提交时间（UTC+8）
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 提交人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='提交人姓名快照')

    team = db.relationship('InventoryTeam', foreign_keys=[team_id],
                           backref=db.backref('checks', lazy='dynamic'))
    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('inventory_checks', lazy='dynamic'))

    def __repr__(self):
        return f'<InventoryCheck {self.check_date} {self.ksc_pno} {self.diff_qty}>'

    def to_dict(self, operator_names=None):
        names = operator_names or {}
        return {
            'id': self.id,
            'check_date': self.check_date.strftime('%Y-%m-%d') if self.check_date else None,
            'team_id': self.team_id,
            'team_name': self.team_name,
            'ksc_pno': self.ksc_pno,
            'part_name': self.part_name or '',
            'check_qty': self.check_qty or 0,
            'sample_qty': self.sample_qty or 0,
            'diff_qty': self.diff_qty or 0,
            'remark': self.remark or '',
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'created_by': self.created_by,
            # 优先运行时反查姓名；用户已删除（查不到）时回退写入时快照
            'created_by_name': (names.get(self.created_by) if self.created_by else None) or self.created_by_name or '',
        }
