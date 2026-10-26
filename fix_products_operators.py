from app_factory import create_app
from models.finished_product import FinishedProduct
from models.user_info import UserInfo

# 创建Flask应用实例
app = create_app()

# 在应用上下文中运行修复操作
with app.app_context():
    from db import db


def fix_products_operators():
    """
    修复成品记录中的操作人信息
    1. 为没有创建者的记录设置默认创建者
    2. 为没有修改者的记录设置默认修改者
    """
    try:
        # 获取所有成品记录
        all_products = FinishedProduct.query.all()
        print(f"找到 {len(all_products)} 条成品记录")
        
        # 获取第一个管理员用户作为默认操作人
        admin_user = UserInfo.query.filter_by(is_admin=True).first()
        if not admin_user:
            # 如果没有管理员用户，获取第一个用户
            admin_user = UserInfo.query.first()
        
        if not admin_user:
            print("错误：没有找到用户记录，无法设置默认操作人")
            return
        
        print(f"使用默认操作人：{admin_user.username} (ID: {admin_user.id})")
        
        # 修复没有创建者的记录
        products_missing_created = FinishedProduct.query.filter(
            FinishedProduct.created_by == None
        ).all()
        
        print(f"找到 {len(products_missing_created)} 条记录缺少创建者")
        for product in products_missing_created:
            product.created_by = admin_user.id
            product.updated_by = admin_user.id
        
        # 修复没有修改者的记录
        products_missing_updated = FinishedProduct.query.filter(
            FinishedProduct.updated_by == None
        ).all()
        
        print(f"找到 {len(products_missing_updated)} 条记录缺少修改者")
        for product in products_missing_updated:
            product.updated_by = admin_user.id
        
        # 提交更改
        db.session.commit()
        print("修复完成！")
        
        # 验证修复结果
        total_fixed = FinishedProduct.query.filter(
            FinishedProduct.created_by != None,
            FinishedProduct.updated_by != None
        ).count()
        
        print(f"修复后，{total_fixed} 条记录有完整的操作人信息")
        
    except Exception as e:
        print(f"修复过程中出现错误：{str(e)}")
        db.session.rollback()


if __name__ == "__main__":
    with app.app_context():
        fix_products_operators()
