from datetime import datetime
from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time, format_utc8_time


class WireScanRecord(db.Model):
    """补线打端子 - 扫描录入记录

    第一次扫描（回路号识别）：写入 lot_no + circuit_no（同 LOT 仅一条回路行）。
    第二次扫描（号机识别）：在 LOT 下追加 machine_no 明细行，补线数量默认 0，可增减修改。
    号机允许重复录入（同一 LOT 下同一号机可有多条记录）。
    """
    __tablename__ = 'wire_scan_record'
    __table_args__ = (
        Index('idx_wire_scan_lot_no', 'lot_no'),
        Index('idx_wire_scan_lot_circuit', 'lot_no', 'circuit_no'),
        Index('idx_wire_scan_created_at', 'created_at'),
    )

    id = db.Column(db.Integer, primary_key=True)
    lot_no = db.Column(db.String(100), nullable=False)      # LOT（固定长度，如9位 Q817A03Q）
    circuit_no = db.Column(db.String(100), nullable=True)   # 回路号（第一次扫描）
    machine_no = db.Column(db.String(100), nullable=True)   # 号机（第二次扫描，如 M-35$139）
    wire_qty = db.Column(db.Integer, default=0)             # 补线数量（默认0，可增减修改）
    # LOT 关联信息快照（保存/扫描时从 lot_login_info 固化为文本，
    # 原始 LOT 登录资料删除后仍可显示，下载页无需再实时关联）
    part_no = db.Column(db.String(100), nullable=True)      # 品番（快照）
    jx_eo = db.Column(db.String(50), nullable=True)         # 京信EO（快照）
    vehicle_type = db.Column(db.String(100), nullable=True) # 车种别（快照）
    created_at = db.Column(db.DateTime, default=get_utc8_time)  # 录入时间（UTC+8）
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 录入人ID
    # 录入人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='录入人姓名快照')
    # 备注：同图纸回路扫描不全时自动补录的占位行标记为「录入不全」
    remark = db.Column(db.String(200), nullable=True, comment='备注（录入不全等）')

    creator = db.relationship('UserInfo', foreign_keys=[created_by],
                              backref=db.backref('wire_scan_records', lazy='dynamic'))

    def __repr__(self):
        return f'<WireScanRecord {self.lot_no}/{self.machine_no}>'

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'lot_no': self.lot_no,
            'circuit_no': self.circuit_no,
            'machine_no': self.machine_no,
            'wire_qty': self.wire_qty,
            'part_no': self.part_no,
            'jx_eo': self.jx_eo,
            'vehicle_type': self.vehicle_type,
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'created_by': self.created_by,
            # 优先运行时关联用户名；账号已删除时回退写入时快照
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name or '',
            'remark': self.remark or '',
        }
