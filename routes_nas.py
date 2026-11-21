# -*- coding: utf-8 -*-
"""NAS 文件搜索蓝图

功能：
    - NAS（群晖 WebDAV）账号配置的读取、保存与连接测试（首次使用向导）。
    - 上传 Excel（需包含「品番」「EO」列）解析出待搜索的品番+EO 组合。
    - 依据品番+EO 在 NAS 指定目录递归搜索 SubList / UsageList 文件。
    - 将搜索结果下载并复用文件管理既有解析逻辑导入 SubList / UsageList 表，
      同时生成 file_info 记录，等效于在「文件管理」页面上传。

设计：复用 routes.py 中的 detect_file_type / parse_file_data / write_file_data
      以及 EO、车种提取函数，避免逻辑重复。
"""
import os
import re
import json
import logging
from datetime import datetime

from flask import (
    Blueprint, request, jsonify, render_template, redirect, url_for,
    flash, session, current_app, send_file, make_response
)

from app_factory import db

from utils.nas import nas_client

logger = logging.getLogger(__name__)

nas_bp = Blueprint('nas', __name__, url_prefix='/nas')

# 权限编码（与 utils/permission_catalog.py 保持一致）
PERM_PAGE = 'can_nas_file_search'
PERM_CONFIG = 'can_nas_file_search_config'
PERM_SEARCH = 'can_nas_file_search_search'
PERM_IMPORT = 'can_nas_file_search_import'


# --------------------------------------------------------------------------
# 权限与登录辅助
# --------------------------------------------------------------------------
def _current_user():
    """获取当前登录用户对象，未登录返回 None。"""
    from models.user_info import UserInfo
    uid = session.get('user_id')
    if not uid:
        return None
    return db.session.get(UserInfo, uid)


def _has_perm(user, perm_code):
    """管理员恒有权限；否则查询 RBAC 有效权限（走统一短 TTL 缓存）。"""
    if user is None:
        return False
    if getattr(user, 'is_admin', False):
        return True
    from utils.permission_utils import get_legacy_permission_row
    perm = get_legacy_permission_row(user.id)
    return bool(perm and getattr(perm, perm_code, False))


def _require_perm(perm_code):
    """校验权限，返回 (user, None) 或 (None, response)。"""
    user = _current_user()
    if user is None:
        return None, (jsonify({'success': False, 'message': '未登录'}), 401)
    if not _has_perm(user, perm_code):
        return None, (jsonify({'success': False, 'message': '权限不足'}), 403)
    return user, None


# --------------------------------------------------------------------------
# 页面
# --------------------------------------------------------------------------
@nas_bp.route('/file_search')
def file_search_page():
    """NAS 文件搜索页面。未登录跳登录；无权限跳 403。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    user = _current_user()
    if not _has_perm(user, PERM_PAGE):
        flash('您没有 NAS 文件搜索页面的访问权限', 'error')
        from routes import forbidden_page
        return forbidden_page('您没有 NAS 文件搜索页面的访问权限')

    cfg = nas_client.load_config()
    configured = cfg is not None
    # 传给前端时不返回明文密码，仅返回是否已配置
    cfg_view = {
        'url': cfg.get('url', '') if cfg else '',
        'dsm_url': cfg.get('dsm_url', '') if cfg else '',
        'username': cfg.get('username', '') if cfg else '',
        'auth_type': cfg.get('auth_type', 'basic') if cfg else 'basic',
        'root': cfg.get('root', '/') if cfg else '/',
        'roots': cfg.get('roots', ['/']) if cfg else ['/'],
        'verify_ssl': cfg.get('verify_ssl', True) if cfg else True,
        'has_password': bool(cfg and cfg.get('password')),
    }

    resp = make_response(render_template(
        'nas_file_search.html',
        nas_config=cfg_view,
        nas_configured=configured,
        can_config=_has_perm(user, PERM_CONFIG),
        can_search=_has_perm(user, PERM_SEARCH),
        can_import=_has_perm(user, PERM_IMPORT),
    ))
    # 页面含交互逻辑，禁用缓存避免浏览器执行旧版本脚本
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    return resp


# --------------------------------------------------------------------------
# 搜索清单模板下载
# --------------------------------------------------------------------------
@nas_bp.route('/download_template')
def download_template():
    """下载搜索清单 Excel 模板（含「品番」「EO」两列表头）。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    user = _current_user()
    if not _has_perm(user, PERM_SEARCH):
        return jsonify({'success': False, 'message': '权限不足'}), 403

    import io
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = '搜索清单'
    ws.append(['品番', 'EO'])
    # 示例行，供使用者参考格式
    ws.append(['91800-XB010AG', 'D1'])
    stream = io.BytesIO()
    wb.save(stream)
    stream.seek(0)
    return send_file(
        stream,
        as_attachment=True,
        download_name='NAS搜索清单模板.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )


# --------------------------------------------------------------------------
# 配置接口
# --------------------------------------------------------------------------
@nas_bp.route('/api/config', methods=['GET'])
def api_get_config():
    """读取当前 NAS 配置（不含明文密码）。"""
    user, err = _require_perm(PERM_PAGE)
    if err:
        return err
    cfg = nas_client.load_config()
    if not cfg:
        return jsonify({'success': True, 'configured': False, 'config': None})
    return jsonify({
        'success': True,
        'configured': True,
        'config': {
            'url': cfg.get('url', ''),
            'dsm_url': cfg.get('dsm_url', ''),
            'username': cfg.get('username', ''),
            'auth_type': cfg.get('auth_type', 'basic'),
            'root': cfg.get('root', '/'),
            'roots': cfg.get('roots', ['/']),
            'verify_ssl': cfg.get('verify_ssl', True),
            'has_password': bool(cfg.get('password')),
        },
    })


@nas_bp.route('/api/list_dirs', methods=['POST'])
def api_list_dirs():
    """浏览 NAS 目录：列出指定路径下的子文件夹，供前端手动选择搜索目录。

    请求体可携带临时连接参数（首次配置未保存时）或省略（使用已保存配置）。
    """
    user, err = _require_perm(PERM_CONFIG)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    path = data.get('path') or '/'

    # 带连接参数时按临时配置连接（配置向导尚未保存的场景）
    cfg = None
    if data.get('url') and data.get('username'):
        password = data.get('password') or ''
        if not password:
            old = nas_client.load_config()
            password = old.get('password', '') if old else ''
        cfg = {
            'url': (data.get('url') or '').strip().rstrip('/'),
            'username': (data.get('username') or '').strip(),
            'password': password,
            'auth_type': data.get('auth_type') or 'basic',
            'verify_ssl': bool(data.get('verify_ssl', True)),
        }

    try:
        dirs, cur = nas_client.list_dirs(cfg, path)
        # 计算上级路径（根目录时为 None）
        parent = None
        if cur and cur != '/':
            idx = cur.rfind('/')
            parent = cur[:idx] if idx > 0 else '/'
        return jsonify({
            'success': True,
            'path': cur,
            'parent': parent,
            'dirs': dirs,
        })
    except Exception as e:
        logger.warning('浏览 NAS 目录失败: %s', e)
        return jsonify({'success': False, 'message': str(e)})


@nas_bp.route('/api/config', methods=['POST'])
def api_save_config():
    """保存 NAS 配置。password 为空时保留旧密码。"""
    user, err = _require_perm(PERM_CONFIG)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    url = (data.get('url') or '').strip()
    dsm_url = (data.get('dsm_url') or '').strip()
    username = (data.get('username') or '').strip()
    password = data.get('password') or ''
    auth_type = data.get('auth_type') or 'basic'
    root = data.get('root') or '/'
    roots = data.get('roots')
    verify_ssl = data.get('verify_ssl', True)

    if not url or not username:
        return jsonify({'success': False, 'message': '请填写 NAS 地址与账号'})
    if not url.lower().startswith(('http://', 'https://')):
        return jsonify({'success': False, 'message': 'NAS 地址需以 http:// 或 https:// 开头'})

    norm_roots = nas_client.normalize_roots(roots if roots else root)
    if not norm_roots:
        return jsonify({'success': False, 'message': '请至少选择一个搜索目录'})

    old = nas_client.load_config()
    if not password and not (old and old.get('password')):
        return jsonify({'success': False, 'message': '请填写密码'})

    nas_client.save_config(url, username, password, auth_type, root,
                           verify_ssl, roots=norm_roots, dsm_url=dsm_url)
    logger.info('NAS 配置已保存，用户=%s，目录数=%d，索引搜索=%s',
                user.username, len(norm_roots), '开' if dsm_url else '关')
    return jsonify({'success': True, 'message': '配置已保存', 'roots': norm_roots})


@nas_bp.route('/api/test', methods=['POST'])
def api_test_connection():
    """测试 NAS 连接；若请求体带参数则先按临时参数测试，否则测试已保存配置。"""
    user, err = _require_perm(PERM_CONFIG)
    if err:
        return err
    data = request.get_json(silent=True) or {}
    cfg = None
    roots = data.get('roots')
    if data.get('url') and data.get('username'):
        password = data.get('password') or ''
        if not password:
            old = nas_client.load_config()
            password = old.get('password', '') if old else ''
        norm_roots = nas_client.normalize_roots(roots if roots else (data.get('root') or '/'))
        cfg = {
            'url': (data.get('url') or '').strip().rstrip('/'),
            'username': (data.get('username') or '').strip(),
            'password': password,
            'auth_type': data.get('auth_type') or 'basic',
            'root': norm_roots[0],
            'roots': norm_roots,
            'verify_ssl': bool(data.get('verify_ssl', True)),
        }
    ok, msg = nas_client.test_connection(cfg)
    return jsonify({'success': ok, 'message': msg})


# --------------------------------------------------------------------------
# Excel 解析：提取「品番」「EO」列
# --------------------------------------------------------------------------
# 品番列可能的表头关键字
_SUBPNO_HEADERS = ('品番', 'subpno', 'sub_pno', 'sub-pno', 'sub pno', 'partno', 'part_no', 'pno', 'parts no')
# EO 列可能的表头关键字
_EO_HEADERS = ('eo', 'eoksc', 'eo_ksc', 'eoksc', 'e/o')


def _read_excel_rows(file_storage):
    """读取上传 Excel 的表头与数据行，返回 (headers, rows)。

    支持 .xlsx（openpyxl）与 .xls（xlrd）。rows 为二维值列表。
    """
    filename = (file_storage.filename or '').lower()
    ext = os.path.splitext(filename)[1]
    data = file_storage.read()
    if not data:
        raise ValueError('文件内容为空')

    import io
    stream = io.BytesIO(data)

    if ext == '.xlsx':
        from openpyxl import load_workbook
        wb = load_workbook(stream, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows_iter = ws.iter_rows(values_only=True)
            headers = None
            rows = []
            for row in rows_iter:
                if headers is None:
                    headers = [str(h).strip() if h is not None else '' for h in row]
                    continue
                rows.append(list(row))
            return headers or [], rows
        finally:
            wb.close()
    elif ext == '.xls':
        import xlrd
        book = xlrd.open_workbook(file_contents=data)
        sheet = book.sheet_by_index(0)
        if sheet.nrows == 0:
            return [], []
        headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
        rows = []
        for r in range(1, sheet.nrows):
            rows.append([sheet.cell_value(r, c) for c in range(sheet.ncols)])
        return headers, rows
    else:
        raise ValueError('仅支持 .xls / .xlsx 文件')


def _match_col(headers, keys):
    """按表头关键字匹配列索引；精确匹配优先，其次包含匹配。找不到返回 None。"""
    lowered = [h.lower().strip() for h in headers]
    # 1) 精确匹配
    for i, h in enumerate(lowered):
        if h in keys:
            return i
    # 2) 包含匹配
    for i, h in enumerate(lowered):
        for k in keys:
            if k in h:
                return i
    return None


def _cell_str(value):
    """把单元格值规范为去空白的字符串（整数型浮点去掉 .0）。"""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def parse_search_excel(file_storage):
    """解析上传 Excel，提取 (品番, EO) 组合列表。

    返回 (items, errors)：items 为 [{'subpno','eo'}]（已去重），errors 为问题行说明。
    """
    headers, rows = _read_excel_rows(file_storage)
    if not headers:
        raise ValueError('未读取到表头，请检查文件格式')

    subpno_idx = _match_col(headers, _SUBPNO_HEADERS)
    eo_idx = _match_col(headers, _EO_HEADERS)
    if subpno_idx is None or eo_idx is None:
        missing = []
        if subpno_idx is None:
            missing.append('品番（SubPno）')
        if eo_idx is None:
            missing.append('EO')
        raise ValueError('上传文件缺少必需列：' + '、'.join(missing))

    items = []
    seen = set()
    errors = []
    for r_i, row in enumerate(rows, start=2):
        subpno = _cell_str(row[subpno_idx]) if subpno_idx < len(row) else ''
        eo = _cell_str(row[eo_idx]) if eo_idx < len(row) else ''
        if not subpno and not eo:
            continue  # 跳过空行
        if not subpno or not eo:
            errors.append(f'第 {r_i} 行：品番或 EO 为空，已跳过')
            continue
        key = (subpno, eo)
        if key in seen:
            continue
        seen.add(key)
        items.append({'subpno': subpno, 'eo': eo})

    if not items:
        raise ValueError('未从文件中解析到有效的品番+EO 数据')
    return items, errors


# --------------------------------------------------------------------------
# 搜索接口
# --------------------------------------------------------------------------
@nas_bp.route('/api/parse_excel', methods=['POST'])
def api_parse_excel():
    """解析上传的 Excel，返回品番+EO 列表。"""
    user, err = _require_perm(PERM_SEARCH)
    if err:
        return err
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'message': '请选择要上传的 Excel 文件'})
    if not file.filename.lower().endswith(('.xls', '.xlsx')):
        return jsonify({'success': False, 'message': '仅支持 .xls / .xlsx 文件'})
    try:
        items, errors = parse_search_excel(file)
        return jsonify({
            'success': True,
            'count': len(items),
            'items': items,
            'errors': errors,
        })
    except ValueError as ve:
        return jsonify({'success': False, 'message': str(ve)})
    except Exception as e:
        logger.exception('解析搜索 Excel 失败')
        return jsonify({'success': False, 'message': f'解析失败：{e}'})


@nas_bp.route('/api/create_manual', methods=['POST'])
def api_create_manual():
    """手动录入品番+EO（无需上传文件）。

    处理逻辑与 Excel 上传（api_parse_excel）保持一致：仅解析并校验品番+EO，
    去重后返回待搜索列表，不写入数据库、不生成文件。
    """
    user, err = _require_perm(PERM_SEARCH)
    if err:
        return err

    data = request.get_json(silent=True) or {}
    # 兼容单条与批量两种入参：{subpno,eo} 或 {rows:[{subpno,eo}]}
    rows = data.get('rows')
    if not isinstance(rows, list) or not rows:
        rows = [data]

    items = []
    seen = set()
    errors = []
    for idx, row in enumerate(rows, 1):
        row = row if isinstance(row, dict) else {}
        subpno = _cell_str(row.get('subpno'))
        eo = _cell_str(row.get('eo'))
        if not subpno and not eo:
            continue  # 跳过空行
        if not subpno or not eo:
            errors.append(f'第 {idx} 行：品番或 EO 为空，已跳过')
            continue
        key = (subpno, eo)
        if key in seen:
            continue  # 同批次去重
        seen.add(key)
        items.append({'subpno': subpno, 'eo': eo})

    if not items:
        return jsonify({'success': False, 'message': '未录入到有效的品番+EO 数据'})

    return jsonify({
        'success': True,
        'count': len(items),
        'items': items,
        'errors': errors,
    })


@nas_bp.route('/api/search', methods=['POST'])
def api_search():
    """依据品番+EO 在 NAS 上搜索 SubList / UsageList 文件。"""
    user, err = _require_perm(PERM_SEARCH)
    if err:
        return err
    cfg = nas_client.load_config()
    if not cfg:
        return jsonify({'success': False, 'message': '尚未配置 NAS，请先完成配置'})

    data = request.get_json(silent=True) or {}
    items = data.get('items') or []
    roots = data.get('roots')
    root = data.get('root')
    mode = data.get('mode') or 'strict'  # strict: 品番+EO 同时命中; loose: 仅品番
    if not items:
        return jsonify({'success': False, 'message': '没有可搜索的品番+EO 数据'})

    # strict：文件名需同时含品番与 EO；loose：仅需含品番
    keywords_by_item = []
    for it in items:
        subpno = str(it.get('subpno') or '').strip()
        eo = str(it.get('eo') or '').strip()
        if not subpno:
            continue
        kws = [subpno] if mode == 'loose' else [subpno, eo]
        keywords_by_item.append({'subpno': subpno, 'eo': eo, 'keywords': [k for k in kws if k]})

    if not keywords_by_item:
        return jsonify({'success': False, 'message': '品番数据为空'})

    # 把所有品番下推给 NAS 层做「任一命中」过滤，只带回相关文件（避免传回十几万条全量）
    match_any = list({e['subpno'].lower() for e in keywords_by_item if e['subpno']})

    try:
        all_files = nas_client.search_files(
            cfg, keywords=[], root=root, roots=roots, match_any=match_any,
            extensions=('sublist', 'usagelist'),
        )
    except Exception as e:
        logger.exception('NAS 搜索失败')
        return jsonify({'success': False, 'message': f'搜索失败：{e}'})

    # 本地按关键字匹配，避免多次 PROPFIND
    # 性能优化（H-7）：预先一次性算好每个文件的 lower() 名称与路径排名，
    # 避免在内层循环里对同一文件重复 .lower() / 重复计算 rank（原 O(n×m) 重复计算）。
    prepared = [{
        'name': f['name'],
        'name_lower': f['name'].lower(),
        'path': f['path'],
        'rank': _path_rank(f['path']),
        'file_type': f.get('file_type'),
        'size_kb': f.get('size_kb', 0),
    } for f in all_files]

    results = []
    used = set()
    for entry in keywords_by_item:
        kws = [k.lower() for k in entry['keywords']]
        candidates = {}
        for f in prepared:
            name = f['name_lower']
            if not all(k in name for k in kws):
                continue
            key = _unique_key(f['name'])
            cur = candidates.get(key)
            if cur is None or f['rank'] < cur['_rank']:
                candidates[key] = {
                    '_rank': f['rank'],
                    'name': f['name'],
                    'path': f['path'],
                    'file_type': f['file_type'],
                    'size_kb': f['size_kb'],
                }
        matched = []
        for key, item in candidates.items():
            if key in used:
                continue
            item.pop('_rank', None)  # 移除内部排序辅助字段，保持对外结构不变
            matched.append(item)
            used.add(key)
        results.append({
            'subpno': entry['subpno'],
            'eo': entry['eo'],
            'files': matched,
            'found': len(matched) > 0,
        })

    total_found = sum(1 for r in results if r['found'])
    return jsonify({
        'success': True,
        'results': results,
        'total_items': len(results),
        'total_found': total_found,
        'total_files': sum(len(r['files']) for r in results),
    })


# --------------------------------------------------------------------------
# 批量打包下载
# --------------------------------------------------------------------------
@nas_bp.route('/api/download_zip', methods=['POST'])
def api_download_zip():
    """将勾选的 NAS 文件打包为 ZIP 下载到本地。"""
    user, err = _require_perm(PERM_SEARCH)
    if err:
        return err

    cfg = nas_client.load_config()
    if not cfg:
        return jsonify({'success': False, 'message': '尚未配置 NAS'})

    data = request.get_json(silent=True) or {}
    files = data.get('files') or []
    if not files:
        return jsonify({'success': False, 'message': '未选择要下载的文件'})

    import io
    import zipfile
    from werkzeug.utils import secure_filename
    # 性能优化（H-7）：打包改为线程池并发预取（限流 4 并发），
    # 原实现逐个文件同步 get_bytes(timeout=120)，多文件时耗时线性累加；
    # 并发后整体耗时随并发数近似下降，同时用有界线程池避免打爆 NAS/内存。
    from concurrent.futures import ThreadPoolExecutor

    buf = io.BytesIO()
    ok, failed, used = 0, [], set()

    # 先并发拉取所有文件字节（仅对信息完整的文件）
    fetch_targets = []
    for f in files:
        remote_path = (f.get('path') or '').strip()
        raw_name = (f.get('name') or '').strip()
        if not remote_path or not raw_name:
            failed.append({'name': raw_name or '未知文件', 'reason': '文件信息不完整'})
            continue
        fetch_targets.append((f, remote_path, raw_name))

    def _fetch(target):
        """下载单个文件字节，返回 (target, bytes) 或 (target, None)。"""
        f, remote_path, raw_name = target
        try:
            return target, nas_client.get_bytes(cfg, remote_path, timeout=120).read()
        except Exception as e:
            logger.warning('打包下载失败 %s: %s', remote_path, e)
            return target, None

    fetched = {}
    if fetch_targets:
        with ThreadPoolExecutor(max_workers=4) as pool:
            for target, data_bytes in pool.map(_fetch, fetch_targets):
                fetched[id(target)] = data_bytes

    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for target in fetch_targets:
            f, remote_path, raw_name = target
            data_bytes = fetched.get(id(target))
            if data_bytes is None:
                failed.append({'name': raw_name, 'reason': '下载失败'})
                continue
            # 同名文件自动加序号，避免 ZIP 内相互覆盖
            arcname = secure_filename(raw_name) or raw_name
            if arcname in used:
                stem, dot, ext = arcname.rpartition('.')
                base = stem if dot else arcname
                suffix = (dot + ext) if dot else ''
                i = 2
                while f'{base}({i}){suffix}' in used:
                    i += 1
                arcname = f'{base}({i}){suffix}'
            used.add(arcname)
            zf.writestr(arcname, data_bytes)
            ok += 1

    if ok == 0:
        return jsonify({'success': False, 'message': '所有文件均下载失败，无法打包',
                        'failed': failed})

    buf.seek(0)
    zip_name = 'NAS文件_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.zip'
    resp = send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=zip_name)
    if failed:
        # 通过响应头告知前端部分文件失败
        resp.headers['X-Failed-Count'] = str(len(failed))
    return resp


# --------------------------------------------------------------------------
# 导入文件管理接口
# --------------------------------------------------------------------------
@nas_bp.route('/api/import', methods=['POST'])
def api_import():
    """下载选中的 NAS 文件并导入到文件管理（解析入库 + 生成 file_info）。"""
    user, err = _require_perm(PERM_IMPORT)
    if err:
        return err
    # 导入会写入主数据，需与上传同等权限
    if not _has_perm(user, 'can_file_management_upload'):
        return jsonify({'success': False, 'message': '缺少文件上传权限'})

    cfg = nas_client.load_config()
    if not cfg:
        return jsonify({'success': False, 'message': '尚未配置 NAS'})

    data = request.get_json(silent=True) or {}
    files = data.get('files') or []
    if not files:
        return jsonify({'success': False, 'message': '未选择要导入的文件'})

    from app_factory import db
    from models.file_info import FileInfo

    # 复用 routes.py 的解析与写入逻辑
    from routes import (
        detect_file_type, parse_file_data, write_file_data,
        extract_eo_from_filename, extract_vehicle_type_from_filename,
        get_upload_dir, get_upload_category,
    )

    os.makedirs(get_upload_dir(), exist_ok=True)  # 确保上传根目录存在

    success, duplicates, failed = [], [], []
    user_id = user.id
    tmp_paths = []

    for f in files:
        remote_path = (f.get('path') or '').strip()
        raw_name = (f.get('name') or '').strip()
        if not remote_path or not raw_name:
            failed.append({'name': raw_name or '未知文件', 'reason': '文件信息不完整'})
            continue

        file_type = detect_file_type(raw_name)
        if not file_type:
            failed.append({'name': raw_name, 'reason': '无法识别文件类型（需含 SubList/UsageList）'})
            continue

        # 复用 secure_filename 逻辑，保持与页面上传一致的文件命名
        from werkzeug.utils import secure_filename
        secure_name = secure_filename(raw_name)
        # 按文件类型放入对应子目录（SubList / UsageList）
        dest_path = os.path.join(get_upload_dir(get_upload_category(raw_name, file_type)), secure_name)

        # 已存在文件（磁盘 + 数据库）视为重复
        if FileInfo.query.filter_by(filename=secure_name).first():
            duplicates.append({'name': raw_name, 'reason': '文件已存在'})
            continue

        try:
            size = nas_client.download_file(cfg, remote_path, dest_path)
            tmp_paths.append(dest_path)
        except Exception as e:
            failed.append({'name': raw_name, 'reason': f'下载失败：{e}'})
            continue

        try:
            records, _ = parse_file_data(dest_path, file_type)
            sp = _cell_str(records[0].get('SubPno')) or None if records else None
            eo = _cell_str(records[0].get('EoKsc')) or None if records else None

            # 内容首行判重（品番+EO）
            if sp and eo:
                from models.sub_list import SubList
                from models.usage_list import UsageList
                model = SubList if file_type == 'SubList' else UsageList
                existed = db.session.query(model.id).filter(
                    model.SubPno == sp, model.EoKsc == eo
                ).first()
                if existed:
                    duplicates.append({'name': raw_name, 'reason': '首行品番+EO 与已存在数据相同'})
                    _safe_remove(dest_path)
                    continue

            write_file_data(file_type, records, secure_name)
            eo_part = eo or extract_eo_from_filename(secure_name)
            vehicle_type_part = extract_vehicle_type_from_filename(secure_name)
            db.session.add(FileInfo(
                filename=secure_name,
                file_type=file_type,
                upload_time=datetime.now(),
                filesize=size,
                eo=eo_part,
                vehicle_type=vehicle_type_part,
                first_subpno=sp,
                category=file_type,
                created_by=user_id,
                updated_by=user_id,
                # 操作人姓名快照：账号删除后记录仍可显示文本
                created_by_name=session.get('username'),
                updated_by_name=session.get('username'),
            ))
            db.session.commit()
            db.session.expire_all()
            success.append(raw_name)
        except Exception as e:
            db.session.rollback()
            _safe_remove(dest_path)
            logger.exception('导入 NAS 文件失败: %s', raw_name)
            failed.append({'name': raw_name, 'reason': str(e)})

    # 导入成功后失效列表缓存
    if success:
        try:
            from query_cache import query_cache_manager
            query_cache_manager.invalidate_list_cache()
        except Exception:
            pass

    return jsonify({
        'success': True,
        'success_count': len(success),
        'duplicate_count': len(duplicates),
        'failed_count': len(failed),
        'success_files': success,
        'duplicates': duplicates,
        'failed': failed,
    })


def _safe_remove(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


# 临时/同步目录关键字：去重时优先级最低
_TEMP_DIR_HINTS = ('京信图纸自动下载', 'zack维护nas', 'mes data', '临时', 'temp', 'tmp')


def _unique_key(name):
    """去重键：忽略大小写与首尾空白。"""
    return (name or '').strip().lower()


def _path_rank(path):
    """路径优先级（越小越优先）：临时/同步目录排后，正式目录按层级浅、路径短优先。"""
    low = (path or '').lower()
    temp = 1 if any(h in low for h in _TEMP_DIR_HINTS) else 0
    return (temp, low.count('/'), len(low))
