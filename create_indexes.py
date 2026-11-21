#!/usr/bin/env python3
"""
数据库索引创建脚本
直接连接SQLite数据库创建索引，不需要Flask应用上下文
"""

import sqlite3
import os

def create_indexes():
    """创建数据库索引"""
    db_path = 'instance/app.db'
    
    if not os.path.exists(db_path):
        print(f"❌ 数据库文件不存在: {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    indexes_to_add = [
        # SubList 表索引 - 优化搜索查询
        "CREATE INDEX IF NOT EXISTS idx_sub_list_subpno ON sub_list(SubPno)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_eoksc ON sub_list(EoKsc)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_setcode ON sub_list(SetCode)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_circuit ON sub_list(Circuit)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_vehicle_type ON sub_list(VehicleType)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_source_file ON sub_list(source_file)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_deleted ON sub_list(deleted)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_upload_time ON sub_list(upload_time)",
        
        # UsageList 表索引 - 优化搜索查询
        "CREATE INDEX IF NOT EXISTS idx_usage_list_eoksc ON usage_list(EoKsc)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_subpno ON usage_list(SubPno)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_kscpno ON usage_list(KscPno)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_vehicle_type ON usage_list(VehicleType)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_comp_kind ON usage_list(Comp_Kind)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_detail_kind ON usage_list(Detail_Kind)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_source_file ON usage_list(source_file)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_deleted ON usage_list(deleted)",
        
        # FileInfo 表索引
        "CREATE INDEX IF NOT EXISTS idx_file_info_file_type ON file_info(file_type)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_upload_time ON file_info(upload_time)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_vehicle_type ON file_info(vehicle_type)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_eo ON file_info(eo)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_filename ON file_info(filename)",
        
        # VehicleType 表索引
        "CREATE INDEX IF NOT EXISTS idx_vehicle_type_part_number ON vehicle_type(part_number)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_type_create_time ON vehicle_type(create_time)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_type_deleted ON vehicle_type(deleted)",
        
        # FinishedProduct 表索引
        "CREATE INDEX IF NOT EXISTS idx_finished_product_vehicle_name ON finished_product(vehicle_name)",
        "CREATE INDEX IF NOT EXISTS idx_finished_product_part_number ON finished_product(part_number)",
        "CREATE INDEX IF NOT EXISTS idx_finished_product_lot_number ON finished_product(lot_number)",
        "CREATE INDEX IF NOT EXISTS idx_finished_product_eo ON finished_product(eo)",
        "CREATE INDEX IF NOT EXISTS idx_finished_product_create_time ON finished_product(create_time)",
        
        # OperationLog 表索引
        "CREATE INDEX IF NOT EXISTS idx_operation_log_user_id ON operation_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_operation_log_operation_type ON operation_log(operation_type)",
        "CREATE INDEX IF NOT EXISTS idx_operation_log_created_at ON operation_log(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_operation_log_target_type ON operation_log(target_type)",
        
        # 用户级权限覆盖表索引
        "CREATE INDEX IF NOT EXISTS idx_user_permission_override_user_id ON user_permission_override(user_id)",
        
        # MaterialSummary 表索引（H-13：列名与模型对齐，原 part_no/vehicle_type/update_time 均不存在）
        "CREATE INDEX IF NOT EXISTS idx_material_summary_part_number ON material_summary(part_number)",
        "CREATE INDEX IF NOT EXISTS idx_material_summary_vehicle_name ON material_summary(vehicle_name)",
        "CREATE INDEX IF NOT EXISTS idx_material_summary_update_time ON material_summary(last_update_time)",
        
        # 复合索引 - 优化常用组合查询
        "CREATE INDEX IF NOT EXISTS idx_sub_list_vt_subpno ON sub_list(VehicleType, SubPno)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_eoksc_circuit ON sub_list(EoKsc, Circuit)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_vt_subpno ON usage_list(VehicleType, SubPno)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_vt_eo ON file_info(vehicle_type, eo)",
        
        # 覆盖索引 - 让 COUNT(active) 直接扫小索引，跳过 2.7GB 表数据页
        # 实测 sub_list 2.7GB 上 COUNT(active) 从 4.2s → 0.26s，usage_list 从 7.8s → 0.12s
        # 十几 GB 时覆盖索引体积仅 ~10MB，能让 COUNT 始终维持在 100ms 以内
        "CREATE INDEX IF NOT EXISTS idx_sub_list_active_count ON sub_list(deleted, is_frozen)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_active_count ON usage_list(deleted, is_frozen)",
    ]
    
    success_count = 0
    fail_count = 0
    
    try:
        for index_sql in indexes_to_add:
            try:
                cursor.execute(index_sql)
                print(f"✅ {index_sql.split()[5]}")
                success_count += 1
            except Exception as e:
                print(f"❌ {index_sql.split()[5]}: {e}")
                fail_count += 1
        
        conn.commit()
        print(f"\n📊 索引创建完成: {success_count} 成功, {fail_count} 失败")
        
    except Exception as e:
        print(f"\n❌ 数据库操作失败: {e}")
    finally:
        conn.close()

def main():
    """主函数"""
    print("🔧 开始创建数据库索引...")
    print("=" * 60)
    create_indexes()
    print("=" * 60)
    print("\n💡 索引优化已完成！")
    print("提示:")
    print("  - 索引可以显著提高查询速度")
    print("  - 但会稍微降低写入性能")
    print("  - 建议定期分析查询性能并调整索引")

if __name__ == "__main__":
    main()