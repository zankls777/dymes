# -*- coding: utf-8 -*-
"""投资计划书 - 明细记录模型（长期保存，支持按日期/车种/批次筛选汇总）"""
from datetime import datetime
from db import db


class InvestmentRecord(db.Model):
    """投资计划书每条投入明细（上传解析后入库长期保存）"""
    __tablename__ = 'investment_record'
    __table_args__ = (
        db.Index('idx_invest_date', 'record_date'),
        db.Index('idx_invest_vehicle', 'vehicle'),
        db.Index('idx_invest_batch', 'batch'),
        db.Index('idx_invest_part', 'part_no'),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_date = db.Column(db.String(10), nullable=False)   # 业务日期 YYYY-MM-DD
    batch = db.Column(db.String(10), nullable=True)          # A/B/J/7/G
    vehicle = db.Column(db.String(100), nullable=True)       # 车种
    item = db.Column(db.String(100), nullable=True)          # 品目
    part_no = db.Column(db.String(200), nullable=True)       # 品番
    lot = db.Column(db.String(100), nullable=True)           # LOT/NO
    qty = db.Column(db.Integer, default=0)                   # 投入数量
    circuits = db.Column(db.Integer, default=0)              # 回路数
    total_circuits = db.Column(db.Integer, default=0)        # 合计回路数
    remark = db.Column(db.Text, default="")                  # 备注
    tags_json = db.Column(db.Text, default="[]")             # 标签 JSON
    source_file = db.Column(db.String(300), nullable=True)   # 来源文件名
    row_no = db.Column(db.Integer, nullable=True)            # 源表行号
    uploaded_at = db.Column(db.DateTime, default=datetime.now, nullable=False)

    def __repr__(self):
        return f'<InvestmentRecord {self.record_date} {self.part_no} qty={self.qty}>'