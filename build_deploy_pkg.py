#!/usr/bin/env python3
"""
BOM3 NAS 部署包构建脚本

功能:
    1. 收集项目运行所需的全部文件（源码/模板/静态资源/迁移/配置）
    2. 排除开发脚本、测试文件、缓存、日志、上传文件等非运行内容
    3. 生成 tar.gz 部署包（跨平台标准格式，NAS 通用）

用法:
    python build_deploy_pkg.py            # 默认输出到 dist/bom3_deploy.tar.gz
    python build_deploy_pkg.py --out /tmp/bom3.tar.gz
"""

import argparse
import os
import shutil
import tarfile
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTPUT = os.path.join(BASE_DIR, "dist", "bom3_deploy.tar.gz")

# 始终排除的目录名 / 文件模式（通用于所有层）
EXCLUDE_DIRS = {
    "__pycache__", ".venv", "venv", "env", ".git", ".idea", ".vscode",
    "node_modules", "logs", "dist", ".trae", "performance_results",
    # 上传目录：数据文件不入包（部署脚本会自动 mkdir -p static/uploads）
    "uploads",
}
EXCLUDE_EXT = {".pyc", ".pyo", ".log", ".tmp", ".db", ".db-wal", ".db-shm", ".sqlite",
               ".backup"}
EXCLUDE_FILES = {
    "server.log", "startup_check.log", "app.db", "package-lock.json",
}

# 根目录白名单：仅这些文件/目录进入部署包（运行必需）
ROOT_ALLOW = {
    # 核心运行文件
    "app.py", "app_factory.py", "config.py", "db.py", "query_cache.py",
    # 业务路由蓝图（app_factory 启动时按蓝图注册，缺一不可）
    "routes.py", "routes_cad.py", "routes_rbac.py", "routes_nas.py",
    "routes_mes.py", "routes_mes_modules.py", "routes_user_mgmt.py",
    # 后台服务/引擎（app_factory 及 routes 运行时按需导入）
    "data_management_service.py", "db_maintenance.py", "auto_backup_service.py",
    "invest_summary_engine.py", "auto_loading_engine.py",
    "corrugated_location_service.py", "excel_import_config.py",
    # 启动脚本
    "start.bat", "start.sh",
    # 部署配置
    "requirements.txt",
    # 目录
    "models", "utils", "templates", "static", "migrations", "template", "scripts",
}


def _is_excluded(name: str, is_dir: bool) -> bool:
    """判断文件/目录是否应排除。"""
    if is_dir:
        return name in EXCLUDE_DIRS
    if name in EXCLUDE_FILES:
        return True
    _, ext = os.path.splitext(name)
    return ext in EXCLUDE_EXT


def _collect_files(root: str, prefix: str) -> list:
    """递归收集文件，返回 (arcname, abs_path) 列表。"""
    result = []
    if not os.path.isdir(root):
        return result
    for entry in sorted(os.listdir(root)):
        src = os.path.join(root, entry)
        rel = os.path.join(prefix, entry)
        if os.path.isdir(src):
            if _is_excluded(entry, True):
                continue
            result.extend(_collect_files(src, rel))
        else:
            if _is_excluded(entry, False):
                continue
            result.append((rel, src))
    return result


def build_package(output: str) -> int:
    """构建部署包。返回文件数。"""
    files = []
    for name in sorted(ROOT_ALLOW):
        src = os.path.join(BASE_DIR, name)
        if os.path.isdir(src):
            files.extend(_collect_files(src, name))
        elif os.path.isfile(src):
            if not _is_excluded(name, False):
                files.append((name, src))

    # 确保输出目录存在
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with tarfile.open(output, "w:gz") as tar:
        for arcname, src in files:
            tar.add(src, arcname=os.path.join("bom3", arcname),
                    filter=_set_script_permission)

    return len(files)


def _set_script_permission(tarinfo):
    """为 .sh 脚本设置可执行权限（Linux/NAS 直接可运行）。"""
    if tarinfo.name.endswith(".sh"):
        tarinfo.mode = 0o755
    return tarinfo


def main() -> int:
    parser = argparse.ArgumentParser(description="BOM3 NAS 部署包构建脚本")
    parser.add_argument("--out", default=DEFAULT_OUTPUT,
                        help="输出文件路径（默认 %s）" % DEFAULT_OUTPUT)
    args = parser.parse_args()

    print("=" * 60)
    print("BOM3 NAS 部署包构建")
    print("=" * 60)
    print("收集运行必需文件...")
    files = build_package(args.out)

    size_kb = os.path.getsize(args.out) / 1024
    print("文件数 : %d" % files)
    print("大小   : %.1f KB" % size_kb)
    print("输出   : %s" % args.out)
    print("=" * 60)
    print("请将部署包上传至 NAS，并参考包内部署说明运行。")
    return 0


if __name__ == "__main__":
    sys_exit = main()
    import sys
    sys.exit(sys_exit)
