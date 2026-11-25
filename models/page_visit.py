# -*- coding: utf-8 -*-
"""首页「常用功能」访问统计

用途：
    记录用户对各功能页面的访问次数与最近访问时间，首页据此自动排序，
    使用越频繁的功能越靠前，无需手工配置。
"""
from db import db
from sqlalchemy import Index
from utils.time_utils import get_utc8_time


class PageVisitStat(db.Model):
    """用户-页面访问统计（每个用户每个功能一行）"""
    __tablename__ = 'page_visit_stat'
    __table_args__ = (
        # 同一用户同一页面只保留一行，便于按 (user_id, page_key) upsert
        Index('idx_page_visit_user_key', 'user_id', 'page_key', unique=True),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, comment='用户ID')
    page_key = db.Column(db.String(100), nullable=False, comment='页面 endpoint，如 main.sub_list')
    visit_count = db.Column(db.Integer, nullable=False, default=0, comment='累计访问次数')
    last_visit_at = db.Column(db.DateTime, default=get_utc8_time, comment='最近访问时间')
