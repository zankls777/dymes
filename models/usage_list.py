# models/usage_list.py
from db import db
from datetime import datetime
from utils.time_utils import get_utc8_time, format_utc8_time

class UsageList(db.Model):
    __tablename__ = 'usage_list'
    __table_args__ = (
        db.Index('idx_usage_list_subpno', 'SubPno'),
        db.Index('idx_usage_list_eoksc', 'EoKsc'),
        db.Index('idx_usage_list_vehicle_subpno', 'VehicleType', 'SubPno'),
        db.Index('idx_usage_list_subpno_eoksc', 'SubPno', 'EoKsc'),
        db.Index('idx_usage_list_comp_detail', 'Comp_Kind', 'Detail_Kind'),
        db.Index('idx_usage_list_kscpno', 'KscPno'),
        db.Index('idx_usage_list_file', 'source_file'),
        db.Index('idx_usage_list_deleted_upload_time', 'deleted', 'upload_time'),
        # 性能优化（H-1）：移除 idx_usage_list_vehicle_eoksc（VehicleType 打头，被
        # idx_usage_list_vehicle_subpno 覆盖）；移除低区分度、几乎不被查询命中的
        # idx_usage_list_part_name / idx_usage_list_usage / idx_usage_list_length
        # （字段基数极低，索引收益低于其维护与存储成本）。
    )
    id = db.Column(db.Integer, primary_key=True)
    SubPno = db.Column(db.String(50), nullable=False)
    EoKsc = db.Column(db.String(50), nullable=False)
    # 新增：车种（从文件名解析，例如 JA_PE2_FRDR_LH_91608-G6550_a3_UsageList.xls -> JA_PE2_FRDR_LH）
    VehicleType = db.Column(db.String(100), index=True)
    Comp_Kind = db.Column(db.String(50))
    Detail_Kind = db.Column(db.String(50))
    KscPno = db.Column(db.String(50))
    Part_Name = db.Column(db.String(50))
    Usage = db.Column(db.String(50))
    Length = db.Column(db.String(50))
    source_file = db.Column(db.String(100), nullable=False)
    deleted = db.Column(db.Boolean, default=False)  # 确保存在
    # 冻结状态字段（与FileInfo冻结状态同步）
    is_frozen = db.Column(db.Boolean, default=False, nullable=False, index=True)
    upload_time = db.Column(db.DateTime, default=get_utc8_time)  # 确保与查询中的字段一致

    def to_dict(self):
        return {
            'id': self.id,
            'SubPno': self.SubPno,
            'EoKsc': self.EoKsc,
            'VehicleType': self.VehicleType,
            'Comp_Kind': self.Comp_Kind,
            'Detail_Kind': self.Detail_Kind,
            'KscPno': self.KscPno,
            'Part_Name': self.Part_Name,
            'Usage': self.Usage,
            'Length': self.Length,
            'source_file': self.source_file,
            'deleted': self.deleted,
            'is_frozen': self.is_frozen,
            'upload_time': format_utc8_time(self.upload_time) if self.upload_time else None
        }