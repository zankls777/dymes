# -*- coding: utf-8 -*-
"""群晖 DSM Web API 客户端（索引搜索）

用途：
    通过 DSM 的文件索引（SYNO.FileStation.Search v2）做秒级文件名搜索，
    替代 WebDAV Depth:infinity 的全量扫描（21 万条目约需 16 秒，
    索引搜索实测 0.36 秒）。

设计要点：
    - 仅依赖 requests，兼容 Win7/Win10/Linux。
    - DSM 端口与 WebDAV 端口不同（本项目实测 55000 / 55005），
      因此 DSM 地址单独配置，见 nas_config.json 的 dsm_url 字段。
    - 参数格式需与 FileStation 前端一致：字符串参数用 JSON 引号包裹、
      pattern 用 *kw* 通配、search_type="simple"、search_content=true。
"""
import json
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

import requests

logger = logging.getLogger(__name__)

# 索引搜索超时（索引命中通常 1 秒内，留足冷启动余量）
_SEARCH_TIMEOUT = 30
# 轮询等待上限（秒）
_POLL_MAX_SECONDS = 20
# 并发搜索的默认线程数（DSM 单体设备，过大反而拖慢）
DEFAULT_WORKERS = 6


class DsmError(RuntimeError):
    """DSM 交互异常。"""


def _q(value):
    """按前端格式把参数编码为 JSON 值（字符串会带双引号）。"""
    return json.dumps(value, ensure_ascii=False)


class DsmClient(object):
    """DSM Web API 会话（懒登录，线程安全）。

    每个线程持有独立的 requests.Session 与 sid：DSM 的搜索任务是按 sid
    隔离的，多线程共用同一 sid 可能被限流或互相干扰，因此用线程局部存储。
    """

    def __init__(self, base_url, username, password, verify_ssl=True):
        self.base_url = (base_url or '').rstrip('/')
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self._local = threading.local()

    # ---------------------------------------------------------------- 登录
    @property
    def _session(self):
        return getattr(self._local, 'session', None)

    @property
    def _sid(self):
        return getattr(self._local, 'sid', None)

    def _login(self):
        if not self.base_url:
            raise DsmError('未配置 DSM 地址（请填写形如 http://ip:55000 的 DSM 主端口地址）')
        s = requests.Session()
        s.verify = self.verify_ssl
        try:
            resp = s.get(
                self.base_url + '/webapi/auth.cgi',
                params={
                    'api': 'SYNO.API.Auth', 'version': '6', 'method': 'login',
                    'account': self.username, 'passwd': self.password,
                    'session': 'FileStation', 'format': 'sid',
                },
                timeout=_SEARCH_TIMEOUT,
            )
        except requests.exceptions.ConnectionError:
            raise DsmError('无法连接 DSM，请检查 DSM 地址与网络（注意 DSM 端口通常与 WebDAV 不同）')

        try:
            data = resp.json()
        except ValueError:
            raise DsmError('DSM 登录响应异常，请确认地址指向 DSM 而非 WebDAV 端口')
        if not data.get('success'):
            code = (data.get('error') or {}).get('code')
            if code in (400, 401):
                raise DsmError('DSM 认证失败，请检查账号或密码')
            raise DsmError('DSM 登录失败（错误码 %s）' % code)

        self._local.session = s
        self._local.sid = data['data']['sid']

    def _call(self, api, method, version, params=None):
        """调用 entry.cgi；sid 失效时自动重登一次。"""
        for attempt in (0, 1):
            if self._sid is None:
                self._login()
            p = {'api': api, 'version': version, 'method': method, '_sid': self._sid}
            if params:
                p.update(params)
            resp = self._session.get(
                self.base_url + '/webapi/entry.cgi', params=p, timeout=_SEARCH_TIMEOUT,
            )
            data = resp.json()
            if data.get('success'):
                return data.get('data') or {}
            code = (data.get('error') or {}).get('code')
            # 106/119 表示会话失效，重登后重试一次
            if code in (106, 119) and attempt == 0:
                self._local.sid = None
                continue
            raise DsmError('DSM 接口 %s 调用失败（错误码 %s）' % (api, code))
        return {}

    # ------------------------------------------------------------ 索引搜索
    def search(self, root, keyword, extensions=None, timeout=_POLL_MAX_SECONDS):
        """在 root 下按文件名关键字做索引搜索。

        keyword：文件名关键字（内部自动包成 *kw* 通配）。
        extensions：可选文件名关键字（如 ['sublist','usagelist']，按「包含」判断）。
        返回 [{'name','path','size'}]，path 为 DSM 绝对路径（含共享文件夹名）。
        """
        extra = {
            'folder_path': _q([root]),
            'recursive': 'true',
            'pattern': _q('*%s*' % keyword),
            'search_content': 'true',
            'search_type': _q('simple'),
        }
        start = self._call('SYNO.FileStation.Search', 'start', '2', extra)
        taskid = start.get('taskid')
        if not taskid:
            return []

        deadline = time.time() + timeout
        files = []
        while True:
            data = self._call('SYNO.FileStation.Search', 'list', '2', {
                'taskid': _q(taskid),
                'offset': '0',
                'limit': '2000',
                'filetype': _q('all'),
                'additional': _q(['real_path', 'size', 'type']),
            })
            files = data.get('files') or []
            if data.get('finished', True) or time.time() > deadline:
                break
            time.sleep(0.2)

        try:
            self._call('SYNO.FileStation.Search', 'stop', '2', {'taskid': _q(taskid)})
        except DsmError:
            pass  # 任务可能已自然结束

        exts = tuple(str(e).lower() for e in (extensions or []) if str(e).strip())
        results = []
        for f in files:
            path = f.get('path') or ''
            if not path:
                continue
            name = path.rstrip('/').split('/')[-1]
            if exts and not any(e in name.lower() for e in exts):
                continue
            additional = f.get('additional') or {}
            results.append({
                'name': name,
                'path': path,
                'size': int(additional.get('size') or 0),
            })
        return results


def make_client(cfg):
    """依据 NAS 配置构造 DSM 客户端；未配置 dsm_url 时返回 None（调用方回退 WebDAV）。"""
    dsm_url = (cfg or {}).get('dsm_url') or ''
    if not dsm_url:
        return None
    return DsmClient(dsm_url, cfg.get('username'), cfg.get('password'),
                     cfg.get('verify_ssl', True))


def search_many(client, roots, keywords, extensions=None, workers=DEFAULT_WORKERS):
    """并发执行「根目录 × 关键字」组合的索引搜索，返回 (hits, failed)。

    hits：[{'name','path','size'}]（已按 path 去重，保持返回顺序稳定）。
    failed：失败组合数，供调用方判断是否整体回退 WebDAV。
    无关键字或无根目录时返回 ([], 0)。
    """
    roots = [r for r in (roots or []) if r]
    keywords = [k for k in (keywords or []) if k]
    if not roots or not keywords:
        return [], 0

    tasks = [(r, k) for r in roots for k in keywords]
    workers = max(1, min(int(workers or 1), len(tasks)))

    results = []
    failed = 0
    lock = threading.Lock()

    def _work(pair):
        root, kw = pair
        return client.search(root, kw, extensions=extensions)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_work, t) for t in tasks]
        for future in futures:
            try:
                hits = future.result()
            except Exception as e:
                with lock:
                    failed += 1
                logger.warning('索引搜索任务失败：%s', e)
                continue
            results.extend(hits)

    seen = set()
    uniq = []
    for h in results:
        if h['path'] in seen:
            continue
        seen.add(h['path'])
        uniq.append(h)
    return uniq, failed
