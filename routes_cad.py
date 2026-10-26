"""RW 图纸产出：上传 RW 差异文件与 DXF 图纸，自动标记增减回路并导出。

模块职责：
  - 页面渲染
  - RW 差异文件上传（Excel，含 SubList差异 表）
  - DXF 图纸上传（文件名含品番以自动识别目标图）
  - 生成标记图纸（增加=红框，删除=红圈）
  - 下载 / 列表 / 清理

文件按会话批次存于 instance/rw_cad/<batch>/，每次访问页面清空历史、开启新批次，
页面仅展示与保留本次上传的数据。
"""
import os
import re
import shutil
from datetime import datetime

from flask import (
    Blueprint, render_template, request, jsonify, session, send_file,
    current_app, flash, redirect, url_for
)
from werkzeug.utils import secure_filename

cad_bp = Blueprint('cad', __name__)

# 存储根目录（相对项目 instance 目录）
SUBDIR = 'rw_cad'

_DXF_EXT = {'.dxf'}
_XLS_EXT = {'.xlsx', '.xls'}

# 差异表中列出增减回路号的列名
_CIRCUIT_COL = 'Circuit'
_TYPE_COL = '类型'
_PNO_COL = 'P/NO'

# 会话批次：每次打开页面生成新批次，仅保留本批次数据
_BATCH_KEY = 'rw_cad_batch'


def _root():
    """返回 rw_cad 根目录，确保存在。"""
    base = os.path.join(current_app.instance_path, SUBDIR)
    os.makedirs(base, exist_ok=True)
    return base


def _new_batch_id():
    """生成唯一的批次标识（时间戳 + 会话用户）。"""
    return '%s_%s' % (datetime.now().strftime('%Y%m%d_%H%M%S%f')[:19],
                      session.get('user_id', 'anon'))


def _cur_batch():
    """返回当前会话批次标识；不存在时返回 None。"""
    return session.get(_BATCH_KEY)


def _start_batch():
    """开启新批次：清理旧批次目录并记录新批次标识。"""
    _clear_all_batches()
    bid = _new_batch_id()
    session[_BATCH_KEY] = bid
    return bid


def _clear_all_batches():
    """删除 rw_cad 根目录下的所有批次目录（不保留历史）。"""
    root = _root()
    for entry in os.scandir(root):
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry.path, ignore_errors=True)
            elif entry.is_file():
                os.remove(entry.path)
        except OSError:
            continue


def _sub(name):
    """返回当前批次下的子目录，确保存在。

    无有效批次时（如未访问页面直接调用接口）自动创建新批次。
    """
    bid = _cur_batch()
    if not bid:
        bid = _start_batch()
    d = os.path.join(_root(), bid, name)
    os.makedirs(d, exist_ok=True)
    return d


def _safe_name(name):
    """清洗文件名，仅保留安全字符。"""
    name = secure_filename(name)
    return name or 'file'


def _require_login():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    return None


def _require_permission():
    """校验页面访问权限。"""
    if session.get('is_admin'):
        return None
    from utils.permission_utils import get_legacy_permission_row
    perm = get_legacy_permission_row(session['user_id'])
    if not perm or not perm.can_rw_comparison:
        return jsonify({'success': False, 'error': '无权限'}), 403
    return None


def _guard():
    return _require_login() or _require_permission()


def _no_cache(resp):
    """禁止浏览器/代理缓存，确保每次访问均取最新记录。"""
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ---------------------------------------------------------------- 差异解析

def _norm_circuit(value):
    """回路号归一化：去掉内部去重后缀 "CA103-1" -> "CA103"。"""
    return re.sub(r'-\d+$', '', str(value or '').strip())


def parse_diff_file(xlsx_path):
    """解析 RW 差异 Excel，返回 {P/NO: {'add': set, 'del': set}}。

    SubList差异 表列：类型(增加/删除)、P/NO、EO、SetCode、Circuit、...
    """
    from openpyxl import load_workbook

    wb = load_workbook(xlsx_path, read_only=True, data_only=True)
    if 'SubList差异' not in wb.sheetnames:
        raise ValueError('文件缺少「SubList差异」工作表')

    ws = wb['SubList差异']
    rows = ws.iter_rows(values_only=True)
    header = next(rows, None)
    if not header:
        raise ValueError('「SubList差异」工作表为空')

    idx = {str(h).strip(): i for i, h in enumerate(header) if h is not None}
    if _CIRCUIT_COL not in idx or _TYPE_COL not in idx or _PNO_COL not in idx:
        raise ValueError('「SubList差异」缺少必需列：类型 / P/NO / Circuit')

    i_type, i_pno, i_circ = idx[_TYPE_COL], idx[_PNO_COL], idx[_CIRCUIT_COL]

    result = {}
    for row in rows:
        if not row:
            continue
        typ = str(row[i_type] or '').strip()
        circ = _norm_circuit(row[i_circ])
        pno = str(row[i_pno] or '').strip()
        if not circ or not pno:
            continue
        bucket = result.setdefault(pno, {'add': set(), 'del': set()})
        if typ.startswith('增加'):
            bucket['add'].add(circ)
        elif typ.startswith('删除'):
            bucket['del'].add(circ)
    wb.close()
    return result


def _merge_diff(target, source):
    """将 source 差异合并入 target（同品番取并集）。"""
    for pno, v in source.items():
        b = target.setdefault(pno, {'add': set(), 'del': set()})
        b['add'] |= v['add']
        b['del'] |= v['del']



# ---------------------------------------------------------------- 页面

@cad_bp.route('/rw_cad')
def rw_cad_page():
    """RW 图纸产出页面。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    if not session.get('is_admin'):
        from utils.permission_utils import get_legacy_permission_row
        perm = get_legacy_permission_row(session['user_id'])
        if not perm or not perm.can_rw_comparison:
            flash('您没有 RW 图纸产出的访问权限', 'error')
            return redirect(url_for('main.index'))

    # 每次访问页面：清空历史数据，开启全新批次
    _start_batch()
    return render_template('rw_cad.html')


# ---------------------------------------------------------------- 差异文件

def _save_uploads(field, exts, kind):
    """保存上传文件到指定子目录，返回 (已保存列表, 错误列表)。

    已保存列表元素：{file_id, filename}；错误列表元素：{filename, error}。
    """
    files = request.files.getlist(field)
    saved, errors = [], []
    for f in files:
        if not f or not f.filename:
            continue
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in exts:
            errors.append({'filename': f.filename, 'error': '文件类型不支持'})
            continue
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S%f')[:19]
        stored = '%s_%s%s' % (_safe_name(os.path.splitext(f.filename)[0]), stamp, ext)
        path = os.path.join(_sub(kind), stored)
        f.save(path)
        saved.append({'file_id': stored, 'filename': f.filename, 'path': path})
    return saved, errors


@cad_bp.route('/api/rw_cad/upload_diff', methods=['POST'])
def api_upload_diff():
    """上传 RW 差异 Excel（可多份，临时保存 7 天），返回各文件解析摘要。"""
    err = _guard()
    if err:
        return err

    if not request.files.getlist('file') and not request.files.getlist('files'):
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    field = 'files' if request.files.getlist('files') else 'file'
    saved, errors = _save_uploads(field, _XLS_EXT, 'diffs')

    results = []
    for item in saved:
        try:
            diff = parse_diff_file(item['path'])
        except Exception as e:
            os.remove(item['path'])
            errors.append({'filename': item['filename'], 'error': '解析失败: %s' % e})
            continue
        if not diff:
            os.remove(item['path'])
            errors.append({'filename': item['filename'], 'error': '未找到增减回路数据'})
            continue
        summary = [{'pno': pno, 'add': len(v['add']), 'del': len(v['del'])}
                   for pno, v in sorted(diff.items())]
        results.append({'file_id': item['file_id'], 'filename': item['filename'], 'summary': summary})

    if not results and errors:
        return jsonify({'success': False, 'error': errors[0]['error'], 'errors': errors}), 400

    return jsonify({'success': True, 'results': results, 'errors': errors})


# ---------------------------------------------------------------- 图纸

@cad_bp.route('/api/rw_cad/upload', methods=['POST'])
def api_upload():
    """上传 DXF 图纸（可多份，临时保存 7 天）。"""
    err = _guard()
    if err:
        return err

    if not request.files.getlist('file') and not request.files.getlist('files'):
        return jsonify({'success': False, 'error': '未选择文件'}), 400

    field = 'files' if request.files.getlist('files') else 'file'
    saved, errors = _save_uploads(field, _DXF_EXT, 'uploads')

    results = []
    for item in saved:
        # 简单校验是否为 DXF（文本含 SECTION）
        try:
            with open(item['path'], 'r', encoding='utf-8', errors='ignore') as fh:
                head = fh.read(4096)
        except OSError:
            errors.append({'filename': item['filename'], 'error': '读取失败'})
            continue
        if 'SECTION' not in head.upper():
            os.remove(item['path'])
            errors.append({'filename': item['filename'], 'error': '不是有效的 DXF'})
            continue
        results.append({
            'file_id': item['file_id'],
            'filename': item['filename'],
            'display': os.path.splitext(item['filename'])[0],
        })

    if not results and errors:
        return jsonify({'success': False, 'error': errors[0]['error'], 'errors': errors}), 400

    return jsonify({'success': True, 'results': results, 'errors': errors})


# ---------------------------------------------------------------- 生成

def _detect_pno(dxf_name, pnos):
    """从图纸文件名中识别所属品番。pnos 为差异文件中的品番集合。

    优先完全包含匹配，取最长命中；无命中返回 None。
    """
    name = os.path.splitext(dxf_name)[0].upper()
    hit = [p for p in pnos if p.upper() in name]
    if not hit:
        return None
    return max(hit, key=len)


@cad_bp.route('/api/rw_cad/generate', methods=['POST'])
def api_generate():
    """按 RW 差异批量标记 DXF 图纸。

    请求体：diff_ids=[...], file_ids=[...]
    多份差异文件合并（同品番取并集）；每份图纸按文件名自动识别品番：
      - 命中基准品番 → 只标删除（红圈）
      - 命中 RW 后品番 → 只标增加（红框）
    """
    err = _guard()
    if err:
        return err

    data = request.get_json(silent=True) or {}
    diff_ids = data.get('diff_ids') or ([data['diff_id']] if data.get('diff_id') else [])
    file_ids = data.get('file_ids') or ([data['file_id']] if data.get('file_id') else [])
    diff_ids = [str(x).strip() for x in diff_ids if str(x).strip()]
    file_ids = [str(x).strip() for x in file_ids if str(x).strip()]

    if not diff_ids or not file_ids:
        return jsonify({'success': False, 'error': '请先上传并选择 RW 差异文件与图纸'}), 400
    for fid in diff_ids + file_ids:
        if os.path.basename(fid) != fid:
            return jsonify({'success': False, 'error': '非法文件名'}), 400

    # 合并多份差异
    diff = {}
    for did in diff_ids:
        p = os.path.join(_sub('diffs'), did)
        if not os.path.isfile(p):
            return jsonify({'success': False, 'error': '差异文件不存在或已过期：%s' % did}), 404
        try:
            _merge_diff(diff, parse_diff_file(p))
        except Exception as e:
            current_app.logger.error('RW 图纸产出-差异解析失败: %s' % e, exc_info=True)
            return jsonify({'success': False, 'error': '差异解析失败: %s' % e}), 500

    if not diff:
        return jsonify({'success': False, 'error': '差异文件中未找到增减回路数据'}), 400

    try:
        from utils.cad import mark_circuits
    except ImportError as e:
        current_app.logger.error('RW 图纸产出-缺少依赖: %s' % e, exc_info=True)
        return jsonify({'success': False, 'error': '服务器缺少 DXF 处理依赖（ezdxf），请联系管理员安装'}), 500

    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    ok, failed = [], []
    for fid in file_ids:
        try:
            src = os.path.join(_sub('uploads'), fid)
            if not os.path.isfile(src):
                failed.append({'file_id': fid, 'error': '图纸不存在或已过期'})
                continue
            pno = _detect_pno(fid, set(diff.keys()))
            if not pno:
                failed.append({'file_id': fid,
                               'error': '无法识别品番（差异含：%s）' % '、'.join(sorted(diff.keys()))})
                continue
            entry = diff[pno]
            out_name = '%s_RW标记_%s.dxf' % (_safe_name(pno), stamp)
            out_path = os.path.join(_sub('output'), out_name)
            stat = mark_circuits(src, sorted(entry['add']), sorted(entry['del']), out_path)
        except Exception as e:
            current_app.logger.error('RW 图纸产出-标记失败 %s: %s' % (fid, e), exc_info=True)
            failed.append({'file_id': fid, 'error': '标记失败: %s' % e})
            continue
        ok.append({
            'file_id': out_name,
            'filename': out_name,
            'source': fid,
            'pno': pno,
            'add_circuits': stat['add_circuits'],
            'add_marks': stat['add_marks'],
            'del_circuits': stat['del_circuits'],
            'del_marks': stat['del_marks'],
            'add_missing': stat['add_missing'],
            'del_missing': stat['del_missing'],
        })

    if not ok:
        return jsonify({'success': False, 'error': failed[0]['error'] if failed else '生成失败', 'failed': failed}), 400
    return jsonify({'success': True, 'results': ok, 'failed': failed})


# ---------------------------------------------------------------- 下载 / 列表

@cad_bp.route('/api/rw_cad/download/<path:file_id>')
def api_download(file_id):
    """下载生成的标记图纸。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if os.path.basename(file_id) != file_id:
        return jsonify({'success': False, 'error': '非法文件名'}), 400

    path = os.path.join(_sub('output'), file_id)
    if not os.path.isfile(path):
        return jsonify({'success': False, 'error': '文件不存在或已过期'}), 404

    # conditional=True 支持断点续传/Range，max_age=0 避免缓存旧文件
    resp = send_file(path, as_attachment=True, download_name=file_id,
                     conditional=True, max_age=0)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@cad_bp.route('/api/rw_cad/list')
def api_list():
    """列出当前会话批次的上传文件（差异文件与图纸），不包含历史数据。"""
    err = _guard()
    if err:
        return err

    def _items(kind):
        d = _sub(kind)
        out = []
        for name in sorted(os.listdir(d), reverse=True):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                out.append({
                    'file_id': name,
                    'filename': name,
                    'mtime': datetime.fromtimestamp(os.path.getmtime(p))
                                     .strftime('%Y-%m-%d %H:%M'),
                })
        return out

    return _no_cache(jsonify({
        'success': True,
        'diffs': _items('diffs'),
        'files': _items('uploads'),
    }))


@cad_bp.route('/api/rw_cad/delete', methods=['POST'])
def api_delete():
    """删除上传的差异文件或图纸。kind: diffs | uploads。"""
    err = _guard()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    file_id = (data.get('file_id') or '').strip()
    kind = (data.get('kind') or 'uploads').strip()
    if kind not in ('diffs', 'uploads'):
        return jsonify({'success': False, 'error': '非法类型'}), 400
    if not file_id or os.path.basename(file_id) != file_id:
        return jsonify({'success': False, 'error': '非法文件名'}), 400

    path = os.path.join(_sub(kind), file_id)
    if os.path.isfile(path):
        os.remove(path)
    return jsonify({'success': True})
