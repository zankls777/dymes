# -*- coding: utf-8 -*-
"""系统在线更新与自动重启

职责：
1) 汇总当前运行信息（版本、运行模式、数据库、运行时长等）；
2) 检查 GitHub 是否有新版本：Git 仓库模式走 git fetch，部署包模式走 GitHub/Gitee API；
3) 执行更新：Git 模式快进合并；部署包模式下载源码压缩包覆盖源码文件，
   始终保留 instance/ logs/ backups/ .venv 等运行数据；
4) 更新后由独立助手进程等待旧进程退出再重新拉起服务，避免“更新即掉线”。

说明：
- 仅管理员可执行更新（路由层校验），本模块只负责能力实现；
- 远端优先 GitHub，失败回退 Gitee（同一仓库镜像），适配 NAS 网络环境。
"""
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

GITHUB_OWNER = 'zankls777'
GITHUB_REPO = 'dymes'
BRANCH = 'master'
REPO_URL = f'https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}'
GIT_URL = f'{REPO_URL}.git'
# 同一仓库的 Gitee 镜像：GitHub 不通时用于拉取（镜像保留相同提交号，可快进合并）
GITEE_GIT_URL = f'https://gitee.com/{GITHUB_OWNER}/{GITHUB_REPO}.git'
GITHUB_ARCHIVE = (f'https://codeload.github.com/{GITHUB_OWNER}/{GITHUB_REPO}'
                  f'/tar.gz/refs/heads/{BRANCH}')
GITEE_ARCHIVE = (f'https://gitee.com/{GITHUB_OWNER}/{GITHUB_REPO}'
                 f'/repository/archive/{BRANCH}.tar.gz')
GITHUB_COMMIT_API = (f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
                     f'/commits/{BRANCH}')
GITEE_COMMIT_API = (f'https://gitee.com/api/v5/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
                    f'/commits/{BRANCH}')

APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(APP_ROOT, 'instance', 'update_state.json')
LOG_DIR = os.path.join(APP_ROOT, 'logs')

# 覆盖更新时必须保留的顶层目录（运行数据 / 虚拟环境 / 依赖 / 构建产物）
_SKIP_TOP = {
    '.git', '.venv', 'venv', 'instance', 'logs', 'backups', 'node_modules',
    'dist', '__pycache__', 'tmp_dxf', 'tmp_png', 'testdata', '.screenshots',
    '.trae', '.idea', '.vscode',
}
# 覆盖更新时保留的文件（密钥、日志）
_SKIP_FILES = {'.secret_key', 'server.log', 'debug.log', '.DS_Store'}
# 覆盖前备份已存在源码文件的容量上限（超出后仅覆盖不再备份）
_BACKUP_LIMIT = 50 * 1024 * 1024

HISTORY_FILE = os.path.join(APP_ROOT, 'instance', 'update_history.json')

_UPDATE_LOCK = threading.Lock()

# 更新任务进度（进程内共享，供前端轮询展示）
_PROGRESS_LOCK = threading.Lock()
# 远端提交历史缓存（部署包模式避免每次打开“关于”页都走网络）
_CHANGE_CACHE = {'ts': 0.0, 'data': None}
_PROGRESS = {
    'running': False, 'stage': 'idle', 'percent': 0, 'message': '',
    'started_at': 0.0, 'finished_at': 0.0, 'error': '', 'result': None,
}


# ---------------------------------------------------------------- 基础工具
def _now_text():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def _set_progress(stage, percent, message, running=True, error='', result=None):
    """更新任务进度回调：前端通过 /api/about/update/status 轮询读取。"""
    with _PROGRESS_LOCK:
        _PROGRESS.update({
            'running': running, 'stage': stage,
            'percent': max(0, min(100, int(percent))), 'message': message,
            'error': error,
        })
        if result is not None:
            _PROGRESS['result'] = result
        if not running:
            _PROGRESS['finished_at'] = time.time()


def get_update_status():
    """返回当前更新任务状态（含已耗时秒数）。"""
    with _PROGRESS_LOCK:
        data = dict(_PROGRESS)
    started = data.get('started_at') or 0
    end = data.get('finished_at') or time.time()
    data['elapsed'] = int(end - started) if started else 0
    return data


def _git(args, timeout=60):
    """执行 git 命令（工作目录固定为项目根目录），失败抛出可读异常。"""
    exe = shutil.which('git')
    if not exe:
        raise RuntimeError('未安装 git，无法使用 Git 模式更新')
    # 禁止 git 弹出账号密码提示，避免后台请求挂起
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    proc = subprocess.run([exe, '-C', APP_ROOT] + list(args), env=env,
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(detail[:500] or f'git 执行失败: {" ".join(args)}')
    return (proc.stdout or '').strip()


def _has_git_repo():
    return os.path.isdir(os.path.join(APP_ROOT, '.git'))


def _update_mode():
    """决定本次更新方式：有 .git 且系统有 git 时用 git，否则用压缩包覆盖。"""
    if _has_git_repo() and shutil.which('git'):
        return 'git'
    return 'archive'


def _mode_label(mode):
    return 'Git 仓库' if mode == 'git' else '部署包'


def _read_state():
    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_state(commit='', source=''):
    """记录部署包模式的当前版本，供下次检查更新比对。"""
    state = _read_state()
    state.update({'commit': commit or '', 'updated_at': _now_text(),
                  'source': source or state.get('source', '')})
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _local_git_version():
    """读取 Git 工作区的当前提交信息；失败返回空字典。"""
    try:
        return {
            'commit': _git(['rev-parse', 'HEAD']),
            'commit_date': _git(['log', '-1', '--format=%ad',
                                 '--date=format:%Y-%m-%d %H:%M:%S']),
            'commit_message': _git(['log', '-1', '--format=%s']),
            'branch': _git(['rev-parse', '--abbrev-ref', 'HEAD']),
        }
    except Exception:
        return {}


def _local_version(mode=None):
    """当前版本信息：Git 模式取提交，部署包模式取更新状态文件。"""
    mode = mode or _update_mode()
    if mode == 'git':
        info = _local_git_version()
        if info:
            return info
    state = _read_state()
    return {'commit': state.get('commit', ''),
            'commit_date': state.get('updated_at', ''),
            'commit_message': '部署包模式（覆盖更新）',
            'branch': BRANCH}


def _fetch_remote_commit():
    """通过 API 获取远端最新提交；GitHub 不通时自动回退 Gitee。"""
    headers = {'User-Agent': 'BOM3-Updater', 'Accept': 'application/json'}
    for api in (GITHUB_COMMIT_API, GITEE_COMMIT_API):
        try:
            req = Request(api, headers=headers)
            with urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode('utf-8'))
            sha = data.get('sha') or ''
            commit = data.get('commit') or {}
            message = (commit.get('message') or '').splitlines()
            committer = commit.get('committer') or {}
            if sha:
                return {'commit': sha,
                        'commit_message': message[0] if message else '',
                        'commit_date': committer.get('date') or data.get('created_at', ''),
                        'source': api}
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- 对外信息
def get_system_info(app_config=None):
    """返回“关于”页展示的系统与版本信息（不发起网络请求）。"""
    mode = _update_mode()
    local = _local_version(mode)
    config = app_config or {}
    started = float(config.get('APP_STARTED_AT') or 0)
    db_uri = str(config.get('SQLALCHEMY_DATABASE_URI') or '')
    db_path = db_uri[len('sqlite:///'):] if db_uri.startswith('sqlite:///') else db_uri
    return {
        'app_name': '大焱资料管理系统',
        'mode': mode,
        'mode_label': _mode_label(mode),
        'repo_url': REPO_URL,
        'branch': local.get('branch') or BRANCH,
        'commit': local.get('commit', ''),
        'commit_short': (local.get('commit') or '')[:8],
        'commit_date': local.get('commit_date', ''),
        'commit_message': local.get('commit_message', ''),
        'flask_config': os.environ.get('FLASK_CONFIG', 'development'),
        'port': os.environ.get('APP_PORT', '5004'),
        'python_version': platform.python_version(),
        'platform': f'{platform.system()} {platform.release()}',
        'hostname': socket.gethostname(),
        'db_path': db_path,
        'started_at': (datetime.fromtimestamp(started).strftime('%Y-%m-%d %H:%M:%S')
                       if started else ''),
        'uptime_seconds': int(time.time() - started) if started else 0,
        'server_time': _now_text(),
    }


# ---------------------------------------------------------------- 检查更新
def check_update():
    """检查是否有新版本；网络异常时抛出可读异常，由路由转成提示。"""
    mode = _update_mode()
    if mode == 'git':
        return _check_git()
    return _check_archive()


def _git_fetch_any(timeout=90):
    """依次尝试 GitHub、Gitee 拉取；返回 (实际使用的地址, FETCH_HEAD 提交号)。"""
    errors = []
    for url in (GIT_URL, GITEE_GIT_URL):
        try:
            _git(['fetch', '--prune', '--quiet', url, BRANCH], timeout=timeout)
            return url, _git(['rev-parse', 'FETCH_HEAD'])
        except Exception as exc:
            errors.append(f'{url} -> {exc}')
    raise RuntimeError('GitHub 与 Gitee 均无法连接：' + '；'.join(errors))


def _check_git():
    used_url, remote = _git_fetch_any()
    behind = int(_git(['rev-list', '--count', 'HEAD..FETCH_HEAD']) or 0)
    commits = []
    if behind:
        raw = _git(['log', '--pretty=format:%h|%ad|%s',
                    '--date=format:%Y-%m-%d %H:%M', 'HEAD..FETCH_HEAD', '-10'])
        for line in raw.splitlines():
            parts = line.split('|', 2)
            if len(parts) == 3:
                commits.append({'short': parts[0], 'date': parts[1], 'message': parts[2]})
    return {
        'mode': 'git', 'mode_label': _mode_label('git'),
        'local': _local_git_version(), 'remote_commit': remote,
        'update_available': behind > 0, 'behind': behind, 'commits': commits,
        'source': used_url,
        'message': (f'发现 {behind} 个新提交' if behind else '当前已是最新版本')
                   + ('（经 Gitee 镜像获取）' if 'gitee.com' in used_url else ''),
    }


def _check_archive():
    local = _local_version('archive')
    remote = _fetch_remote_commit()
    if not remote:
        return {
            'mode': 'archive', 'mode_label': _mode_label('archive'),
            'local': local, 'remote_commit': '', 'update_available': None,
            'behind': 0, 'commits': [],
            'message': '无法连接 GitHub/Gitee 获取版本信息，可直接点击更新尝试',
        }
    available = (remote.get('commit') != local.get('commit')) if local.get('commit') else True
    if not local.get('commit'):
        message = '本地未记录版本号（首次更新），建议执行一次更新'
    elif available:
        message = '发现新版本'
    else:
        message = '当前已是最新版本'
    return {
        'mode': 'archive', 'mode_label': _mode_label('archive'),
        'local': local, 'remote_commit': remote.get('commit', ''),
        'remote_date': remote.get('commit_date', ''),
        'remote_message': remote.get('commit_message', ''),
        'update_available': available, 'behind': 1 if available else 0,
        'commits': [{'short': (remote.get('commit') or '')[:8],
                     'date': remote.get('commit_date', ''),
                     'message': remote.get('commit_message', '')}] if available else [],
        'message': message,
    }


# ---------------------------------------------------------------- 执行更新
def perform_update(progress=None):
    """执行更新（Git 快进合并 或 压缩包覆盖），返回更新结果；不负责重启。

    progress: 可选回调 (stage, percent, message)，用于向页面汇报进度。
    """
    if not _UPDATE_LOCK.acquire(blocking=False):
        raise RuntimeError('已有更新任务正在执行，请稍后再试')
    try:
        report = progress or (lambda *args, **kwargs: None)
        report('check', 5, '校验运行环境...')
        if _update_mode() == 'git':
            result = _update_git(report)
            result['mode'] = 'git'
        else:
            result = _update_archive(report)
            result['mode'] = 'archive'
        result['mode_label'] = _mode_label(result['mode'])
        result['finished_at'] = _now_text()
        return result
    finally:
        _UPDATE_LOCK.release()


def _update_git(progress=None):
    report = progress or (lambda *args, **kwargs: None)
    before = _git(['rev-parse', 'HEAD'])
    report('download', 25, '正在从远端拉取最新代码...')
    used_url, remote = _git_fetch_any(timeout=180)
    if before == remote:
        report('apply', 90, '当前已是最新版本')
        return {'updated': False, 'from': before, 'to': before, 'file_count': 0,
                'files': [], 'source': used_url,
                'message': '当前已是最新版本，无需更新'}
    report('apply', 60, '正在快进合并本地代码...')
    try:
        _git(['merge', '--ff-only', 'FETCH_HEAD'], timeout=120)
    except RuntimeError as exc:
        raise RuntimeError(f'自动合并失败（可能存在本地修改或分支冲突）：{exc}')
    after = _git(['rev-parse', 'HEAD'])
    changed = [line for line in _git(['diff', '--name-only', before, after]).splitlines() if line]
    report('apply', 90, f'代码合并完成，共 {len(changed)} 个文件变更')
    return {'updated': True, 'from': before, 'to': after,
            'file_count': len(changed), 'files': changed[:300], 'source': used_url,
            'message': f'更新成功，共 {len(changed)} 个文件变更'}


def _download(url, dest, timeout=180, progress=None, base=10, span=45):
    """下载更新包到本地文件；progress(stage, percent, message) 汇报下载进度。"""
    req = Request(url, headers={'User-Agent': 'BOM3-Updater'})
    with urlopen(req, timeout=timeout) as resp, open(dest, 'wb') as out:
        total = int(resp.headers.get('Content-Length') or 0)
        done = 0
        last_percent = -1
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                percent = base + int(span * done / total)
                if percent != last_percent:
                    last_percent = percent
                    progress('download', percent,
                             f'正在下载更新包 {done // 1024}KB / {total // 1024}KB')
            elif progress and done and done // (512 * 1024) != last_percent:
                last_percent = done // (512 * 1024)
                progress('download', base + span // 3,
                         f'正在下载更新包 {done // 1024}KB ...')
    if progress:
        progress('download', base + span, '更新包下载完成')


def _safe_extract(tar, dest):
    """安全解压：拒绝绝对路径、父级穿越与链接文件，避免写入目录之外。"""
    root = os.path.realpath(dest)
    for member in tar.getmembers():
        target = os.path.realpath(os.path.join(dest, member.name))
        if target != root and not target.startswith(root + os.sep):
            continue
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            continue
        if member.isdir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        src = tar.extractfile(member)
        if not src:
            continue
        with src, open(target, 'wb') as out:
            shutil.copyfileobj(src, out)


def _overlay_source(src_root, stamp, progress=None, base=70, span=20):
    """把解压出的源码覆盖到项目目录，保留运行数据并备份被覆盖文件。"""
    report = progress or (lambda *args, **kwargs: None)
    backup_dir = os.path.join(APP_ROOT, 'backups', f'auto_update_{stamp}')
    root_real = os.path.realpath(src_root)
    plan = []
    skipped = 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        # 仅跳过顶层的数据/环境目录，避免误伤 static/dist 之类的子目录
        if os.path.realpath(dirpath) == root_real:
            dirnames[:] = [d for d in dirnames if d not in _SKIP_TOP]
        for name in filenames:
            src = os.path.join(dirpath, name)
            rel = os.path.relpath(src, src_root).replace('\\', '/')
            top = rel.split('/', 1)[0]
            if (top in _SKIP_TOP or name in _SKIP_FILES
                    or name.startswith('.env') or name.startswith('server-')):
                skipped += 1
                continue
            plan.append((src, rel))

    copied = failed = 0
    backup_bytes = 0
    errors = []
    total = len(plan) or 1
    for idx, (src, rel) in enumerate(plan, 1):
        dst = os.path.join(APP_ROOT, *rel.split('/'))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            # 覆盖前备份原文件，便于更新异常时回退
            if os.path.exists(dst) and backup_bytes < _BACKUP_LIMIT:
                backup_path = os.path.join(backup_dir, *rel.split('/'))
                os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                shutil.copy2(dst, backup_path)
                backup_bytes += os.path.getsize(dst)
            shutil.copy2(src, dst)
            if dst.endswith('.sh') and os.name != 'nt':
                os.chmod(dst, 0o755)
            copied += 1
        except Exception as exc:
            failed += 1
            if len(errors) < 10:
                errors.append(f'{rel}: {exc}')
        if idx % 5 == 0 or idx == total:
            report('apply', base + int(span * idx / total),
                   f'正在替换文件 {idx}/{total}')
    return {'copied': copied, 'skipped': skipped, 'failed': failed,
            'errors': errors, 'backup_dir': backup_dir if backup_bytes else ''}


def _git_clone(url, dest, timeout=300):
    """浅克隆仓库到指定目录，返回 HEAD 提交号（公开仓库无需账号）。"""
    exe = shutil.which('git')
    if not exe:
        raise RuntimeError('未安装 git')
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    proc = subprocess.run(
        [exe, 'clone', '--depth', '1', '--single-branch', '--branch', BRANCH, url, dest],
        capture_output=True, text=True, timeout=timeout, env=env)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()
        raise RuntimeError(detail[:300] or 'git clone 失败')
    head = subprocess.run([exe, '-C', dest, 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, timeout=30, env=env)
    return (head.stdout or '').strip()


def _update_via_clone(report, stamp):
    """git 浅克隆后覆盖源码：绕开归档页反爬，Gitee 公开仓库可直接拉取。"""
    temp_dir = tempfile.mkdtemp(prefix='bom3_clone_')
    errors = []
    try:
        for url in (GIT_URL, GITEE_GIT_URL):
            dest = os.path.join(temp_dir, 'repo')
            shutil.rmtree(dest, ignore_errors=True)
            try:
                report('download', 30, f'正在从 {url} 拉取源码...')
                commit = _git_clone(url, dest)
                report('apply', 70, '正在替换源码文件...')
                stats = _overlay_source(dest, stamp, progress=report)
                stats.update({'commit': commit, 'source': url})
                return stats
            except Exception as exc:
                errors.append(f'{url} -> {exc}')
        raise RuntimeError('；'.join(errors[-2:]))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _detect_package(path):
    """识别下载内容格式（gzip/zip）；被登录页或网关拦截时给出可读原因。"""
    try:
        with open(path, 'rb') as f:
            magic = f.read(4)
    except OSError as exc:
        raise RuntimeError(f'读取更新包失败：{exc}')
    if magic[:2] == b'\x1f\x8b':
        return 'gzip'
    if magic[:2] == b'PK':
        return 'zip'
    with open(path, 'rb') as f:
        preview = f.read(160).decode('utf-8', 'ignore').replace('\n', ' ')
    raise RuntimeError('下载内容不是压缩包（可能被登录页/网关拦截）：' + preview[:120])


def _safe_extract_zip(zf, dest):
    """安全解压 zip：拒绝路径穿越。"""
    root = os.path.realpath(dest)
    for info in zf.infolist():
        target = os.path.realpath(os.path.join(dest, info.filename))
        if target != root and not target.startswith(root + os.sep):
            continue
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as src, open(target, 'wb') as out:
            shutil.copyfileobj(src, out)


def _archive_result(before, stats, commit, source):
    """统一整理覆盖更新的结果。"""
    if stats['failed'] and not stats['copied']:
        raise RuntimeError('更新写入失败：' + '；'.join(stats['errors'][:3]))
    msg = f"更新完成：覆盖 {stats['copied']} 个文件"
    if stats['failed']:
        msg += f"，{stats['failed']} 个文件写入失败（可能被占用）"
    return {'updated': True, 'from': before, 'commit': commit,
            'file_count': stats['copied'], 'failed': stats['failed'],
            'errors': stats['errors'], 'backup_dir': stats['backup_dir'],
            'source': source, 'message': msg}


def _update_archive(progress=None):
    report = progress or (lambda *args, **kwargs: None)
    before = _read_state().get('commit', '')
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    failures = []

    # 1) 优先 git 浅克隆：公开仓库无需账号，且不受归档页反爬影响
    if shutil.which('git'):
        try:
            stats = _update_via_clone(report, stamp)
            _write_state(stats.get('commit', ''), stats.get('source', ''))
            return _archive_result(before, stats, stats.get('commit', ''),
                                   stats.get('source', ''))
        except Exception as exc:
            failures.append('git 拉取失败: ' + str(exc))
            report('download', 10, 'git 拉取失败，尝试下载压缩包...')
    else:
        failures.append('服务器未安装 git')

    # 2) 回退 HTTP 压缩包（tar.gz / zip），并校验文件头防止保存到登录页
    temp_dir = tempfile.mkdtemp(prefix='bom3_update_')
    try:
        for url in (GITHUB_ARCHIVE, GITEE_ARCHIVE):
            pkg = os.path.join(temp_dir, 'update.pkg')
            try:
                report('download', 10, '正在下载更新包...')
                _download(url, pkg, progress=report, base=10, span=45)
                kind = _detect_package(pkg)
                report('extract', 58, '正在解压并校验更新包...')
                if kind == 'gzip':
                    with tarfile.open(pkg, 'r:gz') as tar:
                        _safe_extract(tar, temp_dir)
                else:
                    import zipfile
                    with zipfile.ZipFile(pkg) as zf:
                        _safe_extract_zip(zf, temp_dir)
                # 压缩包内通常只有一层仓库目录
                entries = [e for e in os.listdir(temp_dir)
                           if os.path.isdir(os.path.join(temp_dir, e))]
                if len(entries) != 1:
                    raise RuntimeError('更新包结构异常：未找到唯一的源码根目录')
                src_root = os.path.join(temp_dir, entries[0])
                report('apply', 70, '正在替换源码文件...')
                stats = _overlay_source(src_root, stamp, progress=report)
                remote = _fetch_remote_commit() or {}
                commit = remote.get('commit', '')
                _write_state(commit, url)
                return _archive_result(before, stats, commit, url)
            except Exception as exc:
                failures.append(f'{url} -> {exc}')
                try:
                    os.remove(pkg)
                except OSError:
                    pass
        raise RuntimeError('更新包获取失败：' + '；'.join(failures[-3:]))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------- 自动重启
def _launch_argv():
    """复用当前启动命令（如 python app.py），保证重启后运行方式一致。"""
    argv = list(sys.argv) or ['app.py']
    entry = argv[0]
    if not os.path.isabs(entry):
        candidate = os.path.join(APP_ROOT, entry)
        if os.path.exists(candidate):
            argv[0] = candidate
    return argv


def schedule_restart(delay=2.0):
    """安排重启：由独立助手进程等待本进程退出后重新拉起服务。

    开发模式（Werkzeug 热重载）下工作进程退出即可由父进程自动重启，无需助手。
    返回 (是否成功, 提示信息)。
    """
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        threading.Timer(delay, lambda: os._exit(0)).start()
        return True, '开发模式：已交由热重载自动重启'

    helper = os.path.join(APP_ROOT, 'scripts', 'restart_helper.py')
    if not os.path.exists(helper):
        return False, '缺少 scripts/restart_helper.py，请手动执行 bash start.sh'

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        job_path = os.path.join(LOG_DIR, f'restart_job_{int(time.time())}.json')
        job = {
            'pid': os.getpid(),
            'app_dir': APP_ROOT,
            'python': sys.executable,
            'argv': _launch_argv(),
            'log': os.path.join(LOG_DIR, 'update_restart.log'),
        }
        with open(job_path, 'w', encoding='utf-8') as f:
            json.dump(job, f, ensure_ascii=False)

        popen_kwargs = {'cwd': APP_ROOT, 'stdout': subprocess.DEVNULL,
                        'stderr': subprocess.DEVNULL, 'close_fds': True}
        if os.name == 'nt':
            popen_kwargs['creationflags'] = (subprocess.DETACHED_PROCESS
                                             | subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            popen_kwargs['start_new_session'] = True
        subprocess.Popen([sys.executable, helper, job_path], **popen_kwargs)
    except Exception as exc:
        return False, f'重启助手启动失败：{exc}'

    threading.Timer(delay, lambda: os._exit(0)).start()
    return True, f'服务将在约 {int(delay)} 秒后自动重启'


# ---------------------------------------------------------------- 更新任务（异步 + 进度）
def start_update_async():
    """启动后台更新线程并立即返回，前端轮询 /api/about/update/status 获取进度。"""
    with _PROGRESS_LOCK:
        if _PROGRESS.get('running'):
            raise RuntimeError('已有更新任务正在进行，请稍候')
        _PROGRESS.update({
            'running': True, 'stage': 'check', 'percent': 2,
            'message': '校验运行环境...', 'started_at': time.time(),
            'finished_at': 0.0, 'error': '', 'result': None,
        })
    threading.Thread(target=_run_update_task, name='bom3-updater', daemon=True).start()


def _run_update_task():
    """更新线程：执行更新 → 记录历史 → 安排重启（失败时保留错误信息）。"""
    try:
        result = perform_update(progress=_set_progress)
        if result.get('updated'):
            _record_update_history(result)
            _set_progress('restart', 95, '更新完成，正在重启服务...', result=result)
            ok, message = schedule_restart()
            if not ok:
                _set_progress('done', 100, '更新完成，但自动重启失败：' + message,
                              running=False, result=result, error=message)
        else:
            _set_progress('done', 100, result.get('message') or '已是最新版本',
                          running=False, result=result)
    except Exception as exc:
        _set_progress('error', 0, f'更新失败：{exc}', running=False, error=str(exc))


# ---------------------------------------------------------------- 历史记录
def _short(commit):
    return (commit or '')[:8]


def _read_history():
    try:
        with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _record_update_history(result):
    """把本次更新写入本机更新历史（最多保留 20 条）。"""
    _CHANGE_CACHE.update({'ts': 0.0, 'data': None})
    try:
        entry = {
            'time': _now_text(),
            'mode': result.get('mode', ''),
            'mode_label': result.get('mode_label', ''),
            'from': _short(result.get('from', '')),
            'to': _short(result.get('to') or result.get('commit', '')),
            'file_count': result.get('file_count', 0),
            'message': result.get('message', ''),
            'source': result.get('source', ''),
        }
        items = _read_history()
        items.insert(0, entry)
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        with open(HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(items[:20], f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_update_history():
    """本机执行过的更新记录（新→旧）。"""
    return _read_history()


def _fetch_remote_commits(limit=30):
    """通过 API 获取远端提交列表（GitHub 不通回退 Gitee）。"""
    headers = {'User-Agent': 'BOM3-Updater', 'Accept': 'application/json'}
    apis = [
        (f'https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
         f'/commits?sha={BRANCH}&per_page={limit}', 'GitHub'),
        (f'https://gitee.com/api/v5/repos/{GITHUB_OWNER}/{GITHUB_REPO}'
         f'/commits?sha={BRANCH}&per_page={limit}', 'Gitee'),
    ]
    for api, label in apis:
        try:
            req = Request(api, headers=headers)
            with urlopen(req, timeout=10) as resp:
                rows = json.loads(resp.read().decode('utf-8'))
            commits = []
            for row in rows if isinstance(rows, list) else []:
                commit = row.get('commit') or {}
                message = (commit.get('message') or '').splitlines()
                commits.append({
                    'short': (row.get('sha') or '')[:8],
                    'date': (commit.get('author') or {}).get('date', '')[:19].replace('T', ' '),
                    'message': message[0] if message else '',
                })
            if commits:
                return commits, label
        except Exception:
            continue
    return [], ''


def get_change_history(limit=30):
    """变更修复历史：优先本地 Git 日志，部署包模式回退远端 API。"""
    mode = _update_mode()
    if mode == 'git':
        try:
            raw = _git(['log', f'-{limit}', '--pretty=format:%h|%ad|%s',
                        '--date=format:%Y-%m-%d %H:%M'], timeout=30)
            changes = []
            for line in raw.splitlines():
                parts = line.split('|', 2)
                if len(parts) == 3:
                    changes.append({'short': parts[0], 'date': parts[1], 'message': parts[2]})
            if changes:
                return {'source': 'git', 'source_label': '本地 Git 提交',
                        'changes': changes, 'updates': get_update_history()}
        except Exception:
            pass

    # 远端历史缓存 5 分钟，避免每次打开页面都等待网络超时
    if _CHANGE_CACHE['data'] and time.time() - _CHANGE_CACHE['ts'] < 300:
        data = dict(_CHANGE_CACHE['data'])
        data['updates'] = get_update_history()
        return data

    changes, label = _fetch_remote_commits(limit)
    data = {
        'source': 'api' if changes else 'none',
        'source_label': f'{label} 提交记录' if changes else '',
        'changes': changes,
        'updates': get_update_history(),
        'message': '' if changes else '暂时无法获取远端提交记录（网络不可达）',
    }
    if changes:
        _CHANGE_CACHE.update({'ts': time.time(), 'data': dict(data)})
    return data
