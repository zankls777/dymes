#!/usr/bin/env python3
"""
检查资材量下载页面表格行的列顺序
"""

with open('templates/material_download.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 查找displayMaterialList函数
func_start = content.find('function displayMaterialList')
if func_start == -1:
    print("❌ 未找到displayMaterialList函数")
    exit(1)

func_end = content.find('}', func_start)
if func_end == -1:
    print("❌ 未找到displayMaterialList函数的结束位置")
    exit(1)

func_content = content[func_start:func_end+1]

# 查找row.innerHTML
row_html_start = func_content.find('row.innerHTML')
if row_html_start == -1:
    print("❌ 未找到row.innerHTML")
    exit(1)

row_html_end = func_content.find(';', row_html_start)
if row_html_end == -1:
    print("❌ 未找到row.innerHTML的结束位置")
    exit(1)

row_html = func_content[row_html_start:row_html_end+1]

print("✅ 找到row.innerHTML的定义:")
print(row_html)

# 检查列顺序
print("\n🔍 检查列顺序:")
columns = [
    'sequenceNumber',
    'vehicle_name',
    'sub_pno',
    'comp_kind',
    'total_quantity',  # 数量列
    'remark',          # 备注列
    'last_update_time',
    '操作按钮'
]

# 检查每个列的位置
for col in columns:
    if col in row_html:
        print(f"✅ 找到 '{col}' 列")
    else:
        print(f"❌ 未找到 '{col}' 列")

# 打印完整的row.innerHTML内容，便于手动检查
print("\n📋 完整的row.innerHTML内容:")
print(row_html)
