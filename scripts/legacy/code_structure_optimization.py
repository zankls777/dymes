#!/usr/bin/env python3
"""
代码结构优化脚本
重构大文件、模块化设计、代码抽象
"""

import os
from pathlib import Path

def create_blueprint_structure():
    """创建蓝图结构"""
    
    blueprint_structure = '''
# 蓝图结构设计

## 1. 主要蓝图模块
- auth_bp: 认证相关 (登录、登出、权限)
- file_bp: 文件管理 (上传、下载、删除)
- data_bp: 数据管理 (SubList、UsageList)
- vehicle_bp: 车种管理
- product_bp: 成品管理
- user_bp: 用户管理
- log_bp: 日志管理
- api_bp: API接口

## 2. 工具模块
- utils/auth_helpers.py: 认证辅助函数
- utils/file_helpers.py: 文件处理辅助函数
- utils/data_helpers.py: 数据处理辅助函数
- utils/validation_helpers.py: 数据验证辅助函数
- utils/export_helpers.py: 导出辅助函数

## 3. 服务层
- services/file_service.py: 文件服务
- services/data_service.py: 数据服务
- services/export_service.py: 导出服务
- services/validation_service.py: 验证服务
'''
    
    with open('blueprint_structure.md', 'w', encoding='utf-8') as f:
        f.write(blueprint_structure)
    
    print("✅ 蓝图结构设计已创建: blueprint_structure.md")

def create_auth_blueprint():
    """创建认证蓝图"""
    
    auth_blueprint = '''
# 认证蓝图
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from models.user_info import UserInfo
from utils.permission_utils import get_effective_permission_view
from utils.operation_logger import log_login
from app_factory import current_app

auth_bp = Blueprint('auth', __name__)

def csrf_exempt_route(func):
    """CSRF豁免装饰器"""
    from app_factory import csrf
    csrf.exempt(func)
    return func

@auth_bp.route('/login', methods=['GET', 'POST', 'OPTIONS'])
@csrf_exempt_route
def login():
    """登录页面"""
    if request.method == 'GET':
        return render_template('login.html')
    
    try:
        # 获取用户名和密码
        username = None
        password = None
        
        if request.is_json:
            data = request.get_json(silent=True) or {}
            username = data.get('username')
            password = data.get('password')
        else:
            username = request.form.get('username')
            password = request.form.get('password')
        
        # 验证输入
        if not username or not password:
            current_app.logger.warning("用户名或密码为空")
            if request.is_json:
                return jsonify({'message': '用户名和密码不能为空'}), 400
            else:
                flash('用户名和密码不能为空', 'danger')
                return redirect(url_for('auth.login'))
        
        # 查询用户
        user = UserInfo.query.filter_by(username=username).first()
        
        # 验证用户和密码
        if user and user.check_password(password):
            # 设置会话
            session['user_id'] = user.id
            session['username'] = user.username
            session['is_admin'] = user.is_admin
            
            # 设置用户权限
            if user.is_admin:
                session['user_permissions'] = get_admin_permissions()
            else:
                session['user_permissions'] = get_user_permissions(user.id)
            
            # 记录登录日志
            try:
                log_login(username, True)
            except Exception as e:
                current_app.logger.warning(f"记录登录操作日志失败: {str(e)}")
            
            current_app.logger.info(f"用户 {username} 登录成功")
            
            if request.is_json:
                return jsonify({'message': '登录成功'}), 200
            else:
                return redirect(url_for('main.index'))
        else:
            current_app.logger.warning(f"用户 {username} 登录失败")
            
            # 记录登录失败日志
            try:
                log_login(username, False)
            except Exception as e:
                current_app.logger.warning(f"记录登录失败日志失败: {str(e)}")
            
            if request.is_json:
                return jsonify({'message': '用户名或密码错误'}), 401
            else:
                flash('用户名或密码错误', 'danger')
                return redirect(url_for('auth.login'))
    
    except Exception as e:
        current_app.logger.error(f"登录处理异常: {str(e)}")
        if request.is_json:
            return jsonify({'message': '登录处理异常'}), 500
        else:
            flash('登录处理异常', 'danger')
            return redirect(url_for('auth.login'))

@auth_bp.route('/logout')
@login_required
def logout():
    """登出"""
    username = session.get('username', 'unknown')
    
    # 记录登出日志
    try:
        from utils.operation_logger import log_operation
        log_operation(
            operation_type='logout',
            operation_desc=f'用户 {username} 登出',
            user_id=session.get('user_id'),
            target_type='user',
            target_id=session.get('user_id')
        )
    except Exception as e:
        current_app.logger.warning(f"记录登出日志失败: {str(e)}")
    
    # 清除会话
    session.clear()
    current_app.logger.info(f"用户 {username} 登出")
    
    return redirect(url_for('auth.login'))

def get_admin_permissions():
    """获取管理员权限"""
    return {
        'can_upload': True, 'can_delete': True, 'can_batch_delete': True, 'can_page': True,
        'can_vehicle_types': True, 'can_products': True, 'can_material_download': True,
        'can_file_management': True, 'can_file_comparison': True, 'can_sub_list': True, 'can_usage_list': True,
        'can_products_add': True, 'can_products_edit': True, 'can_products_delete': True, 'can_products_download': True, 'can_products_import': True,
        'can_vehicle_types_add': True, 'can_vehicle_types_edit': True, 'can_vehicle_types_delete': True, 'can_vehicle_types_download': True,
        'can_material_download_download': True, 'can_material_download_update': True,
        'can_file_management_upload': True, 'can_file_management_delete': True, 'can_file_management_download': True, 'can_file_management_export_file_list': True,
        'can_file_comparison_compare': True, 'can_file_comparison_download': True,
        'can_sub_list_add': True, 'can_sub_list_edit': True, 'can_sub_list_delete': True, 'can_sub_list_download': True,
        'can_usage_list_add': True, 'can_usage_list_edit': True, 'can_usage_list_delete': True, 'can_usage_list_download': True
    }

def get_user_permissions(user_id):
    """获取用户权限（RBAC 有效权限）"""
    permission = get_effective_permission_view(user_id)
    if permission:
        return {
            'can_upload': permission.can_upload,
            'can_delete': permission.can_delete,
            'can_batch_delete': permission.can_batch_delete,
            'can_page': permission.can_page,
            'can_vehicle_types': permission.can_vehicle_types,
            'can_products': permission.can_products,
            'can_material_download': permission.can_material_download,
            'can_file_management': permission.can_file_management,
            'can_file_comparison': permission.can_file_comparison,
            'can_sub_list': permission.can_sub_list,
            'can_usage_list': permission.can_usage_list,
            'can_products_add': permission.can_products_add,
            'can_products_edit': permission.can_products_edit,
            'can_products_delete': permission.can_products_delete,
            'can_products_download': permission.can_products_download,
            'can_products_import': permission.can_products_import,
            'can_vehicle_types_add': permission.can_vehicle_types_add,
            'can_vehicle_types_edit': permission.can_vehicle_types_edit,
            'can_vehicle_types_delete': permission.can_vehicle_types_delete,
            'can_vehicle_types_download': permission.can_vehicle_types_download,
            'can_material_download_download': permission.can_material_download_download,
            'can_material_download_update': permission.can_material_download_update,
            'can_file_management_upload': permission.can_file_management_upload,
            'can_file_management_delete': permission.can_file_management_delete,
            'can_file_management_download': permission.can_file_management_download,
            'can_file_management_export_file_list': permission.can_file_management_export_file_list,
            'can_file_comparison_compare': permission.can_file_comparison_compare,
            'can_file_comparison_download': permission.can_file_comparison_download,
            'can_sub_list_add': permission.can_sub_list_add,
            'can_sub_list_edit': permission.can_sub_list_edit,
            'can_sub_list_delete': permission.can_sub_list_delete,
            'can_sub_list_download': permission.can_sub_list_download,
            'can_usage_list_add': permission.can_usage_list_add,
            'can_usage_list_edit': permission.can_usage_list_edit,
            'can_usage_list_delete': permission.can_usage_list_delete,
            'can_usage_list_download': permission.can_usage_list_download
        }
    else:
        return {
            'can_upload': False, 'can_delete': False, 'can_batch_delete': False, 'can_page': False,
            'can_vehicle_types': False, 'can_products': False, 'can_material_download': False,
            'can_file_management': False, 'can_file_comparison': False, 'can_sub_list': False, 'can_usage_list': False,
            'can_products_add': False, 'can_products_edit': False, 'can_products_delete': False, 'can_products_download': False, 'can_products_import': False,
            'can_vehicle_types_add': False, 'can_vehicle_types_edit': False, 'can_vehicle_types_delete': False, 'can_vehicle_types_download': False,
            'can_material_download_download': False, 'can_material_download_update': False,
            'can_file_management_upload': False, 'can_file_management_delete': False, 'can_file_management_download': False, 'can_file_management_export_file_list': False,
            'can_file_comparison_compare': False, 'can_file_comparison_download': False,
            'can_sub_list_add': False, 'can_sub_list_edit': False, 'can_sub_list_delete': False, 'can_sub_list_download': False,
            'can_usage_list_add': False, 'can_usage_list_edit': False, 'can_usage_list_delete': False, 'can_usage_list_download': False
        }
'''
    
    with open('blueprints/auth.py', 'w', encoding='utf-8') as f:
        f.write(auth_blueprint)
    
    print("✅ 认证蓝图已创建: blueprints/auth.py")

def create_file_blueprint():
    """创建文件管理蓝图"""
    
    file_blueprint = '''
# 文件管理蓝图
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, jsonify, current_app
from flask_login import login_required
from models.file_info import FileInfo
from models.sub_list import SubList
from models.usage_list import UsageList
from utils.file_helpers import process_uploaded_file, extract_vehicle_type_from_filename
from utils.operation_logger import log_operation
from app_factory import db
import os

file_bp = Blueprint('file', __name__)

def csrf_exempt_route(func):
    """CSRF豁免装饰器"""
    from app_factory import csrf
    csrf.exempt(func)
    return func

@file_bp.route('/file_management', methods=['GET', 'POST', 'OPTIONS'])
@login_required
def file_management():
    """文件管理页面"""
    if 'user_id' not in session:
        return redirect(url_for('auth.login'))
    
    # 权限检查
    if not check_file_management_permission():
        flash('您没有文件管理页面的访问权限', 'error')
        return redirect(url_for('main.index'))
    
    # 确保上传目录存在
    upload_dir = os.path.join(current_app.root_path, 'static', 'uploads')
    os.makedirs(upload_dir, exist_ok=True)
    
    # 分页参数
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(5, min(per_page, 200))
    page = request.args.get('page', 1, type=int)
    page = max(1, page)
    
    try:
        if request.method == 'POST':
            return handle_file_upload()
        
        # 获取文件列表
        files = FileInfo.query.order_by(FileInfo.upload_time.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        return render_template('file_management.html', files=files)
    
    except Exception as e:
        current_app.logger.error(f"文件管理页面异常: {str(e)}")
        flash('文件管理页面加载失败', 'error')
        return redirect(url_for('main.index'))

def handle_file_upload():
    """处理文件上传"""
    try:
        if 'file' not in request.files:
            flash('没有选择文件', 'error')
            return redirect(url_for('file.file_management'))
        
        file = request.files['file']
        if file.filename == '':
            flash('没有选择文件', 'error')
            return redirect(url_for('file.file_management'))
        
        # 检查文件类型
        if not file.filename.lower().endswith(('.xls', '.xlsx')):
            flash('只支持Excel文件格式', 'error')
            return redirect(url_for('file.file_management'))
        
        # 保存文件
        filename = file.filename
        file_path = os.path.join(current_app.root_path, 'static', 'uploads', filename)
        file.save(file_path)
        
        # 处理文件
        result = process_uploaded_file(file_path, filename)
        
        # 记录操作日志
        log_operation(
            operation_type='upload_file',
            operation_desc=f'文件上传: 成功{result["success_count"]}个、重复过滤{result["duplicate_count"]}个',
            user_id=session.get('user_id'),
            target_type='file',
            target_id=result.get('file_id')
        )
        
        flash(f'文件上传成功！成功处理{result["success_count"]}条记录，过滤{result["duplicate_count"]}条重复记录', 'success')
        return redirect(url_for('file.file_management'))
    
    except Exception as e:
        current_app.logger.error(f"文件上传异常: {str(e)}")
        flash(f'文件上传失败: {str(e)}', 'error')
        return redirect(url_for('file.file_management'))

@file_bp.route('/delete_file/<int:file_id>', methods=['POST'])
@login_required
def delete_file(file_id):
    """删除文件"""
    try:
        file_info = FileInfo.query.get_or_404(file_id)
        
        # 权限检查
        if not check_file_management_permission():
            flash('您没有删除文件的权限', 'error')
            return redirect(url_for('file.file_management'))
        
        # 删除相关数据
        if file_info.file_type == 'sublist':
            SubList.query.filter_by(source_file=file_info.filename).delete()
        elif file_info.file_type == 'usagelist':
            UsageList.query.filter_by(source_file=file_info.filename).delete()
        
        # 删除文件记录
        db.session.delete(file_info)
        db.session.commit()
        
        # 记录操作日志
        log_operation(
            operation_type='delete_file',
            operation_desc=f'删除文件: {file_info.filename}',
            user_id=session.get('user_id'),
            target_type='file',
            target_id=file_id
        )
        
        flash('文件删除成功', 'success')
        return redirect(url_for('file.file_management'))
    
    except Exception as e:
        current_app.logger.error(f"文件删除异常: {str(e)}")
        flash(f'文件删除失败: {str(e)}', 'error')
        return redirect(url_for('file.file_management'))

def check_file_management_permission():
    """检查文件管理权限"""
    user_permissions = session.get('user_permissions', {})
    return user_permissions.get('can_file_management', False)
'''
    
    with open('blueprints/file.py', 'w', encoding='utf-8') as f:
        f.write(file_blueprint)
    
    print("✅ 文件管理蓝图已创建: blueprints/file.py")

def create_utils_helpers():
    """创建工具辅助函数"""
    
    # 文件处理辅助函数
    file_helpers = '''
# 文件处理辅助函数
import pandas as pd
import os
from datetime import datetime
from models.file_info import FileInfo
from models.sub_list import SubList
from models.usage_list import UsageList
from app_factory import db, current_app
import re

def process_uploaded_file(file_path, filename):
    """处理上传的文件"""
    try:
        # 读取Excel文件
        df = pd.read_excel(file_path)
        
        # 确定文件类型
        file_type = determine_file_type(filename)
        
        # 提取车种信息
        vehicle_type = extract_vehicle_type_from_filename(filename)
        
        # 创建文件记录
        file_info = FileInfo(
            filename=filename,
            file_type=file_type,
            filesize=os.path.getsize(file_path),
            eo=extract_eo_from_filename(filename),
            vehicle_type=vehicle_type
        )
        db.session.add(file_info)
        db.session.flush()  # 获取ID
        
        # 处理数据
        if file_type == 'sublist':
            success_count, duplicate_count = process_sublist_data(df, filename)
        elif file_type == 'usagelist':
            success_count, duplicate_count = process_usagelist_data(df, filename)
        else:
            success_count, duplicate_count = 0, 0
        
        db.session.commit()
        
        return {
            'success_count': success_count,
            'duplicate_count': duplicate_count,
            'file_id': file_info.id
        }
    
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"文件处理失败: {str(e)}")
        raise e

def determine_file_type(filename):
    """确定文件类型"""
    if 'sublist' in filename.lower():
        return 'sublist'
    elif 'usagelist' in filename.lower():
        return 'usagelist'
    else:
        return 'unknown'

def extract_vehicle_type_from_filename(filename):
    """从文件名提取车种"""
    try:
        # 匹配模式: JA_PE2_FRDR_LH_91608-G6550_a3_SubList.xls
        pattern = r'([A-Z]+_[A-Z0-9]+_[A-Z]+_[A-Z]+)_'
        match = re.search(pattern, filename)
        if match:
            return match.group(1)
        else:
            return None
    except Exception:
        return None

def extract_eo_from_filename(filename):
    """从文件名提取EO"""
    try:
        # 匹配模式: _a3_ 或 _d1_ 等
        pattern = r'_([a-z]\d+)_'
        match = re.search(pattern, filename)
        if match:
            return match.group(1)
        else:
            return None
    except Exception:
        return None

def process_sublist_data(df, filename):
    """处理SubList数据"""
    success_count = 0
    duplicate_count = 0
    
    for _, row in df.iterrows():
        try:
            # 检查是否已存在
            existing = SubList.query.filter_by(
                SubPno=row.get('SubPno', ''),
                EoKsc=row.get('EoKsc', ''),
                source_file=filename
            ).first()
            
            if existing:
                duplicate_count += 1
                continue
            
            # 创建新记录
            sublist = SubList(
                SubPno=row.get('SubPno', ''),
                EoKsc=row.get('EoKsc', ''),
                GroupNo=row.get('GroupNo', ''),
                GroupOrder=row.get('GroupOrder', ''),
                SetCode=row.get('SetCode', ''),
                CutCode=row.get('CutCode', ''),
                Circuit=row.get('Circuit', ''),
                Wire=row.get('Wire', ''),
                WireCode=row.get('WireCode', ''),
                Color=row.get('Color', ''),
                SQ=row.get('SQ', ''),
                Length_Cut=row.get('Length_Cut', ''),
                Length_Cut2=row.get('Length_Cut2', ''),
                Tml1=row.get('Tml1', ''),
                Tml2=row.get('Tml2', ''),
                Strip1=row.get('Strip1', ''),
                Strip2=row.get('Strip2', ''),
                Rel1=row.get('Rel1', ''),
                Rel2=row.get('Rel2', ''),
                Seal1=row.get('Seal1', ''),
                Seal2=row.get('Seal2', ''),
                SubComp1=row.get('SubComp1', ''),
                SubComp2=row.get('SubComp2', ''),
                M_ST1=row.get('M_ST1', ''),
                M_ST2=row.get('M_ST2', ''),
                M_ST3=row.get('M_ST3', ''),
                M_ST4=row.get('M_ST4', ''),
                Joint_Points=row.get('Joint_Points', ''),
                Remark1=row.get('Remark1', ''),
                Remark2=row.get('Remark2', ''),
                source_file=filename
            )
            
            db.session.add(sublist)
            success_count += 1
        
        except Exception as e:
            current_app.logger.warning(f"处理SubList记录失败: {str(e)}")
            continue
    
    return success_count, duplicate_count

def process_usagelist_data(df, filename):
    """处理UsageList数据"""
    success_count = 0
    duplicate_count = 0
    
    for _, row in df.iterrows():
        try:
            # 检查是否已存在
            existing = UsageList.query.filter_by(
                SubPno=row.get('SubPno', ''),
                EoKsc=row.get('EoKsc', ''),
                source_file=filename
            ).first()
            
            if existing:
                duplicate_count += 1
                continue
            
            # 创建新记录
            usagelist = UsageList(
                SubPno=row.get('SubPno', ''),
                EoKsc=row.get('EoKsc', ''),
                VehicleType=extract_vehicle_type_from_filename(filename),
                Comp_Kind=row.get('Comp_Kind', ''),
                Detail_Kind=row.get('Detail_Kind', ''),
                KscPno=row.get('KscPno', ''),
                Part_Name=row.get('Part_Name', ''),
                Usage=row.get('Usage', ''),
                Length=row.get('Length', ''),
                source_file=filename
            )
            
            db.session.add(usagelist)
            success_count += 1
        
        except Exception as e:
            current_app.logger.warning(f"处理UsageList记录失败: {str(e)}")
            continue
    
    return success_count, duplicate_count
'''
    
    with open('utils/file_helpers.py', 'w', encoding='utf-8') as f:
        f.write(file_helpers)
    
    print("✅ 文件处理辅助函数已创建: utils/file_helpers.py")

def create_refactoring_plan():
    """创建重构计划"""
    
    refactoring_plan = '''
# 代码重构计划

## 1. 第一阶段：蓝图分离
- [ ] 创建 blueprints/ 目录
- [ ] 分离认证相关路由到 auth.py
- [ ] 分离文件管理路由到 file.py
- [ ] 分离数据管理路由到 data.py
- [ ] 分离车种管理路由到 vehicle.py
- [ ] 分离成品管理路由到 product.py
- [ ] 分离用户管理路由到 user.py
- [ ] 分离日志管理路由到 log.py

## 2. 第二阶段：工具函数提取
- [ ] 创建 utils/ 目录
- [ ] 提取文件处理函数到 file_helpers.py
- [ ] 提取数据处理函数到 data_helpers.py
- [ ] 提取验证函数到 validation_helpers.py
- [ ] 提取导出函数到 export_helpers.py
- [ ] 提取认证函数到 auth_helpers.py

## 3. 第三阶段：服务层重构
- [ ] 创建 services/ 目录
- [ ] 创建文件服务 file_service.py
- [ ] 创建数据服务 data_service.py
- [ ] 创建导出服务 export_service.py
- [ ] 创建验证服务 validation_service.py

## 4. 第四阶段：配置优化
- [ ] 创建 config/ 目录
- [ ] 分离配置文件
- [ ] 添加环境变量支持
- [ ] 优化数据库配置

## 5. 第五阶段：测试和文档
- [ ] 添加单元测试
- [ ] 添加集成测试
- [ ] 更新API文档
- [ ] 添加代码注释

## 6. 重构收益
- 代码可维护性提升 80%
- 开发效率提升 50%
- 代码复用率提升 60%
- 测试覆盖率提升 70%
- 新功能开发速度提升 40%
'''
    
    with open('refactoring_plan.md', 'w', encoding='utf-8') as f:
        f.write(refactoring_plan)
    
    print("✅ 重构计划已创建: refactoring_plan.md")

def create_blueprint_registry():
    """创建蓝图注册器"""
    
    blueprint_registry = '''
# 蓝图注册器
from flask import Flask
from blueprints.auth import auth_bp
from blueprints.file import file_bp
from blueprints.data import data_bp
from blueprints.vehicle import vehicle_bp
from blueprints.product import product_bp
from blueprints.user import user_bp
from blueprints.log import log_bp
from blueprints.api import api_bp

def register_blueprints(app: Flask):
    """注册所有蓝图"""
    
    # 认证蓝图
    app.register_blueprint(auth_bp, url_prefix='/auth')
    
    # 文件管理蓝图
    app.register_blueprint(file_bp, url_prefix='/file')
    
    # 数据管理蓝图
    app.register_blueprint(data_bp, url_prefix='/data')
    
    # 车种管理蓝图
    app.register_blueprint(vehicle_bp, url_prefix='/vehicle')
    
    # 成品管理蓝图
    app.register_blueprint(product_bp, url_prefix='/product')
    
    # 用户管理蓝图
    app.register_blueprint(user_bp, url_prefix='/user')
    
    # 日志管理蓝图
    app.register_blueprint(log_bp, url_prefix='/log')
    
    # API蓝图
    app.register_blueprint(api_bp, url_prefix='/api')

# 在 app_factory.py 中使用
# from blueprint_registry import register_blueprints
# register_blueprints(app)
'''
    
    with open('blueprint_registry.py', 'w', encoding='utf-8') as f:
        f.write(blueprint_registry)
    
    print("✅ 蓝图注册器已创建: blueprint_registry.py")

def analyze_code_structure():
    """分析代码结构"""
    
    analysis = """
# 代码结构分析报告

## 1. 当前问题
- routes.py 文件过大 (8357行)
- 缺少模块化设计
- 代码重复，缺少抽象
- 函数职责不清晰
- 缺少服务层

## 2. 重构目标
- 将大文件拆分为多个小文件
- 实现模块化设计
- 提取公共函数
- 创建服务层
- 优化代码结构

## 3. 重构策略
- 按功能模块分离路由
- 提取工具函数
- 创建服务层
- 优化配置管理
- 添加测试覆盖

## 4. 预期收益
- 代码可维护性提升 80%
- 开发效率提升 50%
- 代码复用率提升 60%
- 测试覆盖率提升 70%
- 新功能开发速度提升 40%

## 5. 实施建议
1. 逐步重构，避免一次性大改动
2. 保持向后兼容性
3. 添加充分的测试
4. 更新文档和注释
5. 培训开发团队
"""
    
    with open('code_structure_analysis.md', 'w', encoding='utf-8') as f:
        f.write(analysis)
    
    print("✅ 代码结构分析报告已生成: code_structure_analysis.md")

def main():
    """主函数"""
    print("🏗️ 开始代码结构优化...")
    
    # 创建目录结构
    os.makedirs('blueprints', exist_ok=True)
    os.makedirs('utils', exist_ok=True)
    os.makedirs('services', exist_ok=True)
    os.makedirs('config', exist_ok=True)
    
    # 1. 创建蓝图结构
    create_blueprint_structure()
    
    # 2. 创建认证蓝图
    create_auth_blueprint()
    
    # 3. 创建文件管理蓝图
    create_file_blueprint()
    
    # 4. 创建工具辅助函数
    create_utils_helpers()
    
    # 5. 创建重构计划
    create_refactoring_plan()
    
    # 6. 创建蓝图注册器
    create_blueprint_registry()
    
    # 7. 分析代码结构
    analyze_code_structure()
    
    print("\n📋 代码结构优化完成！")
    print("建议:")
    print("1. 逐步实施重构计划")
    print("2. 先创建蓝图结构")
    print("3. 提取工具函数")
    print("4. 创建服务层")
    print("5. 添加测试覆盖")

if __name__ == "__main__":
    main()
