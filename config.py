"""
配置文件
支持MySQL和SQLite数据库切换
"""

import os

class Config:
    """基础配置类"""
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'your-secret-key'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Excel导入配置
    EXCEL_AUTO_DEDUP = os.environ.get('EXCEL_AUTO_DEDUP', 'true').lower() == 'true'  # 是否自动去重
    EXCEL_DEDUP_STRICT = os.environ.get('EXCEL_DEDUP_STRICT', 'false').lower() == 'true'  # 严格去重模式
    SKIP_PART_VALIDATION = os.environ.get('SKIP_PART_VALIDATION', 'false').lower() == 'true'  # 是否跳过品番验证
    
    # 数据库配置
    @staticmethod
    def get_database_uri():
        """获取数据库URI，支持环境变量切换"""
        db_type = os.environ.get('DATABASE_TYPE', 'sqlite').lower()
        
        if db_type == 'mysql':
            # MySQL配置
            mysql_host = os.environ.get('MYSQL_HOST', 'localhost')
            mysql_port = os.environ.get('MYSQL_PORT', '3306')
            mysql_user = os.environ.get('MYSQL_USER', 'root')
            mysql_password = os.environ.get('MYSQL_PASSWORD', 'xinyushidai')
            mysql_database = os.environ.get('MYSQL_DATABASE', 'SubList')
            
            return f'mysql+pymysql://{mysql_user}:{mysql_password}@{mysql_host}:{mysql_port}/{mysql_database}'
        else:
            # SQLite配置（默认）
            # 优先使用环境变量 SQLITE_PATH（可指向仓库外部，避免 git pull 覆盖）
            sqlite_path = os.environ.get('SQLITE_PATH')
            if sqlite_path:
                # 允许相对路径，但推荐绝对路径
                db_path = os.path.abspath(sqlite_path)
            else:
                # 回退到项目 instance 目录，使用绝对路径，避免因工作目录变化创建新库
                basedir = os.path.abspath(os.path.dirname(__file__))
                db_path = os.path.join(basedir, 'instance', 'app.db')
            return f'sqlite:///{db_path}'
    
    @staticmethod
    def get_engine_options():
        """获取数据库引擎选项"""
        db_type = os.environ.get('DATABASE_TYPE', 'sqlite').lower()
        # 显式判断，防止环境变量拼写错误触发错误的选项
        if db_type == 'mysql':
            return {
                'pool_size': 25,  # 增大连接池大小，根据实际负载调整
                'max_overflow': 30,  # 增大最大溢出连接数
                'pool_recycle': 1200,  # 缩短连接回收时间，防止连接失效
                'pool_timeout': 15,  # 缩短连接超时时间
                'pool_pre_ping': True,  # 保持连接活跃
                'echo': False,  # 关闭SQLAlchemy日志
                'echo_pool': False,  # 关闭连接池日志
            }
        # 其它情况均按 SQLite 处理
        # SQLAlchemy 对文件型 SQLite 默认使用 QueuePool，这里显式配置连接池参数，
        # 确保并发访问时连接复用充分、超时可控
        return {
            'pool_size': 10,       # 连接池保持的连接数
            'max_overflow': 20,    # 超过 pool_size 时最多额外创建的连接数
            'pool_timeout': 30,    # 获取连接的超时时间（秒）
            'pool_recycle': 1800,  # 连接回收时间（30分钟）
            'connect_args': {
                'timeout': 30,  # SQLite 忙等待超时
                'check_same_thread': False,
                # 注意：不能设置 isolation_level=None（自动提交模式）！
                # 该模式下每个 INSERT 语句都会立即独立提交+fsync，
                # 批量插入性能下降约 40 倍（实测 10 万行：34s vs 0.9s），
                
                # 并导致 db.session.commit() 批量提交策略完全失效。
            },
            'pool_pre_ping': False,  # SQLite 无需预 ping
        }


class DevelopmentConfig(Config):
    """开发环境配置"""
    DEBUG = True
    SQLALCHEMY_DATABASE_URI = Config.get_database_uri()
    SQLALCHEMY_ENGINE_OPTIONS = Config.get_engine_options()


class ProductionConfig(Config):
    """生产环境配置"""
    DEBUG = False
    SQLALCHEMY_DATABASE_URI = Config.get_database_uri()
    SQLALCHEMY_ENGINE_OPTIONS = Config.get_engine_options()


class TestingConfig(Config):
    """测试环境配置"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    WTF_CSRF_ENABLED = False


# 配置字典
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}
