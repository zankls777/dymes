#!/usr/bin/env python3
"""
CSRF 豁免修复脚本
用于安全地移除 CSRF 豁免并修复前端代码
"""

import os
import re
import shutil
from pathlib import Path

def backup_file(file_path):
    """备份文件"""
    backup_path = f"{file_path}.backup"
    shutil.copy2(file_path, backup_path)
    print(f"✅ 已备份: {file_path} -> {backup_path}")

def fix_csrf_exemptions():
    """修复 CSRF 豁免"""
    routes_file = "routes.py"
    
    if not os.path.exists(routes_file):
        print(f"❌ 文件不存在: {routes_file}")
        return
    
    # 备份原文件
    backup_file(routes_file)
    
    # 读取文件内容
    with open(routes_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 定义需要保留豁免的路由（只读操作）
    keep_exempt = [
        'get_headers',
        'get_first_subpno', 
        'get_usage_list_data',
        'get_vehicle_type',
        'get_finished_product',
        'get_finished_products',
        'get_material_summary_list',
        'get_material_summary_statistics',
        'check_existing_vehicle_types',
        'get_all_vehicle_type_ids',
        'get_user_permissions'
    ]
    
    # 需要移除豁免的路由（危险操作）
    remove_exempt = [
        'delete_vehicle_type',
        'batch_delete_vehicle_types', 
        'delete_finished_product',
        'batch_delete_finished_products',
        'update_vehicle_type',
        'update_finished_product',
        'update_user_permissions',
        'add_vehicle_type',
        'batch_add_vehicle_types',
        'add_finished_product',
        'batch_add_finished_products',
        'upload_vehicle_types_excel',
        'upload_finished_products_excel',
        'resolve_vehicle_conflicts',
        'resolve_finished_products_conflicts',
        'overwrite_finished_products_import',
        'import_valid_data_only',
        'finalize_upload_logs'
    ]
    
    # 移除危险操作的 CSRF 豁免
    for func_name in remove_exempt:
        pattern = f'@csrf_exempt_route\s*\n\s*def {func_name}\('
        replacement = f'def {func_name}('
        content = re.sub(pattern, replacement, content)
        print(f"🔒 已移除 {func_name} 的 CSRF 豁免")
    
    # 保留安全操作的 CSRF 豁免
    for func_name in keep_exempt:
        print(f"✅ 保留 {func_name} 的 CSRF 豁免")
    
    # 写回文件
    with open(routes_file, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print(f"✅ 已修复 {routes_file}")

def create_csrf_helper():
    """创建 CSRF 辅助函数"""
    helper_js = """
// CSRF Token 辅助函数
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') ||
           document.querySelector('input[name="csrf_token"]')?.value ||
           document.cookie.split('; ').find(row => row.startsWith('csrf_token='))?.split('=')[1];
}

function makeSecureRequest(url, options = {}) {
    const csrfToken = getCSRFToken();
    if (!csrfToken) {
        console.error('CSRF Token not found');
        return Promise.reject('CSRF Token not found');
    }
    
    return fetch(url, {
        ...options,
        headers: {
            'X-CSRFToken': csrfToken,
            'Content-Type': 'application/json',
            ...options.headers
        }
    });
}

// 全局错误处理
window.addEventListener('unhandledrejection', function(event) {
    if (event.reason && event.reason.includes('CSRF')) {
        alert('安全验证失败，请刷新页面后重试');
        window.location.reload();
    }
});
"""
    
    with open('static/js/csrf-helper.js', 'w', encoding='utf-8') as f:
        f.write(helper_js)
    
    print("✅ 已创建 CSRF 辅助函数: static/js/csrf-helper.js")

def main():
    """主函数"""
    print("🔧 开始修复 CSRF 豁免问题...")
    
    # 1. 修复后端豁免
    fix_csrf_exemptions()
    
    # 2. 创建前端辅助函数
    create_csrf_helper()
    
    print("\n📋 修复完成！接下来需要：")
    print("1. 在模板中引入 csrf-helper.js")
    print("2. 将 fetch() 调用替换为 makeSecureRequest()")
    print("3. 测试所有功能是否正常")
    print("4. 如果出现问题，可以使用 .backup 文件恢复")

if __name__ == "__main__":
    main()
