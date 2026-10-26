from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class MesGroup(db.Model):
    """MES 分组主数据

    业务分组规则维护：将 usage_list / sub_list 中已有的维度字段
    （车种 VehicleType、品番 KscPno/SubPno、EO EoKsc、部位 Comp_Kind）
    组合成一个 MES 分组，供后续归类、查询与统计使用。

    字段均为现有表字段的引用快照，不做冗余数据复制：
    - vehicle_type  -> UsageList.VehicleType
    - ksc_pno       -> UsageList.KscPno
    - sub_pno       -> UsageList.SubPno / SubList.SubPno
    - eo_ksc        -> UsageList.EoKsc / SubList.EoKsc
    - comp_kind     -> UsageList.Comp_Kind
    为空表示该维度不作为分组条件（即通配）。
    """
    __tablename__ = 'mes_group'
    __table_args__ = (
        Index('idx_mes_group_code', 'group_code', unique=True),
        Index('idx_mes_group_active', 'is_active'),
    )

    id = db.Column(db.Integer, primary_key=True)
    group_code = db.Column(db.String(50), nullable=False, unique=True)   # 分组编号
    group_name = db.Column(db.String(100), nullable=False)               # 分组名称

    # 分组维度（关联现有表字段，空=通配）
    vehicle_type = db.Column(db.String(100), nullable=True)              # 车种
    ksc_pno = db.Column(db.String(50), nullable=True)                    # 资材品番
    sub_pno = db.Column(db.String(50), nullable=True)                    # 制造品番
    eo_ksc = db.Column(db.String(50), nullable=True)                     # EO/KSC
    comp_kind = db.Column(db.String(50), nullable=True)                  # 部位类别

    sort_no = db.Column(db.Integer, default=0, nullable=False)           # 排序号（越小越靠前）
    remark = db.Column(db.String(500), nullable=True)                     # 备注
    is_active = db.Column(db.Boolean, default=True, nullable=False)       # 是否启用

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 创建人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='创建人姓名快照')
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('mes_groups', lazy='dynamic'))

    def __repr__(self):
        return f'<MesGroup {self.group_code} {self.group_name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'group_code': self.group_code,
            'group_name': self.group_name,
            'vehicle_type': self.vehicle_type or '',
            'ksc_pno': self.ksc_pno or '',
            'sub_pno': self.sub_pno or '',
            'eo_ksc': self.eo_ksc or '',
            'comp_kind': self.comp_kind or '',
            'sort_no': self.sort_no or 0,
            'remark': self.remark or '',
            'is_active': bool(self.is_active),
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by': self.created_by,
            # 优先运行时关联用户名；账号已删除时回退写入时快照
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name or '',
        }
