# -*- coding: utf-8 -*-
"""数据管理 - 模型定义

支持 CSV / Excel / JSON 批量上传，按业务日期（record_date）逐日归档存储。
每个上传文件形成一个上传批次（UploadBatch），文件内每行生成一条通用数据记录（DataRecord）。
"""
from datetime import datetime
from db import db
from sqlalchemy import Index


class DataUploadBatch(db.Model):
    """数据管理 - 上传批次（每次上传一个文件对应一个批次）"""
    __tablename__ = 'data_upload_batch'
    __table_args__ = (
        Index('idx_upload_batch_time', 'uploaded_at'),
        Index('idx_upload_batch_status', 'status'),
    )

    id = db.Column(db.Integer, primary_key=True)
    batch_no = db.Column(db.String(40), nullable=False, unique=True)  # 批次号（日期+时间戳）
    file_name = db.Column(db.String(300), nullable=False)             # 上传文件原名
    file_type = db.Column(db.String(20), nullable=False)              # csv / excel / json
    category = db.Column(db.String(100), nullable=True)               # 数据类别（可空，页面/文件名指定）
    row_count = db.Column(db.Integer, default=0)                      # 入库记录数
    error_count = db.Column(db.Integer, default=0)                    # 校验失败行数
    status = db.Column(db.String(20), default='success')              # success / partial / failed
    summary = db.Column(db.Text, nullable=True)                       # 解析摘要（列名、抽样数据）
    error_log = db.Column(db.Text, nullable=True)                     # 详细校验错误日志
    uploaded_at = db.Column(db.DateTime, default=datetime.now)        # 上传时间
    uploaded_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 上传人姓名快照：账号删除后记录仍可显示文本
    uploaded_by_name = db.Column(db.String(80), nullable=True, comment='上传人姓名快照')

    uploader = db.relationship('UserInfo', foreign_keys=[uploaded_by],
                               backref=db.backref('data_upload_batches', lazy='dynamic'))

    def __repr__(self):
        return f'<DataUploadBatch {self.batch_no} {self.file_name} {self.status}>'


class DataRecord(db.Model):
    """数据管理 - 通用数据记录（按业务日期归档）

    任意结构的数据行统一存储为「列名 JSON + 行值 JSON」，查询/导出时动态平铺为表格。
    """
    __tablename__ = 'data_record'
    __table_args__ = (
        Index('idx_data_record_date', 'record_date'),
        Index('idx_data_record_category', 'category'),
        Index('idx_data_record_batch', 'batch_id'),
        Index('idx_data_record_name', 'record_key'),
    )

    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('data_upload_batch.id'), nullable=False)
    record_date = db.Column(db.String(10), nullable=False)           # 业务日期 YYYY-MM-DD（按日归档）
    category = db.Column(db.String(100), nullable=True)              # 数据类别
    record_key = db.Column(db.String(200), nullable=True)            # 行主键/标识（第一列值或指定键列）
    keys_json = db.Column(db.Text, nullable=False)                   # 列名数组 JSON
    values_json = db.Column(db.Text, nullable=False)                 # 行值数组 JSON（与 keys 对齐）
    source_file = db.Column(db.String(300), nullable=False)          # 来源文件名
    row_no = db.Column(db.Integer, nullable=True)                    # 源文件中的行号
    uploaded_at = db.Column(db.DateTime, default=datetime.now)       # 入库时间（UTC 一致使用本地时间）

    def __repr__(self):
        return f'<DataRecord {self.record_date} {self.category} {self.record_key}>'