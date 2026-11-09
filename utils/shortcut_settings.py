# -*- coding: utf-8 -*-
"""自定义网址（快捷键）配置

管理员在“快捷键设置”页面维护“固定入口地址 → 目标地址”的映射：
- 固定入口地址（alias）：APK 封装时使用的地址，封装后不再变化；
- 目标地址（target）：随时可在后台调整，调整后固定入口自动跳转到新地址。

配置保存在 system_setting 键值表，因此不依赖前端硬编码或浏览器本地存储。
"""
import json
import logging
import re
import uuid
from urllib.parse import urlparse

from db import db
from models.system_setting import get_setting, set_setting
from utils.time_utils import format_utc8_time, get_utc8_time

logger = logging.getLogger(__name__)

SETTING_KEY = 'web_shortcuts'
SETTING_DESC = '自定义网址映射清单（JSON 数组）'
MAX_ALIAS_LEN = 200
MAX_TARGET_LEN = 500
MAX_REMARK_LEN = 100
ALLOWED_SCHEMES = ('http', 'https')
RESERVED_SEGMENTS = ('api', 'static')      # 系统保留前缀，避免与接口/静态资源冲突
_SCHEME_PREFIX_RE = re.compile(r'^([A-Za-z][A-Za-z0-9+.\-]*):')
# host:port 形式（如 www.baidu.com:8080、localhost:5004）不是协议，需单独识别
_HOST_PORT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.\-]*:\d+(?:[/?#]|$)')


def _scheme_of(text):
    """判断文本是否以“协议://”开头，返回协议名；host:port 形式返回 None。"""
    match = _SCHEME_PREFIX_RE.match(text)
    if not match or _HOST_PORT_RE.match(text):
        return None
    return match.group(1).lower()


def normalize_alias(raw_alias):
    """校验并归一化固定入口地址，只保留路径部分，返回 (alias, error)。

    允许输入完整网址（http://host:port/xidingyi01）或直接输入路径（/xidingyi01），
    统一存成 /xidingyi01，实际访问时按路径匹配跳转。
    """
    text = str(raw_alias or '').strip()
    if not text:
        return None, '请输入自定义网址'
    if len(text) > MAX_ALIAS_LEN:
        return None, f'自定义网址不能超过 {MAX_ALIAS_LEN} 个字符'

    scheme = _scheme_of(text)
    if scheme:
        if scheme not in ALLOWED_SCHEMES or not urlparse(text).netloc:
            return None, '自定义网址必须是 http:// 或 https:// 地址，或 / 开头的路径'
        path = urlparse(text).path
    else:
        path = text

    path = path.split('?', 1)[0].split('#', 1)[0].strip()
    if not path.startswith('/'):
        path = '/' + path
    path = re.sub(r'/+', '/', path).rstrip('/')
    if not path:
        return None, '自定义网址不能是根路径 /'
    if path.split('/')[1].lower() in RESERVED_SEGMENTS:
        return None, '自定义网址不能使用 api / static 等系统保留前缀'
    return path, None


_HOST_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9.\-]*(?::\d+)?$')
_IPV4_HOST_RE = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?$')


def _looks_like_external_host(text):
    """判断输入是否更像外部域名/IP，而不是站内路径。

    规则：首个路径段符合主机名格式，且为域名（含字母+点）、IPv4（数字+点）或带端口。
    例如 www.baidu.com、218.59.244.81:55004 判为外链；/user_management、/mes/production 判为站内路径。
    """
    host = text.lstrip('/').split('/', 1)[0].split('?', 1)[0].split('#', 1)[0]
    if not host or not _HOST_RE.match(host):
        return False
    # 域名（含字母+点）、IPv4（数字+点）或带端口（host:port）都视为站外地址
    return (bool(re.search(r'[A-Za-z]', host)) and '.' in host) or \
        bool(_IPV4_HOST_RE.match(host)) or ':' in host


def normalize_target(raw_target):
    """校验目标地址：支持 http/https 完整网址、裸域名（如 www.baidu.com）或 / 开头的站内路径。

    未带协议的裸域名会自动补 http://，避免被误当成站内路径导致跳回本机 404。
    """
    text = str(raw_target or '').strip()
    if not text:
        return None, '请输入目标网址'
    if len(text) > MAX_TARGET_LEN:
        return None, f'目标网址不能超过 {MAX_TARGET_LEN} 个字符'

    scheme = _scheme_of(text)
    if scheme:
        if scheme not in ALLOWED_SCHEMES or not urlparse(text).netloc:
            return None, '目标网址必须是 http:// 或 https:// 地址、裸域名，或 / 开头的站内路径'
        return text, None

    # 兼容误写成“/https://...”的完整网址，自动去掉多余的前导斜杠
    stripped = text.lstrip('/')
    if stripped != text and _scheme_of(stripped):
        scheme = _scheme_of(stripped)
        if scheme not in ALLOWED_SCHEMES or not urlparse(stripped).netloc:
            return None, '目标网址必须是 http:// 或 https:// 地址、裸域名，或 / 开头的站内路径'
        return stripped, None

    body = text[2:] if text.startswith('//') else text
    if not body:
        return None, '请输入有效目标网址'
    if _looks_like_external_host(body):
        return 'http://' + body.lstrip('/'), None
    if not text.startswith('/'):
        text = '/' + text
    return text, None


def _migrate_legacy(item):
    """兼容旧结构（name=入口网址, url=目标网址）并统一字段名。"""
    alias = item.get('alias')
    if not alias:
        alias = normalize_alias(item.get('name'))[0] or ''
    raw_target = item.get('target') or item.get('url') or ''
    # 兼容历史上被误存为站内路径的裸域名（如 /www.baidu.com），读取时自动纠正为外链
    target = normalize_target(raw_target)[0] or raw_target
    return {
        'id': item.get('id') or uuid.uuid4().hex[:12],
        'alias': alias,
        'target': target,
        'remark': item.get('remark') or '',
        'created_at': item.get('created_at') or '',
        'updated_at': item.get('updated_at') or item.get('created_at') or '',
    }


def load_shortcuts():
    """读取映射清单；配置缺失或损坏时返回空列表，保证页面可用。"""
    raw = get_setting(SETTING_KEY)
    if not raw:
        return []
    try:
        items = json.loads(raw)
    except (TypeError, ValueError):
        logger.error('自定义网址配置解析失败，已按空清单处理')
        return []
    if not isinstance(items, list):
        return []
    result = [_migrate_legacy(item) for item in items if isinstance(item, dict)]
    return [item for item in result if item['target']]


def _save_items(items):
    """序列化写入 system_setting 并提交。"""
    set_setting(SETTING_KEY, json.dumps(items, ensure_ascii=False), SETTING_DESC)
    db.session.commit()


def find_shortcut_by_alias(alias):
    """按固定入口路径查找映射，未命中返回 None。"""
    wanted = str(alias or '').rstrip('/')
    for item in load_shortcuts():
        if item.get('alias') == wanted and item.get('target'):
            return item
    return None


def save_shortcut(payload):
    """新增或更新映射：携带已存在的 id 时更新，否则新增。

    返回 (保存后的条目, 错误信息)，供路由直接转成 JSON 响应。
    """
    alias, error = normalize_alias(payload.get('alias'))
    if error:
        return None, error
    target, error = normalize_target(payload.get('target'))
    if error:
        return None, error

    remark = str(payload.get('remark') or '').strip()[:MAX_REMARK_LEN]
    item_id = str(payload.get('id') or '').strip()
    now = format_utc8_time(get_utc8_time())

    items = load_shortcuts()
    for item in items:
        if item_id and item.get('id') == item_id:
            item.update(alias=alias, target=target, remark=remark, updated_at=now)
            _save_items(items)
            return item, None

    if any(item.get('alias') == alias for item in items):
        return None, '该自定义网址已存在，请更换'

    item = {'id': uuid.uuid4().hex[:12], 'alias': alias, 'target': target,
            'remark': remark, 'created_at': now, 'updated_at': now}
    items.append(item)
    _save_items(items)
    return item, None


def delete_shortcut(item_id):
    """按 id 删除映射，返回是否实际删除。"""
    target_id = str(item_id or '').strip()
    if not target_id:
        return False
    items = load_shortcuts()
    kept = [item for item in items if item.get('id') != target_id]
    if len(kept) == len(items):
        return False
    _save_items(kept)
    return True
