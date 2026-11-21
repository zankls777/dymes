# app_factory.py
import os
import sys
import time
import threading
import secrets
from datetime import timedelta

from flask import Flask
import logging
from logging.handlers import TimedRotatingFileHandler
from db import db
from flask_migrate import Migrate
from jinja2.filters import FILTERS
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf import CSRFProtect
from flask_login import LoginManager, UserMixin
from flask_caching import Cache
from sqlalchemy import event

from models.user_info import UserInfo
from models.vehicle_type import VehicleType

csrf = CSRFProtect()
# 初始化 LoginManager
login_manager = LoginManager()
login_manager.login_view = 'main.login'  # 设置登录视图
# 未登录提示改为中文，避免出现英文默认文案「Please log in to access this page.」
login_manager.login_message = '请先登录后再访问该页面'
login_manager.login_message_category = 'warning'

# 初始化缓存
cache = Cache(config={'CACHE_TYPE': 'SimpleCache'})  # 使用简单内存缓存，可根据需要调整为其他类型

# 用户加载回调函数
@login_manager.user_loader
def load_user(user_id):
    return db.session.get(UserInfo, int(user_id))


def _sync_flask_login_user():
    """统一登录态：兼容历史会话（仅有 session['user_id']，缺少 Flask-Login 的 _user_id）。

    本系统同时使用 Flask-Login（@login_required）与自有 session['user_id'] 两套登录标记。
    若历史/异常会话只写入了后者，@login_required 会误判为未登录并跳转登录页，
    出现「有权限进入却提示请先登录」的问题。此处以 session['user_id'] 为准补齐 Flask-Login 登录态。
    """
    from flask import session
    from flask_login import current_user, login_user

    try:
        if current_user.is_authenticated:
            _clear_stale_login_flash(session)
            return
        uid = session.get('user_id')
        if not uid:
            return
        user = db.session.get(UserInfo, int(uid))
        if user:
            login_user(user, remember=False)
            # 登录态已确认，清除历史遗留的「请先登录」提示，避免误导用户
            _clear_stale_login_flash(session)
    except Exception:
        # 未登录/无请求上下文时忽略，交由 @login_required 正常处理
        pass


def _clear_stale_login_flash(session):
    """清理遗留的「请先登录」警告：用户实际已登录时不应再展示该提示。

    场景：后台 AJAX/预取请求命中 @login_required 被重定向到登录页时，
    Flask-Login 会把 login_message 写入 session['_flashes']，若该响应未被渲染
    （302/JSON），该提示会残留到后续任意页面，造成「有权限却提示请先登录」。
    """
    from flask import get_flashed_messages

    if not session.get('_flashes'):
        return
    # 注意：get_flashed_messages 会消费 flashes；这里只保留非登录提示的消息
    remaining = [(cat, msg) for cat, msg in session['_flashes']
                 if msg != login_manager.login_message]
    if remaining:
        session['_flashes'] = remaining
    else:
        session.pop('_flashes', None)
        # 同步清理已弹出的缓存，避免重复渲染
        try:
            get_flashed_messages()
        except Exception:
            pass

def _check_maintenance_mode():
    """维护模式全局拦截：开启后普通用户访问任意页面均跳转维护页。

    - 管理员（session['is_admin']）不受限制，保证可登录并关闭维护开关；
    - 无需登录即可访问的端点（登录、退出、维护页本身、静态资源）放行；
    - API/XHR 请求返回 503 JSON，普通页面请求渲染维护页。
    """
    try:
        from flask import request, session, render_template, jsonify, has_request_context
        if not has_request_context():
            return None
        path = request.path or ''
        # 静态资源与维护页、登录/退出等放行，避免拦截样式及管理员操作入口
        if path.startswith('/static/') or path.startswith('/favicon'):
            return None
        allowed = ('/login', '/logout', '/maintenance')
        if path in allowed:
            return None
        # 管理员不受维护模式影响
        if session.get('is_admin'):
            return None
        from models.system_setting import is_maintenance_mode
        if not is_maintenance_mode():
            return None

        is_admin = bool(session.get('is_admin'))
        if _wants_json_request():
            return jsonify({'success': False, 'error': '系统正在维护中，请稍后重试', 'code': 503}), 503
        return render_template('maintenance.html', is_admin=is_admin), 503
    except Exception:
        # 维护拦截异常时不阻断业务，交由后续处理
        return None


def _wants_json_request():
    """判断当前请求是否期望 JSON 响应（API/XHR）。"""
    try:
        from flask import request, has_request_context
        if not has_request_context():
            return False
        if request.path.startswith('/api/') or request.path.startswith('/rbac/api/'):
            return True
        if request.is_json:
            return True
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return True
        accept = request.headers.get('Accept', '')
        return 'application/json' in accept and 'text/html' not in accept
    except Exception:
        return False

def _safe_print(msg: str) -> None:
    """安全打印：控制台编码不支持（如 Windows GBK）时降级为 ASCII，避免抛异常。"""
    try:
        print(msg)
    except UnicodeEncodeError:
        try:
            enc = (getattr(sys.stdout, 'encoding', None) or 'ascii')
            print(msg.encode(enc, errors='replace').decode(enc, errors='replace'))
        except Exception:
            pass


def create_app():
    app = Flask(__name__)
    csrf.init_app(app)

    # 使用配置文件
    from config import config, ensure_sqlite_dir
    config_name = os.environ.get('FLASK_CONFIG', 'default')
    app.config.from_object(config[config_name])

    # 全新部署（git clone / 下载包）时 instance/ 目录可能不存在（已被 .gitignore 忽略），
    # SQLite 无法在缺失目录中创建库文件，会报 "unable to open database file"。
    # 这里在初始化数据库前提前建目录，保证首次启动即可自动建库建表。
    try:
        _ok, _db_path, _dir_err = ensure_sqlite_dir(app.config.get('SQLALCHEMY_DATABASE_URI'))
        if not _ok:
            _safe_print(f"[DB] 数据库目录准备失败: {_dir_err}")
        elif _db_path:
            _safe_print(f"[DB] 数据库文件: {_db_path}")
    except Exception as _e:
        _safe_print(f"[DB] 数据库目录检查异常(非致命): {_e}")

    # 初始化扩展（关键步骤）
    db.init_app(app)
    migrate = Migrate(app, db)
    login_manager.init_app(app)
    cache.init_app(app)

    @login_manager.unauthorized_handler
    def _handle_unauthorized():
        """未登录处理：区分 API/XHR 与普通页面，避免后台请求污染 flash 提示。

        - API/XHR（Accept 含 application/json 或 request.is_json / X-Requested-With）：
          直接返回 401 JSON，不写 flash、不重定向，前端自行处理跳转。
        - 普通页面导航：写 flash 并 302 到登录页（保留原有体验）。
        """
        from flask import request, session, redirect, url_for, jsonify, flash

        def _wants_json():
            if request.path.startswith('/api/') or request.path.startswith('/rbac/api/'):
                return True
            if request.is_json:
                return True
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return True
            accept = request.headers.get('Accept', '')
            return 'application/json' in accept and 'text/html' not in accept

        if _wants_json():
            return jsonify({'success': False, 'error': '未登录', 'code': 401}), 401

        if not session.get('_flashes') or all(
                m != login_manager.login_message for _, m in session['_flashes']):
            flash(login_manager.login_message, login_manager.login_message_category)
        return redirect(url_for(login_manager.login_view, next=request.url))

    # 统一登录态：补齐历史会话缺失的 Flask-Login 登录标记
    app.before_request(_sync_flask_login_user)

    # 维护模式全局拦截：开启后普通用户访问任意页面跳转维护页，管理员保持可访问
    app.before_request(_check_maintenance_mode)

    from routes import main_bp
    from routes_cad import cad_bp
    from routes_rbac import rbac_bp
    from routes_nas import nas_bp
    from routes_mes import mes_bp
    from routes_mes_modules import mes_mod_bp
    from routes_user_mgmt import user_mgmt_bp

    # 注册蓝图
    app.register_blueprint(main_bp)
    app.register_blueprint(cad_bp)
    app.register_blueprint(rbac_bp)
    app.register_blueprint(nas_bp)
    app.register_blueprint(mes_bp)
    app.register_blueprint(mes_mod_bp)
    app.register_blueprint(user_mgmt_bp)

    # 用户管理扩展接口使用 JSON + fetch 调用，豁免 CSRF（与 rbac / mes 策略一致）
    try:
        from app_factory import csrf as _csrf
        _csrf.exempt(user_mgmt_bp)
    except Exception:
        pass

    # RBAC 管理接口使用 JSON + fetch 调用，豁免 CSRF（沿用既有 API 端点策略）
    try:
        from app_factory import csrf as _csrf
        _csrf.exempt(rbac_bp)
    except Exception:
        pass

    # MES 分组接口使用 JSON + fetch 调用，豁免 CSRF（与 RBAC 策略一致）
    try:
        from app_factory import csrf as _csrf
        _csrf.exempt(mes_bp)
    except Exception:
        pass

    # MES 模块接口（计划登录/生产入库/品质检查/单价登陆）豁免 CSRF
    try:
        from app_factory import csrf as _csrf
        _csrf.exempt(mes_mod_bp)
    except Exception:
        pass

    # 设置"记住我"token有效期为30天
    # app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)

    # 其他安全相关配置（可选但推荐）
    # app.config['REMEMBER_COOKIE_SECURE'] = True  # 生产环境设为True（HTTPS）
    # app.config['REMEMBER_COOKIE_HTTPONLY'] = True  # 防止XSS攻击
    # app.config['REMEMBER_COOKIE_SAMESITE'] = 'Lax'  # 防止CSRF攻击
    # app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # 会话cookie的SameSite策略

    # 注册日期格式化过滤器
    def datetimeformat(value, format='%Y-%m-%d %H:%M'):
        return value.strftime(format)

    app.jinja_env.filters['datetimeformat'] = datetimeformat

    # 注册文件大小格式化过滤器
    def filesizeformat(value):
        if value is None or value == 0:
            return '0 B'
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if value < 1024:
                return f"{value:.2f} {unit}"
            value /= 1024
        return f"{value:.2f} PB"  # 处理超大文件情况

    app.jinja_env.filters['filesizeformat'] = filesizeformat

    # 侧边栏菜单：内置目录 + 可配置分组，按当前用户权限过滤后注入模板
    from flask import session as _session, g as _g
    from utils.sidebar_menu import build_sidebar_menu as _build_sidebar_menu

    @app.context_processor
    def inject_sidebar_menu():
        """每个请求只构建一次，避免页面多次 include 时重复查询配置"""
        if not _session.get('user_id'):
            return {'sidebar_menu': []}
        cached = getattr(_g, 'sidebar_menu_cache', None)
        if cached is not None:
            return {'sidebar_menu': cached}
        try:
            menu = _build_sidebar_menu(bool(_session.get('is_admin')),
                                       _session.get('user_permissions') or {})
        except Exception as exc:
            app.logger.error('构建侧边栏菜单失败: %s', exc)
            menu = []
        _g.sidebar_menu_cache = menu
        return {'sidebar_menu': menu}

    @app.template_global()
    def asset_v(rel_path):
        """静态资源版本号：取文件修改时间。

        静态资源响应头是 30 天 immutable 强缓存，浏览器不会回源校验，
        因此版本号必须随文件内容变化，否则改完 JS 用户永远拿不到新脚本。
        """
        try:
            import os
            full = os.path.join(app.static_folder or '', rel_path.replace('/', os.sep))
            return str(int(os.path.getmtime(full)))
        except Exception:
            return '1'

    # 创建数据库表
    with app.app_context():
        # SQLite特定初始化 - 使用连接事件持久化 PRAGMA 配置
        try:
            db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
            if str(db_uri).lower().startswith('sqlite'):
                # 在引擎级别注册事件监听器，确保每个新连接都应用优化设置
                @event.listens_for(db.engine, 'connect')
                def set_sqlite_pragma(dbapi_connection, connection_record):
                    """在每个新连接上设置 SQLite 性能优化 PRAGMA"""
                    try:
                        cursor = dbapi_connection.cursor()
                        cursor.execute('PRAGMA journal_mode=WAL')
                        cursor.execute('PRAGMA synchronous=NORMAL')
                        cursor.execute('PRAGMA cache_size=-65536')  # 64MB 页缓存 (负数表示字节)
                        cursor.execute('PRAGMA mmap_size=1073741824')  # 1GB 内存映射
                        cursor.execute('PRAGMA wal_autocheckpoint=20000')  # 80MB 触发 checkpoint（原 4MB 过频：Windows fsync 慢，上传大批量时卡顿）
                        cursor.execute('PRAGMA journal_size_limit=536870912')  # 512MB WAL 日志上限
                        cursor.execute('PRAGMA busy_timeout=30000')  # 30s 忙等待超时
                        cursor.execute('PRAGMA foreign_keys = ON')
                        cursor.close()
                    except Exception as e:
                        app.logger.warning(f"SQLite PRAGMA 设置失败: {e}")
                
                app.logger.info("SQLite连接级性能优化已注册")

                # 启动预热已移除（原 _warmup_sqlite_cache / _warmup_lot_file_pairs）：
                # 旧逻辑在启动期对百万行表做 COUNT/分组全表扫描，实测拖慢启动 20s+ 且与
                # 首请求争抢 IO。改为完全按需加载：
                # - 列表页均已实现服务端分页（WHERE + LIMIT/OFFSET），只查当前页；
                # - LOT 品番+EO 组合集合由 _get_file_resource_pairs_set() 首次调用时
                #   懒加载并写入 TTL 缓存（约 2.6 万条字符串，内存占用 MB 级有界）；
                # - COUNT 结果由 query_cache_manager 首访后缓存（TTL=60s）。
                # 代价仅为每个缓存的首访冷查询一次，不再有启动期固定开销。

                # 资材品番字典后台同步：usage_list 出现新规资材品番时增量补入字典表。
                # 该表供「盘点实查」品番选择器查询（1.4 千行 vs usage_list 53 万行）。
                def _sync_inventory_pno_dict():
                    import time as _t
                    try:
                        _t.sleep(30)  # M-11：错峰延后，避让启动期与首请求
                        with app.app_context():
                            from models.inventory_pno_dict import InventoryPnoDict
                            InventoryPnoDict.sync_from_usage_list(app)
                    except Exception as sync_err:
                        app.logger.warning(f"资材品番字典同步失败(非致命): {sync_err}")

                threading.Thread(target=_sync_inventory_pno_dict, daemon=True).start()

                # 列表页缓存预热：sub_list / usage_list 的活跃行数 COUNT 与各搜索字段索引在
                # 冷缓存下需读取 GB 级页面（机械盘单次数秒~数十秒）。这里在启动 10s 后于
                # 空闲期预读一次，结果写入计数缓存（TTL=30min），并每 10 分钟刷新，
                # 使用户首次打开/首次筛选即可直接命中系统文件缓存；
                # 用户请求侧另有时间预算保护，不会长时间等待。
                def _warm_list_counts():
                    import time as _t
                    _t.sleep(10)  # 仅避让启动瞬间的初始化写锁
                    from routes import warm_list_total_counts, warm_search_indexes
                    while True:
                        try:
                            # 先预读搜索索引（用户最直观的「加筛选后等待」），再预热默认总数
                            warm_search_indexes(app)
                            warm_list_total_counts(app, ttl=1800)
                        except Exception as warm_err:
                            app.logger.warning(f"列表缓存预热失败(非致命): {warm_err}")
                        # 每 10 分钟刷新一次，抵消系统文件缓存淘汰，保持索引页常驻
                        _t.sleep(600)

                threading.Thread(target=_warm_list_counts, daemon=True).start()
        except Exception as e:
            app.logger.warning(f"SQLite初始化配置失败: {e}")
        
        from models.file_info import FileInfo
        from models.data_management import DataUploadBatch, DataRecord
        from models.corrugated_location import CorrugatedLocation, CorrugatedColumnConfig
        from models.sub_list import SubList
        from models.usage_list import UsageList
        from models.vehicle_type import VehicleType
        from models.material_summary import MaterialSummary
        from models.sheet_calculation_result import SheetCalculationResult
        from models.tape_calculation_result import TapeCalculationResult
        from models.tube_calculation_result import TubeCalculationResult
        from models.lot_login_info import LotLoginInfo
        from models.invest_record import InvestmentRecord
        from models.auto_loading import AutoLoadingRecord
        from models.wire_scan_record import WireScanRecord
        from models.wire_scan_detail import WireScanDetail
        from models.wire_crimp_login import WireCrimpLogin
        from models.material_inbound import MaterialInboundDaily
        from models.inventory_check import InventoryTeam, InventoryCheck
        from models.inventory_pno_dict import InventoryPnoDict
        from models.defective_login import DefectiveLogin
        from models.system_setting import SystemSetting
        from models.mes_group import MesGroup
        from models.plan_login import PlanLogin
        from models.production_inbound import ProductionInbound
        from models.quality_check import QualityCheck
        from models.unit_price import UnitPrice
        from models.price_multiplier import PriceMultiplier
        from models.customer import Customer
        # RBAC 权限模型（权限/权限组/用户权限组/用户覆盖），必须导入以便 db.create_all() 建表
        from models.rbac import (  # noqa: F401
            Permission, UserPermissionOverride,
            PermissionGroup, GroupPermission, UserGroup,
        )

        # 结构升级 + 默认数据初始化：统一由 utils.schema_upgrade 维护
        # （建表/补列/索引/RBAC/系统设置），启动与「数据恢复后」共用同一入口。
        # 新增功能只需在 utils/schema_upgrade.py 中登记，无需改动此处。
        try:
            from utils.schema_upgrade import ensure_schema_ready
            _ready = ensure_schema_ready()
            app.logger.info(f"数据库结构与默认数据初始化完成: {_ready}")
        except Exception as e:
            db.session.rollback()
            app.logger.warning(f"数据库结构初始化失败(非致命): {e}")
            try:
                db.create_all()
            except Exception as _ce:
                app.logger.warning(f"兜底建表失败: {_ce}")

        # 说明：user_info / file_info / usage_list / sub_list /
        # plan_login / production_inbound / wire_scan_record / finished_product
        # 的新增字段与索引，均已集中到 utils.schema_upgrade 统一处理（含恢复后重放）。

        # 启动时回填（可选）：为已存在的 UsageList 数据补齐 VehicleType（从 source_file 解析）
        # 默认跳过以避免重启时高 CPU/IO 开销；需要时设置 RUN_STARTUP_MAINTENANCE=true 再执行
        try:
            if os.environ.get('RUN_STARTUP_MAINTENANCE', 'false').lower() == 'true':
                from models.usage_list import UsageList
                from sqlalchemy import or_
                import os as _os

                def _parse_vehicle_type_from_filename(filename: str):
                    try:
                        base = _os.path.basename(filename or '')
                        name_wo_ext = _os.path.splitext(base)[0]
                        parts = name_wo_ext.split('_')
                        # 位置规则：去掉最后三个段（零件号、版本、UsageList），其余作为车种
                        if len(parts) > 3:
                            return '_'.join(parts[:-3])
                    except Exception as e:
                        app.logger.debug(f"从文件名解析车种失败(非致命): {e}")
                    return None

                # 全量重算 VehicleType：对所有记录按新规则解析，若不同则覆盖
                all_items = UsageList.query.all()
                updated = 0
                for item in all_items:
                    vt = _parse_vehicle_type_from_filename(getattr(item, 'source_file', '') or '')
                    if vt and vt != getattr(item, 'VehicleType', None):
                        item.VehicleType = vt
                        updated += 1
                if updated:
                    db.session.commit()
                print(f"Info: RUN_STARTUP_MAINTENANCE applied, UsageList.VehicleType updated={updated}")
        except Exception as e:
            print(f"Warning: backfill UsageList.VehicleType failed: {e}")

        # 创建默认管理员账户（账号密码由 app.py 集中配置，经环境变量注入）
        # 统一读取环境变量，确保开发/生产共用同一套凭据且可被集中修改
        # 注意：不再使用 'admin123!' 之类的硬编码弱口令作为回退值（admin.py 已保证非空）
        _admin_user = os.environ.get('ADMIN_USERNAME', 'admin')
        _admin_pass = os.environ.get('ADMIN_PASSWORD') or ''

        # 本地工具：生成强随机口令并落盘（用于强制重置历史弱口令）
        def _generate_strong_password(length=16):
            import string as _string
            _alphabet = _string.ascii_letters + _string.digits + '!@#$%^&*-_=+'
            return ''.join(secrets.choice(_alphabet) for _ in range(length))

        def _write_admin_pwd_file(pwd):
            try:
                _root = os.path.abspath(os.path.dirname(__file__))
                _inst = os.path.join(_root, 'instance')
                os.makedirs(_inst, exist_ok=True)
                _path = os.path.join(_inst, 'admin_initial_password.txt')
                with open(_path, 'w', encoding='utf-8') as _f:
                    _f.write(pwd)
                try:
                    os.chmod(_path, 0o600)
                except OSError:
                    pass
            except Exception as _we:
                app.logger.warning(f"写入管理员初始口令文件失败(非致命): {_we}")

        admin = UserInfo.query.filter_by(username=_admin_user).first()
        if not admin:
            # 新建管理员并分配全部权限
            # 若环境变量提供的口令为空或命中已知弱口令，强制改用强随机并落盘，
            # 避免部署者误配弱口令（如旧版默认的 admin123!）直接上线。
            from config import KNOWN_WEAK_PASSWORDS
            _init_pass = _admin_pass
            if (not _admin_pass) or (_admin_pass in KNOWN_WEAK_PASSWORDS):
                _init_pass = _generate_strong_password()
                _write_admin_pwd_file(_init_pass)
                app.logger.critical(
                    "安全告警：管理员初始口令为空或命中已知弱口令，已自动改为强随机口令并写入 "
                    "instance/admin_initial_password.txt，请尽快登录「用户管理」修改！"
                )
            admin = UserInfo(username=_admin_user, password=generate_password_hash(_init_pass), is_admin=True)
            db.session.add(admin)
            db.session.commit()
        else:
            # 账号已存在：同步更新密码与管理员标记，保证 app.py 中的配置修改即时生效；
            # 管理员权限由 is_admin 直接放行，无需权限记录。

            # 启动期强制重置历史遗留的已知弱口令（如旧版默认 admin123!、123456 等）。
            # 必须在覆盖 admin.password 之前检测旧哈希，否则无法判断是否需要强制重置。
            # 这能清除已部署实例中仍使用弱口令的 admin 账号，是本次修复的关键收敛动作。
            from config import KNOWN_WEAK_PASSWORDS
            _legacy_weak = any(
                check_password_hash(admin.password, w) for w in KNOWN_WEAK_PASSWORDS
            )

            # 若环境变量未提供口令（空），沿用 app.py 已生成的强随机初始口令
            _effective_pass = _admin_pass or _admin_user  # 兜底不应触达（app.py 已保证非空）
            if not _admin_pass:
                # 复用 instance/admin_initial_password.txt 中的值（由 app.py 生成/维护）
                try:
                    _kp = os.path.join(os.path.abspath(os.path.dirname(__file__)),
                                       'instance', 'admin_initial_password.txt')
                    if os.path.exists(_kp):
                        with open(_kp, 'r', encoding='utf-8') as _kf:
                            _effective_pass = _kf.read().strip() or _effective_pass
                except Exception:
                    pass

            if _legacy_weak:
                # 命中已知弱口令：无视环境变量设定，强制重置为强随机并落盘，要求立即改密
                _new_pwd = _generate_strong_password()
                admin.password = generate_password_hash(_new_pwd)
                _write_admin_pwd_file(_new_pwd)
                app.logger.critical(
                    "安全告警：检测到管理员账号 '%s' 仍使用已知弱口令，已在启动时强制重置为"
                    "强随机口令，新口令已写入 instance/admin_initial_password.txt，请立即登录修改！",
                    _admin_user,
                )
            else:
                admin.password = generate_password_hash(_effective_pass)
            admin.is_admin = True
            db.session.commit()

    # 设置日志：按天轮转，保留30天
    # 防死锁（2026-11-06 实发故障）：控制台/文件日志统一走 QueueHandler + QueueListener
    # 异步写入。直接挂 StreamHandler 时，若 Windows 控制台被鼠标选中（QuickEdit 暂停
    # 输出）或 stderr 管道缓冲区写满，emit 会在持有日志锁期间永久阻塞，随后所有
    # 请求线程在 logging 锁上排队，整个服务假死（接口无响应、页面无法刷新）。
    console_handler = logging.StreamHandler()
    # 优化日志格式，添加更多结构化信息
    console_formatter = logging.Formatter(
        '%(asctime)s %(levelname)s [%(name)s:%(lineno)d] %(message)s'
    )
    console_handler.setFormatter(console_formatter)
    console_handler.setLevel(logging.INFO)
    
    # 支持环境变量配置日志级别
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_level_value = getattr(logging, log_level, logging.INFO)
    
    app.logger.setLevel(log_level_value)
    
    # 清除Flask默认的handlers，避免重复日志
    app.logger.handlers.clear()
    # 关键：关闭向 root logger 的传播，否则 app.logger 会先把日志发给自己 handler，
    # 再传给 root（被 logging 隐式默认 handler 捕获），导致每条日志在 server.log 里出现两遍
    app.logger.propagate = False

    # 实际写入目标 handler 列表，稍后统一挂到异步队列监听器上
    _log_handlers = [console_handler]
    
    # 同步 werkzeug 到控制台
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(log_level_value)
    # 注意：不要将同一个handler添加到多个logger，会导致重复日志
    # werkzeug_logger.addHandler(console_handler)  # 移除这行避免重复日志
    
    # 尝试配置文件日志
    try:
        # 优先使用项目目录下的logs文件夹，避免权限问题
        log_dir = os.environ.get('LOG_DIR') or os.path.join(os.path.dirname(__file__), 'logs')
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, 'dayan.log')

        # 使用自定义的日志轮转处理器，确保日期正确
        from logging.handlers import TimedRotatingFileHandler
        import datetime
        
        class CustomTimedRotatingFileHandler(TimedRotatingFileHandler):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.last_rollover_date = None
                # 关闭延迟，立即打开文件
                self.delay = False
            
            def shouldRollover(self, record):
                """
                检查是否需要轮转
                """
                # 只有当日期改变时才轮转，避免频繁检查
                current_date = datetime.datetime.now().date()
                if self.last_rollover_date != current_date:
                    return True
                return super().shouldRollover(record)
            
            def doRollover(self):
                """
                自定义轮转逻辑，优化Windows系统下的文件处理
                """
                if self.stream:
                    # 关闭当前文件流
                    self.stream.close()
                    self.stream = None
                
                # 获取当前时间
                current_time = datetime.datetime.now()
                current_date = current_time.date()
                
                # 生成新的文件名
                new_filename = os.path.join(
                    os.path.dirname(self.baseFilename),
                    f'dayan-{current_date.strftime("%Y-%m-%d")}.log'
                )
                
                # 只在文件存在时尝试轮转
                if os.path.exists(self.baseFilename):
                    try:
                        if os.path.exists(new_filename):
                            # 目标文件已存在（同一天重复轮转/进程重启），保留新文件名，
                            # 先移除旧轮转文件再重命名，避免 WinError 183 导致轮转失败
                            try:
                                os.remove(new_filename)
                            except OSError:
                                pass
                        os.rename(self.baseFilename, new_filename)
                        _safe_print(f"日志轮转: {os.path.basename(self.baseFilename)} -> {os.path.basename(new_filename)}")
                    except PermissionError:
                        # 在Windows系统下，当其他进程正在使用文件时，重命名会失败
                        # 此时直接跳过轮转，继续使用原文件
                        _safe_print("日志轮转跳过: 文件被其他进程占用")
                    except Exception as e:
                        # 其他错误，记录但不崩溃
                        _safe_print(f"日志轮转异常: {str(e)}")
                
                # 更新轮转日期
                self.last_rollover_date = current_date
                # 同步更新基类的轮转时间基准，避免 shouldRollover 反复返回 True
                self.rolloverAt = self.computeRollover(int(time.time()))

                # 清理超出 backupCount 的历史日志（基类逻辑被重写后不会自动删除，需在此补上）
                self._cleanup_old_logs()

                # 打开新的日志文件
                self.stream = self._open()

            def _cleanup_old_logs(self):
                """按 backupCount 删除最旧的日志文件，避免磁盘无限增长"""
                if not self.backupCount or self.backupCount <= 0:
                    return
                try:
                    log_dir = os.path.dirname(self.baseFilename)
                    prefix = 'dayan-'
                    suffix = '.log'
                    files = []
                    for name in os.listdir(log_dir):
                        if name.startswith(prefix) and name.endswith(suffix):
                            # 仅处理 dayan-YYYY-MM-DD.log 形式的轮转文件
                            stem = name[len(prefix):-len(suffix)]
                            try:
                                datetime.datetime.strptime(stem, '%Y-%m-%d')
                            except ValueError:
                                continue
                            files.append(name)
                    files.sort(reverse=True)
                    for name in files[self.backupCount:]:
                        try:
                            os.remove(os.path.join(log_dir, name))
                        except OSError:
                            pass
                except Exception as e:
                    _safe_print(f"旧日志清理异常: {str(e)}")

        file_handler = CustomTimedRotatingFileHandler(
            filename=log_file,
            when='midnight',
            interval=1,
            backupCount=30,
            encoding='utf-8',
            utc=False  # 使用本地时间，与系统时间保持一致
        )
        
        file_handler.setFormatter(console_formatter)
        file_handler.setLevel(log_level_value)

        _log_handlers.append(file_handler)
        # 注意：不要将同一个handler添加到多个logger，会导致重复日志
        # werkzeug_logger.addHandler(file_handler)  # 移除这行避免重复日志
        
        # 添加日志轮转调试信息（配合 Flask 调试重载只打印一次详细信息）
        import datetime
        current_time = datetime.datetime.now()
        import os as _os
        
        # 仅在 werkzeug 重载的子进程中打印详细信息（避免父进程也打印一次）
        is_reloader_child = _os.environ.get('WERKZEUG_RUN_MAIN') == 'true'
        if is_reloader_child and not getattr(create_app, '_logging_configured', False):
            _safe_print(f"日志配置成功，文件日志: {log_file}")
            _safe_print(f"   当前时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')}")
            _safe_print(f"   日志级别: {log_level}")
            _safe_print("   日志轮转时间: 每天午夜 (00:00)")
            _safe_print("   备份文件格式: dayan-YYYY-MM-DD.log")
            _safe_print("   保留天数: 30天")
            create_app._logging_configured = True
        elif not is_reloader_child:
            # 父进程（负责监控重载）不打印详细信息，避免看起来重复
            pass
        
    except Exception as e:
        _safe_print(f"文件日志配置失败，仅使用控制台输出: {e}")
        _safe_print(f"   建议检查日志目录权限: {log_dir}")

    # 异步日志总线：请求线程只做「入队」（内存操作，永不阻塞），
    # 由 QueueListener 守护线程把记录逐个写到控制台/文件 handler。
    # 即使底层输出（控制台/磁盘）卡死，也只影响监听线程，不会拖垮业务线程。
    # 注意：监听线程需持有模块级引用，防止被 GC 回收后日志静默丢失。
    import queue as _log_queue_mod
    from logging.handlers import QueueHandler as _QueueHandler, QueueListener as _QueueListener

    _log_record_queue = _log_queue_mod.Queue(-1)
    _log_listener = _QueueListener(_log_record_queue, *_log_handlers, respect_handler_level=True)
    _log_listener.start()
    app.logger.addHandler(_QueueHandler(_log_record_queue))
    # 挂到 app 上保活（create_app 可能被调用多次，避免旧监听器线程泄漏）
    app.extensions.setdefault('log_listener', _log_listener)

    # 添加全局异常处理器
    def _wants_json():
        """判断当前请求是否期望 JSON 响应（API/fetch 调用）。

        API 路径或显式声明 Accept: application/json 的请求，出错时应返回 JSON，
        避免前端 resp.json() 解析 HTML 错误页时报 "Unexpected token '<'"。
        """
        try:
            from flask import request, has_request_context
            if not has_request_context():
                return False
            if request.path.startswith('/api/'):
                return True
            accept = request.headers.get('Accept', '')
            return 'application/json' in accept and 'text/html' not in accept
        except Exception:
            return False

    @app.errorhandler(Exception)
    def handle_exception(e):
        """全局异常处理器，记录所有未处理的异常"""
        from werkzeug.exceptions import HTTPException
        # HTTP 异常（403/404/405 等）交给对应的 errorhandler 处理，避免被误报为 500
        if isinstance(e, HTTPException):
            return e
        try:
            # 获取请求信息（后台线程/无请求上下文时 has_request_context 为 False，不能直接访问 request 代理）
            from flask import request, has_request_context
            request_info = ""
            if has_request_context():
                request_info = f" | {request.method} {request.url} | {request.remote_addr}"
            
            # 合并异常信息到一条日志，减少日志数量
            app.logger.error(f"未处理的异常: {str(e)}{request_info}", exc_info=True)
            
            # 返回友好的错误页面
            from flask import render_template
            if _wants_json():
                from flask import jsonify
                return jsonify({'success': False, 'error': '服务器内部错误，请稍后重试'}), 500
            return render_template('error.html', 
                                 error_code=500, 
                                 error_message="服务器内部错误，请稍后重试"), 500
        except Exception as log_error:
            # 如果日志记录也失败了，至少打印到控制台
            print(f"严重错误: 无法记录异常日志: {log_error}")
            print(f"原始异常: {e}")
            return "服务器内部错误", 500

    # 添加HTTP错误处理器
    @app.errorhandler(400)
    def handle_400(e):
        """400错误处理器：多为 CSRF 校验失败或请求体格式错误"""
        from flask import request, has_request_context
        app.logger.warning(f"400错误: {request.url if has_request_context() else 'Unknown URL'} | {str(e)}")
        if _wants_json():
            from flask import jsonify
            return jsonify({'success': False, 'error': '请求校验失败（可能页面已过期），请刷新页面后重试'}), 400
        from flask import render_template
        return render_template('error.html',
                             error_code=400,
                             error_message="请求无效，请刷新页面后重试"), 400

    @app.errorhandler(404)
    def handle_404(e):
        """404错误处理器"""
        from flask import request, has_request_context
        app.logger.warning(f"404错误: {request.url if has_request_context() else 'Unknown URL'}")
        from flask import render_template
        if _wants_json():
            from flask import jsonify
            return jsonify({'success': False, 'error': '接口不存在'}), 404
        return render_template('error.html',
                             error_code=404,
                             error_message="页面不存在"), 404

    @app.errorhandler(403)
    def handle_403(e):
        """403错误处理器"""
        from flask import request, has_request_context
        app.logger.warning(f"403错误: {request.url if has_request_context() else 'Unknown URL'}")
        from flask import render_template, session
        if _wants_json():
            from flask import jsonify
            return jsonify({'success': False, 'error': '无权限或校验失败，请刷新页面后重试'}), 403
        return render_template('error.html',
                             error_code=403,
                             error_message="访问被拒绝",
                             permissions=session.get('user_permissions') or {}), 403

    @app.errorhandler(405)
    def handle_405(e):
        """405错误处理器：请求方法不被允许"""
        from flask import request, has_request_context
        app.logger.warning(f"405错误: {request.method} {request.url if has_request_context() else 'Unknown URL'}")
        from flask import render_template, session
        if _wants_json():
            from flask import jsonify
            return jsonify({'success': False, 'error': '请求方式不被允许'}), 405
        return render_template('error.html',
                             error_code=405,
                             error_message="请求方式不被允许",
                             permissions=session.get('user_permissions') or {}), 405

    @app.errorhandler(500)
    def handle_500(e):
        """500错误处理器"""
        app.logger.error(f"500错误: {str(e)}", exc_info=True)
        from flask import render_template, session
        if _wants_json():
            from flask import jsonify
            return jsonify({'success': False, 'error': '服务器内部错误'}), 500
        return render_template('error.html',
                             error_code=500,
                             error_message="服务器内部错误",
                             permissions=session.get('user_permissions') or {}), 500

    # 添加静态资源缓存头（减少重复请求，提升二次访问速度）
    @app.after_request
    def add_static_cache_headers(response):
        """为静态资源添加长缓存头"""
        from flask import request
        try:
            # 静态资源添加 30 天缓存
            if request.path.startswith('/static/'):
                # 用赋值而非 setdefault：Flask 可能已写入 Cache-Control，setdefault 会失效
                response.headers['Cache-Control'] = 'public, max-age=2592000, immutable'
                # 对静态资源不记录日志，减少 IO 开销
                return response
        except Exception as e:
            app.logger.debug(f"设置静态资源缓存头失败: {str(e)}")
        return response

    # 请求日志中间件：仅记录动态请求，跳过静态资源（性能优化）
    @app.before_request
    def log_request_info():
        """记录请求信息（跳过静态资源以减少 IO 开销）"""
        try:
            from flask import request
            # 静态资源不记录日志，大幅减少日志 IO
            if request.path.startswith('/static/'):
                return
            # 请求明细降为 DEBUG：默认 INFO 级别不落盘，避免日志文件被逐条请求撑爆；
            # 需要排障时设置环境变量 LOG_LEVEL=DEBUG 即可重新打开
            app.logger.debug(f"请求: {request.method} {request.url}")
            if request.method in ['POST', 'PUT', 'PATCH']:
                content_length = request.headers.get('Content-Length', 0)
                app.logger.debug(f"请求数据大小: {content_length} bytes")
        except Exception as e:
            app.logger.warning(f"记录请求信息失败: {str(e)}")

    @app.after_request
    def log_response_info(response):
        """记录响应信息（跳过静态资源，慢请求标记）"""
        try:
            from flask import request
            # 静态资源不记录日志
            if request.path.startswith('/static/'):
                return response
            # 正常响应降为 DEBUG（同请求明细），仅保留 4xx/5xx 告警，减少日志体积
            app.logger.debug(f"响应: {response.status_code} - {request.method} {request.url}")
            if response.status_code >= 400:
                app.logger.warning(f"错误响应: {response.status_code} - {request.url}")
        except Exception as e:
            app.logger.warning(f"记录响应信息失败: {str(e)}")
        return response

    # 启动后台数据库例行维护线程（VACUUM 阈值触发 + 可选归档清理）
    try:
        from db_maintenance import start_maintenance_thread
        start_maintenance_thread(app, interval_hours=24, delay_seconds=600)
    except Exception as e:
        app.logger.warning(f"后台维护线程启动失败: {e}")

    # 启动自动备份到网盘调度线程（daemon，每 60 秒检查一次全量/增量时刻）
    try:
        from auto_backup_service import start_scheduler
        if not getattr(create_app, '_auto_backup_started', False):
            start_scheduler(app)
            create_app._auto_backup_started = True
    except Exception as e:
        app.logger.warning(f"自动备份调度线程启动失败: {e}")

    return app