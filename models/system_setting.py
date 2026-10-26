# -*- coding: utf-8 -*-
"""系统设置 - 键值配置表

用于存放可在页面上维护的系统级配置，例如首页展示的开发人员信息。
以 key/value 形式存储，便于后续扩展其它设置项，无需频繁改表结构。
"""
from datetime import datetime
from db import db


class SystemSetting(db.Model):
    """系统设置项（键值对）"""
    __tablename__ = 'system_setting'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=True)
    description = db.Column(db.String(200), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def __repr__(self):
        return f'<SystemSetting {self.key}>'


# 开发人员信息对应的配置键与默认值
DEVELOPER_SETTING_DEFAULTS = {
    'developer_name': {'value': '张鹏', 'description': '开发人员姓名'},
    'developer_contact': {'value': 'zhu-10@qq.com', 'description': '开发人员联系方式'},
    'developer_feedback_text': {'value': '点击反馈 / 联系开发', 'description': '意见反馈入口显示文字'},
    'developer_feedback_url': {'value': 'mailto:zhu-10@qq.com', 'description': '意见反馈跳转地址'},
}

# 不良品登录 - 是否允许无账号直接访问（'1' 免登录 / '0' 需账号登录）
DEFECTIVE_OPEN_ACCESS_KEY = 'defective_open_access'
DEFECTIVE_OPEN_ACCESS_DEFAULT = '0'


def get_setting(key, default=None):
    """读取单个设置项的值"""
    item = SystemSetting.query.filter_by(key=key).first()
    if item is None or item.value is None:
        return default
    return item.value


def set_setting(key, value, description=None):
    """写入（新增或更新）单个设置项"""
    item = SystemSetting.query.filter_by(key=key).first()
    if item is None:
        item = SystemSetting(key=key, value=value, description=description)
        db.session.add(item)
    else:
        item.value = value
        if description:
            item.description = description
    return item


def get_developer_info():
    """获取开发人员信息，未配置时回落到默认值"""
    return {
        'name': get_setting('developer_name', DEVELOPER_SETTING_DEFAULTS['developer_name']['value']),
        'contact': get_setting('developer_contact', DEVELOPER_SETTING_DEFAULTS['developer_contact']['value']),
        'feedback_text': get_setting('developer_feedback_text', DEVELOPER_SETTING_DEFAULTS['developer_feedback_text']['value']),
        'feedback_url': get_setting('developer_feedback_url', DEVELOPER_SETTING_DEFAULTS['developer_feedback_url']['value']),
    }


def init_developer_settings():
    """初始化开发人员信息默认配置（已存在则跳过，兼容多进程并发启动）"""
    from sqlalchemy.exc import IntegrityError
    for key, meta in DEVELOPER_SETTING_DEFAULTS.items():
        if SystemSetting.query.filter_by(key=key).first() is None:
            db.session.add(SystemSetting(key=key, value=meta['value'], description=meta['description']))
            try:
                db.session.commit()
            except IntegrityError:
                # 其他进程已写入同一键，忽略即可
                db.session.rollback()


def is_defective_open_access():
    """不良品登录是否允许无账号直接访问（默认 False = 需登录）"""
    return get_setting(DEFECTIVE_OPEN_ACCESS_KEY, DEFECTIVE_OPEN_ACCESS_DEFAULT) == '1'


def set_defective_open_access(enabled):
    """设置不良品登录免登录访问开关"""
    set_setting(DEFECTIVE_OPEN_ACCESS_KEY, '1' if enabled else '0', '不良品登录是否免账号访问')
    db.session.commit()


# 系统维护模式 - 开启后普通用户访问任意页面均跳转到维护页（'1' 开启 / '0' 关闭）
MAINTENANCE_MODE_KEY = 'maintenance_mode'
MAINTENANCE_MODE_DEFAULT = '0'


def is_maintenance_mode():
    """系统是否处于维护模式（默认 False = 正常访问）"""
    return get_setting(MAINTENANCE_MODE_KEY, MAINTENANCE_MODE_DEFAULT) == '1'


def set_maintenance_mode(enabled):
    """设置系统维护模式开关"""
    set_setting(MAINTENANCE_MODE_KEY, '1' if enabled else '0', '系统维护模式：开启后普通用户跳转维护页')
    db.session.commit()
