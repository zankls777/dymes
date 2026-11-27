# -*- coding: utf-8 -*-
"""独立重启助手（由在线更新功能调用）

工作流程：
1) 读取任务文件（旧进程 PID、项目目录、解释器、启动参数、日志路径）；
2) 轮询等待旧进程退出（最多 60 秒），避免新旧进程同时抢占端口；
3) 以独立会话/无控制台方式拉起与原来相同的启动命令，输出追加到日志。

本脚本必须能被独立执行：python scripts/restart_helper.py <job.json>
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime


def _log(path, message):
    """追加写日志；日志不可写时静默忽略，不影响重启。"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n')
    except Exception:
        pass


def _pid_alive(pid):
    """跨平台判断进程是否存活（Windows 用 GetExitCodeProcess 判定 STILL_ACTIVE）。"""
    if os.name == 'nt':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def main():
    if len(sys.argv) < 2:
        return 1

    job_path = sys.argv[1]
    try:
        with open(job_path, 'r', encoding='utf-8') as f:
            job = json.load(f)
    except Exception:
        return 1

    app_dir = job.get('app_dir') or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_path = job.get('log') or os.path.join(app_dir, 'logs', 'update_restart.log')
    old_pid = int(job.get('pid') or 0)

    _log(log_path, f'重启助手启动，等待旧进程退出 (PID={old_pid})')
    for _ in range(120):  # 最多等待 60 秒
        if not old_pid or not _pid_alive(old_pid):
            break
        time.sleep(0.5)
    else:
        _log(log_path, '等待旧进程超时，仍尝试启动新进程')

    time.sleep(1.0)  # 给端口释放留出余量

    env = os.environ.copy()
    env.pop('WERKZEUG_RUN_MAIN', None)  # 避免继承开发热重载标记
    env.setdefault('FLASK_CONFIG', 'production')
    env['PYTHONUNBUFFERED'] = '1'

    argv = [job.get('python') or sys.executable] + [str(a) for a in (job.get('argv') or ['app.py'])]
    kwargs = {'cwd': app_dir, 'env': env, 'stdin': subprocess.DEVNULL}
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True

    try:
        with open(log_path, 'a', encoding='utf-8') as out:
            out.write(f'[{datetime.now():%Y-%m-%d %H:%M:%S}] 启动新进程: {" ".join(argv)}\n')
            proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT, **kwargs)
        _log(log_path, f'新进程已启动 (PID={proc.pid})')
    except Exception as exc:
        _log(log_path, f'启动新进程失败: {exc}')
        return 1
    finally:
        try:
            os.remove(job_path)
        except OSError:
            pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
