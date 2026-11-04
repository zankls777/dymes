# security/bruteforce_guard.py
"""
登录暴力破解防护（进程内、零依赖）。

设计目标：
- 按「客户端 IP + 用户名」维度计数，连续失败达到阈值后锁定一段时间；
- 锁定期间该标识的所有登录请求直接被拒（HTTP 429），无需打到数据库；
- 任意一次成功登录立即清零该标识的失败计数与锁定状态；
- 进程内 dict 实现，单实例（内网直连/单 waitress 进程）场景足够。
  若未来做多实例横向扩展，把 _store 替换为 Redis 即可，接口不变。

为什么按 IP+用户名而非仅 IP：
- 仅按 IP 会被「对同一用户名只试 1 次就换 IP」的慢速爆破绕过；
- 仅按用户名会被「对大量用户名各试几次」的用户枚举绕过；
- 二者组合能在「单账号爆破」与「用户名枚举」两条路径上同时设限。
"""

import threading
import time

# ============ 可调参数（参数外置，便于运维调整） ============
# 同一标识在滑动窗口内允许的最大连续失败次数，超出即锁定
MAX_ATTEMPTS = int(__import__('os').environ.get('LOGIN_MAX_ATTEMPTS', '5'))
# 失败计数滑动窗口（秒）：窗口外的旧失败记录会被自然遗忘
ATTEMPT_WINDOW = int(__import__('os').environ.get('LOGIN_ATTEMPT_WINDOW', '300'))  # 5 分钟
# 触发锁定后的封锁时长（秒）
LOCK_DURATION = int(__import__('os').environ.get('LOGIN_LOCK_DURATION', '300'))     # 5 分钟
# 达到该失败比例后前端建议显示验证码（本模块只返回是否需要，验证码由上层决定）
CAPTCHA_AFTER = int(__import__('os').environ.get('LOGIN_CAPTCHA_AFTER', '3'))
# ============================================================

_lock = threading.Lock()

# 数据结构：
# _store[identifier] = {
#     'fails': [(timestamp, count), ...],   # 窗口内的失败时间戳列表
#     'locked_until': float|0,             # 锁定到期时间戳（0 表示未锁定）
# }
_store = {}


def _now():
    return time.time()


def _purge_old(ident_entry):
    """丢弃滑动窗口之外的失败记录，返回剩余失败次数。"""
    cutoff = _now() - ATTEMPT_WINDOW
    fails = [t for t in ident_entry.get('fails', []) if t > cutoff]
    ident_entry['fails'] = fails
    return len(fails)


def check_blocked(identifier):
    """返回 (是否锁定, 剩余锁定秒数)。

    未锁定时剩余秒数为 0；锁定时返回 True 与距解锁的秒数（向上取整）。
    """
    with _lock:
        entry = _store.get(identifier)
        if not entry:
            return False, 0
        if entry.get('locked_until', 0) > _now():
            remaining = int(entry['locked_until'] - _now()) + 1
            return True, max(remaining, 1)
        # 锁定已过期，清理状态
        if entry.get('locked_until', 0):
            entry['locked_until'] = 0
        _purge_old(entry)
        return False, 0


def record_failure(identifier):
    """记录一次失败登录，返回是否需要验证码（达到 CAPTCHA_AFTER）。

    若本次记录使连续失败达到 MAX_ATTEMPTS，则触发锁定。
    """
    with _lock:
        entry = _store.setdefault(identifier, {'fails': [], 'locked_until': 0})
        # 若已处于锁定中，仅续期锁定，不再累加计数（避免无限延长）
        if entry.get('locked_until', 0) > _now():
            entry['locked_until'] = _now() + LOCK_DURATION
            return True
        _purge_old(entry)
        entry['fails'].append(_now())
        count = len(entry['fails'])
        if count >= MAX_ATTEMPTS:
            entry['locked_until'] = _now() + LOCK_DURATION
        return count >= CAPTCHA_AFTER


def record_success(identifier):
    """登录成功：清零该标识的失败计数与锁定状态。"""
    with _lock:
        _store.pop(identifier, None)


def get_remaining_attempts(identifier):
    """返回该标识在锁定前还允许的失败次数（已锁定返回 0）。"""
    with _lock:
        entry = _store.get(identifier)
        if not entry:
            return MAX_ATTEMPTS
        if entry.get('locked_until', 0) > _now():
            return 0
        _purge_old(entry)
        return max(0, MAX_ATTEMPTS - len(entry['fails']))


def requires_captcha(identifier):
    """当前是否应要求验证码（达到 CAPTCHA_AFTER 且未被锁死）。"""
    with _lock:
        entry = _store.get(identifier)
        if not entry:
            return False
        if entry.get('locked_until', 0) > _now():
            return True
        return _purge_old(entry) >= CAPTCHA_AFTER
