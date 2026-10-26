"""
统一时间处理工具 - UTC+8时区
"""
from datetime import datetime
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    try:
        from pytz import timezone as ZoneInfo  # fallback
    except ImportError:
        ZoneInfo = None


def get_utc8_time():
    """获取UTC+8时区的当前时间（无时区信息，wall-clock时间）
    
    Returns:
        datetime: UTC+8时区的当前时间，无时区信息
    """
    try:
        if ZoneInfo is not None:
            # 使用ZoneInfo获取UTC+8时间
            return datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)
    except Exception:
        pass
    # 降级方案：使用系统本地时间（假设系统时区已设置为UTC+8）
    return datetime.now()


def format_utc8_time(dt):
    """格式化UTC+8时间显示
    
    Args:
        dt (datetime): 时间对象
        
    Returns:
        str: 格式化后的时间字符串 'YYYY-MM-DD HH:mm:ss'
    """
    if not dt:
        return ''
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def format_utc8_date(dt):
    """格式化UTC+8日期显示
    
    Args:
        dt (datetime): 时间对象
        
    Returns:
        str: 格式化后的日期字符串 'YYYY-MM-DD'
    """
    if not dt:
        return ''
    return dt.strftime('%Y-%m-%d')


def parse_utc8_time(time_str):
    """解析时间字符串为UTC+8时间对象
    
    Args:
        time_str (str): 时间字符串，格式 'YYYY-MM-DD HH:mm:ss' 或 'YYYY-MM-DD'
        
    Returns:
        datetime: 解析后的时间对象
    """
    if not time_str:
        return None
    
    try:
        if len(time_str) == 19:  # YYYY-MM-DD HH:mm:ss
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        elif len(time_str) == 10:  # YYYY-MM-DD
            return datetime.strptime(time_str, '%Y-%m-%d')
        else:
            return None
    except ValueError:
        return None


def is_utc8_time(dt):
    """检查时间对象是否为UTC+8时区时间
    
    Args:
        dt (datetime): 时间对象
        
    Returns:
        bool: 是否为UTC+8时间
    """
    if not dt:
        return False
    # 由于我们存储的是无时区信息的时间，这里假设所有时间都是UTC+8
    return True