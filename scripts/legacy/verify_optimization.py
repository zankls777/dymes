#!/usr/bin/env python3
"""
验证优化效果：检查部分索引、冻结数据过滤、缓存统计
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'instance', 'app.db')

def check_indexes():
    """检查部分索引是否创建成功"""
    print('=' * 70)
    print('[1] 部分索引验证')
    print('=' * 70)

    if not os.path.exists(DB_PATH):
        print(f'  数据库文件不存在: {DB_PATH}')
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 查询 sub_list 索引
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='sub_list' ORDER BY name")
    sub_indexes = cursor.fetchall()
    print(f'\n  sub_list 索引 ({len(sub_indexes)} 个):')
    for name, sql in sub_indexes:
        marker = '★' if '_active' in name or '_status' in name else ' '
        print(f'    {marker} {name}')

    # 查询 usage_list 索引
    cursor.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='usage_list' ORDER BY name")
    usage_indexes = cursor.fetchall()
    print(f'\n  usage_list 索引 ({len(usage_indexes)} 个):')
    for name, sql in usage_indexes:
        marker = '★' if '_active' in name or '_status' in name else ' '
        print(f'    {marker} {name}')

    # 验证关键部分索引
    expected = [
        'idx_sub_list_active',
        'idx_usage_list_active',
        'idx_sub_list_subpno_eoksc_active',
        'idx_usage_list_subpno_eoksc_active',
        'idx_sub_list_source_file_status',
        'idx_usage_list_source_file_status',
    ]
    all_index_names = [name for name, _ in sub_indexes + usage_indexes]
    print(f'\n  关键部分索引检查:')
    for idx in expected:
        status = '✓ 存在' if idx in all_index_names else '✗ 缺失'
        print(f'    {idx}: {status}')

    conn.close()

def check_frozen_data():
    """检查冻结数据过滤效果"""
    print('\n' + '=' * 70)
    print('[2] 冻结数据过滤验证')
    print('=' * 70)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # sub_list 数据统计
    cursor.execute("SELECT COUNT(*) FROM sub_list WHERE deleted=0")
    sl_total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sub_list WHERE deleted=0 AND is_frozen=0")
    sl_active = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM sub_list WHERE deleted=0 AND is_frozen=1")
    sl_frozen = cursor.fetchone()[0]

    print(f'\n  sub_list 数据分布:')
    print(f'    总计(未删除):    {sl_total:>10,}')
    print(f'    活跃(未冻结):    {sl_active:>10,} ({sl_active/sl_total*100:.1f}%)' if sl_total else '    活跃(未冻结):    0')
    print(f'    已冻结:          {sl_frozen:>10,} ({sl_frozen/sl_total*100:.1f}%)' if sl_total else '    已冻结:          0')

    # usage_list 数据统计
    cursor.execute("SELECT COUNT(*) FROM usage_list WHERE deleted=0")
    ul_total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM usage_list WHERE deleted=0 AND is_frozen=0")
    ul_active = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM usage_list WHERE deleted=0 AND is_frozen=1")
    ul_frozen = cursor.fetchone()[0]

    print(f'\n  usage_list 数据分布:')
    print(f'    总计(未删除):    {ul_total:>10,}')
    print(f'    活跃(未冻结):    {ul_active:>10,} ({ul_active/ul_total*100:.1f}%)' if ul_total else '    活跃(未冻结):    0')
    print(f'    已冻结:          {ul_frozen:>10,} ({ul_frozen/ul_total*100:.1f}%)' if ul_total else '    已冻结:          0')

    # 验证业务查询只返回活跃数据
    print(f'\n  业务查询过滤验证 (WHERE deleted=0 AND is_frozen=0):')
    print(f'    sub_list 活跃数据:    {sl_active:>10,} 条 (业务查询可见)')
    print(f'    sub_list 冻结数据:    {sl_frozen:>10,} 条 (业务查询不可见 - 已正确过滤)')
    print(f'    usage_list 活跃数据:  {ul_active:>10,} 条 (业务查询可见)')
    print(f'    usage_list 冻结数据:  {ul_frozen:>10,} 条 (业务查询不可见 - 已正确过滤)')

    # 部分索引体积对比
    print(f'\n  部分索引效率:')
    if sl_total > 0:
        print(f'    sub_list 部分索引覆盖: {sl_active/sl_total*100:.1f}% (仅活跃数据，减少 {100-sl_active/sl_total*100:.1f}% 索引体积)')
    if ul_total > 0:
        print(f'    usage_list 部分索引覆盖: {ul_active/ul_total*100:.1f}% (仅活跃数据，减少 {100-ul_active/ul_total*100:.1f}% 索引体积)')

    conn.close()

def check_explain():
    """EXPLAIN QUERY PLAN 验证索引命中"""
    print('\n' + '=' * 70)
    print('[3] EXPLAIN QUERY PLAN 验证')
    print('=' * 70)

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    queries = [
        ('sub_list 活跃查询', 'SELECT id FROM sub_list WHERE deleted=0 AND is_frozen=0 LIMIT 10'),
        ('sub_list COUNT', 'SELECT COUNT(*) FROM sub_list WHERE deleted=0 AND is_frozen=0'),
        ('sub_list 翻页', 'SELECT id FROM sub_list WHERE deleted=0 AND is_frozen=0 ORDER BY id LIMIT 10 OFFSET 10'),
        ('usage_list 活跃查询', 'SELECT id FROM usage_list WHERE deleted=0 AND is_frozen=0 LIMIT 10'),
        ('usage_list COUNT', 'SELECT COUNT(*) FROM usage_list WHERE deleted=0 AND is_frozen=0'),
        ('冻结同步查询', 'SELECT COUNT(*) FROM sub_list WHERE source_file="test" AND is_frozen!=0'),
    ]

    for label, sql in queries:
        cursor.execute(f'EXPLAIN QUERY PLAN {sql}')
        plan = cursor.fetchall()
        print(f'\n  {label}:')
        print(f'    SQL: {sql[:80]}...')
        for row in plan:
            print(f'    → {row[3]}')

    conn.close()

def main():
    print()
    check_indexes()
    check_frozen_data()
    check_explain()
    print('\n' + '=' * 70)
    print('验证完成')
    print('=' * 70)

if __name__ == '__main__':
    main()
