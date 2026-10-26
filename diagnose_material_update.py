#!/usr/bin/env python3
"""
诊断一键更新功能数据读取问题
分析finished_product表和资材量汇总的数据关联
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_factory import create_app, db
from models.finished_product import get_finished_product_model
from models.usage_list import UsageList
from models.material_summary import MaterialSummary
from sqlalchemy import text, func


def diagnose():
    """诊断数据读取问题"""
    app = create_app()
    
    with app.app_context():
        FinishedProduct = get_finished_product_model()
        
        print('=' * 70)
        print('一键更新功能数据读取诊断')
        print('=' * 70)
        
        # 1. 检查finished_product表的EO字段情况
        print('\n【1】成品表EO字段统计')
        print('-' * 50)
        
        total_products = db.session.query(FinishedProduct).count()
        print(f'总成品记录数: {total_products}')
        
        # 有eo值的记录
        has_eo = db.session.query(FinishedProduct).filter(
            FinishedProduct.eo.isnot(None),
            FinishedProduct.eo != ''
        ).count()
        print(f'有EO值的记录: {has_eo}')
        
        # eo为空但eo_override为True的记录
        eo_overridden = db.session.query(FinishedProduct).filter(
            FinishedProduct.eo_override == True
        ).count()
        print(f'EO被手动覆盖的记录: {eo_overridden}')
        
        # eo为空但usage_eo有值的记录
        has_usage_eo = db.session.query(FinishedProduct).filter(
            FinishedProduct.eo.is_(None) | (FinishedProduct.eo == ''),
            FinishedProduct.usage_eo.isnot(None),
            FinishedProduct.usage_eo != ''
        ).count()
        print(f'EO为空但UsageEO有值的记录: {has_usage_eo}')
        
        # 2. 检查临时表创建的SQL逻辑
        print('\n【2】分析一键更新的SQL查询逻辑')
        print('-' * 50)
        
        # 当前查询逻辑: WHERE eo IS NOT NULL AND eo != ''
        # 这会过滤掉eo为空的记录，即使它们有usage_eo或eo_override
        
        # 模拟当前查询
        current_filter_sql = text("""
            SELECT COUNT(*) FROM finished_product 
            WHERE eo IS NOT NULL AND eo != ''
        """)
        current_filter_count = db.session.execute(current_filter_sql).scalar()
        print(f'当前查询条件(eo非空)能匹配的记录数: {current_filter_count}')
        
        # 建议的改进查询
        improved_filter_sql = text("""
            SELECT COUNT(*) FROM finished_product 
            WHERE (eo IS NOT NULL AND eo != '') 
               OR (usage_eo IS NOT NULL AND usage_eo != '')
        """)
        improved_filter_count = db.session.execute(improved_filter_sql).scalar()
        print(f'改进查询条件(eo或usage_eo非空)能匹配的记录数: {improved_filter_count}')
        
        # 差值
        missing_records = improved_filter_count - current_filter_count
        if missing_records > 0:
            print(f'\n⚠️  发现问题：有 {missing_records} 条记录因EO为空被过滤掉！')
            print('   这些记录可能有有效的usage_eo值，但无法被一键更新读取')
        else:
            print('\n✅ 当前查询条件已覆盖所有有效记录')
        
        # 3. 检查JOIN逻辑
        print('\n【3】检查与usage_list的JOIN匹配情况')
        print('-' * 50)
        
        # 当前JOIN逻辑
        current_join_sql = text("""
            SELECT COUNT(*) FROM (
                SELECT 
                    f.id, f.part_number, f.eo, f.usage_eo,
                    u.SubPno, u.EoKsc
                FROM finished_product f
                INNER JOIN usage_list u 
                    ON REPLACE(REPLACE(REPLACE(REPLACE(UPPER(f.part_number), ' ', ''), '－', '-'), '–', '-'), '—', '-') 
                     = REPLACE(REPLACE(REPLACE(REPLACE(UPPER(u.SubPno), ' ', ''), '－', '-'), '–', '-'), '—', '-')
                    AND UPPER(TRIM(f.eo)) = UPPER(TRIM(u.EoKsc))
                WHERE f.eo IS NOT NULL AND f.eo != ''
                  AND u.deleted = 0 AND u.EoKsc IS NOT NULL AND u.EoKsc != ''
            )
        """)
        current_join_count = db.session.execute(current_join_sql).scalar()
        print(f'当前JOIN逻辑(eo匹配)的匹配数: {current_join_count}')
        
        # 改进的JOIN逻辑 - 使用COALESCE处理EO
        improved_join_sql = text("""
            SELECT COUNT(*) FROM (
                SELECT 
                    f.id, f.part_number, f.eo, f.usage_eo,
                    u.SubPno, u.EoKsc
                FROM finished_product f
                INNER JOIN usage_list u 
                    ON REPLACE(REPLACE(REPLACE(REPLACE(UPPER(f.part_number), ' ', ''), '－', '-'), '–', '-'), '—', '-') 
                     = REPLACE(REPLACE(REPLACE(REPLACE(UPPER(u.SubPno), ' ', ''), '－', '-'), '–', '-'), '—', '-')
                    AND UPPER(TRIM(COALESCE(f.eo, f.usage_eo, ''))) = UPPER(TRIM(u.EoKsc))
                WHERE (f.eo IS NOT NULL AND f.eo != '') 
                   OR (f.usage_eo IS NOT NULL AND f.usage_eo != '')
                  AND u.deleted = 0 AND u.EoKsc IS NOT NULL AND u.EoKsc != ''
            )
        """)
        improved_join_count = db.session.execute(improved_join_sql).scalar()
        print(f'改进JOIN逻辑(eo/usage_eo匹配)的匹配数: {improved_join_count}')
        
        join_diff = improved_join_count - current_join_count
        if join_diff > 0:
            print(f'\n⚠️  JOIN匹配差异：改进后可多匹配 {join_diff} 条记录！')
        
        # 4. 查看示例数据
        print('\n【4】查看部分成品数据示例')
        print('-' * 50)
        
        sample_sql = text("""
            SELECT id, vehicle_name, part_number, eo, eo_override, usage_eo, quantity, update_time
            FROM finished_product
            ORDER BY update_time DESC
            LIMIT 10
        """)
        samples = db.session.execute(sample_sql).fetchall()
        
        print('最近更新的10条成品记录:')
        print(f'{"ID":<5} {"品番":<20} {"EO":<15} {"EO覆盖":<8} {"UsageEO":<15} {"数量":<8} {"更新时间"}')
        print('-' * 100)
        for row in samples:
            eo_display = row.eo or '(空)'
            usage_eo_display = row.usage_eo or '(空)'
            eo_override_display = '是' if row.eo_override else '否'
            print(f'{row.id:<5} {row.part_number:<20} {eo_display:<15} {eo_override_display:<8} {usage_eo_display:<15} {row.quantity:<8} {row.update_time}')
        
        # 5. 查看EO为空但有usage_eo的记录示例
        print('\n【5】EO为空但有UsageEO的记录示例')
        print('-' * 50)
        
        eo_empty_sql = text("""
            SELECT id, vehicle_name, part_number, eo, eo_override, usage_eo, quantity
            FROM finished_product
            WHERE (eo IS NULL OR eo = '') AND usage_eo IS NOT NULL AND usage_eo != ''
            LIMIT 10
        """)
        eo_empty_samples = db.session.execute(eo_empty_sql).fetchall()
        
        if eo_empty_samples:
            print(f'发现 {len(eo_empty_samples)} 条记录的EO为空但有UsageEO:')
            print(f'{"ID":<5} {"品番":<20} {"EO":<15} {"EO覆盖":<8} {"UsageEO":<15} {"数量"}')
            print('-' * 80)
            for row in eo_empty_samples:
                eo_override_display = '是' if row.eo_override else '否'
                print(f'{row.id:<5} {row.part_number:<20} {"(空)":<15} {eo_override_display:<8} {row.usage_eo:<15} {row.quantity}')
        else:
            print('✅ 没有EO为空但有UsageEO的记录')
        
        # 6. 汇总问题分析
        print('\n【6】问题分析总结')
        print('=' * 70)
        
        if missing_records > 0 or join_diff > 0:
            print('\n🔴 发现以下问题：')
            if missing_records > 0:
                print(f'   1. 查询过滤问题：{missing_records}条记录因eo为空被过滤')
                print('      - 这些记录可能有有效的usage_eo值')
                print('      - 建议修改WHERE条件使用COALESCE(eo, usage_eo)')
            if join_diff > 0:
                print(f'   2. JOIN匹配问题：改进后可多匹配{join_diff}条记录')
                print('      - 建议修改JOIN条件同时匹配eo和usage_eo')
            
            print('\n📋 修复建议：')
            print('   1. 修改temp_finished_normalized创建SQL：')
            print('      - WHERE条件: WHERE COALESCE(eo, usage_eo) IS NOT NULL AND COALESCE(eo, usage_eo) != ""')
            print('      - eo_normalized: UPPER(TRIM(COALESCE(eo, usage_eo)))')
            print('   2. 修改JOIN条件：使用COALESCE(f.eo, f.usage_eo)替代f.eo')
            print('   3. 添加临时表删除语句：DROP TABLE IF EXISTS 确保每次使用新数据')
        else:
            print('\n✅ 未发现明显问题，一键更新逻辑正确')
            print('   如果数据未刷新，可能是以下原因：')
            print('   1. 临时表缓存问题 - 建议检查是否需要添加DROP TABLE IF EXISTS')
            print('   2. 数据更新时间 - 请确认成品数据确实已更新')
            print('   3. 缓存失效 - 确认invalidate_list_cache()是否正确执行')


if __name__ == '__main__':
    diagnose()
