# -*- coding: utf-8 -*-
"""侧边栏菜单目录与分组配置

职责：
1) 内置菜单目录（页面 + 可展开条目），作为唯一数据源，避免模板里散落硬编码；
2) 分组布局可在“菜单管理”页由管理员调整，保存在 system_setting 键值表中；
3) 按登录用户权限过滤后输出给模板渲染，无权限的页面不展示。

说明：
- 配置只保存“分组标题 + 条目归属 + 顺序”，页面标题/图标/权限仍以内置目录为准，
  这样新增页面或修改权限时无需迁移历史配置。
- 配置损坏、条目缺失时自动回落到默认布局，保证侧边栏不会出现空菜单。
"""
import json
import logging

from flask import url_for

from db import db
from models.system_setting import SystemSetting, get_setting, set_setting

logger = logging.getLogger(__name__)

SETTING_KEY = 'sidebar_menu_layout'
SETTING_DESC = '侧边栏分组配置（JSON：分组标题与条目归属）'

# 页面定义：标题、端点、标签页标题、所需权限字段（None 表示登录后可见）
PAGE_DEFS = {
    'sub_list': {'title': 'SubList', 'endpoint': 'main.sub_list', 'tab': 'SubList', 'perm': 'can_sub_list'},
    'usage_list': {'title': 'UsageList', 'endpoint': 'main.usage_list', 'tab': 'UsageList', 'perm': 'can_usage_list'},
    'file_management': {'title': '文件管理', 'endpoint': 'main.file_management', 'tab': '文件管理', 'perm': 'can_file_management'},
    'nas_search': {'title': 'NAS文件搜索', 'endpoint': 'nas.file_search_page', 'tab': 'NAS文件搜索', 'perm': 'can_nas_file_search'},
    'file_comparison': {'title': '数据差异对比', 'endpoint': 'main.file_comparison', 'tab': '数据差异对比', 'perm': 'can_file_comparison'},
    'rw_comparison': {'title': 'RW核对', 'endpoint': 'main.rw_comparison', 'tab': 'RW核对', 'perm': 'can_file_comparison'},
    'rw_cad': {'title': 'RW图纸产出', 'endpoint': 'cad.rw_cad_page', 'tab': 'RW图纸产出', 'perm': 'can_file_comparison'},
    'products': {'title': '成品', 'endpoint': 'main.products', 'tab': '成品', 'perm': 'can_products'},
    'material_download': {'title': '资材量下载', 'endpoint': 'main.material_download', 'tab': '资材量下载', 'perm': 'can_material_download'},
    'tape_calculator': {'title': '胶带换算', 'endpoint': 'main.tape_calculator', 'tab': '胶带换算', 'perm': 'can_material_calculator'},
    'sheet_calculator': {'title': '皮子换算', 'endpoint': 'main.sheet_calculator', 'tab': '皮子换算', 'perm': 'can_material_calculator'},
    'tube_calculator': {'title': '管换算', 'endpoint': 'main.tube_calculator', 'tab': '管换算', 'perm': 'can_material_calculator'},
    'wire_lot_login': {'title': 'LOT登录', 'endpoint': 'main.wire_lot_login', 'tab': 'LOT登录', 'perm': 'can_wire_lot_login'},
    'wire_scan_entry': {'title': '扫描录入', 'endpoint': 'main.wire_scan_entry', 'tab': '扫描录入', 'perm': 'can_wire_scan_entry'},
    'wire_data_download': {'title': '数据下载', 'endpoint': 'main.wire_data_download', 'tab': '数据下载', 'perm': 'can_wire_data_download'},
    'wire_crimp_login': {'title': '压高登陆', 'endpoint': 'main.wire_crimp_login', 'tab': '压高登陆', 'perm': 'can_wire_crimp_login'},
    'material_inbound_daily': {'title': '资材入库登录', 'endpoint': 'main.material_inbound_daily', 'tab': '资材入库登录', 'perm': 'can_material_inbound_daily'},
    'material_inbound': {'title': '仓库资材入库(移动端)', 'endpoint': 'main.material_inbound', 'tab': '仓库资材入库', 'perm': 'can_material_inbound'},
    'corrugated_location': {'title': '波纹管位置录入', 'endpoint': 'main.corrugated_location', 'tab': '波纹管位置录入', 'perm': 'can_corrugated_location'},
    'corrugated_location_search': {'title': '波纹管查询(移动端)', 'endpoint': 'main.corrugated_location_search', 'tab': '波纹管查询', 'perm': 'can_corrugated_location'},
    'inventory_login': {'title': '盘点数据登录', 'endpoint': 'main.inventory_login', 'tab': '盘点数据登录', 'perm': 'can_inventory_login'},
    'inventory_check': {'title': '盘点实查（移动端）', 'endpoint': 'main.inventory_check', 'tab': '盘点实查', 'perm': 'can_inventory_check'},
    'defective_login': {'title': '不良品登录(移动端)', 'endpoint': 'main.defective_login_page', 'tab': '不良品登录', 'perm': 'can_defective_login'},
    'defective_export': {'title': '不良品清单导出', 'endpoint': 'main.defective_export_page', 'tab': '不良品清单导出', 'perm': 'can_defective_export'},
    'inventory_summary': {'title': '数据汇总', 'endpoint': 'main.inventory_summary', 'tab': '数据汇总', 'perm': 'can_inventory_summary'},
    'invest_tidy': {'title': '投入计划书', 'endpoint': 'main.invest_tidy', 'tab': '投入计划书', 'perm': 'can_invest_tidy'},
    'auto_loading': {'title': '汽车厂装车实际', 'endpoint': 'main.auto_loading', 'tab': '汽车厂装车实际', 'perm': 'can_auto_loading'},
    'mes_plan': {'title': '计划登录', 'endpoint': 'mes_mod.plan_page', 'tab': '计划登录', 'perm': 'can_mes_plan'},
    'mes_production': {'title': '生产入库', 'endpoint': 'mes_mod.production_page', 'tab': '生产入库', 'perm': 'can_mes_production'},
    'mes_quality': {'title': '品质检查(包装)', 'endpoint': 'mes_mod.quality_page', 'tab': '品质检查(包装)', 'perm': 'can_mes_quality'},
    'mes_price': {'title': '单价登陆', 'endpoint': 'mes_mod.price_page', 'tab': '单价登陆', 'perm': 'can_mes_price'},
    'mes_group': {'title': 'MES分组', 'endpoint': 'mes.group_page', 'tab': 'MES分组', 'perm': 'can_mes_group'},
    'user_management': {'title': '用户与权限管理', 'endpoint': 'main.user_management', 'tab': '用户与权限管理', 'perm': 'can_system_management'},
    'operation_logs': {'title': '操作记录', 'endpoint': 'main.operation_logs', 'tab': '操作记录', 'perm': 'can_system_management'},
    'data_management': {'title': '数据备份管理', 'endpoint': 'main.data_management', 'tab': '数据备份管理', 'perm': 'can_data_management'},
    'menu_management': {'title': '菜单管理', 'endpoint': 'main.menu_management', 'tab': '菜单管理', 'perm': 'can_page'},
}

# 一级条目定义：page=直接跳转页面；children=可展开的子菜单页面
ENTRY_DEFS = {
    'lists': {'title': '清单列表', 'icon': 'fa-list-ul', 'children': ['sub_list', 'usage_list']},
    'file_management': {'title': '文件管理', 'icon': 'fa-folder', 'page': 'file_management'},
    'nas_search': {'title': 'NAS文件搜索', 'icon': 'fa-server', 'page': 'nas_search'},
    'compare': {'title': '数据对比', 'icon': 'fa-code-fork', 'children': ['file_comparison', 'rw_comparison', 'rw_cad']},
    'products': {'title': '成品', 'icon': 'fa-cube', 'page': 'products'},
    'material_download': {'title': '资材量下载', 'icon': 'fa-download', 'page': 'material_download'},
    'material_calc': {'title': '资材换算', 'icon': 'fa-calculator', 'children': ['tape_calculator', 'sheet_calculator', 'tube_calculator']},
    'material': {'title': '资材管理', 'icon': 'fa-cubes', 'children': ['material_inbound_daily', 'material_inbound', 'corrugated_location', 'corrugated_location_search']},
    'inventory': {'title': '盘点查询', 'icon': 'fa-clipboard', 'children': ['inventory_login', 'inventory_check', 'defective_login', 'defective_export', 'inventory_summary']},
    'wire': {'title': '补线打端子', 'icon': 'fa-scissors', 'children': ['wire_lot_login', 'wire_scan_entry', 'wire_data_download', 'wire_crimp_login']},
    'invest_tidy': {'title': '投入计划书', 'icon': 'fa-file-excel-o', 'page': 'invest_tidy'},
    'auto_loading': {'title': '汽车厂装车实际', 'icon': 'fa-truck', 'page': 'auto_loading'},
    'mes': {'title': 'MES', 'icon': 'fa-industry', 'children': ['mes_plan', 'mes_production', 'mes_quality', 'mes_price', 'mes_group']},
    'user_management': {'title': '用户与权限管理', 'icon': 'fa-users', 'page': 'user_management'},
    'operation_logs': {'title': '操作记录', 'icon': 'fa-history', 'page': 'operation_logs'},
    'data_management': {'title': '数据备份管理', 'icon': 'fa-database', 'page': 'data_management'},
    'menu_management': {'title': '菜单管理', 'icon': 'fa-list', 'page': 'menu_management'},
}

# 默认分组：按实际业务域归类（管理员可在菜单管理页调整）
DEFAULT_GROUPS = [
    {'key': 'data', 'title': '资料与文件', 'entries': ['lists', 'file_management', 'nas_search', 'compare']},
    {'key': 'product', 'title': '成品管理', 'entries': ['products']},
    {'key': 'material', 'title': '资材与盘点', 'entries': ['material_download', 'material_calc', 'material', 'inventory']},
    {'key': 'wire', 'title': '补线打端子', 'entries': ['wire']},
    {'key': 'production', 'title': '生产与 MES', 'entries': ['invest_tidy', 'auto_loading', 'mes']},
    {'key': 'system', 'title': '系统管理', 'entries': ['user_management', 'operation_logs', 'data_management', 'menu_management']},
]

MAX_GROUPS = 12          # 分组数量上限，避免侧边栏过长
MAX_TITLE_LEN = 20       # 分组标题长度上限


def _default_layout():
    """返回默认布局的深拷贝，避免调用方修改常量。"""
    return {'groups': [{'key': g['key'], 'title': g['title'], 'entries': list(g['entries'])} for g in DEFAULT_GROUPS]}


def _clean_title(value, fallback='未命名分组'):
    """清洗分组标题：去空白、限长、空值回落。"""
    title = str(value or '').strip()
    return title[:MAX_TITLE_LEN] if title else fallback


def normalize_layout(raw):
    """校验并补全布局配置。

    - 非法分组/非法条目直接丢弃，条目去重；
    - 目录中存在但配置里缺失的条目，按默认分组补回（默认分组不存在时放到最后一个分组），
      保证新增菜单或历史配置不会导致菜单“凭空消失”。
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('groups'), list):
        return _default_layout()

    groups, seen_groups, seen_entries = [], set(), set()
    for idx, group in enumerate(raw['groups']):
        if not isinstance(group, dict) or len(groups) >= MAX_GROUPS:
            continue
        key = str(group.get('key') or f'g{idx + 1}')[:40]
        if key in seen_groups:
            key = f'{key}_{idx}'
        entries = []
        for entry_key in group.get('entries') or []:
            if entry_key in ENTRY_DEFS and entry_key not in seen_entries:
                entries.append(entry_key)
                seen_entries.add(entry_key)
        groups.append({'key': key, 'title': _clean_title(group.get('title')), 'entries': entries})
        seen_groups.add(key)

    if not groups:
        return _default_layout()

    default_group_of = {ek: g['key'] for g in DEFAULT_GROUPS for ek in g['entries']}
    for entry_key in ENTRY_DEFS:
        if entry_key in seen_entries:
            continue
        target = next((g for g in groups if g['key'] == default_group_of.get(entry_key)), groups[-1])
        target['entries'].append(entry_key)
    return {'groups': groups}


def load_layout():
    """读取布局配置，异常/空配置回落到默认布局。"""
    try:
        raw = get_setting(SETTING_KEY)
        return normalize_layout(json.loads(raw)) if raw else _default_layout()
    except Exception as exc:
        logger.error('读取侧边栏布局失败: %s', exc)
        return _default_layout()


def save_layout(payload):
    """保存布局配置（先规范化再落库），返回保存后的布局。"""
    layout = normalize_layout(payload)
    set_setting(SETTING_KEY, json.dumps(layout, ensure_ascii=False), SETTING_DESC)
    db.session.commit()
    return layout


def reset_layout():
    """恢复默认分组。"""
    SystemSetting.query.filter_by(key=SETTING_KEY).delete()
    db.session.commit()
    return _default_layout()


def _page_allowed(page_key, is_admin, permissions):
    """判断页面是否对当前用户可见（管理员全可见）。"""
    page = PAGE_DEFS.get(page_key)
    if not page:
        return False
    perm = page.get('perm')
    return bool(is_admin or not perm or permissions.get(perm))


def _safe_url(endpoint):
    """端点缺失时返回 None，避免单个菜单异常导致整页 500。"""
    try:
        return url_for(endpoint)
    except Exception as exc:
        logger.warning('侧边栏菜单端点不存在: %s (%s)', endpoint, exc)
        return None


def _build_entry(entry_key, is_admin, permissions):
    """构建单个一级条目（可直接跳转或带子菜单），无可见内容时返回 None。"""
    entry = ENTRY_DEFS.get(entry_key)
    if not entry:
        return None

    if 'children' in entry:
        children = []
        for page_key in entry['children']:
            if not _page_allowed(page_key, is_admin, permissions):
                continue
            page = PAGE_DEFS[page_key]
            url = _safe_url(page['endpoint'])
            if not url:
                continue
            children.append({'key': page_key, 'title': page['title'], 'url': url,
                             'tab': page.get('tab') or page['title']})
        if not children:
            return None
        return {'key': entry_key, 'type': 'sub', 'title': entry['title'],
                'icon': entry['icon'], 'children': children}

    page_key = entry.get('page')
    if not _page_allowed(page_key, is_admin, permissions):
        return None
    page = PAGE_DEFS[page_key]
    url = _safe_url(page['endpoint'])
    if not url:
        return None
    return {'key': entry_key, 'type': 'link', 'title': entry['title'], 'icon': entry['icon'],
            'url': url, 'tab': page.get('tab') or page['title']}


def build_sidebar_menu(is_admin, permissions):
    """按权限过滤并生成模板所需的侧边栏结构。"""
    permissions = permissions or {}
    groups = []
    for group in load_layout()['groups']:
        items = [_build_entry(ek, is_admin, permissions) for ek in group['entries']]
        items = [item for item in items if item]
        if items:
            groups.append({'key': group['key'], 'title': group['title'], 'entries': items})
    return groups


def get_admin_catalog():
    """菜单管理页所需的目录信息（条目标题/图标/子项摘要）。"""
    entries = []
    for key, entry in ENTRY_DEFS.items():
        item = {'key': key, 'title': entry['title'], 'icon': entry['icon'],
                'kind': 'sub' if 'children' in entry else 'link'}
        if 'children' in entry:
            item['summary'] = ' / '.join(PAGE_DEFS[p]['title'] for p in entry['children'] if p in PAGE_DEFS)
        else:
            item['summary'] = PAGE_DEFS.get(entry.get('page'), {}).get('title', '')
        entries.append(item)
    return {'entries': entries}
