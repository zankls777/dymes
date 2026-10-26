#!/usr/bin/env python3
"""
数据库状态检查脚本
"""

import os
from app_factory import create_app, db
from sqlalchemy import text

def check_database_status():
    """检查数据库状态"""
    print("🔍 数据库状态检查")
    print("=" * 50)
    
    # 检查环境变量
    db_type = os.environ.get('DATABASE_TYPE', 'sqlite')
    print(f"📋 当前数据库类型: {db_type}")
    
    # 创建应用实例
    app = create_app()
    
    print(f"🔗 数据库URI: {app.config['SQLALCHEMY_DATABASE_URI']}")
    print(f"⚙️  引擎选项: {app.config['SQLALCHEMY_ENGINE_OPTIONS']}")
    
    # 测试数据库连接
    with app.app_context():
        try:
            # 测试连接
            db.session.execute(text("SELECT 1"))
            print("✅ 数据库连接成功")
            
            # 检查权限表
            if db_type == 'sqlite':
                result = db.session.execute(text("PRAGMA table_info(permission_info)"))
                columns = [row[1] for row in result.fetchall()]
            else:
                result = db.session.execute(text("DESCRIBE permission_info"))
                columns = [row[0] for row in result.fetchall()]
            
            print(f"📊 权限表字段数量: {len(columns)}")
            
            # 检查关键字段
            key_fields = ['can_file_management', 'can_file_management_delete']
            missing_fields = [field for field in key_fields if field not in columns]
            
            if missing_fields:
                print(f"❌ 缺少关键字段: {missing_fields}")
            else:
                print("✅ 所有关键字段都存在")
                
        except Exception as e:
            print(f"❌ 数据库连接失败: {str(e)}")
    
    print("=" * 50)

if __name__ == '__main__':
    check_database_status()
