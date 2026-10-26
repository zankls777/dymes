#!/usr/bin/env python3
"""
数据库快速切换工具
"""

import os
import sys
import subprocess

def switch_database(db_type):
    """切换数据库类型"""
    if db_type.lower() == 'sqlite':
        os.environ['DATABASE_TYPE'] = 'sqlite'
        # 清除MySQL相关环境变量
        for var in ['MYSQL_HOST', 'MYSQL_PORT', 'MYSQL_USER', 'MYSQL_PASSWORD', 'MYSQL_DATABASE']:
            os.environ.pop(var, None)
        print("✅ 已切换到SQLite数据库")
        
    elif db_type.lower() == 'mysql':
        os.environ['DATABASE_TYPE'] = 'mysql'
        # 设置MySQL默认参数
        os.environ.setdefault('MYSQL_HOST', 'localhost')
        os.environ.setdefault('MYSQL_PORT', '3306')
        os.environ.setdefault('MYSQL_USER', 'root')
        os.environ.setdefault('MYSQL_PASSWORD', 'xinyushidai')
        os.environ.setdefault('MYSQL_DATABASE', 'SubList')
        print("✅ 已切换到MySQL数据库")
        
    else:
        print("❌ 不支持的数据库类型，请使用 'sqlite' 或 'mysql'")
        return False
    
    return True

def show_status():
    """显示当前数据库状态"""
    db_type = os.environ.get('DATABASE_TYPE', 'sqlite')
    print(f"📋 当前数据库类型: {db_type}")
    
    if db_type == 'mysql':
        print(f"🏠 MySQL主机: {os.environ.get('MYSQL_HOST', 'localhost')}:{os.environ.get('MYSQL_PORT', '3306')}")
        print(f"👤 用户名: {os.environ.get('MYSQL_USER', 'root')}")
        print(f"🗄️  数据库: {os.environ.get('MYSQL_DATABASE', 'SubList')}")
    else:
        print("📁 数据库文件: app.db")

def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("🔍 数据库切换工具")
        print("=" * 30)
        show_status()
        print("\n💡 使用方法:")
        print("  python3 db_switch.py sqlite    # 切换到SQLite")
        print("  python3 db_switch.py mysql     # 切换到MySQL")
        print("  python3 db_switch.py status    # 查看状态")
        return
    
    command = sys.argv[1].lower()
    
    if command == 'status':
        show_status()
    elif command in ['sqlite', 'mysql']:
        if switch_database(command):
            show_status()
            print(f"\n💡 现在可以运行: python3 app.py")
    else:
        print("❌ 无效命令，请使用 'sqlite', 'mysql' 或 'status'")

if __name__ == '__main__':
    main()
