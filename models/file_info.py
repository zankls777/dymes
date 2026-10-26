from datetime import datetime
from db import db
from sqlalchemy import Index

class FileInfo(db.Model):
    __tablename__ = 'file_info'
    __table_args__ = (
        db.UniqueConstraint('filename', name='uq_file_info_filename'),
        # 性能优化（H-1）：filename 已有唯一约束 uq_file_info_filename（SQLite 会为其建立
        # 唯一索引），独立的 idx_file_info_filename 完全冗余，删除之。
        Index('idx_file_info_file_type', 'file_type'),
        Index('idx_file_info_first_subpno', 'first_subpno'),
        Index('idx_file_info_eo', 'eo'),
        Index('idx_file_info_type_eo', 'file_type', 'eo'),
        Index('idx_file_info_frozen', 'is_frozen'),  # 冻结状态索引
        Index('idx_file_info_category', 'category'),  # 存储子目录索引
    )

    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50), nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)  # 使用UTC时间存储
    filesize = db.Column(db.Integer, default=0)  # 文件大小使用Integer类型，足够存储大多数文件
    eo = db.Column(db.String(10), nullable=True)  # EO部分通常很短，如 d1, a3, b9 等
    vehicle_type = db.Column(db.String(100), nullable=True)  # 车种部分，如 JA_PE2_FRDR_LH 等
    # 新增：从文件内容首行解析得到的 SubPno（用于判重/展示）
    first_subpno = db.Column(db.String(50), nullable=True)  # SubPno通常较短
    # 新增：物理文件所在子目录（相对 static/uploads），如 SubList / UsageList
    # 早期平铺文件为空，取路径时回退到 uploads 根目录以保持兼容
    category = db.Column(db.String(50), nullable=True)
    # 新增：操作人信息
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 上传人ID
    updated_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 更新人ID
    # 操作人姓名快照：用户账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True, comment='上传人姓名快照')
    updated_by_name = db.Column(db.String(80), nullable=True, comment='更新人姓名快照')

    # 冻结相关字段
    is_frozen = db.Column(db.Boolean, default=False, nullable=False)  # 是否冻结
    frozen_at = db.Column(db.DateTime, nullable=True)  # 冻结时间
    frozen_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)  # 冻结操作人ID
    frozen_by_name = db.Column(db.String(80), nullable=True, comment='冻结操作人姓名快照')  # 冻结人姓名快照
    frozen_reason = db.Column(db.String(500), nullable=True)  # 冻结原因
    
    # 关联用户信息
    # 性能优化：显式 lazy='selectin'，批量列表查询时一次性 IN 预取关联用户，避免逐行 N+1
    creator = db.relationship('UserInfo', foreign_keys=[created_by], lazy='selectin', backref=db.backref('created_files', lazy='dynamic'))
    modifier = db.relationship('UserInfo', foreign_keys=[updated_by], lazy='selectin', backref=db.backref('modified_files', lazy='dynamic'))
    freezer = db.relationship('UserInfo', foreign_keys=[frozen_by], lazy='selectin', backref=db.backref('frozen_files', lazy='dynamic'))
    
    def __repr__(self):
        return f'<FileInfo {self.filename}>'
    
    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'filename': self.filename,
            'file_type': self.file_type,
            'upload_time': self.upload_time.strftime('%Y-%m-%d %H:%M:%S') if self.upload_time else None,
            'filesize': self.filesize,
            'eo': self.eo,
            'vehicle_type': self.vehicle_type,
            'first_subpno': self.first_subpno,
            'category': self.category,
            'created_by': self.created_by,
            'updated_by': self.updated_by,
            # 优先运行时关联用户名，账号已删除时回退到写入时快照
            'created_by_name': (self.creator.username if self.creator else None) or self.created_by_name,
            'updated_by_name': (self.modifier.username if self.modifier else None) or self.updated_by_name,
            'is_frozen': self.is_frozen,
            'frozen_at': self.frozen_at.strftime('%Y-%m-%d %H:%M:%S') if self.frozen_at else None,
            'frozen_by': self.frozen_by,
            'frozen_by_name': (self.freezer.username if self.freezer else None) or self.frozen_by_name,
            'frozen_reason': self.frozen_reason,
        }
