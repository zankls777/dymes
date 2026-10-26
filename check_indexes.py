#!/usr/bin/env python3
"""
检查数据库索引的脚本

L-7：补充核心大表（sub_list / usage_list / data_record 等）的索引存在性检查。
此前仅检查 operation_logs/user_info/permission_info，恰恰漏掉了数据量最大、
最依赖索引的两张表，导致索引缺失时无法从本脚本发现。
"""

from app_factory import create_app, db

# 核心大表及其「关键索引」最低要求：(表名, [(列名, 用途说明), ...])
# 只要表中存在一个以这些列为首列的索引即视为达标
_CORE_TABLE_CHECKS = [
    ('sub_list', [
        (('VehicleType',), '车种筛选'),
        (('SubPno',), '资材品番检索'),
        (('EoKsc',), 'EO/KSC 关联'),
        (('VehicleType', 'SubPno'), '车种+品番复合'),
        (('SubPno', 'EoKsc', 'VehicleType'), 'RW对比分组去重覆盖索引'),
    ]),
    ('usage_list', [
        (('VehicleType',), '车种筛选'),
        (('SubPno',), '资材品番检索'),
        (('VehicleType', 'SubPno'), '车种+品番复合'),
    ]),
    ('data_record', [
        (('batch_id',), '按批次取明细'),
        (('record_date',), '按业务日期归档/检索'),
    ]),
]


def _index_first_columns(indexes):
    """把索引集合归一化为 {首列元组: 索引名}，用于前缀匹配判断。"""
    result = {}
    for idx in indexes:
        cols = tuple(idx.get('column_names') or [])
        if cols:
            result.setdefault(cols, idx.get('name'))
    return result


def _check_table(inspector, table, required):
    """检查单表是否具备必需索引，返回 (缺失列表, 已有索引列表)。"""
    try:
        indexes = inspector.get_indexes(table)
    except Exception as e:
        print(f"错误: 无法读取 {table} 索引: {e}")
        return None, []
    present = _index_first_columns(indexes)
    missing = []
    for cols, purpose in required:
        # 命中条件：存在以该列组合为「前缀」的索引
        hit = any(present_cols[:len(cols)] == cols for present_cols in present)
        if not hit:
            missing.append((cols, purpose))
    return missing, indexes


app = create_app()
with app.app_context():
    from sqlalchemy import inspect
    inspector = inspect(db.engine)

    # 1) 原有三张小表的索引明细打印
    for table in ('operation_logs', 'user_info', 'permission_info'):
        print(f"检查操作记录/用户/权限表 ({table}) 的索引:")
        try:
            for index in inspector.get_indexes(table):
                print(f"- {index['name']}: {index['column_names']} "
                      f"(唯一: {index['unique']})")
        except Exception as e:
            print(f"错误: {e}")
        print()

    # 2) L-7 新增：核心大表关键索引存在性校验
    print("=" * 60)
    print("核心大表关键索引检查:")
    missing_total = 0
    for table, required in _CORE_TABLE_CHECKS:
        missing, indexes = _check_table(inspector, table, required)
        if missing is None:
            missing_total += 1
            continue
        status = 'OK' if not missing else f'缺失 {len(missing)} 项'
        print(f"\n[{status}] {table} (现有索引 {len(indexes)} 个)")
        for cols, purpose in missing:
            missing_total += 1
            print(f"  ✗ 缺少索引: {table}({', '.join(cols)}) - {purpose}")

    print(f"\n索引检查完成：{'全部达标' if missing_total == 0 else f'共 {missing_total} 项待补'}")

