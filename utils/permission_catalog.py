"""
权限项目录（Permission Catalog）

作用：
    将现有 PermissionInfo 中的 can_* 字段，映射为带「模块 / 页面 / 操作」语义的权限项元数据，
    并在应用启动时可重复执行地同步进 permission 表。

约定：
    - code 与 PermissionInfo 的 can_* 列名严格一致，保证兼容旧数据与旧调用。
    - permission_type 为 page 时表示页面访问权限，action 表示按钮/操作权限。
    - 同一 module_key 的权限项聚合成一个模块卡片，供管理界面分组展示。
"""

# 模块定义：(module_key, module_name, sort_order)
MODULES = [
    ('index', '首页', 10),
    ('file_management', '文件管理', 20),
    ('file_comparison', '文件对比', 30),
    ('sub_list', 'SubList', 40),
    ('usage_list', 'UsageList', 50),
    ('products', '成品管理', 60),
    ('vehicle_types', '车种管理', 70),
    ('material_download', '资材量下载', 80),
    ('rw_comparison', 'RW对比', 90),
    ('material_calculator', '资材换算', 100),
    ('invest_tidy', '投入计划书', 110),
    ('invest_summary', '投资汇总', 120),
    ('auto_loading', '汽车厂装车实际', 130),
    ('data_management', '数据管理', 140),
    ('corrugated_location', '瓦楞纸板位置', 150),
    ('test_sheet', '测试表', 160),
    ('system_management', '系统管理', 170),
    ('user_management', '用户与权限管理', 180),
    ('wire_lot_login', '补线打端子 - LOT登录', 190),
    ('wire_scan_entry', '补线打端子 - 扫描录入', 200),
    ('wire_crimp_login', '补线打端子 - 压高登陆', 205),
    ('wire_data_download', '补线打端子 - 数据下载', 210),
    ('wire_return_confirm', '补线打端子 - 补线返回确认', 215),
    ('material_inbound_daily', '资材每日入库', 220),
    ('material_inbound', '资材入库', 230),
    ('inventory_login', '盘点数据登录', 240),
    ('inventory_check', '盘点实查', 250),
    ('inventory_summary', '盘点数据汇总', 260),
    ('defective_login', '不良品登录', 265),
    ('defective_export', '不良品清单导出', 266),
    ('nas_file_search', 'NAS文件搜索', 270),
    ('mes_group', 'MES分组', 280),
    ('mes_plan', '计划登录', 290),
    ('mes_production', '生产入库', 300),
    ('mes_quality', '品质检查(包装)', 310),
    ('mes_price', '单价登陆', 320),
]

MODULE_NAME_MAP = {key: name for key, name, _ in MODULES}
MODULE_SORT_MAP = {key: order for key, _, order in MODULES}


def _p(code, name, module_key, permission_type, sort_order, remark=None):
    """构造权限项定义"""
    return {
        'code': code,
        'name': name,
        'module_key': module_key,
        'module': MODULE_NAME_MAP.get(module_key, module_key),
        'permission_type': permission_type,
        'sort_order': sort_order,
        'remark': remark,
    }


# 权限项清单：与 PermissionInfo 的 can_* 字段一一对应
PERMISSION_CATALOG = [
    # ---------- 首页 ----------
    _p('can_index', '首页访问', 'index', 'page', 10),

    # ---------- 文件管理 ----------
    _p('can_file_management', '文件管理页面访问', 'file_management', 'page', 10),
    _p('can_file_management_upload', '上传文件', 'file_management', 'action', 20),
    _p('can_file_management_delete', '删除文件', 'file_management', 'action', 30),
    _p('can_file_management_download', '下载文件', 'file_management', 'action', 40),
    _p('can_file_management_export_file_list', '导出文件清单明细', 'file_management', 'action', 50),
    _p('can_file_management_freeze', '冻结/解冻文件', 'file_management', 'action', 60),
    _p('can_upload', '通用上传', 'file_management', 'action', 70, '兼容旧权限项'),
    _p('can_delete', '通用删除', 'file_management', 'action', 80, '兼容旧权限项'),
    _p('can_batch_delete', '通用批量删除', 'file_management', 'action', 90, '兼容旧权限项'),

    # ---------- 文件对比 ----------
    _p('can_file_comparison', '数据对比页面访问', 'file_comparison', 'page', 10),
    _p('can_file_comparison_compare', '执行对比', 'file_comparison', 'action', 20),
    _p('can_file_comparison_download', '下载对比结果', 'file_comparison', 'action', 30),

    # ---------- SubList ----------
    _p('can_sub_list', 'SubList 页面访问', 'sub_list', 'page', 10),
    _p('can_sub_list_add', '新增', 'sub_list', 'action', 20),
    _p('can_sub_list_edit', '编辑', 'sub_list', 'action', 30),
    _p('can_sub_list_delete', '删除', 'sub_list', 'action', 40),
    _p('can_sub_list_download', '下载', 'sub_list', 'action', 50),
    _p('can_sub_list_show_differences', '显示差异', 'sub_list', 'action', 60),

    # ---------- UsageList ----------
    _p('can_usage_list', 'UsageList 页面访问', 'usage_list', 'page', 10),
    _p('can_usage_list_add', '新增', 'usage_list', 'action', 20),
    _p('can_usage_list_edit', '编辑', 'usage_list', 'action', 30),
    _p('can_usage_list_delete', '删除', 'usage_list', 'action', 40),
    _p('can_usage_list_download', '下载', 'usage_list', 'action', 50),

    # ---------- 成品管理 ----------
    _p('can_products', '成品管理页面访问', 'products', 'page', 10),
    _p('can_products_add', '添加成品', 'products', 'action', 20),
    _p('can_products_batch_add', '批量添加', 'products', 'action', 30),
    _p('can_products_edit', '编辑成品', 'products', 'action', 40),
    _p('can_products_delete', '删除成品', 'products', 'action', 50),
    _p('can_products_batch_delete', '批量删除', 'products', 'action', 60),
    _p('can_products_download', '下载成品', 'products', 'action', 70),
    _p('can_products_import', '导入成品', 'products', 'action', 80),

    # ---------- 车种管理 ----------
    _p('can_vehicle_types', '车种管理页面访问', 'vehicle_types', 'page', 10),
    _p('can_vehicle_types_add', '添加车种', 'vehicle_types', 'action', 20),
    _p('can_vehicle_types_batch_add', '批量添加', 'vehicle_types', 'action', 30),
    _p('can_vehicle_types_edit', '编辑车种', 'vehicle_types', 'action', 40),
    _p('can_vehicle_types_delete', '删除车种', 'vehicle_types', 'action', 50),
    _p('can_vehicle_types_batch_delete', '批量删除', 'vehicle_types', 'action', 60),
    _p('can_vehicle_types_download', '下载车种', 'vehicle_types', 'action', 70),
    _p('can_vehicle_types_import_excel', '导入车种(Excel)', 'vehicle_types', 'action', 80),

    # ---------- 资材量下载 ----------
    _p('can_material_download', '资材量下载页面访问', 'material_download', 'page', 10),
    _p('can_material_download_download', '下载资材量', 'material_download', 'action', 20),
    _p('can_material_download_update', '更新资材量', 'material_download', 'action', 30),

    # ---------- RW对比 ----------
    _p('can_rw_comparison', 'RW对比页面访问', 'rw_comparison', 'page', 10),
    _p('can_rw_comparison_compare', '执行对比', 'rw_comparison', 'action', 20),
    _p('can_rw_comparison_download', '下载结果', 'rw_comparison', 'action', 30),

    # ---------- 资材换算 ----------
    _p('can_material_calculator', '资材换算页面访问', 'material_calculator', 'page', 10),

    # ---------- 投入计划书 ----------
    _p('can_invest_tidy', '投入计划书页面访问', 'invest_tidy', 'page', 10),

    # ---------- 投资汇总 ----------
    _p('can_invest_summary', '投资汇总页面访问', 'invest_summary', 'page', 10),

    # ---------- 汽车厂装车实际 ----------
    _p('can_auto_loading', '装车实际页面访问', 'auto_loading', 'page', 10),
    _p('can_auto_loading_upload', '上传', 'auto_loading', 'action', 20),
    _p('can_auto_loading_delete', '删除', 'auto_loading', 'action', 30),
    _p('can_auto_loading_export', '导出', 'auto_loading', 'action', 40),

    # ---------- 数据管理 ----------
    _p('can_data_management', '数据管理页面访问', 'data_management', 'page', 10),

    # ---------- 瓦楞纸板位置 ----------
    _p('can_corrugated_location', '瓦楞纸板位置页面访问', 'corrugated_location', 'page', 10),

    # ---------- 测试表 ----------
    _p('can_test_sheet', '测试表页面访问', 'test_sheet', 'page', 10),

    # ---------- 系统管理 ----------
    _p('can_page', '系统管理菜单显示', 'system_management', 'page', 10),
    _p('can_system_management', '系统管理权限', 'system_management', 'action', 20),

    # ---------- 用户与权限管理 ----------
    _p('can_user_mgmt_add_user', '添加用户', 'user_management', 'action', 10),
    _p('can_user_mgmt_change_password', '修改密码', 'user_management', 'action', 20),
    _p('can_user_mgmt_reset_password', '重置密码', 'user_management', 'action', 30),
    _p('can_user_mgmt_manage_permissions', '管理用户权限', 'user_management', 'action', 40),
    _p('can_user_mgmt_delete_user', '删除用户', 'user_management', 'action', 50),

    # ---------- 补线打端子 - LOT登录 ----------
    _p('can_wire_lot_login', 'LOT登录页面访问', 'wire_lot_login', 'page', 10),
    _p('can_wire_lot_login_add', '新增LOT', 'wire_lot_login', 'action', 20),
    _p('can_wire_lot_login_upload', '上传LOT资料', 'wire_lot_login', 'action', 30),
    _p('can_wire_lot_login_clear', '清空LOT', 'wire_lot_login', 'action', 40),
    _p('can_wire_lot_login_download', '下载LOT', 'wire_lot_login', 'action', 50),

    # ---------- 补线打端子 - 扫描录入 ----------
    _p('can_wire_scan_entry', '扫描录入页面访问', 'wire_scan_entry', 'page', 10),
    _p('can_wire_scan_entry_scan', '扫描', 'wire_scan_entry', 'action', 20),
    _p('can_wire_scan_entry_save', '保存', 'wire_scan_entry', 'action', 30),
    _p('can_wire_scan_entry_edit', '编辑', 'wire_scan_entry', 'action', 40),
    _p('can_wire_scan_entry_delete', '删除', 'wire_scan_entry', 'action', 50),
    _p('can_wire_scan_entry_download', '下载', 'wire_scan_entry', 'action', 60),

    # ---------- 补线打端子 - 压高登陆 ----------
    _p('can_wire_crimp_login', '压高登陆页面访问', 'wire_crimp_login', 'page', 10),
    _p('can_wire_crimp_login_upload', 'Excel批量导入/删除', 'wire_crimp_login', 'action', 20),

    # ---------- 补线打端子 - 数据下载 ----------
    _p('can_wire_data_download', '数据下载页面访问', 'wire_data_download', 'page', 10),
    _p('can_wire_data_download_query', '查询', 'wire_data_download', 'action', 20),
    _p('can_wire_data_download_download', '下载', 'wire_data_download', 'action', 30),
    _p('can_wire_data_download_edit', '编辑', 'wire_data_download', 'action', 40),
    _p('can_wire_data_download_delete', '删除', 'wire_data_download', 'action', 50),

    # ---------- 补线打端子 - 补线返回确认 ----------
    _p('can_wire_return_confirm', '补线返回确认页面访问', 'wire_return_confirm', 'page', 10),
    _p('can_wire_return_confirm_submit', '扫描确认/删除', 'wire_return_confirm', 'action', 20),

    # ---------- 资材每日入库 ----------
    _p('can_material_inbound_daily', '每日入库页面访问', 'material_inbound_daily', 'page', 10),
    _p('can_material_inbound_daily_upload', '上传', 'material_inbound_daily', 'action', 20),
    _p('can_material_inbound_daily_add', '新增', 'material_inbound_daily', 'action', 30),
    _p('can_material_inbound_daily_edit', '编辑', 'material_inbound_daily', 'action', 40),
    _p('can_material_inbound_daily_delete', '删除', 'material_inbound_daily', 'action', 50),
    _p('can_material_inbound_daily_download', '下载', 'material_inbound_daily', 'action', 60),

    # ---------- 资材入库 ----------
    _p('can_material_inbound', '资材入库页面访问', 'material_inbound', 'page', 10),
    _p('can_material_inbound_scan', '扫码入库', 'material_inbound', 'action', 20),
    _p('can_material_inbound_submit', '提交入库', 'material_inbound', 'action', 30),
    _p('can_material_inbound_revoke', '撤销入库', 'material_inbound', 'action', 40),

    # ---------- 盘点数据登录 ----------
    _p('can_inventory_login', '盘点数据登录页面访问', 'inventory_login', 'page', 10),
    _p('can_inventory_login_edit', '编辑盘点班组', 'inventory_login', 'action', 20),

    # ---------- 盘点实查 ----------
    _p('can_inventory_check', '盘点实查页面访问', 'inventory_check', 'page', 10),
    _p('can_inventory_check_submit', '提交盘点实查', 'inventory_check', 'action', 20),

    # ---------- 盘点数据汇总 ----------
    _p('can_inventory_summary', '数据汇总页面访问', 'inventory_summary', 'page', 10),
    _p('can_inventory_summary_delete', '删除盘点记录', 'inventory_summary', 'action', 20),

    # ---------- 不良品登录 ----------
    _p('can_defective_login', '不良品登录页面访问', 'defective_login', 'page', 10),
    _p('can_defective_login_submit', '提交不良品登录', 'defective_login', 'action', 20),
    _p('can_defective_login_delete', '删除不良品记录', 'defective_login', 'action', 30),

    # ---------- 不良品清单导出 ----------
    _p('can_defective_export', '不良品导出页面访问', 'defective_export', 'page', 10),
    _p('can_defective_export_download', '导出不良品清单', 'defective_export', 'action', 20),

    # ---------- NAS文件搜索 ----------
    _p('can_nas_file_search', '页面访问', 'nas_file_search', 'page', 10),
    _p('can_nas_file_search_config', 'NAS 连接配置', 'nas_file_search', 'action', 20),
    _p('can_nas_file_search_search', '上传 Excel 并搜索', 'nas_file_search', 'action', 30),
    _p('can_nas_file_search_import', '导入文件管理', 'nas_file_search', 'action', 40),

    # ---------- MES分组 ----------
    _p('can_mes_group', 'MES分组页面访问', 'mes_group', 'page', 10),
    _p('can_mes_group_add', '新增分组', 'mes_group', 'action', 20),
    _p('can_mes_group_edit', '编辑分组', 'mes_group', 'action', 30),
    _p('can_mes_group_delete', '删除分组', 'mes_group', 'action', 40),
    _p('can_mes_group_download', '下载分组数据', 'mes_group', 'action', 50),
    _p('can_mes_group_unmask', '查看结算单价/金额', 'mes_group', 'action', 60),

    # ---------- 计划登录 ----------
    _p('can_mes_plan', '计划登录页面访问', 'mes_plan', 'page', 10),
    _p('can_mes_plan_add', '新增计划', 'mes_plan', 'action', 20),
    _p('can_mes_plan_edit', '编辑计划', 'mes_plan', 'action', 30),
    _p('can_mes_plan_delete', '删除计划', 'mes_plan', 'action', 40),
    _p('can_mes_plan_download', '导出计划', 'mes_plan', 'action', 50),

    # ---------- 生产入库 ----------
    _p('can_mes_production', '生产入库页面访问', 'mes_production', 'page', 10),
    _p('can_mes_production_add', '新增入库', 'mes_production', 'action', 20),
    _p('can_mes_production_edit', '编辑入库', 'mes_production', 'action', 30),
    _p('can_mes_production_delete', '删除入库', 'mes_production', 'action', 40),
    _p('can_mes_production_download', '导出入库', 'mes_production', 'action', 50),
    _p('can_mes_production_unmask', '查看生产入库单价', 'mes_production', 'action', 60),

    # ---------- 品质检查(包装) ----------
    _p('can_mes_quality', '品质检查页面访问', 'mes_quality', 'page', 10),
    _p('can_mes_quality_add', '新增检查', 'mes_quality', 'action', 20),
    _p('can_mes_quality_edit', '编辑检查', 'mes_quality', 'action', 30),
    _p('can_mes_quality_delete', '删除检查', 'mes_quality', 'action', 40),
    _p('can_mes_quality_download', '导出检查', 'mes_quality', 'action', 50),

    # ---------- 单价登陆 ----------
    _p('can_mes_price', '单价登陆页面访问', 'mes_price', 'page', 10),
    _p('can_mes_price_add', '新增单价', 'mes_price', 'action', 20),
    _p('can_mes_price_edit', '编辑单价', 'mes_price', 'action', 30),
    _p('can_mes_price_delete', '删除单价', 'mes_price', 'action', 40),
    _p('can_mes_price_download', '导出单价', 'mes_price', 'action', 50),
]

# 权限编码集合，供快速校验
PERMISSION_CODES = {item['code'] for item in PERMISSION_CATALOG}

# 页面类权限编码集合
PAGE_PERMISSION_CODES = {item['code'] for item in PERMISSION_CATALOG
                         if item['permission_type'] == 'page'}


def get_catalog_by_module():
    """按模块聚合权限项，返回有序列表，供管理界面分组渲染"""
    grouped = {}
    for item in PERMISSION_CATALOG:
        grouped.setdefault(item['module_key'], []).append(item)

    result = []
    for module_key, module_name, sort_order in MODULES:
        items = grouped.get(module_key)
        if not items:
            continue
        result.append({
            'module_key': module_key,
            'module_name': module_name,
            'sort_order': sort_order,
            'permissions': sorted(items, key=lambda x: x['sort_order']),
        })
    return result
