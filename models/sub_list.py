from db import db
from datetime import datetime
from utils.time_utils import get_utc8_time, format_utc8_time


class SubList(db.Model):
    # 设置表名（修正缩进）
    __tablename__ = 'sub_list'
    __table_args__ = (
        db.Index('idx_sub_list_subpno', 'SubPno'),
        db.Index('idx_sub_list_eoksc', 'EoKsc'),
        db.Index('idx_sub_list_vehicle_subpno', 'VehicleType', 'SubPno'),
        db.Index('idx_sub_list_subpno_eoksc', 'SubPno', 'EoKsc'),
        db.Index('idx_sub_list_setcode_circuit', 'SetCode', 'Circuit'),
        db.Index('idx_sub_list_wire_wirecode', 'Wire', 'WireCode'),
        # 性能优化（H-1）：移除 idx_sub_list_circuit / idx_sub_list_wire，二者分别是
        # idx_sub_list_setcode_circuit(SetCode,Circuit) / idx_sub_list_wire_wirecode(Wire,WireCode)
        # 的前缀列，独立单列索引被完全覆盖，属冗余。
        # 另移除 idx_sub_list_vehicle_eoksc：与 idx_sub_list_vehicle_subpno 均以 VehicleType 打头，
        # （车种,EO）查询可由（车种,品番）索引扫描后回表过滤，功能完全等价。
        db.Index('idx_sub_list_wirecode', 'WireCode'),
        db.Index('idx_sub_list_color', 'Color'),
        db.Index('idx_sub_list_sq', 'SQ'),
        db.Index('idx_sub_list_tml1_tml2', 'Tml1', 'Tml2'),
        db.Index('idx_sub_list_rel1_rel2', 'Rel1', 'Rel2'),
        db.Index('idx_sub_list_seal1_seal2', 'Seal1', 'Seal2'),
        db.Index('idx_sub_list_file', 'source_file'),
        db.Index('idx_sub_list_deleted_upload_time', 'deleted', 'upload_time'),
        # 性能优化（L-11）：RW 对比选项接口按 (SubPno,EoKsc,VehicleType) 做 DISTINCT/GROUP BY，
        # 并聚合 COUNT(id)/MIN(is_frozen)。附带 is_frozen 使该索引成为覆盖索引，
        # 扫描索引即可完成分组与聚合，无需回表读取 183 万行。
        db.Index('idx_sub_list_pno_eo_vehicle', 'SubPno', 'EoKsc', 'VehicleType', 'is_frozen'),
    )

    id = db.Column(db.Integer, primary_key=True)

    # 基础信息
    SubPno = db.Column(db.String(50), nullable=False)
    EoKsc = db.Column(db.String(50), nullable=False)
    VehicleType = db.Column(db.String(100), index=True)

    # 分组信息
    GroupNo = db.Column(db.String(50))
    GroupOrder = db.Column(db.String(50))

    # 编码信息
    SetCode = db.Column(db.String(50))
    CutCode = db.Column(db.String(50))

    # 电路和电线信息
    Circuit = db.Column(db.String(50))
    Wire = db.Column(db.String(50))
    WireCode = db.Column(db.String(50))
    Color = db.Column(db.String(50))
    SQ = db.Column(db.String(50))

    # 长度信息
    Length_Cut = db.Column(db.String(50))
    Length_Cut2 = db.Column(db.String(50))

    # 端子信息
    Tml1 = db.Column(db.String(50))
    Tml2 = db.Column(db.String(50))

    # 剥线信息
    Strip1 = db.Column(db.String(50))
    Strip2 = db.Column(db.String(50))

    # 继电器信息
    Rel1 = db.Column(db.String(50))
    Rel2 = db.Column(db.String(50))

    # 密封信息
    Seal1 = db.Column(db.String(50))
    Seal2 = db.Column(db.String(50))

    # 组件信息
    SubComp1 = db.Column(db.String(50))
    SubComp2 = db.Column(db.String(50))

    # 标记信息
    M_ST1 = db.Column(db.String(50))
    M_ST2 = db.Column(db.String(50))
    M_ST3 = db.Column(db.String(50))
    M_ST4 = db.Column(db.String(50))

    # 连接点和备注信息
    Joint_Points = db.Column(db.String(50))
    Remark1 = db.Column(db.String(200))
    Remark2 = db.Column(db.String(200))

    # 来源文件信息
    source_file = db.Column(db.String(100), nullable=False)

    # 添加软删除字段
    deleted = db.Column(db.Boolean, default=False)

    # 冻结状态字段（与FileInfo冻结状态同步）
    is_frozen = db.Column(db.Boolean, default=False, nullable=False, index=True)

    # 修正日期时间字段（使用utcnow而不是now）
    upload_time = db.Column(db.DateTime, default=get_utc8_time)

    def __repr__(self):
        return f'<SubList {self.SubPno}>'

    def to_dict(self):
        """将模型转换为字典格式"""
        return {
            'id': self.id,
            'SubPno': self.SubPno,
            'EoKsc': self.EoKsc,
            'VehicleType': self.VehicleType,
            'GroupNo': self.GroupNo,
            'GroupOrder': self.GroupOrder,
            'SetCode': self.SetCode,
            'CutCode': self.CutCode,
            'Circuit': self.Circuit,
            'Wire': self.Wire,
            'WireCode': self.WireCode,
            'Color': self.Color,
            'SQ': self.SQ,
            'Length_Cut': self.Length_Cut,
            'Length_Cut2': self.Length_Cut2,
            'Tml1': self.Tml1,
            'Tml2': self.Tml2,
            'Strip1': self.Strip1,
            'Strip2': self.Strip2,
            'Rel1': self.Rel1,
            'Rel2': self.Rel2,
            'Seal1': self.Seal1,
            'Seal2': self.Seal2,
            'SubComp1': self.SubComp1,
            'SubComp2': self.SubComp2,
            'M_ST1': self.M_ST1,
            'M_ST2': self.M_ST2,
            'M_ST3': self.M_ST3,
            'M_ST4': self.M_ST4,
            'Joint_Points': self.Joint_Points,
            'Remark1': self.Remark1,
            'Remark2': self.Remark2,
            'source_file': self.source_file,
            'deleted': self.deleted,
            'is_frozen': self.is_frozen,
            'upload_time': self.upload_time.isoformat() if self.upload_time else None
        }