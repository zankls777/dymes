# -*- coding: utf-8 -*-
"""群晖 NAS WebDAV 客户端

设计要点：
    - 仅依赖 requests（requirements 已包含），兼容 Win7/Win10/Linux。
    - 配置以 JSON 文件形式存放在 instance/nas_config.json，密码做简单混淆存储，
      避免明文出现在磁盘上（非加密用途，仅防肉眼直读）。
    - 通过 WebDAV 的 PROPFIND (Depth: infinity) 在指定根路径下递归搜索文件。
    - 支持流式下载文件到本地，供导入文件管理复用既有解析逻辑。
"""
import os
import io
import re
import json
import base64
import logging

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

logger = logging.getLogger(__name__)

# 大响应流式解析用的正则（单次全文扫描，不构建 DOM）
# 注意：群晖返回的标签带命名空间前缀（D:/lp1:/g0:）且可能含属性，前缀需通配。
_HREF_RE = re.compile(r'<[A-Za-z0-9]*:?href\b[^>]*>(.*?)</[A-Za-z0-9]*:?href>', re.S | re.I)
# 按 path 精确取该文件的 contentlength（仅在命中的少量文件上使用）
_LEN_RE = re.compile(r'<[A-Za-z0-9]*:?getcontentlength\b[^>]*>(\d+)</', re.I)
# 默认实例目录（配置与 app.db 同级）
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_CONFIG_PATH = os.path.join(_BASE_DIR, 'instance', 'nas_config.json')

# 配置文件字段
_CONFIG_KEYS = ('url', 'dsm_url', 'username', 'password', 'auth_type',
                'root', 'roots', 'verify_ssl')

# 简单混淆用的固定盐值（防止密码明文落盘）
_SALT = b'bom3-nas-2026'


def _obfuscate(text):
    """对密码做 base64 + 固定盐异或混淆，避免明文存储。"""
    if not text:
        return ''
    raw = text.encode('utf-8')
    mixed = bytes((b ^ _SALT[i % len(_SALT)]) for i, b in enumerate(raw))
    return base64.b64encode(mixed).decode('ascii')


def _deobfuscate(token):
    """还原被 _obfuscate 混淆的密码。"""
    if not token:
        return ''
    try:
        mixed = base64.b64decode(token.encode('ascii'))
        raw = bytes((b ^ _SALT[i % len(_SALT)]) for i, b in enumerate(mixed))
        return raw.decode('utf-8')
    except Exception:
        return ''


def load_config():
    """读取 NAS 配置，返回 dict；未配置返回 None。

    返回的 dict 中 'roots' 为归一化后的多根目录列表（兼容旧的单值 'root'）。
    """
    if not os.path.exists(_CONFIG_PATH):
        return None
    try:
        with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not data.get('url') or not data.get('username'):
            return None
        data['password'] = _deobfuscate(data.get('password', ''))
        data['roots'] = normalize_roots(data.get('roots') or data.get('root'))
        data['root'] = data['roots'][0] if data['roots'] else '/'
        return data
    except Exception as e:
        logger.warning('读取 NAS 配置失败: %s', e)
        return None


def save_config(url, username, password, auth_type='basic',
                root='/', verify_ssl=True, keep_password=True, roots=None,
                dsm_url=''):
    """保存 NAS 配置。keep_password=True 且 password 为空时保留旧密码。

    roots：多根目录列表；传入时优先使用，否则回退到单值 root。
    dsm_url：DSM 主端口地址（用于索引搜索），留空则搜索时回退 WebDAV。
    """
    old = {}
    if os.path.exists(_CONFIG_PATH):
        try:
            with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
                old = json.load(f)
        except Exception:
            old = {}

    if not password and keep_password:
        pwd_token = old.get('password', '')
    else:
        pwd_token = _obfuscate(password)

    norm_roots = normalize_roots(roots if roots else root)
    data = {
        'url': (url or '').strip().rstrip('/'),
        'dsm_url': (dsm_url or '').strip().rstrip('/'),
        'username': (username or '').strip(),
        'password': pwd_token,
        'auth_type': auth_type if auth_type in ('basic', 'digest') else 'basic',
        'root': norm_roots[0] if norm_roots else '/',
        'roots': norm_roots,
        'verify_ssl': bool(verify_ssl),
    }
    os.makedirs(os.path.dirname(_CONFIG_PATH), exist_ok=True)
    with open(_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return True


def normalize_root(root):
    """规范化根路径：统一以 / 开头，不以 / 结尾（根目录除外）。"""
    root = (root or '/').strip()
    if not root.startswith('/'):
        root = '/' + root
    if len(root) > 1:
        root = root.rstrip('/')
    return root


def normalize_roots(roots):
    """规范化多根目录：接受字符串或列表，去重、去空、按顺序保留。"""
    if isinstance(roots, str):
        roots = [roots]
    result = []
    for r in (roots or []):
        nr = normalize_root(r)
        if nr not in result:
            result.append(nr)
    return result or ['/']


def _join(base, name):
    """拼接 WebDAV 路径，避免出现重复斜杠。"""
    base = normalize_root(base)
    if base.endswith('/'):
        return base + name
    return base + '/' + name


def _build_auth(cfg):
    if cfg.get('auth_type') == 'digest':
        return HTTPDigestAuth(cfg['username'], cfg['password'])
    return HTTPBasicAuth(cfg['username'], cfg['password'])


def _full_url(cfg, path):
    """把 WebDAV 相对路径拼成完整 URL（对中文/空格做安全编码）。"""
    base = cfg['url'].rstrip('/')
    path = normalize_root(path)
    # 分段编码，保留路径分隔符
    from urllib.parse import quote
    encoded = quote(path, safe='/')
    return base + encoded


def test_connection(cfg=None):
    """测试连接是否可用，返回 (success, message)。"""
    cfg = cfg or load_config()
    if not cfg:
        return False, 'NAS 配置不存在，请先完成配置'
    try:
        url = _full_url(cfg, cfg.get('root', '/'))
        resp = requests.request(
            'PROPFIND', url, auth=_build_auth(cfg),
            headers={'Depth': '0', 'Content-Type': 'application/xml'},
            timeout=15, verify=cfg.get('verify_ssl', True),
        )
        if resp.status_code in (200, 207):
            return True, '连接成功'
        if resp.status_code == 401:
            return False, '认证失败，请检查账号或密码'
        if resp.status_code == 404:
            return False, '路径不存在，请检查 WebDAV 地址或根路径'
        if resp.status_code == 405:
            return False, ('根路径不可用（HTTP 405）。WebDAV 根路径应为共享文件夹名，'
                           '如 /技术资料-，而不是物理路径 /volume1/技术资料-')
        return False, f'连接失败（HTTP {resp.status_code}）'
    except requests.exceptions.SSLError:
        return False, 'SSL 证书校验失败，可尝试关闭 HTTPS 证书校验后重试'
    except requests.exceptions.ConnectionError:
        return False, '无法连接到 NAS，请检查地址与网络'
    except requests.exceptions.Timeout:
        return False, '连接超时，请检查网络或地址'
    except Exception as e:
        return False, f'连接异常：{e}'


def _parse_propfind(xml_bytes, base_path):
    """解析 PROPFIND 响应，返回 [{name, path, is_dir}] 列表。

    为兼容 21 万级条目的大目录，这里直接对文本做单次 href 正则扫描
    （不构建 DOM、不切分 response 块），目录通过 href 以 '/' 结尾判定。
    文件大小不在此处提取（避免大文本回溯），由调用方按需对少量命中项
    调用 _extract_size 精确获取。
    base_path：请求的根路径（用于把 href 还原成相对 WebDAV 路径）。
    """
    items = []
    if not xml_bytes:
        return items

    from urllib.parse import unquote
    from xml.sax.saxutils import unescape
    base_path = normalize_root(base_path)
    if isinstance(xml_bytes, bytes):
        text = xml_bytes.decode('utf-8', 'replace')
    else:
        text = xml_bytes

    for m in _HREF_RE.finditer(text):
        raw_href = m.group(1).strip()
        href = unquote(raw_href)
        # 去掉完整 URL 前缀（部分服务器返回绝对地址）
        if '://' in href:
            idx = href.find('/', href.find('://') + 3)
            href = href[idx:] if idx != -1 else '/'
        href = href.split('?')[0]
        is_dir = href.endswith('/')

        rel = href
        if base_path and rel.startswith(base_path):
            rel = rel[len(base_path):]
        rel = rel.strip('/')
        if not rel:
            continue  # 跳过根目录自身

        name = rel.split('/')[-1]
        # XML 中 & 等会被转义为实体，需还原为真实字符
        name = unescape(name)
        # raw_href 保留服务器原始（未解码）形式，供后续精确取文件大小
        items.append({'name': name, 'path': rel, 'is_dir': is_dir, 'raw_href': raw_href})
    return items


def _extract_size(xml_bytes, raw_href):
    """从 PROPFIND 原始响应中取出指定文件（按原始 href 定位）的字节大小。

    仅在命中的少量文件上调用。直接用服务器返回的原始 href 做字节定位，
    避免因 quote/unquote 细节差异导致匹配失败；随后在其后窗口内取
    getcontentlength（取不到返回 0）。
    """
    if not raw_href:
        return 0
    probe = raw_href.encode('utf-8')
    pos = xml_bytes.find(probe)
    if pos == -1:
        return 0
    window = xml_bytes[pos:pos + 4096].decode('utf-8', 'replace')
    m = _LEN_RE.search(window)
    if not m:
        return 0
    try:
        return int(m.group(1))
    except ValueError:
        return 0


def list_dirs(cfg, path='/', timeout=30):
    """列出指定 WebDAV 路径下的子文件夹（不含文件）。

    返回 (dirs, cur_path)：dirs 为 [{'name','path'}]（path 为 WebDAV 相对路径，
    可直接作为根路径再次传入），cur_path 为规范化后的当前路径。
    """
    cfg = cfg or load_config()
    if not cfg:
        raise RuntimeError('NAS 配置不存在')

    cur = normalize_root(path or '/')
    url = _full_url(cfg, cur)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:propfind xmlns:d="DAV:">'
        '<d:prop><d:resourcetype/></d:prop>'
        '</d:propfind>'
    )
    resp = requests.request(
        'PROPFIND', url, auth=_build_auth(cfg), data=body.encode('utf-8'),
        headers={'Depth': '1', 'Content-Type': 'application/xml; charset=utf-8'},
        timeout=timeout, verify=cfg.get('verify_ssl', True),
    )
    if resp.status_code == 401:
        raise RuntimeError('认证失败，请检查账号或密码')
    if resp.status_code not in (200, 207):
        raise RuntimeError(f'读取目录失败（HTTP {resp.status_code}）')

    items = _parse_propfind(resp.content, cur)
    dirs = []
    for it in items:
        if '/' in it['path']:
            continue  # 仅直接子项
        # 群晖 WebDAV 在根目录返回的子项 href 不带结尾斜杠，导致 is_dir 误判为 False。
        # 这里以「目录标记 或 名称不含扩展名」作为判据兜底。
        is_dir = it['is_dir'] or ('.' not in it['name'])
        if not is_dir:
            continue
        dirs.append({'name': it['name'], 'path': _join(cur, it['name'])})
    dirs.sort(key=lambda d: d['name'].lower())
    return dirs, cur



def _search_one_root(cfg, root, kws, exts, max_depth_files, timeout, match_any=None):
    """在单个根路径下递归搜索，返回匹配文件列表。

    kws/扩展名为基础过滤；match_any 为「任一命中」的品番关键字列表
    （用于批量品番场景，命中任意一个即保留，避免把全部文件带回）。
    """
    root = normalize_root(root or '/')
    url = _full_url(cfg, root)
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<d:propfind xmlns:d="DAV:">'
        '<d:prop><d:resourcetype/><d:getcontentlength/></d:prop>'
        '</d:propfind>'
    )
    resp = requests.request(
        'PROPFIND', url, auth=_build_auth(cfg), data=body.encode('utf-8'),
        headers={'Depth': 'infinity', 'Content-Type': 'application/xml; charset=utf-8'},
        timeout=timeout, verify=cfg.get('verify_ssl', True),
    )
    if resp.status_code not in (200, 207):
        if resp.status_code == 405:
            raise RuntimeError(f'搜索失败（HTTP 405）：根路径 {root} 不可用，'
                               'WebDAV 根路径应选共享文件夹，而不是物理路径 /volume1/xxx')
        raise RuntimeError(f'搜索失败（HTTP {resp.status_code}）')

    all_items = _parse_propfind(resp.content, root)
    raw = resp.content

    from routes import detect_file_type
    results = []
    for item in all_items:
        if item['is_dir']:
            continue
        lower_name = item['name'].lower()
        # 扩展名过滤（文件名包含 SubList/UsageList 或 .xls/.xlsx）
        if exts and not any(e in lower_name for e in exts):
            continue
        # 基础关键字过滤：全部命中
        if kws and not all(k in lower_name for k in kws):
            continue
        # 批量品番过滤：命中任意一个即保留
        if match_any and not any(k in lower_name for k in match_any):
            continue
        file_type = detect_file_type(item['name'])
        if not file_type:
            continue
        item['file_type'] = file_type
        item['size'] = _extract_size(raw, item.get('raw_href'))
        item['size_kb'] = round(item['size'] / 1024, 1)
        results.append(item)
        if max_depth_files and len(results) >= max_depth_files:
            break
    return results


def _search_by_index(cfg, target_roots, any_kws, kws, exts, max_files, workers=None):
    """优先走 DSM 文件索引搜索（秒级）；未配置 dsm_url 或全部失败时返回 None。

    批量品番场景下，把「根目录 × 品番」组合并发提交给索引接口
    （单次约 0.3 秒，串行上百品番会很慢，故并发）。
    """
    from utils.nas.dsm_client import make_client, search_many, DEFAULT_WORKERS
    client = make_client(cfg)
    if client is None:
        return None

    patterns = list(any_kws) or list(kws)
    if not patterns:
        return None

    hits, failed = search_many(
        client, target_roots, patterns, extensions=exts,
        workers=workers or DEFAULT_WORKERS,
    )
    # 全部请求都失败时返回 None，交由 WebDAV 兜底
    if failed and not hits:
        return None

    from routes import detect_file_type
    results = []
    for h in hits:
        name = h['name']
        file_type = detect_file_type(name)
        if not file_type:
            continue
        results.append({
            'name': name,
            'path': h['path'],
            'file_type': file_type,
            'size': h['size'],
            'size_kb': round(h['size'] / 1024, 1),
        })
        if max_files and len(results) >= max_files:
            break
    return results


def search_files(cfg, keywords, root=None, roots=None, extensions=('sub', 'usage'),
                 max_depth_files=0, timeout=120, match_any=None, use_index=True,
                 workers=None):
    """在 NAS 根路径（可多个）下递归搜索文件名包含关键字的文件。

    默认优先使用 DSM 文件索引搜索（需在配置中填写 dsm_url），
    未配置或索引不可用时自动回退到 WebDAV 全量扫描。

    keywords：文件名需要同时包含的关键字列表（不区分大小写）。
    match_any：命中任意一个即保留的关键字列表（批量品番场景，优先于 keywords）。
    root/roots：单个根路径或多根路径列表；均未指定时使用配置中的 roots。
    extensions：文件类型关键字（按文件名包含判断，如 'sublist'/'usagelist'）。
    max_depth_files：结果数量上限，0 表示不限制。
    workers：索引搜索并发数，None 时用默认值。
    """
    cfg = cfg or load_config()
    if not cfg:
        raise RuntimeError('NAS 配置不存在')

    if roots:
        target_roots = normalize_roots(roots)
    elif root:
        target_roots = normalize_roots(root)
    else:
        target_roots = normalize_roots(cfg.get('roots') or cfg.get('root'))

    kws = [str(k).lower() for k in (keywords or []) if str(k).strip()]
    any_kws = [str(k).lower() for k in (match_any or []) if str(k).strip()]
    exts = [str(e).lower() for e in (extensions or []) if str(e).strip()]

    if use_index:
        indexed = _search_by_index(cfg, target_roots, any_kws, kws, exts,
                                   max_depth_files, workers)
        if indexed is not None:
            return indexed

    results = []
    seen = set()
    for r in target_roots:
        for item in _search_one_root(cfg, r, kws, exts, max_depth_files, timeout, any_kws):
            if item['path'] in seen:
                continue
            seen.add(item['path'])
            results.append(item)
    return results


def download_file(cfg, remote_path, dest_path, timeout=300):
    """从 NAS 下载文件到本地 dest_path，返回文件字节数。"""
    cfg = cfg or load_config()
    if not cfg:
        raise RuntimeError('NAS 配置不存在')

    url = _full_url(cfg, remote_path)
    resp = requests.get(
        url, auth=_build_auth(cfg), stream=True,
        timeout=timeout, verify=cfg.get('verify_ssl', True),
    )
    if resp.status_code != 200:
        raise RuntimeError(f'下载失败（HTTP {resp.status_code}）')

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    written = 0
    with open(dest_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            if chunk:
                f.write(chunk)
                written += len(chunk)
    return written


def get_bytes(cfg, remote_path, timeout=300):
    """将 NAS 文件读取到内存（BytesIO），供直接解析使用。"""
    cfg = cfg or load_config()
    if not cfg:
        raise RuntimeError('NAS 配置不存在')
    url = _full_url(cfg, remote_path)
    resp = requests.get(
        url, auth=_build_auth(cfg), timeout=timeout,
        verify=cfg.get('verify_ssl', True),
    )
    if resp.status_code != 200:
        raise RuntimeError(f'下载失败（HTTP {resp.status_code}）')
    return io.BytesIO(resp.content)
