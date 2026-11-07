import os
import sys
import socket
import secrets
import string


def _pause_if_interactive():
    """仅在交互式控制台（双击运行）时暂停，脚本/服务方式启动不阻塞。"""
    try:
        if sys.stdin and sys.stdin.isatty():
            input('\n按回车键关闭窗口...')
    except Exception:
        pass


def _port_in_use(port: int) -> bool:
    """探测本机端口是否已被其他实例监听。

    Windows 下 SO_REUSEADDR 语义宽松，重复启动会静默叠加多个实例抢占同一端口，
    表现为服务行为异常；这里提前探测并给出可读提示。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(('127.0.0.1', port)) == 0


# 依赖导入同样要保护：双击运行时若解释器缺包（或指向 Microsoft Store 别名），
# ModuleNotFoundError 会在任何保护之外抛出，窗口照样一闪而过。
try:
    from app_factory import create_app, db
except ModuleNotFoundError as _imp_err:
    print(f"\n[ERROR] 缺少依赖模块: {_imp_err.name}")
    print(f"        当前解释器: {sys.executable}")
    print("        请在该解释器下执行: pip install -r requirements.txt")
    print("        或直接双击 start.bat（会自动挑选正确解释器）。")
    _pause_if_interactive()
    raise SystemExit(1)

# ============================================================
#  集中启动配置（修改端口 / 管理员账号密码请在此处调整）
#  开发环境与生产环境共用同一套账号密码。
# ============================================================
PORT = int(os.environ.get('APP_PORT', '5004'))            # 服务端口（默认 5004）
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')  # 管理员账号



def _ensure_admin_password():
    """确定管理员初始口令，杜绝硬编码弱口令（原默认 admin123! 可被直接爆破）。

    - 显式设置环境变量 ADMIN_PASSWORD 时优先使用（部署方应保证其强度）；
    - 未设置时：若 instance/admin_initial_password.txt 已存在则复用（重启不丢密码），
      否则生成 16 位强随机口令并落盘（权限 0600），同时在控制台提示立即修改。
    """
    env_pwd = os.environ.get('ADMIN_PASSWORD')
    if env_pwd:
        return env_pwd

    root = os.path.abspath(os.path.dirname(__file__))
    inst_dir = os.path.join(root, 'instance')
    key_path = os.path.join(inst_dir, 'admin_initial_password.txt')
    # 复用已生成的初始口令，避免每次重启都更换导致管理员无法登录
    if os.path.exists(key_path):
        try:
            with open(key_path, 'r', encoding='utf-8') as f:
                existing = f.read().strip()
            if existing:
                return existing
        except Exception:
            pass

    # 生成强随机初始口令
    alphabet = string.ascii_letters + string.digits + '!@#$%^&*-_=+'
    new_pwd = ''.join(secrets.choice(alphabet) for _ in range(16))
    try:
        os.makedirs(inst_dir, exist_ok=True)
        with open(key_path, 'w', encoding='utf-8') as f:
            f.write(new_pwd)
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        print("=" * 56)
        print(">> 安全提示：未设置环境变量 ADMIN_PASSWORD")
        print(">> 已自动生成 16 位强随机管理员初始口令")
        print(f">> 已写入文件: instance/admin_initial_password.txt")
        print(">> 请尽快登录「用户管理」修改此口令！")
        print("=" * 56)
    except Exception as e:
        print(f"[WARN] 写入管理员初始口令文件失败: {e}，使用内存随机口令（重启失效）")
    return new_pwd


ADMIN_PASSWORD = _ensure_admin_password()


def _ensure_secret_key():
    """确定 Flask SECRET_KEY，保证 create_app() 前环境变量必然存在。

    config.py 规定生产环境未设置 SECRET_KEY 直接启动失败，而启动脚本不会注入
    该变量，导致 python app.py / start.bat 一启动即 RuntimeError。此处：
    - 显式设置环境变量 SECRET_KEY 时优先使用；
    - 未设置时复用/生成根目录 .secret_key 持久化强随机密钥（与 config.py 开发
      环境同一文件约定），并写入环境变量供 config.py 读取。
    """
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key

    root = os.path.abspath(os.path.dirname(__file__))
    key_file = os.path.join(root, '.secret_key')
    if os.path.exists(key_file):
        try:
            with open(key_file, 'r', encoding='utf-8') as f:
                existing = f.read().strip()
            if existing:
                return existing
        except Exception:
            pass

    new_key = secrets.token_hex(32)
    try:
        with open(key_file, 'w', encoding='utf-8') as f:
            f.write(new_key)
        try:
            os.chmod(key_file, 0o600)
        except OSError:
            pass
        print(">> 已自动生成强随机 SECRET_KEY 并写入 .secret_key（重启不失效）")
    except Exception as e:
        print(f"[WARN] 写入 .secret_key 失败: {e}，本次使用内存随机密钥（重启后 session 全部失效）")
    return new_key


# 将配置写入环境变量，供 app_factory 创建/同步管理员账号使用
os.environ['APP_PORT'] = str(PORT)
os.environ['ADMIN_USERNAME'] = ADMIN_USERNAME
os.environ['ADMIN_PASSWORD'] = ADMIN_PASSWORD
os.environ['SECRET_KEY'] = _ensure_secret_key()

# 创建应用实例：按 FLASK_CONFIG 自动切换开发/生产配置
# 默认 development（便于本地调试），start.bat / start.sh 会设为 production
_flask_config = os.environ.get('FLASK_CONFIG', 'development').lower()
os.environ['FLASK_CONFIG'] = _flask_config

if __name__ == '__main__':
    # 双击/直接运行：先切到脚本目录并做端口探测，
    # 避免等 30 秒以上的数据预热后才发现已有实例在跑
    os.chdir(os.path.abspath(os.path.dirname(__file__)))
    # 开发模式开启 use_reloader 时，Werkzeug 会以 __main__ 重新执行本脚本；
    # 重载子进程里再探测会命中父进程刚绑定的端口，被误判为「已被占用」而退出，
    # 表现为服务打印完地址后立刻停止。因此重载子进程跳过端口探测。
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true' and _port_in_use(PORT):
        print(f"\n[提示] 端口 {PORT} 已被占用，服务可能已在运行。")
        print(f"       可直接访问 http://localhost:{PORT}")
        print("       如需重启，请先关闭已有进程（或设置 APP_PORT 换端口再启动）。")
        _pause_if_interactive()
        raise SystemExit(0)

try:
    app = create_app()
except Exception:
    # 双击运行（控制台直接启动）时，未捕获异常会让窗口闪一下就消失，
    # 因此这里打印完整堆栈并暂停等待回车；被 WSGI 导入时仍按原样抛出
    if __name__ == '__main__':
        import traceback
        print("\n[ERROR] 应用初始化失败，详细堆栈如下：\n")
        traceback.print_exc()
        _pause_if_interactive()
        raise SystemExit(1)
    raise


def main() -> int:
    _port = PORT
    _is_dev = _flask_config == 'development'
    _server = 'Flask 内置 (debug=True)' if _is_dev else 'Waitress (生产模式)'

    print("=" * 50)
    print(">> 大焱资料管理系统 (开发模式)" if _is_dev else ">> 大焱资料管理系统 (生产模式)")
    print("=" * 50)
    print(f"[DB] 数据库: {app.config.get('SQLALCHEMY_DATABASE_URI', 'N/A')[:80]}")
    print(f"[WEB] 服务地址: http://localhost:{_port}")
    print(f"[USER] 管理员: {ADMIN_USERNAME} / {ADMIN_PASSWORD}")
    print(f"[WSGI] 服务器: {_server}")
    print("=" * 50)

    try:
        if _is_dev:
            # 开发模式：Flask 内置服务器，开启 debug 与热重载
            app.run(debug=True, host='0.0.0.0', port=_port, use_reloader=True)
        else:
            # 生产模式：Waitress 多线程，无热重载，避免重复预热与日志文件占用
            from waitress import serve
            serve(app, host='0.0.0.0', port=_port, threads=8)
    except ModuleNotFoundError as e:
        # 缺少运行依赖是双击启动最常见的失败原因，输出可读提示而非裸堆栈
        print(f"\n[ERROR] 缺少依赖模块: {e.name}")
        print("        请执行: pip install -r requirements.txt")
        _pause_if_interactive()
        return 1
    except OSError as e:
        # 端口被占用等绑定失败
        print(f"\n[ERROR] 端口 {_port} 绑定失败: {e}")
        print("        请关闭占用该端口的程序，或设置 APP_PORT 环境变量更换端口。")
        _pause_if_interactive()
        return 1
    except KeyboardInterrupt:
        print("\n>> 服务已停止")
        return 0
    except Exception:
        import traceback
        print("\n[ERROR] 服务运行异常，详细堆栈如下：\n")
        traceback.print_exc()
        _pause_if_interactive()
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
