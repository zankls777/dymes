#!/usr/bin/env python3
"""
缓存机制优化脚本
添加Redis缓存、静态资源缓存、查询缓存
"""

import os
from pathlib import Path

def create_cache_config():
    """创建缓存配置"""
    
    cache_config = '''
# 缓存配置
CACHE_TYPE = 'redis'
CACHE_REDIS_URL = os.environ.get('REDIS_URL', 'redis://localhost:6379/0')
CACHE_DEFAULT_TIMEOUT = 300  # 5分钟
CACHE_KEY_PREFIX = 'dayan:'

# 静态资源缓存配置
STATIC_CACHE_TIMEOUT = 3600  # 1小时
UPLOAD_CACHE_TIMEOUT = 1800  # 30分钟

# 查询缓存配置
QUERY_CACHE_TIMEOUT = 600    # 10分钟
USER_CACHE_TIMEOUT = 1800    # 30分钟
'''
    
    with open('cache_config.py', 'w', encoding='utf-8') as f:
        f.write(cache_config)
    
    print("✅ 缓存配置已创建: cache_config.py")

def create_cache_helpers():
    """创建缓存辅助函数"""
    
    cache_helpers = '''
# 缓存辅助函数
import redis
import json
import pickle
from functools import wraps
from flask import current_app
import hashlib

class CacheManager:
    """缓存管理器"""
    
    def __init__(self, app=None):
        self.redis_client = None
        if app:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化应用"""
        redis_url = app.config.get('CACHE_REDIS_URL', 'redis://localhost:6379/0')
        self.redis_client = redis.from_url(redis_url, decode_responses=True)
    
    def get(self, key):
        """获取缓存"""
        try:
            value = self.redis_client.get(key)
            if value:
                return json.loads(value)
        except Exception as e:
            current_app.logger.warning(f"缓存获取失败: {e}")
        return None
    
    def set(self, key, value, timeout=300):
        """设置缓存"""
        try:
            self.redis_client.setex(key, timeout, json.dumps(value, default=str))
        except Exception as e:
            current_app.logger.warning(f"缓存设置失败: {e}")
    
    def delete(self, key):
        """删除缓存"""
        try:
            self.redis_client.delete(key)
        except Exception as e:
            current_app.logger.warning(f"缓存删除失败: {e}")
    
    def clear_pattern(self, pattern):
        """清除匹配模式的缓存"""
        try:
            keys = self.redis_client.keys(pattern)
            if keys:
                self.redis_client.delete(*keys)
        except Exception as e:
            current_app.logger.warning(f"缓存清除失败: {e}")

# 全局缓存管理器
cache_manager = CacheManager()

def cache_key(*args, **kwargs):
    """生成缓存键"""
    key_data = str(args) + str(sorted(kwargs.items()))
    return hashlib.md5(key_data.encode()).hexdigest()

def cached(timeout=300, key_prefix=''):
    """缓存装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key_str = f"{key_prefix}:{func.__name__}:{cache_key(*args, **kwargs)}"
            
            # 尝试从缓存获取
            cached_result = cache_manager.get(cache_key_str)
            if cached_result is not None:
                return cached_result
            
            # 执行函数并缓存结果
            result = func(*args, **kwargs)
            cache_manager.set(cache_key_str, result, timeout)
            return result
        return wrapper
    return decorator

def invalidate_cache(pattern):
    """使缓存失效"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            result = func(*args, **kwargs)
            cache_manager.clear_pattern(pattern)
            return result
        return wrapper
    return decorator

# 查询缓存装饰器
def query_cache(timeout=600):
    """查询缓存装饰器"""
    return cached(timeout=timeout, key_prefix='query')

# 用户缓存装饰器
def user_cache(timeout=1800):
    """用户缓存装饰器"""
    return cached(timeout=timeout, key_prefix='user')

# 文件缓存装饰器
def file_cache(timeout=3600):
    """文件缓存装饰器"""
    return cached(timeout=timeout, key_prefix='file')
'''
    
    with open('utils/cache_helpers.py', 'w', encoding='utf-8') as f:
        f.write(cache_helpers)
    
    print("✅ 缓存辅助函数已创建: utils/cache_helpers.py")

def create_static_cache_config():
    """创建静态资源缓存配置"""
    
    static_cache_config = '''
# 静态资源缓存配置
from flask import Flask, send_from_directory, make_response
from datetime import datetime, timedelta

def configure_static_cache(app):
    """配置静态资源缓存"""
    
    @app.after_request
    def add_cache_headers(response):
        """添加缓存头"""
        if response.status_code == 200:
            # 静态资源缓存
            if request.endpoint and request.endpoint.startswith('static'):
                response.cache_control.max_age = 3600  # 1小时
                response.cache_control.public = True
                response.expires = datetime.utcnow() + timedelta(hours=1)
            
            # API响应缓存
            elif request.endpoint and request.endpoint.startswith('api'):
                response.cache_control.max_age = 300   # 5分钟
                response.cache_control.private = True
            
            # HTML页面缓存
            elif response.content_type and 'text/html' in response.content_type:
                response.cache_control.max_age = 0
                response.cache_control.no_cache = True
                response.cache_control.no_store = True
        
        return response

# 静态文件服务优化
def optimized_static_file(filename):
    """优化的静态文件服务"""
    response = make_response(send_from_directory('static', filename))
    
    # 设置缓存头
    if filename.endswith(('.css', '.js', '.png', '.jpg', '.jpeg', '.gif', '.ico')):
        response.cache_control.max_age = 3600
        response.cache_control.public = True
        response.expires = datetime.utcnow() + timedelta(hours=1)
    
    return response
'''
    
    with open('utils/static_cache.py', 'w', encoding='utf-8') as f:
        f.write(static_cache_config)
    
    print("✅ 静态资源缓存配置已创建: utils/static_cache.py")

def create_cache_examples():
    """创建缓存使用示例"""
    
    cache_examples = '''
# 缓存使用示例

from utils.cache_helpers import query_cache, user_cache, file_cache, invalidate_cache

# 1. 查询缓存示例
@query_cache(timeout=600)  # 缓存10分钟
def get_vehicle_types():
    """获取车种列表"""
    return VehicleType.query.all()

@query_cache(timeout=300)  # 缓存5分钟
def get_file_list():
    """获取文件列表"""
    return FileInfo.query.order_by(FileInfo.upload_time.desc()).all()

# 2. 用户缓存示例
@user_cache(timeout=1800)  # 缓存30分钟
def get_user_permissions(user_id):
    """获取用户权限"""
    return PermissionInfo.query.filter_by(user_id=user_id).first()

# 3. 文件缓存示例
@file_cache(timeout=3600)  # 缓存1小时
def get_file_content(file_path):
    """获取文件内容"""
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

# 4. 缓存失效示例
@invalidate_cache('query:get_vehicle_types:*')
def add_vehicle_type(vehicle_data):
    """添加车种（使相关缓存失效）"""
    vehicle = VehicleType(**vehicle_data)
    db.session.add(vehicle)
    db.session.commit()
    return vehicle

@invalidate_cache('user:*')
def update_user_permissions(user_id, permissions):
    """更新用户权限（使用户缓存失效）"""
    permission = PermissionInfo.query.filter_by(user_id=user_id).first()
    if permission:
        for key, value in permissions.items():
            setattr(permission, key, value)
        db.session.commit()

# 5. 手动缓存管理示例
from utils.cache_helpers import cache_manager

def manual_cache_example():
    """手动缓存管理示例"""
    # 设置缓存
    cache_manager.set('custom_key', {'data': 'value'}, timeout=300)
    
    # 获取缓存
    cached_data = cache_manager.get('custom_key')
    
    # 删除缓存
    cache_manager.delete('custom_key')
    
    # 清除模式匹配的缓存
    cache_manager.clear_pattern('query:*')
'''
    
    with open('cache_examples.py', 'w', encoding='utf-8') as f:
        f.write(cache_examples)
    
    print("✅ 缓存使用示例已创建: cache_examples.py")

def create_redis_setup():
    """创建Redis安装和配置脚本"""
    
    redis_setup = '''#!/bin/bash
# Redis 安装和配置脚本

echo "🔧 开始安装和配置 Redis..."

# 1. 安装 Redis
if command -v redis-server &> /dev/null; then
    echo "✅ Redis 已安装"
else
    echo "📦 安装 Redis..."
    # Ubuntu/Debian
    sudo apt-get update
    sudo apt-get install redis-server -y
    
    # CentOS/RHEL
    # sudo yum install redis -y
    # sudo systemctl start redis
    # sudo systemctl enable redis
fi

# 2. 配置 Redis
echo "⚙️ 配置 Redis..."
sudo tee /etc/redis/redis.conf > /dev/null <<EOF
# Redis 配置文件

# 网络配置
bind 127.0.0.1
port 6379
timeout 300

# 内存配置
maxmemory 256mb
maxmemory-policy allkeys-lru

# 持久化配置
save 900 1
save 300 10
save 60 10000

# 日志配置
loglevel notice
logfile /var/log/redis/redis-server.log

# 安全配置
requirepass your_redis_password_here
EOF

# 3. 启动 Redis
echo "🚀 启动 Redis..."
sudo systemctl restart redis-server
sudo systemctl enable redis-server

# 4. 测试连接
echo "🧪 测试 Redis 连接..."
redis-cli ping

# 5. 安装 Python Redis 客户端
echo "📦 安装 Python Redis 客户端..."
pip install redis

echo "✅ Redis 安装和配置完成！"
echo "请修改配置文件中的密码: /etc/redis/redis.conf"
echo "请更新环境变量: REDIS_URL=redis://:password@localhost:6379/0"
'''
    
    with open('setup_redis.sh', 'w', encoding='utf-8') as f:
        f.write(redis_setup)
    
    # 设置执行权限
    os.chmod('setup_redis.sh', 0o755)
    
    print("✅ Redis 安装脚本已创建: setup_redis.sh")

def analyze_cache_performance():
    """分析缓存性能"""
    
    cache_analysis = """
# 缓存性能分析报告

## 1. 当前问题
- 缺少缓存机制，重复查询数据库
- 静态资源没有缓存策略
- 没有Redis缓存支持
- 查询结果没有缓存

## 2. 缓存策略

### 查询缓存
- 车种列表: 10分钟
- 文件列表: 5分钟
- 用户权限: 30分钟
- 统计数据: 15分钟

### 静态资源缓存
- CSS/JS文件: 1小时
- 图片文件: 1小时
- 字体文件: 1天
- HTML页面: 不缓存

### 文件缓存
- 上传文件: 30分钟
- 导出文件: 1小时
- 临时文件: 5分钟

## 3. 性能提升预期
- 数据库查询减少 60-80%
- 页面加载速度提升 30-50%
- 服务器负载降低 40-60%
- 用户体验显著改善

## 4. 实施步骤
1. 安装和配置 Redis
2. 添加缓存辅助函数
3. 为查询添加缓存装饰器
4. 配置静态资源缓存
5. 监控缓存命中率

## 5. 监控指标
- 缓存命中率 > 80%
- 缓存响应时间 < 10ms
- 内存使用率 < 70%
- 缓存失效率 < 5%
"""
    
    with open('cache_performance_analysis.md', 'w', encoding='utf-8') as f:
        f.write(cache_analysis)
    
    print("✅ 缓存性能分析报告已生成: cache_performance_analysis.md")

def main():
    """主函数"""
    print("🚀 开始缓存机制优化...")
    
    # 1. 创建缓存配置
    create_cache_config()
    
    # 2. 创建缓存辅助函数
    create_cache_helpers()
    
    # 3. 创建静态资源缓存配置
    create_static_cache_config()
    
    # 4. 创建缓存使用示例
    create_cache_examples()
    
    # 5. 创建Redis安装脚本
    create_redis_setup()
    
    # 6. 分析缓存性能
    analyze_cache_performance()
    
    print("\n📋 缓存机制优化完成！")
    print("建议:")
    print("1. 运行 setup_redis.sh 安装 Redis")
    print("2. 配置环境变量 REDIS_URL")
    print("3. 在应用中集成缓存辅助函数")
    print("4. 为查询添加缓存装饰器")
    print("5. 监控缓存性能指标")

if __name__ == "__main__":
    main()
