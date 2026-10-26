#!/usr/bin/env python3
"""
高性能查询缓存模块
为 sub_list 和 usage_list 页面提供缓存支持
结合内存缓存和数据库查询优化
"""

import time
import hashlib
import json
from functools import wraps
from collections import OrderedDict
from threading import RLock
from flask import current_app


class LRUCache:
    """线程安全的LRU缓存实现"""
    
    def __init__(self, max_size=1000, default_ttl=300):
        self._cache = OrderedDict()
        self._max_size = max_size
        self._default_ttl = default_ttl
        self._lock = RLock()
        self._hits = 0
        self._misses = 0
    
    def get(self, key):
        """获取缓存值"""
        with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            value, expire_at = self._cache[key]
            now = time.time()
            
            if now > expire_at:
                # 缓存过期，删除
                del self._cache[key]
                self._misses += 1
                return None
            
            # 移动到末尾（最近使用）
            self._cache.move_to_end(key)
            self._hits += 1
            return value
    
    def set(self, key, value, ttl=None):
        """设置缓存值"""
        with self._lock:
            ttl = ttl or self._default_ttl
            expire_at = time.time() + ttl
            
            if key in self._cache:
                self._cache.move_to_end(key)
            
            self._cache[key] = (value, expire_at)
            
            # 超出容量时淘汰最久未使用的
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
    
    def delete(self, key):
        """删除缓存"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
    
    def clear(self):
        """清空缓存"""
        with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def get_stats(self):
        """获取缓存统计"""
        with self._lock:
            total = self._hits + self._misses
            hit_rate = (self._hits / total * 100) if total > 0 else 0
            return {
                'size': len(self._cache),
                'max_size': self._max_size,
                'hits': self._hits,
                'misses': self._misses,
                'hit_rate': f'{hit_rate:.1f}%'
            }


class QueryCacheManager:
    """查询缓存管理器"""
    
    def __init__(self):
        # 不同类型数据使用不同的缓存策略
        self._list_cache = LRUCache(max_size=2000, default_ttl=300)      # 列表查询缓存5分钟
        self._permission_cache = LRUCache(max_size=500, default_ttl=600)  # 权限缓存10分钟
        self._count_cache = LRUCache(max_size=1000, default_ttl=60)       # 计数缓存1分钟
        self._user_cache = LRUCache(max_size=1000, default_ttl=1800)     # 用户缓存30分钟
    
    def _generate_key(self, prefix, *args, **kwargs):
        """生成缓存键

        用 json.dumps(sort_keys=True, default=str) 稳定序列化参数：
        避免 dict/list 参数因顺序不同产生不同键（缓存击穿），
        也避免 None 与字符串 'None' 等 str() 结果相同导致键碰撞。
        """
        try:
            payload = json.dumps(
                {'prefix': prefix, 'args': list(args), 'kwargs': kwargs},
                sort_keys=True, default=str, ensure_ascii=False
            )
        except (TypeError, ValueError):
            # 极端不可序列化对象退化为 repr
            payload = f"{prefix}:{args!r}:{sorted(kwargs.items(), key=lambda kv: kv[0])!r}"
        return hashlib.md5(payload.encode('utf-8')).hexdigest()
    
    def get_list_cache(self, cache_key):
        """获取列表缓存"""
        return self._list_cache.get(cache_key)
    
    def set_list_cache(self, cache_key, value, ttl=None):
        """设置列表缓存"""
        self._list_cache.set(cache_key, value, ttl)

    def delete_list_cache(self, cache_key):
        """按键删除单个列表缓存（M-15：权限缓存按用户精准失效）"""
        self._list_cache.delete(cache_key)
    
    def get_permission(self, user_id):
        """获取用户权限缓存"""
        key = self._generate_key('perm', user_id)
        return self._permission_cache.get(key)
    
    def set_permission(self, user_id, permission_data):
        """设置用户权限缓存"""
        key = self._generate_key('perm', user_id)
        self._permission_cache.set(key, permission_data)
    
    def get_user(self, user_id):
        """获取用户信息缓存"""
        key = self._generate_key('user', user_id)
        return self._user_cache.get(key)
    
    def set_user(self, user_id, user_data):
        """设置用户信息缓存"""
        key = self._generate_key('user', user_id)
        self._user_cache.set(key, user_data)
    
    def get_count(self, count_key):
        """获取计数缓存

        M-4：把明文键（含搜索条件，基数极高）统一哈希为定长 MD5，
        既压缩键长度又避免明文键长期驻留内存造成高基数膨胀。
        """
        return self._count_cache.get(self._hash_count_key(count_key))

    def set_count(self, count_key, value, ttl=None):
        """设置计数缓存

        ttl: 可选自定义过期时间（秒）；不传则用计数缓存默认 TTL。
        """
        self._count_cache.set(self._hash_count_key(count_key), value, ttl)

    @staticmethod
    def _hash_count_key(count_key):
        """将计数缓存键收敛为定长哈希（保持同键幂等）。"""
        return 'c:' + hashlib.md5(str(count_key).encode('utf-8')).hexdigest()
    
    def invalidate_user_cache(self, user_id):
        """使用户相关缓存失效"""
        perm_key = self._generate_key('perm', user_id)
        user_key = self._generate_key('user', user_id)
        self._permission_cache.delete(perm_key)
        self._user_cache.delete(user_key)
    
    def invalidate_list_cache(self):
        """使所有列表缓存失效（数据更新后调用）"""
        self._list_cache.clear()
        self._count_cache.clear()

    def invalidate_all(self):
        """使所有缓存失效（含权限/用户缓存），用于数据权限变更等全局场景"""
        self._list_cache.clear()
        self._count_cache.clear()
        self._permission_cache.clear()
        self._user_cache.clear()
    
    def get_stats(self):
        """获取所有缓存统计"""
        return {
            'list_cache': self._list_cache.get_stats(),
            'permission_cache': self._permission_cache.get_stats(),
            'count_cache': self._count_cache.get_stats(),
            'user_cache': self._user_cache.get_stats()
        }


# 全局缓存管理器实例
query_cache_manager = QueryCacheManager()


def cached_query(ttl=300, key_prefix='query'):
    """
    查询缓存装饰器
    用于缓存数据库查询结果
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"{key_prefix}:{func.__name__}:"
            key_data = str(args) + str(sorted(kwargs.items()))
            cache_key += hashlib.md5(key_data.encode()).hexdigest()
            
            # 尝试从缓存获取
            cached_result = query_cache_manager.get_list_cache(cache_key)
            if cached_result is not None:
                current_app.logger.debug(f"缓存命中: {func.__name__}")
                return cached_result
            
            # 执行函数
            result = func(*args, **kwargs)
            
            # 缓存结果
            query_cache_manager.set_list_cache(cache_key, result, ttl)
            current_app.logger.debug(f"缓存设置: {func.__name__}")
            
            return result
        return wrapper
    return decorator


def invalidate_after_update(target='all'):
    """
    数据更新后使缓存失效的装饰器
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            # 使缓存失效
            query_cache_manager.invalidate_list_cache()
            current_app.logger.info(f"缓存已失效: {target} update")
            return result
        return wrapper
    return decorator


def optimize_query_for_sqlite(query, model, search_params, searchable_fields):
    """
    优化SQLite查询
    对于SQLite，使用更高效的查询方式
    """
    # 优化策略：对于精确匹配的字段使用 == 代替 ilike
    # 对于空值字段不添加过滤条件
    has_exact_match = False
    has_fuzzy_match = False
    
    for field in searchable_fields:
        value = search_params.get(field)
        if not value:
            continue
        
        if '%' in value or '_' in value:
            # 用户自定义通配符，使用 ilike
            query = query.filter(getattr(model, field).ilike(value))
            has_fuzzy_match = True
        elif len(value) <= 3:
            # 短字符串使用精确匹配，利用索引
            query = query.filter(getattr(model, field) == value)
            has_exact_match = True
        else:
            # 长字符串使用模糊匹配
            query = query.filter(getattr(model, field).ilike(f'%{value}%'))
            has_fuzzy_match = True
    
    return query, has_exact_match, has_fuzzy_match


def build_paginated_response(query, page, per_page, model, fields=None):
    """
    构建分页响应
    使用更高效的分页方式
    """
    # 限制每页最大数量
    per_page = max(5, min(per_page, 100))
    page = max(1, page)
    
    # 对于SQLite，使用更高效的计数方式
    # 当有精确匹配条件时，可以使用更快速的查询
    total = query.count()
    
    # 分页查询
    items = query.order_by(model.id).offset((page - 1) * per_page).limit(per_page).all()
    
    total_pages = max(1, (total + per_page - 1) // per_page)
    
    return {
        'items': items,
        'page': page,
        'per_page': per_page,
        'total': total,
        'pages': total_pages,
        'has_prev': page > 1,
        'has_next': page < total_pages,
        'prev_num': page - 1 if page > 1 else None,
        'next_num': page + 1 if page < total_pages else None,
    }


def serialize_items(items, model, field_names):
    """
    序列化查询结果
    只返回需要的字段
    """
    result = []
    for item in items:
        row = {}
        for field in field_names:
            if hasattr(item, field):
                row[field] = getattr(item, field)
        result.append(row)
    return result
