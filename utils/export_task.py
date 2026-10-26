# -*- coding: utf-8 -*-
"""导出/备份后台任务：把长耗时导出放到后台线程，前端轮询进度。

背景
----
整库 2GB+ 的备份/导出是分钟级长任务，若同步阻塞请求，浏览器会长时间
无响应、用户以为"卡死"。本模块用一个单例任务状态 + 后台线程承载导出，
前端通过 /progress 接口拿到百分比与阶段文案，完成后通过 /download 接口
取回文件（一次性、取完即清理）。

设计要点
--------
1. 同一时刻只允许一个导出任务（避免磁盘/IO 争抢与临时文件堆积）；
2. 临时文件统一放在数据库同盘目录，规避系统盘写满；
3. 任务完成后保留临时文件直到被下载或超时，避免请求竞态下丢文件；
4. 仅依赖标准库，兼容 Windows 7/10 与 Linux。
"""
import os
import threading
import time

from utils.time_utils import get_utc8_time, format_utc8_time

# 临时文件保留时长（秒）：下载完成后立即删除；若用户未下载则超时清理
_KEEP_SECONDS = 30 * 60

_LOCK = threading.Lock()
# 单例任务状态（同一时刻仅一个导出任务）
_STATE = {
    'running': False,
    'percent': 0,
    'message': '',
    'stage': 'idle',
    'success': None,
    'error': '',
    'started_at': None,
    'finished_at': None,
    'download_name': '',   # 下载时展示的文件名
    'file_path': '',       # 生成的临时文件绝对路径
    'file_size': 0,        # 文件字节数
    'tag': '',             # 任务标识（功能模块名 / 多表等），用于展示
    'created_ts': 0,       # 文件生成时间戳（用于超时清理）
}


def _update(**kwargs):
    """线程安全地更新任务状态。"""
    with _LOCK:
        _STATE.update(kwargs)


def get_progress():
    """返回当前导出任务的状态快照（供前端轮询；不返回本地文件路径）。"""
    with _LOCK:
        s = dict(_STATE)
    s.pop('file_path', None)
    return s


def get_result_file():
    """返回 (file_path, download_name)，无可用结果时返回 (None, None)。"""
    with _LOCK:
        if _STATE.get('success') and _STATE.get('file_path') \
                and os.path.exists(_STATE['file_path']):
            return _STATE['file_path'], _STATE.get('download_name') or 'export'
    return None, None


def clear_result(delete_file=True):
    """清理任务结果（下载后调用）。"""
    with _LOCK:
        path = _STATE.get('file_path')
        _STATE.update(file_path='', download_name='', file_size=0,
                      success=None, stage='idle', percent=0, message='')
    if delete_file and path:
        try:
            os.remove(path)
        except OSError:
            pass


def is_running():
    with _LOCK:
        return bool(_STATE.get('running'))


def _cleanup_stale_files():
    """清理超过保留时长仍未下载的临时结果文件（best-effort）。"""
    with _LOCK:
        path, created = _STATE.get('file_path'), _STATE.get('created_ts', 0)
    if path and created and (time.time() - created) > _KEEP_SECONDS:
        clear_result(delete_file=True)


def start_export_async(app, worker, download_name, tag='') -> threading.Thread:
    """启动后台导出线程。

    参数
    ----
    app:            Flask 应用对象（线程内需 app_context）
    worker:         可调用对象 worker(progress_cb, result_path) -> None，
                    内部负责把结果写入 result_path，并通过 progress_cb
                    回调 (percent, message) 上报进度；失败时抛异常。
    download_name:  完成后前端下载的默认文件名
    tag:            任务展示标识

    返回后台线程；若已有任务在跑则抛 RuntimeError。
    """
    if is_running():
        raise RuntimeError('已有导出任务正在执行，请等待完成')
    _cleanup_stale_files()

    from data_management_service import new_backup_path
    # 依据下载文件名后缀决定临时文件后缀，便于前端/系统识别类型
    suffix = os.path.splitext(download_name)[1] or '.dat'
    result_path = new_backup_path(suffix)

    _update(running=True, percent=0, stage='running', success=None, error='',
            message='准备导出…', started_at=format_utc8_time(get_utc8_time()),
            finished_at=None, download_name=download_name, tag=tag,
            file_path=result_path, file_size=0, created_ts=0)

    def _progress_cb(pct, msg):
        _update(percent=max(0, min(100, int(pct))), message=msg or '')

    def _worker():
        with app.app_context():
            try:
                worker(_progress_cb, result_path)
                size = os.path.getsize(result_path) if os.path.exists(result_path) else 0
                # 进度封顶 99%，真正的 100% 只由"完成"状态给出
                _update(running=False, success=True, stage='done', percent=100,
                        message='导出完成，正在下载…', file_size=size,
                        created_ts=time.time(),
                        finished_at=format_utc8_time(get_utc8_time()))
            except Exception as e:
                # 失败时清理半成品文件
                try:
                    if os.path.exists(result_path):
                        os.remove(result_path)
                except OSError:
                    pass
                _update(running=False, success=False, stage='error',
                        message=f'导出失败：{e}', error=f'{type(e).__name__}: {e}',
                        file_path='', file_size=0,
                        finished_at=format_utc8_time(get_utc8_time()))

    t = threading.Thread(target=_worker, daemon=True, name='db_export')
    t.start()
    return t
