# -*- coding: utf-8 -*-
"""汽车厂装车实际 - 明细记录模型（长期保存，支持按日期/车种/品目筛选汇总）"""
from datetime import datetime
from db import db


class AutoLoadingRecord(db.Model):
    """汽车厂装车实际明细（来源：SAP 원본 Sheet 的「고객 전일실적」列）"""
    __tablename__ = 'auto_loading_record'
    __table_args__ = (
        db.Index('idx_autoload_date', 'record_date'),
        db.Index('idx_autoload_vehicle', 'vehicle'),
        db.Index('idx_autoload_item', 'item'),
        db.Index('idx_autoload_part', 'part_no'),
        db.Index('idx_autoload_source', 'source_file'),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_date = db.Column(db.String(10), nullable=False)   # 业务日期 YYYY-MM-DD
    plant = db.Column(db.String(50), nullable=True)          # HKMC 공장（工厂）
    legacy_vehicle = db.Column(db.String(100), nullable=True)  # Legacy 차종
    vehicle = db.Column(db.String(100), nullable=True)       # 차종（车型）
    item = db.Column(db.String(100), nullable=True)          # 생관 아이템（品目）
    alc = db.Column(db.String(50), nullable=True)            # ALC
    region = db.Column(db.String(50), nullable=True)         # 지역（地区）
    spec = db.Column(db.String(100), nullable=True)          # 사양
    d_type = db.Column(db.String(50), nullable=True)         # D/TYPE
    part_no = db.Column(db.String(200), nullable=True)       # P/NO（品番）
    circuits = db.Column(db.Integer, default=0)              # 회로수（回路数）
    qty = db.Column(db.Integer, default=0)                   # 고객 전일실적（装车实际数量）
    source_file = db.Column(db.String(300), nullable=True)   # 来源文件名
    row_no = db.Column(db.Integer, nullable=True)            # 源表行号
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    def __repr__(self):
        return f'<AutoLoadingRecord {self.record_date} {self.part_no} qty={self.qty}>'
