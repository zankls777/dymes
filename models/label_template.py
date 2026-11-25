# -*- coding: utf-8 -*-
"""标签打印模版

用于「标签打印」页面保存自定义模版（标签尺寸、元素布局、样式）。
config 以 JSON 文本存储，由前端负责解析与渲染，结构示例：

    {
        "width": 60, "height": 40, "border": true,
        "elements": [
            {"id": "e1", "type": "field", "key": "part_no",
             "x": 3, "y": 3, "w": 54, "h": 8,
             "fontSize": 12, "bold": true, "align": "center", "color": "#000000"}
        ]
    }
"""
import json

from db import db
from utils.time_utils import get_utc8_time, format_utc8_time


class LabelTemplate(db.Model):
    """标签打印模版（名称唯一，团队共享）"""
    __tablename__ = 'label_template'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)   # 模版名称
    config = db.Column(db.Text, nullable=True)                     # 模版配置 JSON 文本

    created_at = db.Column(db.DateTime, default=get_utc8_time)
    updated_at = db.Column(db.DateTime, default=get_utc8_time, onupdate=get_utc8_time)
    created_by = db.Column(db.Integer, db.ForeignKey('user_info.id'), nullable=True)
    # 操作人姓名快照：账号删除后仍可显示历史文本
    created_by_name = db.Column(db.String(80), nullable=True)
    updated_by_name = db.Column(db.String(80), nullable=True)

    def __repr__(self):
        return f'<LabelTemplate {self.id} {self.name}>'

    def to_dict(self):
        """转为前端可用的字典，config 直接返回解析后的对象"""
        try:
            cfg = json.loads(self.config) if self.config else {}
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        return {
            'id': self.id,
            'name': self.name or '',
            'config': cfg,
            'created_at': format_utc8_time(self.created_at) if self.created_at else None,
            'updated_at': format_utc8_time(self.updated_at) if self.updated_at else None,
            'created_by_name': self.created_by_name or '',
            'updated_by_name': self.updated_by_name or '',
        }
