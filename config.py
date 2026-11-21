"""
配置文件
支持MySQL和SQLite数据库切换
"""

import os
import secrets

# sqlite URI 前缀（Windows/Linux 通用，与 Config.get_database_uri 的拼接方式保持一致）
_SQLITE_PREFIX = 'sqlite:///'


def _load_secret_key():
    """安全加载 Flask SECRET_KEY，杜绝可猜测的回退值。

    原实现为 `os.environ.get('SECRET_KEY') or 'your-secret-key'`，
    该字符串可被直接用于离线伪造任意用户 session（Flask 客户端签名 cookie）。
    本函数规则：
    - 优先使用环境变量 SECRET_KEY（部署时通过 .env / 启动脚本注入强随机值）；
    - 开发环境(dev/default)：未设置时自动生成随机密钥并持久化到项目根目录
      `.secret_key` 文件，确保开发会话签名稳定且不可被外部猜测；
    - 生产环境(production)：必须显式设置 SECRET_KEY，缺失则直接启动失败，
      避免带着弱密钥上线。
    """
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key

    config_name = (os.environ.get('FLASK_CONFIG') or 'development').lower()
    if config_name == 'production':
        raise RuntimeError(
            "生产环境必须设置环境变量 SECRET_KEY（建议: python -c \"import secrets;"
            "print(secrets.token_hex(32))\" 生成）。禁止带弱密钥/默认密钥上线。"
        )

    # 开发环境：持久化随机密钥到 .secret_key，单次生成长期复用
    root = os.path.abspath(os.path.dirname(__file__))
    key_file = os.path.join(root, '.secret_key')
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                existing = f.read().strip()
            if existing:
                return existing
        new_key = secrets.token_hex(32)
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(new_key)
        # 密钥文件应仅本用户可读写
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        return new_key
    except Exception:
        # 兜底：内存随机密钥（每次重启失效，仅保证开发可运行）
        return secrets.token_hex(32)


# 已知的弱口令集合：首次/admin 创建或启动时若命中则强制重置为强随机口令
KNOWN_WEAK_PASSWORDS = [
    'admin123!', 'admin123', 'admin', '123456', '12345678', 'password',
    'admin888', 'root', 'root123', 'Admin@123', 'Admin@123!', 'letmein',
    'qwerty', 'abc123', '111111', '000000', 'admin@123',
]


def ensure_sqlite_dir(uri):
    """确保 SQLite 数据库文件所在目录存在。

    背景：instance/ 被 .gitignore 忽略，全新克隆/下载的代码没有该目录，
    而 SQLite 无法在不存在的目录中创建库文件，会抛出
    OperationalError: unable to open database file。

    返回 (是否成功, 数据库文件绝对路径, 错误信息)。
    """
    if not uri or not str(uri).lower().startswith('sqlite'):
        return True, '', ''  # MySQL 等非 SQLite 场景无需处理

    raw = str(uri)[len(_SQLITE_PREFIX):] if str(uri).startswith(_SQLITE_PREFIX) else ''
    if not raw or raw == ':memory:':
        return True, '', ''  # 内存库无需落盘

    db_path = os.path.abspath(raw)
    parent = os.path.dirname(db_path)
    if not parent:
        return True, db_path, ''
    try:
        if not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
    except OSError as e:
        # 目录创建失败（权限不足/路径非法）时返回明确中文提示，便于定位
        return False, db_path, '无法创建数据库目录 %s: %s' % (parent, e)
    return True, db_path, ''


class Config:
    """基础配置类"""
    # SECRET_KEY 不再使用可猜测的回退值；缺失时按环境策略处理（见 _load_secret_key）
    SECRET_KEY = _load_secret_key()
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # ===== 会话 Cookie 安全加固 =====
    # HttpOnly：禁止 JS 读取，缓解 XSS 窃取 session
    SESSION_COOKIE_HTTPONLY = True
    # SameSite=Lax：阻止跨站请求携带会话 cookie，缓解 CSRF
    SESSION_COOKIE_SAMESITE = 'Lax'
    # Secure：仅 HTTPS 传输（内网若未上 TLS，设为 False；HTTP 部署建议前置反向代理开启 TLS）
    SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() == 'true'
    # 「记住登录状态」有效期：默认 30 天，可通过 SESSION_LIFETIME（秒）调整。
    # 勾选后使用持久 Cookie，且每次请求自动续期；未勾选仍为浏览器会话级 Cookie。
    PERMANENT_SESSION_LIFETIME = int(os.environ.get('SESSION_LIFETIME', '2592000'))
    SESSION_REFRESH_EACH_REQUEST = True
    
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
    # 模板自动重载：让 HTML 模板修改立即生效，无需重启服务（仅额外一次 mtime 检查，开销可忽略）
    TEMPLATES_AUTO_RELOAD = True


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
