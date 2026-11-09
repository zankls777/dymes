# -*- coding: utf-8 -*-
"""数据库结构升级 / 初始化（可复用，幂等）

背景
----
恢复 .db 快照会把整库覆盖为「备份时点」的结构。若恢复的是旧快照，
而系统此后新增了表 / 字段 / 索引或默认数据，恢复后将缺失这些内容，
导致新功能不可用（如「列不存在」「表不存在」）。

本模块把 app_factory 启动时的兼容性迁移逻辑抽成可复用函数，
既能被启动流程调用，也能在「数据恢复完成后」立即调用，确保：
    1. 建表  ：db.create_all() 补建快照中缺失的新表
    2. 补列  ：为已有表补齐新增字段（ALTER TABLE ADD COLUMN）
    3. 索引  ：重建业务/部分索引（CREATE INDEX IF NOT EXISTS）
    4. 默认数据：RBAC 权限目录与内置角色、系统设置默认值

幂等性：全部步骤均可重复执行，已存在则不重复处理。
兼容性：仅依赖 SQLAlchemy / 标准库，Windows 7/10 与 Linux 通用。
"""
import logging

from db import db

logger = logging.getLogger('schema_upgrade')

# ---------------------------------------------------------------------------
# 新增字段清单：{表名: [(列名, 列定义SQL), ...]}
# 新功能新增字段时，只需在此登记即可在恢复后自动补齐。
# ---------------------------------------------------------------------------
COLUMN_UPGRADES = {
    'permission_info': [
        # MES 分组
        ('can_mes_group', 'BOOLEAN DEFAULT 0'),
        ('can_mes_group_add', 'BOOLEAN DEFAULT 0'),
        ('can_mes_group_edit', 'BOOLEAN DEFAULT 0'),
        ('can_mes_group_delete', 'BOOLEAN DEFAULT 0'),
        ('can_mes_group_download', 'BOOLEAN DEFAULT 0'),
        ('can_mes_group_unmask', 'BOOLEAN DEFAULT 0'),
        # MES 模块（计划登录 / 生产入库 / 品质检查 / 单价登陆）
        ('can_mes_plan', 'BOOLEAN DEFAULT 0'),
        ('can_mes_plan_add', 'BOOLEAN DEFAULT 0'),
        ('can_mes_plan_edit', 'BOOLEAN DEFAULT 0'),
        ('can_mes_plan_delete', 'BOOLEAN DEFAULT 0'),
        ('can_mes_plan_download', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production_add', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production_edit', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production_delete', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production_download', 'BOOLEAN DEFAULT 0'),
        ('can_mes_production_unmask', 'BOOLEAN DEFAULT 0'),
        ('can_mes_quality', 'BOOLEAN DEFAULT 0'),
        ('can_mes_quality_add', 'BOOLEAN DEFAULT 0'),
        ('can_mes_quality_edit', 'BOOLEAN DEFAULT 0'),
        ('can_mes_quality_delete', 'BOOLEAN DEFAULT 0'),
        ('can_mes_quality_download', 'BOOLEAN DEFAULT 0'),
        ('can_mes_price', 'BOOLEAN DEFAULT 0'),
        ('can_mes_price_add', 'BOOLEAN DEFAULT 0'),
        ('can_mes_price_edit', 'BOOLEAN DEFAULT 0'),
        ('can_mes_price_delete', 'BOOLEAN DEFAULT 0'),
        ('can_mes_price_download', 'BOOLEAN DEFAULT 0'),
        # 文件管理 / 其他页面访问权限
        ('can_file_management_export_file_list', 'BOOLEAN DEFAULT 0'),
        ('can_file_management_freeze', 'BOOLEAN DEFAULT 0'),
        ('can_material_calculator', 'BOOLEAN DEFAULT 0'),
        ('can_invest_tidy', 'BOOLEAN DEFAULT 0'),
        ('can_invest_summary', 'BOOLEAN DEFAULT 0'),
        ('can_auto_loading', 'BOOLEAN DEFAULT 0'),
        ('can_auto_loading_upload', 'BOOLEAN DEFAULT 0'),
        ('can_auto_loading_delete', 'BOOLEAN DEFAULT 0'),
        ('can_auto_loading_export', 'BOOLEAN DEFAULT 0'),
        ('can_data_management', 'BOOLEAN DEFAULT 0'),
        ('can_corrugated_location', 'BOOLEAN DEFAULT 0'),
        ('can_test_sheet', 'BOOLEAN DEFAULT 0'),
        ('can_index', 'BOOLEAN DEFAULT 0'),
        # 补线打端子
        ('can_wire_lot_login', 'BOOLEAN DEFAULT 0'),
        ('can_wire_lot_login_add', 'BOOLEAN DEFAULT 0'),
        ('can_wire_lot_login_upload', 'BOOLEAN DEFAULT 0'),
        ('can_wire_lot_login_clear', 'BOOLEAN DEFAULT 0'),
        ('can_wire_lot_login_download', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry_scan', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry_save', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry_edit', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry_delete', 'BOOLEAN DEFAULT 0'),
        ('can_wire_scan_entry_download', 'BOOLEAN DEFAULT 0'),
        # 压高登陆：页面访问 + Excel 导入/删除
        ('can_wire_crimp_login', 'BOOLEAN DEFAULT 0'),
        ('can_wire_crimp_login_upload', 'BOOLEAN DEFAULT 0'),
        ('can_wire_data_download', 'BOOLEAN DEFAULT 0'),
        ('can_wire_data_download_query', 'BOOLEAN DEFAULT 0'),
        ('can_wire_data_download_download', 'BOOLEAN DEFAULT 0'),
        ('can_wire_data_download_edit', 'BOOLEAN DEFAULT 0'),
        ('can_wire_data_download_delete', 'BOOLEAN DEFAULT 0'),
        # 资材入库
        ('can_material_inbound_daily', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_daily_upload', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_daily_add', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_daily_edit', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_daily_delete', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_daily_download', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_scan', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_submit', 'BOOLEAN DEFAULT 0'),
        ('can_material_inbound_revoke', 'BOOLEAN DEFAULT 0'),
        # 盘点管理
        ('can_inventory_login', 'BOOLEAN DEFAULT 0'),
        ('can_inventory_login_edit', 'BOOLEAN DEFAULT 0'),
        ('can_inventory_check', 'BOOLEAN DEFAULT 0'),
        ('can_inventory_check_submit', 'BOOLEAN DEFAULT 0'),
        ('can_inventory_summary', 'BOOLEAN DEFAULT 0'),
        ('can_inventory_summary_delete', 'BOOLEAN DEFAULT 0'),
        # 不良品登录 / 导出
        ('can_defective_login', 'BOOLEAN DEFAULT 0'),
        ('can_defective_login_submit', 'BOOLEAN DEFAULT 0'),
        ('can_defective_login_delete', 'BOOLEAN DEFAULT 0'),
        ('can_defective_export', 'BOOLEAN DEFAULT 0'),
        ('can_defective_export_download', 'BOOLEAN DEFAULT 0'),
        # NAS 文件搜索
        ('can_nas_file_search', 'BOOLEAN DEFAULT 0'),
        ('can_nas_file_search_config', 'BOOLEAN DEFAULT 0'),
        ('can_nas_file_search_search', 'BOOLEAN DEFAULT 0'),
        ('can_nas_file_search_import', 'BOOLEAN DEFAULT 0'),
    ],
    'user_info': [
        ('is_active', 'BOOLEAN NOT NULL DEFAULT 1'),
        # 用户基本信息扩展
        ('full_name', 'VARCHAR(80)'),
        ('department', 'VARCHAR(80)'),
        # 是否作业者：用于限定生产入库页面的作业者下拉候选
        ('is_operator', 'BOOLEAN NOT NULL DEFAULT 0'),
    ],
    'file_info': [
        ('first_subpno', 'VARCHAR(100)'),
        ('is_frozen', 'BOOLEAN DEFAULT 0'),
        ('frozen_at', 'DATETIME'),
        ('frozen_by', 'INTEGER'),
        ('frozen_reason', 'VARCHAR(500)'),
        # 操作人姓名快照（上传人/更新人/冻结人），用户删除后仍可显示文本
        ('created_by_name', 'VARCHAR(80)'),
        ('updated_by_name', 'VARCHAR(80)'),
        ('frozen_by_name', 'VARCHAR(80)'),
    ],
    # ---- 操作人姓名快照列（用户账号删除后仍可显示历史操作人文本）----
    # operation_logs 需保留历史操作记录：新增 username 文本快照
    'operation_logs': [
        ('username', 'VARCHAR(80)'),
    ],
    # 各业务表操作人姓名快照列
    'plan_login': [
        ('lot_no', 'VARCHAR(100)'),
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'production_inbound': [
        ('lot_no', 'VARCHAR(100)'),
        ('operator', 'VARCHAR(50)'),
        ('unit_price', 'NUMERIC(12, 4) NOT NULL DEFAULT 0'),
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'quality_check': [
        ('lot_no', 'VARCHAR(100)'),
        ('check_at', 'DATETIME'),
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'unit_price': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'customer': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'mes_group': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'inventory_check': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'wire_scan_record': [
        ('part_no', 'VARCHAR(100)'),
        ('jx_eo', 'VARCHAR(50)'),
        ('vehicle_type', 'VARCHAR(100)'),
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'wire_scan_detail': [
        # 图纸编号 SetCode 快照：用于同图纸兄弟回路兜底查询时按 SetCode 精确过滤，
        # 避免返回同 LOT 下不同图纸的无关回路
        ('set_code', 'VARCHAR(50)'),
    ],
    'wire_crimp_login': [
        # 匹配键扩展：线质（如 AVSS/CIVUS），加入后旧数据将被清空由用户重新上传
        ('wire', 'VARCHAR(50) NOT NULL DEFAULT ""'),
    ],
    'material_inbound_daily': [
        ('inbound_by_name', 'VARCHAR(80)'),
    ],
    # 补线打端子 LOT 登录 / 不良品登录 / 数据管理上传批次
    'lot_login_info': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'defective_login': [
        ('created_by_name', 'VARCHAR(80)'),
    ],
    'data_upload_batch': [
        ('uploaded_by_name', 'VARCHAR(80)'),
    ],
    'usage_list': [
        ('VehicleType', 'VARCHAR(100)'),
        ('is_frozen', 'BOOLEAN NOT NULL DEFAULT 0'),
    ],
    'sub_list': [
        ('VehicleType', 'VARCHAR(100)'),
        ('is_frozen', 'BOOLEAN NOT NULL DEFAULT 0'),
    ],
    'finished_product': [
        ('eo', 'VARCHAR(100)'),
        ('eo_override', 'BOOLEAN DEFAULT 0'),
        ('usage_eo', 'VARCHAR(100)'),
        ('needs_eo_attention', 'BOOLEAN DEFAULT 0'),
        ('created_by_name', 'VARCHAR(80)'),
        ('updated_by_name', 'VARCHAR(80)'),
    ],
    'vehicle_type': [
        ('created_by_name', 'VARCHAR(80)'),
        ('updated_by_name', 'VARCHAR(80)'),
    ],
}

# ---------------------------------------------------------------------------
# 索引清单：CREATE INDEX IF NOT EXISTS 语句（幂等）
# ---------------------------------------------------------------------------
INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_wire_scan_lot_circuit "
    "ON wire_scan_record(lot_no, circuit_no)",
    "CREATE INDEX IF NOT EXISTS idx_wsd_lot_circuit "
    "ON wire_scan_detail(lot_no, circuit_no)",
    "CREATE INDEX IF NOT EXISTS idx_wsd_part_eo_circuit "
    "ON wire_scan_detail(part_no, jx_eo, circuit_no)",
    "CREATE INDEX IF NOT EXISTS idx_wsd_lot_setcode "
    "ON wire_scan_detail(lot_no, set_code)",
    # 活跃数据部分索引（deleted=0 AND is_frozen=0）
    "CREATE INDEX IF NOT EXISTS idx_sub_list_active "
    "ON sub_list(deleted, is_frozen) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_active "
    "ON usage_list(deleted, is_frozen) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_subpno_eoksc_active "
    "ON sub_list(SubPno, EoKsc) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_subpno_eoksc_active "
    "ON usage_list(SubPno, EoKsc) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_subpno_active "
    "ON sub_list(SubPno COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_eoksc_active "
    "ON sub_list(EoKsc COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_subpno_active "
    "ON usage_list(SubPno COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_eoksc_active "
    "ON usage_list(EoKsc COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_vehicle_active "
    "ON sub_list(VehicleType COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_vehicle_active "
    "ON usage_list(VehicleType COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_circuit_active "
    "ON sub_list(Circuit COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_setcode_active "
    "ON sub_list(SetCode COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_comp_kind_active "
    "ON usage_list(Comp_Kind COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_kscpno_active "
    "ON usage_list(KscPno COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_source_file_status "
    "ON sub_list(source_file, is_frozen)",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_source_file_status "
    "ON usage_list(source_file, is_frozen)",
    # M-14：文件详情取数按 (source_file, deleted) 过滤，补复合索引命中该访问模式
    "CREATE INDEX IF NOT EXISTS idx_sub_list_source_file_deleted "
    "ON sub_list(source_file, deleted)",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_source_file_deleted "
    "ON usage_list(source_file, deleted)",
    # L-11：RW 对比选项接口按 (SubPno,EoKsc,VehicleType) 分组去重并聚合 COUNT/MIN(is_frozen)，
    # 附带 is_frozen 构成覆盖索引，扫描索引即可完成，无需回表。
    "CREATE INDEX IF NOT EXISTS idx_sub_list_pno_eo_vehicle "
    "ON sub_list(SubPno, EoKsc, VehicleType, is_frozen) WHERE deleted=0",
    # ------------------------------------------------------------------
    # 高频单字段等值搜索补充 NOCASE 部分索引。
    # 搜索逻辑用 column.collate('NOCASE') == value 做精确探测后再 LIKE 兜底，
    # 而原有 Wire/Tml1 等索引是 BINARY 排序规则的全表索引，NOCASE 比较无法复用，
    # 导致这些字段的等值搜索退化为百万行全表扫描。以下索引与 (deleted=0 AND
    # is_frozen=0) 的过滤条件完全匹配，可被等值探测直接命中。
    # ------------------------------------------------------------------
    "CREATE INDEX IF NOT EXISTS idx_sub_list_wire_nc_active "
    "ON sub_list(Wire COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_wirecode_nc_active "
    "ON sub_list(WireCode COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_tml1_nc_active "
    "ON sub_list(Tml1 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_tml2_nc_active "
    "ON sub_list(Tml2 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_length2_nc_active "
    "ON sub_list(Length_Cut2 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_detail_kind_nc_active "
    "ON usage_list(Detail_Kind COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_partname_nc_active "
    "ON usage_list(Part_Name COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_usage_nc_active "
    "ON usage_list(Usage COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    # 列表页搜索表单里其余可搜索字段同样需要 NOCASE 活跃部分索引：
    # 缺少索引时『%x%』子串搜索会退化为 2.8GB 全表扫描（冷缓存实测 70s+），
    # 强制索引扫描后只读几十 MB 的索引树。
    "CREATE INDEX IF NOT EXISTS idx_sub_list_color_nc_active "
    "ON sub_list(Color COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_sq_nc_active "
    "ON sub_list(SQ COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_length1_nc_active "
    "ON sub_list(Length_Cut COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_rel1_nc_active "
    "ON sub_list(Rel1 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_rel2_nc_active "
    "ON sub_list(Rel2 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_seal1_nc_active "
    "ON sub_list(Seal1 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_seal2_nc_active "
    "ON sub_list(Seal2 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_sub_list_remark1_nc_active "
    "ON sub_list(Remark1 COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
    "CREATE INDEX IF NOT EXISTS idx_usage_list_length_nc_active "
    "ON usage_list(Length COLLATE NOCASE) WHERE deleted=0 AND is_frozen=0",
]


# ---------------------------------------------------------------------------
# 冗余索引清理清单（H-1）：DROP INDEX IF EXISTS 语句（幂等）
# 判定依据：前缀列被复合索引完全覆盖、或与唯一约束重复、或低区分度字段收益低于维护成本。
# ---------------------------------------------------------------------------
REDUNDANT_INDEX_DROPS = [
    # 单列索引是其复合索引的前缀列，被完全覆盖
    "DROP INDEX IF EXISTS idx_sub_list_circuit",          # ⊆ idx_sub_list_setcode_circuit(SetCode,Circuit)
    "DROP INDEX IF EXISTS idx_sub_list_wire",             # ⊆ idx_sub_list_wire_wirecode(Wire,WireCode)
    "DROP INDEX IF EXISTS idx_sub_list_vehicle_eoksc",    # VehicleType 打头，被 idx_sub_list_vehicle_subpno 覆盖
    "DROP INDEX IF EXISTS idx_usage_list_vehicle_eoksc",  # 同上（usage_list）
    # filename 已有唯一约束索引 uq_file_info_filename，独立索引冗余
    "DROP INDEX IF EXISTS idx_file_info_filename",
    # 低区分度、几乎不被查询命中的索引，收益低于存储与写入维护成本
    "DROP INDEX IF EXISTS idx_usage_list_part_name",
    "DROP INDEX IF EXISTS idx_usage_list_usage",
    "DROP INDEX IF EXISTS idx_usage_list_length",
]


# ---------------------------------------------------------------------------
# 废弃列清理清单：ALTER TABLE ... DROP COLUMN（幂等，需 SQLite 3.35+）
# 判定依据：字段已从模型移除且无业务引用，残留列徒增库体积与理解成本。
# ---------------------------------------------------------------------------
LEGACY_COLUMN_DROPS = [
    ('production_inbound', 'team'),      # 生产班组（已废弃）
    ('production_inbound', 'location'),  # 库位（已废弃）
    ('production_inbound', 'status'),    # 状态（已废弃）
]


def _drop_legacy_columns(stat):
    """删除模型已移除的残留列，幂等且失败不致命。

    SQLite 3.35+ 支持 DROP COLUMN；失败（如版本过低/列不存在）仅记录日志，
    不影响主流程，避免升级中断。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    removed = []
    for table, column in LEGACY_COLUMN_DROPS:
        try:
            # 仅当列确实存在时才执行，避免无谓报错
            if column not in {c['name'] for c in inspector.get_columns(table)}:
                continue
            # 先删除依赖该列的残留索引（如 idx_prod_inbound_status），否则 DROP COLUMN 会失败
            for idx in inspector.get_indexes(table):
                if column in (idx.get('column_names') or []):
                    db.session.execute(text(f'DROP INDEX IF EXISTS {idx["name"]}'))
            db.session.commit()
            db.session.execute(text(f'ALTER TABLE {table} DROP COLUMN {column}'))
            db.session.commit()
            removed.append(f'{table}.{column}')
        except Exception as e:
            db.session.rollback()
            logger.warning(f"废弃列删除跳过 {table}.{column}: {e}")
    stat['legacy_columns'] = removed


def _import_all_models():
    """导入全部模型，确保 db.create_all() 能建出完整表结构。"""
    # noqa: F401 仅用于触发模型注册
    from models.file_info import FileInfo
    from models.data_management import DataUploadBatch, DataRecord
    from models.corrugated_location import CorrugatedLocation, CorrugatedColumnConfig
    from models.sub_list import SubList
    from models.usage_list import UsageList
    from models.vehicle_type import VehicleType
    from models.material_summary import MaterialSummary
    from models.sheet_calculation_result import SheetCalculationResult
    from models.tape_calculation_result import TapeCalculationResult
    from models.tube_calculation_result import TubeCalculationResult
    from models.lot_login_info import LotLoginInfo
    from models.invest_record import InvestmentRecord
    from models.auto_loading import AutoLoadingRecord
    from models.wire_scan_record import WireScanRecord
    from models.wire_scan_detail import WireScanDetail
    from models.wire_crimp_login import WireCrimpLogin
    from models.wire_label_crimp import WireLabelCrimp
    from models.material_inbound import MaterialInboundDaily
    from models.inventory_check import InventoryTeam, InventoryCheck
    from models.inventory_pno_dict import InventoryPnoDict
    from models.defective_login import DefectiveLogin
    from models.system_setting import SystemSetting
    from models.mes_group import MesGroup
    from models.plan_login import PlanLogin
    from models.production_inbound import ProductionInbound
    from models.quality_check import QualityCheck
    from models.unit_price import UnitPrice
    from models.price_multiplier import PriceMultiplier
    from models.customer import Customer
    from models.rbac import (  # noqa: F401
        Permission, UserPermissionOverride,
        PermissionGroup, GroupPermission, UserGroup,
    )
    try:
        from models.finished_product import get_finished_product_model
        get_finished_product_model()
    except Exception as e:  # 成品模型缺失不阻断其余升级
        logger.warning(f"导入 FinishedProduct 模型失败(非致命): {e}")


def upgrade_schema():
    """执行结构升级：建表 → 补列 → 索引。返回统计 dict。"""
    from sqlalchemy import inspect, text

    stat = {'tables': 'ok', 'columns_added': 0, 'indexes': 'ok', 'errors': []}

    # 1) 建表：补建快照中缺失的新表
    try:
        _import_all_models()
        db.create_all()
    except Exception as e:
        stat['tables'] = f'fail: {e}'
        stat['errors'].append(f'建表失败: {e}')
        logger.error(f"schema_upgrade 建表失败: {e}")
        db.session.rollback()

    # 2) 补列：为已有表补齐新增字段
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    for table, columns in COLUMN_UPGRADES.items():
        if table not in existing_tables:
            continue
        try:
            cur_cols = {c['name'] for c in inspector.get_columns(table)}
            for col_name, col_ddl in columns:
                if col_name in cur_cols:
                    continue
                db.session.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {col_name} {col_ddl}"))
                stat['columns_added'] += 1
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            stat['errors'].append(f'{table} 补列失败: {e}')
            logger.warning(f"schema_upgrade 补列失败 {table}: {e}")

    # 2.5) 结构修正：把 operation_logs.user_id 的 NOT NULL 约束移除
    #      目的：用户账号删除后仅置空操作日志的 user_id 关联而保留日志本身。
    #      SQLite 无法直接 ALTER 约束，需「重建表」实现；仅当仍为 NOT NULL 时执行。
    _relax_operation_logs_user_id(stat)

    # 2.6) 压高知识库索引迁移：唯一键由 (LOT,回路,端子对) 改为 (图纸管理编号,端子对)，
    #      支持同图纸跨 LOT 复用历史压高；幂等，新旧库均安全。
    _migrate_wire_label_crimp_indexes(stat)

    # 3) 索引：CREATE INDEX IF NOT EXISTS（部分索引依赖新列，放在补列之后）
    try:
        for stmt in INDEX_STATEMENTS:
            try:
                db.session.execute(text(stmt))
            except Exception as e:
                logger.debug(f"索引创建跳过(非致命): {e}")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        stat['indexes'] = f'fail: {e}'
        stat['errors'].append(f'索引创建失败: {e}')
        logger.warning(f"schema_upgrade 索引创建失败: {e}")

    # 3.5) 索引清理（H-1）：删除冗余/低收益索引，降低写入放大与库体积。
    #      幂等：DROP INDEX IF EXISTS；仅删除确认冗余的索引，不影响查询正确性。
    try:
        for stmt in REDUNDANT_INDEX_DROPS:
            try:
                db.session.execute(text(stmt))
            except Exception as e:
                logger.debug(f"索引删除跳过(非致命): {e}")
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"schema_upgrade 冗余索引清理失败: {e}")

    # 3.6) 废弃列清理：删除模型已移除的残留列（幂等，失败不致命）。
    _drop_legacy_columns(stat)

    # 4) 清理已废弃的角色表（角色体系已由权限组替代）
    for legacy_table in ('user_role', 'role_permission', 'role'):
        if legacy_table not in existing_tables:
            continue
        try:
            db.session.execute(text(f"DROP TABLE IF EXISTS {legacy_table}"))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logger.warning(f"删除废弃角色表失败 {legacy_table}: {e}")

    # 5) 回填操作人姓名快照：把「当前仍存在的用户」的姓名写入历史记录的
    #    *_name 快照列，确保日后删除该用户时历史文本不丢失。幂等：仅补空值。
    _backfill_operator_name_snapshots(stat)

    return stat


# (表名, 用户ID外键列, 姓名快照列) —— 用于一次性幂等回填历史数据的操作人文本
OPERATOR_NAME_SNAPSHOTS = [
    ('operation_logs', 'user_id', 'username'),
    ('file_info', 'created_by', 'created_by_name'),
    ('file_info', 'updated_by', 'updated_by_name'),
    ('file_info', 'frozen_by', 'frozen_by_name'),
    ('plan_login', 'created_by', 'created_by_name'),
    ('production_inbound', 'created_by', 'created_by_name'),
    ('quality_check', 'created_by', 'created_by_name'),
    ('unit_price', 'created_by', 'created_by_name'),
    ('customer', 'created_by', 'created_by_name'),
    ('mes_group', 'created_by', 'created_by_name'),
    ('inventory_check', 'created_by', 'created_by_name'),
    ('wire_scan_record', 'created_by', 'created_by_name'),
    ('material_inbound_daily', 'inbound_by', 'inbound_by_name'),
    ('material_inbound_daily', 'created_by', 'created_by_name'),
    ('finished_product', 'created_by', 'created_by_name'),
    ('finished_product', 'updated_by', 'updated_by_name'),
    ('vehicle_type', 'created_by', 'created_by_name'),
    ('vehicle_type', 'updated_by', 'updated_by_name'),
    ('lot_login_info', 'created_by', 'created_by_name'),
    ('defective_login', 'created_by', 'created_by_name'),
    ('data_upload_batch', 'uploaded_by', 'uploaded_by_name'),
]


def _migrate_wire_label_crimp_indexes(stat):
    """压高知识库索引迁移（幂等）：

    旧唯一键 uk_wlc_lot_circuit_tml (lot_no, circuit_no, tml1, tml2)
    → 新唯一键 uk_wlc_drawing_tml (drawing_mgmt_no, tml1, tml2)，
    另补普通索引 idx_wlc_lot_circuit 供按 LOT+回路查询最近来源。
    新库由 db.create_all() 直接建出新索引，本函数仅旧库执行 DROP/CREATE。
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    if 'wire_label_crimp' not in set(inspector.get_table_names()):
        return
    idx_names = {i['name'] for i in inspector.get_indexes('wire_label_crimp')}
    try:
        if 'uk_wlc_lot_circuit_tml' in idx_names:
            db.session.execute(text('DROP INDEX IF EXISTS uk_wlc_lot_circuit_tml'))
        if 'uk_wlc_drawing_tml' not in idx_names:
            db.session.execute(text(
                'CREATE UNIQUE INDEX IF NOT EXISTS uk_wlc_drawing_tml '
                'ON wire_label_crimp (drawing_mgmt_no, tml1, tml2)'))
        if 'idx_wlc_lot_circuit' not in idx_names:
            db.session.execute(text(
                'CREATE INDEX IF NOT EXISTS idx_wlc_lot_circuit '
                'ON wire_label_crimp (lot_no, circuit_no)'))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        stat['errors'].append(f'wire_label_crimp 索引迁移失败: {e}')
        logger.warning(f"wire_label_crimp 索引迁移失败(非致命): {e}")


def _relax_operation_logs_user_id(stat):
    """确保 operation_logs 结构正确（幂等）：
        1) user_id 允许为空（账号删除后置空关联，保留日志）
        2) id 保持主键自增（历史版本重建表时曾误删主键，需修复）

    背景：用户账号被删除后，日志需保留但 user_id 置空（改为 NULL）。
    旧库中该列带 NOT NULL，SQLite 不支持直接修改约束，故采用「重建表」。
    重建时必须完整保留 PRIMARY KEY，否则新增行 id 为空导致日志丢失。
    仅在结构不符时执行，兼容 SQLite / MySQL。
    """
    from sqlalchemy import inspect, text

    inspector = inspect(db.engine)
    if 'operation_logs' not in set(inspector.get_table_names()):
        return

    cols = {c['name']: c for c in inspector.get_columns('operation_logs')}
    uid_col = cols.get('user_id')
    if uid_col is None:
        return
    id_col = cols.get('id') or {}
    # 需要重建的两种情形：user_id 仍为 NOT NULL；或 id 丢失主键（select 显示 pk>0）
    user_id_not_null = not uid_col.get('nullable', True)
    id_lost_pk = not (id_col.get('primary_key') or False)
    if not user_id_not_null and not id_lost_pk:
        return  # 结构已正确，无需处理

    dialect = db.engine.dialect.name
    try:
        if dialect == 'mysql':
            if user_id_not_null:
                db.session.execute(text(
                    "ALTER TABLE operation_logs MODIFY user_id INT NULL"))
                db.session.commit()
                logger.info("operation_logs.user_id 已改为可空(MySQL)")
            return

        if dialect != 'sqlite':
            logger.info(f"跳过 operation_logs 结构修正(方言 {dialect})")
            return

        # SQLite：按当前实际列重建表，user_id 改为可空，并保留主键定义
        # 用 PRAGMA table_info 拿到 pk 顺序（>0 即主键成员，按 pk 值排序表示复合主键）
        info_rows = db.session.execute(
            text('PRAGMA table_info(operation_logs)')).fetchall()
        # 形如 (cid, name, type, notnull, dflt_value, pk)
        pk_cols = sorted(
            [(r[5], r[1]) for r in info_rows if r[5]],
            key=lambda x: x[0])
        pk_names = [n for _, n in pk_cols]
        # 自增主键仅当为单列 INTEGER 主键时附加 AUTOINCREMENT，保持原行为
        autoinc = (len(pk_names) == 1 and pk_names[0] == 'id')

        def col_def(name):
            if name == 'user_id':
                base = '"user_id" INTEGER'
            else:
                row = next(r for r in info_rows if r[1] == name)
                col_type = row[2] or 'TEXT'
                notnull = '' if (name in pk_names) else (' NOT NULL' if row[3] else '')
                base = f'"{name}" {col_type}{notnull}'
            return base

        col_names = [r[1] for r in info_rows]
        select_cols = ', '.join(f'"{c}"' for c in col_names)
        pk_clause = ''
        if pk_names:
            pk_clause = ', PRIMARY KEY (' + ', '.join(f'"{c}"' for c in pk_names) + ')'
        else:
            pk_clause = ', PRIMARY KEY ("id")'
        create_sql = (
            'CREATE TABLE operation_logs__new ('
            + ', '.join(col_def(c) for c in col_names)
            + pk_clause
            + (')' if not autoinc else ')')
        )
        db.session.execute(text('DROP TABLE IF EXISTS operation_logs__new'))
        db.session.execute(text(create_sql))
        db.session.execute(text(
            f'INSERT INTO operation_logs__new ({select_cols}) '
            f'SELECT {select_cols} FROM operation_logs'))
        db.session.execute(text('DROP TABLE operation_logs'))
        db.session.execute(text(
            'ALTER TABLE operation_logs__new RENAME TO operation_logs'))
        db.session.commit()
        logger.info("operation_logs 结构已修正（user_id 可空 + 保留主键，SQLite 重建表）")
    except Exception as e:
        db.session.rollback()
        stat['errors'].append(f'operation_logs 结构修正失败: {e}')
        logger.warning(f"operation_logs 结构修正失败: {e}")


def _backfill_operator_name_snapshots(stat):
    """幂等回填：将现存用户的姓名写入历史记录为空的 *_name 快照列。

    仅填充「快照为空且外键指向现存用户」的行，不覆盖已有文本，
    因此可安全重复执行。
    """
    from sqlalchemy import inspect, text
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    total = 0
    for table, fk_col, name_col in OPERATOR_NAME_SNAPSHOTS:
        if table not in existing_tables:
            continue
        try:
            cur_cols = {c['name'] for c in inspector.get_columns(table)}
            if fk_col not in cur_cols or name_col not in cur_cols:
                continue
            res = db.session.execute(text(
                f'UPDATE "{table}" SET "{name_col}" = ('
                f'  SELECT u.username FROM user_info u WHERE u.id = "{table}"."{fk_col}"'
                f') WHERE "{fk_col}" IS NOT NULL '
                f'AND ("{name_col}" IS NULL OR "{name_col}" = \'\') '
                f'AND EXISTS (SELECT 1 FROM user_info u WHERE u.id = "{table}"."{fk_col}")'
            ))
            total += res.rowcount or 0
        except Exception as e:
            db.session.rollback()
            logger.debug(f"回填操作人快照跳过 {table}.{name_col}: {e}")
            continue
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.warning(f"回填操作人快照提交失败: {e}")
    stat['name_snapshots_backfilled'] = total


def init_default_data():
    """初始化默认数据：RBAC 权限目录 / 内置权限组 / 系统设置默认值。"""
    stat = {'rbac': 'ok', 'settings': 'ok', 'errors': []}
    try:
        from utils.rbac_service import init_rbac_defaults
        init_rbac_defaults()
    except Exception as e:
        db.session.rollback()
        stat['rbac'] = f'fail: {e}'
        stat['errors'].append(f'RBAC 初始化失败: {e}')
        logger.warning(f"schema_upgrade RBAC 初始化失败: {e}")

    try:
        from models.system_setting import init_developer_settings
        init_developer_settings()
    except Exception as e:
        db.session.rollback()
        stat['settings'] = f'fail: {e}'
        stat['errors'].append(f'系统设置初始化失败: {e}')
        logger.warning(f"schema_upgrade 系统设置初始化失败: {e}")

    return stat


def ensure_schema_ready():
    """完整初始化：结构升级 + 默认数据 + 历史数据回填。供启动流程与恢复后调用。"""
    result = {'schema': upgrade_schema(), 'defaults': init_default_data()}
    # 历史数据回填（幂等：只回填 created_by_name 仍为空的行）
    try:
        result['wire_created_by_name'] = _backfill_wire_scan_created_by_name()
    except Exception as e:
        db.session.rollback()
        result['wire_created_by_name'] = f'fail: {e}'
        logger.warning(f"回填 wire_scan_record.created_by_name 失败: {e}")
    return result


def _backfill_wire_scan_created_by_name():
    """为历史 wire_scan_record 记录回填 created_by_name 姓名快照（幂等）。

    旧数据写入时只存了 created_by（用户ID），未固化姓名；新写入已补齐。
    本函数把 created_by_name 仍为 NULL/空串 的老行，通过 UserInfo 关联回填。
    """
    from models.wire_scan_record import WireScanRecord
    from models.user_info import UserInfo
    from sqlalchemy import or_

    # 找 created_by_name 为空、但 created_by 有值的老数据
    rows = (WireScanRecord.query
            .filter(WireScanRecord.created_by.isnot(None))
            .filter(or_(
                WireScanRecord.created_by_name.is_(None),
                WireScanRecord.created_by_name == ''
            ))
            .all())
    if not rows:
        return 'skip'
    # 批量查用户
    uids = list({r.created_by for r in rows})
    users = {u.id: u for u in UserInfo.query.filter(UserInfo.id.in_(uids)).all()}
    patched = 0
    for r in rows:
        u = users.get(r.created_by)
        if not u:
            continue
        name = (u.full_name or '').strip() or (u.username or '').strip()
        if name:
            r.created_by_name = name
            patched += 1
    if patched:
        db.session.commit()
        logger.info(f"回填 wire_scan_record.created_by_name: {patched} 条")
    return f'patched={patched}/total={len(rows)}'
