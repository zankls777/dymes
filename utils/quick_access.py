# -*- coding: utf-8 -*-
"""首页「常用功能」卡片定义与访问频率排序

卡片保持与原首页「核心功能入口」一致，仅展示顺序改为按个人使用频率动态调整。
"""
from db import db
from utils.time_utils import get_utc8_time

# 首页功能卡片目录（endpoint 同时作为访问统计的 page_key）
HOME_CARDS = [
    {'key': 'main.sub_list', 'title': 'SubList', 'desc': '部品明细列表查询与下载',
     'icon': 'fa-list-ul', 'color': 'blue', 'perm': 'can_sub_list'},
    {'key': 'main.usage_list', 'title': 'UsageList', 'desc': '使用量清单查询与下载',
     'icon': 'fa-table', 'color': 'green', 'perm': 'can_usage_list'},
    {'key': 'main.file_management', 'title': '文件管理', 'desc': '文件上传、检索与冻结管理',
     'icon': 'fa-folder-open', 'color': 'purple', 'perm': 'can_file_management'},
    {'key': 'main.file_comparison', 'title': '数据差异对比', 'desc': '文件差异 / RW 核对分析',
     'icon': 'fa-code-fork', 'color': 'orange', 'perm': 'can_file_comparison'},
    {'key': 'main.products', 'title': '成品管理', 'desc': '成品信息维护与查询',
     'icon': 'fa-cube', 'color': 'red', 'perm': 'can_products'},
    {'key': 'main.material_download', 'title': '资材量下载', 'desc': '资材用量数据下载',
     'icon': 'fa-download', 'color': 'cyan', 'perm': 'can_material_download'},
    {'key': 'main.tape_calculator', 'title': '资材换算', 'desc': '胶带 / 皮子 / 管换算工具',
     'icon': 'fa-scissors', 'color': 'indigo', 'perm': 'can_material_calculator'},
    {'key': 'main.wire_lot_login', 'title': 'LOT 登录', 'desc': '补线打端子 LOT 登记',
     'icon': 'fa-sign-in', 'color': 'blue', 'perm': 'can_wire_lot_login'},
    {'key': 'main.wire_scan_entry', 'title': '扫描录入', 'desc': '补线数据扫码 / 手动录入',
     'icon': 'fa-barcode', 'color': 'green', 'perm': 'can_wire_scan_entry'},
    {'key': 'main.wire_data_download', 'title': '补线数据下载', 'desc': '历史扫描数据按日期导出',
     'icon': 'fa-file-excel-o', 'color': 'orange', 'perm': 'can_wire_data_download'},
    {'key': 'main.wire_return_confirm', 'title': '补线返回确认', 'desc': '传票扫描核对并登记返回',
     'icon': 'fa-check-square-o', 'color': 'green', 'perm': 'can_wire_return_confirm'},
    {'key': 'main.auto_loading', 'title': '汽车厂装车实际', 'desc': '装车实绩数据上传与统计',
     'icon': 'fa-truck', 'color': 'cyan', 'perm': 'can_auto_loading'},
    {'key': 'main.user_management', 'title': '用户与权限管理', 'desc': '系统用户、角色与权限配置',
     'icon': 'fa-users', 'color': 'purple', 'perm': 'can_system_management'},
    {'key': 'main.operation_logs', 'title': '操作记录', 'desc': '系统操作日志审计查询',
     'icon': 'fa-history', 'color': 'red', 'perm': 'can_system_management'},
]

# 供 before_request 快速判断是否需要统计（避免无关请求查库）
CARD_ENDPOINTS = {c['key'] for c in HOME_CARDS}


def get_home_cards(is_admin, permissions):
    """按权限过滤首页卡片"""
    permissions = permissions or {}
    return [c for c in HOME_CARDS if is_admin or permissions.get(c['perm'])]


def record_visit(user_id, endpoint):
    """记录一次访问（同一用户同一页面累加），失败不影响页面"""
    try:
        from models.page_visit import PageVisitStat
        row = PageVisitStat.query.filter_by(user_id=user_id, page_key=endpoint).first()
        if row is None:
            row = PageVisitStat(user_id=user_id, page_key=endpoint,
                                visit_count=1, last_visit_at=get_utc8_time())
            db.session.add(row)
        else:
            row.visit_count = int(row.visit_count or 0) + 1
            row.last_visit_at = get_utc8_time()
        db.session.commit()
    except Exception:
        # 统计失败静默降级，绝不阻断业务页面
        db.session.rollback()


def sort_cards_by_usage(user_id, cards):
    """按访问频率排序：常访问在前，同频按最近访问；未访问过的保持原顺序排后"""
    try:
        from models.page_visit import PageVisitStat
        stats = {s.page_key: s for s in PageVisitStat.query.filter_by(user_id=user_id).all()}
    except Exception:
        return cards

    def sort_key(pair):
        index, card = pair
        stat = stats.get(card['key'])
        if stat is None:
            return (1, 0, 0, index)   # 未访问过：排后并保持原顺序
        last = stat.last_visit_at.timestamp() if stat.last_visit_at else 0
        return (0, -int(stat.visit_count or 0), -last, index)

    return [c for _, c in sorted(enumerate(cards), key=sort_key)]
