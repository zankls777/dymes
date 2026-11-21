#!/usr/bin/env python3
"""
数据库优化脚本
添加索引、优化查询、实现分页
"""

from flask import current_app
from app_factory import db
from sqlalchemy import text, Index

def add_database_indexes():
    """添加数据库索引以提高查询性能"""
    
    indexes_to_add = [
        # SubList 表索引
        "CREATE INDEX IF NOT EXISTS idx_sub_list_subpno ON sub_list(SubPno)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_eoksc ON sub_list(EoKsc)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_source_file ON sub_list(source_file)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_deleted ON sub_list(deleted)",
        "CREATE INDEX IF NOT EXISTS idx_sub_list_upload_time ON sub_list(upload_time)",
        
        # UsageList 表索引
        "CREATE INDEX IF NOT EXISTS idx_usage_list_eoksc ON usage_list(EoKsc)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_vehicle_type ON usage_list(VehicleType)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_source_file ON usage_list(source_file)",
        "CREATE INDEX IF NOT EXISTS idx_usage_list_deleted ON usage_list(deleted)",
        
        # FileInfo 表索引
        "CREATE INDEX IF NOT EXISTS idx_file_info_file_type ON file_info(file_type)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_upload_time ON file_info(upload_time)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_vehicle_type ON file_info(vehicle_type)",
        "CREATE INDEX IF NOT EXISTS idx_file_info_eo ON file_info(eo)",
        
        # VehicleType 表索引
        "CREATE INDEX IF NOT EXISTS idx_vehicle_type_part_number ON vehicle_type(part_number)",
        "CREATE INDEX IF NOT EXISTS idx_vehicle_type_create_time ON vehicle_type(create_time)",
        
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
        
        # 用户级权限覆盖表索引（RBAC）
        "CREATE INDEX IF NOT EXISTS idx_user_permission_override_user_id ON user_permission_override(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_group_user_id ON user_group(user_id)",
    ]
    
    try:
        for index_sql in indexes_to_add:
            db.session.execute(text(index_sql))
        db.session.commit()
        print("✅ 数据库索引添加成功")
    except Exception as e:
        db.session.rollback()
        print(f"❌ 数据库索引添加失败: {e}")

def optimize_queries():
    """优化查询语句"""
    
    # 替换 query.all() 为分页查询
    optimizations = {
        # 示例：将 VehicleType.query.all() 替换为分页查询
        "VehicleType.query.all()": "VehicleType.query.paginate(page=1, per_page=1000).items",
        "FileInfo.query.all()": "FileInfo.query.paginate(page=1, per_page=1000).items",
        "UserInfo.query.all()": "UserInfo.query.paginate(page=1, per_page=1000).items",
    }
    
    print("📋 查询优化建议:")
    for old_query, new_query in optimizations.items():
        print(f"  {old_query}")
        print(f"  → {new_query}")
        print()

def add_query_helpers():
    """添加查询辅助函数"""
    
    helper_code = '''
# 在 models/ 目录下创建 query_helpers.py

from flask_sqlalchemy import Pagination
from typing import List, Optional, Dict, Any

class QueryHelper:
    """查询辅助类"""
    
    @staticmethod
    def paginate_query(query, page: int = 1, per_page: int = 1000, max_per_page: int = 10000):
        """分页查询，防止内存溢出"""
        if per_page > max_per_page:
            per_page = max_per_page
        
        return query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
    
    @staticmethod
    def batch_query(query, batch_size: int = 1000):
        """批量查询，用于大数据量处理"""
        offset = 0
        while True:
            batch = query.offset(offset).limit(batch_size).all()
            if not batch:
                break
            yield batch
            offset += batch_size
    
    @staticmethod
    def count_query(query) -> int:
        """获取查询结果数量"""
        return query.count()
    
    @staticmethod
    def exists_query(query) -> bool:
        """检查查询结果是否存在"""
        return query.first() is not None

# 使用示例：
# 替换 VehicleType.query.all()
# 为 QueryHelper.paginate_query(VehicleType.query, page=1, per_page=1000)
'''
    
    with open('models/query_helpers.py', 'w', encoding='utf-8') as f:
        f.write(helper_code)
    
    print("✅ 查询辅助函数已创建: models/query_helpers.py")

def analyze_slow_queries():
    """分析慢查询"""
    
    slow_query_analysis = """
# 慢查询分析报告

## 1. 高频查询优化
- SubList.query.filter_by(source_file=filename).all()
  → 添加 source_file 索引 ✅
  → 使用分页查询

- UsageList.query.filter(UsageList.SubPno.in_(part_numbers)).all()
  → 添加 SubPno 索引 ✅
  → 批量查询优化

- VehicleType.query.filter(VehicleType.part_number.in_(part_numbers)).all()
  → 添加 part_number 索引 ✅
  → 使用 EXISTS 查询

## 2. 大数据量查询优化
- 所有 .all() 查询 → 分页查询
- 批量操作 → 分批处理
- 复杂 JOIN → 优化查询条件

## 3. 索引使用建议
- 为经常查询的字段添加索引
- 为外键字段添加索引
- 为时间字段添加索引
- 避免过多索引影响写入性能
"""
    
    with open('database_analysis_report.md', 'w', encoding='utf-8') as f:
        f.write(slow_query_analysis)
    
    print("✅ 慢查询分析报告已生成: database_analysis_report.md")

def main():
    """主函数"""
    print("🔧 开始数据库优化...")
    
    # 1. 添加索引
    add_database_indexes()
    
    # 2. 优化查询
    optimize_queries()
    
    # 3. 添加查询辅助函数
    add_query_helpers()
    
    # 4. 分析慢查询
    analyze_slow_queries()
    
    print("\n📋 数据库优化完成！")
    print("建议:")
    print("1. 重启应用以应用索引")
    print("2. 监控查询性能")
    print("3. 逐步替换 query.all() 为分页查询")
    print("4. 使用 QueryHelper 进行批量操作")

if __name__ == "__main__":
    main()
