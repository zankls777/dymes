#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键打包并投递到「香港」目录

功能:
    1. 调用 build_deploy_pkg.py 构建部署包（tar.gz）
    2. 将生成的部署包移动到「香港」目标目录
    3. 目标目录支持三种配置方式（优先级从高到低）：
        - 命令行参数 --dest
        - 环境变量 BOM3_HK_DIR
        - 脚本内 DEFAULT_HK_DIR

兼容: Windows 7/10、Linux（UNC 路径 \\\\server\\share、映射盘 Z:\\、Linux 挂载点均可）

用法:
    python scripts/pack_to_hk.py                       # 用默认/环境变量目标
    python scripts/pack_to_hk.py --dest "Z:\\香港"
    python scripts/pack_to_hk.py --dest "\\\\server\\share\\香港" --keep
    BOM3_HK_DIR=/mnt/hk python scripts/pack_to_hk.py   # Linux
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(BASE_DIR, "build_deploy_pkg.py")
DIST_DIR = os.path.join(BASE_DIR, "dist")

# 默认香港目录（可留空，改由 --dest 或环境变量 BOM3_HK_DIR 指定）
DEFAULT_HK_DIR = r""


def _resolve_dest(cli_dest):
    """解析目标目录：命令行 > 环境变量 > 脚本常量。"""
    return (cli_dest or os.environ.get("BOM3_HK_DIR") or DEFAULT_HK_DIR or "").strip()


def _build_package():
    """调用 build_deploy_pkg.py 构建部署包，返回生成的文件路径。"""
    if not os.path.isfile(BUILD_SCRIPT):
        raise RuntimeError("未找到打包脚本: %s" % BUILD_SCRIPT)

    # 记录构建前后 dist 内文件差异，稳健定位产物（不依赖脚本内部命名）
    before = set(os.listdir(DIST_DIR)) if os.path.isdir(DIST_DIR) else set()

    print("[1/3] 正在构建部署包 ...")
    result = subprocess.run(
        [sys.executable, BUILD_SCRIPT],
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    output = result.stdout.decode("utf-8", errors="replace")
    print(output.rstrip())
    if result.returncode != 0:
        raise RuntimeError("打包失败（退出码 %d）" % result.returncode)

    after = set(os.listdir(DIST_DIR)) if os.path.isdir(DIST_DIR) else set()
    new_files = sorted(after - before)
    if new_files:
        # 优先取 tar.gz
        for name in new_files:
            if name.endswith(".tar.gz"):
                return os.path.join(DIST_DIR, name)
        return os.path.join(DIST_DIR, new_files[-1])

    # 无新增则取 dist 内最新的 tar.gz
    tars = [os.path.join(DIST_DIR, f) for f in after if f.endswith(".tar.gz")]
    if not tars:
        raise RuntimeError("未找到构建产物（%s 下无 tar.gz）" % DIST_DIR)
    return max(tars, key=os.path.getmtime)


def _move_to_dest(pkg_path, dest_dir, keep):
    """将部署包移动到目标目录。keep=True 时复制保留源文件。"""
    if not os.path.isdir(dest_dir):
        raise RuntimeError("目标目录不存在或不可访问: %s" % dest_dir)

    filename = os.path.basename(pkg_path)
    stem, ext = os.path.splitext(filename)  # ext 对 .tar.gz 仅得 .gz
    if filename.endswith(".tar.gz"):
        stem, ext = filename[:-7], ".tar.gz"

    target = os.path.join(dest_dir, filename)
    # 目标同名文件：追加时间戳避免覆盖
    if os.path.exists(target):
        stamp = time.strftime("%Y%m%d_%H%M%S")
        filename = "%s_%s%s" % (stem, stamp, ext)
        target = os.path.join(dest_dir, filename)
        print("      目标已存在同名文件，改为: %s" % filename)

    action = "复制" if keep else "移动"
    print("[3/3] 正在%s到香港目录 ..." % action)
    if keep:
        shutil.copy2(pkg_path, target)
    else:
        shutil.move(pkg_path, target)
    return target


def main():
    parser = argparse.ArgumentParser(description="一键打包并投递到香港目录")
    parser.add_argument("--dest", default="", help="香港目标目录（UNC/映射盘/Linux 挂载点）")
    parser.add_argument("--keep", action="store_true", help="保留 dist 内源文件（默认移动）")
    args = parser.parse_args()

    dest = _resolve_dest(args.dest)
    if not dest:
        print("错误: 未指定香港目标目录。")
        print("请通过以下任一方式指定：")
        print('  1) 命令行: python scripts/pack_to_hk.py --dest "Z:\\香港"')
        print("  2) 环境变量: set BOM3_HK_DIR=Z:\\香港  (Windows)")
        print("             export BOM3_HK_DIR=/mnt/hk  (Linux)")
        print("  3) 修改 scripts/pack_to_hk.py 中的 DEFAULT_HK_DIR")
        return 2

    print("=" * 60)
    print("BOM3 一键打包 → 香港目录")
    print("=" * 60)
    print("目标目录: %s" % dest)

    try:
        pkg = _build_package()
        print("[2/3] 构建完成: %s (%.1f KB)"
              % (pkg, os.path.getsize(pkg) / 1024))
        target = _move_to_dest(pkg, dest, args.keep)
    except Exception as e:
        print("失败: %s" % e)
        return 1

    print("=" * 60)
    print("完成! 部署包已投递至:")
    print("  %s" % target)
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
