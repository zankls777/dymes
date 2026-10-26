# init_tables.py
"""
初始化数据库表脚本
用于创建缺失的表，特别是sheet_calculation_result和tape_calculation_result
"""

from app_factory import create_app, db

# 创建应用实例
app = create_app()

# 在应用上下文中创建表
with app.app_context():
    print("开始创建数据库表...")
    
    try:
        # 导入所有需要的模型
        from models.sheet_calculation_result import SheetCalculationResult
        from models.tape_calculation_result import TapeCalculationResult
        
        # 创建所有表
        db.create_all()
        print("✅ 数据库表创建成功！")
        print("✅ 已确保以下表存在：")
        print("   - sheet_calculation_result")
        print("   - tape_calculation_result")
    except Exception as e:
        print(f"❌ 数据库表创建失败：{e}")
        import traceback
        traceback.print_exc()
