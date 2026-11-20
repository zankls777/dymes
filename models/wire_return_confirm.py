from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class WireReturnConfirm(db.Model):
    """补线返回确认 - 传票扫描确认记录

    扫描传票得到 LOT + 回路号后，与「数据下载」页(扫描录入数据)比对：
    - 比对一致：拉出相关信息并记录 matched=True；
    - 无对应数据：仅保存 LOT + 回路号（数量选填），matched=False。
    同一 LOT + 回路号只保留一条确认记录（重复确认时更新数量）。
    """
    __tablename__ = 'wire_return_confirm'
    __table_args__ = (
        # 同一 LOT + 回路号只允许入库一次（回路号大小写不敏感）
        Index('uq_wrc_lot_circuit', 'lot_no', 'circuit_no', unique=True),
        Index('idx_wrc_lot', 'lot_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.String(100), nullable=False)       # LOT
    circuit_no = db.Column(db.String(100, collation='NOCASE'), nullable=False)  # 回路号（忽略大小写）
    wire_qty = db.Column(db.Integer, nullable=True)          # 返回数量（选填）
    matched = db.Column(db.Boolean, default=False)           # 是否与扫描录入数据匹配
    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)
    created_by = db.Column(db.Integer, nullable=True)
    created_by_name = db.Column(db.String(80), nullable=True)

    def __repr__(self):
        return f'<WireReturnConfirm {self.lot_no}/{self.circuit_no}>'

    def to_dict(self):
        return {
            'id': self.id,
            'lot_no': self.lot_no or '',
            'circuit_no': self.circuit_no or '',
            'wire_qty': self.wire_qty,
            'matched': bool(self.matched),
            'created_at': format_utc8_time(self.created_at) if self.created_at else '',
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else '',
            'created_by': self.created_by,
            'created_by_name': self.created_by_name or '',
        }
