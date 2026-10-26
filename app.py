from app_factory import create_app, db

import os

# ============================================================
#  集中启动配置（修改端口 / 管理员账号密码请在此处调整）
#  开发环境与生产环境共用同一套账号密码。
# ============================================================
PORT = int(os.environ.get('APP_PORT', '5004'))            # 服务端口（默认 5004）
ADMIN_USERNAME = os.environ.get('ADMIN_USERNAME', 'admin')  # 管理员账号
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123!')  # 管理员密码

# 将配置写入环境变量，供 app_factory 创建/同步管理员账号使用
os.environ['APP_PORT'] = str(PORT)
os.environ['ADMIN_USERNAME'] = ADMIN_USERNAME
os.environ['ADMIN_PASSWORD'] = ADMIN_PASSWORD

# 创建应用实例：按 FLASK_CONFIG 自动切换开发/生产配置
# 默认 development（便于本地调试），start.bat / start.sh 会设为 production
_flask_config = os.environ.get('FLASK_CONFIG', 'development').lower()
os.environ['FLASK_CONFIG'] = _flask_config
app = create_app()

if __name__ == '__main__':
    _port = PORT
    _is_dev = _flask_config == 'development'
    _server = 'Flask 内置 (debug=True, 热重载)' if _is_dev else 'Waitress (生产模式)'

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
    except OSError as _e:
        # 端口被占用等绑定失败：输出可读提示而非裸 traceback，
        # 配合 start.bat 的 cmd /k 让错误停留在窗口中可见
        print(f"[ERROR] 端口 {_port} 绑定失败: {_e}")
        print("        请关闭占用该端口的程序，或设置 APP_PORT 环境变量更换端口。")
        raise SystemExit(1)
