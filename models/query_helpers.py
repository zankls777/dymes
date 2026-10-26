
# 在 models/ 目录下创建 query_helpers.py

from flask_sqlalchemy import Pagination
from typing import List, Optional, Dict, Any

class QueryHelper:
    """查询辅助类"""
    
    @staticmethod
    def paginate_query(query, page: int = 1, per_page: int = 1000, max_per_page: int = 10000):
        """分页查询，防止内存溢出"""
        if per_page > max_per_page:
            per_page = max_per_page
        
        return query.paginate(
            page=page, 
            per_page=per_page, 
            error_out=False
        )
    
    @staticmethod
    def batch_query(query, batch_size: int = 1000):
        """批量查询，用于大数据量处理"""
        offset = 0
        while True:
            batch = query.offset(offset).limit(batch_size).all()
            if not batch:
                break
            yield batch
            offset += batch_size
    
    @staticmethod
    def count_query(query) -> int:
        """获取查询结果数量"""
        return query.count()
    
    @staticmethod
    def exists_query(query) -> bool:
        """检查查询结果是否存在"""
        return query.first() is not None

# 使用示例：
# 替换 VehicleType.query.all()
# 为 QueryHelper.paginate_query(VehicleType.query, page=1, per_page=1000)
