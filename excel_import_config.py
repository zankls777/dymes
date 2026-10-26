#!/usr/bin/env python3
"""
Excel导入配置文件
管理去重策略和导入规则
"""

import os
from typing import Dict, Any, List


class ExcelImportConfig:
    """Excel导入配置类"""
    
    def __init__(self):
        # 基础配置
        self.auto_dedup = os.environ.get('EXCEL_AUTO_DEDUP', 'true').lower() == 'true'
        self.strict_dedup = os.environ.get('EXCEL_DEDUP_STRICT', 'false').lower() == 'true'
        self.quantity_merge = os.environ.get('EXCEL_QUANTITY_MERGE', 'true').lower() == 'true'
        
        # 去重策略配置
        self.dedup_strategies = {
            'identical_rows': {
                'enabled': True,
                'description': '完全相同的行自动去重'
            },
            'quantity_merge': {
                'enabled': self.quantity_merge,
                'description': '相同LOT号但数量不同时，累加数量'
            },
            'vehicle_name_priority': {
                'enabled': True,
                'description': '优先保留有车种信息的行'
            },
            'remark_merge': {
                'enabled': True,
                'description': '合并备注信息'
            }
        }
        
        # 字段权重配置（用于智能去重）
        self.field_weights = {
            'vehicle_name': 3,      # 车种名称权重最高
            'part_number': 5,       # 品番必须完全匹配
            'lot_number': 5,        # LOT号必须完全匹配
            'quantity': 2,          # 数量可以累加
            'remark': 1             # 备注权重最低
        }
        
        # 导入规则配置
        self.import_rules = {
            'skip_empty_lot': True,      # 跳过LOT号为空的记录
            'skip_empty_part': True,     # 跳过品番为空的记录
            'allow_empty_vehicle': True, # 允许车种为空
            'validate_quantity': True,   # 验证数量有效性
            'min_quantity': 0,          # 最小数量
            'max_quantity': 999999      # 最大数量
        }
    
    def should_auto_dedup(self, duplicate_type: str) -> bool:
        """判断是否应该自动去重"""
        if not self.auto_dedup:
            return False
            
        if duplicate_type == 'identical_rows':
            return self.dedup_strategies['identical_rows']['enabled']
        elif duplicate_type == 'quantity_merge':
            return self.dedup_strategies['quantity_merge']['enabled']
        
        return False
    
    def get_merge_strategy(self, duplicate_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        """获取合并策略"""
        if not duplicate_rows:
            return {}
        
        first_row = duplicate_rows[0]
        lot_number = first_row['lot_number']
        
        # 检查是否所有行都完全相同
        all_identical = all(
            row['vehicle_name'] == first_row['vehicle_name'] and
            row['part_number'] == first_row['part_number'] and
            row['quantity'] == first_row['quantity'] and
            row['remark'] == first_row['remark']
            for row in duplicate_rows
        )
        
        if all_identical:
            return {
                'strategy': 'keep_one',
                'description': f'LOT号 {lot_number} 有 {len(duplicate_rows)} 行完全相同的重复数据，保留一条',
                'merged_row': first_row
            }
        
        # 检查是否可以合并数量
        if self.quantity_merge and len(duplicate_rows) > 1:
            # 检查除了数量之外的其他字段是否相同
            can_merge_quantity = all(
                row['vehicle_name'] == first_row['vehicle_name'] and
                row['part_number'] == first_row['part_number'] and
                row['remark'] == first_row['remark']
                for row in duplicate_rows
            )
            
            if can_merge_quantity:
                total_quantity = sum(row['quantity'] for row in duplicate_rows)
                merged_row = {
                    'index': first_row.get('index', 0),
                    'vehicle_name': first_row['vehicle_name'],
                    'part_number': first_row['part_number'],
                    'lot_number': first_row['lot_number'],
                    'quantity': total_quantity,
                    'remark': f"合并自{len(duplicate_rows)}行数据，原数量：{', '.join(str(row['quantity']) for row in duplicate_rows)}",
                    'has_vehicle_name': bool(first_row['vehicle_name'])
                }
                
                return {
                    'strategy': 'merge_quantity',
                    'description': f'LOT号 {lot_number} 合并 {len(duplicate_rows)} 行数据，总数量：{total_quantity}',
                    'merged_row': merged_row
                }
        
        # 无法自动处理，需要用户选择
        return {
            'strategy': 'user_choice',
            'description': f'LOT号 {lot_number} 有 {len(duplicate_rows)} 行数据需要用户选择',
            'duplicate_rows': duplicate_rows
        }
    
    def validate_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """验证数据行"""
        errors = []
        warnings = []
        
        # 检查必填字段
        if not row.get('part_number'):
            errors.append('品番不能为空')
        if not row.get('lot_number'):
            errors.append('LOT号不能为空')
        
        # 检查数量
        if self.import_rules['validate_quantity']:
            try:
                quantity = int(row.get('quantity', 0))
                if quantity < self.import_rules['min_quantity']:
                    errors.append(f'数量不能小于 {self.import_rules["min_quantity"]}')
                if quantity > self.import_rules['max_quantity']:
                    errors.append(f'数量不能大于 {self.import_rules["max_quantity"]}')
            except (ValueError, TypeError):
                errors.append('数量必须是有效的数字')
        
        # 检查车种
        if not self.import_rules['allow_empty_vehicle'] and not row.get('vehicle_name'):
            warnings.append('车种为空，建议填写')
        
        return {
            'valid': len(errors) == 0,
            'errors': errors,
            'warnings': warnings
        }
    
    def get_config_summary(self) -> Dict[str, Any]:
        """获取配置摘要"""
        return {
            'auto_dedup': self.auto_dedup,
            'strict_dedup': self.strict_dedup,
            'quantity_merge': self.quantity_merge,
            'dedup_strategies': self.dedup_strategies,
            'import_rules': self.import_rules
        }


# 全局配置实例
excel_config = ExcelImportConfig()


def get_excel_config() -> ExcelImportConfig:
    """获取Excel导入配置实例"""
    return excel_config


def update_excel_config(**kwargs) -> None:
    """更新Excel导入配置"""
    for key, value in kwargs.items():
        if hasattr(excel_config, key):
            setattr(excel_config, key, value)
        elif key in excel_config.dedup_strategies:
            excel_config.dedup_strategies[key]['enabled'] = value
        elif key in excel_config.import_rules:
            excel_config.import_rules[key] = value


if __name__ == '__main__':
    # 测试配置
    config = get_excel_config()
    print("Excel导入配置:")
    print(f"自动去重: {config.auto_dedup}")
    print(f"严格去重: {config.strict_dedup}")
    print(f"数量合并: {config.quantity_merge}")
    print("\n去重策略:")
    for name, strategy in config.dedup_strategies.items():
        print(f"  {name}: {strategy['enabled']} - {strategy['description']}")
    print("\n导入规则:")
    for name, value in config.import_rules.items():
        print(f"  {name}: {value}")
