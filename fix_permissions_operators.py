#!/usr/bin/env python3
"""
修复权限记录的操作人信息
为没有时间和操作人字段的记录设置默认值
"""

from app_factory import create_app, db
from models.permission_info import PermissionInfo
from models.user_info import UserInfo
from datetime import datetime

app = create_app()

with app.app_context():
    try:
        # 获取第一个管理员用户ID
        admin_user = UserInfo.query.filter_by(is_admin=True).first()
        if not admin_user:
            admin_user = UserInfo.query.first()
        
        if not admin_user:
            print("❌ 没有找到任何用户，请先创建管理员用户")
            exit(1)
        
        default_user_id = admin_user.id
        print(f"✅ 使用默认用户: {admin_user.username} (ID: {default_user_id})")
        
        # 获取所有权限记录
        all_permissions = PermissionInfo.query.all()
        total = len(all_permissions)
        print(f"\n🔍 找到 {total} 条权限记录")
        
        # 修复没有create_time的记录
        permissions_missing_create_time = PermissionInfo.query.filter(
            (PermissionInfo.create_time == None) | (PermissionInfo.create_time == '')
        ).all()
        
        if permissions_missing_create_time:
            print(f"\n⚠️  发现 {len(permissions_missing_create_time)} 条记录缺少创建时间")
            for perm in permissions_missing_create_time:
                perm.create_time = datetime.now()
            db.session.commit()
            print(f"✅ 已修复 {len(permissions_missing_create_time)} 条记录的创建时间")
        else:
            print("✅ 所有记录都有创建时间")
        
        # 修复没有update_time的记录
        permissions_missing_update_time = PermissionInfo.query.filter(
            (PermissionInfo.update_time == None) | (PermissionInfo.update_time == '')
        ).all()
        
        if permissions_missing_update_time:
            print(f"\n⚠️  发现 {len(permissions_missing_update_time)} 条记录缺少更新时间")
            for perm in permissions_missing_update_time:
                perm.update_time = datetime.now()
            db.session.commit()
            print(f"✅ 已修复 {len(permissions_missing_update_time)} 条记录的更新时间")
        else:
            print("✅ 所有记录都有更新时间")
        
        # 修复没有created_by的记录
        permissions_missing_created_by = PermissionInfo.query.filter(
            (PermissionInfo.created_by == None) | (PermissionInfo.created_by == '')
        ).all()
        
        if permissions_missing_created_by:
            print(f"\n⚠️  发现 {len(permissions_missing_created_by)} 条记录缺少创建者信息")
            for perm in permissions_missing_created_by:
                perm.created_by = default_user_id
                print(f"   修复: 用户ID {perm.user_id} -> 创建者ID: {default_user_id}")
            db.session.commit()
            print(f"✅ 已修复 {len(permissions_missing_created_by)} 条记录的创建者信息")
        else:
            print("✅ 所有记录都有创建者信息")
        
        # 修复没有updated_by的记录
        permissions_missing_updated_by = PermissionInfo.query.filter(
            (PermissionInfo.updated_by == None) | (PermissionInfo.updated_by == '')
        ).all()
        
        if permissions_missing_updated_by:
            print(f"\n⚠️  发现 {len(permissions_missing_updated_by)} 条记录缺少修改者信息")
            for perm in permissions_missing_updated_by:
                perm.updated_by = perm.created_by or default_user_id
                print(f"   修复: 用户ID {perm.user_id} -> 修改者ID: {perm.updated_by}")
            db.session.commit()
            print(f"✅ 已修复 {len(permissions_missing_updated_by)} 条记录的修改者信息")
        else:
            print("✅ 所有记录都有修改者信息")
        
        # 验证修复结果
        print("\n🔍 验证修复结果:")
        total_fixed = PermissionInfo.query.filter(
            PermissionInfo.create_time != None,
            PermissionInfo.update_time != None,
            PermissionInfo.created_by != None,
            PermissionInfo.updated_by != None
        ).count()
        
        print(f"✅ 修复完成！")
        print(f"   总记录数: {total}")
        print(f"   修复后有效记录数: {total_fixed}")
        print(f"   修复率: {total_fixed/total*100:.2f}%")
        
    except Exception as e:
        print(f"❌ 修复过程中发生错误: {e}")
        import traceback
        traceback.print_exc()
        db.session.rollback()
