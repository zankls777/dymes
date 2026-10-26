# -*- coding: utf-8 -*-
"""波纹管位置 - 模型定义

包含两个表：
1. CorrugatedLocation：波纹管位置数据记录（上传解析后的每行数据）。
   动态列结构存储（keys_json / values_json），与数据管理模块一致，便于弹性适配 Excel 列。
2. CorrugatedColumnConfig：查询页后台列配置（管理员自定义显示列、启停、顺序、别名）。
"""
from datetime import datetime
from db import db


class CorrugatedLocation(db.Model):
    """波纹管位置 - 数据记录"""
    __tablename__ = 'corrugated_location'
    __table_args__ = (
        db.Index('idx_corrugated_date', 'record_date'),
        db.Index('idx_corrugated_key', 'record_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    record_date = db.Column(db.String(10), nullable=False)      # 业务日期 YYYY-MM-DD
    record_key = db.Column(db.String(200), nullable=True)       # 行标识（第一列值）
    keys_json = db.Column(db.Text, nullable=False)              # 列名数组 JSON
    values_json = db.Column(db.Text, nullable=False)            # 行值数组 JSON（与 keys 对齐）
    source_file = db.Column(db.String(300), nullable=True)      # 来源文件
    row_no = db.Column(db.Integer, nullable=True)               # 源文件行号
    uploaded_at = db.Column(db.DateTime, default=datetime.now)  # 入库时间
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<CorrugatedLocation {self.record_date} {self.record_key}>'


class CorrugatedColumnConfig(db.Model):
    """波纹管位置查询页 - 列配置（后台可自定义显示列）"""
    __tablename__ = 'corrugated_column_config'
    __table_args__ = (
        db.Index('idx_corrugated_cfg_order', 'sort_order'),
    )

    id = db.Column(db.Integer, primary_key=True)
    field_key = db.Column(db.String(100), nullable=False)       # 字段名（Excel 列名）
    display_name = db.Column(db.String(100), nullable=True)     # 页面显示名称（为空则用字段名）
    is_visible = db.Column(db.Boolean, default=True)            # 是否在查询页显示
    sort_order = db.Column(db.Integer, default=0)               # 显示顺序
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<CorrugatedColumnConfig {self.field_key} visible={self.is_visible} order={self.sort_order}>'