"""
EO版本比较工具

EO版本序列: a1, a2, a3, a4, a5, a6, a7, a8, a9, b1, b2, ... b9, c1, ... z9
规则: 先按字母排序(a < b < c < ... < z),同字母按数字排序(1 < 2 < ... < 9)
"""

import re

def parse_eo(eo_str):
    """
    解析EO字符串为 (major_letter, number) 元组用于排序
    
    Args:
        eo_str: EO字符串, 如 'a1', 'b3', 'd9'
        
    Returns:
        tuple: (letter_index, number) 用于比较, None表示无效EO
        
    示例:
        parse_eo('a1') -> (0, 1)
        parse_eo('b3') -> (1, 3)
        parse_eo('z9') -> (25, 9)
        parse_eo('') -> None
        parse_eo(None) -> None
    """
    if not eo_str or not isinstance(eo_str, str):
        return None
    
    eo_str = eo_str.strip()
    if not eo_str:
        return None
    
    # 匹配字母+数字模式: a1, b9, c5 等
    match = re.match(r'^([a-zA-Z])(\d+)$', eo_str)
    if match:
        letter = match.group(1).lower()
        number = int(match.group(2))
        letter_index = ord(letter) - ord('a')
        return (letter_index, number)
    
    # 其他格式: 直接转换为元组, 返回高排序值使其靠后
    # 如 'A1' (大写), '1a' (数字开头), 或其他特殊格式
    return None


def compare_eo(eo1, eo2):
    """
    比较两个EO版本的顺序
    
    Args:
        eo1: 第一个EO字符串
        eo2: 第二个EO字符串
        
    Returns:
        int: -1 (eo1 < eo2), 0 (eo1 == eo2), 1 (eo1 > eo2)
              None (无法比较)
              
    示例:
        compare_eo('a1', 'a2') -> -1
        compare_eo('b1', 'a9') -> 1
        compare_eo('c3', 'c3') -> 0
    """
    parsed1 = parse_eo(eo1)
    parsed2 = parse_eo(eo2)
    
    if parsed1 is None and parsed2 is None:
        return 0  # 都无法解析, 视为相等
    if parsed1 is None:
        return -1  # 无法解析的EO视为最旧
    if parsed2 is None:
        return 1  # 无法解析的EO视为最旧(当前为最新)
    
    if parsed1 < parsed2:
        return -1
    elif parsed1 > parsed2:
        return 1
    else:
        return 0


def is_newer_eo(new_eo, old_eo):
    """
    判断new_eo是否比old_eo更新
    
    Args:
        new_eo: 新EO版本
        old_eo: 旧EO版本
        
    Returns:
        bool: new_eo是否比old_eo更新
    """
    result = compare_eo(new_eo, old_eo)
    return result is not None and result > 0


def is_older_eo(eo1, eo2):
    """
    判断eo1是否比eo2更旧
    
    Args:
        eo1: 待判断的EO
        eo2: 基准EO
        
    Returns:
        bool: eo1是否比eo2更旧
    """
    result = compare_eo(eo1, eo2)
    return result is not None and result < 0


def get_sorted_eos(eo_list):
    """
    对EO列表进行排序(从旧到新)
    
    Args:
        eo_list: EO字符串列表
        
    Returns:
        list: 排序后的EO列表
    """
    parsed = [(parse_eo(eo), eo) for eo in eo_list]
    parsed.sort(key=lambda x: x[0] if x[0] is not None else (999, 999))
    return [eo for _, eo in parsed]


def find_older_versions(current_eo, all_eos):
    """
    找出所有比current_eo更旧的EO版本
    
    Args:
        current_eo: 当前EO版本
        all_eos: 所有EO版本列表
        
    Returns:
        list: 比current_eo更旧的EO列表
    """
    result = []
    for eo in all_eos:
        if eo != current_eo and is_older_eo(eo, current_eo):
            result.append(eo)
    return result
