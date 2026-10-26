#!/usr/bin/env python3
"""
检查material_summary表中是否存在remark列
"""

from app_factory import create_app, db

app = create_app()
with app.app_context():
    from sqlalchemy import inspect
    
    inspector = inspect(db.engine)
    columns = inspector.get_columns('material_summary')
    column_names = [col['name'] for col in columns]
    
    print("material_summary表的列:")
    for col in column_names:
        print(f"  - {col}")
    
    if 'remark' in column_names:
        print("\n✅ remark列已存在于material_summary表中")
    else:
        print("\n❌ remark列不存在于material_summary表中")
        # 尝试手动添加
        from sqlalchemy import text
        try:
            db.session.execute(text("ALTER TABLE material_summary ADD COLUMN remark TEXT"))
            db.session.commit()
            print("✅ 已手动添加remark列到material_summary表中")
        except Exception as e:
            print(f"❌ 手动添加remark列失败: {e}")
