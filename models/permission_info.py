from db import db

class PermissionInfo(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user_info.id'), unique=True, nullable=False)
    
    # 基础权限
    can_upload = db.Column(db.Boolean, default=False)
    can_delete = db.Column(db.Boolean, default=False)
    can_batch_delete = db.Column(db.Boolean, default=False)
    can_page = db.Column(db.Boolean, default=False)
    
    # 页面访问权限
    can_vehicle_types = db.Column(db.Boolean, default=False)
    can_products = db.Column(db.Boolean, default=False)
    can_material_download = db.Column(db.Boolean, default=False)
    can_file_management = db.Column(db.Boolean, default=False)
    can_file_comparison = db.Column(db.Boolean, default=False)
    can_sub_list = db.Column(db.Boolean, default=False)
    can_usage_list = db.Column(db.Boolean, default=False)
    # SubList 扩展按钮权限
    can_sub_list_show_differences = db.Column(db.Boolean, default=False)
    
    # 成品页面按钮权限
    can_products_add = db.Column(db.Boolean, default=False)
    can_products_edit = db.Column(db.Boolean, default=False)
    can_products_delete = db.Column(db.Boolean, default=False)
    can_products_download = db.Column(db.Boolean, default=False)
    can_products_import = db.Column(db.Boolean, default=False)
    
    # 车种页面按钮权限
    can_vehicle_types_add = db.Column(db.Boolean, default=False)
    can_vehicle_types_edit = db.Column(db.Boolean, default=False)
    can_vehicle_types_delete = db.Column(db.Boolean, default=False)
    can_vehicle_types_download = db.Column(db.Boolean, default=False)
    # 车种扩展按钮权限
    can_vehicle_types_batch_add = db.Column(db.Boolean, default=False)
    can_vehicle_types_import_excel = db.Column(db.Boolean, default=False)
    
    # 资材量下载页面按钮权限
    can_material_download_download = db.Column(db.Boolean, default=False)
    can_material_download_update = db.Column(db.Boolean, default=False)
    
    # 文件管理页面按钮权限
    can_file_management_upload = db.Column(db.Boolean, default=False)
    can_file_management_delete = db.Column(db.Boolean, default=False)
    can_file_management_download = db.Column(db.Boolean, default=False)
    # 文件管理：文件清单明细导出
    can_file_management_export_file_list = db.Column(db.Boolean, default=False)
    # 文件管理：冻结/解冻文件
    can_file_management_freeze = db.Column(db.Boolean, default=False)
    
    # 数据对比页面按钮权限
    can_file_comparison_compare = db.Column(db.Boolean, default=False)
    can_file_comparison_download = db.Column(db.Boolean, default=False)
    
    # SubList页面按钮权限
    can_sub_list_add = db.Column(db.Boolean, default=False)
    can_sub_list_edit = db.Column(db.Boolean, default=False)
    can_sub_list_delete = db.Column(db.Boolean, default=False)
    can_sub_list_download = db.Column(db.Boolean, default=False)
    
    # UsageList页面按钮权限
    can_usage_list_add = db.Column(db.Boolean, default=False)
    can_usage_list_edit = db.Column(db.Boolean, default=False)
    can_usage_list_delete = db.Column(db.Boolean, default=False)
    can_usage_list_download = db.Column(db.Boolean, default=False)
    
    # RW对比页面权限
    can_rw_comparison = db.Column(db.Boolean, default=False)
    can_rw_comparison_compare = db.Column(db.Boolean, default=False)
    can_rw_comparison_download = db.Column(db.Boolean, default=False)

    # 车种/成品 批量删除独立权限
    can_vehicle_types_batch_delete = db.Column(db.Boolean, default=False)
    can_products_batch_delete = db.Column(db.Boolean, default=False)
    # 成品批量添加
    can_products_batch_add = db.Column(db.Boolean, default=False)

    # 用户与权限管理页面按钮权限
    can_user_mgmt_add_user = db.Column(db.Boolean, default=False)
    can_user_mgmt_change_password = db.Column(db.Boolean, default=False)
    can_user_mgmt_reset_password = db.Column(db.Boolean, default=False)
    can_user_mgmt_manage_permissions = db.Column(db.Boolean, default=False)
    can_user_mgmt_delete_user = db.Column(db.Boolean, default=False)
    # 系统管理权限
    can_system_management = db.Column(db.Boolean, default=False)

    # 材料计算器页面权限
    can_material_calculator = db.Column(db.Boolean, default=False)

    # 投资整理页面权限
    can_invest_tidy = db.Column(db.Boolean, default=False)

    # 投资汇总页面权限
    can_invest_summary = db.Column(db.Boolean, default=False)

    # 汽车厂装车实际页面权限
    can_auto_loading = db.Column(db.Boolean, default=False)
    can_auto_loading_upload = db.Column(db.Boolean, default=False)
    can_auto_loading_delete = db.Column(db.Boolean, default=False)
    can_auto_loading_export = db.Column(db.Boolean, default=False)

    # 数据管理页面权限
    can_data_management = db.Column(db.Boolean, default=False)

    # 瓦楞纸板位置管理页面权限
    can_corrugated_location = db.Column(db.Boolean, default=False)

    # 测试表页面权限
    can_test_sheet = db.Column(db.Boolean, default=False)

    # 首页/仪表盘权限
    can_index = db.Column(db.Boolean, default=False)

    # 补线打端子 - 页面访问权限
    can_wire_lot_login = db.Column(db.Boolean, default=False)
    can_wire_scan_entry = db.Column(db.Boolean, default=False)
    can_wire_data_download = db.Column(db.Boolean, default=False)
    can_wire_crimp_login = db.Column(db.Boolean, default=False)  # 压高登陆页面

    # 补线打端子 - LOT登录操作权限
    can_wire_lot_login_add = db.Column(db.Boolean, default=False)
    can_wire_lot_login_upload = db.Column(db.Boolean, default=False)
    can_wire_lot_login_clear = db.Column(db.Boolean, default=False)
    can_wire_lot_login_download = db.Column(db.Boolean, default=False)

    # 补线打端子 - 扫描录入操作权限
    can_wire_scan_entry_scan = db.Column(db.Boolean, default=False)
    can_wire_scan_entry_save = db.Column(db.Boolean, default=False)
    can_wire_scan_entry_edit = db.Column(db.Boolean, default=False)
    can_wire_scan_entry_delete = db.Column(db.Boolean, default=False)
    can_wire_scan_entry_download = db.Column(db.Boolean, default=False)

    # 补线打端子 - 压高登陆操作权限
    can_wire_crimp_login_upload = db.Column(db.Boolean, default=False)  # Excel导入/删除

    # 补线打端子 - 数据下载操作权限
    can_wire_data_download_query = db.Column(db.Boolean, default=False)
    can_wire_data_download_download = db.Column(db.Boolean, default=False)
    can_wire_data_download_edit = db.Column(db.Boolean, default=False)
    can_wire_data_download_delete = db.Column(db.Boolean, default=False)

    # 补线打端子 - 补线返回确认
    can_wire_return_confirm = db.Column(db.Boolean, default=False)          # 页面访问
    can_wire_return_confirm_submit = db.Column(db.Boolean, default=False)   # 扫描确认/删除

    # 资材入库 - 页面访问权限
    can_material_inbound_daily = db.Column(db.Boolean, default=False)   # 资材每日入库
    can_material_inbound = db.Column(db.Boolean, default=False)         # 资材入库
    # 资材入库 - 每日入库页面操作权限
    can_material_inbound_daily_upload = db.Column(db.Boolean, default=False)
    can_material_inbound_daily_add = db.Column(db.Boolean, default=False)
    can_material_inbound_daily_edit = db.Column(db.Boolean, default=False)
    can_material_inbound_daily_delete = db.Column(db.Boolean, default=False)
    can_material_inbound_daily_download = db.Column(db.Boolean, default=False)
    # 资材入库 - 入库操作权限
    can_material_inbound_scan = db.Column(db.Boolean, default=False)
    can_material_inbound_submit = db.Column(db.Boolean, default=False)
    can_material_inbound_revoke = db.Column(db.Boolean, default=False)

    # 盘点管理 - 页面访问权限
    can_inventory_login = db.Column(db.Boolean, default=False)      # 盘点数据登录
    can_inventory_check = db.Column(db.Boolean, default=False)      # 盘点实查
    can_inventory_summary = db.Column(db.Boolean, default=False)    # 数据汇总
    # 盘点管理 - 操作权限
    can_inventory_login_edit = db.Column(db.Boolean, default=False)     # 编辑盘点班组
    can_inventory_check_submit = db.Column(db.Boolean, default=False)   # 提交盘点实查
    can_inventory_summary_delete = db.Column(db.Boolean, default=False)  # 删除盘点记录

    # 不良品登录 - 页面访问权限
    can_defective_login = db.Column(db.Boolean, default=False)          # 不良品登录
    # 不良品登录 - 操作权限
    can_defective_login_submit = db.Column(db.Boolean, default=False)   # 提交不良品登录
    can_defective_login_delete = db.Column(db.Boolean, default=False)   # 删除不良品记录

    # 不良品清单导出 - 页面访问权限
    can_defective_export = db.Column(db.Boolean, default=False)         # 不良品导出页面访问
    # 不良品清单导出 - 操作权限
    can_defective_export_download = db.Column(db.Boolean, default=False)  # 导出不良品清单

    # NAS文件搜索 - 页面访问权限
    can_nas_file_search = db.Column(db.Boolean, default=False)
    # NAS文件搜索 - 操作权限
    can_nas_file_search_config = db.Column(db.Boolean, default=False)   # NAS 连接配置
    can_nas_file_search_search = db.Column(db.Boolean, default=False)   # 上传 Excel 并搜索
    can_nas_file_search_import = db.Column(db.Boolean, default=False)   # 导入文件管理

    # MES分组 - 页面访问权限
    can_mes_group = db.Column(db.Boolean, default=False)
    # MES分组 - 操作权限
    can_mes_group_add = db.Column(db.Boolean, default=False)            # 新增分组
    can_mes_group_edit = db.Column(db.Boolean, default=False)           # 编辑分组
    can_mes_group_delete = db.Column(db.Boolean, default=False)         # 删除分组
    can_mes_group_download = db.Column(db.Boolean, default=False)       # 下载分组数据
    can_mes_group_unmask = db.Column(db.Boolean, default=False)         # 查看结算单价/金额（未授权则脱敏）

    # ---------- MES模块（计划登录 / 生产入库 / 品质检查(包装) / 单价登陆） ----------
    # 计划登录
    can_mes_plan = db.Column(db.Boolean, default=False)                 # 计划登录页面访问
    can_mes_plan_add = db.Column(db.Boolean, default=False)
    can_mes_plan_edit = db.Column(db.Boolean, default=False)
    can_mes_plan_delete = db.Column(db.Boolean, default=False)
    can_mes_plan_download = db.Column(db.Boolean, default=False)
    # 生产入库
    can_mes_production = db.Column(db.Boolean, default=False)           # 生产入库页面访问
    can_mes_production_add = db.Column(db.Boolean, default=False)
    can_mes_production_edit = db.Column(db.Boolean, default=False)
    can_mes_production_delete = db.Column(db.Boolean, default=False)
    can_mes_production_download = db.Column(db.Boolean, default=False)
    can_mes_production_unmask = db.Column(db.Boolean, default=False)    # 查看生产入库单价（未授权则脱敏）
    # 品质检查(包装)
    can_mes_quality = db.Column(db.Boolean, default=False)              # 品质检查页面访问
    can_mes_quality_add = db.Column(db.Boolean, default=False)
    can_mes_quality_edit = db.Column(db.Boolean, default=False)
    can_mes_quality_delete = db.Column(db.Boolean, default=False)
    can_mes_quality_download = db.Column(db.Boolean, default=False)
    # 单价登陆
    can_mes_price = db.Column(db.Boolean, default=False)                # 单价登陆页面访问
    can_mes_price_add = db.Column(db.Boolean, default=False)
    can_mes_price_edit = db.Column(db.Boolean, default=False)
    can_mes_price_delete = db.Column(db.Boolean, default=False)
    can_mes_price_download = db.Column(db.Boolean, default=False)

    user = db.relationship('UserInfo', backref=db.backref('permission', uselist=False))

    def __repr__(self):
        return f'<PermissionInfo for {self.user.username}>'
    
    def to_dict(self):
        """转换为字典格式"""
        return {
            'id': self.id,
            'user_id': self.user_id,
            'can_upload': self.can_upload,
            'can_delete': self.can_delete,
            'can_batch_delete': self.can_batch_delete,
            'can_page': self.can_page,
            'can_vehicle_types': self.can_vehicle_types,
            'can_products': self.can_products,
            'can_material_download': self.can_material_download,
            'can_file_management': self.can_file_management,
            'can_file_comparison': self.can_file_comparison,
            'can_sub_list': self.can_sub_list,
            'can_usage_list': self.can_usage_list,
            'can_sub_list_show_differences': self.can_sub_list_show_differences,
            'can_products_add': self.can_products_add,
            'can_products_edit': self.can_products_edit,
            'can_products_delete': self.can_products_delete,
            'can_products_download': self.can_products_download,
            'can_products_import': self.can_products_import,
            'can_vehicle_types_add': self.can_vehicle_types_add,
            'can_vehicle_types_edit': self.can_vehicle_types_edit,
            'can_vehicle_types_delete': self.can_vehicle_types_delete,
            'can_vehicle_types_download': self.can_vehicle_types_download,
            'can_vehicle_types_batch_add': self.can_vehicle_types_batch_add,
            'can_vehicle_types_import_excel': self.can_vehicle_types_import_excel,
            'can_material_download_download': self.can_material_download_download,
            'can_material_download_update': self.can_material_download_update,
            'can_file_management_upload': self.can_file_management_upload,
            'can_file_management_delete': self.can_file_management_delete,
            'can_file_management_download': self.can_file_management_download,
            'can_file_management_export_file_list': self.can_file_management_export_file_list,
            'can_file_management_freeze': self.can_file_management_freeze,
            'can_file_comparison_compare': self.can_file_comparison_compare,
            'can_file_comparison_download': self.can_file_comparison_download,
            'can_sub_list_add': self.can_sub_list_add,
            'can_sub_list_edit': self.can_sub_list_edit,
            'can_sub_list_delete': self.can_sub_list_delete,
            'can_sub_list_download': self.can_sub_list_download,
            'can_usage_list_add': self.can_usage_list_add,
            'can_usage_list_edit': self.can_usage_list_edit,
            'can_usage_list_delete': self.can_usage_list_delete,
            'can_usage_list_download': self.can_usage_list_download,
            'can_rw_comparison': self.can_rw_comparison,
            'can_rw_comparison_compare': self.can_rw_comparison_compare,
            'can_rw_comparison_download': self.can_rw_comparison_download
            ,
            'can_vehicle_types_batch_delete': self.can_vehicle_types_batch_delete,
            'can_products_batch_delete': self.can_products_batch_delete,
            'can_products_batch_add': self.can_products_batch_add,
            'can_user_mgmt_add_user': self.can_user_mgmt_add_user,
            'can_user_mgmt_change_password': self.can_user_mgmt_change_password,
            'can_user_mgmt_reset_password': self.can_user_mgmt_reset_password,
            'can_user_mgmt_manage_permissions': self.can_user_mgmt_manage_permissions,
            'can_user_mgmt_delete_user': self.can_user_mgmt_delete_user,
            'can_system_management': self.can_system_management,
            'can_material_calculator': self.can_material_calculator,
            'can_invest_tidy': self.can_invest_tidy,
            'can_auto_loading': self.can_auto_loading,
            'can_auto_loading_upload': self.can_auto_loading_upload,
            'can_auto_loading_delete': self.can_auto_loading_delete,
            'can_auto_loading_export': self.can_auto_loading_export,
            'can_invest_summary': self.can_invest_summary,
            'can_data_management': self.can_data_management,
            'can_corrugated_location': self.can_corrugated_location,
            'can_test_sheet': self.can_test_sheet,
            'can_index': self.can_index,
            # 补线打端子权限
            'can_wire_lot_login': self.can_wire_lot_login,
            'can_wire_scan_entry': self.can_wire_scan_entry,
            'can_wire_data_download': self.can_wire_data_download,
            'can_wire_crimp_login': self.can_wire_crimp_login,
            'can_wire_crimp_login_upload': self.can_wire_crimp_login_upload,
            'can_wire_lot_login_add': self.can_wire_lot_login_add,
            'can_wire_lot_login_upload': self.can_wire_lot_login_upload,
            'can_wire_lot_login_clear': self.can_wire_lot_login_clear,
            'can_wire_lot_login_download': self.can_wire_lot_login_download,
            'can_wire_scan_entry_scan': self.can_wire_scan_entry_scan,
            'can_wire_scan_entry_save': self.can_wire_scan_entry_save,
            'can_wire_scan_entry_edit': self.can_wire_scan_entry_edit,
            'can_wire_scan_entry_delete': self.can_wire_scan_entry_delete,
            'can_wire_scan_entry_download': self.can_wire_scan_entry_download,
            'can_wire_data_download_query': self.can_wire_data_download_query,
            'can_wire_data_download_download': self.can_wire_data_download_download,
            'can_wire_data_download_edit': self.can_wire_data_download_edit,
            'can_wire_data_download_delete': self.can_wire_data_download_delete,
            # 补线返回确认权限
            'can_wire_return_confirm': self.can_wire_return_confirm,
            'can_wire_return_confirm_submit': self.can_wire_return_confirm_submit,
            # 资材入库权限
            'can_material_inbound_daily': self.can_material_inbound_daily,
            'can_material_inbound': self.can_material_inbound,
            'can_material_inbound_daily_upload': self.can_material_inbound_daily_upload,
            'can_material_inbound_daily_add': self.can_material_inbound_daily_add,
            'can_material_inbound_daily_edit': self.can_material_inbound_daily_edit,
            'can_material_inbound_daily_delete': self.can_material_inbound_daily_delete,
            'can_material_inbound_daily_download': self.can_material_inbound_daily_download,
            'can_material_inbound_scan': self.can_material_inbound_scan,
            'can_material_inbound_submit': self.can_material_inbound_submit,
            'can_material_inbound_revoke': self.can_material_inbound_revoke,
            # 盘点管理权限
            'can_inventory_login': self.can_inventory_login,
            'can_inventory_check': self.can_inventory_check,
            'can_inventory_summary': self.can_inventory_summary,
            'can_inventory_login_edit': self.can_inventory_login_edit,
            'can_inventory_check_submit': self.can_inventory_check_submit,
            'can_inventory_summary_delete': self.can_inventory_summary_delete,
            # 不良品登录权限
            'can_defective_login': self.can_defective_login,
            'can_defective_login_submit': self.can_defective_login_submit,
            'can_defective_login_delete': self.can_defective_login_delete,
            # 不良品清单导出权限
            'can_defective_export': self.can_defective_export,
            'can_defective_export_download': self.can_defective_export_download,
            # NAS文件搜索权限
            'can_nas_file_search': self.can_nas_file_search,
            'can_nas_file_search_config': self.can_nas_file_search_config,
            'can_nas_file_search_search': self.can_nas_file_search_search,
            'can_nas_file_search_import': self.can_nas_file_search_import,
            # MES分组权限
            'can_mes_group': self.can_mes_group,
            'can_mes_group_add': self.can_mes_group_add,
            'can_mes_group_edit': self.can_mes_group_edit,
            'can_mes_group_delete': self.can_mes_group_delete,
            'can_mes_group_download': self.can_mes_group_download,
            'can_mes_group_unmask': self.can_mes_group_unmask,
            # MES模块（计划登录 / 生产入库 / 品质检查(包装) / 单价登陆）
            'can_mes_plan': self.can_mes_plan,
            'can_mes_plan_add': self.can_mes_plan_add,
            'can_mes_plan_edit': self.can_mes_plan_edit,
            'can_mes_plan_delete': self.can_mes_plan_delete,
            'can_mes_plan_download': self.can_mes_plan_download,
            'can_mes_production': self.can_mes_production,
            'can_mes_production_add': self.can_mes_production_add,
            'can_mes_production_edit': self.can_mes_production_edit,
            'can_mes_production_delete': self.can_mes_production_delete,
            'can_mes_production_download': self.can_mes_production_download,
            'can_mes_production_unmask': self.can_mes_production_unmask,
            'can_mes_quality': self.can_mes_quality,
            'can_mes_quality_add': self.can_mes_quality_add,
            'can_mes_quality_edit': self.can_mes_quality_edit,
            'can_mes_quality_delete': self.can_mes_quality_delete,
            'can_mes_quality_download': self.can_mes_quality_download,
            'can_mes_price': self.can_mes_price,
            'can_mes_price_add': self.can_mes_price_add,
            'can_mes_price_edit': self.can_mes_price_edit,
            'can_mes_price_delete': self.can_mes_price_delete,
            'can_mes_price_download': self.can_mes_price_download
        }