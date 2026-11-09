from flask import (
    Blueprint, request, jsonify, render_template, redirect, url_for, flash,
    send_from_directory, current_app, Response, session, make_response, send_file,
    abort
)
import traceback
from urllib.parse import urlparse
from utils.permission_utils import check_permission, check_multiple_permissions, invalidate_legacy_permission_cache
from query_cache import query_cache_manager

# 数据库错误处理装饰器
def handle_db_errors(func):
    """数据库操作错误处理装饰器"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            current_app.logger.error(f"数据库操作错误 in {func.__name__}: {str(e)}", exc_info=True)
            current_app.logger.error(f"请求信息: {request.method} {request.url}")
            current_app.logger.error(f"用户ID: {session.get('user_id', 'Unknown')}")
            
            # 回滚数据库事务
            try:
                db.session.rollback()
                current_app.logger.info("数据库事务已回滚")
            except Exception as rollback_error:
                current_app.logger.error(f"数据库回滚失败: {str(rollback_error)}")
            
            # 根据请求类型返回适当的错误响应
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'success': False, 'message': '数据库操作失败，请重试'}), 500
            else:
                flash('数据库操作失败，请重试', 'error')
                return redirect(url_for('main.index'))
    wrapper.__name__ = func.__name__
    return wrapper
try:
    from utils.operation_logger import (
        log_file_upload, log_batch_file_upload, log_file_delete, log_batch_file_delete, log_file_download,
        log_file_compare, log_user_operation, log_vehicle_type_operation,
        log_product_operation, log_login, log_logout, log_operation
    )
except ModuleNotFoundError:
    # 兼容不同部署路径：将项目根目录加入 sys.path 后再尝试导入
    import sys, os
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = current_dir  # routes.py 与 utils 在同一层级
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from utils.operation_logger import (
        log_file_upload, log_batch_file_upload, log_file_delete, log_batch_file_delete, log_file_download,
        log_file_compare, log_user_operation, log_vehicle_type_operation,
        log_product_operation, log_login, log_logout, log_operation
    )
from models.operation_log import OperationType, TargetType
import json
import re
import threading
import time
from datetime import datetime as _dt

# 全局的资材量更新任务状态（进程内存）
_material_update_state = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'updated': 0,
    'error': None,
}

# 日志明细字段清洗，避免分隔符"|"与换行导致列错位
def _log_sanitize(value) -> str:
    try:
        s = '' if value is None else str(value)
        # 替换竖线与换行、回车为单个空格，并压缩多余空白
        s = re.sub(r'[\r\n|]+', ' ', s)
        s = re.sub(r'\s{2,}', ' ', s).strip()
        return s
    except Exception:
        return ''
from werkzeug.utils import secure_filename
from models.file_info import FileInfo
from models.sub_list import SubList
from models.usage_list import UsageList
from models.user_info import UserInfo
from models.permission_info import PermissionInfo
from models.material_inbound import MaterialInboundDaily
from models.inventory_check import InventoryTeam, InventoryCheck
from models.inventory_pno_dict import InventoryPnoDict
from models.defective_login import DefectiveLogin, DEFECTIVE_REASONS
from utils.time_utils import get_utc8_time, format_utc8_time
from models.operation_log import OperationLog
import os
from pathlib import Path
import re
import math
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from dateutil.relativedelta import relativedelta
import io
from werkzeug.datastructures import Headers
import csv
from werkzeug.security import check_password_hash, generate_password_hash

# 登录暴力破解防护（进程内限速 + 账号锁定 + 失败清零）
from security import check_blocked, record_failure, record_success, get_remaining_attempts
# 移除循环导入，在需要时动态导入
from flask_login import current_user,login_required,logout_user,login_user
from functools import wraps
from app_factory import db
from sqlalchemy import func, text

# # 新增SubList下载路由
#
from openpyxl import Workbook

def extract_eo_from_filename(filename):
    """
    从文件名中提取EO部分
    文件名格式如: JA_PE2_MAIN_91056-G6370_d1_UsageList.xls
    EO部分是倒数第二个分段，如 d1, a3, b9 等
    """
    try:
        # 移除文件扩展名
        name_without_ext = filename.rsplit('.', 1)[0]
        
        # 按下划线分割
        parts = name_without_ext.split('_')
        
        # EO部分是倒数第二个分段
        if len(parts) >= 2:
            eo_part = parts[-2]
            return eo_part
        else:
            return None
    except Exception:
        return None

# 从app_factory导入cache
from app_factory import cache

@cache.memoize(timeout=3600)  # 缓存1小时
def extract_vehicle_type_from_filename(filename):
    """
    从文件名中提取车种部分
    文件名格式如: JA_PE2_FRDR_LH_91606-G6030_d1_UsageList.xls
    车种部分是前面的部分，去掉零件号、EO和文件类型，如 JA_PE2_FRDR_LH
    使用Flask-Caching提高性能
    """
    try:
        # 移除文件扩展名
        name_without_ext = filename.rsplit('.', 1)[0]
        
        # 按下划线分割
        parts = name_without_ext.split('_')
        
        # 车种部分是前面的部分，去掉最后三个分段（零件号、EO、文件类型）
        if len(parts) >= 4:
            vehicle_type_parts = parts[:-3]
            return '_'.join(vehicle_type_parts)
        else:
            return None
    except Exception:
        return None
from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
from openpyxl import load_workbook
import tempfile
import traceback
import json
from openpyxl.utils.dataframe import dataframe_to_rows
from openpyxl.utils import get_column_letter
import logging
import hashlib

main_bp = Blueprint('main', __name__)

# 取消会话版本强制下线逻辑

# 豁免 CSRF 保护的函数 
def csrf_exempt_route(func):
    # 动态导入csrf以避免循环导入
    from app_factory import csrf
    csrf.exempt(func)
    return func

# 配置日志
# logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 创建蓝图
main_bp = Blueprint('main', __name__)

# 登录后落地页顺序：按此顺序找用户第一个有权限的页面
# (权限字段, 蓝图端点名, 页面名称)
LANDING_PAGE_ORDER = [
    ('can_index', 'main.index', '首页'),
    ('can_file_management', 'main.file_management', '文件管理'),
    ('can_file_comparison', 'main.file_comparison', '文件对比'),
    ('can_products', 'main.products', '成品管理'),
    ('can_vehicle_types', 'main.vehicle_types', '车种管理'),
    ('can_material_download', 'main.material_download', '资材量下载'),
    ('can_material_calculator', 'main.material_calculator', '资材换算'),
    ('can_invest_tidy', 'main.invest_tidy', '投入计划书'),
    ('can_auto_loading', 'main.auto_loading', '汽车厂装车实际'),
    ('can_corrugated_location', 'main.corrugated_location', '波纹管位置'),
    ('can_wire_lot_login', 'main.wire_lot_login', 'LOT登录'),
    ('can_wire_scan_entry', 'main.wire_scan_entry', '扫描录入'),
    ('can_wire_data_download', 'main.wire_data_download', '数据下载'),
    ('can_material_inbound_daily', 'main.material_inbound_daily', '资材每日入库'),
    ('can_material_inbound', 'main.material_inbound', '资材入库'),
    ('can_inventory_login', 'main.inventory_login', '盘点数据登录'),
    ('can_inventory_check', 'main.inventory_check', '盘点实查'),
    ('can_inventory_summary', 'main.inventory_summary', '数据汇总'),
    ('can_defective_login', 'main.defective_login_page', '不良品登录'),
    ('can_defective_export', 'main.defective_export_page', '不良品清单导出'),
    ('can_data_management', 'main.data_management', '数据管理'),
    ('can_test_sheet', 'main.test_sheet', '测试表'),
    ('can_mes_plan', 'mes_mod.plan_page', '计划登录'),
    ('can_mes_production', 'mes_mod.production_page', '生产入库'),
    ('can_mes_quality', 'mes_mod.quality_page', '品质检查(包装)'),
    ('can_mes_price', 'mes_mod.price_page', '单价登陆'),
    ('can_system_management', 'main.user_management', '用户管理'),
]


def get_landing_endpoint(is_admin, permissions):
    """返回登录后应落地的端点名。

    管理员固定回首页；非管理员按 LANDING_PAGE_ORDER 找到第一个有权限的页面；
    一个页面权限都没有时回首页（首页功能入口按权限隐藏，不会暴露无权限功能）。

    注意：非管理员若拥有首页权限（can_index），也直接落地首页，便于统一从首页
    导航进入各功能模块。
    """
    if is_admin:
        return 'main.index'
    permissions = permissions or {}
    if permissions.get('can_index'):
        return 'main.index'
    for perm_field, endpoint, _name in LANDING_PAGE_ORDER:
        if perm_field == 'can_index':
            continue
        if permissions.get(perm_field):
            return endpoint
    return 'main.index'


def resolve_login_redirect(next_url, is_admin, permissions):
    """根据未登录前访问的原始地址 next_url 计算登录后应跳转的地址。

    规则：
    - next_url 为空或非法（跨站、非本系统路径）时，回退到默认落地页；
    - 目标页面在 LANDING_PAGE_ORDER 中有对应的权限字段时，校验用户是否有权限：
      有权限 → 返回该原始地址；无权限 → 回退到默认落地页（首页/首个有权限页面）。

    接受本系统内地址（以单个 '/' 开头的相对路径，或与当前请求同源的绝对 URL），
    防止开放重定向漏洞。
    """
    default_url = url_for(get_landing_endpoint(is_admin, permissions))
    if not next_url:
        return default_url

    path = next_url.strip()

    # 兼容未登录钩子传入的同源绝对 URL（request.url 形如 http://host/sub_list?...）：
    # 仅当协议为 http/https 且域名端口与当前请求完全一致时，剥离成站内相对路径；
    # 跨域绝对地址一律拒绝
    if '://' in path:
        parsed = urlparse(path)
        current_host = urlparse(request.host_url).netloc
        if parsed.scheme not in ('http', 'https') or parsed.netloc != current_host:
            return default_url
        path = parsed.path or '/'
        # 保留原始查询串与锚点（如 sub_list 的筛选条件），跳回时不丢失上下文
        if parsed.query:
            path += '?' + parsed.query
        if parsed.fragment:
            path += '#' + parsed.fragment

    # 只允许站内相对路径，拒绝 "//evil.com"、"/\evil.com" 等协议相对跨站地址
    if not path.startswith('/') or path.startswith('//') or path.startswith('/\\'):
        return default_url

    # 取路径部分（去掉查询串和锚点）用于权限匹配
    path_only = path.split('?', 1)[0].split('#', 1)[0]

    # 登录/登出页不应作为跳回目标：
    # 跳回 /logout 会立即再次清空会话（表现为"登录成功却仍是未登录"），
    # 跳回 /login 没有意义，统一回退默认落地页
    if path_only in ('/login', '/logout'):
        return default_url

    # 通过端点反查权限字段，逐一比对 URL 前缀
    for perm_field, endpoint, _name in LANDING_PAGE_ORDER:
        try:
            endpoint_path = url_for(endpoint)
        except Exception:
            continue
        # 根路径 '/' 仅允许精确匹配：'/'.rstrip('/') 为空串，任意路径都 startswith('/')，
        # 不特殊处理时 /logout 这类未登记路径会被误判成首页而原样放行（登录死循环根因）
        if endpoint_path == '/':
            matched = (path_only == '/')
        else:
            matched = (path_only == endpoint_path
                       or path_only.startswith(endpoint_path.rstrip('/') + '/'))
        if matched:
            # 管理员或拥有该页面权限时才允许跳回原始地址
            if is_admin or (permissions or {}).get(perm_field):
                return path
            return default_url
    # 未在权限清单中登记的页面，保守回退到默认落地页
    return default_url


def forbidden_page(message='您没有访问该页面的权限'):
    """统一的 403 提示页：已登录但权限不足，避免误跳回登录页。"""
    return render_template('error.html', error_code=403, error_message=message,
                           permissions=session.get('user_permissions') or {}), 403


def get_cached_user_permission(user_id):
    """获取用户权限（带缓存），缓存对象包含全部权限字段。

    注意：同一用户的权限缓存被所有页面共用，因此必须一次性缓存完整的权限
    对象，而不是只缓存当前页面用到的那个字段，否则先访问 A 页面写入的缓存
    会让后访问的 B 页面读不到自己的权限字段而误判为无权限。
    """
    cached = query_cache_manager.get_permission(user_id)
    if cached is not None:
        return cached

    user = db.session.get(UserInfo, user_id)
    if user is None:
        return None

    permission = PermissionInfo.query.filter_by(user_id=user_id).first()
    data = {'is_admin': bool(user.is_admin)}
    for field in PermissionInfo.__table__.columns.keys():
        if field.startswith('can_'):
            data[field] = True if user.is_admin else bool(getattr(permission, field, False))
    query_cache_manager.set_permission(user_id, data)
    return data


# 工具函数：校验成品品番是否在 UsageList 中存在（按 SubPno 匹配）
# 读侧放开：品番级活跃优先，全部版本均冻结时冻结数据兜底（冻结资料可用于成品计算追溯）
def part_exists_in_usagelist(part_number: str) -> bool:
    try:
        rows, _ = fetch_usagelist_for_part(part_number)
        return bool(rows)
    except Exception:
        return False

# 工具函数：获取UsageList中指定品番的EO信息
def get_usage_eo_for_part(part_number: str) -> str:
    """获取UsageList中指定品番的EO信息（品番级活跃优先，全冻结时取冻结版本）"""
    try:
        rows, _ = fetch_usagelist_for_part(part_number)
        for usage_item in rows:
            if usage_item and usage_item.EoKsc:
                return usage_item.EoKsc.strip()
        return None
    except Exception:
        return None


def _clean_eo_value(value):
    """规范化单个EO值：去空白，空值返回 None"""
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def build_usage_eo_map(part_numbers):
    """批量构建 part_number -> set(EO) 映射（品番级活跃优先，全冻结时冻结兜底）。

    与 fetch_usagelist_for_part 保持一致的“活跃优先”语义：
    - 已删除（deleted=True）的 UsageList 行一律排除；
    - 品番存在任意活跃（is_frozen=False）行时，仅取活跃行的 EO；
    - 品番全部版本均冻结时，才使用冻结行的 EO 兜底。
    返回 {part_number: set(EO)}，仅包含 EO 非空的品番。
    """
    from models.usage_list import UsageList
    result = {}
    try:
        part_numbers = [p for p in (part_numbers or []) if p]
        if not part_numbers:
            return result
        rows = db.session.query(UsageList.SubPno, UsageList.EoKsc, UsageList.is_frozen).filter(
            UsageList.SubPno.in_(part_numbers),
            UsageList.deleted == False
        ).all()
        for sub_pno, eo, is_frozen in rows:
            eo_val = _clean_eo_value(eo)
            if not eo_val:
                continue
            key = (str(sub_pno) if sub_pno is not None else None)
            if key is None:
                continue
            entry = result.setdefault(key, {'active': set(), 'frozen': set()})
            entry['frozen' if is_frozen else 'active'].add(eo_val)
        return {
            key: (entry['active'] if entry['active'] else entry['frozen'])
            for key, entry in result.items()
            if (entry['active'] or entry['frozen'])
        }
    except Exception:
        return result


# 读取Excel文件并将所有单元格转为字符串，空值转为空字符串
def read_and_convert_to_string(file):
    """读取Excel文件并将所有单元格转为字符串，空值转为空字符串"""
    # logger.debug(f"开始处理文件: {file.filename}")

    with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
        tmp.write(file.read())
        tmp.close()
        # logger.debug(f"临时文件已保存: {tmp.name}")

        df = None
        try:
            # 使用pandas读取
            # logger.debug("尝试使用pandas读取文件")
            df = pd.read_excel(tmp.name)
            # 核心：将所有单元格转为字符串，空值转为空字符串
            df = df.fillna('').astype(str)
            # 去除字符串两端的空白
            df = df.map(lambda x: x.strip() if isinstance(x, str) else x)
            # logger.debug(f"pandas读取成功，数据形状: {df.shape}")
        except Exception as e:
            logger.warning(f"pandas读取失败，尝试使用openpyxl读取: {str(e)}")
            try:
                # 使用openpyxl读取
                # logger.debug("尝试使用openpyxl读取文件")
                wb = load_workbook(tmp.name)
                sheet = wb.active
                data = []
                headers = None
                for row in sheet.iter_rows(values_only=True):
                    if headers is None:
                        headers = [str(cell).strip() if cell is not None else '' for cell in row]
                    else:
                        data.append([str(cell).strip() if cell is not None else '' for cell in row])
                if headers and data:
                    df = pd.DataFrame(data, columns=headers)
                    # logger.debug(f"openpyxl读取成功，数据形状: {df.shape}")
            except Exception as e2:
                logger.error(f"openpyxl读取也失败: {str(e2)}")
                raise ValueError("无法读取文件，请检查文件格式")

        os.unlink(tmp.name)
        # logger.debug(f"临时文件已删除: {tmp.name}")
        return df


def _read_file_data(file_info) -> pd.DataFrame:
    """从数据库读取文件对应的业务数据，并转换为规范化 DataFrame

    数据源为数据库中已导入的数据（sub_list / usage_list 表），
    不再依赖磁盘上的原始 Excel 文件。

    Args:
        file_info: FileInfo模型实例

    Returns:
        pd.DataFrame: 规范化后的文件数据

    Raises:
        FileNotFoundError: 数据库中不存在该文件的业务数据
        ValueError: 不支持的文件类型
    """
    # 参数校验
    if file_info is None:
        raise ValueError('文件信息为空')

    file_type = (file_info.file_type or '').lower()
    filename = file_info.filename

    # 性能优化：原实现用 ORM 全量加载对象再逐行 to_dict()（大表百万行时内存/CPU 开销大）。
    # 改为仅查询业务列（with_entities，绕过 ORM 实例化与 to_dict 逐字段反射），
    # 并在 DataFrame 上做向量化 strip，避免 df.map 的逐单元格 Python 回调。
    if file_type == 'sublist':
        model, drop_cols = SubList, ['id', 'deleted', 'is_frozen', 'upload_time', 'source_file']
    elif file_type == 'usagelist':
        model, drop_cols = UsageList, ['id', 'deleted', 'is_frozen', 'upload_time', 'source_file']
    else:
        raise ValueError(f'不支持的文件类型: {file_type}')

    # 取业务列（排除数据库特有列），按 id 保持原始行序
    biz_cols = [c for c in model.__table__.columns.keys() if c not in drop_cols]
    columns = [getattr(model, c) for c in biz_cols]

    # 流式化取数：用 stream() 逐块读取（服务端游标），避免一次性 .all() 把百万行元组
    # 全部驻留内存；再按列聚合为 dict-of-lists，最后一次性构建 DataFrame。
    # 内存峰值约为逐块缓冲 + 一次 DataFrame，显著低于「全量 list[tuple] + DataFrame」双份驻留。
    col_values = {name: [] for name in biz_cols}
    has_row = False
    q = (db.session.query(*columns)
         .filter(model.source_file == filename, model.deleted == False)
         .order_by(model.id))
    # 用 session.execute(statement) 走服务端游标，逐行 append 到各列列表（按列聚合）。
    # 相比一次性 .all() 生成百万行元组列表，这里只保留最终列列表，省去中间 list[tuple] 副本。
    result = db.session.execute(
        q.statement.execution_options(yield_per=2000, stream_results=True))
    for row in result:
        has_row = True
        for i, name in enumerate(biz_cols):
            col_values[name].append(row[i])

    if not has_row:
        raise FileNotFoundError(f'文件 {filename} 在数据库中无业务数据记录')

    # 由列数据构建 DataFrame（列名即业务列名），列内已是连续列表，构建更高效
    df = pd.DataFrame(col_values, columns=biz_cols)

    # 向量化清理：空值转字符串并去除首尾空白（列级操作，走 pandas C 层）
    df = df.fillna('').astype(str)
    for col in df.columns:
        df[col] = df[col].str.strip()

    current_app.logger.info(f'从数据库读取文件 {filename}，共 {len(df)} 条记录')
    return df


def _compare_dfs(df1: pd.DataFrame, df2: pd.DataFrame, key_column: str, compare_columns: list):
    if not key_column or key_column not in df1.columns or key_column not in df2.columns:
        raise ValueError('关键列无效')
    if not compare_columns:
        compare_columns = [col for col in df1.columns if col != key_column]

    # 用字符串化后的键建立 键->首次出现位置 映射，避免循环内对整表做布尔掩码扫描（原实现为 O(n²)）
    key1 = df1[key_column].astype(str).tolist()
    key2 = df2[key_column].astype(str).tolist()
    set1 = set(key1)
    set2 = set(key2)

    first1 = {}
    for pos, k in enumerate(key1):
        first1.setdefault(k, pos)
    first2 = {}
    for pos, k in enumerate(key2):
        first2.setdefault(k, pos)

    records1 = df1.to_dict(orient='records')
    records2 = df2.to_dict(orient='records')

    common_ids = set1.intersection(set2)
    modified_rows1 = []
    modified_rows2 = []
    modified_details = []

    for common_id in common_ids:
        row1 = records1[first1[common_id]]
        row2 = records2[first2[common_id]]
        is_modified = False
        differences = []
        for col in compare_columns:
            if col == key_column or col not in df1.columns or col not in df2.columns:
                continue
            v1 = row1[col] if (isinstance(row1[col], str) and row1[col].strip()) else (row1[col] if not isinstance(row1[col], str) else '')
            v2 = row2[col] if (isinstance(row2[col], str) and row2[col].strip()) else (row2[col] if not isinstance(row2[col], str) else '')
            if v1 != v2:
                is_modified = True
                differences.append({'column': col, 'file1_value': row1[col], 'file2_value': row2[col]})
        if is_modified:
            modified_rows1.append(row1)
            modified_rows2.append(row2)
            modified_details.append({key_column: common_id, 'differences': differences})

    modified_df1 = pd.DataFrame(modified_rows1, columns=df1.columns)
    modified_df2 = pd.DataFrame(modified_rows2, columns=df2.columns)

    added_only = df2[[k not in set1 for k in key2]].copy()
    added_df = pd.concat([added_only, modified_df2], ignore_index=True)
    added_df['__diff_type__'] = 'added'

    deleted_only = df1[[k not in set2 for k in key1]].copy()
    deleted_df = pd.concat([deleted_only, modified_df1], ignore_index=True)
    deleted_df['__diff_type__'] = 'deleted'

    result = {
        'headers': list(df1.columns),
        'added': added_df.to_dict(orient='records'),
        'deleted': deleted_df.to_dict(orient='records'),
        'modified_df1': modified_df1.to_dict(orient='records'),
        'modified_df2': modified_df2.to_dict(orient='records'),
        'modified_details': modified_details,
        'compare_columns': compare_columns,
    }

    def _replace_nan(obj):
        if isinstance(obj, dict):
            return {k: _replace_nan(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_replace_nan(v) for v in obj]
        if isinstance(obj, float) and math.isnan(obj):
            return ''
        return obj

    return _replace_nan(result)


@main_bp.route('/api/files', methods=['GET'])
@login_required
def list_uploaded_files():
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        search = request.args.get('search', '', type=str).strip()
        ftype = request.args.get('type', '', type=str).strip()
        eo = request.args.get('eo', '', type=str).strip()
        vehicle_type = request.args.get('vehicle_type', '', type=str).strip()

        query = FileInfo.query.order_by(FileInfo.upload_time.desc())
        if search:
            query = query.filter(FileInfo.filename.ilike(f'%{search}%'))
        if ftype:
            query = query.filter(FileInfo.file_type.ilike(f'%{ftype}%'))
        if eo:
            # EO搜索：直接搜索EO列
            # 支持部分匹配，如搜索"d"能找到"d1"，搜索"1"能找到"d1"
            query = query.filter(FileInfo.eo.ilike(f'%{eo}%'))
        if vehicle_type:
            query = query.filter(FileInfo.vehicle_type.ilike(f'%{vehicle_type}%'))

        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        items = [{
            'id': f.id,
            'filename': f.filename,
            'filesize': f.filesize,
            'upload_time': f.upload_time.strftime('%Y-%m-%d %H:%M:%S') if f.upload_time else None,
            'file_type': f.file_type,
            'eo': f.eo,
            'vehicle_type': f.vehicle_type,
            'is_frozen': bool(f.is_frozen),
            'frozen_reason': f.frozen_reason or '',
        } for f in pagination.items]

        return jsonify({
            'success': True,
            'items': items,
            'pagination': {
                'page': pagination.page,
                'per_page': pagination.per_page,
                'total_pages': pagination.pages,
                'total_items': pagination.total,
            }
        })
    except Exception as e:
        current_app.logger.error(f"列出文件失败: {str(e)}")
        return jsonify({'success': False, 'error': '获取文件列表失败'}), 500


@main_bp.route('/api/files/ids', methods=['GET'])
@login_required
def get_all_file_ids():
    """获取所有文件ID，用于全选功能"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        search = request.args.get('search', '', type=str).strip()
        ftype = request.args.get('type', '', type=str).strip()
        eo = request.args.get('eo', '', type=str).strip()
        vehicle_type = request.args.get('vehicle_type', '', type=str).strip()

        query = FileInfo.query
        if search:
            query = query.filter(FileInfo.filename.ilike(f'%{search}%'))
        if ftype:
            query = query.filter(FileInfo.file_type.ilike(f'%{ftype}%'))
        if eo:
            query = query.filter(FileInfo.eo.ilike(f'%{eo}%'))
        if vehicle_type:
            query = query.filter(FileInfo.vehicle_type.ilike(f'%{vehicle_type}%'))

        # 只获取ID，不获取其他字段，提高性能
        file_ids = [f.id for f in query.with_entities(FileInfo.id).all()]

        return jsonify({
            'success': True,
            'file_ids': file_ids,
            'total_count': len(file_ids)
        })
    except Exception as e:
        current_app.logger.error(f"获取文件ID失败: {str(e)}")
        return jsonify({'success': False, 'error': '获取文件ID失败'}), 500


@main_bp.route('/api/files/all_slim', methods=['GET'])
@csrf_exempt_route
def get_all_files_slim():
    """返回全部文件的精简元数据（id/filename/file_type/is_frozen），用于前端全选与批量删除展示。

    性能优化：原前端调用 list_uploaded_files?per_page=999999 会加载完整 FileInfo 对象与关联用户，
    这里仅投影 4 个必要列，避免冗余字段与关联对象的加载。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        search = request.args.get('search', '', type=str).strip()
        ftype = request.args.get('type', '', type=str).strip()
        eo = request.args.get('eo', '', type=str).strip()
        vehicle_type = request.args.get('vehicle_type', '', type=str).strip()

        query = db.session.query(
            FileInfo.id, FileInfo.filename, FileInfo.file_type, FileInfo.is_frozen
        )
        if search:
            query = query.filter(FileInfo.filename.ilike(f'%{search}%'))
        if ftype:
            query = query.filter(FileInfo.file_type.ilike(f'%{ftype}%'))
        if eo:
            query = query.filter(FileInfo.eo.ilike(f'%{eo}%'))
        if vehicle_type:
            query = query.filter(FileInfo.vehicle_type.ilike(f'%{vehicle_type}%'))

        items = [
            {'id': fid, 'filename': fname, 'file_type': ftype_, 'is_frozen': bool(frozen)}
            for fid, fname, ftype_, frozen in query.all()
        ]
        return jsonify({'success': True, 'items': items, 'total_count': len(items)})
    except Exception as e:
        current_app.logger.error(f"获取文件精简列表失败: {str(e)}")
        return jsonify({'success': False, 'error': '获取文件精简列表失败'}), 500


# ============== 文件冻结/解冻 API ==============

def sync_freeze_status_to_data(filename, is_frozen, user_id=None, reason=None):
    """
    同步文件的冻结状态到关联的数据库数据（SubList 和 UsageList）

    当文件被冻结/解冻时，关联的 SubList 和 UsageList 记录也需同步更新冻结状态，
    确保冻结的文件及其数据无法被查询和调用。

    Args:
        filename: 文件名（FileInfo.filename），用于匹配 source_file 字段
        is_frozen: 目标冻结状态（True=冻结, False=解冻）
        user_id: 操作人ID（可选，用于日志记录）
        reason: 冻结原因（可选，用于日志记录）

    Returns:
        dict: {'sublist_count': 更新的SubList记录数, 'usagelist_count': 更新的UsageList记录数}
    """
    result = {'sublist_count': 0, 'usagelist_count': 0}

    try:
        # 批量 UPDATE：单条 SQL 完成冻结状态同步（利用 idx_sub_list_source_file_status 索引）
        # 从数千次 ORM UPDATE 降为 1 条 SQL，毫秒级完成
        sublist_count = db.session.query(SubList).filter(
            SubList.source_file == filename,
            SubList.is_frozen != is_frozen
        ).update({SubList.is_frozen: is_frozen}, synchronize_session=False)

        usagelist_count = db.session.query(UsageList).filter(
            UsageList.source_file == filename,
            UsageList.is_frozen != is_frozen
        ).update({UsageList.is_frozen: is_frozen}, synchronize_session=False)

        result['sublist_count'] = sublist_count or 0
        result['usagelist_count'] = usagelist_count or 0

        if result['sublist_count'] > 0 or result['usagelist_count'] > 0:
            action = '冻结' if is_frozen else '解冻'
            current_app.logger.info(
                f'同步数据冻结状态: 文件 {filename} {action}，'
                f'SubList {result["sublist_count"]} 条，'
                f'UsageList {result["usagelist_count"]} 条'
            )
    except Exception as e:
        current_app.logger.error(f'同步数据冻结状态失败: {filename}, 错误: {str(e)}', exc_info=True)
        raise

    return result


def sync_freeze_status_to_data_batch(filenames, is_frozen, user_id=None):
    """批量同步多个文件的冻结状态到 SubList/UsageList（M-2）。

    将原先「每个文件一条 UPDATE」合并为单条 `WHERE source_file IN (...)`，
    批量冻结旧版本场景下 UPDATE 往返次数由 N 降为 1，显著降低事务开销。

    Returns:
        dict: {'sublist_count': 更新数, 'usagelist_count': 更新数}
    """
    result = {'sublist_count': 0, 'usagelist_count': 0}
    names = [n for n in (filenames or []) if n]
    if not names:
        return result
    try:
        # 分批 IN 规避 SQLite 变量上限（默认 999），确保大文件集也能安全执行
        chunk = 500
        for i in range(0, len(names), chunk):
            part = names[i:i + chunk]
            result['sublist_count'] += db.session.query(SubList).filter(
                SubList.source_file.in_(part),
                SubList.is_frozen != is_frozen
            ).update({SubList.is_frozen: is_frozen}, synchronize_session=False) or 0
            result['usagelist_count'] += db.session.query(UsageList).filter(
                UsageList.source_file.in_(part),
                UsageList.is_frozen != is_frozen
            ).update({UsageList.is_frozen: is_frozen}, synchronize_session=False) or 0
        current_app.logger.info(
            f'批量同步数据冻结状态: {len(names)} 个文件 {"冻结" if is_frozen else "解冻"}，'
            f'SubList {result["sublist_count"]} 条，UsageList {result["usagelist_count"]} 条'
        )
    except Exception as e:
        current_app.logger.error(f'批量同步数据冻结状态失败: {e}', exc_info=True)
        raise
    return result


def get_frozen_filenames(files):
    """返回文件/记录列表中处于冻结状态的文件名列表（未冻结返回空列表）。"""
    return [getattr(f, 'filename', '') or '' for f in files
            if getattr(f, 'is_frozen', False)]


def assert_files_mutable(files):
    """写操作前冻结校验：冻结文件禁止删除/覆盖等任何写操作，须先解冻。

    Returns:
        list: 冻结状态文件名列表；空列表表示全部可操作。
    """
    return get_frozen_filenames(files)


def _active_priority_rows(rows):
    """读侧「活跃优先 + 冻结兜底」选择器。

    冻结语义：同品番旧 EO 版本整体归档，显式 (品番, EO) 键正常只对应一种状态。
    - 存在活跃行 → 仅返回活跃行（is_frozen=False）
    - 全部为冻结行 → 返回冻结行兜底（is_frozen=True），供业务页面只读追溯
    返回 (rows, is_frozen)。
    """
    if not rows:
        return [], False
    active = [r for r in rows if not getattr(r, 'is_frozen', False)]
    if active:
        return active, False
    return rows, True


def fetch_sublist_by_key(sub_pno, eo=None):
    """显式 (品番[, EO]) 键只读查询 SubList：活跃优先，活跃缺失时冻结兜底。

    返回 (rows, is_frozen)；is_frozen=True 表示命中的是冻结归档数据，
    调用方须在界面上明确标注冻结状态。
    """
    from models.sub_list import SubList
    q = SubList.query.filter(SubList.SubPno == sub_pno, SubList.deleted == False)
    if eo is not None:
        q = q.filter(SubList.EoKsc == eo)
    return _active_priority_rows(q.all())


def fetch_usagelist_by_key(sub_pno, eo=None):
    """显式 (品番[, EO]) 键只读查询 UsageList：活跃优先，活跃缺失时冻结兜底。

    返回 (rows, is_frozen)。
    """
    from models.usage_list import UsageList
    q = UsageList.query.filter(UsageList.SubPno == sub_pno, UsageList.deleted == False)
    if eo is not None:
        q = q.filter(UsageList.EoKsc == eo)
    return _active_priority_rows(q.all())


def prefetch_key_rows(pairs, model):
    """一次性批量预取多组 (品番, EO) 的行数据。

    与逐行调用 fetch_*_by_key 语义一致（活跃优先、冻结兜底），
    但把 N 组 * 4 次查询压缩为每个模型 1 次 IN 查询。

    Args:
        pairs: 可迭代的 (pno, eo) 组合
        model: SubList 或 UsageList 模型类

    Returns:
        dict: {(pno, eo): (rows, is_frozen)}
    """
    keys = {(p, e) for p, e in pairs if p}
    if not keys:
        return {}
    pnos = {p for p, _ in keys}
    eos = {e for _, e in keys}
    rows = model.query.filter(
        model.SubPno.in_(pnos),
        model.EoKsc.in_(eos),
        model.deleted == False
    ).all()

    grouped = {}
    for r in rows:
        grouped.setdefault((r.SubPno, r.EoKsc), []).append(r)

    result = {}
    for pno, eo in keys:
        result[(pno, eo)] = _active_priority_rows(grouped.get((pno, eo), []))
    return result


def fetch_sublist_for_part(sub_pno):
    """品番级只读查询 SubList：该品番存在任意活跃 EO 版本时仅返回活跃行；
    全部版本均冻结时才返回冻结行兜底（避免同品番多版本双计数）。
    返回 (rows, is_frozen)。
    """
    from models.sub_list import SubList
    rows = SubList.query.filter(
        SubList.SubPno == sub_pno, SubList.deleted == False).all()
    return _active_priority_rows(rows)


def fetch_usagelist_for_part(sub_pno):
    """品番级只读查询 UsageList：活跃优先，全冻结才兜底。返回 (rows, is_frozen)。"""
    from models.usage_list import UsageList
    rows = UsageList.query.filter(
        UsageList.SubPno == sub_pno, UsageList.deleted == False).all()
    return _active_priority_rows(rows)


def query_usagelist_active_priority(*filters, part_numbers=None):
    """UsageList 品番级「活跃优先 + 冻结兜底」批量只读查询（用于聚合/批量场景）。

    同品番存在任意活跃 EO 版本时仅统计活跃行（避免新旧版本双计数）；
    品番全部版本均冻结时才纳入冻结行兜底，保证成品计算/计算器等业务页面
    可正常追溯冻结归档资料。
    - filters: 附加业务条件（如 Comp_Kind == 'TAPE'）
    - part_numbers: 限定品番集合（可选，缩小扫描范围）
    """
    from models.usage_list import UsageList
    sq_conds = [UsageList.deleted == False,
                UsageList.is_frozen == False,
                UsageList.SubPno.isnot(None)]
    if part_numbers is not None:
        sq_conds.append(UsageList.SubPno.in_(part_numbers))
    active_parts_sq = (db.session.query(UsageList.SubPno)
                       .filter(*sq_conds).distinct().subquery())
    outer_conds = [
        UsageList.deleted == False,
        db.or_(UsageList.is_frozen == False,
               ~UsageList.SubPno.in_(db.select(active_parts_sq)))
    ]
    outer_conds.extend(filters)
    if part_numbers is not None:
        outer_conds.append(UsageList.SubPno.in_(part_numbers))
    return UsageList.query.filter(*outer_conds).all()


@main_bp.route('/api/files/freeze/<int:file_id>', methods=['POST'])
@login_required
def api_freeze_file(file_id):
    """手动冻结指定文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有冻结文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        from datetime import datetime
        
        file = db.session.get(FileInfo, file_id)
        if not file:
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        
        if file.is_frozen:
            return jsonify({'success': False, 'error': '文件已被冻结'}), 400
        
        data = request.get_json() or {}
        reason = data.get('reason', '手动冻结').strip()
        
        file.is_frozen = True
        file.frozen_at = datetime.utcnow()
        file.frozen_by = user_id
        file.frozen_reason = reason

        # 同步冻结关联的数据库数据（SubList 和 UsageList）
        sync_result = sync_freeze_status_to_data(file.filename, True, user_id, reason)

        db.session.commit()
        # 冻结后立即失效列表/计数缓存，避免 5 分钟内返回旧数据
        query_cache_manager.invalidate_list_cache()

        current_app.logger.info(
            f'手动冻结文件: {file.filename}, 原因: {reason}, '
            f'同步数据: SubList {sync_result["sublist_count"]} 条, '
            f'UsageList {sync_result["usagelist_count"]} 条'
        )
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.FREEZE_FILE, f"冻结文件: {file.filename}（原因: {reason}）",
                       TargetType.FILE, file_id, file.filename)
        except Exception as _le:
            current_app.logger.warning(f"记录冻结文件日志失败: {_le}")
        return jsonify({
            'success': True,
            'message': f'文件 {file.filename} 已冻结',
            'sync_result': sync_result
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'冻结文件失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/thaw/<int:file_id>', methods=['POST'])
@login_required
def api_thaw_file(file_id):
    """解冻指定文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有解冻文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        
        file = db.session.get(FileInfo, file_id)
        if not file:
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        
        if not file.is_frozen:
            return jsonify({'success': False, 'error': '文件未被冻结'}), 400
        
        file.is_frozen = False
        file.frozen_at = None
        file.frozen_by = None
        file.frozen_reason = None
        file.updated_by = user_id

        # 同步解冻关联的数据库数据（SubList 和 UsageList）
        sync_result = sync_freeze_status_to_data(file.filename, False, user_id)

        db.session.commit()
        # 解冻后立即失效列表/计数缓存，避免 5 分钟内返回旧数据
        query_cache_manager.invalidate_list_cache()

        current_app.logger.info(
            f'解冻文件: {file.filename}, '
            f'同步数据: SubList {sync_result["sublist_count"]} 条, '
            f'UsageList {sync_result["usagelist_count"]} 条'
        )
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.THAW_FILE, f"解冻文件: {file.filename}",
                       TargetType.FILE, file_id, file.filename)
        except Exception as _le:
            current_app.logger.warning(f"记录解冻文件日志失败: {_le}")
        return jsonify({
            'success': True,
            'message': f'文件 {file.filename} 已解冻',
            'sync_result': sync_result
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'解冻文件失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/frozen', methods=['GET'])
@login_required
def api_list_frozen_files():
    """获取所有冻结文件列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management:
            return jsonify({'success': False, 'error': '您没有查看文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        
        query = FileInfo.query.filter(FileInfo.is_frozen == True).order_by(FileInfo.frozen_at.desc())
        
        frozen_files = query.paginate(page=page, per_page=per_page, error_out=False)
        
        files_data = []
        for f in frozen_files.items:
            item = f.to_dict()
            item['frozen_by_name'] = f.freezer.username if f.freezer else None
            files_data.append(item)
        
        return jsonify({
            'success': True,
            'files': files_data,
            'total': frozen_files.total,
            'pages': frozen_files.pages,
            'current_page': page
        })
    except Exception as e:
        current_app.logger.error(f'获取冻结文件列表失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/batch_freeze', methods=['POST'])
@login_required
def api_batch_freeze_files():
    """批量冻结文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有批量冻结文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        from datetime import datetime
        
        data = request.get_json() or {}
        file_ids = data.get('file_ids', [])
        reason = data.get('reason', '批量冻结').strip()
        
        if not file_ids:
            return jsonify({'success': False, 'error': '请选择要冻结的文件'}), 400
        
        frozen_count = 0
        total_sync = {'sublist_count': 0, 'usagelist_count': 0}
        # 性能优化：一次性按主键批量取回，避免循环内 FileInfo.query.get() 产生 N+1 查询
        id_list = [int(fid) for fid in file_ids]
        files = FileInfo.query.filter(FileInfo.id.in_(id_list)).all()
        for file in files:
            if file and not file.is_frozen:
                file.is_frozen = True
                file.frozen_at = datetime.utcnow()
                file.frozen_by = user_id
                file.frozen_reason = reason
                frozen_count += 1
                # 同步冻结关联的数据库数据
                sync_result = sync_freeze_status_to_data(file.filename, True, user_id, reason)
                total_sync['sublist_count'] += sync_result['sublist_count']
                total_sync['usagelist_count'] += sync_result['usagelist_count']

        db.session.commit()
        # 批量冻结后立即失效列表/计数缓存
        query_cache_manager.invalidate_list_cache()

        current_app.logger.info(
            f'批量冻结 {frozen_count} 个文件, '
            f'同步数据: SubList {total_sync["sublist_count"]} 条, '
            f'UsageList {total_sync["usagelist_count"]} 条'
        )
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.BATCH_FREEZE_FILE, f"批量冻结文件 {frozen_count} 个（原因: {reason}）",
                       TargetType.FILE, None, f"{frozen_count}个文件")
        except Exception as _le:
            current_app.logger.warning(f"记录批量冻结文件日志失败: {_le}")
        return jsonify({
            'success': True,
            'frozen_count': frozen_count,
            'sync_result': total_sync
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'批量冻结文件失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/batch_thaw', methods=['POST'])
@login_required
def api_batch_thaw_files():
    """批量解冻文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有批量解冻文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        
        data = request.get_json() or {}
        file_ids = data.get('file_ids', [])
        
        if not file_ids:
            return jsonify({'success': False, 'error': '请选择要解冻的文件'}), 400
        
        thawed_count = 0
        total_sync = {'sublist_count': 0, 'usagelist_count': 0}
        # 性能优化：一次性按主键批量取回，避免循环内 FileInfo.query.get() 产生 N+1 查询
        id_list = [int(fid) for fid in file_ids]
        files = FileInfo.query.filter(FileInfo.id.in_(id_list)).all()
        for file in files:
            if file and file.is_frozen:
                file.is_frozen = False
                file.frozen_at = None
                file.frozen_by = None
                file.frozen_reason = None
                file.updated_by = user_id
                thawed_count += 1
                # 同步解冻关联的数据库数据
                sync_result = sync_freeze_status_to_data(file.filename, False, user_id)
                total_sync['sublist_count'] += sync_result['sublist_count']
                total_sync['usagelist_count'] += sync_result['usagelist_count']

        db.session.commit()
        # 批量解冻后立即失效列表/计数缓存
        query_cache_manager.invalidate_list_cache()

        current_app.logger.info(
            f'批量解冻 {thawed_count} 个文件, '
            f'同步数据: SubList {total_sync["sublist_count"]} 条, '
            f'UsageList {total_sync["usagelist_count"]} 条'
        )
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.BATCH_THAW_FILE, f"批量解冻文件 {thawed_count} 个",
                       TargetType.FILE, None, f"{thawed_count}个文件")
        except Exception as _le:
            current_app.logger.warning(f"记录批量解冻文件日志失败: {_le}")
        return jsonify({
            'success': True,
            'thawed_count': thawed_count,
            'sync_result': total_sync
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'批量解冻文件失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/freeze_old_versions', methods=['POST'])
@login_required
@csrf_exempt_route
def api_freeze_old_versions():
    """批量冻结历史旧版本文件：按品番分组，冻结每组中EO版本较旧的文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有冻结文件的权限'}), 403

    try:
        from models.file_info import FileInfo
        from utils.eo_utils import parse_eo, compare_eo
        from datetime import datetime
        from collections import defaultdict

        # 获取所有活跃的、有品番和EO的文件
        active_files = FileInfo.query.filter(
            FileInfo.is_frozen == False,
            FileInfo.first_subpno.isnot(None),
            FileInfo.first_subpno != '',
            FileInfo.eo.isnot(None),
            FileInfo.eo != ''
        ).all()

        if not active_files:
            return jsonify({'success': True, 'message': '没有需要冻结的旧版本文件', 'frozen_count': 0})

        # 按品番分组
        subpno_groups = defaultdict(list)
        for file in active_files:
            subpno_groups[file.first_subpno].append(file)

        frozen_count = 0
        skipped_count = 0
        groups_processed = 0
        details = []
        to_sync_filenames = []  # M-2：收集待同步文件，循环结束后统一批量 UPDATE

        for subpno, files in subpno_groups.items():
            # 如果只有一个文件，不需要冻结
            if len(files) <= 1:
                skipped_count += len(files)
                continue

            # 找出EO版本最新的文件
            # 先按EO版本排序（从旧到新）
            def get_eo_sort_key(file):
                parsed = parse_eo(file.eo)
                if parsed is None:
                    return (999, 999)  # 无法解析的排到最后
                return parsed

            sorted_files = sorted(files, key=get_eo_sort_key)

            # 最新的文件是最后一个
            latest_file = sorted_files[-1]
            older_files = sorted_files[:-1]

            # 只冻结那些EO版本严格较旧的文件
            for old_file in older_files:
                # 再次确认旧文件的EO确实比最新文件旧
                cmp_result = compare_eo(latest_file.eo, old_file.eo)
                if cmp_result and cmp_result > 0:  # latest > old
                    old_file.is_frozen = True
                    old_file.frozen_at = datetime.utcnow()
                    old_file.frozen_by = user_id
                    old_file.frozen_reason = f'EO版本 {latest_file.eo} 为当前最新版本，此版本({old_file.eo})自动冻结'
                    frozen_count += 1
                    # M-2：仅收集文件名，循环结束后统一批量同步冻结状态（避免 N 次 UPDATE）
                    to_sync_filenames.append(old_file.filename)
                    details.append({
                        'file': old_file.filename,
                        'eo': old_file.eo,
                        'latest_eo': latest_file.eo,
                        'subpno': subpno
                    })

            groups_processed += 1

        if frozen_count > 0:
            # M-2：全部文件收集完毕后，一次性批量同步冻结关联数据（单条 IN 批量 UPDATE）
            sync_freeze_status_to_data_batch(to_sync_filenames, True, user_id)
            db.session.commit()
            # 旧版本冻结后立即失效列表/计数缓存
            query_cache_manager.invalidate_list_cache()
            current_app.logger.info(
                f'批量冻结旧版本: 冻结了 {frozen_count} 个文件，'
                f'涉及 {groups_processed} 个品番组，关联数据库数据已同步冻结'
            )
            try:
                from utils.operation_logger import log_action
                from models.operation_log import OperationType, TargetType
                log_action(OperationType.FREEZE_OLD_VERSIONS,
                           f"批量冻结历史旧版本文件 {frozen_count} 个（涉及 {groups_processed} 个品番组）",
                           TargetType.FILE, None, f"{frozen_count}个旧版本文件")
            except Exception as _le:
                current_app.logger.warning(f"记录冻结旧版本日志失败: {_le}")

        return jsonify({
            'success': True,
            'frozen_count': frozen_count,
            'groups_processed': groups_processed,
            'skipped_count': skipped_count,
            'details': details[:20]  # 只返回前20个详情
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'批量冻结旧版本失败: {str(e)}', exc_info=True)
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/freeze_old_versions_preview', methods=['GET'])
@login_required
def api_freeze_old_versions_preview():
    """预览将被冻结的旧版本文件（不实际冻结）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management_freeze:
            return jsonify({'success': False, 'error': '您没有查看权限'}), 403

    try:
        from models.file_info import FileInfo
        from utils.eo_utils import parse_eo, compare_eo
        from collections import defaultdict

        # 获取所有活跃的、有品番和EO的文件
        active_files = FileInfo.query.filter(
            FileInfo.is_frozen == False,
            FileInfo.first_subpno.isnot(None),
            FileInfo.first_subpno != '',
            FileInfo.eo.isnot(None),
            FileInfo.eo != ''
        ).all()

        # 按品番分组
        subpno_groups = defaultdict(list)
        for file in active_files:
            subpno_groups[file.first_subpno].append(file)

        preview_data = []
        total_to_freeze = 0

        for subpno, files in subpno_groups.items():
            if len(files) <= 1:
                continue

            def get_eo_sort_key(file):
                parsed = parse_eo(file.eo)
                if parsed is None:
                    return (999, 999)
                return parsed

            sorted_files = sorted(files, key=get_eo_sort_key)
            latest_file = sorted_files[-1]
            older_files = sorted_files[:-1]

            will_freeze = []
            for old_file in older_files:
                cmp_result = compare_eo(latest_file.eo, old_file.eo)
                if cmp_result and cmp_result > 0:
                    will_freeze.append({
                        'id': old_file.id,
                        'filename': old_file.filename,
                        'eo': old_file.eo,
                        'latest_eo': latest_file.eo
                    })
                    total_to_freeze += 1

            if will_freeze:
                preview_data.append({
                    'subpno': subpno,
                    'latest_eo': latest_file.eo,
                    'files_to_freeze': will_freeze
                })

        return jsonify({
            'success': True,
            'total_to_freeze': total_to_freeze,
            'groups_affected': len(preview_data),
            'preview': preview_data
        })
    except Exception as e:
        current_app.logger.error(f'预览旧版本冻结失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/files/<int:file_id>', methods=['GET'])
@login_required
def api_get_file_detail(file_id):
    """获取单个文件的详细信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    user_id = session['user_id']
    user = db.session.get(UserInfo, user_id)
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission or not permission.can_file_management:
            return jsonify({'success': False, 'error': '您没有查看文件的权限'}), 403
    
    try:
        from models.file_info import FileInfo
        
        file = db.session.get(FileInfo, file_id)
        if not file:
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        
        file_data = file.to_dict()
        file_data['frozen_by_name'] = file.freezer.username if file.freezer else None
        
        return jsonify({
            'success': True,
            'file': file_data
        })
    except Exception as e:
        current_app.logger.error(f'获取文件详情失败: {str(e)}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ============== 原有文件 API ==============

@main_bp.route('/api/file_headers', methods=['POST'])
@login_required
def api_file_headers():
    try:
        data = request.get_json() or {}
        fid1 = data.get('file_id_1')
        fid2 = data.get('file_id_2')
        if not fid1 or not fid2:
            return jsonify({'success': False, 'error': '必须选择两个文件'}), 400

        f1 = db.session.get(FileInfo, int(fid1))
        f2 = db.session.get(FileInfo, int(fid2))
        if not f1 or not f2:
            return jsonify({'success': False, 'error': '文件不存在'}), 404

        df1 = _read_file_data(f1)
        df2 = _read_file_data(f2)
        headers_list = [list(df1.columns), list(df2.columns)]
        return jsonify({'success': True, 'headers': headers_list[0], 'headers_list': headers_list})
    except Exception as e:
        current_app.logger.error(f"读取文件表头失败: {str(e)}")
        return jsonify({'success': False, 'error': '读取文件表头失败'}), 500


@main_bp.route('/api/file_compare', methods=['POST'])
@login_required
def api_file_compare():
    try:
        data = request.get_json() or {}
        fid1 = data.get('file_id_1')
        fid2 = data.get('file_id_2')
        key_column = data.get('key_column')
        compare_columns = data.get('compare_columns', [])
        compare_mode = (data.get('compare_mode') or 'key').strip()  # 'key' | 'merged_sublist'

        if not fid1 or not fid2:
            return jsonify({'success': False, 'error': '必须选择两个文件'}), 400
        if compare_mode == 'key' and not key_column:
            return jsonify({'success': False, 'error': '请选择关键列'}), 400

        f1 = db.session.get(FileInfo, int(fid1))
        f2 = db.session.get(FileInfo, int(fid2))
        if not f1 or not f2:
            return jsonify({'success': False, 'error': '文件不存在'}), 404

        df1 = _read_file_data(f1)
        df2 = _read_file_data(f2)

        if compare_mode == 'merged_sublist':
            # 仅限 SubList 类型
            if (f1.file_type or '').lower() != 'sublist' or (f2.file_type or '').lower() != 'sublist':
                return jsonify({'success': False, 'error': '合并单元列对比仅支持SubList文件'}), 400

            required_cols = [
                'Circuit','Wire','WireCode','Color','SQ','Length_Cut','Length_Cut2','Tml1','Tml2',
                'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
                'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
            ]
            # 用于展示在 Circuit 左侧的上下文字段
            context_cols = ['SubPno','EoKsc','GroupNo','GroupOrder','SetCode','CutCode']

            # 目标品番（如前端未传，则自动取各自文件中第一个非空 SubPno）
            subpno1 = (data.get('subpno1') or '').strip()
            subpno2 = (data.get('subpno2') or '').strip()

            def _detect_first_subpno(dfx: pd.DataFrame) -> str:
                try:
                    col = 'SubPno'
                    if col in dfx.columns:
                        for v in dfx[col].astype(str).tolist():
                            v = (v or '').strip()
                            if v:
                                return v
                except Exception:
                    pass
                return ''

            def _normalize_setcode(val: str) -> str:
                """将 SetCode 规整为用于合并判断的前缀：只保留前两段+末尾下划线。

                例：CN7PFM_AP24_18 -> CN7PFM_AP24_
                若只有一段：ABC -> ABC_
                为空或非字符串时返回空串。
                """
                try:
                    s = str(val or '').strip()
                    if not s:
                        return ''
                    parts = s.split('_')
                    if len(parts) >= 2:
                        return f"{parts[0]}_{parts[1]}_"
                    # 只有一段时也补一个下划线，保持格式一致
                    return f"{parts[0]}_"
                except Exception:
                    return ''

            def _build_map(dfx: pd.DataFrame, target_subpno: str) -> tuple[set, dict]:
                # 确保所需列存在
                dfx = dfx.copy()
                for c in required_cols + context_cols:
                    if c not in dfx.columns:
                        dfx[c] = ''
                dfx = dfx.fillna('').astype(str)
                for c in required_cols + context_cols:
                    dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
                # 按目标品番过滤（如果提供）
                if target_subpno:
                    try:
                        dfx = dfx[dfx['SubPno'] == target_subpno]
                    except Exception:
                        pass
                merged_set = set()
                merged_to_row = {}
                # to_dict('records') 替代 iterrows，减少逐行 Series 构造开销
                for row in dfx.to_dict('records'):
                    # 将规整后的 SetCode 纳入合并判断键
                    normalized_setcode = _normalize_setcode(row['SetCode'])
                    merged = '|'.join([row[c] for c in required_cols] + [normalized_setcode])
                    merged_set.add(merged)
                    # 组装一行展示数据：回路合并 + 上下文 + 明细
                    out = {'回路合并': merged}
                    for c in context_cols:
                        out[c] = row[c]
                    for c in required_cols:
                        out[c] = row[c]
                    # 仅保留一个代表行
                    if merged not in merged_to_row:
                        merged_to_row[merged] = out
                return merged_set, merged_to_row

            if not subpno1:
                subpno1 = _detect_first_subpno(df1)
            if not subpno2:
                subpno2 = _detect_first_subpno(df2)

            set1, map1 = _build_map(df1, subpno1)
            set2, map2 = _build_map(df2, subpno2)

            added_keys = sorted(list(set2 - set1))  # 文件2有 文件1无 → 增加回路（文件2新增的内容）
            deleted_keys = sorted(list(set1 - set2))  # 文件1有 文件2无 → 删除回路（文件1减少的内容）

            headers_out = ['回路合并'] + context_cols + required_cols
            added_rows = [map2[k] for k in added_keys]
            deleted_rows = [map1[k] for k in deleted_keys]

            result = {
                'mode': 'merged_sublist',
                'headers': headers_out,
                'added_rows': added_rows,
                'deleted_rows': deleted_rows,
                'file_names': [f1.filename, f2.filename],
                'subpno1': subpno1,
                'subpno2': subpno2
            }
            return jsonify({'success': True, 'result': result})
        else:
            if key_column not in df1.columns or key_column not in df2.columns:
                return jsonify({'success': False, 'error': f'关键列 {key_column} 不存在于所选文件中'}), 400

            result = _compare_dfs(df1, df2, key_column, compare_columns)
            result['file_names'] = [f1.filename, f2.filename]
            result['mode'] = 'key'
            return jsonify({'success': True, 'result': result})
    except Exception as e:
        current_app.logger.error(f"文件对比失败: {str(e)}")
        return jsonify({'success': False, 'error': '文件对比失败'}), 500


@main_bp.route('/file_comparison')
@login_required  # 添加登录验证
def file_comparison():
    """渲染数据差异对比页面"""
    # logger.debug("渲染数据差异对比页面")
    # 页面访问权限控制
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_file_comparison:
            flash('您没有数据对比页面的访问权限', 'error')
            return forbidden_page('您没有数据对比页面的访问权限')
    return render_template('file_comparison.html')


@main_bp.route('/rw_comparison')
@login_required  # 添加登录验证
def rw_comparison():
    """渲染RW核对页面"""
    # 页面访问权限控制
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_file_comparison:
            flash('您没有RW核对页面的访问权限', 'error')
            return forbidden_page('您没有RW核对页面的访问权限')
    return render_template('rw_comparison.html')


@main_bp.route('/api/rw_comparison/upload', methods=['POST'])
@csrf_exempt_route
def api_rw_comparison_upload():
    """处理RW对比Excel文件上传"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        import pandas as pd
        import tempfile
        import os
        from models.sub_list import SubList
        from models.usage_list import UsageList
        
        # 检查是否有文件上传
        if 'rwFile' not in request.files:
            return jsonify({'success': False, 'error': '未上传文件'}), 400
        
        file = request.files['rwFile']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        # 检查文件类型
        if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
            return jsonify({'success': False, 'error': '仅支持Excel文件(.xlsx, .xls)'}), 400
        
        # 保存文件到临时目录
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            file.save(tmp.name)
            tmp_path = tmp.name
        
        try:
            # 读取Excel文件
            df = pd.read_excel(tmp_path, dtype=str)
            
            # 检查必需的列：P/NO基准、EO、R/W后P/NO、R/W后EO
            required_columns = ['P/NO基准', 'EO', 'R/W后P/NO', 'R/W后EO']
            for col in required_columns:
                if col not in df.columns:
                    return jsonify({'success': False, 'error': f'文件缺少必需的列：{col}'}), 400
            
            # 确保可选列存在，如果不存在则添加为空值
            optional_columns = ['序号', '车种', 'TEAM']
            for col in optional_columns:
                if col not in df.columns:
                    df[col] = ''
            
            # 处理NaN值
            df = df.fillna('')
            
            # 转换为字典列表
            excel_data = df.to_dict('records')
            
            # 执行对比逻辑
            results = process_rw_comparison_data(excel_data)
            
            # 确保results是列表类型
            if not isinstance(results, list):
                results = []
            
            return jsonify({
                'success': True,
                'results': results
            })
        finally:
            # 删除临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    except Exception as e:
        current_app.logger.error(f"RW对比上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


def process_rw_comparison_data(data_list):
    """处理RW对比数据的核心逻辑，可用于Excel上传和手动新规"""
    try:
        from models.sub_list import SubList
        from models.usage_list import UsageList
        
        results = []

        # 批量预取：把所有行的 (品番, EO) 组合一次性取出，避免循环内 4N 次查库
        all_pairs = set()
        for row in data_list:
            all_pairs.add((row.get('P/NO基准'), row.get('EO')))
            all_pairs.add((row.get('R/W后P/NO'), row.get('R/W后EO')))
        sub_map = prefetch_key_rows(all_pairs, SubList)
        use_map = prefetch_key_rows(all_pairs, UsageList)

        for row in data_list:
            # 获取基准和RW后的P/NO和EO
            base_pno = row['P/NO基准']
            base_eo = row['EO']
            rw_pno = row['R/W后P/NO']
            rw_eo = row['R/W后EO']
            
            # 资料验证：检查数据库中是否存在相应的文件
            validation_messages = []

            # 从预取结果取数（活跃优先，活跃缺失时冻结兜底）
            base_sublist, base_sub_frozen = sub_map.get((base_pno, base_eo), ([], False))
            base_usagelist, base_use_frozen = use_map.get((base_pno, base_eo), ([], False))
            rw_sublist, rw_sub_frozen = sub_map.get((rw_pno, rw_eo), ([], False))
            rw_usagelist, rw_use_frozen = use_map.get((rw_pno, rw_eo), ([], False))

            # 验证基准数据
            if not base_sublist:
                validation_messages.append(f"基准P/NO={base_pno}, EO={base_eo} 缺少对应的SubList数据")
            if not base_usagelist:
                validation_messages.append(f"基准P/NO={base_pno}, EO={base_eo} 缺少对应的UsageList数据")

            # 验证RW后数据
            if not rw_sublist:
                validation_messages.append(f"RW后P/NO={rw_pno}, EO={rw_eo} 缺少对应的SubList数据")
            if not rw_usagelist:
                validation_messages.append(f"RW后P/NO={rw_pno}, EO={rw_eo} 缺少对应的UsageList数据")

            # 冻结状态提示（业务页面允许访问冻结数据，但须明确标注）
            base_frozen = bool(base_sub_frozen or base_use_frozen)
            rw_frozen = bool(rw_sub_frozen or rw_use_frozen)
            if base_frozen and (base_sublist or base_usagelist):
                validation_messages.append("基准资料为已冻结归档版本（只读）")
            if rw_frozen and (rw_sublist or rw_usagelist):
                validation_messages.append("RW后资料为已冻结归档版本（只读）")
            
            # 调试信息
            current_app.logger.debug(
                f"RW对比: base=({base_pno},{base_eo}) rw=({rw_pno},{rw_eo}) "
                f"base_sub={len(base_sublist)} base_use={len(base_usagelist)} "
                f"rw_sub={len(rw_sublist)} rw_use={len(rw_usagelist)}"
            )
            
            # 1. 处理SubList对比
            # SubList的必需列，用于生成复合键
            sublist_required_cols = [
                'SetCode','Circuit','Wire','WireCode','Color','SQ','Length_Cut2','Tml1','Tml2',
                'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
                'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
            ]
            
            # 为SubList数据生成复合键
            def generate_sublist_key(sublist_item):
                key_parts = []
                for col in sublist_required_cols:
                    value = getattr(sublist_item, col, '')
                    key_parts.append(str(value).strip())
                return '|'.join(key_parts)
            
            # 生成键集合
            base_sublist_keys = {generate_sublist_key(item) for item in base_sublist}
            rw_sublist_keys = {generate_sublist_key(item) for item in rw_sublist}
            
            # 计算SubList差异
            sublist_added_keys = rw_sublist_keys - base_sublist_keys
            sublist_deleted_keys = base_sublist_keys - rw_sublist_keys
            sublist_added = len(sublist_added_keys)
            sublist_deleted = len(sublist_deleted_keys)
            
            # 调试信息
            current_app.logger.debug(f"RW对比: sublist_added={len(sublist_added_keys)}, sublist_deleted={len(sublist_deleted_keys)}")
            
            # 2. 处理UsageList对比
            # UsageList的必需列，用于生成复合键
            usagelist_required_cols = [
                'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'
            ]
            
            # 为UsageList数据生成复合键
            def generate_usagelist_key(usagelist_item):
                key_parts = []
                for col in usagelist_required_cols:
                    value = getattr(usagelist_item, col, '')
                    key_parts.append(str(value).strip())
                return '|'.join(key_parts)
            
            # 生成键集合
            base_usagelist_keys = {generate_usagelist_key(item) for item in base_usagelist}
            rw_usagelist_keys = {generate_usagelist_key(item) for item in rw_usagelist}
            
            # 计算UsageList差异
            usagelist_added_keys = rw_usagelist_keys - base_usagelist_keys
            usagelist_deleted_keys = base_usagelist_keys - rw_usagelist_keys
            usagelist_added = len(usagelist_added_keys)
            usagelist_deleted = len(usagelist_deleted_keys)
            
            # 调试信息
            current_app.logger.debug(f"RW对比: usagelist_added={len(usagelist_added_keys)}, usagelist_deleted={len(usagelist_deleted_keys)}")
            
            # 构建结果行，包含验证信息
            result_row = {
                '序号': row['序号'],
                '车种': row['车种'],
                'TEAM': row['TEAM'],
                'P/NO基准': base_pno,
                'EO': base_eo,
                'R/W后P/NO': rw_pno,
                'R/W后EO': rw_eo,
                'sublist增加': sublist_added,
                'sublist删除': sublist_deleted,
                'Usagelist增加': usagelist_added,
                'Usagelist删除': usagelist_deleted,
                '基准冻结': base_frozen,
                'RW后冻结': rw_frozen,
                '验证信息': '; '.join(validation_messages) if validation_messages else '验证通过'
            }
            
            results.append(result_row)
        
        return results
    except Exception as e:
        current_app.logger.error(f"RW对比数据处理失败: {str(e)}")
        raise


@main_bp.route('/api/rw_comparison/manual_add', methods=['POST'])
@csrf_exempt_route
def api_rw_comparison_manual_add():
    """处理手动添加的RW对比数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        # 获取请求数据
        data = request.get_json()
        if not data or 'data' not in data:
            return jsonify({'success': False, 'error': '请求数据格式错误'}), 400
        
        manual_data = data['data']
        if not isinstance(manual_data, list):
            return jsonify({'success': False, 'error': '数据格式错误，必须是数组'}), 400
        
        # 执行对比逻辑
        results = process_rw_comparison_data(manual_data)
        
        # 确保results是列表类型
        if not isinstance(results, list):
            results = []
        
        return jsonify({
            'success': True,
            'results': results
        })
    except Exception as e:
        current_app.logger.error(f"手动添加RW对比数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


def highlight_string_differences(cell1, cell2):
    """
    比较两个字符串并标记字符级差异
    对于Excel单元格，我们将修改它们的富文本格式
    """
    from openpyxl.styles import Font
    from openpyxl.cell.text import InlinePatternFill
    
    text1 = str(cell1.value) if cell1.value is not None else ""
    text2 = str(cell2.value) if cell2.value is not None else ""
    
    # 如果两个字符串相同，不需要标记
    if text1 == text2:
        return
    
    # 简单的字符级差异标记
    # 注意：openpyxl不直接支持富文本中的字符级背景色
    # 我们使用字体颜色来标记差异
    
    # 为两个单元格设置默认字体
    default_font = Font(color="FF000000")  # 黑色
    diff_font = Font(color="FFFF0000")  # 红色
    
    # 这里我们使用一种简化的方法：如果两个单元格内容不同，就将它们标记为红色
    # 由于openpyxl的限制，我们无法实现真正的字符级差异标记
    cell1.font = diff_font
    cell2.font = diff_font

@main_bp.route('/api/rw_comparison/download')
def api_rw_comparison_download():
    """下载RW对比差异文件"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    try:
        import pandas as pd
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from io import BytesIO
        from models.sub_list import SubList
        from models.usage_list import UsageList
        
        # 获取请求参数
        base_pno = request.args.get('base_pno')
        base_eo = request.args.get('base_eo')
        rw_pno = request.args.get('rw_pno')
        rw_eo = request.args.get('rw_eo')
        
        if not all([base_pno, base_eo, rw_pno, rw_eo]):
            return jsonify({'success': False, 'error': '参数不完整'}), 400
        
        # 1. 生成SubList差异数据
        # SubList的必需列，用于生成复合键
        sublist_required_cols = [
            'SetCode','Circuit','Wire','WireCode','Color','SQ','Length_Cut2','Tml1','Tml2',
            'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
            'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
        ]
        
        # 查询基准/RW后 P/NO+EO 的 SubList 数据（活跃优先，冻结兜底只读）
        base_sublist, base_sub_frozen = fetch_sublist_by_key(base_pno, base_eo)
        rw_sublist, rw_sub_frozen = fetch_sublist_by_key(rw_pno, rw_eo)
        
        # 为SubList数据生成复合键
        def generate_sublist_key(sublist_item):
            key_parts = []
            for col in sublist_required_cols:
                value = getattr(sublist_item, col, '')
                key_parts.append(str(value).strip())
            return '|'.join(key_parts)
        
        # 生成键集合
        base_sublist_keys = {generate_sublist_key(item): item for item in base_sublist}
        rw_sublist_keys = {generate_sublist_key(item): item for item in rw_sublist}
        
        # 计算差异
        sublist_added_keys = rw_sublist_keys.keys() - base_sublist_keys.keys()
        sublist_deleted_keys = base_sublist_keys.keys() - rw_sublist_keys.keys()
        sublist_common_keys = base_sublist_keys.keys() & rw_sublist_keys.keys()
        
        # 2. 生成UsageList差异数据
        # UsageList的必需列，用于生成复合键
        usagelist_required_cols = [
            'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'
        ]
        
        # 查询基准/RW后 P/NO+EO 的 UsageList 数据（活跃优先，冻结兜底只读）
        base_usagelist, base_use_frozen = fetch_usagelist_by_key(base_pno, base_eo)
        rw_usagelist, rw_use_frozen = fetch_usagelist_by_key(rw_pno, rw_eo)
        base_frozen = bool(base_sub_frozen or base_use_frozen)
        rw_frozen = bool(rw_sub_frozen or rw_use_frozen)
        
        # 为UsageList数据生成复合键
        def generate_usagelist_key(usagelist_item):
            key_parts = []
            for col in usagelist_required_cols:
                value = getattr(usagelist_item, col, '')
                key_parts.append(str(value).strip())
            return '|'.join(key_parts)
        
        # 生成键集合
        base_usagelist_keys = {generate_usagelist_key(item): item for item in base_usagelist}
        rw_usagelist_keys = {generate_usagelist_key(item): item for item in rw_usagelist}
        
        # 计算差异
        usagelist_added_keys = rw_usagelist_keys.keys() - base_usagelist_keys.keys()
        usagelist_deleted_keys = base_usagelist_keys.keys() - rw_usagelist_keys.keys()
        usagelist_common_keys = base_usagelist_keys.keys() & rw_usagelist_keys.keys()
        
        # 3. 创建Excel文件
        output = BytesIO()
        wb = Workbook()
        
        # 设置填充颜色
        added_fill = PatternFill(start_color='90EE90', end_color='90EE90', fill_type='solid')  # 浅绿色
        deleted_fill = PatternFill(start_color='FFCCCB', end_color='FFCCCB', fill_type='solid')  # 浅红色
        
        # 创建SubList差异工作表
        ws_sublist = wb.active
        ws_sublist.title = 'SubList差异'
        
        # SubList表头 - 添加品番和EO列
        ws_sublist.append(['类型', 'P/NO', 'EO', 'SetCode', 'Circuit', 'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut2', 'Tml1', 'Tml2',
                           'Strip1', 'Strip2', 'Rel1', 'Rel2', 'Seal1', 'Seal2', 'SubComp1', 'SubComp2',
                           'M_ST1', 'M_ST2', 'M_ST3', 'M_ST4', 'Joint_Points', 'Remark1', 'Remark2'])
        
        # 添加SubList增加的数据
        for key in sublist_added_keys:
            item = rw_sublist_keys[key]
            row = ['增加' + ('(已冻结)' if rw_frozen else ''), rw_pno, rw_eo]  # 添加P/NO和EO列
            for col in sublist_required_cols:
                row.append(getattr(item, col, ''))
            ws_sublist.append(row)
            # 设置增加行的颜色
            for cell in ws_sublist[ws_sublist.max_row]:
                cell.fill = added_fill
        
        # 添加SubList删除的数据
        for key in sublist_deleted_keys:
            item = base_sublist_keys[key]
            row = ['删除' + ('(已冻结)' if base_frozen else ''), base_pno, base_eo]  # 添加P/NO和EO列
            for col in sublist_required_cols:
                row.append(getattr(item, col, ''))
            ws_sublist.append(row)
            # 设置删除行的颜色
            for cell in ws_sublist[ws_sublist.max_row]:
                cell.fill = deleted_fill
        
        # 创建UsageList差异工作表
        ws_usagelist = wb.create_sheet(title='UsageList差异')
        
        # UsageList表头 - 添加品番和EO列
        ws_usagelist.append(['类型', 'P/NO', 'EO', 'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'])
        
        # 添加UsageList增加的数据
        for key in usagelist_added_keys:
            item = rw_usagelist_keys[key]
            row = ['增加' + ('(已冻结)' if rw_frozen else ''), rw_pno, rw_eo]  # 添加P/NO和EO列
            for col in usagelist_required_cols:
                row.append(getattr(item, col, ''))
            ws_usagelist.append(row)
            # 设置增加行的颜色
            for cell in ws_usagelist[ws_usagelist.max_row]:
                cell.fill = added_fill
        
        # 添加UsageList删除的数据
        for key in usagelist_deleted_keys:
            item = base_usagelist_keys[key]
            row = ['删除' + ('(已冻结)' if base_frozen else ''), base_pno, base_eo]  # 添加P/NO和EO列
            for col in usagelist_required_cols:
                row.append(getattr(item, col, ''))
            ws_usagelist.append(row)
            # 设置删除行的颜色
            for cell in ws_usagelist[ws_usagelist.max_row]:
                cell.fill = deleted_fill
        
        # 创建字符级差异工作表
        ws_diff = wb.create_sheet(title='字符级差异')
        
        # 添加SubList字符级差异
        ws_diff.append(['类型', 'P/NO', 'EO', '字段名', '基准值', 'RW后值'])
        
        # 比较SubList公共键的字符级差异
        for key in sublist_common_keys:
            base_item = base_sublist_keys[key]
            rw_item = rw_sublist_keys[key]
            
            for col in sublist_required_cols:
                base_value = getattr(base_item, col, '')
                rw_value = getattr(rw_item, col, '')
                
                if str(base_value) != str(rw_value):
                    row = ['修改', base_pno, base_eo, col, base_value, rw_value]
                    ws_diff.append(row)
                    
                    # 获取刚添加的行
                    current_row = ws_diff.max_row
                    
                    # 标记差异单元格
                    base_cell = ws_diff.cell(row=current_row, column=5)
                    rw_cell = ws_diff.cell(row=current_row, column=6)
                    
                    # 高亮显示差异
                    base_cell.font = Font(color="FFFF0000")  # 红色
                    rw_cell.font = Font(color="FFFF0000")  # 红色
        
        # 比较UsageList公共键的字符级差异
        for key in usagelist_common_keys:
            base_item = base_usagelist_keys[key]
            rw_item = rw_usagelist_keys[key]
            
            for col in usagelist_required_cols:
                base_value = getattr(base_item, col, '')
                rw_value = getattr(rw_item, col, '')
                
                if str(base_value) != str(rw_value):
                    row = ['修改', base_pno, base_eo, col, base_value, rw_value]
                    ws_diff.append(row)
                    
                    # 获取刚添加的行
                    current_row = ws_diff.max_row
                    
                    # 标记差异单元格
                    base_cell = ws_diff.cell(row=current_row, column=5)
                    rw_cell = ws_diff.cell(row=current_row, column=6)
                    
                    # 高亮显示差异
                    base_cell.font = Font(color="FFFF0000")  # 红色
                    rw_cell.font = Font(color="FFFF0000")  # 红色
        
        # 保存Excel文件
        wb.save(output)
        output.seek(0)
        
        # 生成文件名，添加日期时间戳
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        frozen_tag = '_含冻结归档' if (base_frozen or rw_frozen) else ''
        filename = f"RW对比差异_{base_pno}_{base_eo}_vs_{rw_pno}_{rw_eo}{frozen_tag}_{timestamp}.xlsx"
        
        # 返回文件
        return send_file(output, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    
    except Exception as e:
        current_app.logger.error(f"RW对比下载失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/rw_comparison/download_list', methods=['POST'])
@csrf_exempt_route
def api_rw_comparison_download_list():
    """下载RW对比结果清单"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        import pandas as pd
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from io import BytesIO
        
        # 获取请求数据
        data = request.get_json()
        results = data.get('results', [])
        
        if not results:
            return jsonify({'success': False, 'error': '没有对比结果数据'}), 400
        
        # 创建Excel文件
        output = BytesIO()
        wb = Workbook()
        ws = wb.active
        ws.title = 'RW对比结果清单'
        
        # 设置填充颜色
        added_fill = PatternFill(start_color='90EE90', end_color='90EE90', fill_type='solid')  # 浅绿色
        deleted_fill = PatternFill(start_color='FFCCCB', end_color='FFCCCB', fill_type='solid')  # 浅红色
        
        # 表头
        ws.append(['序号', '车种', 'TEAM', 'P/NO基准', 'EO', 'R/W后P/NO', 'R/W后EO', 'sublist增加', 'sublist删除', 'Usagelist增加', 'Usagelist删除', '数据状态'])

        # 添加数据
        for row in results:
            # 冻结状态标注
            status_parts = []
            if row.get('基准冻结'):
                status_parts.append('基准已冻结')
            if row.get('RW后冻结'):
                status_parts.append('RW后已冻结')
            status_text = '、'.join(status_parts)

            ws.append([
                row.get('序号', ''),
                row.get('车种', ''),
                row.get('TEAM', ''),
                row.get('P/NO基准', ''),
                row.get('EO', ''),
                row.get('R/W后P/NO', ''),
                row.get('R/W后EO', ''),
                row.get('sublist增加', 0),
                row.get('sublist删除', 0),
                row.get('Usagelist增加', 0),
                row.get('Usagelist删除', 0),
                status_text
            ])
            
            # 标记差异点颜色
            current_row = ws.max_row
            
            # 检查sublist增加
            if row.get('sublist增加', 0) > 0:
                ws.cell(row=current_row, column=8).fill = added_fill
            
            # 检查sublist删除
            if row.get('sublist删除', 0) > 0:
                ws.cell(row=current_row, column=9).fill = deleted_fill
            
            # 检查Usagelist增加
            if row.get('Usagelist增加', 0) > 0:
                ws.cell(row=current_row, column=10).fill = added_fill
            
            # 检查Usagelist删除
            if row.get('Usagelist删除', 0) > 0:
                ws.cell(row=current_row, column=11).fill = deleted_fill
        
        # 保存Excel文件
        wb.save(output)
        output.seek(0)
        
        # 生成文件名，添加日期时间戳
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"RW对比结果清单_{timestamp}.xlsx"
        
        # 返回文件
        return send_file(output, as_attachment=True, download_name=filename, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    
    except Exception as e:
        current_app.logger.error(f"RW对比结果清单下载失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/rw_comparison/download_detail_zip', methods=['POST'])
@csrf_exempt_route
def api_rw_comparison_download_detail_zip():
    """下载所有RW对比结果详细文件的压缩包"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        import zipfile
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from models.sub_list import SubList
        from models.usage_list import UsageList
        
        # 获取请求数据
        data = request.get_json()
        results = data.get('results', [])
        
        if not results:
            return jsonify({'success': False, 'error': '没有对比结果数据'}), 400
        
        # 创建ZIP文件
        zip_output = BytesIO()
        with zipfile.ZipFile(zip_output, 'w', zipfile.ZIP_DEFLATED) as zf:
            # 遍历每个对比结果，生成详细文件并添加到ZIP中
            for i, result in enumerate(results, 1):
                base_pno = result.get('P/NO基准')
                base_eo = result.get('EO')
                rw_pno = result.get('R/W后P/NO')
                rw_eo = result.get('R/W后EO')
                
                if not all([base_pno, base_eo, rw_pno, rw_eo]):
                    continue
                
                # 生成详细对比文件，复用api_rw_comparison_download的逻辑
                # 1. 生成SubList差异数据
                # SubList的必需列，用于生成复合键
                sublist_required_cols = [
                    'SetCode','Circuit','Wire','WireCode','Color','SQ','Length_Cut2','Tml1','Tml2',
                    'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
                    'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
                ]
                
                # 查询基准/RW后 P/NO+EO 的 SubList 数据（活跃优先，冻结兜底只读）
                base_sublist, base_sub_frozen = fetch_sublist_by_key(base_pno, base_eo)
                rw_sublist, rw_sub_frozen = fetch_sublist_by_key(rw_pno, rw_eo)
                
                # 为SubList数据生成复合键
                def generate_sublist_key(sublist_item):
                    key_parts = []
                    for col in sublist_required_cols:
                        value = getattr(sublist_item, col, '')
                        key_parts.append(str(value).strip())
                    return '|'.join(key_parts)
                
                # 生成键集合
                base_sublist_keys = {generate_sublist_key(item): item for item in base_sublist}
                rw_sublist_keys = {generate_sublist_key(item): item for item in rw_sublist}
                
                # 计算差异
                sublist_added_keys = rw_sublist_keys.keys() - base_sublist_keys.keys()
                sublist_deleted_keys = base_sublist_keys.keys() - rw_sublist_keys.keys()
                
                # 2. 生成UsageList差异数据
                # UsageList的必需列，用于生成复合键
                usagelist_required_cols = [
                    'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'
                ]
                
                # 查询基准/RW后 P/NO+EO 的 UsageList 数据（活跃优先，冻结兜底只读）
                base_usagelist, base_use_frozen = fetch_usagelist_by_key(base_pno, base_eo)
                rw_usagelist, rw_use_frozen = fetch_usagelist_by_key(rw_pno, rw_eo)
                base_frozen = bool(base_sub_frozen or base_use_frozen)
                rw_frozen = bool(rw_sub_frozen or rw_use_frozen)
                
                # 为UsageList数据生成复合键
                def generate_usagelist_key(usagelist_item):
                    key_parts = []
                    for col in usagelist_required_cols:
                        value = getattr(usagelist_item, col, '')
                        key_parts.append(str(value).strip())
                    return '|'.join(key_parts)
                
                # 生成键集合
                base_usagelist_keys = {generate_usagelist_key(item): item for item in base_usagelist}
                rw_usagelist_keys = {generate_usagelist_key(item): item for item in rw_usagelist}
                
                # 计算差异
                usagelist_added_keys = rw_usagelist_keys.keys() - base_usagelist_keys.keys()
                usagelist_deleted_keys = base_usagelist_keys.keys() - rw_usagelist_keys.keys()
                
                # 3. 创建Excel文件
                detail_output = BytesIO()
                wb = Workbook()
                
                # 设置填充颜色
                added_fill = PatternFill(start_color='90EE90', end_color='90EE90', fill_type='solid')  # 浅绿色
                deleted_fill = PatternFill(start_color='FFCCCB', end_color='FFCCCB', fill_type='solid')  # 浅红色
                
                # 创建SubList差异工作表
                ws_sublist = wb.active
                ws_sublist.title = 'SubList差异'
                
                # SubList表头 - 添加品番和EO列
                ws_sublist.append(['类型', 'P/NO', 'EO', 'SetCode', 'Circuit', 'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut2', 'Tml1', 'Tml2',
                               'Strip1', 'Strip2', 'Rel1', 'Rel2', 'Seal1', 'Seal2', 'SubComp1', 'SubComp2',
                               'M_ST1', 'M_ST2', 'M_ST3', 'M_ST4', 'Joint_Points', 'Remark1', 'Remark2'])
                
                # 添加SubList增加的数据
                for key in sublist_added_keys:
                    item = rw_sublist_keys[key]
                    row = ['增加' + ('(已冻结)' if rw_frozen else ''), rw_pno, rw_eo]  # 添加P/NO和EO列
                    for col in sublist_required_cols:
                        row.append(getattr(item, col, ''))
                    ws_sublist.append(row)
                    # 设置增加行的颜色
                    for cell in ws_sublist[ws_sublist.max_row]:
                        cell.fill = added_fill
                
                # 添加SubList删除的数据
                for key in sublist_deleted_keys:
                    item = base_sublist_keys[key]
                    row = ['删除' + ('(已冻结)' if base_frozen else ''), base_pno, base_eo]  # 添加P/NO和EO列
                    for col in sublist_required_cols:
                        row.append(getattr(item, col, ''))
                    ws_sublist.append(row)
                    # 设置删除行的颜色
                    for cell in ws_sublist[ws_sublist.max_row]:
                        cell.fill = deleted_fill
                
                # 创建UsageList差异工作表
                ws_usagelist = wb.create_sheet(title='UsageList差异')
                
                # UsageList表头 - 添加品番和EO列
                ws_usagelist.append(['类型', 'P/NO', 'EO', 'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'])
                
                # 添加UsageList增加的数据
                for key in usagelist_added_keys:
                    item = rw_usagelist_keys[key]
                    row = ['增加' + ('(已冻结)' if rw_frozen else ''), rw_pno, rw_eo]  # 添加P/NO和EO列
                    for col in usagelist_required_cols:
                        row.append(getattr(item, col, ''))
                    ws_usagelist.append(row)
                    # 设置增加行的颜色
                    for cell in ws_usagelist[ws_usagelist.max_row]:
                        cell.fill = added_fill
                
                # 添加UsageList删除的数据
                for key in usagelist_deleted_keys:
                    item = base_usagelist_keys[key]
                    row = ['删除' + ('(已冻结)' if base_frozen else ''), base_pno, base_eo]  # 添加P/NO和EO列
                    for col in usagelist_required_cols:
                        row.append(getattr(item, col, ''))
                    ws_usagelist.append(row)
                    # 设置删除行的颜色
                    for cell in ws_usagelist[ws_usagelist.max_row]:
                        cell.fill = deleted_fill
                
                # 保存Excel文件到BytesIO
                wb.save(detail_output)
                detail_output.seek(0)
                
                # 生成文件名
                frozen_tag = '_含冻结归档' if (base_frozen or rw_frozen) else ''
                detail_filename = f"RW对比差异_{base_pno}_{base_eo}_vs_{rw_pno}_{rw_eo}{frozen_tag}.xlsx"
                
                # 添加到ZIP文件
                zf.writestr(detail_filename, detail_output.getvalue())
        
        # 重置ZIP文件指针
        zip_output.seek(0)
        
        # 生成文件名，添加日期时间戳
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"明细详单_{timestamp}.zip"
        
        # 返回ZIP文件
        return send_file(zip_output, as_attachment=True, download_name=filename, mimetype='application/zip')
    
    except Exception as e:
        current_app.logger.error(f"RW对比明细详单下载失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/rw_comparison/options', methods=['GET'])
@login_required
def api_rw_comparison_options():
    """获取可用于RW对比的资料选项列表（P/NO + EO 组合）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from models.sub_list import SubList
        from sqlalchemy import func

        search = request.args.get('search', '', type=str).strip()
        limit = request.args.get('limit', 200, type=int)

        # 查询所有未删除的 distinct SubPno + EoKsc + VehicleType（含冻结归档）
        # is_frozen 取组内最小值：组内有活跃行即视为活跃；全部冻结才标注为冻结
        query = db.session.query(
            SubList.SubPno,
            SubList.EoKsc,
            SubList.VehicleType,
            func.count(SubList.id).label('row_count'),
            func.min(SubList.is_frozen).label('frozen_flag')
        ).filter(
            SubList.deleted == False
        )

        if search:
            query = query.filter(
                db.or_(
                    SubList.SubPno.ilike(f'%{search}%'),
                    SubList.EoKsc.ilike(f'%{search}%'),
                    SubList.VehicleType.ilike(f'%{search}%')
                )
            )

        query = query.group_by(
            SubList.SubPno, SubList.EoKsc, SubList.VehicleType
        ).order_by(
            SubList.SubPno, SubList.EoKsc
        ).limit(limit)

        rows = query.all()

        options = []
        for r in rows:
            is_frozen = bool(r.frozen_flag)
            label = f"{r.SubPno}  |  EO: {r.EoKsc}  |  {r.VehicleType or ''}  ({r.row_count}行)"
            if is_frozen:
                label += "  【已冻结】"
            options.append({
                'sub_pno': r.SubPno or '',
                'eo': r.EoKsc or '',
                'vehicle_type': r.VehicleType or '',
                'row_count': r.row_count,
                'is_frozen': is_frozen,
                'label': label
            })

        return jsonify({'success': True, 'options': options})
    except Exception as e:
        current_app.logger.error(f"获取RW对比选项失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/rw_comparison/select_compare', methods=['POST'])
@csrf_exempt_route
def api_rw_comparison_select_compare():
    """从资料库选择资料执行RW对比"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        data = request.get_json()
        if not data or 'pairs' not in data:
            return jsonify({'success': False, 'error': '请提供对比资料对'}), 400

        pairs = data['pairs']
        if not isinstance(pairs, list) or len(pairs) == 0:
            return jsonify({'success': False, 'error': '对比资料对不能为空'}), 400

        # 构建与 process_rw_comparison_data 兼容的数据格式
        compare_data = []
        for idx, pair in enumerate(pairs, 1):
            compare_data.append({
                '序号': str(idx),
                '车种': pair.get('vehicle_type', ''),
                'TEAM': '',
                'P/NO基准': pair.get('base_pno', ''),
                'EO': pair.get('base_eo', ''),
                'R/W后P/NO': pair.get('rw_pno', ''),
                'R/W后EO': pair.get('rw_eo', '')
            })

        results = process_rw_comparison_data(compare_data)
        if not isinstance(results, list):
            results = []

        return jsonify({'success': True, 'results': results})
    except Exception as e:
        current_app.logger.error(f"选择资料RW对比失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/get_first_subpno', methods=['POST'])
@csrf_exempt_route
def get_first_subpno():
    """获取第一个SubPno值"""
    try:
        file1_name = request.form.get('file1Name')
        file2_name = request.form.get('file2Name')
        
        if not file1_name or not file2_name:
            return jsonify({'error': '文件名不能为空'}), 400
            
        # 这里可以根据需要实现具体的逻辑
        # 目前返回一个默认值
        return jsonify({'first_subpno': 'default'})
    except Exception as e:
        logger.error(f"获取第一个SubPno失败: {str(e)}")
        return jsonify({'error': '获取SubPno失败'}), 500


@main_bp.route('/vehicle_types')
@login_required
def vehicle_types():
    """车种管理页面"""
    # 检查用户是否有车种管理权限
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_vehicle_types:
            flash('您没有车种管理权限', 'error')
            return forbidden_page('您没有车种管理页面的访问权限')
    
    # 获取分页参数
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)  # 默认每页20条
    
    # 获取车种数据（分页）
    from models.vehicle_type import VehicleType
    pagination = VehicleType.query.order_by(VehicleType.create_time.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    vehicle_types = pagination.items
    
    return render_template('vehicle_types.html', 
                         vehicle_types=vehicle_types, 
                         pagination=pagination,
                         current_page=page,
                         per_page=per_page)


@main_bp.route('/products')
@login_required
def products():
    """成品管理页面"""
    # 检查用户是否有成品管理权限
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_products:
            flash('您没有成品管理权限', 'error')
            return forbidden_page('您没有成品管理页面的访问权限')
    
    try:
        import time
        page_start_time = time.time()
        
        from models.finished_product import FinishedProduct
        from sqlalchemy import text as sa_text
        from sqlalchemy.orm import joinedload
        is_mysql = str(current_app.config.get('SQLALCHEMY_DATABASE_URI', '')).lower().startswith('mysql')
        FinishedProductModel = FinishedProduct
        
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        vehicle_name = request.args.get('vehicle_name', '').strip()
        part_number = request.args.get('part_number', '').strip()
        lot_number = request.args.get('lot_number', '').strip()
        eo = request.args.get('eo', '').strip()
        eo_status = request.args.get('eo_status', '').strip()
        
        # 构建查询，使用joinedload预先加载creator和modifier关系
        query = db.session.query(FinishedProductModel).options(
            joinedload(FinishedProductModel.creator),
            joinedload(FinishedProductModel.modifier)
        )
        
        if vehicle_name:
            query = query.filter(FinishedProductModel.vehicle_name.like(f'%{vehicle_name}%'))
        if part_number:
            query = query.filter(FinishedProductModel.part_number.like(f'%{part_number}%'))
        if lot_number:
            query = query.filter(FinishedProductModel.lot_number.like(f'%{lot_number}%'))
        if eo and hasattr(FinishedProductModel, 'eo'):
            query = query.filter(FinishedProductModel.eo.like(f"%{eo}%"))
        
        # EO状态筛选
        if eo_status:
            if eo_status == 'normal':
                # 正常状态：既不是EO覆盖也不是EO注意
                query = query.filter(
                    ~FinishedProductModel.eo_override,
                    ~FinishedProductModel.needs_eo_attention
                )
            elif eo_status == 'eo_override':
                # EO覆盖状态
                query = query.filter(FinishedProductModel.eo_override == True)
            elif eo_status == 'eo_attention':
                # EO注意状态
                query = query.filter(FinishedProductModel.needs_eo_attention == True)
        
        # 按创建时间倒序排列
        query = query.order_by(FinishedProductModel.create_time.desc())
        
        # 分页
        pagination_start = time.time()
        pagination = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        pagination_end = time.time()
        
        current_app.logger.info(f'[Products] 分页查询完成，返回{len(pagination.items)}条记录，耗时: {pagination_end - pagination_start:.3f}秒')
        
        # 暂时移除参考EO查询以提升页面加载速度，改为异步加载
        usage_eo_multi_map = {}
        current_app.logger.info('[Products] 跳过参考EO查询以提升性能，将通过异步API加载')

        page_end_time = time.time()
        total_load_time = page_end_time - page_start_time
        
        current_app.logger.info(f'[Products] 页面加载完成，总耗时: {total_load_time:.3f}秒')
        
        return render_template('products.html', 
                             products=pagination.items,
                             pagination=pagination,
                             current_page=page,
                             per_page=per_page,
                             vehicle_name=vehicle_name,
                             part_number=part_number,
                             lot_number=lot_number,
                             eo=eo,
                             eo_status=eo_status,
                             usage_eo_multi_map=usage_eo_multi_map)
    
    except Exception as e:
        current_app.logger.error(f"获取成品列表失败: {str(e)}")
        flash('获取成品列表失败', 'error')
        return render_template('products.html', products=[], pagination=None)


@main_bp.route('/material_download')
@login_required
def material_download():
    """资材量下载页面"""
    # 检查用户是否有资材量下载权限
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download:
            flash('您没有资材量下载权限', 'error')
            return forbidden_page('您没有资材量下载页面的访问权限')
    
    return render_template('material_download.html')


@main_bp.route('/material_calculator')
@login_required
def material_calculator():
    """资材换算页面：默认重定向到胶带换算"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_calculator'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问资材换算页面，权限不足")
        return forbidden_page('您没有资材换算页面的访问权限')
    return redirect(url_for('main.tape_calculator'))


@main_bp.route('/material_calculator/tape')
@login_required
def tape_calculator():
    """胶带换算页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_calculator'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问胶带换算页面，权限不足")
        return forbidden_page('您没有资材换算页面的访问权限')
    return render_template('tape_calculator.html')


@main_bp.route('/material_calculator/sheet')
@login_required
def sheet_calculator():
    """皮子换算页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_calculator'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问皮子换算页面，权限不足")
        return forbidden_page('您没有资材换算页面的访问权限')
    return render_template('sheet_calculator.html')


@main_bp.route('/material_calculator/tube')
@login_required
def tube_calculator():
    """管换算页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_calculator'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问管换算页面，权限不足")
        return forbidden_page('您没有资材换算页面的访问权限')
    return render_template('tube_calculator.html')


# ============================================================
# 生产投入计划书自动汇总系统
# ============================================================
@main_bp.route('/invest_tidy', methods=['GET', 'POST'])
@login_required
def invest_tidy():
    """投入计划书：批量上传每日「YYYY년M월D일 투입계획서.xlsx」，
    自动识别 A/B/J/7/G 批次明细Sheet，清洗打标后输出四类汇总，
    并按 日期/车种/批次/自然月/季度 选择性累加与导出。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_invest_tidy'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问投入计划书页面，权限不足")
        return forbidden_page('您没有投入计划书页面的访问权限')

    if request.method == 'POST':
        files = [f for f in request.files.getlist('excelFiles') if f and f.filename]
        if not files:
            flash('请选择要上传的Excel文件（可多选）', 'error')
            return redirect(url_for('main.invest_tidy'))
        bad = [f.filename for f in files
               if not f.filename.lower().endswith(('.xlsx', '.xlsm', '.xls'))]
        if bad:
            flash('仅支持 .xlsx / .xlsm / .xls 格式：' + '、'.join(bad), 'error')
            return redirect(url_for('main.invest_tidy'))

        try:
            from invest_summary_engine import (
                parse_workbook, save_parsed_to_db, db_file_rows, db_totals,
                db_batch_options, upload_calendar, available_months,
                save_invest_source
            )
            upload_stats = []
            indi_total, input_total = 0, 0
            for excel_file in files:
                tmp_in = tempfile.NamedTemporaryFile(
                    delete=False, suffix='.xlsx', prefix='invest_in_')
                tmp_in.close()
                try:
                    excel_file.save(tmp_in.name)
                    parsed = parse_workbook(tmp_in.name)
                    parsed['source_file'] = excel_file.filename  # 页面/下载统一显示上传原名
                    stats = save_parsed_to_db(parsed)             # 直接入库（LOT唯一查重）
                    # 保存源文件到单独目录，供下载原始文件使用
                    try:
                        save_invest_source(tmp_in.name, excel_file.filename)
                    except Exception as se:
                        current_app.logger.warning(
                            f"投入源文件保存失败《{excel_file.filename}》: {se}")
                finally:
                    if os.path.exists(tmp_in.name):
                        try:
                            os.remove(tmp_in.name)
                        except OSError:
                            pass
                upload_stats.append({'source_file': excel_file.filename, 'stats': stats})
                indi_total += stats['indicator_total']
                input_total += stats['input_total']

            # 入库反馈：成功条数 + LOT 重复提醒 + MES678 指示数量对比提醒
            for u in upload_stats:
                s = u['stats']
                if s['inserted']:
                    flash(f"《{u['source_file']}》已直接入库 {s['inserted']} 条"
                          f"（投入日期 {s['record_date']}）", 'success')
                if s['dup_lots']:
                    flash(f"《{u['source_file']}》跳过 {len(s['dup_lots'])} 条重复LOT，"
                          f"该LOT已在系统中存在：" + '、'.join(s['dup_lots'][:10]), 'warning')
                if s['in_dup_count']:
                    flash(f"《{u['source_file']}》文件内发现 {s['in_dup_count']} 个重复LOT，"
                          f"已按「每个LOT只保留一条」入库", 'warning')
                if not s['inserted'] and not s['dup_lots'] and not s['in_dup_count']:
                    flash(f"《{u['source_file']}》未识别到可入库的投入明细数据"
                          f"（请确认格式为「YYYY年M月D일 투입계획서」且Sheet含投入计划书与批次号 1-9 / A-Z）", 'warning')
            # MES678 指示数量合计 与 各Sheet投入数量合计 自动对比
            if indi_total > 0 and input_total > 0 and indi_total != input_total:
                flash(f"MES678指示数量合计 {indi_total:,} 与 各Sheet投入数量合计 {input_total:,} 不一致，"
                      f"差异 {abs(indi_total - input_total):,}（" +
                      ('指示数量 多 ' if indi_total > input_total else '投入数量 多 ') +
                      f"{abs(indi_total - input_total):,}），请核对源文件！", 'error')
            return render_template('invest_tidy.html',
                                   batches=db_file_rows() or None,
                                   totals=db_totals(),
                                   filter_display=None,
                                   filter_count=None,
                                   filter_export_url='',
                                   filter_options=db_batch_options(),
                                   filter_values={
                                       'date_from': datetime.now().strftime('%Y-%m-%d'),
                                       'date_to': '',
                                   },
                                   calendar_data=upload_calendar(),
                                   months=available_months())
        except Exception as e:
            current_app.logger.error(f"投入计划书解析失败: {e}", exc_info=True)
            flash(f'解析失败：{type(e).__name__}: {e}', 'error')
            return redirect(url_for('main.invest_tidy'))

    # GET：支持历史数据筛选（长期保存的数据按 日期/车种/批次/关键词/周期 汇总）
    f = request.args
    today_str = datetime.now().strftime('%Y-%m-%d')
    # 周期快捷筛选：scope=month/month=YYYY-MM 或 scope=quarter/quarter=YYYY-Qn
    period = None
    try:
        from invest_summary_engine import resolve_period
        period = resolve_period(f.get('scope', ''), f.get('month', ''),
                                f.get('quarter', ''))
    except Exception:
        period = None
    date_from = period[0] if period else f.get('date_from', '')
    date_to = period[1] if period else f.get('date_to', '')
    # 筛选表单的日期默认显示当天（不触发自动筛选）
    display_date_from = date_from or today_str
    display_date_to = date_to or today_str
    filter_on = bool(f.get('filter') or f.get('keyword') or date_from or date_to
                     or f.get('vehicle') or f.get('batch') or period)
    filter_display = None
    filter_options = {}
    filter_count = None
    filter_export_url = ''
    try:
        from invest_summary_engine import (
            db_batch_options, count_records, batch_detail_export_url
        )
        filter_options = db_batch_options()
        if filter_on:
            # 仅统计条数（DB 层 COUNT，不加载明细、不做汇总），避免大数据量卡顿
            filter_count = count_records(date_from, date_to,
                                         f.get('vehicle', ''), f.get('batch', ''),
                                         f.get('keyword', ''))
            filter_export_url = batch_detail_export_url(
                date_from, date_to, f.get('vehicle', ''), f.get('batch', ''),
                f.get('keyword', ''))
    except Exception as e:
        current_app.logger.error(f"投入计划书筛选失败: {e}", exc_info=True)
        
        flash(f'筛选失败：{type(e).__name__}: {e}', 'error')
    calendar_data = None
    months = []
    try:
        from invest_summary_engine import upload_calendar, available_months
        calendar_data = upload_calendar(f.get('cy', type=int) or None,
                                        f.get('cm', type=int) or None)
        months = available_months()
    except Exception:
        try:
            from invest_summary_engine import upload_calendar, available_months
            calendar_data = upload_calendar()
            months = available_months()
        except Exception:
            pass
    # 页面下方展示已上传入库文件列表：优先会话内本次上传，否则从数据库长期数据读取
    prev = session.get('invest_result') or []
    if isinstance(prev, dict):   # 兼容旧的单文件字典结构
        prev = [prev]
    if prev:
        batches = _invest_list_rows(prev)
        totals = _invest_totals(prev)
    else:
        from invest_summary_engine import db_file_rows, db_totals
        batches = db_file_rows()
        totals = db_totals()
    if not batches:
        batches = None
    if not totals:
        totals = None
    return render_template('invest_tidy.html', batches=batches,
                           totals=totals,
                           filter_display=filter_display,
                           filter_count=filter_count,
                           filter_export_url=filter_export_url,
                           filter_options=filter_options,
                           filter_values={
                               'date_from': display_date_from,
                               'date_to': display_date_to,
                               'vehicle': f.get('vehicle', ''),
                               'batch': f.get('batch', ''),
                               'keyword': f.get('keyword', ''),
                               'scope': f.get('scope', ''),
                               'month': f.get('month', ''),
                               'quarter': f.get('quarter', ''),
                           },
                           calendar_data=calendar_data,
                           months=months)


def _invest_totals(batches):
    """批量上传后的总览合计。"""
    if not batches:
        return None
    return {
        'files': len(batches),
        'rows': sum(len(b['result']['records']) for b in batches),
        'qty': sum(b['result']['totals'].get('总投入数量', 0) for b in batches),
        'circuits': sum(b['result']['totals'].get('总回路数', 0) for b in batches),
        'parts': sum(len(b['result']['part_summary']) for b in batches),
    }


def _invest_list_rows(entries):
    """把会话中的上传批次转换为页面文件列表行（不依赖超大Cookie字段）。

    每行含：source_file / record_date / record_len / parts_len / detail_sheets。
    detail_sheets 按批次去重，避免同一批次多个Sheet重复显示（如 7/7/7/7）。
    """
    rows = []
    for e in entries:
        r = e.get('result', {})
        seen = set()
        sheets = []
        for s in (r.get('detail_sheets') or []):
            b = s.get('batch')
            if not b or b in seen:
                continue
            seen.add(b)
            sheets.append({'name': s.get('name'), 'batch': b})
        rows.append({
            'source_file': r.get('source_file', ''),
            'record_date': r.get('record_date'),
            'record_len': len(r.get('records') or []),
            'parts_len': len(r.get('part_summary') or []),
            'detail_sheets': sheets,
        })
    return rows


@main_bp.route('/invest_tidy/batch_detail/download')
@login_required
def invest_batch_detail_download():
    """导出「批次明细」单表 Excel（仅按投入日期排序导出，不做汇总处理）。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_invest_tidy'):
        return forbidden_page('您没有投入计划书页面的访问权限')

    f = request.args
    try:
        from invest_summary_engine import resolve_period, build_batch_detail_xlsx
        period = resolve_period(f.get('scope', ''), f.get('month', ''),
                                f.get('quarter', ''))
        date_from = period[0] if period else f.get('date_from', '')
        date_to = period[1] if period else f.get('date_to', '')
        path, name = build_batch_detail_xlsx(
            date_from, date_to, f.get('vehicle', ''), f.get('batch', ''),
            f.get('keyword', ''))
        from flask import after_this_request

        @after_this_request
        def _cleanup(resp):
            try:
                os.remove(path)
            except OSError:
                pass
            return resp

        return send_file(
            path, as_attachment=True, download_name=name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"批次明细导出失败: {e}", exc_info=True)
        flash(f'导出失败：{type(e).__name__}: {e}', 'error')
        return redirect(url_for('main.invest_tidy'))


@main_bp.route('/invest_tidy/batch_download', methods=['POST'])
@login_required
def invest_tidy_batch_download():
    """批量下载：勾选多个已入库文件，逐个生成汇总Excel并以ZIP打包返回。

    优化点：
      - 按文件名列表 IN 查询，避免全表加载后 Python 分组
      - 先查一次拿到所有相关记录，再按来源文件分桶，减少 DB 往返
    """
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    names = request.form.getlist('filenames')
    names = [n for n in (x.strip() for x in names) if n]
    if not names:
        flash('请先勾选要下载的文件', 'warning')
        return redirect(url_for('main.invest_tidy'))
    try:
        import zipfile
        from invest_summary_engine import query_records, summarize, export_excel
        all_recs = query_records(source_file=names)
        by_file = {}
        for r in all_recs:
            by_file.setdefault(r.get('来源文件'), []).append(r)
        tmp_dir = tempfile.mkdtemp(prefix='invest_batch_')
        zip_path = os.path.join(
            tmp_dir, f'投入计划书批量汇总_{datetime.now().strftime("%Y%m%d_%H%M%S")}.zip')
        done = 0
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name in names:
                recs = by_file.get(name) or []
                if not recs:
                    continue
                summary = summarize(recs)
                parsed = {'source_file': name, 'records': recs, 'errors': []}
                tmp = tempfile.NamedTemporaryFile(
                    delete=False, suffix='.xlsx', prefix='invest_b_', dir=tmp_dir)
                tmp.close()
                export_excel(parsed, summary, tmp.name)
                base = os.path.splitext(os.path.basename(name))[0]
                zf.write(tmp.name, arcname=f'{base}_投入计划书汇总.xlsx')
                done += 1
        if not done:
            flash('所选文件均无入库数据，无法生成下载', 'warning')
            return redirect(url_for('main.invest_tidy'))
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        from flask import after_this_request
        import shutil

        @after_this_request
        def _cleanup_tmp(response):
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return response

        return send_file(zip_path, as_attachment=True,
                         download_name=f'投入计划书批量汇总_{ts}.zip')
    except Exception as e:
        current_app.logger.error(f"投入计划书批量下载失败: {e}", exc_info=True)
        flash(f'批量下载失败：{type(e).__name__}: {e}', 'error')
        return redirect(url_for('main.invest_tidy'))


@main_bp.route('/invest_tidy/download')
@login_required
def invest_tidy_download():
    """下载汇总Excel：?filtered=1 按筛选条件从数据库汇总下载；否则下载最近一次上传批次。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    # 1) 筛选下载：仅导出「批次明细」单表（不做任何汇总/聚合处理，速度快）
    f = request.args
    if f.get('filtered'):
        try:
            from invest_summary_engine import (
                resolve_period, build_batch_detail_xlsx
            )
            period = resolve_period(f.get('scope', ''), f.get('month', ''),
                                    f.get('quarter', ''))
            date_from = period[0] if period else f.get('date_from', '')
            date_to = period[1] if period else f.get('date_to', '')
            path, name = build_batch_detail_xlsx(
                date_from, date_to, f.get('vehicle', ''), f.get('batch', ''),
                f.get('keyword', ''))
            from flask import after_this_request

            @after_this_request
            def _cleanup_filter(resp):
                try:
                    os.remove(path)
                except OSError:
                    pass
                return resp

            return send_file(
                path, as_attachment=True, download_name=name,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        except Exception as e:
            current_app.logger.error(f"批次明细导出失败: {e}", exc_info=True)
            flash(f'下载失败：{type(e).__name__}: {e}', 'error')
            return redirect(url_for('main.invest_tidy'))

    # 2) 按文件名下载：优先返回上传的原始源文件；源文件不存在时回退为汇总生成
    filename = f.get('filename')
    if filename:
        try:
            from invest_summary_engine import get_invest_source_path
            src_path = get_invest_source_path(filename)
            if os.path.exists(src_path):
                return send_file(src_path, as_attachment=True,
                                 download_name=filename)
        except Exception as e:
            current_app.logger.warning(f"投入源文件读取失败《{filename}》: {e}")
        try:
            from invest_summary_engine import query_records, summarize, export_excel
            recs = query_records(source_file=filename)
            if not recs:
                flash('数据库中无此文件的记录', 'warning')
                return redirect(url_for('main.invest_tidy'))
            summary = summarize(recs)
            parsed = {'source_file': filename, 'records': recs, 'errors': []}
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx',
                                              prefix='invest_file_')
            tmp.close()
            export_excel(parsed, summary, tmp.name)
            base = os.path.splitext(filename)[0]
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            from flask import after_this_request

            @after_this_request
            def _cleanup_file(response):
                try:
                    os.remove(tmp.name)
                except OSError:
                    pass
                return response

            return send_file(tmp.name, as_attachment=True,
                             download_name=f'{base}_投入计划书汇总_{ts}.xlsx')
        except Exception as e:
            current_app.logger.error(f"按文件名下载失败: {e}", exc_info=True)
            flash(f'下载失败：{type(e).__name__}: {e}', 'error')
            return redirect(url_for('main.invest_tidy'))

    # 3) 最近一次上传批次的单文件汇总下载（默认最近一个）
    entries = session.get('invest_result') or []
    if isinstance(entries, dict):   # 兼容旧的单文件字典结构
        entries = [entries]
    if not entries:
        flash('请先上传并解析投入计划书后再下载', 'error')
        return redirect(url_for('main.invest_tidy'))
    idx = request.args.get('index', -1, type=int)
    entry = entries[idx] if 0 <= idx < len(entries) else entries[-1]
    out = entry.get('out')
    if not out or not os.path.exists(out):
        flash('汇总文件不存在，请重新上传解析', 'error')
        return redirect(url_for('main.invest_tidy'))
    try:
        base = os.path.splitext(entry['result']['source_file'])[0]
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        return send_file(out, as_attachment=True,
                         download_name=f'{base}_投入计划书汇总_{ts}.xlsx')
    except Exception as e:
        current_app.logger.error(f"汇总结果下载失败: {e}", exc_info=True)
        flash('下载失败，请重新上传解析', 'error')
        return redirect(url_for('main.invest_tidy'))


# ============================================================
# 兼容重定向：旧「投入计划书汇总」URL → 「投入计划书」
# ============================================================
@main_bp.route('/invest_summary', methods=['GET', 'POST'])
@login_required
def invest_summary_redirect():
    """旧页面 URL 重定向到「投入计划书」（保留参数）。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_invest_summary'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问投资汇总页面，权限不足")
        return forbidden_page('您没有投入计划书页面的访问权限')
    qs = request.query_string.decode('utf-8', 'ignore')
    return redirect(url_for('main.invest_tidy') + ('?' + qs if qs else ''))

@main_bp.route('/invest_summary/download')
@login_required
def invest_summary_download_redirect():
    """旧下载 URL 重定向到「投入计划书」下载（保留参数）。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_invest_summary'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问投资汇总下载，权限不足")
        return forbidden_page('您没有投入计划书页面的访问权限')
    qs = request.query_string.decode('utf-8', 'ignore')
    return redirect(url_for('main.invest_tidy_download') + ('?' + qs if qs else ''))


@main_bp.route('/invest_tidy/reupload', methods=['POST'])
@login_required
def invest_tidy_reupload():
    """单个文件重新上传：同名覆盖入库数据，并替换页面列表中的条目。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    old_name = request.form.get('old_name', '')
    upload = request.files.get('excelFile')
    if not upload or not upload.filename:
        flash('请选择新的Excel文件', 'error')
        return redirect(url_for('main.invest_tidy'))
    if not upload.filename.lower().endswith(('.xlsx', '.xlsm', '.xls')):
        flash('仅支持 .xlsx / .xlsm / .xls 格式', 'error')
        return redirect(url_for('main.invest_tidy'))
    try:
        from invest_summary_engine import parse_workbook, save_parsed_to_db, save_invest_source
        tmp_in = tempfile.NamedTemporaryFile(
            delete=False, suffix='.xlsx', prefix='invest_in_')
        tmp_in.close()
        try:
            upload.save(tmp_in.name)
            parsed = parse_workbook(tmp_in.name)
            parsed['source_file'] = upload.filename
            stats = save_parsed_to_db(parsed)   # 直接入库（同名覆盖 + LOT唯一查重）
            # 保存源文件到单独目录
            try:
                save_invest_source(tmp_in.name, upload.filename)
            except Exception as se:
                current_app.logger.warning(
                    f"投入源文件保存失败《{upload.filename}》: {se}")
        finally:
            if os.path.exists(tmp_in.name):
                try:
                    os.remove(tmp_in.name)
                except OSError:
                    pass
        msgs = [f"《{upload.filename}》已重新上传并覆盖入库（{stats['inserted']} 条）"]
        if stats['dup_lots']:
            msgs.append(f"跳过 {len(stats['dup_lots'])} 条重复LOT：" + '、'.join(stats['dup_lots'][:10]))
        if stats['in_dup_count']:
            msgs.append(f"文件内 {stats['in_dup_count']} 个重复LOT已合并")
        flash('；'.join(msgs), 'success' if not stats['dup_lots'] else 'warning')
    except Exception as e:
        current_app.logger.error(f"投入计划书重新上传失败: {e}", exc_info=True)
        flash(f'重新上传失败：{type(e).__name__}: {e}', 'error')
    return redirect(url_for('main.invest_tidy'))


@main_bp.route('/invest_tidy/delete', methods=['POST'])
@login_required
def invest_tidy_delete():
    """删除某文件的所有入库记录，并从页面列表移除。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    name = request.form.get('filename', '')
    if not name:
        flash('缺少文件名参数', 'error')
        return redirect(url_for('main.invest_tidy'))
    from models.invest_record import InvestmentRecord
    try:
        deleted = InvestmentRecord.query.filter_by(source_file=name).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        deleted = 0
        current_app.logger.error(f"投入计划书删除入库失败: {e}", exc_info=True)
    # 同步删除保存的源文件
    try:
        from invest_summary_engine import delete_invest_source
        delete_invest_source(name)
    except Exception as e:
        current_app.logger.warning(f"投入源文件删除失败《{name}》: {e}")
    # 页面列表改为每次从数据库实时读取，无需再维护会话条目
    flash(f'已删除《{name}》的入库数据（{deleted} 条记录）', 'success')
    return redirect(url_for('main.invest_tidy'))


# ============================================================
# 汽车厂装车实际（批量上传 SAP 导出 / 筛选汇总 / 导出 / 删除）
# ============================================================
@main_bp.route('/auto_loading', methods=['GET', 'POST'])
@login_required
def auto_loading():
    """汽车厂装车实际：上传 SAP 导出 Excel（「SAP 원본」Sheet），
    提取「고객 전일실적」列作为装车实际数量，明细入库长期保存，
    支持按 日期/车种/品目 筛选与导出。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_auto_loading'):
        current_app.logger.warning(
            f"用户 {session.get('username')} 尝试访问汽车厂装车实际页面，权限不足")
        return forbidden_page('您没有汽车厂装车实际页面的访问权限')

    # 上传权限单独校验（页面权限 ≠ 上传权限）
    can_upload = bool(session.get('is_admin')) or check_permission(
        session.get('user_id'), 'can_auto_loading_upload')

    if request.method == 'POST':
        if not can_upload:
            flash('您没有上传汽车厂装车实际数据的权限', 'error')
            return redirect(url_for('main.auto_loading'))
        files = [f for f in request.files.getlist('excelFiles') if f and f.filename]
        if not files:
            flash('请选择要上传的Excel文件（可多选）', 'error')
            return redirect(url_for('main.auto_loading'))
        bad = [f.filename for f in files
               if not f.filename.lower().endswith(('.xlsx', '.xlsm', '.xls'))]
        if bad:
            flash('仅支持 .xlsx / .xlsm / .xls 格式：' + '、'.join(bad), 'error')
            return redirect(url_for('main.auto_loading'))

        try:
            from auto_loading_engine import (
                parse_workbook, save_parsed_to_db, save_source,
                extract_date_from_filename
            )
            for excel_file in files:
                tmp_in = tempfile.NamedTemporaryFile(
                    delete=False, suffix='.xlsx', prefix='autoload_in_')
                tmp_in.close()
                try:
                    excel_file.save(tmp_in.name)
                    parsed = parse_workbook(tmp_in.name)
                    parsed['source_file'] = excel_file.filename
                    parsed['record_date'] = extract_date_from_filename(excel_file.filename)
                    if parsed.get('errors'):
                        for err in parsed['errors']:
                            flash(f"《{excel_file.filename}》{err['message']}", 'error')
                        continue
                    stats = save_parsed_to_db(parsed)
                    try:
                        save_source(tmp_in.name, excel_file.filename)
                    except Exception as se:
                        current_app.logger.warning(
                            f"装车实际源文件保存失败《{excel_file.filename}》: {se}")
                finally:
                    if os.path.exists(tmp_in.name):
                        try:
                            os.remove(tmp_in.name)
                        except OSError:
                            pass
                flash(f"《{excel_file.filename}》已入库 {stats['inserted']} 条"
                      f"（装车日期 {stats['record_date']}，"
                      f"装车实际合计 {stats['total_qty']:,}，"
                      f"解析耗时 {parsed.get('elapsed_ms', 0) / 1000:.2f}s）", 'success')
        except Exception as e:
            current_app.logger.error(f"汽车厂装车实际解析失败: {e}", exc_info=True)
            flash(f'解析失败：{type(e).__name__}: {e}', 'error')
        return redirect(url_for('main.auto_loading'))

    # GET：筛选 + 文件列表 + 上传日历
    from auto_loading_engine import (
        resolve_period, count_records, export_url, db_filter_options,
        available_months, upload_calendar, db_file_rows, db_totals
    )
    f = request.args
    today_str = datetime.now().strftime('%Y-%m-%d')
    period = resolve_period(f.get('scope', ''), f.get('month', ''), f.get('quarter', ''))
    date_from = period[0] if period else f.get('date_from', '')
    date_to = period[1] if period else f.get('date_to', '')
    display_date_from = date_from or today_str
    display_date_to = date_to or today_str
    filter_on = bool(f.get('filter') or f.get('keyword') or date_from or date_to
                     or f.get('vehicle') or f.get('item') or period)
    filter_count = None
    filter_export_url = ''
    filter_pivot_url = ''
    if filter_on:
        filter_count = count_records(date_from, date_to, f.get('vehicle', ''),
                                     f.get('item', ''), f.get('keyword', ''))
        filter_export_url = export_url(date_from, date_to, f.get('vehicle', ''),
                                       f.get('item', ''), f.get('keyword', ''))
        filter_pivot_url = export_url(date_from, date_to, f.get('vehicle', ''),
                                      f.get('item', ''), f.get('keyword', ''),
                                      fmt='pivot')
    calendar_data = upload_calendar(f.get('cy', type=int) or None,
                                    f.get('cm', type=int) or None)
    files = db_file_rows() or None
    totals = db_totals() or None
    return render_template('auto_loading.html',
                           files=files, totals=totals,
                           filter_count=filter_count,
                           filter_export_url=filter_export_url,
                           filter_options=db_filter_options(),
                           filter_values={
                               'date_from': display_date_from,
                               'date_to': display_date_to,
                               'vehicle': f.get('vehicle', ''),
                               'item': f.get('item', ''),
                               'keyword': f.get('keyword', ''),
                               'scope': f.get('scope', ''),
                               'month': f.get('month', ''),
                               'quarter': f.get('quarter', ''),
                           },
                           can_upload=can_upload,
                           can_delete=bool(session.get('is_admin'))
                           or check_permission(session.get('user_id'), 'can_auto_loading_delete'),
                           can_export=bool(session.get('is_admin'))
                           or check_permission(session.get('user_id'), 'can_auto_loading_export'),
                           calendar_data=calendar_data,
                           months=available_months())


@main_bp.route('/auto_loading/download')
@login_required
def auto_loading_download():
    """下载：?filtered=1 按筛选条件导出明细；?filename= 下载上传的原始源文件。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_auto_loading'):
        return forbidden_page('您没有汽车厂装车实际页面的访问权限')
    if not (bool(session.get('is_admin'))
            or check_permission(session.get('user_id'), 'can_auto_loading_export')):
        return forbidden_page('您没有汽车厂装车实际数据的导出权限')

    from flask import after_this_request
    f = request.args

    # 1) 筛选导出：按条件流式导出（format=pivot 横表 / 默认明细单表）
    if f.get('filtered'):
        try:
            from auto_loading_engine import (resolve_period, build_detail_xlsx,
                                             build_pivot_xlsx)
            period = resolve_period(f.get('scope', ''), f.get('month', ''),
                                    f.get('quarter', ''))
            date_from = period[0] if period else f.get('date_from', '')
            date_to = period[1] if period else f.get('date_to', '')
            builder = build_pivot_xlsx if f.get('format') == 'pivot' else build_detail_xlsx
            path, name = builder(
                date_from, date_to, f.get('vehicle', ''), f.get('item', ''),
                f.get('keyword', ''))

            @after_this_request
            def _cleanup_filter(resp):
                try:
                    os.remove(path)
                except OSError:
                    pass
                return resp

            return send_file(
                path, as_attachment=True, download_name=name,
                mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        except Exception as e:
            current_app.logger.error(f"装车实际筛选导出失败: {e}", exc_info=True)
            flash(f'下载失败：{type(e).__name__}: {e}', 'error')
            return redirect(url_for('main.auto_loading'))

    # 2) 按文件名下载上传的原始源文件
    filename = f.get('filename')
    if filename:
        try:
            from auto_loading_engine import get_source_path
            src_path = get_source_path(filename)
            if os.path.exists(src_path):
                return send_file(src_path, as_attachment=True, download_name=filename)
            flash('源文件不存在（可能已删除）', 'warning')
        except Exception as e:
            current_app.logger.warning(f"装车实际源文件读取失败《{filename}》: {e}")
            flash('源文件读取失败', 'error')
        return redirect(url_for('main.auto_loading'))

    flash('缺少下载参数', 'error')
    return redirect(url_for('main.auto_loading'))


@main_bp.route('/auto_loading/reupload', methods=['POST'])
@login_required
def auto_loading_reupload():
    """单个文件重新上传：同名覆盖入库数据。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not (bool(session.get('is_admin'))
            or check_permission(session.get('user_id'), 'can_auto_loading_upload')):
        flash('您没有上传汽车厂装车实际数据的权限', 'error')
        return redirect(url_for('main.auto_loading'))
    upload = request.files.get('excelFile')
    if not upload or not upload.filename:
        flash('请选择新的Excel文件', 'error')
        return redirect(url_for('main.auto_loading'))
    if not upload.filename.lower().endswith(('.xlsx', '.xlsm', '.xls')):
        flash('仅支持 .xlsx / .xlsm / .xls 格式', 'error')
        return redirect(url_for('main.auto_loading'))
    try:
        from auto_loading_engine import (
            parse_workbook, save_parsed_to_db, save_source, extract_date_from_filename
        )
        tmp_in = tempfile.NamedTemporaryFile(
            delete=False, suffix='.xlsx', prefix='autoload_in_')
        tmp_in.close()
        try:
            upload.save(tmp_in.name)
            parsed = parse_workbook(tmp_in.name)
            parsed['source_file'] = upload.filename
            parsed['record_date'] = extract_date_from_filename(upload.filename)
            if parsed.get('errors'):
                flash('；'.join(e['message'] for e in parsed['errors']), 'error')
                return redirect(url_for('main.auto_loading'))
            stats = save_parsed_to_db(parsed)
            try:
                save_source(tmp_in.name, upload.filename)
            except Exception as se:
                current_app.logger.warning(
                    f"装车实际源文件保存失败《{upload.filename}》: {se}")
        finally:
            if os.path.exists(tmp_in.name):
                try:
                    os.remove(tmp_in.name)
                except OSError:
                    pass
        flash(f"《{upload.filename}》已重新上传并覆盖入库（{stats['inserted']} 条，"
              f"装车日期 {stats['record_date']}，"
              f"解析耗时 {parsed.get('elapsed_ms', 0) / 1000:.2f}s）", 'success')
    except Exception as e:
        current_app.logger.error(f"汽车厂装车实际重新上传失败: {e}", exc_info=True)
        flash(f'重新上传失败：{type(e).__name__}: {e}', 'error')
    return redirect(url_for('main.auto_loading'))


@main_bp.route('/auto_loading/delete', methods=['POST'])
@login_required
def auto_loading_delete():
    """删除某文件的所有入库记录，并删除保存的源文件。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not (bool(session.get('is_admin'))
            or check_permission(session.get('user_id'), 'can_auto_loading_delete')):
        flash('您没有删除汽车厂装车实际数据的权限', 'error')
        return redirect(url_for('main.auto_loading'))
    name = request.form.get('filename', '')
    if not name:
        flash('缺少文件名参数', 'error')
        return redirect(url_for('main.auto_loading'))
    from models.auto_loading import AutoLoadingRecord
    try:
        deleted = AutoLoadingRecord.query.filter_by(source_file=name).delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        deleted = 0
        current_app.logger.error(f"装车实际删除入库失败: {e}", exc_info=True)
    try:
        from auto_loading_engine import delete_source
        delete_source(name)
    except Exception as e:
        current_app.logger.warning(f"装车实际源文件删除失败《{name}》: {e}")
    flash(f'已删除《{name}》的入库数据（{deleted} 条记录）', 'success')
    return redirect(url_for('main.auto_loading'))


# ============================================================
# 数据管理（批量上传 / 每日归档 / 查询下载 / 备份恢复）
# ============================================================
@main_bp.route('/data_management')
@login_required
def data_management():
    """数据管理页面：批量上传 / 每日记录查询 / 多格式导出 / 备份恢复。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_data_management'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问数据管理页面，权限不足")
        return forbidden_page('您没有数据管理页面的访问权限')
    try:
        from data_management_service import list_categories
        categories = list_categories()
    except Exception:
        categories = []
    backup_config = {}
    backup_history = []
    try:
        from auto_backup_service import load_config, load_history
        backup_config = load_config()
        backup_history = load_history()
    except Exception:
        pass
    return render_template('data_management.html', categories=categories,
                           backup_config=backup_config,
                           backup_history=backup_history)


@main_bp.route('/data_management/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def data_management_upload():
    """AJAX 单文件上传（前端支持多文件并发，逐个上传显示进度）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from data_management_service import (detect_file_type, parse_file,
                                             save_upload, build_upload_report)
        from datetime import datetime as _dt
        file = request.files.get('file')
        business_date = request.form.get('business_date', '').strip()
        category = request.form.get('category', '').strip()
        if not file or not file.filename:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        ftype = detect_file_type(file.filename)
        if not ftype:
            return jsonify({'success': False, 'error': '仅支持 CSV/Excel/JSON 文件'}), 400

        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=os.path.splitext(file.filename)[1])
        tmp.close()
        file.save(tmp.name)
        try:
            parsed_records, parsed_report = parse_file(tmp.name, ftype)
            batch, error_lines = save_upload(parsed_records, file.filename, ftype,
                                             fallback_date=business_date or None,
                                             fallback_category=category or None,
                                             uploaded_by=session.get('user_id'),
                                             uploaded_by_name=session.get('username'))
            report = build_upload_report(batch, parsed_records, parsed_report,
                                         error_lines)
            return jsonify({'success': True, 'report': report})
        finally:
            try:
                os.remove(tmp.name)
            except OSError:
                pass
    except Exception as e:
        current_app.logger.error(f"数据管理上传失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/records')
@login_required
@csrf_exempt_route
def data_management_records():
    """日期范围 / 类别 / 关键字查询（分页返回，供页面表格渲染）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    from data_management_service import query_records, list_categories, daily_summary
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    category = request.args.get('category', '').strip()
    keyword = request.args.get('keyword', '').strip()
    page = max(1, request.args.get('page', 1, type=int))
    per_page = max(5, min(request.args.get('per_page', 20, type=int), 200))
    try:
        items, total, page, per_page, columns = query_records(
            start, end, category, keyword, page, per_page)
        return jsonify({
            'success': True, 'items': items, 'total': total, 'page': page,
            'per_page': per_page, 'columns': columns,
            'categories': list_categories(), 'daily': daily_summary(start, end),
        })
    except Exception as e:
        current_app.logger.error(f"数据查询失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/export')
@login_required
@csrf_exempt_route
def data_management_export():
    """按当前筛选条件导出 CSV / Excel / JSON / PDF。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    from data_management_service import (export_csv, export_excel, export_json,
                                         export_pdf)
    from utils.time_utils import get_utc8_time
    fmt = request.args.get('format', 'excel')
    start = request.args.get('start', '').strip()
    end = request.args.get('end', '').strip()
    category = request.args.get('category', '').strip()
    keyword = request.args.get('keyword', '').strip()
    ext_map = {'csv': '.csv', 'excel': '.xlsx', 'json': '.json', 'pdf': '.pdf'}
    if fmt not in ext_map:
        fmt = 'excel'
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext_map[fmt])
        tmp.close()
        if fmt == 'csv':
            export_csv(start, end, category, keyword, tmp.name)
        elif fmt == 'excel':
            export_excel(start, end, category, keyword, tmp.name)
        elif fmt == 'json':
            export_json(start, end, category, keyword, tmp.name)
        else:
            ret = export_pdf(start, end, category, keyword, tmp.name)
            if ret.get('fallback'):
                import shutil
                tmp2 = tempfile.NamedTemporaryFile(delete=False, suffix='.html')
                tmp2.close()
                shutil.move(ret['html_path'], tmp2.name)
                os.remove(tmp.name)
                tmp.name = tmp2.name
        now = get_utc8_time()
        timestamp = now.strftime('%Y%m%d_%H%M%S')
        name = f"数据记录导出_{start or '全部'}_{end or '全部'}_{timestamp}{ext_map[fmt]}"
        return send_file(tmp.name, as_attachment=True, download_name=name)
    except Exception as e:
        current_app.logger.error(f"数据导出失败: {e}", exc_info=True)
        flash(f'导出失败：{e}', 'error')
        return redirect(url_for('main.data_management'))


@main_bp.route('/data_management/stats')
@login_required
@csrf_exempt_route
def data_management_db_stats():
    """返回数据库维护状态（文件大小 / 空闲页 / 可回收空间），供页面展示。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from db_maintenance import db_stats
        return jsonify({'success': True, 'stats': db_stats()})
    except Exception as e:
        current_app.logger.error(f"数据库状态查询失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/vacuum/start', methods=['POST'])
@login_required
@csrf_exempt_route
def data_management_vacuum_start():
    """手动触发空闲页回收（仅管理员）。

    回收可能耗时较久（取决于空闲页数量），改为后台任务 + 进度轮询：
    本接口启动任务并立即返回；前端轮询 /data_management/vacuum/progress 展示进度。
    """
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可回收'}), 403
    try:
        from db_maintenance import start_vacuum_async
        start_vacuum_async(current_app._get_current_object())
        current_app.logger.warning(
            f"手动触发空闲页回收 by {session.get('username')}")
        return jsonify({'success': True, 'started': True,
                        'message': '已开始回收空闲页，请查看进度'})
    except RuntimeError as e:   # 已有任务在跑等业务性提示，不作为服务器错误
        return jsonify({'success': False, 'error': str(e)}), 409
    except Exception as e:
        current_app.logger.error(f"空闲页回收启动失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/vacuum/progress')
@login_required
@csrf_exempt_route
def data_management_vacuum_progress():
    """返回空闲页回收进度（供前端轮询）。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限'}), 403
    try:
        from db_maintenance import get_vacuum_progress
        return jsonify({'success': True, 'progress': get_vacuum_progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/data_management/backup/download')
@login_required
def data_management_backup_download():
    """下载数据库备份快照（SQLite 在线备份）。

    长耗时（2GB+ 分钟级），改为后台任务 + 进度轮询：
    本接口启动任务并立即返回；前端轮询 /data_management/backup/download/progress
    展示进度，完成后跳转 /data_management/backup/download/result 下载。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from data_management_service import backup_database
        from utils.export_task import start_export_async
        from datetime import datetime as _dt
        name = f"数据备份_{_dt.now():%Y%m%d_%H%M%S}.db"

        def _worker(progress_cb, result_path):
            backup_database(result_path, progress_cb=progress_cb)

        start_export_async(current_app._get_current_object(), _worker, name,
                           tag='整库备份')
        current_app.logger.warning(
            f"开始整库备份 by {session.get('username')}")
        return jsonify({'success': True, 'started': True,
                        'message': '已开始生成备份快照，请查看进度'})
    except Exception as e:
        current_app.logger.error(f"备份启动失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/download/progress')
@login_required
def data_management_backup_download_progress():
    """返回整库备份进度（供前端轮询）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from utils.export_task import get_progress
        return jsonify({'success': True, 'progress': get_progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/data_management/backup/download/result')
@login_required
def data_management_backup_download_result():
    """下载已生成的备份快照；文件取走后立即清理。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from utils.export_task import get_result_file, clear_result
        path, name = get_result_file()
        if not path:
            return jsonify({'success': False, 'error': '备份文件尚未就绪或已过期'}), 404
        resp = send_file(path, as_attachment=True, download_name=name)
        # 文件已被读取到响应中，任务结束即可清理临时文件
        clear_result(delete_file=True)
        return resp
    except Exception as e:
        current_app.logger.error(f"备份下载失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/restore', methods=['POST'])
@login_required
@csrf_exempt_route
def data_management_backup_restore():
    """上传 .db 备份快照并恢复（仅管理员）。

    恢复为长耗时操作：上传完成后立即在后台线程执行，接口立刻返回，
    前端轮询 /data_management/backup/restore/progress 展示各阶段进度。
    """
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可恢复'}), 403
    file = request.files.get('snapshot')
    if not file or not file.filename.lower().endswith('.db'):
        return jsonify({'success': False, 'error': '请上传 .db 备份快照文件'}), 400
    try:
        from data_management_service import start_restore_async
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        tmp.close()
        file.save(tmp.name)
        # 交给后台线程执行，避免请求阻塞；异常（如已有任务在跑）直接反馈
        start_restore_async(tmp.name)
        current_app.logger.warning(f"开始从快照恢复数据库: {file.filename}, "
                                   f"by user {session.get('username')}")
        return jsonify({'success': True, 'started': True,
                        'message': '已开始恢复，正在后台执行，请查看进度'})
    except Exception as e:
        current_app.logger.error(f"恢复启动失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/restore/progress')
@login_required
@csrf_exempt_route
def data_management_backup_restore_progress():
    """返回数据库恢复进度（供前端轮询）。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限'}), 403
    try:
        from data_management_service import get_restore_progress
        return jsonify({'success': True, 'progress': get_restore_progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# 自动备份到网盘（配置 / 立即执行 / 历史）
# ============================================================
@main_bp.route('/data_management/backup/auto/config', methods=['GET', 'POST'])
@login_required
def data_management_auto_backup_config():
    """自动备份配置：GET 返回当前配置，POST 保存并重启调度判断。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    from auto_backup_service import load_config, save_config
    if request.method == 'POST':
        try:
            data = request.get_json(silent=True) or {}
            cfg = {
                'enabled': bool(data.get('enabled', False)),
                'target_dir': str(data.get('target_dir') or '').strip(),
                'mode': data.get('mode') or 'both',
                'full_time': data.get('full_time') or '21:30',
                'incr_interval': int(data.get('incr_interval') or 60),
                'keep_full': int(data.get('keep_full') or 30),
                'keep_incr': int(data.get('keep_incr') or 90),
            }
            save_config(cfg)
            return jsonify({'success': True, 'config': cfg})
        except Exception as e:
            current_app.logger.error(f"自动备份配置保存失败: {e}", exc_info=True)
            return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500
    return jsonify({'success': True, 'config': load_config()})


@main_bp.route('/data_management/backup/auto/run', methods=['POST'])
@login_required
def data_management_auto_backup_run():
    """立即执行一次备份（body: {"type":"full"|"incr"}，管理员）。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可执行'}), 403
    from auto_backup_service import run_full_backup, run_incremental_backup
    body = request.get_json(silent=True) or {}
    kind = (body.get('type') or 'full').strip().lower()
    try:
        if kind == 'full':
            entry = run_full_backup()
        elif kind == 'incr':
            entry = run_incremental_backup()
        else:
            return jsonify({'success': False, 'error': 'type 仅支持 full / incr'}), 400
        return jsonify({'success': True, 'entry': entry})
    except Exception as e:
        current_app.logger.error(f"立即备份失败({kind}): {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/auto/history')
@login_required
def data_management_auto_backup_history():
    """自动备份历史记录列表。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from auto_backup_service import load_history
        return jsonify({'success': True, 'history': load_history()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/data_management/backup/auto/restore-incr', methods=['POST'])
@login_required
def data_management_auto_backup_restore_incr():
    """增量恢复：上传 incr_*.xlsx 将三类增量数据重新写回数据库（仅管理员）。

    按唯一键去重：投入明细按 LOT（无 LOT 按 来源文件+行号），
    上传批次按 batch_no，数据记录按 来源文件+行号。
    """
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可恢复'}), 403
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': '请选择增量备份文件（incr_*.xlsx）'}), 400
    if not file.filename.lower().endswith('.xlsx'):
        return jsonify({'success': False, 'error': '仅支持 .xlsx 增量备份文件'}), 400
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    tmp.close()
    file.save(tmp.name)
    try:
        from auto_backup_service import restore_incr_file
        stat = restore_incr_file(tmp.name)
        current_app.logger.warning(
            f"增量数据已恢复: {file.filename} 投入明细+{stat.get('invest_added')}"
            f"/跳过{stat.get('invest_skipped')} 批次+{stat.get('batch_added')}"
            f"/跳过{stat.get('batch_skipped')} 数据记录+{stat.get('record_added')}"
            f"/跳过{stat.get('record_skipped')} by {session.get('username')}")
        return jsonify({'success': True, 'stat': stat})
    except Exception as e:
        current_app.logger.error(f"增量恢复失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass


# ============================================================
# 按功能模块 / 指定表导出数据库（仅管理员）
# ============================================================
@main_bp.route('/data_management/backup/export/tables')
@login_required
def data_management_export_tables_list():
    """返回可导出的功能模块清单与数据库全部表清单（供页面渲染）。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可导出'}), 403
    try:
        from utils.db_export import FUNCTION_TABLES, list_tables, EXPORT_FORMATS
        all_tables = []
        error = None
        try:
            all_tables = list_tables()
        except Exception as e:
            error = str(e)
        functions = {
            name: {'desc': cfg.get('desc', ''), 'tables': list(cfg.get('tables', []))}
            for name, cfg in FUNCTION_TABLES.items()
        }
        return jsonify({'success': True, 'functions': functions,
                        'tables': all_tables, 'formats': EXPORT_FORMATS,
                        'error': error})
    except Exception as e:
        current_app.logger.error(f"导出清单获取失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/export/run', methods=['POST'])
@login_required
def data_management_export_tables_run():
    """按功能模块或指定表导出：body {tables:[...] | function:str, format:db|xlsx|csv}。

    长耗时（大表数分钟），改为后台任务 + 进度轮询：
    本接口启动任务并立即返回；前端轮询 /data_management/backup/export/progress
    展示进度，完成后跳转 /data_management/backup/export/result 下载。
    """
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限，仅管理员可导出'}), 403
    from utils.db_export import export_tables, function_tables, _valid_tables
    from utils.export_task import start_export_async
    body = request.get_json(silent=True) or {}
    fmt = (body.get('format') or 'db').strip().lower()
    func = (body.get('function') or '').strip()
    tables = body.get('tables') or []
    if func:
        tables = function_tables(func)
        if not tables:
            return jsonify({'success': False, 'error': f'未知功能模块: {func}'}), 400
    tables = _valid_tables(tables)
    if not tables:
        return jsonify({'success': False, 'error': '未选择任何有效的表'}), 400
    ext_map = {'db': '.db', 'xlsx': '.xlsx', 'csv': '.zip'}
    if fmt not in ext_map:
        return jsonify({'success': False, 'error': 'format 仅支持 db / xlsx / csv'}), 400
    try:
        tag = func or ('多表' if len(tables) > 1 else tables[0])
        name = f"{tag}_{get_utc8_time():%Y%m%d_%H%M%S}{ext_map[fmt]}"

        def _worker(progress_cb, result_path):
            export_tables(tables, fmt, result_path, progress_cb=progress_cb)

        start_export_async(current_app._get_current_object(), _worker, name,
                           tag=tag)
        current_app.logger.warning(
            f"按表导出数据库(后台): {tag} format={fmt} tables={tables} "
            f"by {session.get('username')}")
        return jsonify({'success': True, 'started': True, 'tag': tag,
                        'message': '已开始导出，请查看进度'})
    except Exception as e:
        current_app.logger.error(f"按表导出失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/data_management/backup/export/progress')
@login_required
def data_management_export_tables_progress():
    """返回按表导出进度（供前端轮询）。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限'}), 403
    try:
        from utils.export_task import get_progress
        return jsonify({'success': True, 'progress': get_progress()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/data_management/backup/export/result')
@login_required
def data_management_export_tables_result():
    """下载按表导出结果；文件取走后立即清理。"""
    if 'user_id' not in session or not session.get('is_admin'):
        return jsonify({'success': False, 'error': '无权限'}), 403
    try:
        from utils.export_task import get_result_file, clear_result
        path, name = get_result_file()
        if not path:
            return jsonify({'success': False, 'error': '导出文件尚未就绪或已过期'}), 404
        resp = send_file(path, as_attachment=True, download_name=name)
        clear_result(delete_file=True)
        return resp
    except Exception as e:
        current_app.logger.error(f"导出下载失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


# ==================== 波纹管位置模块 ====================
# 页面1：登录页（上传/预览/编辑/列配置） 页面2：查询页（免登录模糊查询）


@main_bp.route('/corrugated_location')
@login_required
def corrugated_location():
    """波纹管位置登录页：上传窗口 + 模板下载 + 预览 + 编辑。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_corrugated_location'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问波纹管位置页面，权限不足")
        return forbidden_page('您没有波纹管位置页面的访问权限')
    try:
        from corrugated_location_service import (ensure_default_configs,
                                                list_all, get_column_configs,
                                                visible_columns)
        ensure_default_configs()
        preview = list_all()
        return render_template('corrugated_location.html',
                               preview=preview,
                               column_configs=get_column_configs(),
                               visible_columns=visible_columns())
    except Exception as e:
        current_app.logger.error(f"波纹管位置页面加载失败: {e}", exc_info=True)
        flash(f'波纹管位置页面加载失败：{e}', 'error')
        return redirect(url_for('main.data_management'))


@main_bp.route('/corrugated_location/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def corrugated_location_upload():
    """上传 Excel -> 解析返回预览（不落库，页面确认后保存）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    file = request.files.get('file')
    if not file or not file.filename:
        return jsonify({'success': False, 'error': '未选择文件'}), 400
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify({'success': False, 'error': '仅支持 Excel 文件(.xlsx/.xls)'}), 400
    try:
        from corrugated_location_service import parse_excel
        data = parse_excel(file.read(), file.filename)
        return jsonify({'success': True, 'metadata': {
            'file_name': file.filename,
            'columns': data['columns'], 'row_count': data['row_count'],
            'errors': len(data['errors']),
        }, 'records': data['records']})
    except Exception as e:
        current_app.logger.error(f"波纹管位置解析失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/corrugated_location/save', methods=['POST'])
@login_required
@csrf_exempt_route
def corrugated_location_save():
    """保存预览数据（全量覆盖旧数据），并同步列配置。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        body = request.get_json(silent=True) or {}
        columns = body.get('columns') or []
        rows = body.get('records') or []   # [{values:[...]}]
        file_name = body.get('file_name', '')
        if not columns:
            return jsonify({'success': False, 'error': '列信息为空'}), 400
        from corrugated_location_service import save_from_array, invalidate_query_cache
        value_rows = [r.get('values') if isinstance(r, dict) else r for r in rows]
        inserted = save_from_array(columns, value_rows, file_name)
        invalidate_query_cache()
        return jsonify({'success': True, 'inserted': inserted,
                        'message': f'已保存 {inserted} 条记录'})
    except Exception as e:
        current_app.logger.error(f"波纹管位置保存失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/corrugated_location/template')
@login_required
def corrugated_location_template():
    """下载官方 Excel 录入模板。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    try:
        from corrugated_location_service import build_template_workbook
        bio = build_template_workbook()
        return send_file(bio, as_attachment=True, download_name='波纹管位置录入模板.xlsx',
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"模板下载失败: {e}", exc_info=True)
        flash('模板生成失败，请稍后再试', 'error')
        return redirect(url_for('main.corrugated_location'))


@main_bp.route('/corrugated_location/edit', methods=['POST'])
@login_required
@csrf_exempt_route
def corrugated_location_edit():
    """手动编辑单行数据。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        body = request.get_json(silent=True) or {}
        row_id = body.get('id')
        data = body.get('data') or {}
        if not row_id:
            return jsonify({'success': False, 'error': '缺少记录ID'}), 400
        from corrugated_location_service import update_row, invalidate_query_cache
        ok, msg = update_row(int(row_id), data)
        if not ok:
            return jsonify({'success': False, 'error': msg}), 404
        invalidate_query_cache()
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        current_app.logger.error(f"波纹管位置编辑失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/corrugated_location/delete', methods=['POST'])
@login_required
@csrf_exempt_route
def corrugated_location_delete():
    """删除单行数据。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        body = request.get_json(silent=True) or {}
        row_id = body.get('id')
        if not row_id:
            return jsonify({'success': False, 'error': '缺少记录ID'}), 400
        from corrugated_location_service import delete_row, invalidate_query_cache
        ok, msg = delete_row(int(row_id))
        if not ok:
            return jsonify({'success': False, 'error': msg}), 404
        invalidate_query_cache()
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        current_app.logger.error(f"波纹管位置删除失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/corrugated_location/clear', methods=['POST'])
@login_required
@csrf_exempt_route
def corrugated_location_clear():
    """清空全部波纹管位置数据（需管理员确认后执行）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from corrugated_location_service import clear_all, invalidate_query_cache
        n = clear_all()
        invalidate_query_cache()
        return jsonify({'success': True, 'message': f'已清空 {n} 条记录'})
    except Exception as e:
        current_app.logger.error(f"波纹管位置清空失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/corrugated_location/columns', methods=['GET', 'POST'])
@login_required
@csrf_exempt_route
def corrugated_location_columns():
    """后台列配置：GET 返回当前配置；POST 保存（仅管理员）。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    try:
        from corrugated_location_service import get_column_configs, save_column_configs
        if request.method == 'GET':
            return jsonify({'success': True, 'configs': get_column_configs()})
        if not session.get('is_admin'):
            return jsonify({'success': False, 'error': '仅管理员可修改列配置'}), 403
        body = request.get_json(silent=True) or {}
        configs = save_column_configs(body.get('configs') or [])
        return jsonify({'success': True, 'configs': configs,
                        'message': '列配置已保存'})
    except Exception as e:
        current_app.logger.error(f"波纹管位置列配置失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


# 页面2：查询页（免登录）
@main_bp.route('/corrugated_location/search')
def corrugated_location_search():
    """波纹管位置查询页（无需登录即可访问）。"""
    try:
        from corrugated_location_service import (ensure_default_configs,
                                                visible_columns, list_all)
        ensure_default_configs()
        cols = visible_columns()
        total = list_all()['total']
        return render_template('corrugated_location_search.html',
                               total=total, columns=cols)
    except Exception as e:
        current_app.logger.error(f"波纹管位置查询页失败: {e}", exc_info=True)
        return render_template('error.html', error_code=500,
                               error_message=f"页面加载失败：{e}"), 500


@main_bp.route('/corrugated_location/search/api')
def corrugated_location_search_api():
    """查询页模糊查询 API（免登录，带缓存）。"""
    try:
        keyword = request.args.get('q', '').strip()
        page = max(1, request.args.get('page', 1, type=int))
        per_page = max(10, min(request.args.get('per_page', 20, type=int), 200))
        from corrugated_location_service import query_cached, visible_columns
        result = query_cached(keyword, page, per_page)
        result.pop('columns', None)  # 避免覆盖真实列配置
        return jsonify({'success': True, **result,
                        'columns': visible_columns()})
    except Exception as e:
        current_app.logger.error(f"波纹管位置查询失败: {e}", exc_info=True)
        return jsonify({'success': False, 'error': f'{type(e).__name__}: {e}'}), 500


@main_bp.route('/wire/lot_login')
@login_required
def wire_lot_login():
    """补线打端子 - LOT登录页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_wire_lot_login'):
        return render_template('error.html', error_code=403, error_message='访问被拒绝'), 403
    return render_template('wire_lot_login.html')


# LOT登录 Excel 列名映射（键为数据库字段，值为允许的列名，匹配时忽略空格/大小写）
LOT_LOGIN_COLUMN_ALIASES = {
    'vehicle_type': ['车种', '车型'],
    'item_name': ['ITEM名', 'ITEM', 'ITEMNAME'],
    'part_no': ['品番'],
    'jx_eo': ['京信EO', 'EO', 'JXEO'],
    'lot_no': ['LOTNO', 'LOT NO', 'LOT号', 'LOT'],
    'quantity': ['入荷数量', '数量'],
}


def _normalize_col_key(key):
    """列名规范化：去空格、转大写（中文保持不变）"""
    return str(key).replace(' ', '').replace('　', '').upper()


def _parse_lot_login_excel(file_path):
    """解析 LOT登录 Excel（纯读取，无 DB 操作）。

    必需列：LOT NO；可选列：车种、ITEM名、品番、京信EO、入荷数量。
    返回 (rows, missing_columns)，rows 为 dict 列表；缺 LOT NO 列时抛 ValueError。
    """
    # 别名表：规范化后的列名 -> 数据库字段
    alias_map = {}
    for field, aliases in LOT_LOGIN_COLUMN_ALIASES.items():
        for a in aliases:
            alias_map[_normalize_col_key(a)] = field

    ext = os.path.splitext(file_path)[1].lower()
    raw_rows = []  # 每行为 dict（原始列名 -> 值）
    if ext == '.xlsx':
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            headers = None
            for row in ws.iter_rows(values_only=True):
                if headers is None:
                    headers = [str(h).strip() if h is not None else '' for h in row]
                    continue
                row_obj = {}
                for i, v in enumerate(row):
                    row_obj[headers[i]] = v if v is not None else None
                raw_rows.append(row_obj)
        finally:
            wb.close()
    elif ext == '.xls':
        import xlrd
        book = xlrd.open_workbook(file_path)
        sheet = book.sheet_by_index(0)
        if sheet.nrows == 0:
            raise ValueError('空工作表')
        headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
        for r in range(1, sheet.nrows):
            row_obj = {}
            for c in range(sheet.ncols):
                key = headers[c]
                row_obj[key] = sheet.cell_value(r, c) if sheet.cell_value(r, c) != '' else None
            raw_rows.append(row_obj)
    else:
        raise ValueError('不支持的文件格式（仅支持 xls/xlsx）')

    # 列名映射
    col_map = {}  # 数据库字段 -> 原始列名
    if headers:
        for h in headers:
            field = alias_map.get(_normalize_col_key(h))
            if field and field not in col_map:
                col_map[field] = h
    if 'lot_no' not in col_map:
        raise ValueError('缺少必需列：LOT NO')

    rows = []
    missing_columns = []
    for raw in raw_rows:
        row = {}
        empty = True
        for field, col in col_map.items():
            val = raw.get(col)
            if val is None:
                row[field] = None
            else:
                val = str(val).strip()
                row[field] = val if val != '' else None
            if row[field]:
                empty = False
        if empty:
            continue  # 跳过全空行
        # 入荷数量转整数（兼容千分位逗号/空格）
        qty = row.get('quantity')
        if qty is not None:
            try:
                row['quantity'] = int(str(qty).replace(',', '').replace(' ', ''))
            except (ValueError, TypeError):
                row['quantity'] = None
        rows.append(row)
    return rows, missing_columns


def _check_part_eo_resources(part_no, eo):
    """核对"品番+EO"组合在资料库（file_info）中的资料情况。

    返回 dict：exists（组合是否存在）、checklist（SubList/UsageList 资料核对项）、
    other_eos（同品番已有的其他 EO，辅助定位输错 EO 的情况）。
    """
    from models.file_info import FileInfo
    from sqlalchemy import func as sa_func

    base = FileInfo.query.filter(
        FileInfo.first_subpno.isnot(None),
        FileInfo.first_subpno != ''
    )
    # 1) 精确匹配（走索引）
    files = base.filter(FileInfo.first_subpno == part_no, FileInfo.eo == eo).all()
    if not files:
        # 2) 大小写不敏感兜底
        files = base.filter(
            sa_func.lower(FileInfo.first_subpno) == part_no.lower(),
            sa_func.lower(FileInfo.eo) == eo.lower()
        ).all()

    def _file_brief(f):
        return {
            'filename': f.filename,
            'file_type': f.file_type,
            'upload_time': f.upload_time.strftime('%Y-%m-%d %H:%M') if f.upload_time else None,
            'is_frozen': bool(f.is_frozen),
        }

    checklist = []
    for ftype, label in [
        ('SubList', 'SubList 资料（子明细表）'),
        ('UsageList', 'UsageList 资料（用量明细表）'),
    ]:
        matched = [f for f in files if (f.file_type or '') == ftype]
        checklist.append({
            'file_type': ftype,
            'item': label,
            'exists': len(matched) > 0,
            'files': [_file_brief(f) for f in matched],
        })

    # 同品番其他 EO（仅精确匹配品番，数量有限）
    rows = FileInfo.query.filter(
        FileInfo.first_subpno == part_no,
        FileInfo.eo.isnot(None),
        FileInfo.eo != ''
    ).with_entities(FileInfo.eo).all()
    from utils.eo_utils import get_sorted_eos
    other_eos = get_sorted_eos(sorted({
        str(r[0]).strip() for r in rows
        if str(r[0]).strip() and str(r[0]).strip().lower() != eo.lower()
    }))
    # 最新版本 = 全部 EO（含当前 EO）按版本排序后的最后一个
    all_eos = get_sorted_eos(sorted({
        str(r[0]).strip() for r in rows if str(r[0]).strip()
    }))
    latest_eo = all_eos[-1] if all_eos else None

    return {
        'exists': len(files) > 0,
        'part_no': part_no,
        'eo': eo,
        'latest_eo': latest_eo,
        'checklist': checklist,
        'other_eos': other_eos,
    }


def _batch_resource_map(part_eo_pairs):
    """批量核对 (品番, EO) 组合是否在资料库（file_info）中存在。

    入参为可迭代的 (part_no, eo) 元组（允许为空值，自动跳过）。
    返回 set，元素为规范化（小写）后的 (part_no, eo) 二元组，表示已存在的组合。
    """
    pairs = set()
    for p, e in part_eo_pairs:
        p = str(p or '').strip()
        e = str(e or '').strip()
        if p and e:
            pairs.add((p.lower(), e.lower()))
    if not pairs:
        return set()
    existing_all = _get_file_resource_pairs_set()
    return pairs & existing_all


# file_info 中全部有效"品番+EO"组合（小写规范化）的缓存键与TTL
_FILE_PAIRS_CACHE_KEY = 'lot_login:file_resource_pairs'
_FILE_PAIRS_TTL = 120
_LOT_COUNT_PREFIX = 'lot_login:count:'
_LOT_PAGE_PREFIX = 'lot_login:page:'


def _get_file_resource_pairs_set():
    """返回 file_info 中全部有效 (品番, EO) 规范化（小写）集合。

    全表仅取两列（约3.6万行），构建集合后进程内缓存 30 秒，
    供 LOT 列表资料状态 O(1) 比对，避免每次列表请求重复 IN 查询。
    """
    from query_cache import query_cache_manager
    cached = query_cache_manager.get_list_cache(_FILE_PAIRS_CACHE_KEY)
    if cached is not None:
        return cached
    from models.file_info import FileInfo
    rows = FileInfo.query.filter(
        FileInfo.first_subpno.isnot(None),
        FileInfo.first_subpno != '',
        FileInfo.eo.isnot(None),
        FileInfo.eo != ''
    ).with_entities(FileInfo.first_subpno, FileInfo.eo).all()
    pairs = {(str(sp).strip().lower(), str(eo).strip().lower()) for sp, eo in rows if sp and eo}
    query_cache_manager.set_list_cache(_FILE_PAIRS_CACHE_KEY, pairs, _FILE_PAIRS_TTL)
    return pairs


def _invalidate_lot_caches(include_file_pairs=True):
    """LOT 数据变更后失效相关列表/计数缓存。

    include_file_pairs=True 时（文件上传/删除等资料库变更场景）一并清资料组合缓存；
    LOT 自身的增删只需清列表与计数。
    """
    from query_cache import query_cache_manager
    # LRUCache 无按前缀删除能力，列表缓存体量小，直接清空（与既有 invalidate 策略一致）
    query_cache_manager.invalidate_list_cache()
    if include_file_pairs:
        query_cache_manager._list_cache.delete(_FILE_PAIRS_CACHE_KEY)
    # LOT 数据变更会影响 EO 变更核对结果，一并失效，避免弹出陈旧提醒
    _invalidate_eo_check()


# LOT 列表默认仅拉取最近 N 条（提升首屏速度），用户可手动切换为全量分页
_LOT_DEFAULT_LIMIT = 50


def _serialize_lot_rows(rows):
    """轻量手工序列化 LOT 行（避免全量 ORM to_dict 与全字段加载）"""
    from utils.time_utils import format_utc8_time
    return [{
        'id': r.id,
        'vehicle_type': r.vehicle_type,
        'item_name': r.item_name,
        'part_no': r.part_no,
        'jx_eo': r.jx_eo,
        'lot_no': r.lot_no,
        'quantity': r.quantity,
        'operator': r.operator,
        'source': r.source,
        'remark': r.remark,
        'created_at': format_utc8_time(r.created_at) if r.created_at else None,
        'created_by': r.created_by,
    } for r in rows]


def _build_lot_result(result, rows, need_facets):
    """补全 records / resource_map（仅核对当前页组合，O(1) 集合比对）并按需附加 facets。

    result 为已填好分页元信息的 dict；rows 为当前页 ORM 行。
    """
    from models.lot_login_info import LotLoginInfo
    record_dicts = _serialize_lot_rows(rows)
    existing_pairs = _get_file_resource_pairs_set()
    resource_map = {}
    for r in record_dicts:
        p = str(r.get('part_no') or '').strip()
        e = str(r.get('jx_eo') or '').strip()
        if p and e:
            resource_map[f'{p}||{e}'] = (p.lower(), e.lower()) in existing_pairs
    result['records'] = record_dicts
    result['resource_map'] = resource_map

    if need_facets:
        # 车种下拉选项：仅取有值的车种并去重（单列 distinct，走索引，开销可控）
        vt_rows = LotLoginInfo.query.with_entities(LotLoginInfo.vehicle_type).filter(
            LotLoginInfo.vehicle_type.isnot(None),
            LotLoginInfo.vehicle_type != ''
        ).distinct().order_by(LotLoginInfo.vehicle_type).all()
        result['vehicle_types'] = [v[0] for v in vt_rows if v[0]]

    return result


@main_bp.route('/wire/lot_login/verify', methods=['POST'])
@login_required
def wire_lot_login_verify():
    """LOT 登录前核对：验证"品番+EO"组合是否存在于资料库"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        data = request.get_json(silent=True) or {}
        part_no = str(data.get('part_no') or '').strip()
        eo = str(data.get('jx_eo') or '').strip()
        if not part_no or not eo:
            return jsonify({'success': False, 'error': '品番和京信EO不能为空'}), 400
        result = _check_part_eo_resources(part_no, eo)
        result['success'] = True
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f'LOT登录资料核对失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/lot_login/data')
@login_required
def wire_lot_login_data():
    """LOT登录记录列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        from sqlalchemy import or_, func as sa_func
        from utils.time_utils import format_utc8_time
        from query_cache import query_cache_manager

        # ---------- 分页 / 筛选参数 ----------
        # limit 模式（默认）：仅取最近 N 套（默认50），不做全表 COUNT，首屏最快；
        # list 模式（limit=0）：原有全量分页行为，保留翻页能力。
        try:
            limit = int(request.args.get('limit', _LOT_DEFAULT_LIMIT))
        except (TypeError, ValueError):
            limit = _LOT_DEFAULT_LIMIT
        limit = max(0, min(limit, 500))  # 0 表示不做条数限制（list 模式）
        try:
            page = max(1, int(request.args.get('page', 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = int(request.args.get('per_page', _LOT_DEFAULT_LIMIT))
        except (TypeError, ValueError):
            per_page = _LOT_DEFAULT_LIMIT
        per_page = max(10, min(per_page, 500))
        q = str(request.args.get('q') or '').strip()
        only_no_data = request.args.get('only_no_data') in ('1', 'true', 'True')
        vehicle_type = str(request.args.get('vehicle_type') or '').strip()
        start_date = str(request.args.get('start_date') or '').strip()
        end_date = str(request.args.get('end_date') or '').strip()
        # facet 统计（车种下拉选项）仅在显式请求时计算，避免首屏额外开销
        need_facets = request.args.get('facets') in ('1', 'true', 'True')

        cache_key = (f'{_LOT_PAGE_PREFIX}{q.lower()}|{only_no_data}|{vehicle_type.lower()}|'
                     f'{start_date}|{end_date}|{page}|{per_page}|{limit}|{int(need_facets)}')

        cached = query_cache_manager.get_list_cache(cache_key)
        if cached is not None:
            return jsonify(cached)

        def _apply_common_filters(query):
            """按关键词 / 车种 / 登记日期区间过滤（供列表与 facet 复用）"""
            if q:
                like = f'%{q}%'
                query = query.filter(or_(
                    LotLoginInfo.lot_no.ilike(like),
                    LotLoginInfo.vehicle_type.ilike(like),
                    LotLoginInfo.item_name.ilike(like),
                    LotLoginInfo.part_no.ilike(like),
                    LotLoginInfo.jx_eo.ilike(like),
                    LotLoginInfo.operator.ilike(like),
                ))
            if vehicle_type:
                query = query.filter(LotLoginInfo.vehicle_type == vehicle_type)
            if start_date:
                try:
                    query = query.filter(LotLoginInfo.created_at >= _dt.strptime(start_date, '%Y-%m-%d'))
                except ValueError:
                    pass
            if end_date:
                try:
                    # 结束日期含当天：用 < 次日零点，避免时间部分被截断
                    end_exclusive = _dt.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                    query = query.filter(LotLoginInfo.created_at < end_exclusive)
                except ValueError:
                    pass
            return query

        base_q = _apply_common_filters(LotLoginInfo.query)

        def _finish(res, row_list):
            """组装 records/resource_map/facets 并写入短缓存（15秒）"""
            built = _build_lot_result(res, row_list, need_facets)
            query_cache_manager.set_list_cache(cache_key, built, 15)
            return jsonify(built)

        # 无资料筛选依赖 file_info 组合集合（内存比对）；有该筛选时统一走 id 集合路径
        if only_no_data:
            existing_pairs = _get_file_resource_pairs_set()
            tri = _apply_common_filters(
                LotLoginInfo.query.with_entities(
                    LotLoginInfo.id, LotLoginInfo.part_no, LotLoginInfo.jx_eo))
            no_data_ids = [
                rid for rid, p, e in tri.all()
                if p and e and (str(p).strip().lower(), str(e).strip().lower()) not in existing_pairs
            ]
            # id 自增近似时间倒序
            no_data_ids.sort(reverse=True)
            total = len(no_data_ids)
            # limit 模式：只取最近 limit 条参与分页，避免全表数据回填
            if limit:
                no_data_ids = no_data_ids[:limit]
            total_for_paging = len(no_data_ids)
            page_ids = no_data_ids[(page - 1) * per_page: page * per_page]
            rows = LotLoginInfo.query.filter(LotLoginInfo.id.in_(page_ids)) \
                .order_by(LotLoginInfo.id.desc()).all() if page_ids else []
        elif limit:
            # limit 模式（默认）：无 COUNT，只取最近 limit 条，单页返回；用多取 1 条判断有无更多
            rows = base_q.order_by(
                LotLoginInfo.created_at.desc(), LotLoginInfo.id.desc()
            ).limit(limit + 1).all()
            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]
            result = {
                'success': True,
                'records': [],
                'resource_map': {},
                'total': len(rows),
                'loaded': len(rows),
                'limited': True,
                'limit': limit,
                'has_more': has_more,
                'page': 1,
                'per_page': per_page,
                'total_pages': 1,
                'has_prev': False,
                'has_next': False,
            }
            return _finish(result, rows)
        else:
            # list 模式：原有全量分页行为（保持翻页能力）
            # 计数缓存（30秒），COUNT 走主键
            count_key = f'{_LOT_COUNT_PREFIX}{q.lower()}'
            total = query_cache_manager.get_count(count_key)
            if total is None:
                total = base_q.with_entities(sa_func.count(LotLoginInfo.id)).scalar() or 0
                query_cache_manager.set_count(count_key, total)
            rows = base_q.order_by(
                LotLoginInfo.created_at.desc(), LotLoginInfo.id.desc()
            ).limit(per_page).offset((page - 1) * per_page).all()
            total_for_paging = total

        total_pages = max(1, (total_for_paging + per_page - 1) // per_page)
        result = {
            'success': True,
            'records': [],
            'resource_map': {},
            'total': total_for_paging,
            'loaded': len(rows),
            'limited': False,
            'limit': 0,
            'has_more': page < total_pages,
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'has_prev': page > 1,
            'has_next': page < total_pages,
        }
        return _finish(result, rows)
    except Exception as e:
        current_app.logger.error(f'查询LOT登录列表失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/lot_login/add', methods=['POST'])
@login_required
def wire_lot_login_add():
    """手动登记 LOT"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login_add'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        data = request.get_json(silent=True) or {}
        lot_no = str(data.get('lot_no') or '').strip()
        if not lot_no:
            return jsonify({'success': False, 'error': 'LOT号不能为空'}), 400
        # 品番+EO 必填，且组合必须存在于资料库（双重校验，防止绕过前端）
        part_no = str(data.get('part_no') or '').strip()
        jx_eo = str(data.get('jx_eo') or '').strip()
        if not part_no or not jx_eo:
            return jsonify({'success': False, 'error': '品番和京信EO为LOT登录必填项'}), 400
        check = _check_part_eo_resources(part_no, jx_eo)
        if not check['exists']:
            return jsonify({
                'success': False,
                'error': f'品番 {part_no} + EO {jx_eo} 在资料库中不存在，禁止登记',
                'part_no': part_no, 'eo': jx_eo,
                'checklist': check['checklist'], 'other_eos': check['other_eos'],
                'latest_eo': check.get('latest_eo'),
            }), 400
        # LOT NO 唯一性检查
        existed = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
        if existed:
            return jsonify({'success': False, 'error': f'LOT号 {lot_no} 已登记', 'duplicate': True}), 409
        qty = data.get('quantity')
        try:
            qty = int(str(qty).replace(',', '').replace(' ', '')) if qty not in (None, '') else 0
        except (ValueError, TypeError):
            qty = 0
        rec = LotLoginInfo(
            vehicle_type=str(data.get('vehicle_type') or '').strip() or None,
            item_name=str(data.get('item_name') or '').strip() or None,
            part_no=str(data.get('part_no') or '').strip() or None,
            jx_eo=str(data.get('jx_eo') or '').strip() or None,
            lot_no=lot_no,
            quantity=qty,
            operator=str(data.get('operator') or '').strip() or None,
            source='manual',
            remark=str(data.get('remark') or '').strip() or None,
            created_by=session.get('user_id'),
            # 登记人姓名快照：账号删除后记录仍可显示文本
            created_by_name=session.get('username'),
        )
        db.session.add(rec)
        db.session.commit()
        _invalidate_lot_caches(include_file_pairs=False)
        return jsonify({'success': True, 'record': rec.to_dict()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'手动登记LOT失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/lot_login/clear', methods=['POST'])
@login_required
def wire_lot_login_clear():
    """清空全部 LOT 登录记录（谨慎操作，前端二次确认）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login_clear'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        cnt = db.session.query(LotLoginInfo).delete()
        db.session.commit()
        _invalidate_lot_caches(include_file_pairs=False)
        return jsonify({'success': True, 'deleted_count': cnt})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'清空LOT登录记录失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/lot_login/upload', methods=['POST'])
@login_required
def wire_lot_login_upload():
    """Excel 批量导入 LOT 登录数据（车种/ITEM名/品番/京信EO/LOT NO/入荷数量）"""
    import time
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login_upload'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    upload_dir = get_upload_dir('temp')  # 临时解析文件统一放入 temp 子目录
    try:
        from models.lot_login_info import LotLoginInfo
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '仅支持 xls/xlsx 文件'}), 400

        secure_name = secure_filename(file.filename)
        file_path = os.path.join(upload_dir, f'_lot_upload_{int(time.time())}_{secure_name}')
        file.save(file_path)
        try:
            rows, missing_columns = _parse_lot_login_excel(file_path)
        except Exception as e:
            return jsonify({'success': False, 'error': f'文件解析失败：{e}'}), 400
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass

        if not rows:
            return jsonify({'success': False, 'error': '文件中没有有效数据行'}), 400

        # 批量查重（一次查询）
        lot_nos = [r['lot_no'] for r in rows if r.get('lot_no')]
        existing = set()
        if lot_nos:
            try:
                existing = {r[0] for r in db.session.query(LotLoginInfo.lot_no).filter(
                    LotLoginInfo.lot_no.in_(lot_nos)).all()}
            except Exception:
                pass

        imported = 0
        duplicates = 0
        skipped_no_lot = 0
        errors = []
        to_import = []  # 待入库行（已去重、已跳过缺 LOT 号）
        user_id = session.get('user_id')
        for idx, r in enumerate(rows, start=2):  # 从第2行开始计数（第1行为表头）
            lot_no = r.get('lot_no')
            if not lot_no:
                skipped_no_lot += 1
                continue
            if lot_no in existing:
                duplicates += 1
                continue
            existing.add(lot_no)
            to_import.append(r)
            db.session.add(LotLoginInfo(
                vehicle_type=r.get('vehicle_type'),
                item_name=r.get('item_name'),
                part_no=r.get('part_no'),
                jx_eo=r.get('jx_eo'),
                lot_no=lot_no,
                quantity=r.get('quantity') or 0,
                source='excel',
                created_by=user_id,
                # 登记人姓名快照：账号删除后记录仍可显示文本
                created_by_name=session.get('username'),
            ))
            imported += 1

        # 批量核对"品番+EO"资料：无资料仅告警提示，不阻断导入（仍允许正常登记）
        existing_res = _batch_resource_map((r.get('part_no'), r.get('jx_eo')) for r in to_import)
        no_resource_rows = []
        for r in to_import:
            p = str(r.get('part_no') or '').strip()
            e = str(r.get('jx_eo') or '').strip()
            if p and e and (p.lower(), e.lower()) not in existing_res:
                no_resource_rows.append({'lot_no': r.get('lot_no'), 'part_no': p, 'jx_eo': e})
            elif not p or not e:
                no_resource_rows.append({
                    'lot_no': r.get('lot_no'), 'part_no': p or None, 'jx_eo': e or None,
                    'incomplete': True,
                })

        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            current_app.logger.error(f'LOT批量导入失败: {e}')
            return jsonify({'success': False, 'error': f'导入失败：{e}'}), 500
        _invalidate_lot_caches(include_file_pairs=False)

        return jsonify({
            'success': True,
            'total': len(rows),
            'imported_count': imported,
            'imported_lot_nos': [r.get('lot_no') for r in to_import],
            'duplicate_count': duplicates,
            'skipped_no_lot': skipped_no_lot,
            'errors': errors,
            'missing_columns': missing_columns,
            'no_resource_count': len(no_resource_rows),
            'no_resource_rows': no_resource_rows[:200],
            'no_resource_truncated': len(no_resource_rows) > 200,
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'LOT Excel导入失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


def _generate_lot_excel(records, existing_pairs):
    """将 LOT 登录记录生成为 xlsx 字节流（性能优化版）。

    records: 可迭代的记录对象，需支持属性访问（lot_no/vehicle_type/item_name/
             part_no/jx_eo/quantity/source/operator/remark/created_at）
    existing_pairs: 已有资料 (品番, EO) 小写规范化集合，用于判定资料状态

    性能要点：
    - write_only 流式写出：append 直接进入 XML 管线，无随机访问、内存占用低
    - with_entities 轻量行 + 仅对着色行构造样式单元格（普通行为纯 tuple）
    - 全部样式对象（Font/Alignment/PatternFill）只创建一次并跨单元格共享
    返回 (BytesIO, 数据行数)。
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook(write_only=True)
    ws = wb.create_sheet('LOT登录记录')
    # write_only 模式下列宽/冻结窗格需在写出行之前设置
    widths = [20, 12, 12, 16, 10, 18, 10, 10, 12, 20, 20]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = 'A2'

    # 共享样式（只建一次）：预先解析出 StyleArray，着色单元格直接赋 _style，
    # 跳过逐单元格的样式属性解析机制（1万着色行场景实测快约1.7倍）
    header_font = Font(bold=True, color='FFFFFF')
    header_fill = PatternFill('solid', fgColor='2E7D32')
    header_align = Alignment(horizontal='center', vertical='center')
    red_fill = PatternFill('solid', fgColor='FDE2E2')
    yellow_fill = PatternFill('solid', fgColor='FFF4D6')
    wrap_align = Alignment(vertical='center', wrap_text=True)

    def _styled_template(fill=None):
        c = WriteOnlyCell(ws, value='x')
        if fill is not None:
            c.fill = fill
        c.alignment = wrap_align
        return c

    _hdr_tmpl = WriteOnlyCell(ws, value='x')
    _hdr_tmpl.font = header_font
    _hdr_tmpl.fill = header_fill
    _hdr_tmpl.alignment = header_align
    header_style = _hdr_tmpl._style
    red_style = _styled_template(red_fill)._style
    yellow_style = _styled_template(yellow_fill)._style

    headers = ['LOT NO', '车种', 'ITEM名', '品番', '京信EO', '资料状态',
               '入荷数量', '来源', '操作员', '备注', '登记时间']
    header_cells = []
    for h in headers:
        c = WriteOnlyCell(ws, value=h)
        c._style = header_style
        header_cells.append(c)
    ws.append(header_cells)

    count = 0
    for r in records:
        p = str(r.part_no or '').strip()
        e = str(r.jx_eo or '').strip()
        if not p or not e:
            status, row_style = '待补充（品番或EO缺失）', yellow_style
        elif (p.lower(), e.lower()) in existing_pairs:
            status, row_style = '有资料', None
        else:
            status, row_style = '无资料', red_style
        values = [
            r.lot_no or '',
            r.vehicle_type or '',
            r.item_name or '',
            r.part_no or '',
            r.jx_eo or '',
            status,
            r.quantity if r.quantity is not None else '',
            'Excel导入' if r.source == 'excel' else '手动登录',
            r.operator or '',
            r.remark or '',
            r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else '',
        ]
        if row_style is None:
            ws.append(values)  # 纯 tuple，零样式开销
        else:
            cells = []
            for v in values:
                c = WriteOnlyCell(ws, value=v)
                c._style = row_style
                cells.append(c)
            ws.append(cells)
        count += 1

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output, count


@main_bp.route('/wire/lot_login/download')
@login_required
def wire_lot_login_download():
    """LOT 登录记录导出 Excel（含实时资料状态，便于核实）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_lot_login_download'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        from utils.time_utils import get_utc8_time

        # 只取导出需要的 10 列，避免全量 ORM 模型实例化
        records = LotLoginInfo.query.with_entities(
            LotLoginInfo.lot_no, LotLoginInfo.vehicle_type, LotLoginInfo.item_name,
            LotLoginInfo.part_no, LotLoginInfo.jx_eo, LotLoginInfo.quantity,
            LotLoginInfo.source, LotLoginInfo.operator, LotLoginInfo.remark,
            LotLoginInfo.created_at,
        ).order_by(
            LotLoginInfo.created_at.desc(), LotLoginInfo.id.desc()).all()
        existing_pairs = _batch_resource_map((r.part_no, r.jx_eo) for r in records)

        output, _ = _generate_lot_excel(records, existing_pairs)

        ts = get_utc8_time().strftime('%Y%m%d_%H%M%S')
        filename = f'LOT登录数据_{ts}.xlsx'
        return send_file(
            output, as_attachment=True, download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f'LOT登录记录导出失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry')
@login_required
def wire_scan_entry():
    """补线打端子 - 扫描录入页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry'):
        return render_template('error.html', error_code=403, error_message='访问被拒绝'), 403
    from models.system_setting import is_wire_scan_auto_same_drawing
    # 前端 pending 行需要当前操作人姓名（待保存时已记录，保存入库后复用）
    current_user_name = _wire_op_name(session.get('user_id'))
    return render_template('wire_scan_entry.html',
                           auto_same_drawing=is_wire_scan_auto_same_drawing(),
                           current_user_name=current_user_name or '')


# 扫描录入：LOT 固定长度（示例 Q817A03Q = 8 位，如长度变化只需修改此常量）
WIRE_LOT_LEN = 8


def _parse_wire_circuit(content):
    """解析回路号扫描内容：LOT(固定长度) + 01 + 回路号，示例 Q817A03Q01RS122。

    返回 (lot_no, circuit_no)，格式非法时抛 ValueError。
    """
    content = str(content or '').strip()
    if len(content) < WIRE_LOT_LEN + 2 + 1:
        raise ValueError(f'内容过短：应包含 LOT({WIRE_LOT_LEN}位)+01+回路号')
    lot_no = content[:WIRE_LOT_LEN]
    if content[WIRE_LOT_LEN:WIRE_LOT_LEN + 2] != '01':
        raise ValueError(f'格式错误：LOT后应为固定位01，实际为 {content[WIRE_LOT_LEN:WIRE_LOT_LEN + 2]}')
    circuit_no = content[WIRE_LOT_LEN + 2:]
    if not circuit_no:
        raise ValueError('回路号为空')
    return lot_no, circuit_no


def _parse_wire_machine(content):
    """解析号机扫描内容：LOT(固定长度) + 号机（含$），示例 Q817A03QM-35$139。

    返回 (lot_no, machine_no)，格式非法时抛 ValueError。
    """
    content = str(content or '').strip()
    if len(content) <= WIRE_LOT_LEN:
        raise ValueError(f'内容过短：应包含 LOT({WIRE_LOT_LEN}位)+号机')
    lot_no = content[:WIRE_LOT_LEN]
    machine_no = content[WIRE_LOT_LEN:]
    if '$' not in machine_no:
        raise ValueError('号机格式错误：应为 LOT+号机$编号（如 Q817A03QM-35$139）')
    return lot_no, machine_no


# ========================================================================
# 扫描资料文本快照：扫码拉取到 SubList 资料后立即以文本固化到 wire_scan_detail，
# 数据下载/导出直接读快照，不再实时条件查询 sub_list；
# 原始资料（sub_list / 文件）删除或冻结后，已扫描数据仍完整可查可下载。
# ========================================================================
WIRE_SNAPSHOT_ROW_CAP = 20   # 每回路最多固化的明细行数（与历史展示上限一致）
WIRE_HEAL_NEG_TTL = 300      # 无资料回路的自愈负缓存秒数（避免每次加载都全表扫描）
_wire_heal_neg_cache = {}    # (lot_no, 回路号大写) -> 负缓存过期时刻


def _wire_sublist_circuit_rows(part, eo, circuit):
    """按 品番+EO+回路号 实时拉取 SubList 明细（仅扫码/保存/自愈时调用）。

    顺序：活跃精确 → 品番/EO 精确存在则冻结精确（常见“回路无资料”场景，
    全部走复合索引）；仅当品番/EO 精确不存在（大小写/空格差异）时，
    才退化为 upper(trim()) 归一化全表兜底，避免 79 万行表的无谓全扫描。
    """
    from sqlalchemy import func, exists
    from models.sub_list import SubList
    part = (part or '').strip()
    eo = (eo or '').strip()
    cu = (circuit or '').strip().upper()
    if not (part and eo and cu):
        return []
    base_active = (SubList.deleted == False, SubList.is_frozen == False)
    base_any = (SubList.deleted == False,)

    def _query(bases, normalize=False):
        if normalize:
            key_cols = (func.upper(func.trim(SubList.SubPno)) == part.upper(),
                        func.upper(func.trim(SubList.EoKsc)) == eo.upper())
        else:
            key_cols = (SubList.SubPno == part, SubList.EoKsc == eo)
        return (SubList.query
                .filter(*key_cols,
                        func.upper(func.trim(SubList.Circuit)) == cu, *bases)
                .order_by(SubList.id)
                .limit(WIRE_SNAPSHOT_ROW_CAP)
                .all())

    def _part_eo_exists(bases):
        return db.session.query(
            exists().where(SubList.SubPno == part, SubList.EoKsc == eo, *bases)
        ).scalar()

    rows = _query(base_active)
    if rows:
        return rows
    # O(1) 闸门：该品番/EO 组合从未上传或已被删除 → 不可能有 sub_list 行，
    # 直接返回，避免后续 exists() 和归一化全表扫描（79 万行）
    try:
        if (part.lower(), eo.lower()) not in _get_file_resource_pairs_set():
            return []
    except Exception:
        pass
    # 组合在 file_info 中存在但活跃精确查询无果：
    # 用 base_any（仅 deleted=0，走索引 ~1ms）判断精确键是否存在，
    # 避免 base_active（含 is_frozen=0）触发全表扫描（~400ms）
    if _part_eo_exists(base_any):
        # 精确键存在（含冻结行）：查冻结精确（走索引）
        return _query(base_any)
    # 精确键不存在（大小写/空格差异或整品番冻结），才走归一化兜底
    rows = _query(base_active, normalize=True)
    if rows:
        return rows
    return _query(base_any, normalize=True)


def _wire_store_snapshot(lot_no, circuit_no, part, eo, sub_rows):
    """将拉取到的 SubList 行以纯文本替换固化到 wire_scan_detail（同事务内刷库）。

    性能要点：批量保存时本函数会在循环内被多次调用，因此
    - 不做 delete 后立即 flush（由外层事务统一刷库，减少写锁持有次数）；
    - 使用 bulk_insert_mappings 走 executemany，避免逐行 ORM 实例化开销。
    """
    from models.wire_scan_detail import WireScanDetail
    lot_no = (lot_no or '').strip()
    circuit_no = (circuit_no or '').strip() or None
    if not lot_no or not sub_rows:
        return 0
    WireScanDetail.query.filter(
        WireScanDetail.lot_no == lot_no,
        WireScanDetail.circuit_no == circuit_no
    ).delete(synchronize_session=False)
    part = (part or '').strip() or None
    eo = (eo or '').strip() or None
    rows = sub_rows[:WIRE_SNAPSHOT_ROW_CAP]
    # 一次性加载压高知识库候选（来源行 + 本批图纸编号知识行），
    # 固化时直接把真实 CCH 压高写入快照，避免先写行号再回改的双写开销
    set_codes = {(getattr(s, 'SetCode', None) or '').strip()
                 for s in rows if (getattr(s, 'SetCode', None) or '').strip()}
    source_rows, drawing_map = _wire_load_crimp_candidates(
        lot_no, circuit_no, set_codes)
    # bulk_insert_mappings 不触发 Python 侧 default，需显式写入时间戳
    from utils.time_utils import get_utc8_time
    now = get_utc8_time()

    def _resolve_strips(s):
        """解析单行压高：拍照知识库命中→真实 CCH；未命中→保留 sub_list 原值（行号）"""
        cr = _wire_pick_crimp(source_rows, drawing_map,
                              s.Tml1, s.Tml2,
                              (getattr(s, 'SetCode', None) or '').strip())
        return (cr[0] if cr and cr[0] else s.Strip1,
                cr[1] if cr and cr[1] else s.Strip2)

    mappings = []
    for seq, s in enumerate(rows):
        strip1, strip2 = _resolve_strips(s)
        mappings.append({
            'lot_no': lot_no, 'circuit_no': circuit_no,
            'part_no': part, 'jx_eo': eo,
            'set_code': (getattr(s, 'SetCode', None) or '').strip() or None,
            'seq': seq,
            'wire': s.Wire, 'color': s.Color, 'sq': s.SQ, 'length_cut2': s.Length_Cut2,
            'tml1': s.Tml1, 'tml2': s.Tml2, 'rel1': s.Rel1, 'rel2': s.Rel2,
            'seal1': s.Seal1, 'seal2': s.Seal2, 'strip1': strip1, 'strip2': strip2,
            'remark1': s.Remark1, 'is_frozen': bool(s.is_frozen),
            'created_at': now, 'updated_at': now,
        })
    if mappings:
        db.session.bulk_insert_mappings(WireScanDetail, mappings)
    return len(mappings)


def _wire_norm_tml(text):
    """端子号宽松归一化：大写去非字母数字，并压缩数字段前导零，
    使 'TKC12-06422' 与 OCR 可能的 'TKC12-6422' 视为同一端子。"""
    import re as _re
    norm = _re.sub(r'[^A-Z0-9]', '', str(text or '').upper())
    return _re.sub(r'0+(\d)', r'\1', norm)


def _wire_tml_key(t1, t2):
    return _wire_norm_tml(t1), _wire_norm_tml(t2)


def _wire_norm_sq(text):
    """线径规格归一化：使 Excel 里 '0.30' 与图纸里 '0.3' 视为同一规格。

    规则：去空白转大写；能转浮点时统一去掉无意义的尾随零（0.30 -> 0.3）；
    非纯数值（如 '0.50R'）仅做大小写/空白归一。
    """
    raw = str(text or '').strip().upper()
    if not raw:
        return ''
    try:
        return '{:g}'.format(float(raw))  # {:g} 自动去除尾随零：0.30 -> 0.3
    except (TypeError, ValueError):
        return raw


def _wire_crimp_has_value(row):
    return bool((row.strip1 or '').strip() or (row.strip2 or '').strip())


def _wire_load_crimp_candidates(lot_no, circuit_no, drawing_codes):
    """一次性加载压高知识库候选（批量保存循环内调用，禁止逐行查库）。

    返回 (source_rows, drawing_map)：
    - source_rows：该 LOT+回路「最近拍照来源」的知识行；
    - drawing_map：图纸管理编号 -> 知识行列表，供同图纸跨回路复用。
    """
    from models.wire_label_crimp import WireLabelCrimp
    from sqlalchemy import or_, and_
    lot_no = (lot_no or '').strip()
    circuit_no = (circuit_no or '').strip()
    drawing_codes = {d for d in (drawing_codes or set()) if d}
    conds = []
    if lot_no and circuit_no:
        conds.append(and_(WireLabelCrimp.lot_no == lot_no,
                          WireLabelCrimp.circuit_no == circuit_no))
    if drawing_codes:
        conds.append(WireLabelCrimp.drawing_mgmt_no.in_(list(drawing_codes)))
    if not conds:
        return [], {}
    all_rows = WireLabelCrimp.query.filter(or_(*conds)).all()
    source_rows, drawing_map = [], {}
    for r in all_rows:
        if lot_no and circuit_no and r.lot_no == lot_no and r.circuit_no == circuit_no:
            source_rows.append(r)
        if r.drawing_mgmt_no:
            drawing_map.setdefault(r.drawing_mgmt_no, []).append(r)
    return source_rows, drawing_map


def _wire_pick_crimp(source_rows, drawing_map, tml1, tml2, drawing):
    """按端子对挑选压高：来源行优先 → 同图纸知识行 → 单候选兜底。
    返回 (strip1, strip2) 或 None；空压高行跳过。"""
    key = _wire_tml_key(tml1, tml2)
    for pool in (source_rows, drawing_map.get(drawing or '', [])):
        for r in pool:
            if _wire_crimp_has_value(r) and _wire_tml_key(r.tml1, r.tml2) == key:
                return (r.strip1 or '').strip(), (r.strip2 or '').strip()
    # 单候选兜底：该作用域内仅有一条压高知识（同回路/同图纸唯一线材场景）
    fallback = source_rows or drawing_map.get(drawing or '', [])
    valid = [r for r in fallback if _wire_crimp_has_value(r)]
    if len(valid) == 1:
        r = valid[0]
        return (r.strip1 or '').strip(), (r.strip2 or '').strip()
    return None


def _wire_norm_machine(machine_no):
    """号机归一化：取 $ 编号前的主体（M-15$139 -> M-15），与上传表号机名对齐。"""
    return str(machine_no or '').split('$')[0].strip().upper()


def _wire_user_crimp_query_keys(detail_rows, machine_no):
    """根据扫描明细 + 号机，构造压高登陆匹配键列表。

    匹配键 = 回路号 + 号机(去$编号) + 线质 + 颜色 + 规格 + 端子(双端子组合)。
    明细每行自带 circuit_no/wire/color/sq/tml1/tml2；号机来自扫描记录行。
    返回 [{'circuit_no','machine_no','wire','color','sq','tml1','tml2'}, ...]。
    """
    norm_m = _wire_norm_machine(machine_no)
    keys = []
    for d in (detail_rows or []):
        t1 = _wire_norm_tml(d.get('tml1') if isinstance(d, dict) else getattr(d, 'tml1', None))
        t2 = _wire_norm_tml(d.get('tml2') if isinstance(d, dict) else getattr(d, 'tml2', None))
        if not (t1 and t2):
            continue
        keys.append({
            'circuit_no': str(d.get('circuit') if isinstance(d, dict) else getattr(d, 'circuit_no', '') or '').strip().upper(),
            'machine_no': norm_m,
            # 线质：匹配键之一，大小写归一
            'wire': str(d.get('wire') if isinstance(d, dict) else getattr(d, 'wire', '') or '').strip().upper(),
            'color': str(d.get('color') if isinstance(d, dict) else getattr(d, 'color', '') or '').strip(),
            # 规格归一化：图纸 '0.3' 与 Excel '0.30' 视为同一规格
            'sq': _wire_norm_sq(d.get('sq') if isinstance(d, dict) else getattr(d, 'sq', '')),
            'tml1': t1, 'tml2': t2,
        })
    return keys


def _wire_lookup_user_crimp(keys):
    """按匹配键批量查询用户上传维护的压高（wire_crimp_login）。

    :param keys: _wire_user_crimp_query_keys 构造的键列表
    :return: {(circuit_no, machine_no, wire, color, sq, tml1, tml2): (strip1, strip2)}
    """
    from models.wire_crimp_login import WireCrimpLogin
    if not keys:
        return {}
    # 同回路+号机+线质+颜色下候选集（规格与端子双侧归一化比对在 Python 侧完成，
    # 避免 SQL 侧 '0.30' 与 '0.3' 严格相等导致漏匹配）
    conds = []
    from sqlalchemy import or_
    for k in keys:
        conds.append(
            (WireCrimpLogin.circuit_no == k['circuit_no'])
            & (WireCrimpLogin.machine_no == k['machine_no'])
            & (WireCrimpLogin.wire == k['wire'])
            & (WireCrimpLogin.color == k['color'])
        )
    rows = WireCrimpLogin.query.filter(or_(*conds)).all() if conds else []
    result = {}
    for k in keys:
        for r in rows:
            if (r.circuit_no == k['circuit_no'] and r.machine_no == k['machine_no']
                    and (r.wire or '').strip().upper() == k['wire']
                    and r.color == k['color']
                    and _wire_norm_sq(r.sq) == k['sq']
                    and _wire_tml_key(r.tml1, r.tml2) == (k['tml1'], k['tml2'])):
                result[(k['circuit_no'], k['machine_no'], k['wire'], k['color'], k['sq'], k['tml1'], k['tml2'])] = (
                    r.strip1 or '', r.strip2 or '')
                break
    return result


def _wire_apply_label_crimp(lot_no, circuit_no):
    """把最近拍照来源行中的真实 CCH 压高应用到已固化快照行（同事务内）。

    用于拍照当时快照已存在的场景（扫码固化后再拍照）；快照重建走
    _wire_store_snapshot 已在插入时解析压高，无需调用本函数。
    匹配规则：端子对宽松匹配优先，快照仅一行时兜底。返回更新行数。
    """
    from models.wire_scan_detail import WireScanDetail
    lot_no = (lot_no or '').strip()
    circuit_no = (circuit_no or '').strip()
    if not lot_no or not circuit_no:
        return 0
    source_rows, _ = _wire_load_crimp_candidates(lot_no, circuit_no, set())
    source_rows = [r for r in source_rows if _wire_crimp_has_value(r)]
    if not source_rows:
        return 0
    rows = WireScanDetail.query.filter_by(
        lot_no=lot_no, circuit_no=circuit_no).all()
    if not rows:
        return 0
    updated = 0
    for ov in source_rows:
        k1, k2 = _wire_tml_key(ov.tml1, ov.tml2)
        matched = [r for r in rows if _wire_tml_key(r.tml1, r.tml2) == (k1, k2)]
        if not matched and len(rows) == 1:
            matched = rows
        for r in matched:
            # 标签/历史缺失的一侧不覆盖，保留原值
            if (ov.strip1 or '').strip():
                r.strip1 = ov.strip1
            if (ov.strip2 or '').strip():
                r.strip2 = ov.strip2
            updated += 1
    return updated


def _wire_register_or_reuse_crimp(sd, user_id):
    """处理一张标签识别结果的压高「登记 / 复用」（同事务，不自行 commit）。

    规则（用户确认）：
    - 本次识别到压高：按 (图纸管理编号, 端子对) upsert 知识行；单侧缺失时
      用同键历史记录补全（partial）；
    - 本次识别不到压高：不新增任何压高，仅在同图纸（或同 LOT+回路）存在
      端子对相同的历史记录时调取（reused），并把最近来源刷新为本次 LOT/回路；
    - 无历史可调取：返回 none，不写库；端子都未识别：no_terminal。

    :return: {'status', 'strip1', 'strip2', 'drawing', 'history_from'}
    """
    from models.wire_label_crimp import WireLabelCrimp
    lot = (sd.get('lot_no') or '').strip()
    circuit = (sd.get('circuit') or '').strip()
    tml1 = (sd.get('tml1') or '').strip()
    tml2 = (sd.get('tml2') or '').strip()
    drawing = (sd.get('drawing_mgmt_no') or '').strip() or None
    photo1 = (sd.get('strip1') or '').strip()
    photo2 = (sd.get('strip2') or '').strip()
    base = {'drawing': drawing, 'history_from': None}

    # 端子是压高归属，端子对都识别不到时无法定位/复用，直接放弃写库
    if not (tml1 or tml2):
        return dict(base, status='no_terminal', strip1='', strip2='')

    # 历史候选：图纸编号优先（跨 LOT 复用）；图纸缺失时退化为来源 LOT+回路
    if drawing:
        cands = WireLabelCrimp.query.filter_by(drawing_mgmt_no=drawing).all()
    else:
        cands = WireLabelCrimp.query.filter_by(
            lot_no=lot, circuit_no=circuit).all()
    key = _wire_tml_key(tml1, tml2)
    hist = next((r for r in cands if _wire_tml_key(r.tml1, r.tml2) == key), None)

    # —— 本次无压高：只调取，不新增 ——
    if not (photo1 or photo2):
        if hist and _wire_crimp_has_value(hist):
            history_from = {'lot_no': hist.lot_no, 'circuit_no': hist.circuit_no}
            hist.lot_no, hist.circuit_no = lot, circuit
            db.session.flush()
            return dict(base, status='reused',
                        strip1=(hist.strip1 or ''), strip2=(hist.strip2 or ''),
                        history_from=history_from)
        return dict(base, status='none', strip1='', strip2='')

    # —— 本次识别到压高：upsert；缺失侧用同键历史补全 ——
    if hist is None:
        hist = WireLabelCrimp(lot_no=lot, circuit_no=circuit,
                              drawing_mgmt_no=drawing,
                              tml1=tml1, tml2=tml2, created_by=user_id)
        db.session.add(hist)
    final1 = photo1 or (hist.strip1 or '')
    final2 = photo2 or (hist.strip2 or '')
    partial = (not photo1 and bool(final1)) or (not photo2 and bool(final2))
    hist.strip1, hist.strip2 = final1, final2
    # 最近来源刷新（同图纸跨 LOT 复用时，知识行跟随到最新拍照回路）
    hist.lot_no, hist.circuit_no = lot, circuit
    if not hist.drawing_mgmt_no and drawing:
        hist.drawing_mgmt_no = drawing
    hist.part_no = (sd.get('part_no') or '').strip() or None
    hist.jx_eo = (sd.get('jx_eo') or '').strip() or None
    hist.machine_no = (sd.get('machine_no') or '').strip() or None
    hist.cut_no = (sd.get('cut_no') or '').strip() or None
    db.session.flush()
    return dict(base, status='partial' if partial else 'stored',
                strip1=final1, strip2=final2)


def _wire_stamp_lot_records(lot_no, part, eo, vehicle):
    """把 LOT 登录关联信息（品番/EO/车种别）文本快照到该 LOT 的全部扫描记录。"""
    from models.wire_scan_record import WireScanRecord
    WireScanRecord.query.filter_by(lot_no=lot_no).update({
        'part_no': (part or '').strip() or None,
        'jx_eo': (eo or '').strip() or None,
        'vehicle_type': (vehicle or '').strip() or None,
    }, synchronize_session=False)


def _wire_snapshot_dicts(lot_no, circuit_no):
    """读取 (lot, circuit) 已固化的文本快照（按 seq 排序），返回 dict 列表。"""
    from models.wire_scan_detail import WireScanDetail
    if not lot_no or not circuit_no:
        return []
    rows = (WireScanDetail.query
            .filter(WireScanDetail.lot_no == lot_no,
                    WireScanDetail.circuit_no == (circuit_no or '').strip())
            .order_by(WireScanDetail.seq)
            .all())
    return [d.to_dict() for d in rows]


def _wire_snapshot_sibling_circuits(lot_no, exclude_circuit_up, limit=60, set_code=None):
    """从已固化的 wire_scan_detail 快照中取该 LOT 下同图纸的其他回路（兄弟回路）。

    用于 same_drawing 实时资料缺失（原始资料被删除/冻结）时的兜底：
    仅依据 LOT 快照，不查询 sub_list、不依赖 LOT 登录资料，
    保证「已拉取过的信息不丢失」。返回结构与 same_drawing.circuits 一致。

    关键：必须按图纸编号 SetCode 过滤，仅返回与当前回路同图纸的回路，
    避免把同 LOT 下不同图纸的回路误判为兄弟回路（导致大量无关数据）。
    - set_code 不为空时：按该 SetCode 精确过滤；
    - set_code 为空时：尝试从当前回路自身的快照行取 SetCode；
    - 仍无法确定 SetCode 时：返回空（宁可少返回，不可错返回）。
    """
    from models.wire_scan_detail import WireScanDetail
    from sqlalchemy import func
    lot_no = (lot_no or '').strip()
    if not lot_no:
        return []
    exclude_up = (exclude_circuit_up or '').strip().upper()

    # 确定用于过滤的 SetCode 集合（当前回路可能有多行，取全部非空 SetCode）
    target_set_codes = set()
    if set_code:
        target_set_codes.add(str(set_code).strip().upper())
    if not target_set_codes:
        cur_rows = (WireScanDetail.query
                    .filter(WireScanDetail.lot_no == lot_no,
                            func.upper(func.coalesce(WireScanDetail.circuit_no, '')) == exclude_up)
                    .all())
        for r in cur_rows:
            sc = (r.set_code or '').strip()
            if sc:
                target_set_codes.add(sc.upper())
    if not target_set_codes:
        # 无法确定当前回路的图纸编号，不能安全地判定兄弟回路，返回空
        return []

    rows = (WireScanDetail.query
            .filter(WireScanDetail.lot_no == lot_no)
            .order_by(WireScanDetail.circuit_no, WireScanDetail.seq)
            .all())
    # 先按回路号分组，记录每个回路的 SetCode 集合与首行明细
    circ_meta = {}  # circuit_up -> {'name': str, 'set_codes': set, 'first': row}
    for d in rows:
        c = (d.circuit_no or '').strip()
        if not c:
            continue
        c_up = c.upper()
        if c_up == exclude_up:
            continue
        meta = circ_meta.get(c_up)
        if meta is None:
            meta = {'name': c, 'set_codes': set(), 'first': d}
            circ_meta[c_up] = meta
        sc = (d.set_code or '').strip()
        if sc:
            meta['set_codes'].add(sc.upper())
    circuits = []
    for c_up, meta in circ_meta.items():
        # 仅当该回路的 SetCode 与当前回路有交集时，才视为同图纸兄弟回路
        if not (meta['set_codes'] & target_set_codes):
            continue
        d = meta['first']
        circuits.append({
            'circuit': meta['name'], 'wire': d.wire, 'color': d.color, 'sq': d.sq,
            'length_cut2': d.length_cut2, 'tml1': d.tml1, 'tml2': d.tml2,
            'strip1': d.strip1, 'strip2': d.strip2,
            'rel1': d.rel1, 'rel2': d.rel2,
            'seal1': d.seal1, 'seal2': d.seal2, 'remark1': d.remark1,
            'frozen': bool(d.is_frozen),
        })
        if len(circuits) >= limit:
            break
    return circuits


def _wire_ensure_snapshot(lot_no, circuit_no, lot_info_map=None, commit=True,
                          force_refresh=False, stamp=True):
    """确保 (lot, circuit) 的文本快照存在，返回快照明细 dict 列表（无资料为 []）。

    - 已有快照且未强制刷新：直接返回文本，不查 sub_list（资料删除后数据不丢）。
    - 无快照：按 LOT 登录信息实时拉取一次 sub_list 并以文本固化（保存兜底/历史自愈）。
    - LOT 已登录品番/EO 时同步把品番/EO/车种别快照到该 LOT 的扫描记录。

    stamp=False 用于 batch_save 这类会遍历多个回路的场景：盖章是按 LOT 的全量
    UPDATE，同 LOT 多回路时只需执行一次，由调用方去重后统一处理，避免重复写放大。
    """
    from models.lot_login_info import LotLoginInfo
    lot_no = (lot_no or '').strip()
    circuit_no = (circuit_no or '').strip()
    if not lot_no or not circuit_no:
        return []
    # 解析 LOT 登录信息（优先用调用方批量预取的 map）
    li = None
    if lot_info_map is not None:
        li = lot_info_map.get(lot_no)
    if li is None:
        lr = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
        if lr:
            li = {'part_no': lr.part_no, 'jx_eo': lr.jx_eo, 'vehicle_type': lr.vehicle_type}
    part = ((li or {}).get('part_no') or '').strip()
    eo = ((li or {}).get('jx_eo') or '').strip()
    vehicle = ((li or {}).get('vehicle_type') or '').strip()
    if li is not None and stamp:
        # 仅在 LOT 有登录信息时盖章，避免 LOT 登录被删后用空值覆盖历史快照
        _wire_stamp_lot_records(lot_no, part, eo, vehicle)
    if not force_refresh:
        existing = _wire_snapshot_dicts(lot_no, circuit_no)
        if existing:
            return existing
    if not (part and eo):
        if commit:
            db.session.commit()
        return []
    # 负缓存：近期已确认资料库中确无此回路时，短时间内不再重查
    import time
    neg_key = (lot_no, circuit_no.upper())
    neg_expire = _wire_heal_neg_cache.get(neg_key)
    if not force_refresh and neg_expire and neg_expire > time.time():
        if commit:
            db.session.commit()
        return []
    # 注意：本函数在 batch_save 的循环内被调用，此时必须复用外层事务，
    # 不能在此处 commit（否则会把外层尚未写完的批次提前提交，导致保存失败后无法回滚）。
    sub_rows = _wire_sublist_circuit_rows(part, eo, circuit_no)
    if sub_rows:
        _wire_store_snapshot(lot_no, circuit_no, part, eo, sub_rows)
    else:
        _wire_heal_neg_cache[neg_key] = time.time() + WIRE_HEAL_NEG_TTL
    if commit:
        db.session.commit()
    return _wire_snapshot_dicts(lot_no, circuit_no)


def _wire_build_lot_info(records, lot_nos):
    """组装 lot_no -> {part_no,jx_eo,vehicle_type}。

    优先用扫描记录上固化的文本快照（原始 LOT 登录删除后仍可显示），
    缺失时回退 lot_login_info 实时查询。
    """
    lot_info = {}
    missing_lots = set()
    for r in records:
        li = lot_info.get(r.lot_no)
        if li is None:
            if r.part_no or r.jx_eo or r.vehicle_type:
                li = {'part_no': r.part_no or '', 'jx_eo': r.jx_eo or '',
                      'vehicle_type': r.vehicle_type or ''}
                lot_info[r.lot_no] = li
            else:
                missing_lots.add(r.lot_no)
        else:
            # 同 LOT 多行快照字段互补（历史数据可能部分为空）
            if not li['part_no'] and r.part_no:
                li['part_no'] = r.part_no
            if not li['jx_eo'] and r.jx_eo:
                li['jx_eo'] = r.jx_eo
            if not li['vehicle_type'] and r.vehicle_type:
                li['vehicle_type'] = r.vehicle_type
    if missing_lots:
        from models.lot_login_info import LotLoginInfo
        for lr in LotLoginInfo.query.filter(LotLoginInfo.lot_no.in_(list(missing_lots))).all():
            lot_info[lr.lot_no] = {'part_no': lr.part_no or '', 'jx_eo': lr.jx_eo or '',
                                   'vehicle_type': lr.vehicle_type or ''}
    return lot_info


def _wire_load_snapshot_map(lot_info, lot_circuit, lazy_heal=True):
    """直接从 wire_scan_detail 文本表读取明细，返回 (subl, subl_by_lot)。

    - subl:        品番|EO|回路  -> [明细dict]（兼容旧取数键）
    - subl_by_lot: LOT|回路      -> [明细dict]（与品番/EO 无关的稳定键）

    关键点：subl_by_lot 仅依赖 LOT+回路号，不依赖 LOT 登录资料(part_no/jx_eo)。
    因此当 LOT 登录资料(lot_login_info)被删除、或记录上未盖章品番/EO 时，
    前端仍能用 LOT|回路 取到已固化的线束明细，保证「已拉取信息不丢失」。

    lazy_heal=True 时，对尚无快照的 (lot,回路) 实时拉取一次并固化（历史数据自愈），
    新流程数据在扫码/保存时已固化，正常请求零 sub_list 查询。
    """
    from models.wire_scan_detail import WireScanDetail
    subl = {}
    subl_by_lot = {}
    if not lot_circuit:
        return subl, subl_by_lot
    all_lots = list(lot_circuit.keys())
    snap_rows = (WireScanDetail.query
                 .filter(WireScanDetail.lot_no.in_(all_lots))
                 .order_by(WireScanDetail.lot_no, WireScanDetail.circuit_no,
                           WireScanDetail.seq)
                 .all())
    snap_group = {}
    for d in snap_rows:
        snap_group.setdefault((d.lot_no, (d.circuit_no or '').strip().upper()), []).append(d)

    def _put_key(part, eo, c_up, dicts):
        if not (part and eo and c_up):
            return
        subl[f'{part.strip().upper()}|{eo.strip().upper()}|{c_up}'] = dicts

    for lot_no, circs in lot_circuit.items():
        li = lot_info.get(lot_no) or {}
        part = (li.get('part_no') or '').strip()
        eo = (li.get('jx_eo') or '').strip()
        for circ in circs:
            c_up = circ.strip().upper()
            details = snap_group.get((lot_no, c_up))
            if not details and lazy_heal and part and eo:
                # 仅历史缺口/扫码时未固化成功的数据走一次实时拉取并固化。
                # 自愈属尽力而为：单组失败（如撞上资料上传写锁）不影响整体返回
                try:
                    healed = _wire_ensure_snapshot(lot_no, circ,
                                                   lot_info_map={lot_no: li}, commit=True)
                    details = healed or None
                except Exception as heal_err:
                    db.session.rollback()
                    current_app.logger.warning(
                        f'快照自愈失败(已跳过) {lot_no}/{circ}: {heal_err}')
            if not details:
                continue
            # details 有两种来源：
            # 1) snap_group 中的 WireScanDetail ORM 对象（需 to_dict 转换）
            # 2) lazy_heal 自愈时 _wire_ensure_snapshot 已返回的 dict 列表（不能再调 to_dict）
            # 统一归一化为 dict 列表，避免出现 'dict' object has no attribute 'to_dict'
            dicts = [d if isinstance(d, dict) else d.to_dict() for d in details]
            # 稳定键：仅 LOT+回路（不依赖登录资料，资料删除后仍可命中）
            subl_by_lot[f'{lot_no.strip()}|{c_up}'] = dicts
            # 主用键：当前 lot_info 的品番/EO（前端下载页/录入页取数键）
            _put_key(part, eo, c_up, dicts)
            # 兼容键：快照行内固化的品番/EO（LOT 登录后来变更过的场景）
            # details 来源可能是 ORM 对象（有 part_no 属性）或 dict（自愈路径，
            # to_dict 不含 part_no/jx_eo），用 getattr/get 安全取值避免 attribute 错误
            first = details[0]
            sp = str(getattr(first, 'part_no', None) or (first.get('part_no') if isinstance(first, dict) else '') or '').strip()
            se = str(getattr(first, 'jx_eo', None) or (first.get('jx_eo') if isinstance(first, dict) else '') or '').strip()
            if sp and se and (sp.upper() != part.upper() or se.upper() != eo.upper()):
                _put_key(sp, se, c_up, dicts)
    return subl, subl_by_lot


@main_bp.route('/wire/scan_entry/data')
@login_required
def wire_scan_entry_data():
    """扫描录入记录列表（按 LOT 分组，回路行在前）

    同时返回 LOT 关联信息：lot_no -> 品番(part_no)、京信EO(jx_eo)、车种别；
    线束明细直接读取扫码时固化的 wire_scan_detail 文本快照，
    不再实时条件查询 sub_list（速度快，且原始资料删除后数据不丢失）。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        # 性能优化：默认只加载最近3天的扫描数据；历史数据通过数据下载页面按日期查询
        from datetime import datetime, timedelta
        from sqlalchemy import func
        start = (request.args.get('start') or '').strip()
        end = (request.args.get('end') or '').strip()
        q = WireScanRecord.query
        # 用范围比较代替 func.date()，避免对列做函数运算导致 created_at 索引失效（全表扫描）
        if start:
            try:
                q = q.filter(WireScanRecord.created_at >= datetime.strptime(start, '%Y-%m-%d'))
            except ValueError:
                pass
        if end:
            try:
                q = q.filter(WireScanRecord.created_at < datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1))
            except ValueError:
                pass
        elif not start:
            # 未指定日期时默认最近 3 天
            q = q.filter(WireScanRecord.created_at >= datetime.utcnow() - timedelta(days=3))
        # 行数上限：默认窗口大时只取近期数据，防止全量查询拖慢接口
        #（上限 2000 行足够覆盖日扫码量；历史数据通过数据下载页按日期查询）
        # 排序按 id 倒序取最近 2000 条后再反转为升序，保证最终为「扫描进入顺序」
        records = (q
                   .order_by(WireScanRecord.id.desc())
                   .limit(2000)
                   .all())
        records.reverse()
        # LOT 关联信息：优先读扫描记录上固化的文本快照，缺失才回退 lot_login_info
        lot_nos = list({r.lot_no for r in records})
        lot_info = _wire_build_lot_info(records, lot_nos)
        # 每个 LOT 出现的全部回路号（回路行/号机行均可能带 circuit_no）
        lot_circuit = {}
        for r in records:
            if r.circuit_no:
                lot_circuit.setdefault(r.lot_no, set()).add(r.circuit_no.strip())
        # 线束明细：直接读 wire_scan_detail 文本快照；
        # 仅历史缺口（扫码时未固化）惰性实时拉取一次并补固化，新数据零 sub_list 查询
        subl, subl_by_lot = _wire_load_snapshot_map(lot_info, lot_circuit, lazy_heal=True)
        return jsonify({'success': True, 'records': [r.to_dict() for r in records],
                        'lot_info': lot_info, 'sublist': subl,
                        'sublist_by_lot': subl_by_lot})
    except Exception as e:
        current_app.logger.error(f'查询扫描录入列表失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


def _wire_op_name(user_id):
    """取扫描录入操作人姓名快照（full_name 优先，username 兜底；供写入时固化）。"""
    if not user_id:
        return None
    try:
        from models.user_info import UserInfo
        u = db.session.get(UserInfo, user_id)
        if not u:
            return None
        return (u.full_name or '').strip() or (u.username or '').strip() or None
    except Exception:
        return None


@main_bp.route('/wire/scan_entry/scan_circuit', methods=['POST'])
@login_required
def wire_scan_entry_scan_circuit():
    """第一次扫描：回路号识别，内容格式 LOT+01+回路号（示例 Q817A03Q01RS122）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_scan'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        data = request.get_json(silent=True) or {}
        content = str(data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'error': '扫描内容不能为空'}), 400
        try:
            lot_no, circuit_no = _parse_wire_circuit(content)
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400

        uid = session.get('user_id')
        op_name = _wire_op_name(uid)
        # 同 LOT 仅保留一条回路行：已存在则更新回路号（补齐姓名快照），否则新建
        rec = (WireScanRecord.query
               .filter_by(lot_no=lot_no, machine_no=None)
               .order_by(WireScanRecord.id)
               .first())
        if rec:
            rec.circuit_no = circuit_no
            if op_name and not rec.created_by_name:
                rec.created_by_name = op_name
        else:
            rec = WireScanRecord(lot_no=lot_no, circuit_no=circuit_no,
                                 created_by=uid, created_by_name=op_name)
            db.session.add(rec)
        # 合并：同步更新该 LOT 下所有号机行的回路号（保持同 LOT 信息一致）
        WireScanRecord.query.filter_by(lot_no=lot_no).filter(
            WireScanRecord.machine_no.isnot(None)
        ).update({'circuit_no': circuit_no}, synchronize_session=False)
        # 扫码即固化：把品番/EO/车种别快照到记录，并将该回路资料以文本存入快照表
        _wire_ensure_snapshot(lot_no, circuit_no, commit=False)
        db.session.commit()
        return jsonify({'success': True, 'record': rec.to_dict(),
                        'lot_no': lot_no, 'circuit_no': circuit_no})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'回路扫描保存失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/scan_machine', methods=['POST'])
@login_required
def wire_scan_entry_scan_machine():
    """第二次扫描：号机识别，内容格式 LOT+号机$编号（示例 Q817A03QM-35$139），补线数量默认0"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_scan'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        data = request.get_json(silent=True) or {}
        content = str(data.get('content') or '').strip()
        if not content:
            return jsonify({'success': False, 'error': '扫描内容不能为空'}), 400
        try:
            lot_no, machine_no = _parse_wire_machine(content)
        except ValueError as e:
            return jsonify({'success': False, 'error': str(e)}), 400

        # 号机允许重复录入，不再查重
        # 合并：号机行继承同 LOT 回路行的回路号（两次扫描合并为一条完整信息）
        from models.lot_login_info import LotLoginInfo
        circuit_row = (WireScanRecord.query
                       .filter_by(lot_no=lot_no, machine_no=None)
                       .order_by(WireScanRecord.id)
                       .first())
        li_row = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
        uid = session.get('user_id')
        op_name = _wire_op_name(uid)
        rec = WireScanRecord(lot_no=lot_no, circuit_no=circuit_row.circuit_no if circuit_row else None,
                             machine_no=machine_no, wire_qty=0,
                             part_no=(li_row.part_no if li_row else None),
                             jx_eo=(li_row.jx_eo if li_row else None),
                             vehicle_type=(li_row.vehicle_type if li_row else None),
                             created_by=uid, created_by_name=op_name)
        db.session.add(rec)
        # 保存即固化：品番/EO/车种别盖章 + 回路资料文本快照（缺失才实时拉取）
        if rec.circuit_no:
            _wire_ensure_snapshot(lot_no, rec.circuit_no, commit=False)
        db.session.commit()
        return jsonify({'success': True, 'record': rec.to_dict(),
                        'lot_no': lot_no, 'machine_no': machine_no})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'号机扫描保存失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/qty', methods=['POST'])
@login_required
def wire_scan_entry_qty():
    """修改补线数量"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_edit'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        data = request.get_json(silent=True) or {}
        rec_id = data.get('id')
        try:
            wire_qty = int(str(data.get('wire_qty') or 0).replace(',', '').replace(' ', ''))
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': '数量格式错误'}), 400
        if wire_qty <= 0:
            return jsonify({'success': False, 'error': '补线数量不能为0，请输入大于0的数值'}), 400
        rec = db.session.get(WireScanRecord, rec_id)
        if not rec:
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        rec.wire_qty = wire_qty
        db.session.commit()
        return jsonify({'success': True, 'record': rec.to_dict()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'修改补线数量失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/batch_save', methods=['POST'])
@login_required
def wire_scan_entry_batch_save():
    """批量保存扫描录入（回路号+号机配对后，统一提交补线数量）

    每项格式：{"lot_no": "...", "circuit_no": "...", "machine_no": "...", "wire_qty": 0}
    回路行（同 LOT, machine_no=None）若已存在则更新回路号，否则新建；
    号机行允许重复录入，每次直接新建。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_save'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        from models.lot_login_info import LotLoginInfo
        data = request.get_json(silent=True) or {}
        items = data.get('items') or []
        if not items:
            return jsonify({'success': False, 'error': '没有可保存的数据'}), 400
        # 解析每条 item 携带的扫描时间（前端扫描瞬间记录），用于按扫描顺序落库
        from datetime import datetime as _dt
        def _parse_scan_at(raw):
            """把前端 ISO 字符串解析为 datetime；解析失败返回 None（回退统一时间）"""
            s = str(raw or '').strip()
            if not s:
                return None
            try:
                # 兼容 '2026-10-12T08:30:15.123' 与带 Z/时区后缀的写法
                return _dt.fromisoformat(s.replace('Z', '+00:00')).replace(tzinfo=None)
            except (ValueError, TypeError):
                return None

        # 先解析并校验全部条目
        parsed = []
        for it in items:
            lot_no = str(it.get('lot_no') or '').strip()
            if not lot_no:
                continue
            circuit_no = str(it.get('circuit_no') or '').strip() or None
            machine_no = str(it.get('machine_no') or '').strip() or None
            try:
                wire_qty = int(str(it.get('wire_qty') or 0).replace(',', '').replace(' ', ''))
            except (ValueError, TypeError):
                wire_qty = 0

            # 无号机占位行不入库（仅前端扫描中间态，无实际数据，
            # 避免数据下载页重复显示同一回路）
            if not machine_no:
                continue

            # 数量不允许为0：拒绝保存
            if wire_qty <= 0:
                return jsonify({'success': False,
                                'error': f'补线数量不能为0（LOT {lot_no}，号机 {machine_no}），请输入大于0的数值'}), 400
            parsed.append((lot_no, circuit_no, machine_no, wire_qty, _parse_scan_at(it.get('scan_at'))))
        if not parsed:
            return jsonify({'success': False, 'error': '没有可保存的数据'}), 400

        # 批量预取 LOT 登录信息（品番/EO/车种别），保存时直接文本快照到每条记录
        lot_map = {}
        lot_nos = list({p[0] for p in parsed})
        for lr in LotLoginInfo.query.filter(LotLoginInfo.lot_no.in_(lot_nos)).all():
            lot_map[lr.lot_no] = {'part_no': lr.part_no, 'jx_eo': lr.jx_eo,
                                  'vehicle_type': lr.vehicle_type}
        # 号机行允许重复录入，每次直接新建；用 bulk_insert 走 executemany，
        # 避免大批量时逐条 ORM add 带来的实例化与 flush 开销
        # 注意：bulk_insert_mappings 不触发 Python 侧 default，需显式写入 created_at
        from utils.time_utils import get_utc8_time
        now = get_utc8_time()
        saved_pairs = set()
        mappings = []
        for lot_no, circuit_no, machine_no, wire_qty, scan_at in parsed:
            li = lot_map.get(lot_no) or {}
            mappings.append({
                'lot_no': lot_no,
                'circuit_no': circuit_no,
                'machine_no': machine_no,
                'wire_qty': wire_qty,
                'part_no': (li.get('part_no') or None),
                'jx_eo': (li.get('jx_eo') or None),
                'vehicle_type': (li.get('vehicle_type') or None),
                # 按扫描瞬间的时间落库（前端逐条记录），缺失时回退统一保存时间，
                # 保证同一次批量保存的 created_at 仍反映真实扫描先后顺序
                'created_at': scan_at or now,
                'created_by': session.get('user_id'),
            })
            saved_pairs.add((lot_no, circuit_no))
        db.session.bulk_insert_mappings(WireScanRecord, mappings)
        saved = len(mappings)
        # 保存即固化：涉及的每个 (LOT,回路) 确保资料文本快照存在，
        # 扫码时已固化的直接命中；缺失的此时实时拉取一次并固化（兜底保证）
        for lot_no, circuit_no in saved_pairs:
            if circuit_no:
                _wire_ensure_snapshot(lot_no, circuit_no,
                                      lot_info_map=lot_map, commit=False, stamp=False)
        # 盖章是按 LOT 的全量 UPDATE，按 LOT 去重后统一执行，避免同 LOT 多回路重复写
        for lot_no in {p[0] for p in parsed}:
            if lot_no in lot_map:
                _wire_stamp_lot_records(lot_no,
                                        (lot_map[lot_no].get('part_no') or '').strip(),
                                        (lot_map[lot_no].get('jx_eo') or '').strip(),
                                        (lot_map[lot_no].get('vehicle_type') or '').strip())
        db.session.commit()
        return jsonify({'success': True, 'saved': saved})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'批量保存扫描记录失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/same_drawing')
@login_required
def wire_scan_entry_same_drawing():
    """按 LOT 扫描后查询同一图纸编号下的所有其他回路

    图纸编号 = SetCode 完整值（后缀不减，如 CN8RF_IA142_4 保持原样）。
    返回同品番+EO 下、SetCode 完全相同的全部回路明细（排除当前已扫回路）。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        from sqlalchemy import func, exists
        from models.wire_scan_record import WireScanRecord
        from models.lot_login_info import LotLoginInfo
        from models.sub_list import SubList
        lot = (request.args.get('lot') or '').strip()
        circuit = (request.args.get('circuit') or '').strip()
        if not lot or not circuit:
            return jsonify({'success': False, 'error': '缺少参数'}), 400
        li = (LotLoginInfo.query.filter_by(lot_no=lot).first())
        if not li or not li.part_no or not li.jx_eo:
            # LOT登录资料已删除或未登记品番/EO：回退该 LOT 已固化的兄弟回路快照，
            # 保证「已拉取过的信息不丢失」（不查询 sub_list、不依赖登录资料）
            snap_circuits = _wire_snapshot_sibling_circuits(lot, circuit.upper())
            if snap_circuits:
                return jsonify({'success': True, 'circuits': snap_circuits,
                                'drawing': '', 'snapshot': True}), 200
            return jsonify({'success': False, 'error': 'LOT未登录品番/EO',
                            'reason': 'no_login'}), 200
        part = li.part_no.strip()
        eo = li.jx_eo.strip()
        cu = circuit.upper()
        # 找到当前回路的明细，取其 SetCode 图纸编号
        # 资料拉取：活跃精确 → 组合存在则冻结精确 → 归一化兜底（与 _wire_sublist_circuit_rows 同策略）
        # 性能：SubPno/EoKsc 精确等值可走复合索引（~0.3ms）；
        # 仅在精确键不存在（大小写/空格差异）时才退化为 upper(trim()) 全表扫描（~800ms）。
        base_active = (SubList.deleted == False, SubList.is_frozen == False)
        base_any = (SubList.deleted == False,)

        def _detail(bases, normalize=False):
            if normalize:
                key_cols = (func.upper(func.trim(SubList.SubPno)) == part.upper(),
                            func.upper(func.trim(SubList.EoKsc)) == eo.upper())
            else:
                key_cols = (SubList.SubPno == part, SubList.EoKsc == eo)
            return (SubList.query
                    .filter(*key_cols,
                            func.upper(func.trim(SubList.Circuit)) == cu, *bases)
                    .first())

        dtl = _detail(base_active)
        part_eo_exists = None
        if not dtl:
            # 精确键是否存在：用 base_any 走索引判断，避免 base_active 全表扫描
            part_eo_exists = db.session.query(
                exists().where(SubList.SubPno == part, SubList.EoKsc == eo, *base_any)
            ).scalar()
            dtl = _detail(base_any) if part_eo_exists else _detail(base_active, normalize=True)
            if not dtl:
                dtl = _detail(base_any, normalize=True)
        if not dtl or not dtl.SetCode:
            return jsonify({'success': True, 'circuits': [], 'drawing': ''})
        drawing = (dtl.SetCode or '').strip()
        if not drawing:
            return jsonify({'success': True, 'circuits': [], 'drawing': drawing})
        # 同图纸 = SetCode 完整值完全相等（后缀不去掉），活跃优先，冻结兜底。
        # SubPno/EoKsc 用精确等值命中复合索引，仅 SetCode/Circuit 保留归一化（列基数低，开销可控）。
        du = drawing.upper()

        def _same_rows(bases):
            return (SubList.query
                    .filter(SubList.SubPno == part, SubList.EoKsc == eo,
                            func.upper(func.trim(SubList.SetCode)) == du,
                            func.upper(func.trim(SubList.Circuit)) != cu, *bases)
                    .order_by(SubList.Circuit)
                    .limit(60)
                    .all())

        rows = _same_rows(base_active)
        if not rows:
            rows = _same_rows(base_any)
        # 精确键不存在（大小写/空格差异）时才走归一化兜底
        if not rows and part_eo_exists is False:
            rows = (SubList.query
                    .filter(func.upper(func.trim(SubList.SubPno)) == part.upper(),
                            func.upper(func.trim(SubList.EoKsc)) == eo.upper(),
                            func.upper(func.trim(SubList.SetCode)) == du,
                            func.upper(func.trim(SubList.Circuit)) != cu, *base_active)
                    .order_by(SubList.Circuit).limit(60).all())
            if not rows:
                rows = (SubList.query
                        .filter(func.upper(func.trim(SubList.SubPno)) == part.upper(),
                                func.upper(func.trim(SubList.EoKsc)) == eo.upper(),
                                func.upper(func.trim(SubList.SetCode)) == du,
                                func.upper(func.trim(SubList.Circuit)) != cu, *base_any)
                        .order_by(SubList.Circuit).limit(60).all())
        # 实时无资料（原始资料已删除/冻结等）：回退该 LOT 下已固化的兄弟回路快照，
        # 保证「已拉取过的信息不丢失」，同一图纸的兄弟回路仍能自动带入。
        # 必须传入当前回路的 SetCode(drawing)，确保只返回同图纸回路，避免无关数据。
        if not rows:
            snap_circuits = _wire_snapshot_sibling_circuits(lot, cu, set_code=drawing)
            if snap_circuits:
                return jsonify({'success': True, 'circuits': snap_circuits,
                                'drawing': drawing, 'snapshot': True})
        circuits = []
        seen = set()
        for s in rows:
            c = (s.Circuit or '').strip()
            if not c or c in seen:
                continue
            seen.add(c)
            circuits.append({
                'circuit': c, 'wire': s.Wire, 'color': s.Color, 'sq': s.SQ,
                'length_cut2': s.Length_Cut2, 'tml1': s.Tml1, 'tml2': s.Tml2,
                'strip1': s.Strip1, 'strip2': s.Strip2,
                'rel1': s.Rel1, 'rel2': s.Rel2,
                'seal1': s.Seal1, 'seal2': s.Seal2, 'remark1': s.Remark1,
                'frozen': bool(s.is_frozen)
            })
        return jsonify({'success': True, 'circuits': circuits, 'drawing': drawing})
    except Exception as e:
        current_app.logger.error(f'查询同图纸回路失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/circuit_detail')
@login_required
def wire_scan_entry_circuit_detail():
    """扫码后立即拉取资料：按 LOT+回路号 即时返回该回路的 SubList 明细

    扫码枪扫完回路号（或号机配对）后前端立即调用，不等数量填写完成。
    拉取逻辑：活跃优先，活跃缺失时回退到包含冻结状态的资料，保证冻结资料也能即时拉取。
    拉到的资料立即以纯文本固化到 wire_scan_detail（扫码即存储），
    原始资料后续被删除时，仍可返回已固化的快照数据（snapshot=True）。
    返回格式与 data 路由的 sublist 条目一致（前端可直接合并进 sublistMap）。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_scan'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        lot = (request.args.get('lot') or '').strip()
        circuit = (request.args.get('circuit') or '').strip()
        if not lot or not circuit:
            return jsonify({'success': False, 'error': '缺少参数'}), 400
        li = LotLoginInfo.query.filter_by(lot_no=lot).first()
        if not li:
            # LOT登录资料已被删除：若该回路已有固化快照，仍返回快照（数据不丢失），
            # 品番/EO 取快照行内固化的值
            existing = _wire_snapshot_dicts(lot, circuit)
            if existing:
                return jsonify({'success': True, 'detail': existing, 'snapshot': True,
                                'part_no': existing[0].get('part_no') or '',
                                'jx_eo': existing[0].get('jx_eo') or ''}), 200
            return jsonify({'success': False, 'error': 'LOT未登录', 'reason': 'no_login'}), 200
        part = (li.part_no or '').strip()
        eo_val = (li.jx_eo or '').strip()
        if not part or not eo_val:
            # LOT已登录但未登记品番/EO：若已有快照仍返回，避免已拉取信息消失
            existing = _wire_snapshot_dicts(lot, circuit)
            if existing:
                return jsonify({'success': True, 'detail': existing, 'snapshot': True,
                                'part_no': existing[0].get('part_no') or '',
                                'jx_eo': existing[0].get('jx_eo') or ''}), 200
            return jsonify({'success': False, 'error': 'LOT未登记品番/EO', 'reason': 'no_login'}), 200
        # 品番/EO/车种别同步盖章到该 LOT 已有扫描记录
        _wire_stamp_lot_records(lot, part, eo_val, li.vehicle_type or '')
        # 实时拉取（活跃→归一化→冻结兜底，最多20行）
        rows = _wire_sublist_circuit_rows(part, eo_val, circuit)
        if rows:
            # 扫码即存储：刷新固化为文本快照
            _wire_store_snapshot(lot, circuit, part, eo_val, rows)
            db.session.commit()
            detail = _wire_snapshot_dicts(lot, circuit)
            return jsonify({'success': True, 'detail': detail,
                            'part_no': part, 'jx_eo': eo_val})
        # 实时无资料：若历史已固化过快照（如原始资料已被删除），仍返回快照保证数据不丢
        existing = _wire_snapshot_dicts(lot, circuit)
        if existing:
            return jsonify({'success': True, 'detail': existing, 'snapshot': True,
                            'part_no': part, 'jx_eo': eo_val})
        return jsonify({'success': True, 'not_found': True, 'part_no': part, 'jx_eo': eo_val})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'扫码即时拉取资料失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/label_photo', methods=['POST'])
@login_required
def wire_scan_entry_label_photo():
    """拍照识别线束标签：离线识别二维码/文字，登记或调取真实 CCH 压高

    sub_list.Strip1/Strip2 存的是标签行号锚点（4.1/4.3），真实压高只在标签上。
    压高按 (图纸管理编号, 端子对) 存入 wire_label_crimp 知识库：
    - 本次识别到压高：登记/更新知识，并回写已固化快照（单侧缺失可用历史补全）；
    - 本次识别不到压高：不新增，自动调取同图纸同端子对的历史压高回写；
    - 无历史可调取：不添加压高，仅回传识别结果。
    扫码固化新快照时由 _wire_store_snapshot 自动应用知识库（支持同图纸跨回路）。

    请求：multipart/form-data，字段 photo（标签照片，≤15MB）
    返回：{'success', 'scan_detail', 'codes', 'applied', 'overridden',
           'crimp_source': stored|partial|reused|none|no_terminal,
           'history_from', 'part_no', 'jx_eo', 'errors'}
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_scan'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.lot_login_info import LotLoginInfo
        from utils.image_code_reader import read_label

        photo = request.files.get('photo')
        if not photo or not photo.filename:
            return jsonify({'success': False, 'error': '未收到照片'}), 400
        img_bytes = photo.read()
        if not img_bytes:
            return jsonify({'success': False, 'error': '照片内容为空'}), 400
        # 大小护栏：手机直出照片一般 <15MB，超限拒收避免 OCR 长时间占用
        if len(img_bytes) > 15 * 1024 * 1024:
            return jsonify({'success': False, 'error': '照片超过 15MB，请压缩后重试'}), 400

        result = read_label(img_bytes)
        sd = result.get('scan_detail') or {}
        codes = [c.get('text') for c in (result.get('codes') or []) if c.get('text')]
        resp = {'success': True, 'scan_detail': sd, 'codes': codes,
                'applied': 0, 'overridden': False,
                'crimp_source': 'none', 'history_from': None,
                'part_no': '', 'jx_eo': '',
                'errors': result.get('errors') or []}
        # 码识别/OCR 任一能力不可用（多为服务解释器缺依赖）必须留痕，
        # 否则前端只会看到“全部未识别”，无法定位是照片问题还是环境问题
        if resp['errors']:
            current_app.logger.warning(
                '标签拍照识别能力降级: %s',
                '; '.join('%s: %s' % (e.get('stage'), e.get('message'))
                          for e in resp['errors']))

        lot_no = (sd.get('lot_no') or '').strip()
        circuit = (sd.get('circuit') or '').strip()
        # LOT 登录信息顺带返回，前端可补齐品番/EO 显示
        if lot_no:
            li = LotLoginInfo.query.filter_by(lot_no=lot_no).first()
            if li:
                resp['part_no'] = (li.part_no or '').strip()
                resp['jx_eo'] = (li.jx_eo or '').strip()

        # LOT/回路缺失无法定位归属，仅回传识别结果，不登记也不复用
        if not lot_no or not circuit:
            resp['message'] = '未识别到 LOT/回路号，未回写压高'
            return jsonify(resp)

        # 登记（本次识别）或复用（同图纸历史）；同事务内完成，不自行 commit
        outcome = _wire_register_or_reuse_crimp(sd, session.get('user_id'))
        status = outcome['status']
        resp['crimp_source'] = status
        resp['history_from'] = outcome.get('history_from')

        if status in ('stored', 'partial', 'reused'):
            # 最终生效压高回写响应体（reused/partial 时与本次照片识别值不同）
            sd['strip1'] = outcome['strip1']
            sd['strip2'] = outcome['strip2']
            # 立即应用到已固化快照；无快照则 applied=0，扫码固化时自动应用
            resp['applied'] = _wire_apply_label_crimp(lot_no, circuit)
            resp['overridden'] = True
        elif status == 'no_terminal':
            resp['message'] = '未识别到端子信息，无法定位压高归属，未添加压高'
        else:
            resp['message'] = '本次未识别到 CCH 压高，且同图纸下无历史记录可调取，未添加压高'
        db.session.commit()
        return jsonify(resp)
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'标签拍照识别失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


# ========================================================================
# 压高登陆：用户经 Excel 维护压高，扫描录入页按匹配键拉取显示
# 匹配键 = 回路号 + 号机(去$编号) + 颜色 + 规格 + 端子(双端子组合)
# ========================================================================

@main_bp.route('/wire/crimp_login')
@login_required
def wire_crimp_login():
    """压高登陆 - Excel 上传维护页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_wire_crimp_login'):
        return render_template('error.html', error_code=403, error_message='访问被拒绝'), 403
    try:
        from models.wire_crimp_login import WireCrimpLogin
        from sqlalchemy import func
        crimp_total = WireCrimpLogin.query.with_entities(func.count()).scalar() or 0
    except Exception:
        crimp_total = 0
    return render_template('wire_crimp_login.html', crimp_total=crimp_total)


def _split_pair(text, sep='\n'):
    """把双值单元格（端子名1/2、压高1/2）按换行/斜杠拆成两侧，缺失侧用空串。"""
    if text is None:
        return '', ''
    parts = str(text).replace('\r', '\n').split(sep)
    parts = [p.strip() for p in parts if p is not None]
    while len(parts) < 2:
        parts.append('')
    return parts[0], parts[1]


def _parse_crimp_login_excel(file_path):
    """解析压高登陆 Excel（参考 template/AA.xlsx 列结构）。

    关键列（1-based）：回路号(8)、线质(9)、规格/颜色(10，sq/color 用/分隔)、
    端子名1/2(14，换行分隔)、号机名(15)、压高(16，换行分隔)。
    返回 (rows, missing_columns)，rows 为已归一化的字典列表。
    """
    from openpyxl import load_workbook
    wb = load_workbook(file_path, data_only=True, read_only=True)
    ws = wb.active
    rows_iter = ws.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if not header:
        return [], []
    # 表头模糊匹配（兼容中英文/空格差异）
    def _col(name_keywords):
        for i, h in enumerate(header):
            h = str(h or '').strip()
            if any(k in h for k in name_keywords):
                return i
        return None
    idx = {
        'circuit': _col(['回路号', '回路']),
        'wire': _col(['线质']),
        'spec_color': _col(['规格/颜色', '规格', '颜色']),
        'terminal': _col(['端子名', '端子']),
        'machine': _col(['号机名', '号机']),
        'crimp': _col(['压高']),
        'lot': _col(['LOT', 'LOT/NO', 'LOTNO']),
    }
    missing = [k for k, v in idx.items()
               if v is None and k in ('circuit', 'wire', 'spec_color', 'terminal', 'machine', 'crimp')]
    out = []
    for raw in rows_iter:
        if raw is None:
            continue
        def _get(key):
            c = idx.get(key)
            return raw[c] if (c is not None and c < len(raw)) else None
        circuit = str(_get('circuit') or '').strip()
        if not circuit:
            continue
        # 线质必填：匹配键之一，缺失会导致扫描录入页无法匹配
        wire = str(_get('wire') or '').strip().upper()
        if not wire:
            continue
        spec_color = str(_get('spec_color') or '').strip()
        # 规格/颜色 形如 0.50/B：sq/color 用 / 分隔
        if '/' in spec_color:
            sq, color = [x.strip() for x in spec_color.split('/', 1)]
        else:
            sq, color = spec_color, ''
        tml1, tml2 = _split_pair(_get('terminal'))
        machine = str(_get('machine') or '').strip()
        strip1, strip2 = _split_pair(_get('crimp'))
        if not (tml1 and tml2 and machine and (strip1 or strip2)):
            continue
        out.append({
            'lot_no': str(_get('lot') or '').strip() or None,
            'circuit_no': circuit.upper(),
            'machine_no': _wire_norm_machine(machine),
            'wire': wire,
            'color': color,
            'sq': sq,
            # 端子名保留原始值（含 -），匹配时在查询侧统一归一化
            'tml1': tml1,
            'tml2': tml2,
            'strip1': strip1,
            'strip2': strip2,
        })
    return out, missing


@main_bp.route('/wire/crimp_login/upload', methods=['POST'])
@login_required
def wire_crimp_login_upload():
    """Excel 批量导入压高登陆数据（去重 upsert：同匹配键覆盖更新）"""
    import time
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_crimp_login_upload'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    upload_dir = get_upload_dir('temp')
    try:
        from models.wire_crimp_login import WireCrimpLogin
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        file = request.files['file']
        if not file.filename:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        if not allowed_file(file.filename):
            return jsonify({'success': False, 'error': '仅支持 xls/xlsx 文件'}), 400
        secure_name = secure_filename(file.filename)
        file_path = os.path.join(upload_dir, f'_crimp_upload_{int(time.time())}_{secure_name}')
        file.save(file_path)
        try:
            rows, missing_columns = _parse_crimp_login_excel(file_path)
        except Exception as e:
            return jsonify({'success': False, 'error': f'文件解析失败：{e}'}), 400
        finally:
            try:
                os.remove(file_path)
            except OSError:
                pass
        if not rows:
            return jsonify({'success': False, 'error': '文件中没有有效压高数据行',
                            'missing_columns': missing_columns}), 400
        if missing_columns:
            return jsonify({'success': False,
                            'error': f'缺少必要列：{", ".join(missing_columns)}',
                            'missing_columns': missing_columns}), 400
        # 同匹配键去重：后者覆盖前者；再与库内既有记录 upsert
        user_id = session.get('user_id')
        source_file = secure_name
        # 匹配键签名：端子名用归一化值（兼容库内旧归一化数据与本次原始值）
        # 键 = 回路号 + 号机 + 线质 + 颜色 + 规格 + 端子(双端子)
        key_sig = lambda c, m, w, col, sq_, t1, t2: (
            c, m, w, col, sq_, _wire_norm_tml(t1), _wire_norm_tml(t2))
        # 表格内重复项自动去除（按归一化匹配键去重，保留每条键的首个出现）
        raw_total = len(rows)
        seen_sig = set()
        deduped_rows = []
        duplicates = 0
        for r in rows:
            sig = key_sig(r['circuit_no'], r['machine_no'], r['wire'], r['color'], r['sq'], r['tml1'], r['tml2'])
            if sig in seen_sig:
                duplicates += 1
                continue
            seen_sig.add(sig)
            deduped_rows.append(r)
        rows = deduped_rows
        # 库内同键候选一次性加载（按回路/号机/线质/颜色/规格粗定位，端子名在 Python 侧归一化比对）
        existing = {}
        sigs = [key_sig(r['circuit_no'], r['machine_no'], r['wire'], r['color'], r['sq'], r['tml1'], r['tml2'])
                for r in rows]
        if sigs:
            from sqlalchemy import or_
            econds = [(
                (WireCrimpLogin.circuit_no == s[0]) & (WireCrimpLogin.machine_no == s[1])
                & (WireCrimpLogin.wire == s[2])
                & (WireCrimpLogin.color == s[3]) & (WireCrimpLogin.sq == s[4])
            ) for s in sigs]
            for er in WireCrimpLogin.query.filter(or_(*econds)).all():
                esig = key_sig(er.circuit_no, er.machine_no, er.wire, er.color, er.sq, er.tml1, er.tml2)
                existing[esig] = er
        skipped = 0
        inserted = 0
        for r in rows:
            sig = key_sig(r['circuit_no'], r['machine_no'], r['wire'], r['color'], r['sq'], r['tml1'], r['tml2'])
            if sig in existing:
                # 已存在相同匹配键记录：保留原值不覆盖，支持分批持续累积上传
                skipped += 1
                continue
            db.session.add(WireCrimpLogin(
                lot_no=r['lot_no'], circuit_no=r['circuit_no'], machine_no=r['machine_no'],
                wire=r['wire'],
                color=r['color'], sq=r['sq'], tml1=r['tml1'], tml2=r['tml2'],
                strip1=r['strip1'], strip2=r['strip2'],
                source_file=source_file, created_by=user_id))
            inserted += 1
            # 新键加入 existing 避免同批重复插入
            existing[sig] = WireCrimpLogin()
        try:
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            return jsonify({'success': False, 'error': f'导入失败：{e}'}), 500
        return jsonify({
            'success': True,
            'total': raw_total,
            'duplicates': duplicates,
            'inserted': inserted,
            'skipped': skipped,
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/crimp_login/delete', methods=['POST'])
@login_required
def wire_crimp_login_delete():
    """批量删除压高登陆记录（单个或全选）。

    请求体：{'ids': [int, ...]}
    仅上传权限用户可删除；按主键精确删除，不影响匹配逻辑其余记录。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_crimp_login_upload'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_crimp_login import WireCrimpLogin
        payload = request.get_json(silent=True) or {}
        ids = payload.get('ids') or []
        # 仅保留合法正整数主键
        ids = [int(i) for i in ids if str(i).isdigit()]
        if not ids:
            return jsonify({'success': False, 'error': '未选择要删除的记录'}), 400
        # 去重后批量删除
        ids = list(dict.fromkeys(ids))
        deleted = WireCrimpLogin.query.filter(WireCrimpLogin.id.in_(ids)).delete(
            synchronize_session=False)
        db.session.commit()
        return jsonify({'success': True, 'deleted': deleted})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/crimp_login/data')
@login_required
def wire_crimp_login_data():
    """压高登陆已维护记录列表（便于查看/核对）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_crimp_login'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        from models.wire_crimp_login import WireCrimpLogin
        from sqlalchemy import func
        rows = (WireCrimpLogin.query
                .order_by(WireCrimpLogin.circuit_no, WireCrimpLogin.machine_no,
                          WireCrimpLogin.color, WireCrimpLogin.sq,
                          WireCrimpLogin.tml1, WireCrimpLogin.tml2)
                .limit(2000).all())
        return jsonify({'success': True, 'rows': [r.to_dict() for r in rows],
                        'total': len(rows)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/crimp_login/download')
@login_required
def wire_crimp_login_download():
    """压高登陆全量 Excel 下载（回路号/号机/线质/颜色/规格/端子/压高/时间）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_crimp_login'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from models.wire_crimp_login import WireCrimpLogin
        from utils.time_utils import get_utc8_time
        rows = (WireCrimpLogin.query
                .order_by(WireCrimpLogin.circuit_no, WireCrimpLogin.machine_no,
                          WireCrimpLogin.wire, WireCrimpLogin.color, WireCrimpLogin.sq,
                          WireCrimpLogin.tml1, WireCrimpLogin.tml2)
                .all())
        wb = Workbook()
        ws = wb.active
        ws.title = '压高登陆'
        headers = ['回路号', '号机', '线质', '颜色', '规格', '端子1', '端子2',
                   '压高1', '压高2', 'LOT', '来源文件', '登录时间']
        ws.append(headers)
        # 表头样式：加粗 + 浅蓝底 + 居中
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill('solid', fgColor='D9E1F2')
            c.alignment = Alignment(horizontal='center', vertical='center')
        for r in rows:
            ws.append([
                r.circuit_no or '', r.machine_no or '', r.wire or '',
                r.color or '', r.sq or '',
                r.tml1 or '', r.tml2 or '', r.strip1 or '', r.strip2 or '',
                r.lot_no or '', r.source_file or '',
                r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else '',
            ])
        # 数据行内容换行、顶部对齐
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical='top')
        # 列宽
        widths = [12, 10, 10, 10, 8, 20, 20, 14, 14, 18, 28, 20]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i) if i <= 26 else 'A' + chr(64 + i - 26)].width = w
        ts = get_utc8_time().strftime('%Y%m%d_%H%M%S')
        filename = f'压高登陆_{len(rows)}条_{ts}.xlsx'
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f'压高登陆下载失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/crimp_login/match', methods=['POST'])
@login_required
def wire_crimp_login_match():
    """扫描录入页批量拉取用户压高：按匹配键返回压高 map。

    请求体：{'lot_no':..., 'circuit_no':..., 'machine_no':...,
             'detail': [{'circuit','color','sq','tml1','tml2'}, ...]}
    返回：{'success', 'crimp': { '行索引': {'strip1','strip2'} } }（仅命中行，未命中不返回）
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry'):
        return jsonify({'success': False, 'error': '无权限访问'}), 403
    try:
        data = request.get_json(silent=True) or {}
        machine_no = str(data.get('machine_no') or '').strip()
        detail = data.get('detail') or []
        keys = _wire_user_crimp_query_keys(detail, machine_no)
        matched = _wire_lookup_user_crimp(keys)
        # 按请求顺序回填空 key -> 压高
        result = {}
        for i, d in enumerate(detail):
            t1 = _wire_norm_tml(d.get('tml1'))
            t2 = _wire_norm_tml(d.get('tml2'))
            if not (t1 and t2):
                continue
            k = (str(d.get('circuit') or '').strip().upper(),
                 _wire_norm_machine(machine_no),
                 str(d.get('wire') or '').strip().upper(),
                 str(d.get('color') or '').strip(),
                 _wire_norm_sq(d.get('sq')),
                 t1, t2)
            if k in matched:
                result[str(i)] = {'strip1': matched[k][0], 'strip2': matched[k][1]}
        return jsonify({'success': True, 'crimp': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/download')
@login_required
def wire_scan_entry_download():
    """历史扫描数据下载（按日期范围，Excel）

    完整历史数据从录入页面迁移到下载页：录入页面仅加载最近7天，
    需要历史数据时在此按日期范围查询并下载。
    列：车种别/序号/EO/品番/LOT/NO/回路号/线质/规格颜色/
    长度/数量/配线区分/端子名1/2/号机名/压高/日期（按录入时间倒序、同时间按LOT聚集）
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_download'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from datetime import datetime, timedelta
        from sqlalchemy import func
        from models.wire_scan_record import WireScanRecord
        start = (request.args.get('start') or '').strip()
        end = (request.args.get('end') or '').strip()
        q = WireScanRecord.query
        # 用范围比较代替 func.date()，避免对列做函数运算导致 created_at 索引失效（全表扫描）
        if start:
            try:
                q = q.filter(WireScanRecord.created_at >= datetime.strptime(start, '%Y-%m-%d'))
            except ValueError:
                pass
        if end:
            try:
                q = q.filter(WireScanRecord.created_at < datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1))
            except ValueError:
                pass
        elif not start:
            # 未指定日期范围默认最近7天
            q = q.filter(WireScanRecord.created_at >= datetime.utcnow() - timedelta(days=7))
        # 排序：按记录 id 升序 = 扫描进入的先后顺序（与下载页面展示顺序一致）
        records = (q
                   .order_by(WireScanRecord.id)
                   .all())
        lot_nos = list({r.lot_no for r in records})
        # LOT 关联信息：优先读记录上固化的文本快照，缺失才回退 lot_login_info
        lot_info = _wire_build_lot_info(records, lot_nos)
        # 每个 LOT 出现的全部回路号（与 /wire/scan_entry/data 一致，
        # 不再只取首条电路行，避免“首行无资料、后续回路有资料”时导出漏配）
        lot_circuit = {}
        for r in records:
            if r.circuit_no:
                lot_circuit.setdefault(r.lot_no, set()).add(r.circuit_no.strip())
        # 线束明细：直接读 wire_scan_detail 文本快照，不再实时条件查询 sub_list；
        # 仅历史缺口惰性实时拉取一次并补固化。原始资料删除后导出内容仍完整
        subl, subl_by_lot = _wire_load_snapshot_map(lot_info, lot_circuit, lazy_heal=True)

        # ---- 压高来源：与下载页面保持一致，统一取「压高登录」(wire_crimp_login) 的实时匹配结果 ----
        # 旧实现直接导出 wire_scan_detail 固化时的 strip1/strip2（历史值），
        # 而页面展示的是压高登录匹配值，二者不同源会导致 Excel 内容“不更新”。
        # 先做一次预遍历，收集全部 (记录, 明细) 的匹配键并去重，再分批查询，避免逐行查库。
        rec_detail_map = {}  # 记录id -> (回路键, 明细列表)
        for r in records:
            circ_key = (r.circuit_no or '').strip().upper()
            if not circ_key and lot_circuit.get(r.lot_no):
                circ_key = next(iter(lot_circuit[r.lot_no]), '').strip().upper()
            dtl_list = subl_by_lot.get(f'{r.lot_no.strip()}|{circ_key}')
            if dtl_list is None:
                li = lot_info.get(r.lot_no) or {}
                dtl_list = subl.get(
                    f'{(li.get("part_no") or "").strip().upper()}|'
                    f'{(li.get("jx_eo") or "").strip().upper()}|{circ_key}') or []
            rec_detail_map[r.id] = (circ_key, dtl_list)

        # 匹配键去重（回路+号机+线质+颜色+规格+端子对），同键多行只查一次
        crimp_keys, seen_crimp_key = [], set()
        for r in records:
            if not r.machine_no:
                continue
            for d in rec_detail_map.get(r.id, ('', []))[1]:
                for k in _wire_user_crimp_query_keys([d], r.machine_no):
                    tup = (k['circuit_no'], k['machine_no'], k['wire'], k['color'],
                           k['sq'], k['tml1'], k['tml2'])
                    if tup not in seen_crimp_key:
                        seen_crimp_key.add(tup)
                        crimp_keys.append(k)
        # 分批查询（单条 OR 条件过多会拖慢 SQLite），结果按 7 元组键汇总
        user_crimp = {}
        for i in range(0, len(crimp_keys), 300):
            user_crimp.update(_wire_lookup_user_crimp(crimp_keys[i:i + 300]))

        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        output = BytesIO()
        wb = Workbook()
        ws = wb.active
        ws.title = '扫描录入'
        headers = ['车种别', '序号', 'EO', '品番', 'LOT/NO', '回路号',
                   '线质', '规格/颜色', '长度', '数量', '配线区分', '端子名1/2', '号机名',
                   '压高', '操作人', '日期']
        ws.append(headers)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill('solid', fgColor='D9E1F2')
            c.alignment = Alignment(horizontal='center', vertical='center')
        seen_lot_first = set()
        for r in records:
            li = lot_info.get(r.lot_no) or {}
            part_no = li.get('part_no') or ''
            eo = li.get('jx_eo') or ''
            vehicle = li.get('vehicle_type') or ''
            # 每条记录按自身回路号匹配资料（预遍历已算好，直接复用）
            circ_key, dtl_list = rec_detail_map.get(r.id, ('', []))
            rows = dtl_list if dtl_list else [None]
            for dtl in rows:
                seq = 1 if r.lot_no not in seen_lot_first else ''
                seen_lot_first.add(r.lot_no)
                tml = f'{dtl["tml1"] or ""}\n{dtl["tml2"] or ""}' if dtl else ''
                # 压高与页面同源：命中压高登录才输出，未命中留空（不再回退固化旧值）
                crimp = ''
                if dtl and r.machine_no:
                    t1 = _wire_norm_tml(dtl['tml1'])
                    t2 = _wire_norm_tml(dtl['tml2'])
                    if t1 and t2:
                        pair = user_crimp.get((
                            str(dtl['circuit'] or '').strip().upper(),
                            _wire_norm_machine(r.machine_no),
                            str(dtl['wire'] or '').strip().upper(),
                            str(dtl['color'] or '').strip(),
                            _wire_norm_sq(dtl['sq']), t1, t2))
                        if pair:
                            crimp = f'{pair[0] or ""}\n{pair[1] or ""}'
                spec_color = f'{dtl["sq"] or ""}/{dtl["color"] or ""}' if dtl else ''
                # 配线区分：Remark1 以 "TW-" 开头时取 TW；否则取 Rel2（如 S、E*）；都空则为空白
                def _wire_div_type(d):
                    if not d:
                        return ''
                    rk = (d.get('remark1') or '').strip()
                    if rk.upper().startswith('TW'):
                        return 'TW'
                    return (d.get('rel2') or '') or (d.get('rel1') or '')
                div_type = _wire_div_type(dtl) if dtl else ''
                machine = ''
                if r.machine_no:
                    machine = str(r.machine_no).split('$')[0]
                # 备注：冻结归档资料追加醒目标注，保持原备注内容不变
                remark_val = ''
                if dtl:
                    remark_val = (dtl['remark1'] or '')
                    if dtl.get('frozen'):
                        remark_val = (remark_val + ' ' if remark_val else '') + '【资料已冻结归档·只读】'
                ws.append([
                    vehicle, seq, eo, part_no, r.lot_no, r.circuit_no or '',
                    (dtl['wire'] or '') if dtl else '', spec_color,
                    (dtl['length_cut2'] or '') if dtl else '',
                    r.wire_qty if r.machine_no else '', div_type, tml, machine, crimp or '',
                    r.created_by_name or '',
                    r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else ''
                ])
        # 行内容自动换行
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical='top')
        # 文件名拼接下载时刻（UTC+8），避免同名覆盖、便于追溯
        from utils.time_utils import get_utc8_time
        ts = get_utc8_time().strftime('%Y%m%d_%H%M%S')
        filename = f'扫描录入数据_{start or "近7天"}_{end or "至今"}_{ts}.xlsx'
        wb.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f'扫描数据下载失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/delete', methods=['POST'])
@login_required
def wire_scan_entry_delete():
    """删除扫描录入记录"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_delete'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        data = request.get_json(silent=True) or {}
        rec = db.session.get(WireScanRecord, data.get('id'))
        if not rec:
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        db.session.delete(rec)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'删除扫描记录失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/scan_entry/update', methods=['POST'])
@login_required
def wire_scan_entry_update():
    """修改扫描录入记录（支持 lot_no / circuit_no / machine_no / wire_qty）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_wire_scan_entry_edit'):
        return jsonify({'success': False, 'error': '无权限操作'}), 403
    try:
        from models.wire_scan_record import WireScanRecord
        data = request.get_json(silent=True) or {}
        rec_id = data.get('id')
        if not rec_id:
            return jsonify({'success': False, 'error': '缺少记录ID'}), 400
        rec = db.session.get(WireScanRecord, rec_id)
        if not rec:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 逐字段更新：仅更新传入的字段，未传的不动
        changed = []
        if 'lot_no' in data:
            val = str(data['lot_no'] or '').strip()
            if not val:
                return jsonify({'success': False, 'error': 'LOT不能为空'}), 400
            if rec.lot_no != val:
                rec.lot_no = val
                changed.append('lot_no')
        if 'circuit_no' in data:
            val = str(data['circuit_no'] or '').strip()
            if rec.circuit_no != val:
                rec.circuit_no = val
                changed.append('circuit_no')
        if 'machine_no' in data:
            # machine_no 允许清空（回路行无号机）
            val = str(data['machine_no'] or '').strip() or None
            if rec.machine_no != val:
                rec.machine_no = val
                changed.append('machine_no')
        if 'wire_qty' in data:
            val = data['wire_qty']
            if val is not None:
                try:
                    val = int(val)
                except (TypeError, ValueError):
                    return jsonify({'success': False, 'error': '补线数量必须为整数'}), 400
                if val < 0:
                    return jsonify({'success': False, 'error': '补线数量不能为负数'}), 400
            if rec.wire_qty != val:
                rec.wire_qty = val
                changed.append('wire_qty')

        if not changed:
            return jsonify({'success': True, 'record': rec.to_dict(), 'message': '无变更'})

        # LOT/回路变更：按新 LOT 重新盖章品番/EO/车种别，并确保新回路的资料文本快照
        if 'lot_no' in changed or 'circuit_no' in changed:
            db.session.flush()
            _wire_ensure_snapshot(rec.lot_no, rec.circuit_no, commit=False)

        db.session.commit()
        current_app.logger.info(
            f"扫描记录修改: id={rec_id} 字段={','.join(changed)} 操作员={session.get('username')}"
        )
        return jsonify({'success': True, 'record': rec.to_dict()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f'修改扫描记录失败: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/wire/data_download')
@login_required
def wire_data_download():
    """补线打端子 - 数据下载页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_wire_data_download'):
        return render_template('error.html', error_code=403, error_message='访问被拒绝'), 403
    return render_template('wire_data_download.html')


@main_bp.route('/test_sheet')
@login_required
def test_sheet():
    """测试皮子换算页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_test_sheet'):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问测试表页面，权限不足")
        return forbidden_page('您没有测试表页面的访问权限')
    return render_template('test_sheet.html')


@main_bp.route('/api/tape_calculator/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def tape_calculator_upload():
    """处理胶带换算Excel文件上传和计算"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        # 检查文件是否存在
        if 'excelFile' not in request.files:
            return jsonify({'success': False, 'error': '未上传文件'}), 400
        
        file = request.files['excelFile']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        # 检查文件类型
        if not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            return jsonify({'success': False, 'error': '仅支持Excel文件(.xls, .xlsx)'}), 400
        
        # 读取Excel文件
        import pandas as pd
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(file.read())
            tmp_path = tmp.name
        
        try:
            # 使用pandas读取Excel文件
            df = pd.read_excel(tmp_path)
            
            # 检查必需的列（仅验证品番和EO）
            required_columns = ['品番', 'EO']
            for col in required_columns:
                if col not in df.columns:
                    return jsonify({'success': False, 'error': f'文件缺少必需的列：{col}'}), 400
            
            # 确保可选列存在，如果不存在则添加为空值
            optional_columns = ['车种', 'LOT', '数量']
            for col in optional_columns:
                if col not in df.columns:
                    df[col] = ''
            
            # 处理NaN值
            df = df.fillna('')
            
            # 转换为字典列表
            excel_data = df.to_dict('records')
            
            # 服务器端验证：检查是否存在对应的基础资料
            from models.usage_list import UsageList
            missing_data = []
            
            for row in excel_data:
                part_number = row.get('品番', '').strip()
                eo = row.get('EO', '').strip()
                
                if part_number and eo:
                    # 显式 (品番+EO) 键：活跃优先，活跃缺失时冻结兜底（冻结资料允许只读用于计算）
                    _key_rows, _ = fetch_usagelist_by_key(part_number, eo)
                    any_usage_item = _key_rows[0] if _key_rows else None
                    
                    # 只有当品番和EO完全不存在基础资料时，才报错
                    if not any_usage_item:
                        missing_data.append({
                            '品番': part_number,
                            'EO': eo
                        })
            
            # 如果缺少基础资料，返回错误提示
            if missing_data:
                # 构建错误信息
                error_msg = '缺少以下品番和EO组合的基础资料：\n'
                for item in missing_data:
                    error_msg += f'品番: {item["品番"]}, EO: {item["EO"]}\n'
                error_msg += '请确保已上传对应的基础资料后再进行计算。'
                
                return jsonify({'success': False, 'error': error_msg}), 400
            
            # 处理数据并计算
            results = process_tape_calculation_data(excel_data)
            
            # 合并相同KscPno前8位的结果
            merged_results = {}
            for item in results:
                ksc_pno = item.get('KscPno')
                if ksc_pno:
                    # 计算KscPno前8位
                    ksc_pno_prefix = ksc_pno[:8] if len(ksc_pno) >= 8 else ksc_pno
                    if ksc_pno_prefix in merged_results:
                        # 合并结果值
                        merged_results[ksc_pno_prefix]['结果'] += item.get('结果', 0)
                    else:
                        # 添加新的结果项
                        merged_results[ksc_pno_prefix] = item.copy()
            
            # 转换为列表
            results = list(merged_results.values())
            
            return jsonify({
                'success': True,
                'results': results
            })
        finally:
            # 删除临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    except Exception as e:
        current_app.logger.error(f"胶带换算上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/tape_calculator/get', methods=['GET'])
@login_required
@csrf_exempt_route
def get_tape_calculation_result():
    """获取胶带换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库获取结果，直接返回空结果
    return jsonify({
        'success': True,
        'results': []
    })


@main_bp.route('/api/tape_calculator/delete', methods=['DELETE'])
@login_required
@csrf_exempt_route
def delete_tape_calculation_result():
    """删除胶带换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库删除结果，直接返回成功
    return jsonify({
        'success': True,
        'message': '计算结果已清除'
    })


@main_bp.route('/api/tape_calculator/skip', methods=['POST'])
@login_required
@csrf_exempt_route
def tape_calculator_skip():
    """跳过胶带换算资料上传步骤"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        # 直接从UsageList表中获取TAPE类型的数据
        results = []
        try:
            # 查询数据库中的UsageList数据，只获取TAPE类型的记录
            # 品番级活跃优先：同品番有活跃版本时不取冻结行，全冻结品番冻结兜底（避免双计数）
            usage_items = query_usagelist_active_priority(UsageList.Comp_Kind == 'TAPE')
            
            # 处理每个查询结果，直接添加到结果列表中
            for item in usage_items:
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 处理KscPno，提取前缀和后缀
                ksc_pno = item.KscPno or ''
                prefix = ''
                suffix = ''
                converted_pno = ''
                
                if ksc_pno:
                    # 提取前缀（前六位），如果长度不足则取全部
                    prefix = ksc_pno[:6]
                    
                    # 提取后缀（后三位），如果长度不足则取全部
                    suffix = ksc_pno[-3:]
                    
                    # 合并生成换算品番
                    converted_pno = f"{prefix}{suffix}"
                else:
                    # 如果KscPno为空，使用空字符串
                    converted_pno = ''
                
                # 假设成品数量为1（如果没有Excel数据）
                quantity = 1
                
                # 计算结果：成品 * Usage * Length
                result = quantity * usage * length
                
                # 创建结果条目
                result_row = {
                    '车种': '',
                    'EO': item.EoKsc or '',
                    '品番': item.SubPno or '',
                    'LOT': '',
                    '数量': quantity,
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': item.Detail_Kind or '',
                    'KscPno': item.KscPno or '',
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '前缀（前六位）': prefix,
                    '后缀（后三位）': suffix,
                    '换算品番(合并)': converted_pno,
                    '结果': result
                }
                
                # 直接添加到结果列表中，不进行合并
                results.append(result_row)
        except Exception as e:
            current_app.logger.error(f"从UsageList获取TAPE数据失败: {str(e)}")
            results = []
        
        return jsonify({
            'success': True,
            'results': results,
            'message': '已跳过资料上传步骤，系统已对已成功上传的资料进行计算处理。'
        })
    except Exception as e:
        current_app.logger.error(f"跳过资料上传步骤失败: {str(e)}")
        return jsonify({'success': False, 'error': f'跳过失败: {str(e)}'}), 500


# 管换算API路由
@main_bp.route('/api/tube_calculator/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def tube_calculator_upload():
    """处理管换算Excel文件上传和计算"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        # 检查文件是否存在
        if 'excelFile' not in request.files:
            return jsonify({'success': False, 'error': '未上传文件'}), 400
        
        file = request.files['excelFile']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        # 检查文件类型
        if not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            return jsonify({'success': False, 'error': '仅支持Excel文件(.xls, .xlsx)'}), 400
        
        # 读取Excel文件
        import pandas as pd
        import tempfile
        import os
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            tmp.write(file.read())
            tmp_path = tmp.name
        
        try:
            # 使用pandas读取Excel文件
            df = pd.read_excel(tmp_path)
            
            # 检查必需的列（仅验证品番和EO）
            required_columns = ['品番', 'EO']
            for col in required_columns:
                if col not in df.columns:
                    return jsonify({'success': False, 'error': f'文件缺少必需的列：{col}'}), 400
            
            # 确保可选列存在，如果不存在则添加为空值
            optional_columns = ['车种', 'LOT', '数量']
            for col in optional_columns:
                if col not in df.columns:
                    df[col] = ''
            
            # 处理NaN值
            df = df.fillna('')
            
            # 转换为字典列表
            excel_data = df.to_dict('records')
            
            # 服务器端验证：检查是否存在对应的基础资料
            from models.usage_list import UsageList
            missing_data = []
            
            for row in excel_data:
                part_number = row.get('品番', '').strip()
                eo = row.get('EO', '').strip()
                
                if part_number and eo:
                    # 显式 (品番+EO) 键：活跃优先，活跃缺失时冻结兜底（冻结资料允许只读用于计算）
                    _key_rows, _ = fetch_usagelist_by_key(part_number, eo)
                    any_usage_item = _key_rows[0] if _key_rows else None
                    
                    # 只有当品番和EO完全不存在基础资料时，才报错
                    if not any_usage_item:
                        missing_data.append({
                            '品番': part_number,
                            'EO': eo
                        })
            
            # 如果缺少基础资料，返回错误提示
            if missing_data:
                # 构建错误信息
                error_msg = '缺少以下品番和EO组合的基础资料：\n'
                for item in missing_data:
                    error_msg += f'品番: {item["品番"]}, EO: {item["EO"]}\n'
                error_msg += '请确保已上传对应的基础资料后再进行计算。'
                
                return jsonify({'success': False, 'error': error_msg}), 400
            
            # 处理数据并计算
            results = process_tube_calculation_data(excel_data)
            
            # 合并相同KscPno的结果
            merged_results = {}
            for item in results:
                ksc_pno = item.get('KscPno')
                if ksc_pno:
                    if ksc_pno in merged_results:
                        # 合并结果值
                        merged_results[ksc_pno]['结果'] += item.get('结果', 0)
                    else:
                        # 添加新的结果项
                        merged_results[ksc_pno] = item.copy()
            
            # 转换为列表
            results = list(merged_results.values())
            
            return jsonify({
                'success': True,
                'results': results
            })
        finally:
            # 删除临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    except Exception as e:
        current_app.logger.error(f"管换算上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/tube_calculator/get', methods=['GET'])
@login_required
@csrf_exempt_route
def get_tube_calculation_result():
    """获取管换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库获取结果，直接返回空结果
    return jsonify({
        'success': True,
        'results': []
    })


@main_bp.route('/api/tube_calculator/delete', methods=['DELETE'])
@login_required
@csrf_exempt_route
def delete_tube_calculation_result():
    """删除管换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库删除结果，直接返回成功
    return jsonify({
        'success': True,
        'message': '计算结果已清除'
    })


@main_bp.route('/api/tube_calculator/skip', methods=['POST'])
@login_required
@csrf_exempt_route
def tube_calculator_skip():
    """跳过关换算资料上传步骤"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.usage_list import UsageList
        
        # 直接从UsageList表中获取TUBE类型的数据
        results = []
        try:
            # 查询数据库中的UsageList数据，只获取TUBE类型的记录
            # 品番级活跃优先：同品番有活跃版本时不取冻结行，全冻结品番冻结兜底（避免双计数）
            usage_items = query_usagelist_active_priority(UsageList.Comp_Kind == 'TUBE')
            
            # 处理每个查询结果，不进行合并
            for item in usage_items:
                # 跳过"경질 튜브(HARD PP)(배가름)"类型的管
                detail_kind = item.Detail_Kind or ''
                if '경질 튜브(HARD PP)(배가름)' in detail_kind:
                    continue
                
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 计算KscPno前8位
                ksc_pno = item.KscPno or ''
                ksc_pno_prefix = ksc_pno[:8] if len(ksc_pno) >= 8 else ksc_pno
                
                # 假设成品数量为1（如果没有Excel数据）
                quantity = 1
                
                # 计算结果：成品 * Usage * Length
                result = quantity * usage * length
                
                # 为每条记录创建一个结果条目
                result_row = {
                    'SubPno': item.SubPno,
                    'EoKsc': item.EoKsc,
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': detail_kind,
                    'KscPno': item.KscPno or '',
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '取值': ksc_pno_prefix,
                    '成品': quantity,
                    '结果': result
                }
                results.append(result_row)
        except Exception as e:
            current_app.logger.error(f"从UsageList获取数据失败: {str(e)}")
            results = []
        
        return jsonify({
            'success': True,
            'results': results,
            'message': '已跳过资料上传步骤，系统已对已成功上传的资料进行计算处理。'
        })
    except Exception as e:
        current_app.logger.error(f"跳过资料上传步骤失败: {str(e)}")
        return jsonify({'success': False, 'error': f'跳过失败: {str(e)}'}), 500


@main_bp.route('/api/sheet_calculator/get', methods=['GET'])
@login_required
@csrf_exempt_route
def get_sheet_calculation_result():
    """获取皮子换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库获取结果，直接返回空结果
    return jsonify({
        'success': True,
        'results': []
    })


@main_bp.route('/api/sheet_calculator/delete', methods=['DELETE'])
@login_required
@csrf_exempt_route
def delete_sheet_calculation_result():
    """删除皮子换算结果"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 不再从数据库删除结果，直接返回成功
    return jsonify({
        'success': True,
        'message': '计算结果已清除'
    })


@main_bp.route('/api/sheet_calculator/skip', methods=['POST'])
@login_required
@csrf_exempt_route
def sheet_calculator_skip():
    """跳过皮子换算资料上传步骤"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.usage_list import UsageList
        
        # 直接从UsageList表中获取V/SHEET类型的数据
        results = []
        try:
            # 查询数据库中的UsageList数据，只获取V/SHEET类型的记录
            # 品番级活跃优先：同品番有活跃版本时不取冻结行，全冻结品番冻结兜底（避免双计数）
            usage_items = query_usagelist_active_priority(UsageList.Comp_Kind == 'V/SHEET')
            
            # 处理每个查询结果，直接添加到结果列表中
            for item in usage_items:
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 处理KscPno，提取前缀和后缀
                ksc_pno = item.KscPno or ''
                prefix = ''
                suffix = ''
                converted_pno = ''
                
                if ksc_pno:
                    # 提取前缀（前六位），如果长度不足则取全部
                    prefix = ksc_pno[:6]
                    
                    # 提取后缀（后三位），如果长度不足则取全部
                    suffix = ksc_pno[-3:]
                    
                    # 合并生成换算品番
                    converted_pno = f"{prefix}{suffix}"
                else:
                    # 如果KscPno为空，使用空字符串
                    converted_pno = ''
                
                # 假设成品数量为1（如果没有Excel数据）
                quantity = 1
                
                # 计算结果：成品 * Usage * Length
                result = quantity * usage * length
                
                # 创建结果条目
                result_row = {
                    '车种': '',
                    'EO': item.EoKsc or '',
                    '品番': item.SubPno or '',
                    'LOT': '',
                    '数量': quantity,
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': item.Detail_Kind or '',
                    'KscPno': item.KscPno or '',
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '前缀（前六位）': prefix,
                    '后缀（后三位）': suffix,
                    '换算品番(合并)': converted_pno,
                    '结果': result
                }
                
                # 直接添加到结果列表中，不进行合并
                results.append(result_row)
        except Exception as e:
            current_app.logger.error(f"从UsageList获取V/SHEET数据失败: {str(e)}")
            results = []
        
        return jsonify({
            'success': True,
            'results': results,
            'message': '已跳过资料上传步骤，系统已对已成功上传的资料进行计算处理。'
        })
    except Exception as e:
        current_app.logger.error(f"跳过资料上传步骤失败: {str(e)}")
        return jsonify({'success': False, 'error': f'跳过失败: {str(e)}'}), 500


@main_bp.route('/api/sheet_calculator/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def sheet_calculator_upload():
    """处理皮子换算Excel文件上传和计算"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        import pandas as pd
        import os
        from werkzeug.utils import secure_filename
        
        # 检查是否有文件上传
        if 'excelFile' not in request.files:
            return jsonify({'success': False, 'error': '请上传Excel文件'}), 400
        
        file = request.files['excelFile']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': '请选择要上传的文件'}), 400
        
        # 检查文件扩展名
        if not (file.filename.endswith('.xls') or file.filename.endswith('.xlsx')):
            return jsonify({'success': False, 'error': '请上传Excel文件'}), 400
        
        # 保存文件到临时位置
        filename = secure_filename(file.filename)
        upload_dir = get_upload_dir('temp')  # 临时解析文件统一放入 temp 子目录
        
        tmp_path = os.path.join(upload_dir, filename)
        file.save(tmp_path)
        
        try:
            # 使用pandas读取Excel文件
            df = pd.read_excel(tmp_path)
            
            # 检查必需的列（仅验证品番和EO）
            required_columns = ['品番', 'EO']
            for col in required_columns:
                if col not in df.columns:
                    return jsonify({'success': False, 'error': f'文件缺少必需的列：{col}'}), 400
            
            # 确保可选列存在，如果不存在则添加为空值
            optional_columns = ['车种', 'LOT', '数量']
            for col in optional_columns:
                if col not in df.columns:
                    df[col] = ''
            
            # 处理NaN值
            df = df.fillna('')
            
            # 转换为字典列表
            excel_data = df.to_dict('records')
            
            # 服务器端验证：检查是否存在对应的基础资料
            from models.usage_list import UsageList
            missing_data = []
            
            for row in excel_data:
                part_number = row.get('品番', '').strip()
                eo = row.get('EO', '').strip()
                
                if part_number and eo:
                    # 显式 (品番+EO) 键：活跃优先，活跃缺失时冻结兜底（冻结资料允许只读用于计算）
                    _key_rows, _ = fetch_usagelist_by_key(part_number, eo)
                    any_usage_item = _key_rows[0] if _key_rows else None
                    
                    # 只有当品番和EO完全不存在基础资料时，才报错
                    if not any_usage_item:
                        missing_data.append({
                            '品番': part_number,
                            'EO': eo
                        })
            
            # 如果缺少基础资料，返回错误提示
            if missing_data:
                # 构建错误信息
                error_msg = '缺少以下品番和EO组合的基础资料：\n'
                for item in missing_data:
                    error_msg += f'品番: {item["品番"]}, EO: {item["EO"]}\n'
                error_msg += '请确保已上传对应的基础资料后再进行计算。'
                
                return jsonify({'success': False, 'error': error_msg}), 400
            
            # 处理数据并计算
            results = process_sheet_calculation_data(excel_data)
            
            # 合并相同KscPno的结果
            merged_results = {}
            for item in results:
                ksc_pno = item.get('KscPno')
                if ksc_pno:
                    if ksc_pno in merged_results:
                        # 合并结果值
                        merged_results[ksc_pno]['结果'] += item.get('结果', 0)
                    else:
                        # 添加新的结果项
                        merged_results[ksc_pno] = item.copy()
            
            # 转换为列表
            results = list(merged_results.values())
            
            return jsonify({
                'success': True,
                'results': results
            })
        finally:
            # 删除临时文件
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    
    except Exception as e:
        current_app.logger.error(f"皮子换算上传失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


def process_sheet_calculation_data(data_list):
    """处理皮子换算数据的核心逻辑"""
    try:
        from models.usage_list import UsageList
        
        results = []
        
        for row in data_list:
            # 获取Excel中的数据
            vehicle_type = row.get('车种', '').strip()
            eo = row.get('EO', '').strip()
            part_number = row.get('品番', '').strip()
            lot = row.get('LOT', '').strip()
            quantity = row.get('数量', 0)
            
            # 确保quantity是数字
            try:
                quantity = float(quantity)
            except (ValueError, TypeError):
                quantity = 0
            
            # 查询数据库中的UsageList数据，只获取V/SHEET类别的数据
            # 显式 (品番+EO) 键：活跃优先，冻结兜底；Comp_Kind 在内存过滤
            _vk_rows, _ = fetch_usagelist_by_key(part_number, eo)
            usage_items = [it for it in _vk_rows if it.Comp_Kind == 'V/SHEET']
            
            # 处理每个查询结果
            for item in usage_items:
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 计算结果：数量 * Usage * Length
                result = quantity * usage * length
                
                # 处理KscPno，提取前缀和后缀
                ksc_pno = item.KscPno or ''
                prefix = ''
                suffix = ''
                converted_pno = ''
                
                if ksc_pno:
                    # 提取前缀（前六位），如果长度不足则取全部
                    prefix = ksc_pno[:6]
                    
                    # 提取后缀（后三位），如果长度不足则取全部
                    suffix = ksc_pno[-3:]
                    
                    # 合并生成换算品番
                    converted_pno = f"{prefix}{suffix}"
                else:
                    # 如果KscPno为空，使用空字符串
                    converted_pno = ''
                
                # 构建结果行
                result_row = {
                    'EoKsc': item.EoKsc or '',
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': item.Detail_Kind or '',
                    'KscPno': ksc_pno,
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '前缀（前六位）': prefix,
                    '后缀（后三位）': suffix,
                    '换算品番(合并)': converted_pno,
                    '成品': quantity,
                    '结果': result
                }
                
                results.append(result_row)
        
        return results
    except Exception as e:
        current_app.logger.error(f"皮子换算数据处理失败: {str(e)}")
        raise


def process_tape_calculation_data(data_list):
    """处理胶带换算数据的核心逻辑"""
    try:
        from models.usage_list import UsageList
        
        results = []
        
        for row in data_list:
            # 获取Excel中的数据
            vehicle_type = row.get('车种', '').strip()
            eo = row.get('EO', '').strip()
            part_number = row.get('品番', '').strip()
            lot = row.get('LOT', '').strip()
            quantity = row.get('数量', 0)
            
            # 确保quantity是数字
            try:
                quantity = float(quantity)
            except (ValueError, TypeError):
                quantity = 0
            
            # 查询数据库中的UsageList数据，只获取TAPE类型的记录
            # 显式 (品番+EO) 键：活跃优先，冻结兜底；Comp_Kind 在内存过滤
            _tk_rows, _ = fetch_usagelist_by_key(part_number, eo)
            usage_items = [it for it in _tk_rows if it.Comp_Kind == 'TAPE']
            
            # 处理每个查询结果
            for item in usage_items:
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 计算结果：数量 * Usage * Length
                result = quantity * usage * length
                
                # 构建结果行
                result_row = {
                    'SubPno': item.SubPno,
                    'EoKsc': item.EoKsc,
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': item.Detail_Kind or '',
                    'KscPno': item.KscPno or '',
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '成品数量': quantity,
                    '结果': result
                }
                
                results.append(result_row)
        
        return results
    except Exception as e:
        current_app.logger.error(f"胶带换算数据处理失败: {str(e)}")
        raise


def process_tube_calculation_data(data_list):
    """处理管换算数据的核心逻辑"""
    try:
        from models.usage_list import UsageList
        
        # 直接使用列表存储结果，不进行合并
        results = []
        
        for row in data_list:
            # 获取Excel中的数据
            vehicle_type = row.get('车种', '').strip()
            eo = row.get('EO', '').strip()
            part_number = row.get('品番', '').strip()
            lot = row.get('LOT', '').strip()
            quantity = row.get('数量', 0)
            
            # 确保quantity是数字
            try:
                quantity = float(quantity)
            except (ValueError, TypeError):
                quantity = 0
            
            # 查询数据库中的UsageList数据，只获取TUBE类型的记录
            # 显式 (品番+EO) 键：活跃优先，冻结兜底；Comp_Kind 在内存过滤
            _ub_rows, _ = fetch_usagelist_by_key(part_number, eo)
            usage_items = [it for it in _ub_rows if it.Comp_Kind == 'TUBE']
            
            # 处理每个查询结果
            for item in usage_items:
                # 跳过"경질 튜브(HARD PP)(배가름)"类型的管
                detail_kind = item.Detail_Kind or ''
                if '경질 튜브(HARD PP)(배가름)' in detail_kind:
                    continue
                
                # 确保Usage和Length是数字
                try:
                    usage = float(item.Usage) if item.Usage else 0
                except (ValueError, TypeError):
                    usage = 0
                
                try:
                    length = float(item.Length) if item.Length else 0
                except (ValueError, TypeError):
                    length = 0
                
                # 计算KscPno前8位
                ksc_pno = item.KscPno or ''
                ksc_pno_prefix = ksc_pno[:8] if len(ksc_pno) >= 8 else ksc_pno
                
                # 计算结果：成品 * Usage * Length
                result = quantity * usage * length
                
                # 为每条记录创建一个结果条目
                result_row = {
                    'SubPno': item.SubPno,
                    'EoKsc': item.EoKsc,
                    'Comp_Kind': item.Comp_Kind or '',
                    'Detail_Kind': detail_kind,
                    'KscPno': item.KscPno or '',
                    'Part_Name': item.Part_Name or '',
                    'Usage': usage,
                    'Length': length,
                    '取值': ksc_pno_prefix,
                    '成品': quantity,
                    '结果': result
                }
                results.append(result_row)
        
        # 直接返回结果列表
        return results
    except Exception as e:
        current_app.logger.error(f"管换算数据处理失败: {str(e)}")
        raise


# 车种管理API路由
@main_bp.route('/api/vehicle_types/add', methods=['POST'])
@csrf_exempt_route
def add_vehicle_type():
    """添加单个车种"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        # 验证EO和品番是否与UsageList一致
        eo = data.get('eo', '').strip()
        part_number = data['part_number'].strip()
        
        if eo and part_number:
            # 检查UsageList中是否存在该品番
            usage_data = UsageList.query.filter_by(SubPno=part_number, deleted=False, is_frozen=False).first()
            if usage_data:
                # 品番存在，检查EO是否一致
                if usage_data.EoKsc != eo:
                    # EO不一致，返回冲突信息
                    return jsonify({
                        'success': False,
                        'conflict': True,
                        'conflict_type': 'eo_mismatch',
                        'message': f'品番 {part_number} 在UsageList中的EO编号为 {usage_data.EoKsc}，与输入的EO编号 {eo} 不一致',
                        'usage_data': {
                            'eo': usage_data.EoKsc,
                            'part_number': usage_data.SubPno
                        },
                        'input_data': {
                            'eo': eo,
                            'part_number': part_number
                        }
                    }), 409  # 409 Conflict
            else:
                # 品番在UsageList中不存在
                return jsonify({
                    'success': False, 
                    'error': f'品番 {part_number} 在UsageList中不存在，请检查数据'
                }), 400
        elif not eo and part_number:
            # 输入数据没有EO，检查UsageList中是否有该品番
            usage_data = UsageList.query.filter_by(SubPno=part_number, deleted=False, is_frozen=False).first()
            if usage_data and usage_data.EoKsc:
                # 品番存在且有EO，返回冲突信息
                return jsonify({
                    'success': False,
                    'conflict': True,
                    'conflict_type': 'eo_missing',
                    'message': f'品番 {part_number} 在UsageList中的EO编号为 {usage_data.EoKsc}，但输入数据没有EO编号',
                    'usage_data': {
                        'eo': usage_data.EoKsc,
                        'part_number': usage_data.SubPno
                    },
                    'input_data': {
                        'eo': '',
                        'part_number': part_number
                    }
                }), 409  # 409 Conflict
        
        # 设置EO相关状态
        eo_override = False
        usage_eo = None
        needs_eo_attention = False
        
        if usage_data and usage_data.EoKsc:
            if eo and usage_data.EoKsc != eo:
                # EO不一致，标记为强制覆盖
                eo_override = True
                usage_eo = usage_data.EoKsc
            elif not eo:
                # 没有输入EO，但UsageList中有EO，标记为需要EO注意
                needs_eo_attention = True
                usage_eo = usage_data.EoKsc
            else:
                # EO一致，状态正常
                usage_eo = usage_data.EoKsc
        
        # 记录操作人信息
        current_user_id = session.get('user_id')
        
        vehicle = VehicleType(
            eo=eo,
            part_number=part_number,
            vehicle_name=data.get('vehicle_name', ''),
            item=data.get('item', ''),
            vehicle_item_system_version=data.get('vehicle_item_system_version', ''),
            description=data.get('description', ''),
            source='manual',
            eo_override=eo_override,
            usage_eo=usage_eo,
            needs_eo_attention=needs_eo_attention,
            created_by=current_user_id,
            updated_by=current_user_id,
            # 操作人姓名快照：账号删除后记录仍可显示文本
            created_by_name=session.get('username'),
            updated_by_name=session.get('username')
        )

        db.session.add(vehicle)

        db.session.commit()
        
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.ADD_VEHICLE_TYPE,
                       f"添加车种: {part_number}",
                       TargetType.VEHICLE_TYPE, vehicle.id, part_number)
        except Exception as _le:
            current_app.logger.warning(f"记录添加车种日志失败: {_le}")

        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"添加车种失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
@main_bp.route('/api/vehicle_types/resolve_single_conflict', methods=['POST'])
@csrf_exempt_route
def resolve_single_vehicle_conflict():
    """处理单个车种添加的EO冲突"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        choice = data.get('choice')  # 'usage' 或 'input'
        vehicle_data = data.get('vehicle_data')
        
        from models.vehicle_type import VehicleType
        
        # 根据用户选择使用相应的EO编号
        if choice == 'usage':
            eo = vehicle_data['usage_data']['eo']
            eo_override = False
            usage_eo = vehicle_data['usage_data']['eo']
            needs_eo_attention = False
        else:  # choice == 'input'
            eo = vehicle_data['input_data']['eo'] or ''  # 如果input_eo为空，则设置为空字符串
            if not eo and vehicle_data['usage_data']['eo']:
                # 用户选择使用输入数据，但输入数据没有EO，标记为需要EO注意
                eo_override = False
                needs_eo_attention = True
            else:
                # 用户选择使用输入数据，有EO或没有EO，标记为强制覆盖
                eo_override = True
                needs_eo_attention = False
            usage_eo = vehicle_data['usage_data']['eo']
        
        # 记录操作人信息
        current_user_id = session.get('user_id')
        
        vehicle = VehicleType(
            eo=eo,
            part_number=vehicle_data['input_data']['part_number'],
            vehicle_name=vehicle_data.get('vehicle_name', ''),
            item=vehicle_data.get('item', ''),
            vehicle_item_system_version=vehicle_data.get('vehicle_item_system_version', ''),
            description=vehicle_data.get('description', ''),
            source='manual',
            eo_override=eo_override,
            usage_eo=usage_eo,
            needs_eo_attention=needs_eo_attention,
            created_by=current_user_id,
            updated_by=current_user_id,
            # 操作人姓名快照：账号删除后记录仍可显示文本
            created_by_name=session.get('username'),
            updated_by_name=session.get('username')
        )

        db.session.add(vehicle)

        db.session.commit()
        
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"处理车种冲突失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/batch_add', methods=['POST'])
@csrf_exempt_route
def batch_add_vehicle_types():
    """批量添加车种"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：车种批量添加
    user_id = session['user_id']
    if not check_permission(user_id, 'can_vehicle_types_batch_add'):
        return jsonify({'success': False, 'error': '没有批量添加车种的权限'}), 403
    try:
        data = request.get_json()
        vehicles = data.get('vehicles', [])
        is_resolved_conflicts = data.get('is_resolved_conflicts', False)  # 标记是否为已处理的冲突数据
        
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        # 第一步：收集所有品番，批量检查是否已存在
        all_part_numbers = [vehicle_data['part_number'].strip() for vehicle_data in vehicles]
        
        # 批量查询已存在的品番
        existing_vehicles = VehicleType.query.filter(VehicleType.part_number.in_(all_part_numbers)).all()
        existing_part_numbers = {vehicle.part_number for vehicle in existing_vehicles}
        
        # 第二步：收集所有品番，批量检查UsageList中的数据
        usage_data_map = {}
        if not is_resolved_conflicts:
            # 品番级活跃优先：同品番有活跃版本时不取冻结行，全冻结品番冻结兜底
            usage_list_data = query_usagelist_active_priority(part_numbers=all_part_numbers)
            usage_data_map = {item.SubPno: item for item in usage_list_data}
        
        validation_errors = []
        duplicate_part_numbers = []
        eo_conflicts = []
        valid_vehicles = []
        
        for i, vehicle_data in enumerate(vehicles):
            eo = vehicle_data.get('eo', '').strip()
            part_number = vehicle_data['part_number'].strip()
            
            # 优先检查品番是否已存在（提前判断）
            if part_number in existing_part_numbers:
                duplicate_part_numbers.append(f"第{i+1}行: 品番 {part_number} 已存在")
                continue
            
            # 如果是已处理的冲突数据，跳过EO一致性检查
            if is_resolved_conflicts:
                valid_vehicles.append(vehicle_data)
                continue
            
            # 验证EO和品番是否与UsageList一致
            usage_data = usage_data_map.get(part_number)
            
            if eo and part_number:
                # 有EO的情况，检查与UsageList的一致性
                if usage_data:
                    # 品番存在，检查EO是否一致
                    if usage_data.EoKsc != eo:
                        # EO不一致，记录冲突
                        eo_conflicts.append({
                            'row': i + 1,
                            'part_number': part_number,
                            'input_eo': eo,
                            'usage_eo': usage_data.EoKsc,
                            'vehicle_data': vehicle_data,
                            'conflict_type': 'eo_mismatch'
                        })
                        continue
                else:
                    # 品番在UsageList中不存在
                    validation_errors.append(f"第{i+1}行: 品番 {part_number} 在UsageList中不存在")
                    continue
            elif not eo and part_number:
                # 没有EO的情况，检查UsageList中是否有该品番
                if usage_data and usage_data.EoKsc:
                    # 品番存在且有EO，记录冲突
                    eo_conflicts.append({
                        'row': i + 1,
                        'part_number': part_number,
                        'input_eo': '',
                        'usage_eo': usage_data.EoKsc,
                        'vehicle_data': vehicle_data,
                        'conflict_type': 'eo_missing'
                    })
                    continue
                elif usage_data and not usage_data.EoKsc:
                    # 品番存在但没有EO，直接通过
                    pass
                else:
                    # 品番在UsageList中不存在
                    validation_errors.append(f"第{i+1}行: 品番 {part_number} 在UsageList中不存在")
                    continue
            
            # 添加到有效数据列表
            valid_vehicles.append(vehicle_data)
        
        # 显示重复品番的提示
        if duplicate_part_numbers:
            duplicate_message = '以下品番已存在，已自动过滤：\n' + '\n'.join(duplicate_part_numbers)
            if not valid_vehicles:
                return jsonify({
                    'success': False, 
                    'error': duplicate_message + '\n\n没有有效数据可添加'
                }), 400
        
        # 如果有EO冲突，返回冲突信息
        if eo_conflicts:
            return jsonify({
                'success': False,
                'conflict': True,
                'message': f'发现 {len(eo_conflicts)} 个EO冲突，请选择处理方式',
                'conflicts': eo_conflicts,
                'duplicate_part_numbers': duplicate_part_numbers,
                'validation_errors': validation_errors
            }), 409
        
        if validation_errors:
            error_message = '数据验证失败:\n' + '\n'.join(validation_errors)
            if duplicate_part_numbers:
                error_message = duplicate_message + '\n\n' + error_message
            return jsonify({
                'success': False, 
                'error': error_message
            }), 400
        
        if not valid_vehicles:
            return jsonify({
                'success': False, 
                'error': '没有有效数据可添加'
            }), 400
        
        # 验证通过，开始添加数据
        for vehicle_data in valid_vehicles:
            # 检查UsageList中的数据，设置eo_override和usage_eo
            usage_data = UsageList.query.filter_by(SubPno=vehicle_data['part_number'], deleted=False, is_frozen=False).first()
            eo_override = False
            usage_eo = None
            
            if usage_data and usage_data.EoKsc != vehicle_data.get('eo', ''):
                # EO不一致，标记为强制覆盖
                eo_override = True
                usage_eo = usage_data.EoKsc
            
            vehicle = VehicleType(
                eo=vehicle_data.get('eo', ''),
                part_number=vehicle_data['part_number'],
                vehicle_name=vehicle_data.get('vehicle_name', ''),
                item=vehicle_data.get('item', ''),
                vehicle_item_system_version=vehicle_data.get('vehicle_item_system_version', ''),
                description=vehicle_data.get('description', ''),
                source='manual',
                eo_override=eo_override,
                usage_eo=usage_eo
            )

            db.session.add(vehicle)
        

        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"批量添加车种失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/check_existing', methods=['POST'])
@csrf_exempt_route
def check_existing_vehicle_types():
    """检查品番是否已存在"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        part_numbers = data.get('part_numbers', [])
        
        if not part_numbers:
            return jsonify({'success': False, 'error': '品番列表不能为空'}), 400
        
        from models.vehicle_type import VehicleType
        
        # 批量查询已存在的品番
        existing_vehicles = VehicleType.query.filter(VehicleType.part_number.in_(part_numbers)).all()
        existing_part_numbers = [vehicle.part_number for vehicle in existing_vehicles]
        
        return jsonify({
            'success': True,
            'existing_part_numbers': existing_part_numbers
        })
    except Exception as e:
        current_app.logger.error(f"检查品番是否存在失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/download', methods=['GET'])
@csrf_exempt_route
def download_vehicle_types():
    """下载车种数据为Excel文件"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.vehicle_type import VehicleType
        
        # 获取所有车种数据，按创建时间倒序排列
        vehicles = VehicleType.query.order_by(VehicleType.create_time.desc()).all()
        
        # 创建Excel工作簿
        wb = Workbook()
        ws = wb.active
        ws.title = "车种数据"
        
        # 设置表头
        headers = ['序号', '品番', '车种', 'ITEAM', '车种_ITEAM__系统版', 'EO']
        for col, header in enumerate(headers, 1):
            ws.cell(row=1, column=col, value=header)
        
        # 填充数据
        for row, vehicle in enumerate(vehicles, 2):
            # 序号
            ws.cell(row=row, column=1, value=row - 1)
            
            # 品番
            ws.cell(row=row, column=2, value=vehicle.part_number)
            
            # 车种
            ws.cell(row=row, column=3, value=vehicle.vehicle_name or '')
            
            # ITEAM
            ws.cell(row=row, column=4, value=vehicle.item or '')
            
            # 车种_ITEAM__系统版
            ws.cell(row=row, column=5, value=vehicle.vehicle_item_system_version or '')
            
            # EO（使用显示格式）
            eo_display = vehicle.get_display_eo()
            ws.cell(row=row, column=6, value=eo_display)
        
        # 设置列宽
        column_widths = [8, 20, 15, 15, 25, 15]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + col)].width = width
        
        # 设置表头样式
        from openpyxl.styles import Font, PatternFill, Alignment
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
        
        # 保存到临时文件
        import tempfile
        import os
        from datetime import datetime
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"车种数据_{timestamp}.xlsx"
        
        with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
            wb.save(tmp.name)
            tmp_path = tmp.name
        
        # 读取文件内容并返回
        with open(tmp_path, 'rb') as f:
            file_content = f.read()
        
        # 删除临时文件
        os.unlink(tmp_path)
        
        # 创建响应
        from flask import make_response
        response = make_response(file_content)
        response.headers['Content-Type'] = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        
        # 处理中文文件名编码问题
        import urllib.parse
        # 使用RFC 5987格式的filename*字段，这是处理非ASCII文件名的标准方式
        response.headers['Content-Disposition'] = f'attachment; filename*=UTF-8\'\'{urllib.parse.quote(filename)}'
        
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.EXPORT_VEHICLE_TYPE, f"导出车种数据 {len(vehicles)} 条",
                       TargetType.VEHICLE_TYPE, None, f"{len(vehicles)}条车种")
        except Exception as _le:
            current_app.logger.warning(f"记录导出车种日志失败: {_le}")
        
        return response
        
    except Exception as e:
        current_app.logger.error(f"下载车种数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/resolve_excel_duplicates', methods=['POST'])
@csrf_exempt_route
def resolve_excel_duplicates():
    """处理Excel内部重复品番和数据冲突（集成处理）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        choices = data.get('choices', [])  # 所有品番的选择（包括重复品番和独立冲突）
        all_excel_data = data.get('all_excel_data', [])  # 获取所有Excel数据
        all_duplicates_no_import = data.get('all_duplicates_no_import', False)  # 是否所有品番都选择不导入
        
        if not all_excel_data:
            return jsonify({'success': False, 'error': '没有Excel数据需要导入'}), 400
        
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        # 清空现有数据
        VehicleType.query.delete()

        db.session.commit()  # 确保清空操作完成
        
        # 收集所有要插入的车辆数据
        vehicles_to_insert = []
        processed_part_numbers = set()
        processed_choices = {}  # 用于去重，只保留每个品番的最后一个选择
        
        # 添加调试日志
        current_app.logger.info(f"收到的choices数量: {len(choices)}")
        for i, choice in enumerate(choices):
            current_app.logger.info(f"Choice {i}: part_number={choice.get('part_number')}, selected_row={choice.get('selected_row', {}).get('eo', 'N/A')}")
        
        # 首先去重，只保留每个品番的最后一个选择
        for choice in choices:
            part_number = choice['part_number']
            processed_choices[part_number] = choice
        
        current_app.logger.info(f"去重后的choices数量: {len(processed_choices)}")
        for part_number in processed_choices:
            current_app.logger.info(f"去重后品番: {part_number}")
        
        # 然后处理去重后的选择
        for part_number, choice in processed_choices.items():
            selected_row = choice['selected_row']
            processed_part_numbers.add(part_number)
            
            # 查找UsageList中的数据，设置eo_override和usage_eo
            usage_data = UsageList.query.filter_by(SubPno=part_number, deleted=False, is_frozen=False).first()
            eo_override = selected_row.get('eo_override', False)
            usage_eo = selected_row.get('usage_eo')
            needs_eo_attention = False
            
            if not eo_override and not selected_row.get('eo', '') and usage_data and usage_data.EoKsc:
                # 没有输入EO，但UsageList中有EO，标记为需要EO注意
                needs_eo_attention = True
                usage_eo = usage_data.EoKsc
            
            vehicle = VehicleType(
                eo=selected_row.get('eo', ''),
                part_number=part_number,
                vehicle_name=selected_row.get('vehicle_name', ''),
                item=selected_row.get('item', ''),
                vehicle_item_system_version=selected_row.get('vehicle_item_system_version', ''),
                description=selected_row.get('description', ''),
                source='excel',
                eo_override=eo_override,
                usage_eo=usage_eo,
                needs_eo_attention=needs_eo_attention
            )
            vehicles_to_insert.append(vehicle)
        
        # 检查是否所有重复和冲突都选择不导入
        if all_duplicates_no_import:
            current_app.logger.info(f"用户选择不导入所有重复和冲突数据，不导入任何数据")
            return jsonify({
                'success': True,
                'message': '已跳过所有重复和冲突数据，没有数据需要导入'
            })
        
        # 如果没有用户选择的数据（即没有重复和冲突），则导入所有Excel数据
        if len(processed_choices) == 0:
            current_app.logger.info(f"没有重复和冲突，导入所有Excel数据")
            
            # 对all_excel_data进行去重，只保留每个品番的第一个出现
            unique_excel_data = {}
            for excel_data in all_excel_data:
                part_number = excel_data['part_number']
                if part_number not in unique_excel_data:
                    unique_excel_data[part_number] = excel_data
            
            current_app.logger.info(f"all_excel_data去重前: {len(all_excel_data)}, 去重后: {len(unique_excel_data)}")
            
            # 处理所有Excel数据
            for part_number, excel_data in unique_excel_data.items():
                # 检查UsageList中的数据，设置eo_override和usage_eo
                usage_data = UsageList.query.filter_by(SubPno=part_number, deleted=False, is_frozen=False).first()
                eo_override = False
                usage_eo = None
                needs_eo_attention = False
                
                if usage_data:
                    if usage_data.EoKsc != excel_data.get('eo', ''):
                        # EO不一致，标记为强制覆盖
                        eo_override = True
                        usage_eo = usage_data.EoKsc
                        needs_eo_attention = False
                    else:
                        # EO一致
                        eo_override = False
                        usage_eo = usage_data.EoKsc
                        needs_eo_attention = False
                else:
                    # 品番在UsageList中不存在
                    eo_override = False
                    usage_eo = None
                    needs_eo_attention = False
                
                vehicle = VehicleType(
                    eo=excel_data.get('eo', ''),
                    part_number=part_number,
                    vehicle_name=excel_data.get('vehicle_name', ''),
                    item=excel_data.get('item', ''),
                    vehicle_item_system_version=excel_data.get('vehicle_item_system_version', ''),
                    description=excel_data.get('description', ''),
                    source='excel',
                    eo_override=eo_override,
                    usage_eo=usage_eo,
                    needs_eo_attention=needs_eo_attention
                )
                vehicles_to_insert.append(vehicle)
        else:
            current_app.logger.info(f"处理用户选择的 {len(processed_choices)} 条数据")
                

        
        # 一次性插入所有车辆数据
        current_app.logger.info(f"准备插入 {len(vehicles_to_insert)} 条车辆数据")
        for vehicle in vehicles_to_insert:
            current_app.logger.info(f"插入品番: {vehicle.part_number}")

            db.session.add(vehicle)
        

        
        db.session.commit()
        
        # 计算总导入数量
        total_imported = len(vehicles_to_insert)
        
        if len(processed_choices) == 0:
            message = f'成功导入 {total_imported} 条数据（无重复和冲突）'
        else:
            message = f'成功导入 {total_imported} 条用户选择的数据'
        
        return jsonify({
            'success': True,
            'message': message
        })
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"处理Excel重复品番和数据冲突失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/usage_list/data', methods=['GET'])
@csrf_exempt_route
def get_usage_list_data():
    """获取UsageList数据用于前端验证"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.usage_list import UsageList
        
        # P2-1: 使用 with_entities 只查询需要的列 + yield_per 流式返回，避免加载全部 ORM 对象
        usage_data = UsageList.query.filter_by(
            deleted=False, is_frozen=False
        ).with_entities(
            UsageList.EoKsc, UsageList.SubPno
        ).yield_per(1000)
        
        # 转换为前端需要的格式（直接从元组构建，避免 ORM 对象开销）
        data = [{'eo': row[0], 'part_number': row[1]} for row in usage_data]
        
        return jsonify({'success': True, 'data': data})
    except Exception as e:
        current_app.logger.error(f"获取UsageList数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/template/<path:filename>', methods=['GET'])
def download_template(filename):
    """下载模板文件"""
    try:
        # 构建模板文件的完整路径
        template_dir = os.path.join(current_app.root_path, 'template')
        file_path = os.path.join(template_dir, filename)
        
        # 检查文件是否存在
        if not os.path.exists(file_path):
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        
        # 发送文件
        return send_file(file_path, as_attachment=True)
    except Exception as e:
        current_app.logger.error(f"下载模板文件失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/resolve_batch_conflicts', methods=['POST'])
@csrf_exempt_route
def resolve_batch_vehicle_conflicts():
    """处理批量添加的EO冲突"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        choices = data.get('choices', [])  # [{row: 1, choice: 'usage'}, ...]
        
        from models.vehicle_type import VehicleType
        
        for choice in choices:
            row = choice['row']
            choice_type = choice['choice']  # 'usage' 或 'input'
            vehicle_data = choice['vehicle_data']
            
            # 根据用户选择使用相应的EO编号
            if choice_type == 'usage':
                eo = choice['usage_eo']
                eo_override = False
                usage_eo = choice['usage_eo']
            else:  # choice_type == 'input'
                eo = choice['input_eo'] or ''  # 如果input_eo为空，则设置为空字符串
                eo_override = True
                usage_eo = choice['usage_eo']
            
            vehicle = VehicleType(
                eo=eo,
                part_number=vehicle_data['part_number'],
                vehicle_name=vehicle_data.get('vehicle_name', ''),
                item=vehicle_data.get('item', ''),
                vehicle_item_system_version=vehicle_data.get('vehicle_item_system_version', ''),
                description=vehicle_data.get('description', ''),
                source='manual',
                eo_override=eo_override,
                usage_eo=usage_eo
            )

            db.session.add(vehicle)
        

        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"处理批量车种冲突失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
@main_bp.route('/api/vehicle_types/upload_excel', methods=['POST'])
@csrf_exempt_route
def upload_vehicle_types_excel():
    """上传Excel文件导入车种数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：车种导入
    user_id = session['user_id']
    if not check_permission(user_id, 'can_vehicle_types_import_excel'):
        return jsonify({'success': False, 'error': '没有导入车种的权限'}), 403
    
    try:
        action = request.form.get('action')
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '未选择文件'}), 400
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'success': False, 'error': '只支持Excel文件'}), 400
        
        # 读取Excel文件
        import pandas as pd
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        df = pd.read_excel(file)
        
        # 验证Excel文件格式
        required_columns = ['品番', '车种', 'ITEAM', '车种_ITEAM__系统版', 'EO']
        if not all(col in df.columns for col in required_columns):
            return jsonify({'success': False, 'error': 'Excel文件格式错误，需要包含品番、车种、ITEAM、车种_ITEAM__系统版、EO列'}), 400
        
        # 收集Excel数据
        excel_data_list = []
        all_part_numbers = []
        
        # to_dict('records') + 索引对齐替代 iterrows（保留原 Excel 行号 index+1）
        _excel_rows = df.to_dict('records')
        for index, row in zip(df.index, _excel_rows):
            # 检查是否为空行（品番为空或NaN）
            if pd.isna(row['品番']) or str(row['品番']).strip() == '':
                continue  # 跳过空行
            
            part_number = str(row['品番']).strip()
            vehicle_name = str(row['车种']).strip() if pd.notna(row['车种']) else ''
            item = str(row['ITEAM']).strip() if pd.notna(row['ITEAM']) else ''
            vehicle_item_system_version = str(row['车种_ITEAM__系统版']).strip() if pd.notna(row['车种_ITEAM__系统版']) else ''
            eo = str(row['EO']).strip() if pd.notna(row['EO']) else ''
            description = ''  # 描述字段在新格式中不包含
            
            all_part_numbers.append(part_number)
            excel_data_list.append({
                'index': index + 1,
                'part_number': part_number,
                'vehicle_name': vehicle_name,
                'item': item,
                'vehicle_item_system_version': vehicle_item_system_version,
                'eo': eo,
                'description': description
            })
        
        # 检查Excel文件内部的品番重复
        excel_duplicates = []
        seen_part_numbers = {}
        
        for excel_data in excel_data_list:
            part_number = excel_data['part_number']
            if part_number in seen_part_numbers:
                # 发现重复品番
                if part_number not in [dup['part_number'] for dup in excel_duplicates]:
                    # 第一次发现这个重复品番，收集所有相关行
                    duplicate_rows = [excel_data]
                    for other_data in excel_data_list:
                        if other_data['part_number'] == part_number and other_data['index'] != excel_data['index']:
                            duplicate_rows.append(other_data)
                    
                    excel_duplicates.append({
                        'part_number': part_number,
                        'rows': duplicate_rows
                    })
            else:
                seen_part_numbers[part_number] = excel_data['index']
        
        # 查询现有数据用于对比
        existing_vehicles = VehicleType.query.filter(VehicleType.part_number.in_(all_part_numbers)).all()
        existing_vehicles_map = {vehicle.part_number: vehicle for vehicle in existing_vehicles}
        
        # 查询UsageList数据（品番级活跃优先：同品番有活跃版本时不取冻结行，全冻结品番冻结兜底）
        usage_list_data = query_usagelist_active_priority(part_numbers=all_part_numbers)
        # 按品番聚合 EO 版本集合：用于「输入EO是否命中任一版本」判定，
        # 并以版本规则取最新作为展示/比对代表值（避免字典后写覆盖或字符串序取到旧版本）。
        from utils.eo_utils import get_sorted_eos as _get_sorted_eos
        usage_eo_sets = {}
        for item in usage_list_data:
            _eo_val = _clean_eo_value(item.EoKsc)
            if item.SubPno and _eo_val:
                usage_eo_sets.setdefault(item.SubPno, set()).add(_eo_val)
        usage_data_map = {}
        for _pn, _eos in usage_eo_sets.items():
            usage_data_map[_pn] = {
                'eos': _eos,
                'latest': _get_sorted_eos(sorted(_eos))[-1],
            }
        
        # 为每个重复品番添加EO冲突信息
        for duplicate in excel_duplicates:
            for row in duplicate['rows']:
                part_number = row['part_number']
                eo = row['eo']
                
                # 检查UsageList中的EO一致性（多版本：输入EO命中任一版本即一致）
                usage_data = usage_data_map.get(part_number)
                if usage_data:
                    if eo and eo in usage_data['eos']:
                        # EO一致
                        row['eo_conflict'] = False
                        row['usage_eo'] = usage_data['latest']
                        row['input_eo'] = eo
                        row['conflict_message'] = "EO一致"
                    else:
                        # EO不一致，展示最新版本
                        row['eo_conflict'] = True
                        row['usage_eo'] = usage_data['latest']
                        row['input_eo'] = eo
                        row['conflict_message'] = f"EO不一致: Excel({eo}) vs UsageList({usage_data['latest']})"
                else:
                    # 品番在UsageList中不存在
                    row['eo_conflict'] = True
                    row['usage_eo'] = None
                    row['input_eo'] = eo
                    row['conflict_message'] = "品番在UsageList中不存在"
        
        # 收集数据差异信息（与现有数据的差异）
        data_differences = []
        for excel_data in excel_data_list:
            index = excel_data['index']
            part_number = excel_data['part_number']
            vehicle_name = excel_data['vehicle_name']
            item = excel_data['item']
            vehicle_item_system_version = excel_data['vehicle_item_system_version']
            eo = excel_data['eo']
            description = excel_data['description']
            
            # 检查现有数据，记录差异
            existing_vehicle = existing_vehicles_map.get(part_number)
            if existing_vehicle:
                # 品番已存在，记录数据差异
                differences = []
                if existing_vehicle.vehicle_name != vehicle_name:
                    differences.append(f"车种: {existing_vehicle.vehicle_name} → {vehicle_name}")
                if existing_vehicle.item != item:
                    differences.append(f"ITEAM: {existing_vehicle.item} → {item}")
                if existing_vehicle.vehicle_item_system_version != vehicle_item_system_version:
                    differences.append(f"车种_ITEAM__系统版: {existing_vehicle.vehicle_item_system_version} → {vehicle_item_system_version}")
                if existing_vehicle.eo != eo:
                    differences.append(f"EO: {existing_vehicle.eo} → {eo}")
                if existing_vehicle.description != description:
                    differences.append(f"描述: {existing_vehicle.description} → {description}")
                
                if differences:
                    data_differences.append({
                        'row': index,
                        'part_number': part_number,
                        'differences': differences,
                        'old_data': {
                            'vehicle_name': existing_vehicle.vehicle_name,
                            'item': existing_vehicle.item,
                            'vehicle_item_system_version': existing_vehicle.vehicle_item_system_version,
                            'eo': existing_vehicle.eo,
                            'description': existing_vehicle.description
                        },
                        'new_data': {
                            'vehicle_name': vehicle_name,
                            'item': item,
                            'vehicle_item_system_version': vehicle_item_system_version,
                            'eo': eo,
                            'description': description
                        }
                    })
        
        # 收集独立的冲突信息（非重复品番但与UsageList有冲突）
        conflicts = []
        current_app.logger.info(f"开始检查独立冲突，总共 {len(excel_data_list)} 个Excel数据")
        
        for excel_data in excel_data_list:
            index = excel_data['index']
            part_number = excel_data['part_number']
            vehicle_name = excel_data['vehicle_name']
            item = excel_data['item']
            vehicle_item_system_version = excel_data['vehicle_item_system_version']
            eo = excel_data['eo']
            description = excel_data['description']
            
            # 检查UsageList数据一致性（多版本：输入EO命中任一版本即一致，展示取最新版本）
            usage_data = usage_data_map.get(part_number)
            if usage_data:
                if not (eo and eo in usage_data['eos']):
                    # EO不一致，记录冲突（展示最新版本）
                    current_app.logger.info(f"发现EO冲突: 品番={part_number}, Excel EO={eo}, UsageList 最新EO={usage_data['latest']}")
                    conflicts.append({
                        'row': index,  # 添加行号
                        'eo': eo,
                        'part_number': part_number,
                        'excel_data': f"品番: {part_number}, EO: {eo}",
                        'usage_data': f"品番: {part_number}, EO: {usage_data['latest']}",
                        'vehicle_name': vehicle_name,
                        'item': item,
                        'vehicle_item_system_version': vehicle_item_system_version,
                        'description': description,
                        'usage_eo': usage_data['latest'],
                        'input_eo': eo,
                        'is_duplicate': part_number in seen_part_numbers and len([d for d in excel_data_list if d['part_number'] == part_number]) > 1
                    })
            else:
                # 品番在UsageList中不存在，记录冲突
                current_app.logger.info(f"发现品番不存在冲突: 品番={part_number}, Excel EO={eo}")
                conflicts.append({
                    'row': index,  # 添加行号
                    'eo': eo,
                    'part_number': part_number,
                    'excel_data': f"品番: {part_number}, EO: {eo}",
                    'usage_data': f"品番: 不存在",
                    'vehicle_name': vehicle_name,
                    'item': item,
                    'vehicle_item_system_version': vehicle_item_system_version,
                    'description': description,
                    'is_duplicate': part_number in seen_part_numbers and len([d for d in excel_data_list if d['part_number'] == part_number]) > 1
                })
        
        # 添加冲突总结日志
        current_app.logger.info(f"冲突检查完成: 发现 {len(conflicts)} 个冲突")
        for conflict in conflicts:
            current_app.logger.info(f"冲突详情: 品番={conflict['part_number']}, 行号={conflict['row']}, 是否重复={conflict['is_duplicate']}")
        
        if action == 'validate':
            # 验证模式
            message = f"<div class='alert alert-info py-2'><h6 class='mb-2'><i class='fa fa-check-circle me-2'></i>验证完成！</h6>"
            message += f"<div class='d-flex flex-wrap gap-2'><span class='badge bg-primary'>总数据: {len(df)}</span>"
            message += f"<span class='badge bg-success'>通过验证: {len(excel_data_list) - len(conflicts)}</span>"
            message += f"<span class='badge bg-warning text-dark'>重复品番: {len(excel_duplicates)}</span>"
            message += f"<span class='badge bg-danger'>数据冲突: {len(conflicts)}</span>"
            message += f"<span class='badge bg-info'>数据差异: {len(data_differences)}</span></div></div>"
            
            if excel_duplicates:
                message += "<div class='card mb-2'><div class='card-header bg-warning text-dark py-2'>"
                message += "<h6 class='mb-0'><i class='fa fa-exclamation-triangle me-2'></i>重复品番详情</h6></div>"
                message += "<div class='card-body p-2'><div class='table-responsive' style='max-height: 200px; overflow-y: auto;'>"
                message += "<table class='table table-sm table-bordered mb-0'>"
                message += "<thead class='table-light'><tr class='text-center'>"
                message += "<th style='width: 18%'>品番</th><th style='width: 12%'>重复行数</th><th style='width: 70%'>详细信息</th>"
                message += "</tr></thead><tbody>"
                
                for duplicate in excel_duplicates:
                    message += f"<tr><td class='text-center'><strong>{duplicate['part_number']}</strong></td>"
                    message += f"<td class='text-center'><span class='badge bg-warning'>{len(duplicate['rows'])}行</span></td>"
                    message += "<td class='small'>"
                    for i, row in enumerate(duplicate['rows']):
                        message += f"<div class='mb-1'>第{row['index']}行: 车种={row['vehicle_name']}, ITEAM={row['item']}, EO={row['eo']}"
                        if row['eo_conflict']:
                            message += f" <span class='badge bg-danger badge-sm'>{row['conflict_message']}</span>"
                        message += "</div>"
                    message += "</td></tr>"
                
                message += "</tbody></table></div></div></div>"
            
            if conflicts:
                message += "<div class='card mb-2'><div class='card-header bg-danger text-white py-2'>"
                message += "<h6 class='mb-0'><i class='fa fa-exclamation-triangle me-2'></i>数据冲突详情</h6></div>"
                message += "<div class='card-body p-2'><div class='table-responsive' style='max-height: 200px; overflow-y: auto;'>"
                message += "<table class='table table-sm table-bordered mb-0'>"
                message += "<thead class='table-light'><tr class='text-center'>"
                message += "<th style='width: 12%'>行号</th><th style='width: 20%'>品番</th><th style='width: 18%'>冲突类型</th><th style='width: 50%'>详细信息</th>"
                message += "</tr></thead><tbody>"
                
                for conflict in conflicts:
                    message += f"<tr><td class='text-center'><span class='badge bg-secondary badge-sm'>{conflict['row']}</span></td>"
                    message += f"<td class='text-center'><strong>{conflict['part_number']}</strong></td>"
                    
                    if conflict.get('is_duplicate'):
                        message += "<td class='text-center'><span class='badge bg-warning badge-sm'>重复品番冲突</span></td>"
                    else:
                        message += "<td class='text-center'><span class='badge bg-danger badge-sm'>独立冲突</span></td>"
                    
                    message += "<td class='small'>"
                    if 'usage_eo' in conflict:
                        message += f"Excel EO: <span class='text-primary'>{conflict['input_eo']}</span> vs UsageList EO: <span class='text-success'>{conflict['usage_eo']}</span>"
                    else:
                        message += "<span class='text-danger'>品番在UsageList中不存在</span>"
                    message += "</td></tr>"
                
                message += "</tbody></table></div></div></div>"
            
            if data_differences:
                message += "<div class='card mb-2'><div class='card-header bg-info text-white py-2'>"
                message += "<h6 class='mb-0'><i class='fa fa-info-circle me-2'></i>数据差异详情</h6></div>"
                message += "<div class='card-body p-2'><div class='table-responsive' style='max-height: 200px; overflow-y: auto;'>"
                message += "<table class='table table-sm table-bordered mb-0'>"
                message += "<thead class='table-light'><tr class='text-center'>"
                message += "<th style='width: 12%'>行号</th><th style='width: 20%'>品番</th><th style='width: 68%'>差异内容</th>"
                message += "</tr></thead><tbody>"
                
                for diff in data_differences:
                    message += f"<tr><td class='text-center'><span class='badge bg-secondary badge-sm'>{diff['row']}</span></td>"
                    message += f"<td class='text-center'><strong>{diff['part_number']}</strong></td>"
                    message += "<td class='small'>"
                    for change in diff['differences']:
                        message += f"<span class='badge bg-info text-dark badge-sm me-1'>{change}</span>"
                    message += "</td></tr>"
                
                message += "</tbody></table></div></div></div>"
            
            return jsonify({
                'success': True, 
                'message': message,
                'excel_duplicates': excel_duplicates,
                'conflicts': conflicts,  # 添加冲突信息
                'data_differences': data_differences,  # 添加数据差异信息
                'all_excel_data': excel_data_list  # 包含所有Excel数据
            })
        
        elif action == 'upload':
            # 上传模式 - 优先处理重复品番，同时处理冲突
            if excel_duplicates or conflicts:
                # 有Excel内部重复品番或冲突，需要用户处理
                return jsonify({
                    'success': True,
                    'excel_duplicates': excel_duplicates,
                    'conflicts': conflicts,  # 添加冲突信息
                    'data_differences': data_differences,  # 添加数据差异信息
                    'all_excel_data': excel_data_list,  # 包含所有Excel数据
                    'message': f'发现 {len(excel_duplicates)} 个Excel内部重复品番和 {len(conflicts)} 个数据冲突，请处理后再上传'
                })
            else:
                # 无冲突无重复，直接上传
                # 清空现有数据
                VehicleType.query.delete()

                db.session.commit()  # 确保清空操作完成
                
                # 处理所有Excel数据
                processed_part_numbers = set()
                for excel_data in excel_data_list:
                    part_number = excel_data['part_number']
                    
                    # 避免重复插入
                    if part_number in processed_part_numbers:
                        continue
                    processed_part_numbers.add(part_number)
                    
                    # 检查UsageList中的数据，设置eo_override和usage_eo
                    usage_data = usage_data_map.get(excel_data['part_number'])
                    eo_override = False
                    usage_eo = None
                    needs_eo_attention = False
                    
                    if usage_data:
                        if usage_data.EoKsc != excel_data.get('eo', ''):
                            # EO不一致，标记为强制覆盖
                            eo_override = True
                            usage_eo = usage_data.EoKsc
                            needs_eo_attention = False
                        else:
                            # EO一致
                            eo_override = False
                            usage_eo = usage_data.EoKsc
                            needs_eo_attention = False
                    else:
                        # 品番在UsageList中不存在
                        eo_override = False
                        usage_eo = None
                        needs_eo_attention = False
                    
                    vehicle = VehicleType(
                        eo=excel_data.get('eo', ''),
                        part_number=excel_data['part_number'],
                        vehicle_name=excel_data.get('vehicle_name', ''),
                        item=excel_data.get('item', ''),
                        vehicle_item_system_version=excel_data.get('vehicle_item_system_version', ''),
                        description=excel_data.get('description', ''),
                        source='excel',
                        eo_override=eo_override,
                        usage_eo=usage_eo,
                        needs_eo_attention=needs_eo_attention
                    )

                    db.session.add(vehicle)
                

                
                db.session.commit()
                
                # 记录文件上传操作日志
                try:
                    log_file_upload(file.filename, '车种Excel文件')
                except Exception as e:
                    current_app.logger.warning(f"记录车种上传操作日志失败: {str(e)}")
                
                try:
                    from utils.operation_logger import log_action
                    from models.operation_log import OperationType, TargetType
                    log_action(OperationType.IMPORT_VEHICLE_TYPE,
                               f"导入车种数据 {len(processed_part_numbers)} 条（文件: {file.filename}）",
                               TargetType.VEHICLE_TYPE, None, f"{len(processed_part_numbers)}条车种")
                except Exception as _le:
                    current_app.logger.warning(f"记录导入车种日志失败: {_le}")
                
                return jsonify({
                    'success': True,
                    'message': f'成功导入 {len(processed_part_numbers)} 条数据'
                })
        
        # 如果没有匹配的action，返回错误
        return jsonify({'success': False, 'error': '无效的操作类型'}), 400
        
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"Excel导入车种数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/resolve_conflicts', methods=['POST'])
@csrf_exempt_route
def resolve_vehicle_conflicts():
    """处理车种数据冲突"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        choices = data.get('choices', [])
        
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        # 清空现有数据
        VehicleType.query.delete()
        
        # 根据用户选择处理数据
        for choice in choices:
            index = choice['index']
            choice_type = choice['choice']
            
            # 这里需要从session中获取之前上传的数据
            # 简化处理，实际应该从session或临时存储中获取
            pass
        

        
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"处理车种冲突失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/<int:vehicle_id>', methods=['GET'])
@csrf_exempt_route
def get_vehicle_type(vehicle_id):
    """获取单个车种信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.vehicle_type import VehicleType
        vehicle = db.session.get(VehicleType, vehicle_id)
        if not vehicle:
            return jsonify({'success': False, 'error': '车种不存在'}), 404
        
        return jsonify({'success': True, 'vehicle': vehicle.to_dict()})
    except Exception as e:
        current_app.logger.error(f"获取车种信息失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/<int:vehicle_id>', methods=['PUT'])
@csrf_exempt_route
def update_vehicle_type(vehicle_id):
    """更新车种信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        data = request.get_json()
        
        from models.vehicle_type import VehicleType
        from models.usage_list import UsageList
        
        vehicle = db.session.get(VehicleType, vehicle_id)
        if not vehicle:
            return jsonify({'success': False, 'error': '车种不存在'}), 404
        
        eo = data.get('eo', '').strip()
        part_number = data['part_number'].strip()
        
        # 检查品番是否与其他记录重复（排除当前记录）
        existing_vehicle = VehicleType.query.filter(
            VehicleType.part_number == part_number,
            VehicleType.id != vehicle_id
        ).first()
        if existing_vehicle:
            return jsonify({
                'success': False, 
                'error': f'品番 {part_number} 已存在，请使用其他品番'
            }), 400
        
        # 验证EO和品番是否与UsageList一致，并设置eo_override、usage_eo和needs_eo_attention
        usage_data = UsageList.query.filter_by(SubPno=part_number, deleted=False, is_frozen=False).first()
        
        # 总是根据UsageList数据重新计算状态
        # 这样可以确保当EO改为与UsageList一致时，状态能正确更新
        eo_override = False
        usage_eo = None
        needs_eo_attention = False
        
        if usage_data:
            if eo and usage_data.EoKsc and usage_data.EoKsc != eo:
                # EO不一致，标记为强制覆盖
                eo_override = True
                usage_eo = usage_data.EoKsc
                needs_eo_attention = False
            elif not eo and usage_data.EoKsc:
                # 输入没有EO，但UsageList中有EO，标记为需要EO注意
                eo_override = False
                usage_eo = usage_data.EoKsc
                needs_eo_attention = True
            else:
                # EO一致或都没有EO，状态为正常
                eo_override = False
                usage_eo = usage_data.EoKsc if usage_data.EoKsc else None
                needs_eo_attention = False
        else:
            # 品番在UsageList中不存在，状态为正常
            eo_override = False
            usage_eo = None
            needs_eo_attention = False
        
        # 更新数据
        vehicle.eo = eo
        vehicle.part_number = part_number
        vehicle.vehicle_name = data.get('vehicle_name', '')
        vehicle.item = data.get('item', '')
        vehicle.vehicle_item_system_version = data.get('vehicle_item_system_version', '')
        vehicle.description = data.get('description', '')
        vehicle.eo_override = eo_override
        vehicle.usage_eo = usage_eo
        vehicle.needs_eo_attention = needs_eo_attention
        # 记录修改者信息
        current_user_id = session.get('user_id')
        if hasattr(vehicle, 'updated_by'):
            vehicle.updated_by = current_user_id
        vehicle.update_time = datetime.utcnow()
        

        
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.UPDATE_VEHICLE_TYPE,
                       f"更新车种: {part_number}",
                       TargetType.VEHICLE_TYPE, vehicle_id, part_number)
        except Exception as _le:
            current_app.logger.warning(f"记录更新车种日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"更新车种失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/<int:vehicle_id>', methods=['DELETE'])
@csrf_exempt_route
def delete_vehicle_type(vehicle_id):
    """删除车种"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.vehicle_type import VehicleType
        vehicle = db.session.get(VehicleType, vehicle_id)
        
        if not vehicle:
            return jsonify({'success': False, 'error': '车种不存在'}), 404
        
        deleted_part_number = vehicle.part_number
        deleted_vehicle_name = getattr(vehicle, 'vehicle_name', None) or getattr(vehicle, 'name', None)
        vehicle_id_for_log = vehicle.id


        
        db.session.delete(vehicle)

        db.session.commit()

        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.DELETE_VEHICLE_TYPE, f"删除车种: {deleted_part_number}",
                       TargetType.VEHICLE_TYPE, vehicle_id_for_log, deleted_vehicle_name or deleted_part_number)
        except Exception as _le:
            current_app.logger.warning(f"记录删除车种日志失败: {_le}")
        
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"删除车种失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/get_headers', methods=['POST'])
@login_required
@csrf_exempt_route  # 豁免此端点的CSRF保护
def get_headers():
    """获取上传文件的表头"""
    # logger.debug("接收到获取表头的请求")

    try:
        # 检查是否有文件上传
        if 'files' not in request.files:
            logger.warning("请求中没有文件")
            return jsonify({'success': False, 'error': '未上传文件'}), 400

        files = request.files.getlist('files')
        # 性能优化：仅处理前两个文件的表头
        if len(files) > 2:
            # logger.debug(f"上传了 {len(files)} 个文件，仅读取前两个文件表头以提高性能")
            files = files[:2]

        # 检查文件数量
        if len(files) == 0:
            logger.warning("没有上传文件")
            return jsonify({'success': False, 'error': '未上传文件'}), 400

        headers_list = []  # 存储所有文件的表头

        # 轻量读取表头的内部函数
        def _read_headers_only(fobj) -> list:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx') as tmp:
                tmp.write(fobj.read())
                tmp_path = tmp.name
            try:
                try:
                    # pandas 仅读取表头（nrows=0）
                    df_head = pd.read_excel(tmp_path, nrows=0)
                    return list(df_head.columns)
                except Exception:
                    # 回退 openpyxl 读取首行
                    wb = load_workbook(tmp_path, read_only=True)
                    sheet = wb.active
                    for row in sheet.iter_rows(max_row=1, values_only=True):
                        return [str(cell).strip() if cell is not None else '' for cell in row]
                    return []
            finally:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

        # 处理每个文件（仅前两个）
        for file in files:
            if file.filename == '':
                logger.warning("文件名为空")
                continue
            if not file.filename.endswith(('.xlsx', '.xls')):
                logger.warning(f"文件类型不支持: {file.filename}")
                continue
            # logger.debug(f"读取表头（轻量）: {file.filename}")
            try:
                headers = _read_headers_only(file)
                headers_list.append(headers)
                # logger.debug(f"成功获取表头: {headers}")
            except Exception as e:
                logger.warning(f"读取表头失败: {file.filename}, {str(e)}")

        # 如果没有有效的文件
        if len(headers_list) == 0:
            return jsonify({'success': False, 'error': '没有有效的Excel文件'}), 400

        # 返回数据（保持第一个文件的表头顺序作为主表头）
        return jsonify({
            'success': True,
            'headers': headers_list[0] if headers_list else [],  # 使用第一个文件的表头作为主表头
            'headers_list': headers_list  # 所有文件的表头列表
        })

    except Exception as e:
        logger.error(f"获取表头失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'获取表头失败: {str(e)}'}), 400
@main_bp.route('/batch_compare', methods=['POST'])
@login_required
@csrf_exempt_route  # 豁免此端点的CSRF保护
def batch_compare():
    """执行批量文件对比，标记增加、删除和修改的数据行"""
    # logger.debug("接收到批量对比请求")

    try:
        # 检查文件上传
        if 'files' not in request.files:
            logger.warning("请求中没有文件")
            return jsonify({'success': False, 'error': '未上传文件'}), 400

        files = request.files.getlist('files')
        # 新增：读取前端传入的对比模式
        compare_mode = (request.form.get('compare_mode') or 'key').strip()
        key_column = request.form.get('key_column')
        compare_columns = request.form.getlist('compare_columns')  # 获取用户选择的对比列

        if len(files) < 2:
            logger.warning(f"上传文件数量不足: {len(files)}")
            return jsonify({'success': False, 'error': '至少需要上传两个文件'}), 400

        # 仅在关键列模式下要求关键列
        if compare_mode == 'key' and not key_column:
            logger.warning("未选择关键列")
            return jsonify({'success': False, 'error': '请选择关键列'}), 400

        # logger.debug(f"关键列: {key_column}")
        # logger.debug(f"上传文件数量: {len(files)}")
        # logger.debug(f"选择的对比列: {compare_columns}")

        # 如果是"合并单元列对比（仅限SubList）"模式，按与 /api/file_compare 相同的逻辑进行
        if compare_mode == 'merged_sublist':
            try:
                # 仅取前两个文件
                file1, file2 = files[0], files[1]
                df1 = read_and_convert_to_string(file1)
                df2 = read_and_convert_to_string(file2)

                # 必需列与上下文字段（与 /api/file_compare 保持一致）
                required_cols = [
                    'Circuit','Wire','WireCode','Color','SQ','Length_Cut','Length_Cut2','Tml1','Tml2',
                    'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
                    'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
                ]
                context_cols = ['SubPno','EoKsc','GroupNo','GroupOrder','SetCode','CutCode']

                # 简单校验是否像 SubList：至少包含部分关键列
                need_any = {'Circuit', 'Wire', 'WireCode'}
                if len(need_any.intersection(set(map(str, df1.columns)))) == 0 or len(need_any.intersection(set(map(str, df2.columns)))) == 0:
                    return jsonify({'success': False, 'error': '合并单元列对比仅支持SubList文件'}), 400

                def _normalize_setcode(val: str) -> str:
                    try:
                        s = str(val or '').strip()
                        if not s:
                            return ''
                        parts = s.split('_')
                        if len(parts) >= 2:
                            return f"{parts[0]}_{parts[1]}_"
                        return f"{parts[0]}_"
                    except Exception:
                        return ''

                def _build_map(dfx: pd.DataFrame) -> tuple[set, dict]:
                    dfx = dfx.copy()
                    for c in required_cols + context_cols:
                        if c not in dfx.columns:
                            dfx[c] = ''
                    dfx = dfx.fillna('').astype(str)
                    for c in required_cols + context_cols:
                        dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
                    merged_set = set()
                    merged_to_row = {}
                    # to_dict('records') 替代 iterrows，减少逐行 Series 构造开销
                    for row in dfx.to_dict('records'):
                        normalized_setcode = _normalize_setcode(row['SetCode'])
                        merged = '|'.join([row[c] for c in required_cols] + [normalized_setcode])
                        merged_set.add(merged)
                        out = {'回路合并': merged}
                        for c in context_cols:
                            out[c] = row[c]
                        for c in required_cols:
                            out[c] = row[c]
                        if merged not in merged_to_row:
                            merged_to_row[merged] = out
                    return merged_set, merged_to_row

                set1, map1 = _build_map(df1)
                set2, map2 = _build_map(df2)

                added_keys = sorted(list(set1 - set2))
                deleted_keys = sorted(list(set2 - set1))

                headers_out = ['回路合并'] + context_cols + required_cols
                added_rows = [map1[k] for k in added_keys]
                deleted_rows = [map2[k] for k in deleted_keys]

                result = {
                    'mode': 'merged_sublist',
                    'headers': headers_out,
                    'added_rows': added_rows,
                    'deleted_rows': deleted_rows,
                    'file_names': [file1.filename, file2.filename],
                }
                return jsonify({'success': True, 'result': result})
            except Exception as e:
                logger.error(f"合并单元列对比失败: {str(e)}", exc_info=True)
                return jsonify({'success': False, 'error': f'对比失败: {str(e)}'}), 400

        # --- 以下为关键列对比模式原有逻辑 ---
        # 读取所有文件并转为字符串
        all_data = []
        file_names = []

        for i, file in enumerate(files):
            try:
                # logger.debug(f"处理文件 {i + 1}: {file.filename}")
                df = read_and_convert_to_string(file)
                all_data.append(df)
                file_names.append(file.filename)
            except Exception as e:
                logger.error(f"处理文件 {file.filename} 失败: {str(e)}")
                return jsonify({'success': False, 'error': f'处理文件 {file.filename} 失败: {str(e)}'}), 400

        # 检查关键列
        for i, df in enumerate(all_data):
            if key_column not in df.columns:
                logger.error(f"文件 {file_names[i]} 中不存在关键列 '{key_column}'")
                return jsonify({'success': False, 'error': f'文件 {file_names[i]} 中不存在关键列 "{key_column}"'}), 400

        # 如果没有选择对比列，则使用除关键列外的所有列
        if not compare_columns:
            compare_columns = [col for col in all_data[0].columns if col != key_column]
            # logger.debug(f"未选择对比列，使用除关键列外的所有列: {compare_columns}")

        # 只处理前两个文件进行对比
        if len(all_data) >= 2:
            df1 = all_data[0]
            df2 = all_data[1]

            # logger.debug(f"文件1形状: {df1.shape}")
            # logger.debug(f"文件2形状: {df2.shape}")

            # 准备修改的数据结构
            modified_df1 = pd.DataFrame(columns=df1.columns)  # 存储文件1中的修改行
            modified_df2 = pd.DataFrame(columns=df2.columns)  # 存储文件2中的对应修改行
            modified_details = []  # 存储详细的修改信息

            # 生成唯一标识集合
            set1 = set(df1[key_column].astype(str))
            set2 = set(df2[key_column].astype(str))

            # 查找两个文件都有的行
            common_ids = set1.intersection(set2)
            # logger.debug(f"共有ID数量: {len(common_ids)}")

            # 遍历共有ID，查找内容有差异的行
            for common_id in common_ids:
                # 获取两个文件中对应ID的行
                row1 = df1[df1[key_column] == common_id].iloc[0]
                row2 = df2[df2[key_column] == common_id].iloc[0]

                # 比较选中的列
                is_modified = False
                differences = []

                for col in compare_columns:  # 只比较用户选择的列
                    if col != key_column and col in df1.columns and col in df2.columns:
                        # 特殊处理：将所有空白值视为相同
                        val1 = row1[col] if row1[col].strip() else ''
                        val2 = row2[col] if row2[col].strip() else ''

                        if val1 != val2:
                            is_modified = True
                            differences.append({
                                'column': col,
                                'file1_value': row1[col],
                                'file2_value': row2[col]
                            })

                # 如果有差异，添加到修改结果中
                if is_modified:
                    # 为修改的行添加标记，以便前端区分
                    row1_dict = row1.to_dict()
                    row1_dict['__diff_type__'] = 'modified'  # 添加修改标记
                    row2_dict = row2.to_dict()
                    row2_dict['__diff_type__'] = 'modified'  # 添加修改标记

                    modified_df1 = pd.concat([modified_df1, pd.DataFrame([row1])])
                    modified_df2 = pd.concat([modified_df2, pd.DataFrame([row2])])
                    modified_details.append({
                        key_column: common_id,
                        'differences': differences
                    })

            # 标记差异
            # 增加 = 文件2有但文件1没有的行 + 文件2中的修改行
            added_df = pd.concat([
                df2[df2[key_column].apply(lambda x: str(x) not in set1)].copy(),  # 文件2有但文件1没有的行
                modified_df2  # 文件2中的修改行
            ])
            added_df['__diff_type__'] = 'added'

            # 删除 = 文件1有但文件2没有的行 + 文件1中的修改行
            deleted_df = pd.concat([
                df1[df1[key_column].apply(lambda x: str(x) not in set2)].copy(),  # 文件1有但文件2没有的行
                modified_df1  # 文件1中的修改行
            ])
            deleted_df['__diff_type__'] = 'deleted'

            # logger.debug(f"增加记录数量: {len(added_df)}")
            # logger.debug(f"删除记录数量: {len(deleted_df)}")
            # logger.debug(f"修改记录数量: {len(modified_df1)}")

            # 输出详细的差异信息
            # for detail in modified_details:
            #     key_value = detail[key_column]
            #     diffs = detail['differences']
            #     # logger.debug(f"ID '{key_value}' 有 {len(diffs)} 处差异")
            #     for diff in diffs:
            #         # logger.debug(
            #         #     f"  列 '{diff['column']}': 文件1='{diff['file1_value']}', 文件2='{diff['file2_value']}'")
            #         pass

            # 构造结果
            result = {
                'file_names': file_names[:2],  # 只显示前两个文件
                'headers': list(df1.columns),
                'added': added_df.to_dict(orient='records'),
                'deleted': deleted_df.to_dict(orient='records'),
                'modified_df1': modified_df1.to_dict(orient='records'),  # 文件1中的修改行
                'modified_df2': modified_df2.to_dict(orient='records'),  # 文件2中的对应修改行
                'modified_details': modified_details,  # 详细的修改信息
                'compare_columns': compare_columns  # 传递选择的对比列到前端
            }

            # 处理可能的NaN值
            def replace_nan(obj):
                """递归替换所有NaN值为空字符串"""
                if isinstance(obj, dict):
                    return {k: replace_nan(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [replace_nan(item) for item in obj]
                elif isinstance(obj, float) and math.isnan(obj):
                    return ''  # NaN值替换为空字符串
                else:
                    return obj

            # 应用替换
            result = replace_nan(result)

            # 记录文件上传和对比操作日志（同一批次合并为一条上传记录）
            try:
                filenames = [f.filename for f in files if f.filename]
                if filenames:
                    log_batch_file_upload(filenames, count=len(filenames))
                if len(filenames) >= 2:
                    log_file_compare(filenames[0], filenames[1], compare_mode)
            except Exception as e:
                logger.warning(f"记录操作日志失败: {str(e)}")

            return jsonify({'success': True, 'result': result})
        else:
            logger.warning("需要至少两个文件进行对比")
            return jsonify({'success': False, 'error': '需要至少两个文件进行对比'}), 400

    except Exception as e:
        logger.error(f"对比文件失败: {str(e)}", exc_info=True)
        # 记录完整的错误堆栈
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'对比文件失败: {str(e)}'}), 500


@main_bp.route('/download_batch_result', methods=['POST'])
@csrf_exempt_route  # 豁免此端点的CSRF保护
def download_batch_result():
    """下载批量对比结果"""
    # logger.debug("接收到下载结果请求")

    try:
        data = request.json
        result = data.get('result', {})
        headers = result.get('headers', [])
        added = result.get('added', [])
        deleted = result.get('deleted', [])
        modified_df1 = result.get('modified_df1', [])  # 文件1中的修改行
        modified_df2 = result.get('modified_df2', [])  # 文件2中的对应修改行
        modified_details = result.get('modified_details', [])  # 详细的修改信息
        key_column = request.form.get('key_column')  # 获取关键列参数
        compare_columns = result.get('compare_columns', [])  # 获取选择的对比列

        if not key_column:
            logger.warning("未提供关键列信息")
            return jsonify({'success': False, 'error': '未提供关键列信息'}), 400

        if not added and not deleted and not modified_df1:
            logger.warning("没有可下载的差异数据")
            return jsonify({'success': False, 'error': '没有可下载的差异数据'}), 400

        # logger.debug(f"增加记录数量: {len(added)}")
        # logger.debug(f"删除记录数量: {len(deleted)}")
        # logger.debug(f"修改记录数量: {len(modified_df1)}")
        # logger.debug(f"选择的对比列: {compare_columns}")

        # 创建工作簿
        # logger.debug("创建Excel工作簿")
        wb = Workbook()

        # 样式定义
        # 基准列样式（蓝色字体）
        key_column_font = Font(color="0000FF", bold=True)  # 蓝色粗体

        # 差异单元格样式（红色字体）
        diff_font = Font(color="FF0000", bold=True)  # 红色粗体

        # 普通文本样式
        normal_font = Font()  # 默认样式

        # 创建增加回路工作表
        if added:
            # logger.debug("创建增加回路工作表")
            ws_added = wb.create_sheet(title='增加回路')
            ws_added.append(headers)

            # 写入增加的数据
            for row in added:
                ws_added.append([row.get(h, '') for h in headers])

            # 应用样式到数据行
            for row in range(2, len(added) + 2):
                for col in range(1, len(headers) + 1):
                    cell = ws_added.cell(row=row, column=col)
                    header = headers[col - 1]

                    if header == key_column:
                        # 基准列使用蓝色字体
                        cell.font = key_column_font
                    else:
                        # 普通列使用默认样式
                        cell.font = normal_font

        # 创建删除回路工作表
        if deleted:
            # logger.debug("创建删除回路工作表")
            ws_deleted = wb.create_sheet(title='删除回路')
            ws_deleted.append(headers)

            # 写入删除的数据
            for row in deleted:
                ws_deleted.append([row.get(h, '') for h in headers])

            # 应用样式到数据行
            for row in range(2, len(deleted) + 2):
                for col in range(1, len(headers) + 1):
                    cell = ws_deleted.cell(row=row, column=col)
                    header = headers[col - 1]

                    if header == key_column:
                        # 基准列使用蓝色字体
                        cell.font = key_column_font
                    else:
                        # 普通列使用默认样式
                        cell.font = normal_font

        # 创建修改回路工作表 - 单元格级差异标记
        if modified_df1 and modified_df2:
            # logger.debug("创建修改回路工作表")
            ws_modified = wb.create_sheet(title='修改回路')

            # 创建表头，包括文件1和文件2的列
            modified_headers = []
            for header in headers:
                # 只添加关键列和用户选择的对比列
                if header == key_column or header in compare_columns:
                    modified_headers.append(f"{header} (文件1)")
                    modified_headers.append(f"{header} (文件2)")
                    modified_headers.append("差异")

            ws_modified.append(modified_headers)

            # 创建修改详情字典，便于快速查找差异列
            diff_dict = {}
            for detail in modified_details:
                key_value = detail.get(list(headers)[0], '')
                diff_dict[key_value] = {diff['column']: diff for diff in detail.get('differences', [])}

            # 写入修改的数据
            for i in range(len(modified_df1)):
                row1 = modified_df1[i]
                row2 = modified_df2[i]
                row_data = []

                # 获取当前行的关键列值
                key_value = row1.get(list(headers)[0], '')
                # 获取当前行的差异信息
                row_diffs = diff_dict.get(key_value, {})

                for header in headers:
                    # 只处理关键列和用户选择的对比列
                    if header == key_column or header in compare_columns:
                        # 添加文件1的值
                        row_data.append(row1.get(header, ''))
                        # 添加文件2的值
                        row_data.append(row2.get(header, ''))
                        # 添加差异标记
                        if header in row_diffs:
                            row_data.append("✗")
                        else:
                            row_data.append("✓")

                ws_modified.append(row_data)

            # 应用样式
            for row in range(2, len(modified_df1) + 2):
                for col in range(1, len(modified_headers) + 1):
                    cell = ws_modified.cell(row=row, column=col)

                    # 每隔3列是差异列
                    if col % 3 == 0:
                        if cell.value == "✗":
                            # 差异标记使用红色字体
                            cell.font = diff_font

                            # 获取对应的值列索引
                            val_col1 = col - 2  # 文件1的值列
                            val_col2 = col - 1  # 文件2的值列

                            # 获取对应的值列的表头
                            header_idx = (col - 1) // 3
                            header = headers[header_idx]

                            # 标记有差异的单元格
                            ws_modified.cell(row=row, column=val_col1).font = diff_font
                            ws_modified.cell(row=row, column=val_col2).font = diff_font

                            # 如果是关键列，也保持蓝色字体
                            if header == key_column:
                                ws_modified.cell(row=row, column=val_col1).font = Font(color="0000FF", bold=True,
                                                                                       underline="single")
                                ws_modified.cell(row=row, column=val_col2).font = Font(color="0000FF", bold=True,
                                                                                       underline="single")
                        else:
                            # 相同标记使用默认样式
                            cell.font = normal_font
                    else:
                        # 获取对应列的表头
                        header_idx = (col - 1) // 3
                        header = headers[header_idx]

                        if header == key_column:
                            # 基准列使用蓝色字体
                            cell.font = key_column_font
                        else:
                            # 普通列使用默认样式
                            cell.font = normal_font

        # 创建修改详情工作表
        if modified_details:
            # logger.debug("创建修改详情工作表")
            ws_details = wb.create_sheet(title='修改详情')

            # 表头
            ws_details.append(['关键列值', '列名', '文件1值', '文件2值'])

            # 写入详细的修改信息
            for detail in modified_details:
                key_value = detail.get(list(headers)[0], '')  # 获取关键列值
                for diff in detail.get('differences', []):
                    # 只添加用户选择的对比列的差异
                    if diff.get('column', '') in compare_columns or diff.get('column', '') == key_column:
                        ws_details.append([
                            key_value,
                            diff.get('column', ''),
                            diff.get('file1_value', ''),
                            diff.get('file2_value', '')
                        ])

            # 应用样式到数据行
            for row in range(2, ws_details.max_row + 1):
                for col in range(1, 5):  # 只有4列
                    cell = ws_details.cell(row=row, column=col)

                    if col == 1:  # 关键列值
                        cell.font = key_column_font
                    else:  # 其他列
                        cell.font = diff_font

        # 删除默认的Sheet1
        if 'Sheet' in wb.sheetnames:
            # logger.debug("删除默认工作表")
            del wb['Sheet']

        # 保存并返回
        # logger.debug("保存Excel文件")
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        wb.save(temp_file.name)
        temp_file.close()

        # logger.debug(f"Excel文件已保存: {temp_file.name}")

        # 处理中文文件名编码问题
        filename = f'数据对比结果_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
        import urllib.parse
        
        # 记录对比结果下载操作日志
        try:
            log_file_download(filename, 'result')
        except Exception as e:
            logger.warning(f"记录下载操作日志失败: {str(e)}")
        
        response = send_file(
            temp_file.name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=urllib.parse.quote(filename),
            after_this_request=lambda: os.unlink(temp_file.name)
        )
        return response
    except Exception as e:
        logger.error(f"下载失败: {str(e)}", exc_info=True)
        # 记录完整的错误堆栈
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': f'下载失败: {str(e)}'}), 500


# 登录路由
@main_bp.route('/login', methods=['GET', 'POST', 'OPTIONS'])
@csrf_exempt_route
def login():
    # 处理 GET 请求 - 返回登录页面
    if request.method == 'GET':
        return render_template('login.html')

    # 处理 POST 请求 - 验证登录
    try:
        # 同时兼容 JSON 和传统 form 表单
        username = None
        password = None
        if request.is_json:
            data = request.get_json(silent=True) or {}
            # current_app.logger.debug(f"收到JSON登录请求: {data}")
            username = data.get('username')
            password = data.get('password')
        else:
            username = request.form.get('username')
            password = request.form.get('password')
            # current_app.logger.debug(f"收到FORM登录请求: username={username}")
        # remember = data.get('remember', False)  # 从请求中获取"记住我"状态

        # 验证用户名和密码是否存在
        if not username or not password:
            current_app.logger.warning("用户名或密码为空")
            if request.is_json:
                return jsonify({'message': '用户名和密码不能为空'}), 400
            else:
                flash('用户名和密码不能为空', 'danger')
                return redirect(url_for('main.login', next=request.url))

        # ---- 暴力破解防护：按「客户端 IP + 用户名」维度限速与锁定 ----
        # 在命中数据库前拦截已锁定标识，避免无谓的查询开销与爆破尝试。
        # 内网直连时 request.remote_addr 即真实客户端 IP；若前置反向代理，
        # 需自行在代理层设置 X-Forwarded-For 并在此取之（此处保持简单，使用 remote_addr）。
        _client_ip = request.remote_addr or 'unknown'
        _identifier = f"{_client_ip}|{username}"
        _blocked, _retry_after = check_blocked(_identifier)
        if _blocked:
            current_app.logger.warning(f"登录被锁定拦截: ip={_client_ip} user={username}")
            if request.is_json:
                return jsonify({
                    'message': f'登录尝试次数过多，请 {_retry_after} 秒后重试',
                    'retry_after': _retry_after,
                    'locked': True,
                }), 429
            flash(f'登录尝试次数过多，请 {_retry_after} 秒后重试', 'danger')
            return redirect(url_for('main.login', next=request.url))

        # 查询用户
        user = UserInfo.query.filter_by(username=username).first()

        # 验证用户和密码 - 修改为使用 password 属性
        if user and check_password_hash(user.password, password):
            # 账号禁用校验：已禁用的账号禁止登录
            if not getattr(user, 'is_active', True):
                current_app.logger.warning(f"用户 {username} 账号已被禁用，拒绝登录")
                try:
                    log_login(username, False)
                except Exception:
                    pass
                if request.is_json:
                    return jsonify({'message': '该账号已被禁用，请联系管理员'}), 403
                flash('该账号已被禁用，请联系管理员', 'danger')
                return redirect(url_for('main.login', next=request.url))

            # remember = request.form.get('remember', False)  # 获取是否勾选"记住我"
            # login_user(user, remember=remember)  # 使用Flask-Login的remember参数
            login_user(user)
            # 防会话固定：认证成功后更换 session 标识（Flask 2.3+ 的 regenerate 保留数据），
            # 失败则跳过（旧版本无此方法），不影响正常登录。
            try:
                session.regenerate()
            except Exception:
                pass
            # 登录成功：清零该标识的失败计数与锁定状态
            record_success(_identifier)
            # 登录成功，设置会话
            session['user_id'] = user.id
            session['is_admin'] = user.is_admin
            session['username'] = user.username
            # 「记住登录状态」：勾选则写持久 Cookie（有效期 PERMANENT_SESSION_LIFETIME），
            # 不勾选则为浏览器会话级 Cookie，关闭浏览器即失效。
            if request.is_json:
                session.permanent = bool((request.get_json(silent=True) or {}).get('remember'))
            else:
                session.permanent = request.form.get('remember') in ('1', 'true', 'on')
            
            # 获取用户权限信息并存储到session
            if not user.is_admin:
                permission = PermissionInfo.query.filter_by(user_id=user.id).first()
                if permission:
                    # 动态获取所有权限字段
                    user_permissions = {}
                    for field in permission.__table__.columns:
                        if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                            user_permissions[field.name] = getattr(permission, field.name)
                    session['user_permissions'] = user_permissions
                    # 记录权限会话版本
                    # 取消基于会话版本的强制下线逻辑
                else:
                    # 默认权限（全部为False）
                    session['user_permissions'] = {
                        'can_upload': False, 'can_delete': False, 'can_batch_delete': False, 'can_page': False,
                        'can_vehicle_types': False, 'can_products': False, 'can_material_download': False,
                        'can_file_management': False, 'can_file_comparison': False, 'can_sub_list': False, 'can_usage_list': False,
                        'can_products_add': False, 'can_products_edit': False, 'can_products_delete': False, 'can_products_download': False, 'can_products_import': False,
                        'can_vehicle_types_add': False, 'can_vehicle_types_edit': False, 'can_vehicle_types_delete': False, 'can_vehicle_types_download': False,
                        'can_material_download_download': False, 'can_material_download_update': False,
                        'can_file_management_upload': False, 'can_file_management_delete': False, 'can_file_management_download': False, 'can_file_management_export_file_list': False,
                        'can_file_management_freeze': False,
                        'can_file_comparison_compare': False, 'can_file_comparison_download': False,
                        'can_sub_list_add': False, 'can_sub_list_edit': False, 'can_sub_list_delete': False, 'can_sub_list_download': False,
                        'can_usage_list_add': False, 'can_usage_list_edit': False, 'can_usage_list_delete': False, 'can_usage_list_download': False,
                        'can_nas_file_search': False, 'can_nas_file_search_config': False, 'can_nas_file_search_search': False, 'can_nas_file_search_import': False
                    }
            else:
                # 管理员拥有所有权限
                session['user_permissions'] = {
                    'can_upload': True, 'can_delete': True, 'can_batch_delete': True, 'can_page': True,
                    'can_vehicle_types': True, 'can_products': True, 'can_material_download': True,
                    'can_file_management': True, 'can_file_comparison': True, 'can_sub_list': True, 'can_usage_list': True,
                    'can_products_add': True, 'can_products_edit': True, 'can_products_delete': True, 'can_products_download': True, 'can_products_import': True,
                    'can_vehicle_types_add': True, 'can_vehicle_types_edit': True, 'can_vehicle_types_delete': True, 'can_vehicle_types_download': True,
                    'can_material_download_download': True, 'can_material_download_update': True,
                    'can_file_management_upload': True, 'can_file_management_delete': True, 'can_file_management_download': True, 'can_file_management_export_file_list': True,
                    'can_file_management_freeze': True,
                    'can_file_comparison_compare': True, 'can_file_comparison_download': True,
                    'can_sub_list_add': True, 'can_sub_list_edit': True, 'can_sub_list_delete': True, 'can_sub_list_download': True,
                    'can_usage_list_add': True, 'can_usage_list_edit': True, 'can_usage_list_delete': True, 'can_usage_list_download': True,
                    'can_nas_file_search': True, 'can_nas_file_search_config': True, 'can_nas_file_search_search': True, 'can_nas_file_search_import': True
                }
            # 计算登录后落地页：优先回到未登录前访问的原始地址（需有权限），否则按权限落首页/首个可访问页面
            is_admin = bool(user.is_admin)
            permissions = session.get('user_permissions')
            next_url = None
            if request.is_json:
                data_for_next = request.get_json(silent=True) or {}
                next_url = data_for_next.get('next')
            if not next_url:
                next_url = request.args.get('next')
            landing_url = resolve_login_redirect(next_url, is_admin, permissions)

            # 登录成功
            current_app.logger.info(f"用户 {username} 登录成功，落地页：{landing_url}")

            # 记录登录操作日志
            try:
                log_login(username, True)
            except Exception as e:
                current_app.logger.warning(f"记录登录操作日志失败: {str(e)}")

            if request.is_json:
                return jsonify({'message': '登录成功', 'redirect': landing_url}), 200
            else:
                return redirect(landing_url)
        else:
            # 记录失败并触发限速/锁定；返回剩余尝试次数，帮助用户感知风险
            record_failure(_identifier)
            _remaining = get_remaining_attempts(_identifier)
            current_app.logger.warning(f"用户 {username} 登录失败：用户名或密码错误")
            if request.is_json:
                if _remaining > 0:
                    _msg = f'用户名或密码错误（还可尝试 {_remaining} 次）'
                else:
                    _msg = '登录尝试次数过多，请稍后重试'
                return jsonify({'message': _msg}), 401
            else:
                flash('用户名或密码错误', 'danger')
                return redirect(url_for('main.login', next=request.url))

    except Exception as e:
        current_app.logger.error(f"登录处理错误: {str(e)}", exc_info=True)
        return jsonify({'message': '服务器内部错误，请稍后重试'}), 500

    return render_template('login.html')

# 退出路由
@main_bp.route('/logout')
def logout():
    # 记录登出操作日志
    try:
        username = session.get('username', '未知用户')
        log_logout(username)
    except Exception as e:
        current_app.logger.warning(f"记录登出操作日志失败: {str(e)}")
    
    # 清除整个会话
    logout_user()
    session.clear()
    # 注意：不要把登出页自身 URL 塞进 next，否则再次登录会被跳回 /logout
    # 再次清空会话，形成"登录→登出→登录"死循环
    return redirect(url_for('main.login'))
# 用户管理页面路由
@main_bp.route('/user_management', methods=['GET', 'POST'])
def user_management():
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    # 页面访问权限控制：仅管理员或拥有系统管理权限的用户可访问
    if not check_permission(session['user_id'], 'can_system_management'):
        return forbidden_page('您没有用户管理页面的访问权限')

    if request.method == 'POST':
        action = request.form.get('action')
        # 操作级权限校验：管理员自动通过，非管理员需具备对应操作权限
        action_permission_map = {
            'add_user': 'can_user_mgmt_add_user',
            'change_password': 'can_user_mgmt_change_password',
            'reset_password': 'can_user_mgmt_reset_password',
            'permission_management': 'can_user_mgmt_manage_permissions',
            'delete_permission': 'can_user_mgmt_manage_permissions',
            'delete_user': 'can_user_mgmt_delete_user',
            'toggle_user_status': 'can_user_mgmt_delete_user',
        }
        if action in action_permission_map:
            if not check_permission(session['user_id'], action_permission_map[action]):
                flash('您没有权限执行该操作', 'error')
                return redirect(url_for('main.user_management'))
        if action == 'add_user':
            username = request.form.get('username')
            password = request.form.get('password')
            is_admin = request.form.get('is_admin') == 'on'
            is_operator = request.form.get('is_operator') == 'on'
            full_name = (request.form.get('full_name') or '').strip() or None
            department = (request.form.get('department') or '').strip() or None

            if UserInfo.query.filter_by(username=username).first():
                flash('用户名已存在', 'error')
            else:
                # 记录操作人信息
                current_user_id = session.get('user_id')
                new_user = UserInfo(
                    username=username, 
                    password=generate_password_hash(password), 
                    full_name=full_name,
                    department=department,
                    is_admin=is_admin,
                    is_operator=is_operator,
                    created_by=current_user_id,
                    updated_by=current_user_id
                )

                db.session.add(new_user)

                db.session.commit()

                # 为新用户分配默认权限
                permission = PermissionInfo(
                    user_id=new_user.id, 
                    can_upload=False, 
                    can_delete=False, 
                    can_batch_delete=False, 
                    can_page=False,
                    can_vehicle_types=False,
                    can_products=False,
                    can_material_download=False,
                    can_file_management=True,
                    can_file_comparison=False,
                    can_sub_list=False,
                    can_usage_list=False,
                    can_products_add=False,
                    can_products_edit=False,
                    can_products_delete=False,
                    can_products_download=False,
                    can_products_import=False,
                    can_vehicle_types_add=False,
                    can_vehicle_types_edit=False,
                    can_vehicle_types_delete=False,
                    can_vehicle_types_download=False,
                    can_material_download_download=False,
                    can_material_download_update=False,
                    can_file_management_upload=False,
                    can_file_management_delete=False,
                    can_file_management_download=False,
                    can_file_comparison_compare=False,
                    can_file_comparison_download=False,
                    can_sub_list_add=False,
                    can_sub_list_edit=False,
                    can_sub_list_delete=False,
                    can_sub_list_download=False,
                    can_usage_list_add=False,
                    can_usage_list_edit=False,
                    can_usage_list_delete=False,
                    can_usage_list_download=False
                )

                db.session.add(permission)

                db.session.commit()

                try:
                    from utils.operation_logger import log_action
                    from models.operation_log import OperationType, TargetType
                    log_action(OperationType.ADD_USER, f"添加用户: {username}",
                               TargetType.USER, new_user.id, username)
                except Exception as e:
                    current_app.logger.warning(f"记录添加用户日志失败: {e}")

                flash('用户添加成功', 'success')
        elif action == 'change_password':
            user_id = request.form.get('user_id')
            new_password = request.form.get('new_password')
            # current_app.logger.info(f"尝试修改用户ID: {user_id} 的密码")

            user = db.session.get(UserInfo, user_id)
            if user:
                # current_app.logger.info(f"找到用户: {user.username}，原密码哈希: {user.password[:20]}...")
                user.password = generate_password_hash(new_password)
                # 记录修改者信息
                current_user_id = session.get('user_id')
                if hasattr(user, 'updated_by'):
                    user.updated_by = current_user_id
                if hasattr(user, 'update_time'):
                    user.update_time = datetime.now()

                db.session.commit()
                # current_app.logger.info(f"密码已更新，新密码哈希: {user.password[:20]}...")
                try:
                    from utils.operation_logger import log_action
                    from models.operation_log import OperationType, TargetType
                    log_action(OperationType.CHANGE_PASSWORD, f"修改密码: {user.username}",
                               TargetType.USER, user.id, user.username)
                except Exception as e:
                    current_app.logger.warning(f"记录修改密码日志失败: {e}")
                if user.id == session.get('user_id'):
                    logout_user()
                    session.clear()
                    # 会话已清空，必须直接跳转登录页；
                    # 否则继续向下渲染 user_management 会因 session['user_id'] 缺失抛 500
                    flash('密码修改成功，请重新登录', 'success')
                    return redirect(url_for('main.login'))
                flash('密码修改成功', 'success')
            else:
                flash('用户不存在', 'error')
        elif action == 'delete_user':
            from sqlalchemy import text

            user_id = request.form.get('user_id')
            user = db.session.get(UserInfo, user_id)
            # 保护 admin 账号：任何账号（包括 admin 本人）都不可删除
            if user and user.username == 'admin':
                current_app.logger.warning(f"用户 {session.get('username')} 尝试删除 admin 账号，已被拒绝")
                flash('admin 账号为系统内置账号，不允许删除', 'error')
            elif user:
                try:
                    deleted_username = user.username
                    # 1) 保留该用户的操作日志：仅置空 user_id 关联，回填姓名的文本快照，
                    #    确保账号删除后历史操作记录与操作人文本都不丢失
                    db.session.execute(text(
                        "UPDATE operation_logs SET user_id = NULL, "
                        "username = COALESCE(username, :uname) WHERE user_id = :uid"
                    ), {'uid': user_id, 'uname': deleted_username})
                    # 私有权限数据直接清除
                    PermissionInfo.query.filter_by(user_id=user_id).delete()
                    db.session.execute(text("DELETE FROM user_group WHERE user_id = :uid"), {'uid': user_id})
                    db.session.execute(text("DELETE FROM user_permission_override WHERE user_id = :uid"), {'uid': user_id})

                    # 2) 动态发现所有引用 user_info 的外键列，将非空引用置空
                    #    这样以后新增业务表也无需再手动维护清理列表
                    #    注意：XXX_by_name 等姓名快照文本列不是外键，不会被 pragma 发现，
                    #         因此会自动保留；此处仅置空真正的 user_id 外键列。
                    fk_rows = db.session.execute(text(
                        "SELECT m.name AS tbl, p.\"from\" AS col "
                        "FROM sqlite_master m JOIN pragma_foreign_key_list(m.name) p "
                        "WHERE m.type = 'table' AND p.\"table\" = 'user_info'"
                    )).fetchall()
                    for tbl, col in fk_rows:
                        # 跳过 user_info 自身（created_by/updated_by）稍后单独处理，避免 WHERE 冲突
                        db.session.execute(text(
                            f'UPDATE "{tbl}" SET "{col}" = NULL '
                            f'WHERE "{col}" = :uid AND "{tbl}" != \'user_info\''
                        ), {'uid': user_id})
                    # user_info 自引用（创建人/更新人）单独置空
                    db.session.execute(text("UPDATE user_info SET created_by = NULL WHERE created_by = :uid"), {'uid': user_id})
                    db.session.execute(text("UPDATE user_info SET updated_by = NULL WHERE updated_by = :uid"), {'uid': user_id})

                    # 3) 删除用户
                    db.session.delete(user)
                    db.session.commit()
                    # 用户及其权限已删除，清除相关缓存
                    query_cache_manager.invalidate_user_cache(user_id)
                    invalidate_legacy_permission_cache(user_id)
                    try:
                        from utils.operation_logger import log_action
                        from models.operation_log import OperationType, TargetType
                        log_action(OperationType.DELETE_USER, f"删除用户: {deleted_username}",
                                   TargetType.USER, None, deleted_username)
                    except Exception as e:
                        current_app.logger.warning(f"记录删除用户日志失败: {e}")
                    flash('用户删除成功', 'success')
                except Exception as e:
                    db.session.rollback()
                    current_app.logger.exception(f'删除用户 {user_id} 失败: {e}')
                    flash('删除用户失败，请稍后重试', 'error')
            else:
                flash('用户不存在', 'error')
        elif action == 'toggle_user_status':
            user_id = request.form.get('user_id')
            # 目标状态：'1'/'on'/True 表示启用，其余表示禁用
            enable = str(request.form.get('enable', '')).lower() in ('1', 'true', 'on', 'yes')
            user = db.session.get(UserInfo, user_id)
            if not user:
                flash('用户不存在', 'error')
            elif user.username == 'admin':
                current_app.logger.warning(f"用户 {session.get('username')} 尝试禁用 admin 账号，已被拒绝")
                flash('admin 账号为系统内置账号，不允许禁用', 'error')
            elif user.id == session.get('user_id'):
                flash('不能禁用自己的账号', 'error')
            else:
                user.is_active = enable
                user.updated_by = session.get('user_id')
                user.update_time = datetime.now()
                db.session.commit()
                # 权限缓存与账号状态相关，禁用后立即失效
                query_cache_manager.invalidate_user_cache(user_id)
                invalidate_legacy_permission_cache(user_id)
                try:
                    from utils.operation_logger import log_action
                    from models.operation_log import OperationType, TargetType
                    opt = OperationType.ENABLE_USER if enable else OperationType.DISABLE_USER
                    log_action(opt, f"{'启用' if enable else '禁用'}账号: {user.username}",
                               TargetType.USER, user.id, user.username)
                except Exception as e:
                    current_app.logger.warning(f"记录账号状态日志失败: {e}")
                flash('账号已启用' if enable else '账号已禁用', 'success')
        elif action == 'reset_password':
            user_id = request.form.get('user_id')
            user = db.session.get(UserInfo, user_id)
            if user:
                # 修复：不再写死弱口令 123456。管理员可指定 new_password；
                # 若不指定则生成 16 位强随机口令并回显给操作者，由其安全交付给该用户。
                import secrets as _secrets, string as _string
                _new_pwd = (request.form.get('new_password') or '').strip()
                if not _new_pwd:
                    _alphabet = _string.ascii_letters + _string.digits + '!@#$%^&*-_=+'
                    _new_pwd = ''.join(_secrets.choice(_alphabet) for _ in range(16))
                    flash(f'已生成强随机密码：{_new_pwd}（请妥善交付给用户）', 'success')
                # 记录修改者信息
                current_user_id = session.get('user_id')
                if hasattr(user, 'updated_by'):
                    user.updated_by = current_user_id
                if hasattr(user, 'update_time'):
                    user.update_time = datetime.now()

                db.session.commit()
                try:
                    from utils.operation_logger import log_action
                    from models.operation_log import OperationType, TargetType
                    log_action(OperationType.RESET_PASSWORD, f"重置密码: {user.username}",
                               TargetType.USER, user.id, user.username)
                except Exception as e:
                    current_app.logger.warning(f"记录重置密码日志失败: {e}")
                flash('密码重置成功', 'success')
            else:
                flash('用户不存在', 'error')
        elif action == 'delete_permission':
            user_id = request.form.get('user_id')
            user = db.session.get(UserInfo, user_id)
            if user:
                # 删除用户的权限记录
                permission = PermissionInfo.query.filter_by(user_id=user_id).first()
                if permission:
                    db.session.delete(permission)
                    db.session.commit()
                    # 记录操作日志
                    admin_user = db.session.get(UserInfo, session['user_id'])
                    current_app.logger.info(f"用户 {admin_user.username} 删除了用户 {user.username} 的所有权限")
                    try:
                        from utils.operation_logger import log_action
                        from models.operation_log import OperationType, TargetType
                        log_action(OperationType.DELETE_PERMISSION, f"删除权限记录: {user.username}",
                                   TargetType.PERMISSION, user.id, user.username)
                    except Exception as e:
                        current_app.logger.warning(f"记录删除权限日志失败: {e}")
                    flash('用户权限删除成功', 'success')
                else:
                    flash('用户无权限记录', 'error')
            else:
                flash('用户不存在', 'error')
        elif action == 'permission_management':
            user_id = request.form.get('user_id')
            permissions = request.form.getlist('permissions')
            # 规范化前端权限键名到模型字段（别名映射）
            alias_map = {
                'can_vehicle_types_import': 'can_vehicle_types_import_excel'
            }
            permissions = [alias_map.get(p, p) for p in permissions]
            
            # 获取或创建权限记录
            permission = PermissionInfo.query.filter_by(user_id=user_id).first()
            if not permission:
                permission = PermissionInfo(user_id=user_id)
                # 设置创建人信息
                current_user_id = session.get('user_id')
                if hasattr(permission, 'created_by'):
                    permission.created_by = current_user_id
                if hasattr(permission, 'updated_by'):
                    permission.updated_by = current_user_id
                if hasattr(permission, 'create_time'):
                    permission.create_time = datetime.now()
                if hasattr(permission, 'update_time'):
                    permission.update_time = datetime.now()
                db.session.add(permission)
            
            # 重置所有权限为False
            for field in permission.__table__.columns:
                if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                    try:
                        setattr(permission, field.name, False)
                    except AttributeError:
                        # 忽略不存在的字段
                        pass
            
            # 设置选中的权限为True
            for perm in permissions:
                if hasattr(permission, perm):
                    try:
                        setattr(permission, perm, True)
                    except AttributeError:
                        # 忽略不存在的字段
                        pass
            
            # 记录操作人信息
            current_user_id = session.get('user_id')
            
            # 更新PermissionInfo的操作人信息
            if hasattr(permission, 'updated_by'):
                permission.updated_by = current_user_id
            if hasattr(permission, 'update_time'):
                permission.update_time = datetime.now()
            
            # 更新对应的UserInfo的操作人信息
            user = db.session.get(UserInfo, user_id)
            if user:
                user.updated_by = current_user_id
                user.update_time = datetime.now()
            
            db.session.commit()
            # 同步写入 RBAC 覆盖表，使 RBAC 成为唯一权威来源
            try:
                from utils.rbac_service import sync_overrides_from_selected, invalidate_user_permission_cache
                sync_overrides_from_selected(user_id, set(permissions),
                                             operator_id=session.get('user_id'))
                invalidate_user_permission_cache(user_id)
            except Exception as e:
                current_app.logger.warning(f"同步 RBAC 权限覆盖异常: {e}")
            # 权限已变更，清除该用户的权限缓存，避免中间件读到旧权限
            query_cache_manager.invalidate_user_cache(user_id)
            invalidate_legacy_permission_cache(user_id)
            try:
                from utils.operation_logger import log_action
                from models.operation_log import OperationType, TargetType
                target_name = user.username if user else str(user_id)
                log_action(OperationType.UPDATE_PERMISSIONS,
                           f"更新权限: {target_name}（{len(permissions)} 项）",
                           TargetType.PERMISSION, user_id, target_name)
            except Exception as e:
                current_app.logger.warning(f"记录权限更新日志失败: {e}")
            flash('权限更新成功', 'success')

    from sqlalchemy.orm import joinedload
    users = UserInfo.query.options(
        joinedload(UserInfo.modifier),
        joinedload(UserInfo.creator),
        joinedload(UserInfo.permission)
    ).all()
    # 当前登录用户信息，用于前端按钮级权限控制
    current_user = db.session.get(UserInfo, session['user_id'])
    return render_template('user_management.html', users=users, current_user=current_user)
@main_bp.route('/api/users/<int:user_id>/permissions', methods=['GET'])
@csrf_exempt_route
def get_user_permissions(user_id):
    # 只要已登录即可访问；非管理员仅可获取自己的权限
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    if not session.get('is_admin') and session.get('user_id') != user_id:
        return jsonify({'error': 'Forbidden'}), 403

    try:
        # 首先检查用户是否是管理员
        user = db.session.get(UserInfo, user_id)
        if user and user.is_admin:
            # 管理员拥有所有权限
            permissions = []
            # 获取所有权限字段
            for field in PermissionInfo.__table__.columns:
                if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                    permissions.append(field.name)
            return jsonify({'permissions': permissions})
        
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if permission:
            permissions = []
            # 动态获取所有权限字段
            for field in permission.__table__.columns:
                if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                    try:
                        if getattr(permission, field.name):
                            permissions.append(field.name)
                    except AttributeError:
                        # 忽略不存在的字段
                        pass
            return jsonify({'permissions': permissions})
        else:
            return jsonify({'permissions': []})
    except Exception as e:
        current_app.logger.error(f"获取用户权限失败: {str(e)}")
        return jsonify({'error': '获取权限失败'}), 500

@main_bp.route('/api/users/<int:user_id>/permissions', methods=['POST'])
@csrf_exempt_route
def update_user_permissions(user_id):
    # 检查用户是否登录且为管理员
    if 'user_id' not in session or not session['is_admin']:
        return jsonify({'success': False, 'error': '权限不足'}), 403
    
    try:
        permission = PermissionInfo.query.filter_by(user_id=user_id).first()
        if not permission:
            permission = PermissionInfo(user_id=user_id)
            # 设置创建人信息
            current_user_id = session.get('user_id')
            if hasattr(permission, 'created_by'):
                permission.created_by = current_user_id
            if hasattr(permission, 'updated_by'):
                permission.updated_by = current_user_id
            if hasattr(permission, 'create_time'):
                permission.create_time = datetime.now()
            if hasattr(permission, 'update_time'):
                permission.update_time = datetime.now()

            db.session.add(permission)

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': '无效的请求数据'}), 400
            
        permissions = data.get('permissions', [])
        # 规范化前端权限键名到模型字段（别名映射）
        alias_map = {
            'can_vehicle_types_import': 'can_vehicle_types_import_excel'
        }
        permissions = [alias_map.get(p, p) for p in permissions]

        # 重置所有权限为False
        for field in permission.__table__.columns:
            if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                try:
                    setattr(permission, field.name, False)
                except AttributeError:
                    # 忽略不存在的字段
                    pass
        
        # 设置选中的权限为True
        for perm in permissions:
            if hasattr(permission, perm):
                try:
                    setattr(permission, perm, True)
                except AttributeError:
                    # 忽略不存在的字段
                    pass


        # 记录操作人信息
        current_user_id = session.get('user_id')
        
        # 更新PermissionInfo的操作人信息
        if hasattr(permission, 'updated_by'):
            permission.updated_by = current_user_id
        if hasattr(permission, 'update_time'):
            permission.update_time = datetime.now()
        
        # 更新对应的UserInfo的操作人信息
        user = db.session.get(UserInfo, user_id)
        if user:
            user.updated_by = current_user_id
            user.update_time = datetime.now()

        try:
            # bump 版本，使已登录会话失效
            # 不再维护会话版本
            db.session.commit()
        except Exception as e:
            # 如果因为缺少列导致失败，尝试自动补齐列后重试（MySQL）
            if False and 'session_version' in str(e):
                try:
                    from sqlalchemy import text
                    db.session.rollback()
                    pass
                    db.session.commit()
                    # 重新设置并提交
                    pass
                    db.session.commit()
                except Exception:
                    db.session.rollback()
                    raise
            else:
                db.session.rollback()
                raise
        
        # 同步写入新 RBAC 用户覆盖表：勾选的置 allow，未勾选的置 deny。
        # 保证以 RBAC 为唯一权威来源后，权限变更立即对 check_permission 等读取路径生效。
        try:
            from utils.rbac_service import sync_overrides_from_selected
            ok_rbac, msg_rbac = sync_overrides_from_selected(
                user_id, set(permissions), operator_id=session.get('user_id'))
            if not ok_rbac:
                current_app.logger.warning(f"同步 RBAC 权限覆盖失败: {msg_rbac}")
        except Exception as e:
            current_app.logger.warning(f"同步 RBAC 权限覆盖异常: {e}")

        # 记录权限变更日志
        # 权限已变更，清除该用户的权限缓存，避免中间件读到旧权限
        query_cache_manager.invalidate_user_cache(user_id)
        invalidate_legacy_permission_cache(user_id)
        try:
            from utils.rbac_service import invalidate_user_permission_cache
            invalidate_user_permission_cache(user_id)
        except Exception:
            pass
        user = db.session.get(UserInfo, user_id)
        if user:
            admin_user = db.session.get(UserInfo, session['user_id'])
            current_app.logger.info(f"用户 {admin_user.username} 修改了用户 {user.username} 的权限")
            try:
                from utils.operation_logger import log_action
                from models.operation_log import OperationType, TargetType
                log_action(OperationType.UPDATE_PERMISSIONS,
                           f"更新权限: {user.username}（{len(permissions)} 项）",
                           TargetType.PERMISSION, user_id, user.username)
            except Exception as e:
                current_app.logger.warning(f"记录权限更新日志失败: {e}")
        # 如果更新的是当前登录用户的权限，刷新session中的权限信息
        if user_id == session.get('user_id'):
            if not user.is_admin:
                # 动态获取所有权限字段
                user_permissions = {}
                for field in permission.__table__.columns:
                    if field.name.startswith('can_') and field.name != 'id' and field.name != 'user_id':
                        user_permissions[field.name] = getattr(permission, field.name)
                session['user_permissions'] = user_permissions
                # 不再把会话版本写入 session
        
        return jsonify({'success': True})
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"更新用户权限失败: {str(e)}")
        return jsonify({'success': False, 'error': '更新权限失败'}), 500


# 权限管理路由
@main_bp.route('/permission_management', methods=['GET', 'POST'])
def permission_management():
    """旧版权限管理页已停用，统一跳转到「用户管理」页（权限维护已并入其中）。"""
    flash('权限维护已并入「用户管理」页面，请到该页面修改', 'info')
    return redirect(url_for('main.user_management'))


# 在每次请求前刷新当前登录用户的会话权限，确保前端显示与数据库一致
@main_bp.before_app_request
def refresh_session_permissions():
    try:
        # 静态资源请求无需刷新权限，避免无谓的缓存/数据库查询
        if request.endpoint == 'static':
            return
        if 'user_id' in session:
            # 账号禁用即时生效：已禁用的登录用户在下一次请求时强制下线
            if request.endpoint not in ('main.login', 'main.logout'):
                current = db.session.get(UserInfo, session['user_id'])
                if current is None or not getattr(current, 'is_active', True):
                    session.clear()
                    logout_user()
                    if request.path.startswith('/api/') or request.accept_mimetypes.best == 'application/json':
                        return jsonify({'success': False, 'error': '账号已被禁用，请重新登录', 'code': 403}), 403
                    flash('该账号已被禁用，请联系管理员', 'danger')
                    return redirect(url_for('main.login', next=request.url))
            # 走带缓存的权限读取，避免每个请求固定 2 次数据库查询
            data = get_cached_user_permission(session['user_id'])
            if not data:
                return
            if data.get('is_admin'):
                # 管理员无需刷新，模板通过 is_admin 放行
                return
            if session.get('user_permissions') != {k: v for k, v in data.items() if k != 'is_admin'}:
                session['user_permissions'] = {k: v for k, v in data.items() if k != 'is_admin'}
    except Exception as e:
        current_app.logger.error(f"刷新会话权限失败: {str(e)}")


# 管理端：对比某用户数据库权限与会话权限（仅管理员可用，用于调试）
# @main_bp.route('/admin/debug/permissions/<int:user_id>', methods=['GET'])
# def admin_debug_permissions(user_id):
#     if 'user_id' not in session or not session.get('is_admin'):
#         return jsonify({'success': False, 'error': 'Unauthorized'}), 401
# 
#     try:
#         user = UserInfo.query.get(user_id)
#         if not user:
#             return jsonify({'success': False, 'error': '用户不存在'}), 404
# 
#         permission = PermissionInfo.query.filter_by(user_id=user_id).first()
#         db_true = []
#         if permission:
#             for field in permission.__table__.columns:
#                 if field.name.startswith('can_') and field.name not in ('id', 'user_id'):
#                     if getattr(permission, field.name):
#                         db_true.append(field.name)
# 
#         session_true = []
#         if user_id == session.get('user_id'):
#             sess = session.get('user_permissions', {}) or {}
#             session_true = [k for k, v in sess.items() if v]
#         else:
#             # 对于非当前登录用户，无会话快照，返回空并提示
#             session_true = None
# 
#         diff = {
#             'only_in_db': sorted(list(set(db_true) - set(session_true or []))),
#             'only_in_session': sorted(list(set(session_true or []) - set(db_true)))
#         }
# 
#         return jsonify({
#             'success': True,
#             'user': {'id': user.id, 'username': user.username, 'is_admin': user.is_admin},
#             'db_permissions_true': sorted(db_true),
#             'session_permissions_true': sorted(session_true) if session_true is not None else None,
#             'diff': diff,
#             'note': '若 session_permissions_true 为 null，说明被对比的不是当前登录用户；请用该用户登录后再调用以比较会话权限'
#         })
#     except Exception as e:
#         current_app.logger.error(f"调试权限对比失败: {str(e)}")
#         return jsonify({'success': False, 'error': '内部错误'}), 500


# 权限检查装饰器
def require_permission(permission_name):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):  # 修复参数错误
            if 'user_id' not in session:
                flash('请先登录', 'error')
                return redirect(url_for('main.login', next=request.url))

            user_id = session['user_id']
            user = db.session.get(UserInfo, user_id)

            # 管理员拥有所有权限
            if user and user.is_admin:
                return f(*args, **kwargs)

            permission = PermissionInfo.query.filter_by(user_id=user_id).first()
            if not permission:
                flash('权限配置错误，请联系管理员', 'error')
                return forbidden_page('权限配置错误，请联系管理员')

            # 检查所需权限
            if permission_name == 'upload' and not permission.can_upload:
                flash('您没有上传文件的权限', 'error')
                return redirect(url_for('main.file_management'))
            elif permission_name == 'delete' and not permission.can_delete:
                flash('您没有删除文件的权限', 'error')
                return redirect(url_for('main.file_management'))
            elif permission_name == 'batch_delete' and not permission.can_batch_delete:
                flash('您没有批量删除文件的权限', 'error')
                return redirect(url_for('main.file_management'))
            elif permission_name == 'user_management' and not permission.can_page:
                flash('您没有访问用户管理页面的权限', 'error')
                return redirect(url_for('main.file_management'))

            return f(*args, **kwargs)

        return decorated_function

    return decorator


# 菜单管理页面路由
@main_bp.route('/menu_management')
@login_required
def menu_management():
    """菜单管理页面"""
    # 检查用户是否有菜单管理权限
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_page:
            flash('您没有菜单管理权限', 'error')
            return forbidden_page('您没有菜单管理权限')
    
    return render_template('menu_management.html')


def _can_edit_sidebar_menu():
    """侧边栏分组管理权限：管理员，或拥有页面配置权限(can_page)的用户。"""
    user = db.session.get(UserInfo, session['user_id'])
    if not user:
        return False
    if user.is_admin:
        return True
    permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
    return bool(permission and permission.can_page)


# 侧边栏分组配置API（管理员在线调整分组标题、顺序与条目归属）
@main_bp.route('/api/sidebar_menu', methods=['GET'])
@csrf_exempt_route
def get_sidebar_menu_config():
    """读取当前分组配置 + 内置菜单目录，供菜单管理页渲染"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not _can_edit_sidebar_menu():
        return jsonify({'success': False, 'error': '无菜单管理权限'}), 403

    try:
        from utils.sidebar_menu import get_admin_catalog, load_layout
        return jsonify({'success': True, 'layout': load_layout(), 'catalog': get_admin_catalog()})
    except Exception as e:
        current_app.logger.error(f"读取侧边栏分组失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '读取分组配置失败'}), 500


@main_bp.route('/api/sidebar_menu', methods=['POST'])
@csrf_exempt_route
def save_sidebar_menu_config():
    """保存分组配置：只落库分组标题/顺序/条目归属，页面权限仍由内置目录控制"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not _can_edit_sidebar_menu():
        return jsonify({'success': False, 'error': '无菜单管理权限'}), 403

    data = request.get_json(silent=True) or {}
    payload = data.get('layout') if isinstance(data.get('layout'), dict) else data
    if not isinstance(payload, dict) or not isinstance(payload.get('groups'), list):
        return jsonify({'success': False, 'error': '配置格式不正确'}), 400

    try:
        from utils.sidebar_menu import save_layout
        saved = save_layout(payload)
        return jsonify({'success': True, 'layout': saved, 'message': '分组配置已保存'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存侧边栏分组失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '保存失败，请稍后重试'}), 500


@main_bp.route('/api/sidebar_menu/reset', methods=['POST'])
@csrf_exempt_route
def reset_sidebar_menu_config():
    """恢复默认分组（按业务域内置的默认布局）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not _can_edit_sidebar_menu():
        return jsonify({'success': False, 'error': '无菜单管理权限'}), 403

    try:
        from utils.sidebar_menu import reset_layout
        layout = reset_layout()
        return jsonify({'success': True, 'layout': layout, 'message': '已恢复默认分组'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"恢复侧边栏默认分组失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '恢复失败，请稍后重试'}), 500

# 根路由 - 已登录用户重定向到文件管理首页，未登录用户重定向到登录页面
@main_bp.route('/')
def index():
    """网站主页（仪表盘）：登录用户均可访问，无权限的功能入口不展示"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    # 实时刷新权限，避免 session 中为旧快照（新增权限列后无需重新登录）
    if not session.get('is_admin'):
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if permission:
            user_permissions = {}
            for field in permission.__table__.columns:
                if field.name.startswith('can_') and field.name not in ('id', 'user_id'):
                    user_permissions[field.name] = getattr(permission, field.name)
            session['user_permissions'] = user_permissions
    return render_template('index.html', developer_info=get_developer_info())


def get_developer_info():
    """首页用户信息卡展示的开发人员信息（来源：系统设置页配置）"""
    from models.system_setting import get_developer_info as _get
    try:
        return _get()
    except Exception as e:
        current_app.logger.error(f"读取开发人员信息失败: {str(e)}")
        from models.system_setting import DEVELOPER_SETTING_DEFAULTS as _d
        return {k.replace('developer_', ''): v['value'] for k, v in _d.items()}


# ================= 系统设置（开发人员信息） =================
# 仅 admin 账号可访问/修改开发人员信息设置
DEVELOPER_SETTINGS_ADMIN_USERNAME = 'admin'


def can_manage_developer_settings(user):
    """仅允许 admin 账号管理开发人员信息设置"""
    return bool(user and user.username == DEVELOPER_SETTINGS_ADMIN_USERNAME)


@main_bp.route('/developer_settings')
@login_required
def developer_settings():
    """开发人员信息设置页面（仅 admin 账号）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    user = db.session.get(UserInfo, session['user_id'])
    if not can_manage_developer_settings(user):
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问开发人员信息设置，权限不足")
        return forbidden_page('仅 admin 账号可进行系统设置')
    return render_template('developer_settings.html',
                           developer_info=get_developer_info(),
                           maintenance_mode=_get_maintenance_mode_safe())


@main_bp.route('/api/developer_settings', methods=['GET', 'POST'])
@csrf_exempt_route
@login_required
def api_developer_settings():
    """开发人员信息读写接口（仅 admin 账号）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    user = db.session.get(UserInfo, session['user_id'])
    if not can_manage_developer_settings(user):
        return jsonify({'success': False, 'error': '仅 admin 账号可进行系统设置'}), 403

    from models.system_setting import set_setting, init_developer_settings, DEVELOPER_SETTING_DEFAULTS

    if request.method == 'GET':
        return jsonify({'success': True, 'data': get_developer_info()})

    try:
        data = request.get_json(silent=True) or {}
        name = (data.get('name') or '').strip()
        contact = (data.get('contact') or '').strip()
        feedback_text = (data.get('feedback_text') or '').strip()
        feedback_url = (data.get('feedback_url') or '').strip()

        if not name:
            return jsonify({'success': False, 'error': '开发人员姓名不能为空'}), 400
        if not contact:
            return jsonify({'success': False, 'error': '联系方式不能为空'}), 400
        if not feedback_text:
            return jsonify({'success': False, 'error': '反馈入口文字不能为空'}), 400
        if not feedback_url:
            return jsonify({'success': False, 'error': '反馈跳转地址不能为空'}), 400

        # 保证默认键存在后逐项更新
        init_developer_settings()
        set_setting('developer_name', name, DEVELOPER_SETTING_DEFAULTS['developer_name']['description'])
        set_setting('developer_contact', contact, DEVELOPER_SETTING_DEFAULTS['developer_contact']['description'])
        set_setting('developer_feedback_text', feedback_text, DEVELOPER_SETTING_DEFAULTS['developer_feedback_text']['description'])
        set_setting('developer_feedback_url', feedback_url, DEVELOPER_SETTING_DEFAULTS['developer_feedback_url']['description'])
        db.session.commit()

        return jsonify({'success': True, 'message': '保存成功', 'data': get_developer_info()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存开发人员信息失败: {str(e)}")
        return jsonify({'success': False, 'error': f'保存失败: {str(e)}'}), 500


# ================= 系统维护模式 =================
def _get_maintenance_mode_safe():
    """安全读取维护模式开关，读取异常时回退为关闭。"""
    try:
        from models.system_setting import is_maintenance_mode
        return is_maintenance_mode()
    except Exception:
        return False


@main_bp.route('/maintenance')
def maintenance_page():
    """系统维护提示页（维护模式开启时普通用户被跳转至此）"""
    return render_template('maintenance.html', is_admin=bool(session.get('is_admin')))


@main_bp.route('/api/maintenance_mode', methods=['GET', 'POST'])
@csrf_exempt_route
@login_required
def api_maintenance_mode():
    """系统维护模式开关读写接口（仅 admin 账号）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    user = db.session.get(UserInfo, session['user_id'])
    if not can_manage_developer_settings(user):
        return jsonify({'success': False, 'error': '仅 admin 账号可进行系统设置'}), 403

    from models.system_setting import is_maintenance_mode, set_maintenance_mode

    if request.method == 'GET':
        return jsonify({'success': True, 'enabled': is_maintenance_mode()})

    try:
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get('enabled'))
        set_maintenance_mode(enabled)
        current_app.logger.info(f"用户 {session.get('username')} 将系统维护模式设置为: {enabled}")
        return jsonify({'success': True, 'message': '设置成功', 'enabled': enabled})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"设置系统维护模式失败: {str(e)}")
        return jsonify({'success': False, 'error': f'设置失败: {str(e)}'}), 500


# ================= 快捷键设置（自定义网站快捷入口） =================
def _can_manage_shortcuts():
    """快捷键设置的管理权限：管理员或拥有系统管理权限(can_system_management)的用户。"""
    return check_permission(session.get('user_id'), 'can_system_management')


@main_bp.route('/shortcut_settings')
@login_required
def shortcut_settings():
    """快捷键设置页面：维护自定义网站清单（数量不限，地址可随时调整）。"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not _can_manage_shortcuts():
        current_app.logger.warning(f"用户 {session.get('username')} 尝试访问快捷键设置，权限不足")
        return forbidden_page('您没有快捷键设置的访问权限')
    return render_template('shortcut_settings.html')


def _alias_conflicts_with_system(alias):
    """固定入口是否与系统现有静态路由冲突（冲突时该入口永远不会生效）。"""
    return any(not rule.arguments and rule.rule == alias
               for rule in current_app.url_map.iter_rules())


@main_bp.route('/api/web_shortcuts', methods=['GET', 'POST'])
@csrf_exempt_route
@login_required
def api_web_shortcuts():
    """自定义网址映射读写接口：GET 查询清单，POST 新增或更新单条记录。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not _can_manage_shortcuts():
        return jsonify({'success': False, 'error': '您没有快捷键设置的访问权限'}), 403

    from utils.shortcut_settings import load_shortcuts, normalize_alias, save_shortcut

    if request.method == 'GET':
        return jsonify({'success': True, 'items': load_shortcuts()})

    try:
        payload = request.get_json(silent=True) or {}
        alias, error = normalize_alias(payload.get('alias'))
        if error:
            return jsonify({'success': False, 'error': error}), 400
        if _alias_conflicts_with_system(alias):
            return jsonify({'success': False, 'error': f'该网址 {alias} 与系统现有页面冲突，请更换'}), 400
        item, error = save_shortcut(payload)
        if error:
            return jsonify({'success': False, 'error': error}), 400
        return jsonify({'success': True, 'item': item, 'items': load_shortcuts(),
                        'message': '保存成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"保存自定义网址失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '保存失败，请稍后重试'}), 500


@main_bp.route('/api/web_shortcuts/delete', methods=['POST'])
@csrf_exempt_route
@login_required
def api_web_shortcuts_delete():
    """删除单条自定义网站记录。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not _can_manage_shortcuts():
        return jsonify({'success': False, 'error': '您没有快捷键设置的访问权限'}), 403

    from utils.shortcut_settings import delete_shortcut, load_shortcuts

    data = request.get_json(silent=True) or {}
    try:
        if not delete_shortcut(data.get('id')):
            return jsonify({'success': False, 'error': '记录不存在或已被删除'}), 404
        return jsonify({'success': True, 'items': load_shortcuts(), 'message': '删除成功'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除自定义网址失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '删除失败，请稍后重试'}), 500


@main_bp.route('/<path:alias_path>', methods=['GET'])
def shortcut_alias_redirect(alias_path):
    """自定义网址固定入口：按后台配置 302 跳转到可随时调整的目标地址。

    说明：该路由为兜底路由，系统已有的静态页面路由优先级更高，不会被覆盖；
    只有命中“快捷键设置”中登记过的入口路径才会跳转，其余仍按 404 处理。
    """
    from utils.shortcut_settings import find_shortcut_by_alias
    item = find_shortcut_by_alias('/' + alias_path)
    if not item:
        abort(404)
    return redirect(item['target'], code=302)


# 文件管理路由（上传、搜索、列表展示）
_file_info_schema_checked = False  # 模块级标记，避免每次请求都检查 schema

@main_bp.route('/file_management', methods=['GET', 'POST','OPTIONS'])
@login_required
def file_management():
    # 在函数开头导入 db：函数内有其他位置也 import db，
    # 若不提前导入，预检1等早期引用会因局部变量未赋值而抛 UnboundLocalError
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    # 页面访问权限控制
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_file_management:
            flash('您没有文件管理页面的访问权限', 'error')
            return forbidden_page('您没有文件管理页面的访问权限')

    get_upload_dir()  # 确保上传根目录存在（各类型子目录在保存时按需创建）

    # 运行时兜底：确保 SQLite 中 file_info 表存在所需列（只执行一次）
    global _file_info_schema_checked
    if not _file_info_schema_checked:
        try:
            from sqlalchemy import inspect, text
            inspector = inspect(db.engine)
            cols = {c['name'] for c in inspector.get_columns('file_info')}
            missing = []
            if 'eo' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN eo TEXT")
            if 'vehicle_type' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN vehicle_type TEXT")
            if 'first_subpno' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN first_subpno TEXT")
            if 'category' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN category VARCHAR(50)")
            if 'created_by' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN created_by INTEGER")
            if 'updated_by' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN updated_by INTEGER")
            # 冻结相关字段
            if 'is_frozen' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN is_frozen BOOLEAN NOT NULL DEFAULT 0")
            if 'frozen_at' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN frozen_at DATETIME")
            if 'frozen_by' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN frozen_by INTEGER")
            if 'frozen_reason' not in cols:
                missing.append("ALTER TABLE file_info ADD COLUMN frozen_reason VARCHAR(500)")
            if missing:
                for stmt in missing:
                    db.session.execute(text(stmt))
                db.session.commit()
            _file_info_schema_checked = True  # 标记已完成，后续请求跳过
        except Exception:
            # 忽略兜底失败，避免影响正常请求；推荐用正式迁移修复
            pass

    # 分页参数初始化
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(5, min(per_page, 1000))  # 限制每页显示数量在5-1000之间
    page = request.args.get('page', 1, type=int)
    page = max(1, page)  # 页码最小为1

    try:
        if request.method == 'POST':
            # 检查用户是否有上传权限
            user_id = session['user_id']
            user = db.session.get(UserInfo, user_id)

            # 管理员拥有所有权限
            if not user.is_admin:
                permission = PermissionInfo.query.filter_by(user_id=user_id).first()
                if not permission or not permission.can_file_management_upload:
                    flash('你没有上传文件的权限', 'error')
                    return redirect(request.url)

            # 文件上传逻辑
            if 'files' not in request.files:
                flash('未选择上传文件', 'error')
                return redirect(request.url)

            files = request.files.getlist('files')
            success_files = []
            failed_files = []
            duplicate_files = []

            # 是否为AJAX分批上传
            is_ajax_upload = request.headers.get('X-Requested-With') == 'XMLHttpRequest'

            # 记录批量上传开始日志（方案A）- 仅非AJAX模式使用，避免产生“预估”残留
            start_log_id = 0
            if not is_ajax_upload:
                try:
                    from utils.operation_logger import log_batch_file_upload_start
                    start_log_id = log_batch_file_upload_start(len(files))
                except Exception:
                    start_log_id = 0

            any_success = False
                        
            # 预检 1：批量检查文件名是否已存在（一次查询替代 N 次）
            all_secure_names = []
            file_map = {}  # secure_name -> file object
            for file in files:
                if not file.filename:
                    failed_files.append({'name': '未命名文件', 'reason': '文件名为空'})
                    continue
                if not allowed_file(file.filename):
                    failed_files.append({'name': file.filename, 'reason': '无效文件格式（仅支持 xls/xlsx）'})
                    continue
                secure_name = secure_filename(file.filename)
                all_secure_names.append(secure_name)
                file_map[secure_name] = file
                        
            # 批量查询已存在的文件名
            existing_filenames = set()
            if all_secure_names:
                try:
                    existing_records = db.session.query(FileInfo.filename).filter(
                        FileInfo.filename.in_(all_secure_names)
                    ).all()
                    existing_filenames = {r[0] for r in existing_records}
                except Exception:
                    pass
                        
            # 跳过已存在的文件
            valid_files = []
            for secure_name in all_secure_names:
                if secure_name in existing_filenames:
                    original_file = file_map[secure_name]
                    duplicate_files.append({'name': original_file.filename, 'reason': '文件已存在'})
                else:
                    valid_files.append(file_map[secure_name])
                        
            # ===== 阶段 1：保存文件到磁盘（按类型放入 SubList/UsageList 子文件夹） =====
            parse_items = []  # (file, secure_name, file_path, file_type, filesize)
            for file in valid_files:
                secure_name = secure_filename(file.filename)
                file_type = detect_file_type(secure_name)
                if not file_type:
                    failed_files.append({'name': file.filename, 'reason': '无法识别文件类型'})
                    continue
                # 按文件类型落盘到 static/uploads/<file_type>/ 子目录
                file_path = os.path.join(get_upload_dir(file_type), secure_name)
                try:
                    # 保存文件到磁盘
                    file.save(file_path)
                    parse_items.append((file, secure_name, file_path, file_type, os.path.getsize(file_path)))
                except Exception as e:
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    failed_files.append({'name': file.filename, 'reason': str(e)})

            # ===== 阶段 2：并行解析 Excel（纯文件读取，无 DB 操作，线程安全） =====
            parsed = {}  # secure_name -> ('ok', records, first_subpno, first_eo) | ('err', msg)
            if parse_items:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                def _parse_one(item):
                    _, sname, fpath, ftype, _ = item
                    try:
                        records, _ = parse_file_data(fpath, ftype)
                        sp = str(records[0].get('SubPno') or '').strip() or None if records else None
                        eo = str(records[0].get('EoKsc') or '').strip() or None if records else None
                        return sname, ('ok', records, sp, eo)
                    except Exception as ex:
                        return sname, ('err', str(ex))

                max_workers = min(4, len(parse_items))
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(_parse_one, item): item for item in parse_items}
                    for fut in as_completed(futures):
                        sname, result = fut.result()
                        parsed[sname] = result

            # ===== 阶段 3：内容首行查重（预检 2，写入前查询，无自命中问题）并收集待写入项 =====
            write_items = []  # (file, secure_name, file_path, file_type, filesize, records, first_subpno, first_eo)
            for item in parse_items:
                file, secure_name, file_path, file_type, filesize = item
                p = parsed.get(secure_name)
                if not p or p[0] == 'err':
                    reason = (p[1] if p and p[0] == 'err' else '文件解析失败')
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    failed_files.append({'name': file.filename, 'reason': reason})
                    continue
                _, records, content_subpno, content_eo = p
                # 预检 2：检查首行 SubPno+EoKsc 是否已存在
                is_duplicate = False
                if content_subpno and content_eo:
                    try:
                        if file_type == 'SubList':
                            existed = db.session.query(SubList.id).filter(
                                SubList.SubPno == content_subpno,
                                SubList.EoKsc == content_eo
                            ).first()
                        else:
                            existed = db.session.query(UsageList.id).filter(
                                UsageList.SubPno == content_subpno,
                                UsageList.EoKsc == content_eo
                            ).first()
                        if existed:
                            is_duplicate = True
                    except Exception:
                        pass  # 查询失败不阻塞上传
                if is_duplicate:
                    duplicate_files.append({'name': file.filename, 'reason': '首行品番+EO 与已存在数据相同'})
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
                    continue
                write_items.append((file, secure_name, file_path, file_type, filesize, records, content_subpno, content_eo))

            # ===== 阶段 4：批量写入（每请求一次 commit；失败时整批回滚后逐文件重试） =====
            if write_items:
                # 快速路径：全部文件在同一事务中写入，仅一次 commit。
                # SQLite 每次 commit 约 40-400ms（Windows fsync 慢），逐文件 commit 时
                # 1000 个文件会产生数百秒的 fsync 开销；合并为每请求一次后几乎可忽略。
                try:
                    for witem in write_items:
                        _, sname, _, ftype, _, records, sp, eo = witem
                        write_file_data(ftype, records, sname)
                        eo_part = eo if eo else extract_eo_from_filename(sname)
                        vehicle_type_part = extract_vehicle_type_from_filename(sname)
                        db.session.add(FileInfo(
                            filename=sname,
                            file_type=ftype,
                            upload_time=datetime.now(),
                            filesize=witem[4],
                            eo=eo_part,
                            vehicle_type=vehicle_type_part,
                            first_subpno=sp,
                            category=ftype,
                            created_by=user_id,
                            updated_by=user_id
                        ))
                    db.session.commit()
                    db.session.expire_all()
                    for witem in write_items:
                        success_files.append(witem[0].filename)
                    any_success = True
                except Exception as batch_ex:
                    # 慢速回退：整批回滚后逐文件独立事务重试，保证失败隔离与准确提示。
                    # 该路径仅在批量提交失败时触发（如并发同名文件触发唯一约束），频率很低。
                    current_app.logger.error(f'批量写入失败，转为逐文件重试: {batch_ex}')
                    db.session.rollback()
                    for witem in write_items:
                        file, sname, fpath, ftype, _, records, sp, eo = witem
                        try:
                            write_file_data(ftype, records, sname)
                            eo_part = eo if eo else extract_eo_from_filename(sname)
                            vehicle_type_part = extract_vehicle_type_from_filename(sname)
                            db.session.add(FileInfo(
                                filename=sname,
                                file_type=ftype,
                                upload_time=datetime.now(),
                                filesize=witem[4],
                                eo=eo_part,
                                vehicle_type=vehicle_type_part,
                                first_subpno=sp,
                                category=ftype,
                                created_by=user_id,
                                updated_by=user_id
                            ))
                            db.session.commit()
                            db.session.expire_all()
                            success_files.append(file.filename)
                            any_success = True
                        except Exception as ex:
                            db.session.rollback()
                            if ('uq_file_info_filename' in str(ex) or 'Duplicate' in str(ex) or
                                    'UNIQUE constraint failed' in str(ex) or 'UNIQUE constraint' in str(ex)):
                                duplicate_files.append({'name': file.filename, 'reason': '文件已存在'})
                            else:
                                failed_files.append({'name': file.filename, 'reason': str(ex)})
                            try:
                                os.remove(fpath)
                            except OSError:
                                pass

            # 上传完成后失效列表/计数缓存，确保新数据立即可见
            if any_success:
                try:
                    query_cache_manager.invalidate_list_cache()
                    
                    # 批量上传时跳过自动冻结（冻结逻辑对每个旧文件执行 2 次全表 UPDATE，
                    # 610 个文件会触发数千次 UPDATE，导致上传极慢）。
                    # 如需冻结旧版本，请在上传完成后通过页面手动操作。
                except Exception as commit_err:
                    current_app.logger.error(f'提交会话失败: {commit_err}')
                    try:
                        db.session.rollback()
                    except Exception:
                        pass

            # 完成后更新开始日志为完成（若有）
            try:
                if start_log_id:
                    from utils.operation_logger import log_batch_file_upload_finalize
                    log_batch_file_upload_finalize(start_log_id, success_files, len(success_files))
            except Exception:
                pass

            # AJAX支持：若为异步请求，直接返回JSON汇总，不设置flash消息
            if is_ajax_upload:
                result = {
                    'success': True,
                    'success_count': len(success_files),
                    'duplicate_count': len(duplicate_files),
                    'failed_count': len(failed_files),
                    'success_files': success_files,  # success_files 已经是字符串列表
                    'duplicates': [f.get('name') for f in duplicate_files],  # duplicate_files 是字典列表
                    'failed': failed_files,
                }
                return jsonify(result)

            # 非AJAX：使用闪现消息（合并为单条，优先级 error > warning > success）
            msg_success = f'成功上传 {len(success_files)} 个文件' if success_files else ''
            
            # 重复文件显示逻辑：1个文件显示文件名，多个文件显示"第一个文件等X个文件"
            if duplicate_files:
                if len(duplicate_files) == 1:
                    msg_warning = f'跳过重复文件: {duplicate_files[0]["name"]}'
                else:
                    msg_warning = f'跳过重复文件: {duplicate_files[0]["name"]} 等{len(duplicate_files)}个文件'
            else:
                msg_warning = ''
            
            msg_error = f'上传失败: {", ".join([f["name"] + "(" + f["reason"] + ")" for f in failed_files])}' if failed_files else ''
            if msg_error:
                flash(msg_error, 'error')
            elif msg_warning:
                flash('；'.join(filter(None, [msg_success, msg_warning])), 'warning')
            elif msg_success:
                flash(msg_success, 'success')

            # 记录批量上传操作日志（同一批次合并）
            try:
                if success_files and not start_log_id:
                    # 仅当未生成"开始日志"时才单独写合并日志
                    log_batch_file_upload(success_files, count=len(success_files))
            except Exception as _:
                current_app.logger.warning('记录批量上传日志失败（表单）')

            return redirect(request.url)

        # 搜索逻辑
        search_filename = request.args.get('search_filename', '').strip()
        search_vehicle_type = request.args.get('search_vehicle_type', '').strip()
        search_eo = request.args.get('search_eo', '').strip()
        search_date = request.args.get('search_date', '').strip()
        show_frozen = request.args.get('show_frozen', '').strip()
        
        # 构建查询：默认排除冻结文件，除非显式请求显示冻结文件
        query = FileInfo.query
        if show_frozen == 'true':
            # 显示冻结文件
            query = query.filter(FileInfo.is_frozen == True)
        elif show_frozen == 'all':
            # 显示所有文件（含冻结）
            pass
        else:
            # 默认：只显示未冻结文件
            query = query.filter(FileInfo.is_frozen == False)
        
        query = query.order_by(FileInfo.upload_time.desc())

        if search_filename:
            query = query.filter(FileInfo.filename.ilike(f'%{search_filename}%'))
        if search_vehicle_type:
            # 车种搜索：直接搜索vehicle_type列
            # 支持部分匹配，如搜索"JA"能找到"JA_PE2_FRDR_LH"
            query = query.filter(FileInfo.vehicle_type.ilike(f'%{search_vehicle_type}%'))
        if search_eo:
            # EO搜索：直接搜索EO列
            # 支持部分匹配，如搜索"d"能找到"d1"，搜索"1"能找到"d1"
            query = query.filter(FileInfo.eo.ilike(f'%{search_eo}%'))
        if search_date:
            query = apply_date_filter(query, search_date)

        # 分页处理
        total_items = query.count()
        total_pages = max(1, (total_items + per_page - 1) // per_page)  # 确保总页数≥1
        page = max(1, min(page, total_pages))  # 限制页码在有效范围
        files = query.paginate(page=page, per_page=per_page, error_out=False)

        # 获取用户权限并传递到模板
        user_id = session['user_id']
        current_user_obj = db.session.get(UserInfo, user_id)
        permissions = {}
        if current_user_obj and not current_user_obj.is_admin:
            perm_obj = PermissionInfo.query.filter_by(user_id=user_id).first()
            if perm_obj:
                # 获取所有权限字段
                from sqlalchemy import inspect as sa_inspect
                mapper = sa_inspect(PermissionInfo)
                for col in mapper.columns:
                    permissions[col.key] = getattr(perm_obj, col.key, False)

        # 传递参数到模板
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        is_mysql = str(db_uri).lower().startswith('mysql')
        return render_template(
            'file_management.html',
            files=files,
            search_filename=search_filename,
            search_vehicle_type=search_vehicle_type,
            search_eo=search_eo,
            search_date=search_date,
            show_frozen=show_frozen,
            per_page=per_page,
            db_is_mysql=is_mysql,
            permissions=permissions
        )

    except Exception as e:
        flash(f'服务器错误: {str(e)}', 'error')
        current_app.logger.error(f'文件管理页面错误: {str(e)}', exc_info=True)
        db_uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        is_mysql = str(db_uri).lower().startswith('mysql')
        
        # 获取用户权限并传递到模板
        user_id = session.get('user_id')
        permissions = {}
        if user_id:
            current_user_obj = db.session.get(UserInfo, user_id)
            if current_user_obj and not current_user_obj.is_admin:
                perm_obj = PermissionInfo.query.filter_by(user_id=user_id).first()
                if perm_obj:
                    from sqlalchemy import inspect as sa_inspect
                    mapper = sa_inspect(PermissionInfo)
                    for col in mapper.columns:
                        permissions[col.key] = getattr(perm_obj, col.key, False)
        
        return render_template(
            'file_management.html',
            files=FileInfo.query.filter(FileInfo.is_frozen == False).paginate(page=1, per_page=per_page, error_out=False),
            search_filename=search_filename,
            search_vehicle_type=search_vehicle_type,
            search_eo=search_eo,
            search_date=search_date,
            per_page=per_page,
            db_is_mysql=is_mysql,
            permissions=permissions
        )


# 下载文件路由
@main_bp.route('/download/<filename>')
def download_file(filename):
    # 权限：文件管理-下载(单个)
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_file_management_download:
            flash('你没有下载文件的权限', 'error')
            return redirect(url_for('main.file_management'))
    
    # 下载为只读操作：冻结文件允许下载（历史追溯需要），但绝不改变冻结状态
    # （原"访问即自动解冻"逻辑会导致 FileInfo 解冻而关联数据行未同步的状态不一致，已移除）

    # 从数据库读取文件数据并生成 Excel 提供下载（不再依赖磁盘上的原始文件）
    try:
        file_info = FileInfo.query.filter_by(filename=filename).first()
        if not file_info:
            flash('文件不存在', 'error')
            return redirect(url_for('main.file_management'))

        # 记录文件下载操作日志
        try:
            log_file_download(filename, 'file')
        except Exception as e:
            current_app.logger.warning(f"记录下载操作日志失败: {str(e)}")

        # 从数据库读取业务数据并生成 Excel 文件
        df = _read_file_data(file_info)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')
        output.seek(0)
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        current_app.logger.error(f"下载文件失败: {str(e)}", exc_info=True)
        flash('下载失败，请稍后再试', 'error')
        return redirect(url_for('main.file_management'))


# 文件清单明细下载（导出所有文件名为Excel）
@main_bp.route('/file_list_download', methods=['POST'])
def file_list_download():
    try:
        # 登录与权限：文件管理-下载
        if 'user_id' not in session:
            return jsonify({'error': '未登录'}), 401
        user = db.session.get(UserInfo, session['user_id'])
        if not user.is_admin:
            permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
            if not permission or not permission.can_file_management_export_file_list:
                return jsonify({'error': '权限不足'}), 403

        # 查询所有文件名（流式读取，降低大文件清单的内存峰值）
        # 先用只读查询判断是否有文件，避免空表时仍生成工作簿
        if not db.session.query(FileInfo.id).first():
            return jsonify({'error': '没有可导出的文件'}), 400

        # 解析文件名，提取车种、品番、EO、类型（从后往前拆分）
        def parse_filename(filename):
            # 默认值
            car_type = ''
            product_no = ''
            eo = ''
            file_type = ''
            
            # 示例：JA_PE2_MAIN_91066-G6870_d1_UsageList.xls
            # 先获取文件扩展名
            ext = '.' + filename.rsplit('.', 1)[1] if '.' in filename else ''
            name_without_ext = filename.rsplit('.', 1)[0] if '.' in filename else filename
            
            # 按下划线分割
            parts = name_without_ext.split('_')
            
            if len(parts) >= 4:
                # 从后往前拆分
                # 类型：最后一部分 + 扩展名
                file_type = parts[-1] + ext
                # EO：倒数第二部分
                eo = parts[-2]
                # 品番：倒数第三部分
                product_no = parts[-3]
                # 车种：剩余部分（可能包含下划线）
                car_type = '_'.join(parts[:-3])
            elif len(parts) >= 3:
                # 处理可能的不同格式
                file_type = parts[-1] + ext
                eo = parts[-2]
                product_no = parts[-3]
                car_type = '_'.join(parts[:-3]) if len(parts) > 3 else parts[0]
            
            return car_type, product_no, eo, file_type

        # 生成 Excel（流式写出，边查边写，避免一次性构建全部行）
        import io
        output = io.BytesIO()
        wb = Workbook(write_only=True)
        ws = wb.create_sheet('Files')
        headers = ['文件名', '车种', '品番', 'EO', '类型']
        ws.append(headers)
        # 按上传时间倒序流式读取，逐行解析文件名并写出
        export_q = FileInfo.query.order_by(FileInfo.upload_time.desc()).yield_per(500)
        for f in export_q:
            car_type, product_no, eo, file_type = parse_filename(f.filename)
            ws.append([f.filename, car_type, product_no, eo, file_type])
        wb.save(output)
        output.seek(0)

        # 设置下载响应头（使用单一Content-Disposition）
        from werkzeug.datastructures import Headers
        from datetime import datetime
        import urllib.parse
        headers = Headers()
        filename = f'file_list_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        headers.set('Content-Disposition', f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}")

        return Response(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            headers=headers
        )
    except Exception as e:
        current_app.logger.error(f'导出文件清单失败: {str(e)}', exc_info=True)
        return jsonify({'error': '导出失败，请重试'}), 500
# 删除文件路由
@main_bp.route('/delete_file/<int:file_id>', methods=['POST'])
def delete_file(file_id):
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    user_id = session['user_id']
    # AJAX 请求检测（放在前面，便于统一返回 JSON）
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    
    # 使用兼容的权限检查
    if not check_permission(user_id, 'can_file_management_delete'):
        if is_ajax:
            return jsonify({'success': False, 'message': '你没有删除文件的权限'}), 403
        flash('你没有删除文件的权限', 'error')
        return redirect(url_for('main.file_management'))
    file_info = db.session.get(FileInfo, file_id)
    if not file_info:
        if is_ajax:
            return jsonify({'success': True, 'message': '记录不存在，视为已删除'}), 200
        else:
            # 非 AJAX 请求也提供友好的错误提示，而不是直接404
            flash('文件记录不存在，可能已被删除', 'warning')
            return redirect(url_for('main.file_management'))

    # 冻结校验：冻结文件禁止删除（须先解冻），防止历史版本资料被误删
    frozen_names = assert_files_mutable([file_info])
    if frozen_names:
        msg = f'文件「{frozen_names[0]}」已冻结，禁止删除，请先解冻后再操作'
        if is_ajax:
            return jsonify({'success': False, 'message': msg,
                            'code': 'FROZEN_FILE', 'frozen_files': frozen_names}), 409
        flash(msg, 'error')
        return redirect(url_for('main.file_management'))

    file_path = resolve_upload_path(file_info)

    try:
        # 删除物理文件
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError as e:
                current_app.logger.warning(f"删除物理文件失败: {str(e)}")
        
        # 删除关联数据 - 针对SQLite优化
        # 用批量 DELETE 代替逐行 ORM 删除（原实现先全量加载再逐条 delete，行数多时极慢）
        if file_info.file_type == 'SubList':
            try:
                SubList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
            except Exception as e:
                current_app.logger.warning(f"删除SubList关联数据失败: {str(e)}")
        elif file_info.file_type == 'UsageList':
            try:
                UsageList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
            except Exception as e:
                current_app.logger.warning(f"删除UsageList关联数据失败: {str(e)}")

        # 记录文件删除操作日志（在删除记录之前）
        try:
            result = log_file_delete(file_info.filename, file_id, user_id)
        except Exception as e:
            current_app.logger.warning(f"记录删除操作日志失败: {str(e)}")
        
        # 删除文件记录
        db.session.delete(file_info)
        db.session.commit()
        # 文件删除后失效列表/计数缓存
        query_cache_manager.invalidate_list_cache()
        # 正常完成
        if is_ajax:
            return jsonify({'success': True, 'message': '文件及关联数据已成功删除'}), 200
        else:
            flash('文件及关联数据已成功删除', 'success')
            return redirect(url_for('main.file_management'))
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"删除文件失败: {str(e)}")
        if is_ajax:
            return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500
        else:
            flash(f'删除失败: {str(e)}', 'error')
            return redirect(url_for('main.file_management'))

    # 理论上不会到达这里；为了保险起见补一个兜底返回
    if is_ajax:
        return jsonify({'success': False, 'message': '未能完成删除操作'}), 500
    return redirect(url_for('main.file_management'))


# 导入查询缓存模块
from query_cache import query_cache_manager, optimize_query_for_sqlite, build_paginated_response

# 子列表搜索路由
@main_bp.route('/sub_list', methods=['GET', 'POST'])
@login_required
def sub_list():
    user_id = session['user_id']
    
    # 优化1：读取用户权限（缓存内含全部权限字段，避免与其它页面共用缓存时缺字段）
    cached_perm = get_cached_user_permission(user_id)
    if cached_perm is not None and not cached_perm.get('is_admin') and not cached_perm.get('can_sub_list'):
        flash('您没有访问SubList页面的权限', 'error')
        return forbidden_page('您没有访问SubList页面的权限')
    
    # 获取搜索参数
    search_params = {k: request.args.get(k, '').strip() for k in [
        'VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit', 'Wire', 'WireCode',
        'Color', 'SQ', 'Length_Cut', 'Length_Cut2', 'Tml1', 'Tml2',
        'Rel1', 'Rel2', 'Seal1', 'Seal2', 'Remark1'
    ]}

    if request.method == 'POST' and request.is_json:
        data = request.json or {}
        for k in search_params.keys():
            if k in data:
                search_params[k] = str(data[k]).strip()

    # 分页处理
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(5, min(per_page, 100))
    page = request.args.get('page', 1, type=int)
    page = max(1, page)
    
    # 生成缓存键
    cache_key_parts = ['sub_list']
    for k, v in sorted(search_params.items()):
        if v:
            cache_key_parts.append(f"{k}={v}")
    cache_key_parts.append(f"page={page}")
    cache_key_parts.append(f"per_page={per_page}")
    cache_key = ':'.join(cache_key_parts)
    
    # 按钮加载：只有「显式触发查询」才查库（点加载/搜索按钮、翻页、或 URL 带搜索条件）。
    # 直接打开页面时不再对百万行表做 COUNT + 分页查询，避免进入页面即产生全表扫描。
    # 注意：AJAX 请求一律视为显式加载——前端局部刷新必然是在要数据，
    # 这样即使客户端脚本尚未刷新（未携带 loaded 标记）也不会拿到空结果。
    _explicit_load = (
        request.method == 'POST'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.args.get('loaded') == '1'
        or 'page' in request.args
        or any(v for v in search_params.values() if v)
    )

    # 尝试从缓存获取（未显式加载时不做任何查询）
    cached_result = query_cache_manager.get_list_cache(cache_key) if _explicit_load else None
    if not _explicit_load:
        items_data = {
            'items': [],
            'loaded': False,
            'pagination': {'page': 1, 'per_page': per_page, 'total': 0, 'pages': 0,
                           'has_prev': False, 'has_next': False,
                           'prev_num': None, 'next_num': None},
        }
    elif cached_result is not None:
        current_app.logger.debug(f"SubList缓存命中: {cache_key}")
        items_data = cached_result
    else:
        # 优化2：使用 with_entities 只查询需要的列，避免 SELECT * 和 ORM 对象开销
        # 对于108万行表，SELECT * + ORM 对象创建开销巨大
        sub_list_columns = [
            SubList.id, SubList.VehicleType, SubList.SubPno, SubList.EoKsc,
            SubList.SetCode, SubList.Circuit, SubList.Wire, SubList.WireCode,
            SubList.Color, SubList.SQ, SubList.Length_Cut, SubList.Length_Cut2,
            SubList.Tml1, SubList.Tml2, SubList.Rel1, SubList.Rel2,
            SubList.Seal1, SubList.Seal2, SubList.Joint_Points, SubList.Remark1
        ]

        from sqlalchemy import func
        # 基础过滤（COUNT 与数据查询共用）
        base_filters = [SubList.deleted == False, SubList.is_frozen == False]
        searchable_fields = ['VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit',
                            'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut', 'Length_Cut2',
                            'Tml1', 'Tml2', 'Rel1', 'Rel2', 'Seal1', 'Seal2', 'Remark1']
        active_search = {f: v for f, v in search_params.items()
                         if v and f in searchable_fields}

        # P1-1: COUNT 结果缓存 - 命中缓存时不再重复统计（写操作会整体失效缓存）
        count_cache_key = f"count:sub_list:{':'.join(f'{k}={v}' for k, v in sorted(search_params.items()) if v)}"

        # 直接从元组构建字典，避免 ORM 对象开销
        column_names = ['id', 'VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit',
                       'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut', 'Length_Cut2',
                       'Tml1', 'Tml2', 'Rel1', 'Rel2', 'Seal1', 'Seal2',
                       'Joint_Points', 'Remark1']

        # 构造数据库侧过滤条件：单字段走「等值索引快路径 + 强制索引模糊兜底」，
        # 多字段走「逐字段等值探测 + 全模糊兜底」（详见辅助函数说明）
        if len(active_search) == 1:
            field, value = next(iter(active_search.items()))
            items_list, total, search_timeout = _single_field_page(
                SubList, 'sub_list', sub_list_columns, column_names,
                field, value, page, per_page, count_cache_key)
        else:
            filters = _build_multi_field_filters(SubList, 'sub_list', active_search, base_filters)
            # 先取数据页（LIMIT 尽早返回），再在时间预算内统计总数；
            # 大表冷缓存下 COUNT 可能需数十秒，超时则返回 None（前端按未知总数展示）
            page_stmt = (db.session.query(*sub_list_columns).filter(*filters)
                         .order_by(SubList.id)
                         .offset((page - 1) * per_page).limit(per_page).statement)
            items_list = _bounded_rows(page_stmt)
            search_timeout = items_list is None
            if search_timeout:
                items_list, total = [], None
            else:
                count_stmt = db.session.query(func.count(SubList.id)).filter(*filters).statement
                total = _cached_count(count_cache_key, count_stmt)

        has_next = (len(items_list) >= per_page) if total is None else (page < max(1, (total + per_page - 1) // per_page))
        total_pages = None if total is None else max(1, (total + per_page - 1) // per_page)

        items_data = {
            'items': [dict(zip(column_names, row)) for row in items_list],
            'search_timeout': search_timeout,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': total_pages,
                'has_prev': page > 1,
                'has_next': has_next,
                'prev_num': page - 1 if page > 1 else None,
                'next_num': page + 1 if has_next else None,
            }
        }

        # 缓存查询结果
        query_cache_manager.set_list_cache(cache_key, items_data, ttl=300)
        current_app.logger.debug(f"SubList缓存设置: {cache_key}")
    
    # 如果是AJAX请求，返回JSON
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(items_data)
    
    # 模板用: 仅包含非空值的搜索参数 + per_page, 避免分页链接 URL 过长
    template_search_params = {k: v for k, v in search_params.items() if v}
    template_search_params['per_page'] = per_page

    # 构建分页对象用于模板渲染
    class PaginationHelper:
        def __init__(self, data):
            items_list = data['items']
            # 如果 items 已经是字典列表,直接使用;否则从元组构建(兼容两种路径)
            if items_list and isinstance(items_list[0], tuple):
                column_names = ['id', 'VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit',
                               'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut', 'Length_Cut2',
                               'Tml1', 'Tml2', 'Rel1', 'Rel2', 'Seal1', 'Seal2',
                               'Joint_Points', 'Remark1']
                self.items = [type('Item', (), dict(zip(column_names, row)))() for row in items_list]
            elif items_list and isinstance(items_list[0], dict):
                self.items = [type('Item', (), d)() for d in items_list]
            else:
                self.items = items_list
            self.page = data['pagination']['page']
            self.per_page = data['pagination']['per_page']
            self.total = data['pagination']['total']
            self.pages = data['pagination']['pages']
            self.has_prev = data['pagination']['has_prev']
            self.has_next = data['pagination']['has_next']
            self.prev_num = data['pagination']['prev_num']
            self.next_num = data['pagination']['next_num']
        
        def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
            last_page = self.pages
            # pages 为 None 表示总数统计超时（未知），此时不渲染数字页码，只保留上一页/下一页
            if not last_page or last_page <= 0:
                return []
            num = self.page
            pages_set = set()
            for x in range(1, min(left_edge + 1, last_page + 1)):
                if x > 0:
                    pages_set.add(x)
            for x in range(max(1, num - left_current), min(last_page + 1, num + right_current + 1)):
                if x > 0:
                    pages_set.add(x)
            for x in range(max(1, last_page - right_edge + 1), last_page + 1):
                if x > 0:
                    pages_set.add(x)
            result = []
            for page_num in range(1, last_page + 1):
                if page_num in pages_set:
                    result.append(page_num)
                else:
                    if result and result[-1] is not None:
                        result.append(None)
            if result and result[-1] is None:
                result = result[:-1]
            return result
    
    items = PaginationHelper(items_data)
    
    return render_template(
        'sub_list.html',
        items=items,
        loaded=bool(items_data.get('loaded', True)),
        search_params=template_search_params,
        per_page=per_page
    )
# 用量列表搜索路由
@main_bp.route('/usage_list', methods=['GET', 'POST'])
@login_required
def usage_list():
    user_id = session['user_id']
    
    # 优化1：读取用户权限（缓存内含全部权限字段，避免与其它页面共用缓存时缺字段）
    cached_perm = get_cached_user_permission(user_id)
    if cached_perm is not None and not cached_perm.get('is_admin') and not cached_perm.get('can_usage_list'):
        flash('您没有访问UsageList页面的权限', 'error')
        return forbidden_page('您没有访问UsageList页面的权限')
    
    # 获取搜索参数
    search_params = {k: request.args.get(k, '').strip() for k in [
        'SubPno', 'EoKsc', 'VehicleType', 'Comp_Kind', 'Detail_Kind',
        'KscPno', 'Part_Name', 'Usage', 'Length'
    ]}

    if request.method == 'POST' and request.is_json:
        data = request.json or {}
        for k in search_params.keys():
            if k in data:
                search_params[k] = str(data[k]).strip()

    # 分页处理
    per_page = request.args.get('per_page', 10, type=int)
    per_page = max(5, min(per_page, 100))
    page = request.args.get('page', 1, type=int)
    page = max(1, page)
    
    # 生成缓存键
    cache_key_parts = ['usage_list']
    for k, v in sorted(search_params.items()):
        if v:
            cache_key_parts.append(f"{k}={v}")
    cache_key_parts.append(f"page={page}")
    cache_key_parts.append(f"per_page={per_page}")
    cache_key = ':'.join(cache_key_parts)
    
    # 按钮加载：只有「显式触发查询」才查库（点加载/搜索按钮、翻页、或 URL 带搜索条件）。
    # 直接打开页面时不再对五十万行表做 COUNT + 分页查询。
    # 注意：AJAX 请求一律视为显式加载，避免旧客户端脚本未带 loaded 标记时拿到空结果。
    _explicit_load = (
        request.method == 'POST'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.args.get('loaded') == '1'
        or 'page' in request.args
        or any(v for v in search_params.values() if v)
    )

    # 尝试从缓存获取（未显式加载时不做任何查询）
    cached_result = query_cache_manager.get_list_cache(cache_key) if _explicit_load else None
    if not _explicit_load:
        items_data = {
            'items': [],
            'loaded': False,
            'pagination': {'page': 1, 'per_page': per_page, 'total': 0, 'pages': 0,
                           'has_prev': False, 'has_next': False,
                           'prev_num': None, 'next_num': None},
        }
    elif cached_result is not None:
        current_app.logger.debug(f"UsageList缓存命中: {cache_key}")
        items_data = cached_result
    else:
        # 优化2：使用 with_entities 只查询需要的列，避免 SELECT * 和 ORM 对象开销
        usage_list_columns = [
            UsageList.id, UsageList.VehicleType, UsageList.SubPno, UsageList.EoKsc,
            UsageList.Comp_Kind, UsageList.Detail_Kind, UsageList.KscPno,
            UsageList.Part_Name, UsageList.Usage, UsageList.Length
        ]
        
        # 收集过滤条件（用于 COUNT 和数据查询）
        from sqlalchemy import func as sa_func
        base_filters = [UsageList.deleted == False, UsageList.is_frozen == False]
        searchable_fields = ['SubPno', 'EoKsc', 'VehicleType', 'Comp_Kind', 'Detail_Kind',
                            'KscPno', 'Part_Name', 'Usage', 'Length']
        active_search = {f: v for f, v in search_params.items()
                         if v and f in searchable_fields}

        # P1-1: COUNT 结果缓存 - 命中缓存时不再重复统计（写操作会整体失效缓存）
        count_cache_key = f"count:usage_list:{':'.join(f'{k}={v}' for k, v in sorted(search_params.items()) if v)}"

        # 直接从元组构建字典，避免 ORM 对象开销
        column_names = ['id', 'VehicleType', 'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind',
                       'KscPno', 'Part_Name', 'Usage', 'Length']

        # 构造数据库侧过滤条件：单字段走「等值索引快路径 + 强制索引模糊兜底」，
        # 多字段走「逐字段等值探测 + 全模糊兜底」（详见辅助函数说明）
        if len(active_search) == 1:
            field, value = next(iter(active_search.items()))
            items_list, total, search_timeout = _single_field_page(
                UsageList, 'usage_list', usage_list_columns, column_names,
                field, value, page, per_page, count_cache_key)
        else:
            # 搜索优化：不区分大小写的子串模糊匹配（与下载路由语义一致）
            # M-6：逐字段等值探测 + 全模糊兜底（结果与全模糊一致，但可命中索引）
            filters = _build_multi_field_filters(UsageList, 'usage_list', active_search, base_filters)
            # 先取数据页，再在时间预算内统计总数（超时返回 None，前端按未知总数展示）
            page_stmt = (db.session.query(*usage_list_columns).filter(*filters)
                         .order_by(UsageList.id)
                         .offset((page - 1) * per_page).limit(per_page).statement)
            items_list = _bounded_rows(page_stmt)
            search_timeout = items_list is None
            if search_timeout:
                items_list, total = [], None
            else:
                count_stmt = db.session.query(sa_func.count(UsageList.id)).filter(*filters).statement
                total = _cached_count(count_cache_key, count_stmt)

        has_next = (len(items_list) >= per_page) if total is None else (page < max(1, (total + per_page - 1) // per_page))
        total_pages = None if total is None else max(1, (total + per_page - 1) // per_page)

        items_data = {
            'items': [dict(zip(column_names, row)) for row in items_list],
            'search_timeout': search_timeout,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': total_pages,
                'has_prev': page > 1,
                'has_next': has_next,
                'prev_num': page - 1 if page > 1 else None,
                'next_num': page + 1 if has_next else None,
            }
        }
        
        # 缓存查询结果
        query_cache_manager.set_list_cache(cache_key, items_data, ttl=300)
        current_app.logger.debug(f"UsageList缓存设置: {cache_key}")
    
    # 如果是AJAX请求，返回JSON
    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return jsonify(items_data)
    
    # 模板用: 仅包含非空值的搜索参数 + per_page, 避免分页链接 URL 过长
    template_search_params = {k: v for k, v in search_params.items() if v}
    template_search_params['per_page'] = per_page

    # 构建分页对象用于模板渲染
    class PaginationHelper:
        def __init__(self, data):
            items_list = data['items']
            if items_list and isinstance(items_list[0], tuple):
                column_names = ['id', 'VehicleType', 'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind',
                               'KscPno', 'Part_Name', 'Usage', 'Length']
                self.items = [type('Item', (), dict(zip(column_names, row)))() for row in items_list]
            elif items_list and isinstance(items_list[0], dict):
                self.items = [type('Item', (), d)() for d in items_list]
            else:
                self.items = items_list
            self.page = data['pagination']['page']
            self.per_page = data['pagination']['per_page']
            self.total = data['pagination']['total']
            self.pages = data['pagination']['pages']
            self.has_prev = data['pagination']['has_prev']
            self.has_next = data['pagination']['has_next']
            self.prev_num = data['pagination']['prev_num']
            self.next_num = data['pagination']['next_num']
        
        def iter_pages(self, left_edge=2, left_current=2, right_current=5, right_edge=2):
            last_page = self.pages
            # pages 为 None 表示总数统计超时（未知），此时不渲染数字页码，只保留上一页/下一页
            if not last_page or last_page <= 0:
                return []
            num = self.page
            pages_set = set()
            for x in range(1, min(left_edge + 1, last_page + 1)):
                if x > 0:
                    pages_set.add(x)
            for x in range(max(1, num - left_current), min(last_page + 1, num + right_current + 1)):
                if x > 0:
                    pages_set.add(x)
            for x in range(max(1, last_page - right_edge + 1), last_page + 1):
                if x > 0:
                    pages_set.add(x)
            result = []
            for page_num in range(1, last_page + 1):
                if page_num in pages_set:
                    result.append(page_num)
                else:
                    if result and result[-1] is not None:
                        result.append(None)
            if result and result[-1] is None:
                result = result[:-1]
            return result
    
    items = PaginationHelper(items_data)
    
    return render_template(
        'usage_list.html',
        items=items,
        loaded=bool(items_data.get('loaded', True)),
        search_params=template_search_params,
        per_page=per_page
    )


# 辅助函数：构造不区分大小写的子串模糊匹配表达式（搜索用）
#
# 性能说明（重要）：
#   sub_list 约 198 万行（库文件 2.8GB，位于机械盘）、usage_list 约 127 万行。
#   『%x%』形式的子串 LIKE 无法命中索引，只能全表扫描，冷缓存下单次 COUNT/数据页
#   需读取 GB 级页面，实测 5~80 秒；这正是一次打开列表页就长时间等待的根因。
#
#   1) 等值快路径：用户输入正好是某个字段整串时，等值 NOCASE 比较可命中
#      『COLLATE NOCASE 部分索引』（毫秒级），因此先做等值探测；
#   2) 模糊兜底：仍保持原有的子串匹配语义，但用 INDEXED BY 强制扫描该字段的
#      NOCASE 部分索引（索引远小于表），避免读取整表页面；
#   3) 所有 COUNT 都带时间预算（_bounded_count），超时即中断并返回 None，
#      页面先展示当页数据、总数标注为未知，不再让用户为分页数等几十秒。
_PREFIX_UNUSABLE = ('%', '_')

# 字段 -> 可在其中/模糊搜索时强制使用的 NOCASE 部分索引（key 为数据库列名）
_NOCASE_ACTIVE_INDEXES = {
    'sub_list': {
        'SubPno': 'idx_sub_list_subpno_active',
        'EoKsc': 'idx_sub_list_eoksc_active',
        'VehicleType': 'idx_sub_list_vehicle_active',
        'Circuit': 'idx_sub_list_circuit_active',
        'SetCode': 'idx_sub_list_setcode_active',
        'Wire': 'idx_sub_list_wire_nc_active',
        'WireCode': 'idx_sub_list_wirecode_nc_active',
        'Tml1': 'idx_sub_list_tml1_nc_active',
        'Tml2': 'idx_sub_list_tml2_nc_active',
        'Length_Cut': 'idx_sub_list_length1_nc_active',
        'Length_Cut2': 'idx_sub_list_length2_nc_active',
        'Color': 'idx_sub_list_color_nc_active',
        'SQ': 'idx_sub_list_sq_nc_active',
        'Rel1': 'idx_sub_list_rel1_nc_active',
        'Rel2': 'idx_sub_list_rel2_nc_active',
        'Seal1': 'idx_sub_list_seal1_nc_active',
        'Seal2': 'idx_sub_list_seal2_nc_active',
        'Remark1': 'idx_sub_list_remark1_nc_active',
    },
    'usage_list': {
        'SubPno': 'idx_usage_list_subpno_active',
        'EoKsc': 'idx_usage_list_eoksc_active',
        'VehicleType': 'idx_usage_list_vehicle_active',
        'Comp_Kind': 'idx_usage_list_comp_kind_active',
        'Detail_Kind': 'idx_usage_list_detail_kind_nc_active',
        'KscPno': 'idx_usage_list_kscpno_active',
        'Part_Name': 'idx_usage_list_partname_nc_active',
        'Usage': 'idx_usage_list_usage_nc_active',
        'Length': 'idx_usage_list_length_nc_active',
    },
}

# COUNT 时间预算（毫秒）：超时即放弃统计，避免大表冷缓存下的长等待
_COUNT_BUDGET_MS = 800
# 数据页查询预算：整表扫描等极端条件至少给足冷缓存读盘时间（实测冷索引扫描 5~8s），
# 超出才中断并提示用户收窄条件，避免误判为「无结果」
_PAGE_BUDGET_MS = 8000
# 后台预热默认视图总数时使用较宽松的预算（不占用用户请求线程）
_COUNT_HEAVY_BUDGET_MS = 120000
# 计数缓存哨兵：表示该条件近期统计超时，短时间内不再重试
_COUNT_UNKNOWN = -1
_COUNT_TTL = 600
_COUNT_UNKNOWN_TTL = 120

_count_conn_lock = threading.Lock()
_count_conn = None


def _like_pattern(value):
    """把用户输入转义为 LIKE 子串模式（按字面值匹配，不解释 % 和 _）。"""
    escaped = str(value).replace('\\', '\\\\').replace('%', r'\%').replace('_', r'\_')
    return '%' + escaped + '%'


def _build_fuzzy_candidate_expr(column, value, table_name):
    """构造「等值」候选表达式，用于命中 COLLATE NOCASE 索引；不可用时返回 None。

    - 刻意不转义、不追加通配符，SQLAlchemy 对裸等值比较生成简单 SQL，
      SQLite 才能对其应用索引优化；
    - 仅当该字段存在 NOCASE 部分索引时才启用快路径：普通的 BINARY 索引无法服务于
      NOCASE 比较，强制等值探测只会多一次全表扫描。
    """
    if db.engine.dialect.name != 'sqlite':
        return None
    if not _NOCASE_ACTIVE_INDEXES.get(table_name, {}).get(column.name):
        return None
    text_value = str(value)
    if not text_value or text_value[0] in _PREFIX_UNUSABLE:
        return None
    if any(ch in text_value for ch in ('%', '_', '\\')):
        return None
    return column.collate('NOCASE') == text_value


def _fuzzy_search_expr(column, value):
    """转义 LIKE 通配符，按字面值做不区分大小写的子串模糊匹配。

    - SQLite: 使用 NOCASE 排序规则，避免 lower() 函数开销（实测快 2 倍）
    - 其他方言: 回退到 ilike
    """
    pattern = _like_pattern(value)
    if db.engine.dialect.name == 'sqlite':
        return column.collate('NOCASE').like(pattern, escape='\\')
    return column.ilike(pattern, escape='\\')


def _get_count_connection():
    """获取进程内复用的只读统计连接（懒加载；常驻可避免反复打开/关闭 WAL 文件）。"""
    global _count_conn
    if _count_conn is not None:
        return _count_conn
    db_path = db.engine.url.database
    if not db_path:
        return None
    if not os.path.isabs(db_path):
        db_path = os.path.join(current_app.root_path, db_path)
    if not os.path.exists(db_path):
        return None
    import sqlite3 as sqlite_driver
    con = sqlite_driver.connect(db_path, timeout=30, check_same_thread=False)
    try:
        # 库文件已被大量增删改切碎，索引页散落在 2.8GB 文件各处；把 mmap 覆盖到整个文件，
        # 使随机页访问走内存映射（避免每页一次 pread 系统调用，并享受系统顺序预读）
        mmap_bytes = min(os.path.getsize(db_path), 3 * 1024 ** 3)
        con.execute('PRAGMA cache_size=-32768')
        con.execute(f'PRAGMA mmap_size={max(mmap_bytes, 512 * 1024 ** 2)}')
        con.execute('PRAGMA busy_timeout=30000')
    except Exception:
        pass
    _count_conn = con
    return con


def _run_bounded(sql, binds, budget_ms, fetch_all):
    """在独立只读连接上执行 SQL，并施加硬性时间预算；超时返回 None。

    说明：百万行级大表 + 机械盘，任何全表/全索引扫描都可能耗时数秒到数十秒。
    统计与慢查询统一在这里卡时间上限，超时用 sqlite3 interrupt 中断，
    让请求线程能尽快返回（数据页仍正常展示，仅总数/慢查询降级）。
    """
    with _count_conn_lock:
        con = _get_count_connection()
        if con is None:
            return None
        timed_out = {'flag': False}

        def _abort():
            timed_out['flag'] = True
            try:
                con.interrupt()
            except Exception:
                pass

        timer = threading.Timer(max(0.05, budget_ms / 1000.0), _abort)
        timer.daemon = True
        timer.start()
        try:
            cursor = con.execute(sql) if binds is None else con.execute(sql, binds)
            return cursor.fetchall() if fetch_all else cursor.fetchone()
        except Exception as exc:
            if timed_out['flag'] or 'interrupt' in str(exc).lower():
                current_app.logger.info('查询超过 %.0fms 预算，已中断', budget_ms)
            else:
                current_app.logger.warning(f'查询执行失败: {exc}')
            return None
        finally:
            timer.cancel()


def _compile_statement(statement):
    """把 SQLAlchemy 语句编译成 (SQL 文本, 位置参数)；原生 SQL 字符串直接返回。"""
    if isinstance(statement, str):
        return statement, None
    compiled = statement.compile(db.engine, compile_kwargs={'render_postcompile': True})
    sql = str(compiled)
    positiontup = getattr(compiled, 'positiontup', None) or []
    binds = tuple(compiled.params[name] for name in positiontup) if positiontup else None
    return sql, binds


def _bounded_count(statement, params=None, budget_ms=_COUNT_BUDGET_MS):
    """带时间预算的 COUNT：超时返回 None（总数未知）。"""
    if db.engine.dialect.name != 'sqlite':
        return db.session.execute(statement, params or {}).scalar()
    sql, binds = _compile_statement(statement)
    if isinstance(statement, str) and params is not None:
        binds = params
    row = _run_bounded(sql, binds, budget_ms, fetch_all=False)
    return int(row[0]) if row else None


def _bounded_rows(statement, params=None, budget_ms=_PAGE_BUDGET_MS):
    """带时间预算的数据页查询；超时返回 None（调用方按「查询超时」降级提示）。"""
    if db.engine.dialect.name != 'sqlite':
        return db.session.execute(statement, params or {}).fetchall()
    sql, binds = _compile_statement(statement)
    if isinstance(statement, str) and params is not None:
        binds = params
    return _run_bounded(sql, binds, budget_ms, fetch_all=True)


def _cached_count(cache_key, statement, params=None, budget_ms=_COUNT_BUDGET_MS):
    """计数缓存 + 时间预算统计；返回 None 表示总数未知（前端降级展示）。"""
    cached = query_cache_manager.get_count(cache_key)
    if cached is not None:
        return None if cached == _COUNT_UNKNOWN else cached
    total = _bounded_count(statement, params, budget_ms)
    if total is None:
        query_cache_manager.set_count(cache_key, _COUNT_UNKNOWN, ttl=_COUNT_UNKNOWN_TTL)
    else:
        query_cache_manager.set_count(cache_key, total, ttl=_COUNT_TTL)
    return total


def _indexed_fuzzy_page(table_name, column_names, field, value, page, per_page):
    """用 INDEXED BY 强制扫描字段的 NOCASE 部分索引取一页数据；超时返回 None。

    - 按被搜索字段排序（与索引顺序一致），保证翻页结果稳定，且无需排序临时表；
    - 命中行极少，回表取整行的代价可忽略；无匹配时需扫完整个索引，
      因此同样受 _PAGE_BUDGET_MS 预算保护。
    """
    index_name = _NOCASE_ACTIVE_INDEXES.get(table_name, {}).get(field)
    if not index_name:
        return None
    sql = (
        f"SELECT {', '.join(column_names)} FROM {table_name} INDEXED BY {index_name} "
        f"WHERE deleted = 0 AND is_frozen = 0 "
        f"AND {field} COLLATE NOCASE LIKE :pattern "
        f"ORDER BY {field} COLLATE NOCASE, id LIMIT :limit OFFSET :offset"
    )
    return _bounded_rows(sql, {
        'pattern': _like_pattern(value),
        'limit': per_page,
        'offset': (page - 1) * per_page,
    })


def _fuzzy_count(table_name, model, field, value, count_cache_key):
    """模糊匹配的计数：命中索引时也用 INDEXED BY，否则退回原全表 COUNT（带预算）。"""
    index_name = _NOCASE_ACTIVE_INDEXES.get(table_name, {}).get(field)
    if index_name:
        sql = (
            f"SELECT COUNT(*) FROM {table_name} INDEXED BY {index_name} "
            f"WHERE deleted = 0 AND is_frozen = 0 "
            f"AND {field} COLLATE NOCASE LIKE :pattern"
        )
        return _cached_count(count_cache_key, sql, {'pattern': _like_pattern(value)})
    count_stmt = (db.session.query(func.count(model.id))
                  .filter(model.deleted == False, model.is_frozen == False,
                          _fuzzy_search_expr(getattr(model, field), value)).statement)
    return _cached_count(count_cache_key, count_stmt)


def _single_field_page(model, table_name, columns, column_names,
                       field, value, page, per_page, count_cache_key):
    """单字段搜索分页：等值索引快路径 → 强制索引模糊兜底。

    返回 (items, total, timed_out)；total 为 None 表示总数未知，
    timed_out=True 表示数据页查询超过预算被中断（前端给出收窄条件提示）。
    """
    column = getattr(model, field)
    base_filters = [model.deleted == False, model.is_frozen == False]
    offset = (page - 1) * per_page

    candidate_expr = _build_fuzzy_candidate_expr(column, value, table_name)
    if candidate_expr is not None:
        eq_query = db.session.query(*columns).filter(*base_filters, candidate_expr)
        eq_items = eq_query.offset(offset).limit(per_page).all()
        if eq_items:
            return eq_items, _cached_count(
                count_cache_key, eq_query.with_entities(func.count()).statement), False
        if page > 1:
            # 页码越界时无法区分「等值无结果」和「翻过了最后一页」，用等值统计确认
            eq_total = _cached_count(
                count_cache_key, eq_query.with_entities(func.count()).statement)
            if eq_total:
                return [], eq_total, False

    if _NOCASE_ACTIVE_INDEXES.get(table_name, {}).get(field):
        fuzzy_items = _indexed_fuzzy_page(table_name, column_names, field, value, page, per_page)
    else:
        fuzzy_query = (db.session.query(*columns)
                       .filter(*base_filters, _fuzzy_search_expr(column, value))
                       .order_by(model.id)
                       .offset(offset).limit(per_page))
        fuzzy_items = _bounded_rows(fuzzy_query.statement)

    if fuzzy_items is None:
        # 数据页查询超时：不再做计数，直接按「查询超时」降级返回
        return [], None, True
    return fuzzy_items, _fuzzy_count(table_name, model, field, value, count_cache_key), False


def _build_multi_field_filters(model, table_name, active_search, base_filters):
    """多字段搜索的过滤条件构造（M-6：逐字段等值探测 + 全模糊兜底）。

    语义说明：多字段之间是 AND（同时满足）。对每个字段独立做「等值探测」：
    - 若某字段的等值命中某行（即用户输入正好是该字段整串），则该行必然也满足全模糊，
      因此该字段可用等值表达式（命中 NOCASE 索引）替代全模糊；
    - 若某字段等值在当前全表无任何命中，说明无行满足"该字段整串等于输入"，
      但可能有行满足子串匹配，于是该字段退回全模糊 LIKE。
    由于「等值」是「全模糊」的子集，且逐字段取并集/回退不改变每个字段的判定结果，
    最终各字段 AND 组合出的结果与优化前**逐行完全一致**。

    仅对存在 NOCASE 部分索引的字段做探测：其他字段的等值比较无法命中索引，
    探测本身会变成一次全表扫描，反而更慢。

    base_filters 作为探测命中判定的基准（deleted/is_frozen 等），保证探测与最终查询同口径。
    """
    filters = list(base_filters)
    for field, value in active_search.items():
        column = getattr(model, field)
        candidate_expr = _build_fuzzy_candidate_expr(column, value, table_name)
        if candidate_expr is not None:
            # 等值探测：命中任意一行即可确定使用等值表达式（走索引）
            if db.session.query(column).filter(*base_filters, candidate_expr).first() is not None:
                filters.append(candidate_expr)
                continue
        # 等值整表未命中：子串可能出现在词中，退回全模糊
        filters.append(_fuzzy_search_expr(column, value))
    return filters


def warm_list_total_counts(app, budget_ms=_COUNT_HEAVY_BUDGET_MS, ttl=_COUNT_TTL):
    """后台预热 sub_list / usage_list 默认视图（无筛选）的活跃行数。

    大表 COUNT 冷缓存下要读取 GB 级页面，放在启动后的空闲时段完成，
    避免用户首次打开列表页时等待；只写计数缓存，不影响业务数据。
    """
    try:
        with app.app_context():
            from sqlalchemy import func as sa_func
            for cache_key, model in (('count:sub_list:', SubList),
                                     ('count:usage_list:', UsageList)):
                cached = query_cache_manager.get_count(cache_key)
                if cached is not None and cached != _COUNT_UNKNOWN:
                    continue  # 已有有效结果（未知哨兵不算，仍需重新统计）
                stmt = (db.session.query(sa_func.count(model.id))
                        .filter(model.deleted == False, model.is_frozen == False)).statement
                total = _bounded_count(stmt, budget_ms=budget_ms)
                if total is not None:
                    query_cache_manager.set_count(cache_key, total, ttl=ttl)
                    app.logger.info(f"列表总数预热完成 {cache_key}={total}")
                db.session.remove()
    except Exception as exc:
        app.logger.warning(f"列表总数预热失败（非致命）: {exc}")


def warm_search_indexes(app, budget_ms=_COUNT_HEAVY_BUDGET_MS):
    """后台预读搜索用的 NOCASE 部分索引。

    原因：库文件 2.8GB 位于机械盘（顺序读 77MB/s、随机 6.3ms/次），且索引页经过
    多次批量导入/删除后已碎片化（实测单个 25MB 索引的叶子页散落在 2.4GB 范围内、
    跳变 4500+ 次），冷文件缓存下首次按字段筛选需数秒；预读一次后同一批页面
    会留在系统文件缓存，后续搜索仅需 100~300ms。

    常用字段（品番/EO/车种）优先预热，让用户最先用到的筛选最早变快；
    整体在后台串行执行，不占用请求线程。
    """
    priority_fields = ('SubPno', 'EoKsc', 'VehicleType')
    tasks = []
    for table_name, index_map in _NOCASE_ACTIVE_INDEXES.items():
        for field, index_name in index_map.items():
            # 常用字段排前面（0），其余字段随后（1）
            tasks.append((0 if field in priority_fields else 1, table_name, index_name))
    try:
        with app.app_context():
            for _rank, table_name, index_name in sorted(tasks):
                sql = (f"SELECT COUNT(*) FROM {table_name} INDEXED BY {index_name} "
                       f"WHERE deleted = 0 AND is_frozen = 0")
                # 单索引预算宽松即可；失败（超时/异常）不影响业务
                _bounded_count(sql, budget_ms=budget_ms)
            app.logger.info('搜索索引预热完成')
    except Exception as exc:
        app.logger.warning(f"搜索索引预热失败（非致命）: {exc}")


# 辅助函数：检测文件类型
def detect_file_type(filename):
    if 'sublist' in filename.lower():
        return 'SubList'
    elif 'usagelist' in filename.lower():
        return 'UsageList'
    return None


# 上传文件根目录（所有分类子目录均位于其下）
UPLOAD_ROOT_PARTS = ('static', 'uploads')


def get_upload_root():
    """返回 static/uploads 的绝对路径"""
    return os.path.join(current_app.root_path, *UPLOAD_ROOT_PARTS)


def get_upload_category(filename, file_type=None):
    """按文件类型确定存储子目录名（SubList / UsageList），无法识别时返回 None（存根目录）"""
    return file_type or detect_file_type(filename)


def get_upload_dir(category=None):
    """返回上传目录绝对路径，并按需创建子目录"""
    root = get_upload_root()
    if category:
        path = os.path.join(root, category)
    else:
        path = root
    os.makedirs(path, exist_ok=True)
    return path


def resolve_upload_path(file_info):
    """解析 FileInfo 对应的物理文件路径。

    优先使用记录中的 category 子目录，其次按文件名推断子目录；
    为兼容历史平铺文件，若子目录下不存在则回退到 uploads 根目录。
    """
    root = get_upload_root()
    filename = file_info.filename
    candidates = []
    category = getattr(file_info, 'category', None) or get_upload_category(filename, getattr(file_info, 'file_type', None))
    if category:
        candidates.append(os.path.join(root, category, filename))
    candidates.append(os.path.join(root, filename))
    for path in candidates:
        if os.path.exists(path):
            return path
    return candidates[0]


# 辅助函数：处理文件数据
def parse_file_data(file_path, file_type):
    """解析 Excel 文件为 records 列表（纯文件读取，无 DB 操作，线程安全，可并行调用）。

    返回 (records, headers)；文件为空或字段缺失时抛 ValueError。
    """
    ext = os.path.splitext(file_path)[1].lower()
    records = None
    headers = None
    if ext == '.xlsx':
        # 轻量路径：openpyxl read_only 直接读取，避免 pandas DataFrame 构建开销（快约 3 倍）
        from openpyxl import load_workbook
        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            ws = wb.active
            records = []
            headers = None
            for row in ws.iter_rows(values_only=True):
                if headers is None:
                    headers = [str(h).strip() if h is not None else '' for h in row]
                    continue
                row_obj = {}
                for i, v in enumerate(row):
                    row_obj[headers[i]] = v if v is not None else None
                records.append(row_obj)
        finally:
            wb.close()
        if not records:
            raise ValueError('空工作表')
        if file_type == 'SubList':
            validate_sub_list_headers(headers)
        else:
            validate_usage_list_headers(headers)
    elif ext == '.xls':
        # 轻量路径：优先使用 xlrd 直接读取，避免 DataFrame 构建开销
        try:
            import xlrd
            book = xlrd.open_workbook(file_path)
            sheet = book.sheet_by_index(0)
            if sheet.nrows == 0:
                raise ValueError('空工作表')
            headers = [str(sheet.cell_value(0, c)).strip() for c in range(sheet.ncols)]
            records = []
            for r in range(1, sheet.nrows):
                row_obj = {}
                for c in range(sheet.ncols):
                    key = headers[c]
                    value = sheet.cell_value(r, c)
                    # xlrd 返回的数值类型需要按需转字符串或保持数值；此处保持原样
                    row_obj[key] = value if value != '' else None
                records.append(row_obj)
        except Exception:
            # 回退1：pandas+xlrd 引擎
            try:
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings('ignore', message='.*OLE2 inconsistency.*')
                    warnings.filterwarnings('ignore', message='.*file size.*not 512.*')
                    df = pd.read_excel(file_path, engine='xlrd')
                    df = df.replace({np.nan: None})
                    headers = list(df.columns)
                    records = df.to_dict(orient='records')
            except Exception:
                # 回退2：pyexcel 直接读取 .xls
                try:
                    import pyexcel as p
                    rec_iter = p.iget_records(file_name=file_path)
                    rows = list(rec_iter)
                    if not rows:
                        raise ValueError('pyexcel 读取为空')
                    from itertools import chain
                    all_keys = set(chain.from_iterable([r.keys() for r in rows]))
                    records = []
                    for r in rows:
                        records.append({k: r.get(k) for k in all_keys})
                    headers = list(all_keys)
                except Exception as e3:
                    raise ValueError('读取 .xls 失败（xlrd 与 pyexcel 均失败）。请手动另存为 .xlsx 后再上传') from e3
        if file_type == 'SubList':
            validate_sub_list_headers(headers or list((records[0].keys() if records else [])))
        else:
            validate_usage_list_headers(headers or list((records[0].keys() if records else [])))
    else:
        raise ValueError('不支持的文件格式')
    return records, headers


def write_file_data(file_type, records, base_name):
    """将解析好的 records 写入数据库（先删后插，不 commit）。

    事务边界与提交时机由调用方控制；本函数仅执行 DELETE + 批量 INSERT。
    """
    from sqlalchemy import insert as sa_insert
    from sqlalchemy import text as sa_text
    is_mysql = str(current_app.config.get('SQLALCHEMY_DATABASE_URI', '')).lower().startswith('mysql')

    # 冻结防护（纵深防御）：同名文件已冻结时禁止先删后插覆盖，须先解冻
    from models.file_info import FileInfo
    frozen_hit = FileInfo.query.filter_by(filename=base_name, is_frozen=True).first()
    if frozen_hit:
        raise PermissionError(f'文件「{base_name}」已冻结，禁止覆盖导入，请先解冻后再操作')

    try:
        # MySQL 会话级优化：临时关闭校验，加速批量导入
        if is_mysql:
            db.session.execute(sa_text('SET FOREIGN_KEY_CHECKS=0'))
            db.session.execute(sa_text('SET UNIQUE_CHECKS=0'))

        if file_type == 'SubList':
            # 幂等保障：同一来源文件数据先删除再插入，避免重复累积
            db.session.execute(sa_text('DELETE FROM sub_list WHERE source_file = :src'), {'src': base_name})
            # 使用统一的车种提取函数
            vehicle_type = extract_vehicle_type_from_filename(base_name)
            for rec in records:
                if not rec.get('VehicleType'):
                    rec['VehicleType'] = vehicle_type
                rec['source_file'] = base_name
            CHUNK = 10000
            if is_mysql:
                # 使用 MySQL 的 INSERT IGNORE 以避免重复行造成的回滚开销
                from sqlalchemy.dialects.mysql import insert as mysql_insert
                stmt = mysql_insert(SubList).prefix_with('IGNORE')
            else:
                stmt = sa_insert(SubList)
            for i in range(0, len(records), CHUNK):
                db.session.execute(stmt, records[i:i+CHUNK])
        elif file_type == 'UsageList':
            # 幂等保障：同一来源文件数据先删除再插入，避免重复累积
            db.session.execute(sa_text('DELETE FROM usage_list WHERE source_file = :src'), {'src': base_name})
            # 使用统一的车种提取函数
            vehicle_type = extract_vehicle_type_from_filename(base_name)
            for rec in records:
                if not rec.get('VehicleType'):
                    rec['VehicleType'] = vehicle_type
                rec['source_file'] = base_name
            CHUNK = 10000
            if is_mysql:
                from sqlalchemy.dialects.mysql import insert as mysql_insert
                stmt = mysql_insert(UsageList).prefix_with('IGNORE')
            else:
                stmt = sa_insert(UsageList)
            for i in range(0, len(records), CHUNK):
                db.session.execute(stmt, records[i:i+CHUNK])
        else:
            raise ValueError(f'未知文件类型: {file_type}')
    finally:
        # 恢复 MySQL 会话级优化开关（无论是否提交都需恢复，避免会话残留关闭状态）
        try:
            if is_mysql:
                db.session.execute(sa_text('SET UNIQUE_CHECKS=1'))
                db.session.execute(sa_text('SET FOREIGN_KEY_CHECKS=1'))
        except Exception:
            pass


def process_file(file_path, file_type, do_commit=True):
    """解析并写入单个文件（兼容旧调用方；file_management 已改用分离的 parse/write 流程）。"""
    try:
        records, _ = parse_file_data(file_path, file_type)
        write_file_data(file_type, records, os.path.basename(file_path))
        # 提取首行 SubPno/EoKsc 供调用方使用（避免调用方再次读取文件）
        first_subpno = None
        first_eo = None
        try:
            if records:
                first = records[0]
                first_subpno = str(first.get('SubPno') or '').strip() or None
                first_eo = str(first.get('EoKsc') or '').strip() or None
        except Exception:
            pass
        if do_commit:
            db.session.commit()
        return True, None, first_subpno, first_eo
    except Exception as e:
        db.session.rollback()
        message = str(e)
        current_app.logger.error(f'文件处理失败: {message}')
        return False, message, None, None


# 辅助函数：验证SubList字段
def validate_sub_list_columns(df):
    required = {
        'SubPno', 'EoKsc', 'GroupNo', 'GroupOrder', 'SetCode', 'CutCode',
        'Circuit', 'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut'
    }
    if not required.issubset(set(df.columns)):
        raise ValueError(f'缺少必要字段: {required - set(df.columns)}')


def validate_sub_list_headers(headers):
    required = {
        'SubPno', 'EoKsc', 'GroupNo', 'GroupOrder', 'SetCode', 'CutCode',
        'Circuit', 'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut'
    }
    if not required.issubset(set(headers)):
        raise ValueError(f'缺少必要字段: {required - set(headers)}')


# 辅助函数：验证UsageList字段
def validate_usage_list_columns(df):
    required = {'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name'}
    if not required.issubset(set(df.columns)):
        raise ValueError(f'缺少必要字段: {required - set(df.columns)}')


def validate_usage_list_headers(headers):
    required = {'SubPno', 'EoKsc', 'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name'}
    if not required.issubset(set(headers)):
        raise ValueError(f'缺少必要字段: {required - set(headers)}')


# 辅助函数：处理日期搜索
def apply_date_filter(query, search_date):
    if not search_date:
        return query

    date_formats = [
        ('%Y-%m-%d', r'^\d{4}-\d{2}-\d{2}$'),
        ('%Y-%m', r'^\d{4}-\d{2}$'),
        ('%Y', r'^\d{4}$')
    ]

    start = end = None
    for fmt, pattern in date_formats:
        if re.match(pattern, search_date):
            try:
                dt = datetime.strptime(search_date, fmt)
                if fmt == '%Y-%m-%d':
                    start, end = dt, dt + relativedelta(days=1) - relativedelta(seconds=1)
                elif fmt == '%Y-%m':
                    start, end = dt, dt + relativedelta(months=1) - relativedelta(seconds=1)
                else:
                    start, end = dt.replace(month=1, day=1), dt.replace(month=12, day=31)
                break
            except ValueError:
                continue

    if start and end:
        query = query.filter(FileInfo.upload_time >= start, FileInfo.upload_time <= end)
    return query


# 辅助函数：文件类型检查
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'xls', 'xlsx'}


@main_bp.route('/sublist_download', methods=['POST'])
@csrf_exempt_route
def sublist_download():
    try:
        current_app.logger.info('开始处理SubList下载请求')
        # 权限：SubList 下载
        if 'user_id' not in session:
            current_app.logger.warning('用户未登录')
            return jsonify({'error': '未登录'}), 401
        user = db.session.get(UserInfo, session['user_id'])
        if not user.is_admin:
            permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
            if not permission or not permission.can_sub_list_download:
                return jsonify({'error': '权限不足'}), 403
        data = request.get_json()
        current_app.logger.debug(f'收到下载请求数据: {data}')
        search_params = data.get('search_params', {})
        format_type = data.get('format', 'csv').lower()
        visible_columns = data.get('visible_columns', [])
        show_differences = data.get('show_differences', False)
        current_app.logger.debug(f'解析参数: format={format_type}, visible_columns={len(visible_columns)}, show_differences={show_differences}')

        query = SubList.query.filter(SubList.deleted == False, SubList.is_frozen == False)
        current_app.logger.info('开始构建数据库查询')

        # 与 sub_list 列表路由保持完全同口径：字段走白名单、值统一 strip 后再过滤。
        # 修复1：未 trim 的输入（如 'PAD '）会让 LIKE 命中 0 行 → 提示「没有数据可导出」。
        # 修复2：原先未做 hasattr 校验，前端若传来非法键（如 name="是否是花线"）会抛 AttributeError → 500。
        sub_searchable_fields = ['VehicleType', 'SubPno', 'EoKsc', 'SetCode', 'Circuit',
                                 'Wire', 'WireCode', 'Color', 'SQ', 'Length_Cut', 'Length_Cut2',
                                 'Tml1', 'Tml2', 'Rel1', 'Rel2', 'Seal1', 'Seal2', 'Remark1']
        for field in sub_searchable_fields:
            value = str(search_params.get(field) or '').strip()
            if value:
                # 与列表页搜索语义一致：字面子串模糊匹配（转义 LIKE 通配符）
                query = query.filter(_fuzzy_search_expr(getattr(SubList, field), value))

        current_app.logger.info('开始执行数据库查询')
        try:
            # P2-1: 使用 with_entities(func.count()) 避免 query.count() 的子查询开销
            from sqlalchemy import func as dl_func
            total_count = query.with_entities(dl_func.count(SubList.id)).scalar()
            current_app.logger.info(f'数据库中共有 {total_count} 条记录')
            
            if total_count == 0:
                current_app.logger.warning('没有数据可导出')
                return jsonify({'error': '没有数据可导出'}), 400
            
            # 如果数据量过大，使用流式处理
            if total_count > 50000:
                current_app.logger.info(f'数据量较大({total_count}条)，使用流式导出处理')
                
            # 对于超大数据量，建议使用CSV格式
            if total_count > 200000 and format_type == 'excel':
                current_app.logger.warning(f'数据量很大({total_count}条)，建议使用CSV格式以获得更好的性能')
                
        except Exception as e:
            current_app.logger.error(f'数据库查询失败: {str(e)}', exc_info=True)
            return jsonify({'error': '数据库查询失败，请重试'}), 500

        # 性能优化（M-1）：以主键 id 递增的 keyset 分页替代 OFFSET 分页。
        # OFFSET 在百万级数据下越翻越慢（需跳过 offset 行），keyset 每批恒定开销。
        base_query = query.order_by(SubList.id)
        first_row = base_query.with_entities(SubList.id).first()
        last_id = first_row[0] if first_row else None

        def iter_id_batches(batch_size):
            """按 id 游标分批返回 [id, ...]，避免大 offset 的 O(n) 跳过开销。"""
            nonlocal last_id
            cursor = last_id
            while True:
                rows = (query.order_by(SubList.id)
                        .filter(SubList.id > cursor)
                        .limit(batch_size)
                        .with_entities(SubList.id).all())
                if not rows:
                    break
                cursor = rows[-1][0]
                yield [r[0] for r in rows]

        # 定义流式数据生成器
        def stream_data_generator():
            batch_size = 10000  # 每批处理1万条记录
            excluded_fields = ['source_file', 'deleted', 'upload_time']
            processed_count = 0
            
            current_app.logger.info('开始流式处理数据')
            
            for id_batch in iter_id_batches(batch_size):
                # 分批查询数据（M-1：keyset 游标，避免大 offset 跳过开销）
                batch_items = SubList.query.filter(SubList.id.in_(id_batch)).order_by(SubList.id).all()
                if not batch_items:
                    break
                
                # 转换当前批次数据
                batch_data = []
                for item in batch_items:
                    item_dict = item.to_dict()
                    # 过滤掉不需要的字段
                    filtered_dict = {k: v for k, v in item_dict.items() if k not in excluded_fields}
                    batch_data.append(filtered_dict)
                
                processed_count += len(batch_data)
                current_app.logger.info(f'已处理 {processed_count}/{total_count} 条记录')
                
                yield batch_data
        
        # 先获取第一批数据来获取列名
        first_batch_items = query.limit(1).all()
        if not first_batch_items:
            current_app.logger.warning('没有数据可导出')
            return jsonify({'error': '没有数据可导出'}), 400
        
        # 获取所有列名
        sample_item = first_batch_items[0].to_dict()
        excluded_fields = ['source_file', 'deleted', 'upload_time']
        all_columns = [k for k, v in sample_item.items() if k not in excluded_fields]
        
        # 筛选可见列
        if visible_columns:
            existing_columns = [col for col in visible_columns if col in all_columns]
            current_app.logger.info(f'可见列: {visible_columns}, 存在的列: {existing_columns}')
            if existing_columns:
                all_columns = existing_columns
                current_app.logger.info(f'筛选后列数: {len(all_columns)}')
        
        # 确保车种列（VehicleType）始终被包含
        if 'VehicleType' not in all_columns:
            # 尝试插入到 SubPno 之前，如果 SubPno 存在的话
            try:
                if 'SubPno' in all_columns:
                    idx = all_columns.index('SubPno')
                    all_columns.insert(idx, 'VehicleType')
                elif 'id' in all_columns:
                    idx = all_columns.index('id')
                    all_columns.insert(idx + 1, 'VehicleType')
                else:
                    all_columns.insert(0, 'VehicleType')
                current_app.logger.info('已强制添加车种列(VehicleType)')
            except Exception as e:
                current_app.logger.warning(f'添加车种列失败: {str(e)}')
        
        # 创建完整的数据流生成器
        def stream_data_generator_complete():
            batch_size = 10000
            excluded_fields = ['source_file', 'deleted', 'upload_time']
            
            for id_batch in iter_id_batches(batch_size):
                batch_items = SubList.query.filter(SubList.id.in_(id_batch)).order_by(SubList.id).all()
                if not batch_items:
                    break
                
                batch_data = []
                for item in batch_items:
                    item_dict = item.to_dict()
                    filtered_dict = {k: v for k, v in item_dict.items() if k not in excluded_fields}
                    batch_data.append(filtered_dict)
                
                yield batch_data

        if format_type == 'excel':
            current_app.logger.info('开始生成Excel文件（优化版本）')
            filename = f'sublist_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            
            # 对于大数据量，使用优化的Excel生成
            if total_count > 50000:
                current_app.logger.info(f'数据量较大({total_count}行)，使用优化Excel生成')
            
            try:
                # 尝试使用xlsxwriter（更快）
                try:
                    import xlsxwriter
                    current_app.logger.info('使用xlsxwriter引擎生成Excel')
                    
                    output = io.BytesIO()
                    workbook = xlsxwriter.Workbook(output, {'constant_memory': True, 'default_date_format': 'yyyy-mm-dd'})
                    worksheet = workbook.add_worksheet('SubList')
                    
                    # 写入表头
                    for col_idx, col_name in enumerate(all_columns):
                        worksheet.write(0, col_idx, col_name)
                    
                    # 分批写入数据
                    row_count = 1
                    batch_size = 10000  # 优化批次大小以提高性能
                    
                    for id_batch in iter_id_batches(batch_size):
                        batch_items = SubList.query.filter(SubList.id.in_(id_batch)).order_by(SubList.id).all()
                        if not batch_items:
                            break
                        
                        # 批量写入数据
                        for item in batch_items:
                            item_dict = item.to_dict()
                            filtered_dict = {k: v for k, v in item_dict.items() if k not in ['source_file', 'deleted', 'upload_time']}
                            
                            for col_idx, col in enumerate(all_columns):
                                value = filtered_dict.get(col, '')
                                worksheet.write(row_count, col_idx, value)
                            
                            row_count += 1
                        
                        # 每处理100000行记录一次进度，减少日志频率
                        if row_count % 100000 == 1:
                            current_app.logger.info(f'Excel已写入 {row_count-1}/{total_count} 行')
                    
                    workbook.close()
                    output.seek(0)
                    current_app.logger.info(f'xlsxwriter Excel生成完成，共 {row_count-1} 行')
                    
                except ImportError:
                    current_app.logger.warning('xlsxwriter不可用，回退到openpyxl')
                    raise ImportError("xlsxwriter not available")
                except Exception as e:
                    current_app.logger.error(f'xlsxwriter处理失败: {str(e)}，回退到openpyxl')
                    raise ImportError("xlsxwriter processing failed")
                
            except ImportError:
                # 回退到优化的openpyxl
                current_app.logger.info('使用优化的openpyxl引擎生成Excel')
                
                from openpyxl import Workbook
                from openpyxl.styles import PatternFill
                
                wb = Workbook()
                ws = wb.active
                ws.title = 'SubList'
                
                # 写入表头
                ws.append(all_columns)
                
                # 如果显示差异，准备颜色映射（简化版本）
                column_color_maps = {}
                if show_differences:
                    colors = ['FFDDDD', 'DDFFDD', 'DDDDFF', 'FFFFDD', 'FFDDFF', 'DDFFFF']
                    # 只对前几列应用颜色，减少处理时间
                    limited_columns = all_columns[:5]  # 只处理前5列
                    
                    for col in limited_columns:
                        # 简化颜色映射，只收集前1000个唯一值
                        unique_values = set()
                        # M-1：沿用 keyset 游标采样，避免 OFFSET 跳行开销
                        for _i, id_batch in enumerate(iter_id_batches(1000)):
                            batch_items = SubList.query.filter(SubList.id.in_(id_batch)).order_by(SubList.id).all()
                            if not batch_items:
                                break
                            for item in batch_items:
                                item_dict = item.to_dict()
                                value = item_dict.get(col)
                                if value is not None:
                                    unique_values.add(str(value))
                                if len(unique_values) >= 100:  # 限制唯一值数量
                                    break
                            if len(unique_values) >= 100:
                                break
                        
                        if len(unique_values) > 1:
                            value_color_map = {}
                            for i, value in enumerate(list(unique_values)[:6]):  # 最多6种颜色
                                value_color_map[value] = colors[i % len(colors)]
                            column_color_maps[col] = value_color_map
                
                # 分批写入数据（优化内存使用）
                row_count = 1
                batch_size = 10000  # 优化批次大小
                
                for id_batch in iter_id_batches(batch_size):
                    batch_items = SubList.query.filter(SubList.id.in_(id_batch)).order_by(SubList.id).all()
                    if not batch_items:
                        break
                    
                    # 逐行写入，减少内存占用
                    for item in batch_items:
                        item_dict = item.to_dict()
                        filtered_dict = {k: v for k, v in item_dict.items() if k not in ['source_file', 'deleted', 'upload_time']}
                        row_values = [filtered_dict.get(col, '') for col in all_columns]
                        ws.append(row_values)
                        row_count += 1
                    
                    # 每处理100000行记录一次进度，减少日志频率
                    if row_count % 100000 == 1:
                        current_app.logger.info(f'openpyxl Excel已写入 {row_count-1}/{total_count} 行')
                
                # 应用颜色（简化版本，跳过以节省时间）
                if show_differences and column_color_maps:
                    current_app.logger.info('跳过颜色格式应用以节省处理时间...')
                
                # 保存到内存
                output = io.BytesIO()
                wb.save(output)
                output.seek(0)
                current_app.logger.info(f'openpyxl Excel生成完成，共 {row_count-1} 行')
            
            # Excel文件生成完成，准备返回
            headers = Headers()
            # 处理中文文件名编码问题
            import urllib.parse
            disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
            headers.set('Content-Disposition', disposition)
            current_app.logger.info(f'Excel文件生成完成，文件名: {filename}')
            
            # 写入下载日志
            try:
                log_file_download(filename, 'file')
            except Exception as _:
                current_app.logger.warning('记录SubList下载日志失败')
            
            return Response(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers=headers)
        else:
            # CSV格式 - 使用流式响应优化性能
            current_app.logger.info('开始生成CSV文件（流式处理）')
            filename = f'sublist_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

            # 性能优化（H-6）：不再预加载 all_data（原先在内存中累积全部行，百万级时
            # 峰值达数百 MB）。改为在生成器内按批查询并逐行 yield，内存占用降为常数级。
            # 使用 stream_with_context 保证响应流式产出期间应用/请求上下文可用。
            from flask import stream_with_context

            def generate_csv():
                # 生成CSV头部
                yield ','.join(f'"{col}"' for col in all_columns) + '\n'
                # yield_per 游标式分批（500 行/批），边取边写，不驻留全量数据
                for item in query.yield_per(500):
                    item_dict = item.to_dict()
                    row_values = [item_dict.get(col, '') for col in all_columns]
                    csv_row = ','.join(
                        f'"{str(cell) if cell is not None else ""}"' for cell in row_values)
                    yield csv_row + '\n'

            headers = Headers()
            # 处理中文文件名编码问题
            import urllib.parse
            disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
            headers.set('Content-Disposition', disposition)
            
            try:
                log_file_download(filename, 'file')
            except Exception as _:
                current_app.logger.warning('记录SubList下载日志失败')
            
            return Response(stream_with_context(generate_csv()),
                            mimetype='text/csv', headers=headers)

    except Exception as e:
        current_app.logger.error(f'下载SubList数据出错: {str(e)}')
        return jsonify({'error': '下载数据时出错'}), 500


# 用量列表下载路由
@main_bp.route('/usagelist_download', methods=['POST'])
@csrf_exempt_route
def usagelist_download():
    try:
        current_app.logger.info('开始处理UsageList下载请求')
        # 权限：UsageList 下载
        if 'user_id' not in session:
            current_app.logger.warning('用户未登录')
            return jsonify({'error': '未登录'}), 401
        user = db.session.get(UserInfo, session['user_id'])
        if not user.is_admin:
            permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
            if not permission or not permission.can_usage_list_download:
                return jsonify({'error': '权限不足'}), 403
        data = request.get_json()
        current_app.logger.debug(f'收到下载请求数据: {data}')
        if not data:
            current_app.logger.warning('请求数据为空')
            return jsonify({'error': '请求数据为空'}), 400

        search_params = data.get('search_params', {})
        format_type = data.get('format', 'csv').lower()
        selected_columns = data.get('columns', [])  # 获取用户选择的列
        current_app.logger.debug(f'解析参数: format={format_type}, selected_columns={len(selected_columns)}')

        if format_type not in {'csv', 'excel'}:
            current_app.logger.warning(f'无效格式: {format_type}')
            return jsonify({'error': '无效格式，仅支持csv/excel'}), 400

        query = UsageList.query.filter(UsageList.deleted == False, UsageList.is_frozen == False)
        # 与 usage_list 列表路由保持完全同口径：字段走白名单、值统一 strip 后再过滤。
        # 修复：前端若传了未 trim 的输入（如 'PAD '，中文输入法易带全角/半角空格），
        # LIKE '%PAD %' 会命中 0 行，导致页面明明有数据却提示「无匹配数据」。
        usage_searchable_fields = ['SubPno', 'EoKsc', 'VehicleType', 'Comp_Kind', 'Detail_Kind',
                                   'KscPno', 'Part_Name', 'Usage', 'Length']
        for field in usage_searchable_fields:
            value = str(search_params.get(field) or '').strip()
            if value:
                # 与列表页搜索语义一致：字面子串模糊匹配（转义 LIKE 通配符）
                query = query.filter(_fuzzy_search_expr(getattr(UsageList, field), value))

        # P2-1: 使用 with_entities(func.count()) 避免 query.count() 的子查询开销
        from sqlalchemy import func as dl_func
        total_count = query.with_entities(dl_func.count(UsageList.id)).scalar()
        current_app.logger.info(f'UsageList数据库中共有 {total_count} 条记录')
        
        if total_count == 0:
            current_app.logger.warning('没有数据可导出')
            return jsonify({'error': '无匹配数据'}), 404
        
        # 如果数据量过大，使用流式处理
        if total_count > 50000:
            current_app.logger.info(f'UsageList数据量较大({total_count}条)，使用流式导出处理')

        # 性能优化（M-1）：keyset 分页替代 OFFSET，避免百万级导出越翻越慢。
        _ufirst = query.with_entities(UsageList.id).first()
        usage_last_id = _ufirst[0] if _ufirst else None

        def iter_usage_batches(batch_size):
            """按 id 游标分批返回 [id, ...]，避免大 offset 的 O(n) 跳过开销。"""
            nonlocal usage_last_id
            cursor = usage_last_id
            while True:
                rows = (query.order_by(UsageList.id)
                        .filter(UsageList.id > cursor)
                        .limit(batch_size)
                        .with_entities(UsageList.id).all())
                if not rows:
                    break
                cursor = rows[-1][0]
                yield [r[0] for r in rows]

        # 获取模型的所有字段（排除不需要的）
        excluded_fields = ['source_file', 'deleted', 'upload_time']
        all_fields = [c.key for c in UsageList.__table__.columns if c.key not in excluded_fields]

        # 如果没有选择列，使用所有可用字段
        if not selected_columns:
            columns_to_export = all_fields
        else:
            # 过滤出存在且有效的列
            columns_to_export = [col for col in selected_columns if col in all_fields]

            # 如果过滤后没有有效列，使用所有可用字段
            if not columns_to_export:
                columns_to_export = all_fields

        # 确保车种列（VehicleType）始终被包含
        if 'VehicleType' not in columns_to_export:
            # 尝试插入到合适位置
            try:
                if 'SubPno' in columns_to_export:
                    idx = columns_to_export.index('SubPno')
                    columns_to_export.insert(idx, 'VehicleType')
                elif 'id' in columns_to_export:
                    idx = columns_to_export.index('id')
                    columns_to_export.insert(idx + 1, 'VehicleType')
                else:
                    columns_to_export.insert(0, 'VehicleType')
                current_app.logger.info('已强制添加车种列(VehicleType)')
            except Exception as e:
                current_app.logger.warning(f'添加车种列失败: {str(e)}')

        # 调整顺序：将 VehicleType 放在 id 之后、SubPno 之前
        try:
            cols = list(columns_to_export)
            has_vehicle = 'VehicleType' in cols
            if has_vehicle:
                cols.remove('VehicleType')
                # 找到 id 的位置
                if 'id' in cols:
                    id_idx = cols.index('id')
                    insert_idx = id_idx + 1
                else:
                    insert_idx = 0
                # 如果 SubPno 存在且其位置在 id 后面，则将插入位置设置为 SubPno 当前位置
                if 'SubPno' in cols:
                    sub_idx = cols.index('SubPno')
                    if sub_idx > insert_idx:
                        insert_idx = sub_idx
                cols.insert(insert_idx if insert_idx <= len(cols) else len(cols), 'VehicleType')
            columns_to_export = cols
        except Exception:
            pass

        # 本地化列名处理
        display_columns = columns_to_export.copy()
        try:
            if 'VehicleType' in display_columns:
                vehicle_type_idx = display_columns.index('VehicleType')
                display_columns[vehicle_type_idx] = '车种'
        except Exception:
            pass

        if format_type == 'excel':
            current_app.logger.info('开始生成Excel文件（优化版本）')
            filename = f'usagelist_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            
            # 对于大数据量，使用优化的Excel生成
            if total_count > 50000:
                current_app.logger.info(f'UsageList数据量较大({total_count}行)，使用优化Excel生成')
            
            try:
                # 尝试使用xlsxwriter（更快）
                try:
                    import xlsxwriter
                    current_app.logger.info('使用xlsxwriter引擎生成UsageList Excel')
                    
                    output = io.BytesIO()
                    workbook = xlsxwriter.Workbook(output, {'constant_memory': True, 'default_date_format': 'yyyy-mm-dd'})
                    worksheet = workbook.add_worksheet('UsageList')
                    
                    # 写入表头
                    for col_idx, col_name in enumerate(display_columns):
                        worksheet.write(0, col_idx, col_name)
                    
                    # 分批写入数据
                    row_count = 1
                    batch_size = 10000  # 优化批次大小以提高性能
                    
                    for id_batch in iter_usage_batches(batch_size):
                        batch_items = UsageList.query.filter(UsageList.id.in_(id_batch)).order_by(UsageList.id).all()
                        if not batch_items:
                            break
                        
                        # 批量写入数据
                        for item in batch_items:
                            item_dict = item.to_dict()
                            filtered_dict = {k: v for k, v in item_dict.items() if k in columns_to_export}
                            
                            for col_idx, col in enumerate(columns_to_export):
                                value = filtered_dict.get(col, '')
                                worksheet.write(row_count, col_idx, value)
                            
                            row_count += 1
                        
                            # 每处理100000行记录一次进度，减少日志频率
                            if row_count % 100000 == 1:
                                current_app.logger.info(f'UsageList Excel已写入 {row_count-1}/{total_count} 行')
                    
                    workbook.close()
                    output.seek(0)
                    current_app.logger.info(f'xlsxwriter UsageList Excel生成完成，共 {row_count-1} 行')
                    
                except ImportError:
                    current_app.logger.warning('xlsxwriter不可用，回退到openpyxl')
                    raise ImportError("xlsxwriter not available")
                except Exception as e:
                    current_app.logger.error(f'xlsxwriter UsageList处理失败: {str(e)}，回退到openpyxl')
                    raise ImportError("xlsxwriter processing failed")
                
            except ImportError:
                # 回退到优化的openpyxl
                current_app.logger.info('使用优化的openpyxl引擎生成UsageList Excel')
                
                from openpyxl import Workbook
                
                wb = Workbook()
                ws = wb.active
                ws.title = 'UsageList'
                
                # 写入表头
                ws.append(display_columns)
                
                # 分批写入数据
                row_count = 1
                batch_size = 10000
                
                for id_batch in iter_usage_batches(batch_size):
                    batch_items = UsageList.query.filter(UsageList.id.in_(id_batch)).order_by(UsageList.id).all()
                    if not batch_items:
                        break
                    
                    # 批量准备数据
                    batch_data = []
                    for item in batch_items:
                        item_dict = item.to_dict()
                        filtered_dict = {k: v for k, v in item_dict.items() if k in columns_to_export}
                        row_values = [filtered_dict.get(col, '') for col in columns_to_export]
                        batch_data.append(row_values)
                    
                    # 批量写入
                    for row_values in batch_data:
                        ws.append(row_values)
                        row_count += 1
                    
                    # 每处理100000行记录一次进度，减少日志频率
                    if row_count % 100000 == 1:
                        current_app.logger.info(f'openpyxl UsageList Excel已写入 {row_count-1}/{total_count} 行')
                
                # 保存到内存
                output = io.BytesIO()
                wb.save(output)
                output.seek(0)
                current_app.logger.info(f'openpyxl UsageList Excel生成完成，共 {row_count-1} 行')
            
            # Excel文件生成完成，准备返回
            headers = Headers()
            # 处理中文文件名编码问题
            import urllib.parse
            disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
            headers.set('Content-Disposition', disposition)
            current_app.logger.info(f'UsageList Excel文件生成完成，文件名: {filename}')
            
            # 写入下载日志
            try:
                log_file_download(filename, 'file')
            except Exception as _:
                current_app.logger.warning('记录UsageList下载日志失败')
            
            return Response(output, mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                            headers=headers)
        else:
            # CSV格式 - 使用流式响应优化性能
            current_app.logger.info('开始生成CSV文件（流式处理）')
            filename = f'usagelist_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'

            # 性能优化（H-6）：与 SubList 导出同理，去除 all_data 预加载，改为游标式流式产出。
            from flask import stream_with_context

            def generate_csv():
                # 生成CSV头部
                yield ','.join(f'"{col}"' for col in display_columns) + '\n'
                # yield_per 游标式分批（500 行/批），边取边写，内存常数级
                for item in query.yield_per(500):
                    item_dict = item.to_dict()
                    row_values = [item_dict.get(col, '') for col in columns_to_export]
                    csv_row = ','.join(
                        f'"{str(cell) if cell is not None else ""}"' for cell in row_values)
                    yield csv_row + '\n'
            
            headers = Headers()
            # 处理中文文件名编码问题
            import urllib.parse
            disposition = f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}"
            headers.set('Content-Disposition', disposition)
            
            try:
                log_file_download(filename, 'file')
            except Exception as _:
                current_app.logger.warning('记录UsageList下载日志失败')
            
            return Response(stream_with_context(generate_csv()),
                            mimetype='text/csv', headers=headers)

    except Exception as e:
        current_app.logger.error(f'下载错误: {str(e)}', exc_info=True)
        return jsonify({'error': '服务器内部错误，请重试'}), 500
# 在routes.py文件中，使用蓝图定义批量删除路由
# 批量删除路由处理函数
@main_bp.route('/batch_delete_files', methods=['POST'])
def batch_delete_files():
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    user_id = session['user_id']
    
    # 使用兼容的权限检查 - 检查多个权限（任一满足即可）
    if not check_multiple_permissions(user_id, ['can_file_management_delete', 'can_batch_delete']):
        return jsonify({'success': False, 'message': '你没有批量删除文件的权限'}), 403

    try:
        # 获取前端发送的文件ID列表
        data = request.get_json()
        file_ids = data.get('file_ids', [])
        # 统一转为整数ID，过滤非法项
        try:
            file_ids = [int(fid) for fid in file_ids if str(fid).strip().isdigit()]
        except Exception:
            return jsonify({'success': False, 'message': '文件ID格式不正确'}), 400

        if not file_ids:
            return jsonify({'success': False, 'message': '没有提供文件ID'}), 400

        # 开始事务

        # 批量查找文件对象
        files_to_delete = FileInfo.query.filter(FileInfo.id.in_(file_ids)).all()
        found_ids = {f.id for f in files_to_delete}
        not_found_ids = [fid for fid in file_ids if fid not in found_ids]
        deleted_count = 0

        # 冻结校验：整批中含冻结文件时整批拒绝（删除不可逆，避免部分成功造成误判）
        frozen_names = assert_files_mutable(files_to_delete)
        if frozen_names:
            return jsonify({
                'success': False,
                'message': f'以下 {len(frozen_names)} 个文件已冻结，禁止删除，请先解冻后再操作：'
                           + '、'.join(frozen_names[:10]) + ('…' if len(frozen_names) > 10 else ''),
                'code': 'FROZEN_FILE',
                'frozen_files': frozen_names
            }), 409

        # 按文件类型分组，批量删除关联数据
        sublist_files = [f for f in files_to_delete if f.file_type == 'SubList']
        usagelist_files = [f for f in files_to_delete if f.file_type == 'UsageList']
        
        # 批量删除SubList关联数据
        if sublist_files:
            sublist_filenames = [f.filename for f in sublist_files]
            try:
                # 使用批量删除，提高性能
                SubList.query.filter(SubList.source_file.in_(sublist_filenames)).delete(synchronize_session=False)
                current_app.logger.info(f"批量删除 {len(sublist_filenames)} 个SubList文件的关联数据")
            except Exception as e:
                current_app.logger.warning(f"批量删除SubList关联数据失败: {str(e)}")
                # 回退到逐个删除
                for file_info in sublist_files:
                    try:
                        SubList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
                    except Exception as e2:
                        current_app.logger.warning(f"删除SubList关联数据失败 {file_info.filename}: {str(e2)}")
        
        # 批量删除UsageList关联数据
        if usagelist_files:
            usagelist_filenames = [f.filename for f in usagelist_files]
            try:
                # 使用批量删除，提高性能
                UsageList.query.filter(UsageList.source_file.in_(usagelist_filenames)).delete(synchronize_session=False)
                current_app.logger.info(f"批量删除 {len(usagelist_filenames)} 个UsageList文件的关联数据")
            except Exception as e:
                current_app.logger.warning(f"批量删除UsageList关联数据失败: {str(e)}")
                # 回退到逐个删除
                for file_info in usagelist_files:
                    try:
                        UsageList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
                    except Exception as e2:
                        current_app.logger.warning(f"删除UsageList关联数据失败 {file_info.filename}: {str(e2)}")

        # 批量删除物理文件
        failed_files = []
        for file_info in files_to_delete:
            file_path = resolve_upload_path(file_info)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    current_app.logger.warning(f"删除物理文件失败 {file_info.filename}: {str(e)}")
                    failed_files.append(file_info.filename)

        # 记录批量删除操作日志（在删除文件记录之前）
        try:
            filenames = [f.filename for f in files_to_delete]
            current_app.logger.info(f"开始记录批量删除日志: {len(filenames)}个文件")
            result = log_batch_file_delete(filenames, len(files_to_delete), user_id)
            current_app.logger.info(f"批量删除日志记录结果: {result}")
        except Exception as e:
            current_app.logger.warning(f"记录批量删除操作日志失败: {str(e)}")

        # 批量删除文件记录
        try:
            # 使用批量删除，提高性能
            deleted_count = FileInfo.query.filter(FileInfo.id.in_(file_ids)).delete(synchronize_session=False)
            current_app.logger.info(f"批量删除 {deleted_count} 个文件记录")
        except Exception as e:
            current_app.logger.warning(f"批量删除文件记录失败: {str(e)}")
            # 回退到逐个删除
            deleted_count = 0
            for file_info in files_to_delete:
                try:
                    db.session.delete(file_info)
                    deleted_count += 1
                except Exception as e2:
                    current_app.logger.error(f"删除文件记录失败 {file_info.filename}: {str(e2)}")
                    continue

        # 提交事务
        db.session.commit()

        # 验证删除结果
        remaining_count = FileInfo.query.filter(FileInfo.id.in_(file_ids)).count()

        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 个文件',
            'deleted_count': deleted_count,
            'requested_ids': file_ids,
            'not_found_ids': not_found_ids,
            'remaining_after_commit': int(remaining_count),
            'failed_files': failed_files
        }), 200

    except Exception as e:
        # 发生错误，回滚事务

        db.session.rollback()
        current_app.logger.error(f'批量删除文件失败: {str(e)}')
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500


# 按文件名批量删除路由处理函数
@main_bp.route('/batch_delete_files_by_filename', methods=['POST'])
def batch_delete_files_by_filename():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': '未登录'}), 401

    user_id = session['user_id']
    
    # 使用兼容的权限检查 - 检查多个权限（任一满足即可）
    if not check_multiple_permissions(user_id, ['can_file_management_delete', 'can_batch_delete']):
        return jsonify({'success': False, 'message': '你没有批量删除文件的权限'}), 403

    try:
        # 获取前端发送的文件名列表
        data = request.get_json()
        filenames = data.get('filenames', [])
        
        # 过滤空字符串
        filenames = [filename.strip() for filename in filenames if filename.strip()]
        
        if not filenames:
            return jsonify({'success': False, 'message': '没有提供文件名'}), 400

        # 开始事务

        # 批量查找文件对象
        files_to_delete = FileInfo.query.filter(FileInfo.filename.in_(filenames)).all()
        found_filenames = {f.filename for f in files_to_delete}
        not_found_filenames = [filename for filename in filenames if filename not in found_filenames]
        deleted_count = 0

        if not files_to_delete:
            return jsonify({
                'success': False, 
                'message': '没有找到匹配的文件',
                'not_found_filenames': not_found_filenames
            }), 404

        # 冻结校验：整批中含冻结文件时整批拒绝（删除不可逆，避免部分成功造成误判）
        frozen_names = assert_files_mutable(files_to_delete)
        if frozen_names:
            return jsonify({
                'success': False,
                'message': f'以下 {len(frozen_names)} 个文件已冻结，禁止删除，请先解冻后再操作：'
                           + '、'.join(frozen_names[:10]) + ('…' if len(frozen_names) > 10 else ''),
                'code': 'FROZEN_FILE',
                'frozen_files': frozen_names
            }), 409

        # 保存要删除的文件名列表，用于后续验证
        delete_filenames = [f.filename for f in files_to_delete]
        
        # 按文件类型分组，批量删除关联数据
        sublist_files = [f for f in files_to_delete if f.file_type == 'SubList']
        usagelist_files = [f for f in files_to_delete if f.file_type == 'UsageList']
        
        # 批量删除SubList关联数据
        if sublist_files:
            sublist_filenames = [f.filename for f in sublist_files]
            try:
                # 使用批量删除，提高性能
                SubList.query.filter(SubList.source_file.in_(sublist_filenames)).delete(synchronize_session=False)
                current_app.logger.info(f"批量删除 {len(sublist_filenames)} 个SubList文件的关联数据")
            except Exception as e:
                current_app.logger.warning(f"批量删除SubList关联数据失败: {str(e)}")
                # 回退到逐个删除
                for file_info in sublist_files:
                    try:
                        SubList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
                    except Exception as e2:
                        current_app.logger.warning(f"删除SubList关联数据失败 {file_info.filename}: {str(e2)}")
        
        # 批量删除UsageList关联数据
        if usagelist_files:
            usagelist_filenames = [f.filename for f in usagelist_files]
            try:
                # 使用批量删除，提高性能
                UsageList.query.filter(UsageList.source_file.in_(usagelist_filenames)).delete(synchronize_session=False)
                current_app.logger.info(f"批量删除 {len(usagelist_filenames)} 个UsageList文件的关联数据")
            except Exception as e:
                current_app.logger.warning(f"批量删除UsageList关联数据失败: {str(e)}")
                # 回退到逐个删除
                for file_info in usagelist_files:
                    try:
                        UsageList.query.filter_by(source_file=file_info.filename).delete(synchronize_session=False)
                    except Exception as e2:
                        current_app.logger.warning(f"删除UsageList关联数据失败 {file_info.filename}: {str(e2)}")

        # 批量删除物理文件
        failed_files = []
        for file_info in files_to_delete:
            file_path = resolve_upload_path(file_info)
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError as e:
                    current_app.logger.warning(f"删除物理文件失败 {file_info.filename}: {str(e)}")
                    failed_files.append(file_info.filename)

        # 记录批量删除操作日志（在删除文件记录之前）
        try:
            log_filenames = [f.filename for f in files_to_delete]
            current_app.logger.info(f"开始记录按文件名批量删除日志: {len(log_filenames)}个文件")
            result = log_batch_file_delete(log_filenames, len(files_to_delete), user_id)
            current_app.logger.info(f"按文件名批量删除日志记录结果: {result}")
        except Exception as e:
            current_app.logger.warning(f"记录按文件名批量删除操作日志失败: {str(e)}")

        # 批量删除文件记录
        try:
            # 使用批量删除，提高性能
            deleted_count = FileInfo.query.filter(FileInfo.filename.in_(delete_filenames)).delete(synchronize_session=False)
            current_app.logger.info(f"按文件名批量删除 {deleted_count} 个文件记录")
        except Exception as e:
            current_app.logger.warning(f"按文件名批量删除文件记录失败: {str(e)}")
            # 回退到逐个删除
            deleted_count = 0
            for file_info in files_to_delete:
                try:
                    db.session.delete(file_info)
                    deleted_count += 1
                except Exception as e2:
                    current_app.logger.error(f"按文件名删除文件记录失败 {file_info.filename}: {str(e2)}")
                    continue

        # 提交事务
        db.session.commit()

        # 验证删除结果
        remaining_count = FileInfo.query.filter(FileInfo.filename.in_(delete_filenames)).count()

        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 个文件',
            'deleted_count': deleted_count,
            'requested_filenames': filenames,
            'not_found_filenames': not_found_filenames,
            'remaining_after_commit': int(remaining_count),
            'failed_files': failed_files
        }), 200

    except Exception as e:
        # 发生错误，回滚事务

        db.session.rollback()
        current_app.logger.error(f'按文件名批量删除文件失败: {str(e)}')
        return jsonify({'success': False, 'message': f'删除失败: {str(e)}'}), 500

@main_bp.route('/api/vehicle_types/all_ids', methods=['GET'])
@csrf_exempt_route
def get_all_vehicle_type_ids():
    """获取所有车种数据的ID（用于全选功能）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.vehicle_type import VehicleType
        
        # 获取所有车种的ID
        all_ids = db.session.query(VehicleType.id).all()
        vehicle_ids = [str(id[0]) for id in all_ids]
        
        current_app.logger.info(f"获取所有车种ID，共 {len(vehicle_ids)} 个")
        
        return jsonify({
            'success': True,
            'total_count': len(vehicle_ids),
            'vehicle_ids': vehicle_ids
        })
        
    except Exception as e:
        current_app.logger.error(f"获取所有车种ID失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/vehicle_types/batch_delete', methods=['POST'])
@csrf_exempt_route
def batch_delete_vehicle_types():
    """批量删除车种数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 400
    # 权限：车种批量删除
    user_id = session['user_id']
    if not check_permission(user_id, 'can_vehicle_types_batch_delete'):
        return jsonify({'success': False, 'error': '没有批量删除车种的权限'}), 403
    try:
        data = request.get_json()
        vehicle_ids = data.get('vehicle_ids', [])
        
        if not vehicle_ids:
            return jsonify({'success': False, 'error': '没有选择要删除的车种'}), 400
        
        from models.vehicle_type import VehicleType
        
        # 查询要删除的车种
        vehicles_to_delete = VehicleType.query.filter(VehicleType.id.in_(vehicle_ids)).all()
        
        if not vehicles_to_delete:
            return jsonify({'success': False, 'error': '未找到要删除的车种'}), 404
        
        # 记录要删除的车种信息（用于日志）
        deleted_part_numbers = [v.part_number for v in vehicles_to_delete]
        current_app.logger.info(f"批量删除车种: {deleted_part_numbers}")
        
        # 执行删除
        for vehicle in vehicles_to_delete:

            db.session.delete(vehicle)
        
        # 提交事务

        db.session.commit()
        
        deleted_count = len(vehicles_to_delete)
        current_app.logger.info(f"成功批量删除 {deleted_count} 个车种")

        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            detail = "||".join(deleted_part_numbers)
            log_action(OperationType.BATCH_DELETE_VEHICLE_TYPE,
                       f"批量删除车种 {deleted_count} 个",
                       TargetType.VEHICLE_TYPE, None, f"{deleted_count}个车种", detail)
        except Exception as _le:
            current_app.logger.warning(f"记录批量删除车种日志失败: {_le}")
        
        return jsonify({
            'success': True,
            'message': f'成功删除 {deleted_count} 个车种',
            'deleted_count': deleted_count,
            'deleted_part_numbers': deleted_part_numbers
        })
        
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"批量删除车种失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# 成品管理API路由
@main_bp.route('/api/finished_products', methods=['POST'])
@csrf_exempt_route
def add_finished_product():
    """添加成品"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import FinishedProduct
        
        vehicle_name = request.form.get('vehicle_name', '').strip()
        part_number = request.form.get('part_number', '').strip()
        lot_number = request.form.get('lot_number', '').strip()
        eo = request.form.get('eo', '').strip()
        quantity = request.form.get('quantity', '').strip()
        remark = request.form.get('remark', '').strip()
        
        # 验证必填字段
        if not all([vehicle_name, part_number, lot_number, quantity]):
            return jsonify({'success': False, 'error': '请填写所有必填字段'}), 400
        
        # 验证数量格式
        try:
            quantity = int(quantity)
            if quantity < 0:
                return jsonify({'success': False, 'error': '数量必须大于等于0'}), 400
        except ValueError:
            return jsonify({'success': False, 'error': '数量必须是有效的数字'}), 400
        
        # 检查LOT号是否已存在
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        existing_lot = FinishedProductModel.query.filter_by(lot_number=lot_number).first()
        if existing_lot:
            return jsonify({'success': False, 'error': f'LOT号 {lot_number} 已存在，请使用其他LOT号'}), 400
        
        # 不再校验品番是否存在于 UsageList（按需求，仅处理 EO 与 LOT）

        # 获取UsageList中的EO信息（若品番不存在，usage_eo 视为 None，并标记注意）
        part_exists = part_exists_in_usagelist(part_number)
        usage_eo = get_usage_eo_for_part(part_number) if part_exists else None
        eo_override = False
        needs_eo_attention = False
        
        # EO逻辑处理
        if eo and usage_eo and eo != usage_eo:
            # 用户输入的EO与UsageList中的EO不一致，标记为强制覆盖
            eo_override = True
        elif not eo and usage_eo:
            # 用户未输入EO但UsageList中有EO，标记为需要注意
            needs_eo_attention = True
            eo = None  # 确保EO为空
        elif not part_exists:
            # 品番在 UsageList 中不存在，也标记为需要注意
            needs_eo_attention = True

        # 创建新成品（FinishedProductModel 已在上方 LOT 号检查时获取）
        new_product = FinishedProductModel(
            vehicle_name=vehicle_name,
            part_number=part_number,
            lot_number=lot_number,
            quantity=quantity,
            remark=remark,
            source='manual',
            create_time=datetime.now(),
            update_time=datetime.now()
        )
        # 兼容旧模型：仅在存在对应属性时再赋值
        if hasattr(new_product, 'eo'):
            new_product.eo = eo
        if hasattr(new_product, 'eo_override'):
            new_product.eo_override = eo_override
        if hasattr(new_product, 'usage_eo'):
            new_product.usage_eo = usage_eo
        if hasattr(new_product, 'needs_eo_attention'):
            new_product.needs_eo_attention = needs_eo_attention
        # 记录操作人信息
        current_user_id = session.get('user_id')
        if hasattr(new_product, 'created_by'):
            new_product.created_by = current_user_id
        if hasattr(new_product, 'updated_by'):
            new_product.updated_by = current_user_id
        
        db.session.add(new_product)
        db.session.commit()
        
        current_app.logger.info(f"成功添加成品: {vehicle_name} - {part_number} - {lot_number}")
        # 操作记录：添加成品（带字段明细）
        try:
            detail = f"{_log_sanitize(vehicle_name)}|{_log_sanitize(part_number)}|{_log_sanitize(lot_number)}|{_log_sanitize(eo or '')}|{_log_sanitize(quantity)}|{_log_sanitize(remark or '')}"
            log_operation(
                operation_type=OperationType.ADD_PRODUCT,
                operation_desc=f"添加成品: {vehicle_name}-{part_number}",
                target_type=TargetType.PRODUCT,
                target_id=new_product.id,
                target_name=part_number,
                file_path=detail
            )
        except Exception:
            current_app.logger.warning('记录添加成品操作日志失败')
        
        return jsonify({
            'success': True,
            'message': '成品添加成功',
            'product_id': new_product.id
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"添加成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/batch_add', methods=['POST'])
@csrf_exempt_route
def batch_add_finished_products():
    """批量添加成品"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：成品批量添加
    user_id = session['user_id']
    if not check_permission(user_id, 'can_products_batch_add'):
        return jsonify({'success': False, 'error': '没有批量添加成品的权限'}), 403
    try:
        from models.finished_product import get_finished_product_model
        
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        batch_data = data.get('batch_data', '').strip()
        
        if not batch_data:
            return jsonify({
                'success': False, 
                'error': '请输入批量数据',
                'format_help': '数据格式：车种,EO,品番,LOT号,数量,备注(可选)\n示例：\n测试车种,EO001,测试品番,TEST001,10,测试备注\n测试车种2,EO002,测试品番2,TEST002,20'
            }), 400
        
        lines = batch_data.split('\n')
        success_count = 0
        failed_count = 0
        errors = []
        created_products = []

        # 预取：收集所有将要处理的 lot 与 part_number（按新顺序：车种,EO,品番,LOT,数量,备注）
        raw_items = []
        for line in lines:
            s = line.strip()
            if not s:
                continue
            import re
            parts = [p.strip() for p in re.split(r'[,，、;；]', s) if p.strip()]
            if len(parts) >= 5:
                raw_items.append(parts)

        from sqlalchemy import select
        # 预取已存在的 LOT 集合（索引3）
        lot_numbers = [p[3].strip() for p in raw_items]
        if lot_numbers:
            existing_lots = set(
                x[0] for x in db.session.execute(
                    select(FinishedProductModel.lot_number).where(FinishedProductModel.lot_number.in_(lot_numbers))
                ).all()
            )
        else:
            existing_lots = set()

        # 在同一批次内也需要去重，避免同批内重复 LOT 触发唯一约束
        batch_seen_lots = set(existing_lots)

        # 预取 UsageList 的 EO 映射（part_number 在索引2，品番级活跃优先）
        part_numbers = list({p[2].strip() for p in raw_items})
        usage_eo_multi = build_usage_eo_map(part_numbers)
        
        processed_lines = 0
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line:
                continue
                
            try:
                # 解析行数据（新顺序）：车种,EO,品番,LOT,数量,备注(可选)
                # 使用更鲁棒的分割方法，支持多种逗号字符
                import re
                
                # 检测字符串中的逗号类型
                comma_chars = []
                for char in line:
                    if char in [',', '，', '、', ';', '；']:
                        comma_chars.append(char)
                
                # 使用正则表达式分割，支持多种分隔符
                # 支持：英文/中文逗号、顿号、分号、制表符、多空格，且允许行首带序号列
                # 跳过提示/表头/示例行
                if re.search(r'(格式|示例|header|表头)', line, re.IGNORECASE):
                    continue
                if re.search(r'(车种|品番|LOT|数量|备注|EO)', line) and (',' not in line and '，' not in line and '、' not in line and ';' not in line and '；' not in line and '\t' not in line):
                    # 标题行，直接跳过
                    continue
                raw_parts = re.split(r'[,，、;；\t]+|\s{2,}', line)
                parts = [part.strip() for part in raw_parts if part.strip()]
                # 如果首列是序号(#/数字)，仅当总字段数>=6时才丢弃（避免把合法第一列数字当作序号误删）
                if parts and (parts[0] == '#' or parts[0].isdigit()) and len(parts) >= 6:
                    parts = parts[1:]
                
                # 调试信息：显示原始分割结果和逗号检测
                # debug_info = f"检测到的分隔符: {comma_chars}\n原始分割: {raw_parts}\n清理后: {parts}"
                
                if len(parts) < 5:
                    errors.append(f"第{line_num}行: 数据格式错误，当前有{len(parts)}个字段，至少需要5个字段\n  当前数据: {line}\n  正确格式: 车种,EO,品番,LOT号,数量,备注(可选)")
                    failed_count += 1
                    continue
                
                vehicle_name = parts[0].strip()
                eo = parts[1].strip()
                part_number = parts[2].strip()
                lot_number = parts[3].strip()
                quantity_str = parts[4].strip()
                remark = parts[5].strip() if len(parts) > 5 else ''
                processed_lines += 1
                
                # 验证必填字段（备注是可选的）。车种允许为空
                if not part_number:
                    errors.append(f"第{line_num}行: 品番不能为空")
                    failed_count += 1
                    continue
                if not lot_number:
                    errors.append(f"第{line_num}行: LOT号不能为空")
                    failed_count += 1
                    continue
                if not quantity_str:
                    errors.append(f"第{line_num}行: 数量不能为空")
                    failed_count += 1
                    continue
                
                # 验证数量格式
                try:
                    quantity = int(quantity_str)
                    if quantity < 0:
                        errors.append(f"第{line_num}行: 数量 '{quantity_str}' 必须大于等于0")
                        failed_count += 1
                        continue
                except ValueError:
                    errors.append(f"第{line_num}行: 数量 '{quantity_str}' 必须是有效的数字")
                    failed_count += 1
                    continue
                
                # 检查LOT号是否已存在（包含数据库已有和本批次前面出现过的）
                if lot_number in batch_seen_lots:
                    errors.append(f"第{line_num}行: LOT号 {lot_number} 已存在（数据库或本批次内重复）")
                    failed_count += 1
                    continue
                # 标记为已使用，防止本批后续再用同一 LOT
                batch_seen_lots.add(lot_number)
                
                # 不再校验品番是否存在于 UsageList（按需求，仅处理 EO 与 LOT）

                # 获取UsageList中的EO信息（若品番不存在，usage_eo 视为 None，并标记注意）
                usage_eo_set = usage_eo_multi.get(part_number, set())
                usage_eo = (sorted(usage_eo_set)[0] if usage_eo_set else None)
                part_exists = bool(usage_eo_set)
                eo_override = False
                needs_eo_attention = False

                # EO逻辑处理（支持多EO：提供的EO在集合中即视为一致）
                if eo and eo in usage_eo_set:
                    eo_override = False
                elif eo and usage_eo and eo != usage_eo:
                    # 用户输入的EO与UsageList中的EO不一致，标记为强制覆盖
                    eo_override = True
                elif not eo and usage_eo:
                    # 用户未输入EO但UsageList中有EO，标记为需要注意
                    needs_eo_attention = True
                    eo = None  # 确保EO为空
                elif not part_exists:
                    needs_eo_attention = True

                # 创建新成品
                new_product = FinishedProductModel(
                    vehicle_name=vehicle_name,
                    part_number=part_number,
                    lot_number=lot_number,
                    quantity=quantity,
                    remark=remark,
                    source='batch',
                    create_time=datetime.now(),
                    update_time=datetime.now(),
                    created_by=user_id,
                    updated_by=user_id
                )
                if hasattr(new_product, 'eo'):
                    new_product.eo = eo
                if hasattr(new_product, 'eo_override'):
                    new_product.eo_override = eo_override
                if hasattr(new_product, 'usage_eo'):
                    new_product.usage_eo = usage_eo
                if hasattr(new_product, 'needs_eo_attention'):
                    new_product.needs_eo_attention = needs_eo_attention
                
                db.session.add(new_product)
                created_products.append(new_product)
                success_count += 1
                
            except Exception as e:
                errors.append(f"第{line_num}行: {str(e)}")
                failed_count += 1
        
        # 提交事务
        if success_count > 0:
            db.session.commit()
            current_app.logger.info(f"批量添加成品成功: {success_count} 条，失败: {failed_count} 条")
            # 操作记录：批量添加成品（写入明细）
            try:
                # 从输入构建明细：车种|品番|LOT|EO|数量|备注（展示仍用统一的表头顺序）
                detail_lines = []
                for parts in raw_items:
                    try:
                        v = (parts[0] if len(parts) > 0 else '').strip()
                        eo_v = (parts[1] if len(parts) > 1 else '').strip()
                        p = (parts[2] if len(parts) > 2 else '').strip()
                        lot = (parts[3] if len(parts) > 3 else '').strip()
                        qty = (parts[4] if len(parts) > 4 else '').strip()
                        rmk = (parts[5] if len(parts) > 5 else '').strip()
                        detail_lines.append("|".join([
                            _log_sanitize(v),
                            _log_sanitize(p),
                            _log_sanitize(lot),
                            _log_sanitize(eo_v),
                            _log_sanitize(qty),
                            _log_sanitize(rmk),
                        ]))
                    except Exception:
                        continue
                # 确保没有空行与尾随分隔符
                detail_lines = [dl.strip('|') for dl in detail_lines if dl and dl.strip('|')]
                file_path = '||'.join(detail_lines[:2000]) if detail_lines else None
                log_operation(
                    operation_type=OperationType.BATCH_ADD_PRODUCT,
                    operation_desc=f"批量添加成品: 成功 {success_count} 条，失败 {failed_count} 条",
                    target_type=TargetType.PRODUCT,
                    file_path=file_path
                )
            except Exception:
                current_app.logger.warning('记录批量添加成品操作日志失败')
        
        if processed_lines == 0 and success_count == 0 and failed_count == 0:
            return jsonify({
                'success': False,
                'error': '未解析到任何有效数据，请按“车种,EO,品番,LOT,数量(,备注)”的顺序填写，每行一条',
                'format_help': '示例：\n轿车,EO001,A123,001,100\nSUV,EO002,B456,002,200'
            }), 400
        
        # 提交事务后，重新查询创建的产品对象，确保能够正确获取用户信息
        created_products_dict = []
        if success_count > 0:
            # 重新查询创建的产品对象，使用joinedload预加载用户信息
            from sqlalchemy.orm import joinedload
            created_lot_numbers = [product.lot_number for product in created_products]
            reloaded_products = db.session.query(FinishedProductModel).options(
                joinedload(FinishedProductModel.creator),
                joinedload(FinishedProductModel.modifier)
            ).filter(FinishedProductModel.lot_number.in_(created_lot_numbers)).all()
            
            # 转换为字典列表
            created_products_dict = [product.to_dict() for product in reloaded_products]
        
        return jsonify({
            'success': True,
            'message': f'批量添加完成，成功 {success_count} 条，失败 {failed_count} 条',
            'success_count': success_count,
            'failed_count': failed_count,
            'errors': errors,
            'created_products': created_products_dict
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"批量添加成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/ids', methods=['GET'])
@csrf_exempt_route
def get_finished_product_ids():
    """仅返回全部成品 ID 列表（用于前端「全选」等场景）
    性能优化：原前端调用 /api/finished_products?per_page=10000 会实例化全部 ORM 对象并逐条 to_dict，
    这里只查主键列，避免加载全部字段与关联对象。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()

        # 只取主键，走覆盖索引，不加载实体
        ids = [pid for (pid,) in db.session.query(FinishedProductModel.id).all()]
        return jsonify({'success': True, 'data': ids})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products', methods=['GET'])
@csrf_exempt_route
def get_finished_products():
    """获取成品列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        vehicle_name = request.args.get('vehicle_name', '').strip()
        part_number = request.args.get('part_number', '').strip()
        lot_number = request.args.get('lot_number', '').strip()
        
        # 构建查询，使用joinedload预先加载creator和modifier关系
        from sqlalchemy.orm import joinedload
        query = db.session.query(FinishedProductModel).options(
            joinedload(FinishedProductModel.creator),
            joinedload(FinishedProductModel.modifier)
        )
        
        if vehicle_name:
            query = query.filter(FinishedProductModel.vehicle_name.like(f'%{vehicle_name}%'))
        if part_number:
            query = query.filter(FinishedProductModel.part_number.like(f'%{part_number}%'))
        if lot_number:
            query = query.filter(FinishedProductModel.lot_number.like(f'%{lot_number}%'))
        
        # 按创建时间倒序排列
        query = query.order_by(FinishedProductModel.create_time.desc())
        
        # 分页
        pagination = query.paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # 转换为前端需要的格式
        products = []
        for product in pagination.items:
            products.append(product.to_dict())
        
        return jsonify({
            'success': True,
            'data': products,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取成品列表失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# 全局缓存，用于存储参考EO数据
_reference_eo_cache = {}
_cache_timestamp = 0
CACHE_DURATION = 300  # 缓存5分钟
_file_info_cache = None  # 缓存 file_info 数据
_file_info_cache_timestamp = 0
FILE_INFO_CACHE_DURATION = 600  # file_info 缓存10分钟

def clear_reference_eo_cache():
    """清除参考EO缓存"""
    global _reference_eo_cache, _cache_timestamp, _file_info_cache, _file_info_cache_timestamp
    _reference_eo_cache = {}
    _cache_timestamp = 0
    _file_info_cache = None
    _file_info_cache_timestamp = 0

def _normalize_part_number(pn):
    """标准化品番"""
    return pn.upper().replace(' ', '').replace('－', '-').replace('–', '-').replace('—', '-')

def _get_cached_file_info():
    """获取缓存的 file_info 数据"""
    import time
    global _file_info_cache, _file_info_cache_timestamp
    
    current_time = time.time()
    
    # 检查缓存是否有效
    if (_file_info_cache is not None and 
        current_time - _file_info_cache_timestamp < FILE_INFO_CACHE_DURATION):
        return _file_info_cache
    
    # 缓存失效，重新查询（含 is_frozen，用于成品页面标注冻结归档 EO 版本）
    from models.file_info import FileInfo
    _file_info_cache = FileInfo.query.filter(FileInfo.file_type == 'UsageList').with_entities(
        FileInfo.filename, FileInfo.eo, FileInfo.is_frozen
    ).all()
    _file_info_cache_timestamp = current_time

    return _file_info_cache

def _build_eo_maps(file_info_rows, pn_mapping):
    """基于 file_info (filename, eo, is_frozen) 行构建：
    返回 (品番->EO列表, 品番->冻结EO列表)。
    同一 EO 只要存在活跃文件即视为活跃；仅当该 EO 的文件全部为冻结状态时才标注冻结。
    """
    usage_eo_multi_map = {}
    frozen_eo_map = {}
    for pn_norm, original_pns in pn_mapping.items():
        eo_set = set()
        eo_frozen = {}   # eo -> True=仅见冻结文件 / False=存在活跃文件
        for row in file_info_rows:
            try:
                filename, eo, is_frozen = row[0], row[1], row[2]
                if not filename:
                    continue
                # 文件名格式：车种_品番_EO_UsageList.xls，品番在倒数第三个位置
                parts = filename.split('_')
                if len(parts) >= 3:
                    potential_pn = parts[-3]
                    if _normalize_part_number(potential_pn) == pn_norm and eo and str(eo).strip():
                        eo_val = str(eo).strip()
                        eo_set.add(eo_val)
                        if not is_frozen:
                            eo_frozen[eo_val] = False      # 出现活跃文件即不可再标冻结
                        else:
                            eo_frozen.setdefault(eo_val, True)
            except Exception:
                continue
        if eo_set:
            eo_list = sorted(eo_set)
            frozen_list = sorted([e for e, fz in eo_frozen.items() if fz])
            for original_pn in original_pns:
                usage_eo_multi_map[original_pn] = eo_list
                if frozen_list:
                    frozen_eo_map[original_pn] = frozen_list
    return usage_eo_multi_map, frozen_eo_map


def _query_missing_eos(part_numbers):
    """查询缺失的品番EO信息，返回 (品番->EO列表, 品番->冻结EO列表)"""
    try:
        # 标准化品番
        normalized_pns = set()
        pn_mapping = {}  # 标准化品番 -> 原始品番列表

        for pn_raw in part_numbers:
            pn_norm = _normalize_part_number(pn_raw)
            normalized_pns.add(pn_norm)
            if pn_norm not in pn_mapping:
                pn_mapping[pn_norm] = []
            pn_mapping[pn_norm].append(pn_raw)

        # 使用缓存的 file_info 数据
        file_info_rows = _get_cached_file_info()

        return _build_eo_maps(file_info_rows, pn_mapping)

    except Exception as e:
        current_app.logger.error(f"查询缺失EO失败: {str(e)}")
        return {}, {}

@main_bp.route('/api/finished_products/clear_reference_eo_cache', methods=['POST'])
@csrf_exempt_route
def clear_reference_eo_cache_api():
    """清除参考EO缓存 - 调试用"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        clear_reference_eo_cache()
        return jsonify({'success': True, 'message': '缓存已清除'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/finished_products/reference_eos', methods=['POST'])
@csrf_exempt_route
def get_reference_eos():
    """批量获取参考EO信息 - 使用file_info表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.file_info import FileInfo
        import time
        
        data = request.get_json()
        part_numbers = data.get('part_numbers', [])
        
        # current_app.logger.info(f'[ReferenceEO] 收到请求，品番列表: {part_numbers}')
        
        if not part_numbers:
            # current_app.logger.info('[ReferenceEO] 品番列表为空，返回空结果')
            return jsonify({'success': True, 'data': {}})
        
        eo_query_start = time.time()
        
        # 检查缓存
        current_time = time.time()
        global _cache_timestamp
        
        # 性能监控
        cache_check_start = time.time()
        
        # current_app.logger.info(f'[ReferenceEO] 缓存状态: 时间戳={_cache_timestamp}, 缓存大小={len(_reference_eo_cache)}')
        
        if current_time - _cache_timestamp < CACHE_DURATION and len(_reference_eo_cache) > 0:
            # 从缓存中获取数据（缓存结构：{pn_norm: {'eos': [...], 'frozen': [...]}}）
            usage_eo_multi_map = {}
            frozen_eo_map = {}
            missing_pns = []  # 缓存中缺失的品番

            for pn_raw in part_numbers:
                pn_norm = _normalize_part_number(pn_raw)
                if pn_norm in _reference_eo_cache:
                    cached = _reference_eo_cache[pn_norm]
                    usage_eo_multi_map[pn_raw] = cached.get('eos', [])
                    if cached.get('frozen'):
                        frozen_eo_map[pn_raw] = cached['frozen']
                else:
                    missing_pns.append(pn_raw)

            # 如果有缺失的品番，需要查询数据库
            if missing_pns:
                # 查询缺失的品番，返回 (EO映射, 冻结EO映射)
                missing_eo_map, missing_frozen_map = _query_missing_eos(missing_pns)
                usage_eo_multi_map.update(missing_eo_map)
                frozen_eo_map.update(missing_frozen_map)

                # 更新缓存
                for pn_raw in missing_pns:
                    pn_norm = _normalize_part_number(pn_raw)
                    if pn_raw in missing_eo_map:
                        _reference_eo_cache[pn_norm] = {
                            'eos': missing_eo_map[pn_raw],
                            'frozen': missing_frozen_map.get(pn_raw, [])
                        }
            else:
                pass

            return jsonify({
                'success': True,
                'data': usage_eo_multi_map,
                'frozen': frozen_eo_map
            })
        
        # current_app.logger.info('[ReferenceEO] 缓存未命中，执行数据库查询')
        
        # 缓存未命中，执行数据库查询
        # 标准化品番
        normalized_pns = set()
        pn_mapping = {}  # 标准化品番 -> 原始品番列表
        
        for pn_raw in part_numbers:
            pn_norm = _normalize_part_number(pn_raw)
            normalized_pns.add(pn_norm)
            if pn_norm not in pn_mapping:
                pn_mapping[pn_norm] = []
            pn_mapping[pn_norm].append(pn_raw)
        
        # 使用缓存的 file_info 数据
        file_info_rows = _get_cached_file_info()
        eo_query_end = time.time()
        
        file_info_query_time = eo_query_end - eo_query_start
        # current_app.logger.info(f'[ReferenceEO] file_info查询完成，查询{len(part_numbers)}个品番，找到{len(file_info_rows)}条记录，file_info查询耗时: {file_info_query_time:.3f}秒')
        
        # 构建品番到EO的映射（含冻结EO标注）
        usage_eo_multi_map, frozen_eo_map = _build_eo_maps(file_info_rows, pn_mapping)

        # 更新缓存（结构：{pn_norm: {'eos': [...], 'frozen': [...]}}）
        for pn_norm, original_pns in pn_mapping.items():
            # 该标准化品番任一原始品番有数据即缓存（原始品番共享同一结果）
            sample_pn = original_pns[0]
            if sample_pn in usage_eo_multi_map:
                _reference_eo_cache[pn_norm] = {
                    'eos': usage_eo_multi_map[sample_pn],
                    'frozen': frozen_eo_map.get(sample_pn, [])
                }

        # 计算总耗时
        total_time = time.time() - eo_query_start
        processing_time = total_time - file_info_query_time

        # 更新缓存时间戳
        _cache_timestamp = current_time

        return jsonify({
            'success': True,
            'data': usage_eo_multi_map,
            'frozen': frozen_eo_map
        })
        
    except Exception as e:
        current_app.logger.error(f"获取参考EO失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# EO变更核对：异步后台计算 + 结果缓存 + 轮询
# 页面切换时不再同步等待全表联表比对，避免阻塞页面响应
# ============================================================
_EO_CHECK_CACHE = {
    'result': None,      # 最近一次成功的核对结果
    'timestamp': 0.0,    # 结果生成时间戳
    'running': False,    # 是否有后台任务正在计算
    'error': None,       # 最近一次失败原因
    'fingerprint': None, # 结果对应的数据指纹（成品/LOT 表行数+最大时间），用于判断缓存是否过期
}
_EO_CHECK_TTL = 300      # 结果缓存有效期（秒）
_EO_CHECK_LOCK = threading.Lock()


def _invalidate_eo_check():
    """成品 / LOT 登录数据变更后立即失效 EO 变更核对缓存，避免继续展示陈旧结果。

    仅清结果并置时间戳为 0；正在进行的后台任务由 _start_eo_change_check 复用，
    不会重复投递，保证下次查询必然基于最新数据重算。
    """
    with _EO_CHECK_LOCK:
        _EO_CHECK_CACHE['result'] = None
        _EO_CHECK_CACHE['timestamp'] = 0.0
        _EO_CHECK_CACHE['error'] = None


def _eo_changed(eo1, eo2):
    """两侧EO是否不一致：优先按EO版本语义比较，无法解析时按去空格大写比较。"""
    from utils.eo_utils import parse_eo
    s1, s2 = (eo1 or '').strip(), (eo2 or '').strip()
    if not s1 or not s2:
        return False  # 任一侧未登记EO时无法判定变更，避免误报
    p1, p2 = parse_eo(s1), parse_eo(s2)
    if p1 is not None and p2 is not None:
        return p1 != p2
    return s1.upper() != s2.upper()


def _eo_check_fingerprint():
    """计算 EO 核对所依赖数据（成品表/LOT 登录表）的轻量指纹。

    行数变化（增/删/清空）与最大时间变化（修改）都会改变指纹，
    用于判断缓存结果是否已过期，无需在每个写入口手工失效。
    """
    from models.finished_product import FinishedProduct
    from models.lot_login_info import LotLoginInfo
    from sqlalchemy import func as _func
    fp_count, fp_max = db.session.query(
        _func.count(FinishedProduct.id),
        _func.max(FinishedProduct.update_time),
    ).one()
    lot_count, lot_max = db.session.query(
        _func.count(LotLoginInfo.id),
        _func.max(LotLoginInfo.created_at),
    ).one()
    return (fp_count, str(fp_max or ''), lot_count, str(lot_max or ''))


def _eo_check_cache_fresh(fingerprint):
    """缓存有效条件：结果存在、TTL 未过、且数据指纹未变。"""
    if _EO_CHECK_CACHE['result'] is None:
        return False
    if (time.time() - _EO_CHECK_CACHE['timestamp']) >= _EO_CHECK_TTL:
        return False
    if _EO_CHECK_CACHE.get('fingerprint') != fingerprint:
        return False
    return True


def _eo_check_cache_usable(fingerprint):
    """缓存可用条件（忽略 TTL）：结果存在且数据指纹未变。

    用于手动强制核对场景——指纹未变说明数据没动，即便 TTL 已过也可直接复用，
    避免重复触发重算导致前端长时间 pending。
    """
    if _EO_CHECK_CACHE['result'] is None:
        return False
    if fingerprint is None:
        return False
    return _EO_CHECK_CACHE.get('fingerprint') == fingerprint


def _compute_eo_change_result(app):
    """后台线程中执行EO变更核对，结果写入缓存。"""
    try:
        with app.app_context():
            from models.finished_product import FinishedProduct
            from models.lot_login_info import LotLoginInfo

            fingerprint = _eo_check_fingerprint()
            rows = (
                db.session.query(
                    FinishedProduct.vehicle_name,
                    FinishedProduct.part_number,
                    FinishedProduct.lot_number,
                    FinishedProduct.eo,
                    LotLoginInfo.jx_eo,
                    LotLoginInfo.vehicle_type,
                )
                .join(LotLoginInfo, LotLoginInfo.lot_no == FinishedProduct.lot_number)
                .all()
            )

            items = []
            vehicle_count = {}
            for vehicle_name, part_number, lot_number, fp_eo, lot_eo, lot_vehicle_type in rows:
                if not _eo_changed(fp_eo, lot_eo):
                    continue
                vehicle_name = vehicle_name or '未填写'
                items.append({
                    'vehicle_name': vehicle_name,
                    'part_number': part_number or '',
                    'lot_number': lot_number or '',
                    'product_eo': (fp_eo or '').strip(),
                    'lot_eo': (lot_eo or '').strip(),
                    'lot_vehicle_type': lot_vehicle_type or '',
                })
                vehicle_count[vehicle_name] = vehicle_count.get(vehicle_name, 0) + 1

            items.sort(key=lambda x: (x['vehicle_name'], x['part_number'], x['lot_number']))
            vehicles = [
                {'vehicle': v, 'count': c}
                for v, c in sorted(vehicle_count.items(), key=lambda kv: (-kv[1], kv[0]))
            ]
            result = {
                'success': True,
                'total': len(items),
                'vehicles': vehicles,
                'items': items,
            }
            with _EO_CHECK_LOCK:
                _EO_CHECK_CACHE['result'] = result
                _EO_CHECK_CACHE['timestamp'] = time.time()
                _EO_CHECK_CACHE['error'] = None
                _EO_CHECK_CACHE['fingerprint'] = fingerprint
    except Exception as e:
        app.logger.error(f"EO变更核对后台任务失败: {str(e)}")
        with _EO_CHECK_LOCK:
            _EO_CHECK_CACHE['error'] = str(e)
    finally:
        with _EO_CHECK_LOCK:
            _EO_CHECK_CACHE['running'] = False


def _start_eo_change_check(app, force=False):
    """启动后台核对任务。force=True 时忽略缓存强制重算；
    数据指纹变化（成品/LOT 增删改）时自动视为缓存过期并重算。"""
    try:
        fingerprint = _eo_check_fingerprint()
    except Exception:
        fingerprint = None
    with _EO_CHECK_LOCK:
        # 数据未变（指纹一致）时一律复用已有结果，即便 force 也无需重算；
        # 只有数据真的变了才重算，避免手动点击反复触发后台任务。
        if fingerprint is not None and _eo_check_cache_usable(fingerprint):
            return False
        if _EO_CHECK_CACHE['running']:
            return False  # 已有任务在跑，复用
        _EO_CHECK_CACHE['running'] = True
    threading.Thread(target=_compute_eo_change_result, args=(app,), daemon=True).start()
    return True


@main_bp.route('/api/finished_products/eo_change_check', methods=['GET'])
def eo_change_check():
    """EO变更核对（异步）：立即返回缓存结果；无有效缓存时投递后台任务并返回 pending，
    前端轮询该接口直到 status 变为 done，从而不阻塞页面切换。"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    # force=1 表示手动点击核对，忽略缓存强制重算
    force = request.args.get('force') == '1'
    _start_eo_change_check(current_app._get_current_object(), force=force)

    try:
        fingerprint = _eo_check_fingerprint()
    except Exception:
        fingerprint = None
    with _EO_CHECK_LOCK:
        result = _EO_CHECK_CACHE['result']
        # force 时只要数据指纹未变，也直接复用缓存结果（秒回），只有数据变了才回 pending 等待重算
        fresh = (fingerprint is not None and _eo_check_cache_fresh(fingerprint)) or \
                (fingerprint is not None and _eo_check_cache_usable(fingerprint))
        running = _EO_CHECK_CACHE['running']
        error = _EO_CHECK_CACHE['error']

    if fresh:
        payload = dict(result)
        payload['status'] = 'done'
        payload['cached'] = True
        return jsonify(payload)

    if running:
        return jsonify({'success': True, 'status': 'pending'})

    if error:
        return jsonify({'success': False, 'status': 'error', 'error': error}), 500

    return jsonify({'success': True, 'status': 'pending'})


@main_bp.route('/api/finished_products/<int:product_id>', methods=['GET'])
@csrf_exempt_route
def get_finished_product(product_id):
    """获取单个成品信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        product = db.session.query(FinishedProductModel).get(product_id)
        if not product:
            return jsonify({'success': False, 'error': '成品不存在'}), 404
        
        return jsonify({
            'success': True,
            'product': product.to_dict()
        })
        
    except Exception as e:
        current_app.logger.error(f"获取成品信息失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/<int:product_id>', methods=['PUT'])
@csrf_exempt_route
def update_finished_product(product_id):
    """更新成品信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        vehicle_name = data.get('vehicle_name', '').strip()
        part_number = data.get('part_number', '').strip()
        lot_number = data.get('lot_number', '').strip()
        eo = data.get('eo', '').strip()
        quantity = data.get('quantity', 0)
        remark = data.get('remark', '').strip()
        
        # 验证必填字段
        if not all([vehicle_name, part_number, lot_number]):
            return jsonify({'success': False, 'error': '车种、品番、LOT号不能为空'}), 400
        
        # 验证数量
        try:
            quantity = int(quantity)
            if quantity < 0:
                return jsonify({'success': False, 'error': '数量必须大于等于0'}), 400
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': '数量必须是有效的数字'}), 400
        
        # 查找成品
        product = db.session.query(FinishedProductModel).get(product_id)
        if not product:
            return jsonify({'success': False, 'error': '成品不存在'}), 404
        
        # 记录修改前快照
        def _safe_get(obj, attr):
            try:
                return getattr(obj, attr)
            except Exception:
                return None
        before_snapshot = {
            'vehicle_name': _safe_get(product, 'vehicle_name'),
            'part_number': _safe_get(product, 'part_number'),
            'lot_number': _safe_get(product, 'lot_number'),
            'eo': _safe_get(product, 'eo') if hasattr(product, 'eo') else None,
            'quantity': _safe_get(product, 'quantity'),
            'remark': _safe_get(product, 'remark'),
            'eo_override': _safe_get(product, 'eo_override') if hasattr(product, 'eo_override') else None,
            'usage_eo': _safe_get(product, 'usage_eo') if hasattr(product, 'usage_eo') else None,
            'needs_eo_attention': _safe_get(product, 'needs_eo_attention') if hasattr(product, 'needs_eo_attention') else None,
        }
        
        # 检查LOT号是否已被其他成品使用
        if lot_number != product.lot_number:
            existing_lot = db.session.query(FinishedProductModel).filter_by(lot_number=lot_number).first()
            if existing_lot:
                return jsonify({'success': False, 'error': f'LOT号 {lot_number} 已被其他成品使用'}), 400
        
        # 获取 UsageList 中的 EO 集合，并按集合逻辑重算状态
        from models.usage_list import UsageList
        from sqlalchemy import func
        try:
            pn_raw = (part_number or '').strip()
            # 规范化品番：去空格、转大写、统一连字符
            pn_norm = pn_raw.upper().replace(' ', '').replace('－', '-').replace('–', '-').replace('—', '-')
            # 在数据库侧也做相同规范化以对齐
            subpno_norm = func.replace(func.replace(func.replace(func.replace(func.upper(UsageList.SubPno), ' ', ''), '－', '-'), '–', '-'), '—', '-')
            deleted_col = getattr(UsageList, 'deleted', None)
            frozen_col = getattr(UsageList, 'is_frozen', None)
            filters = [subpno_norm == pn_norm]
            if deleted_col is not None:
                filters.append(deleted_col == False)
            if frozen_col is not None:
                filters.append(frozen_col == False)
            usage_items = UsageList.query.filter(*filters).all()
        except Exception:
            usage_items = []
        usage_eo_set_norm = set()
        usage_eo_originals = set()
        for item in usage_items:
            try:
                raw = (item.EoKsc or '').strip()
                if raw:
                    usage_eo_originals.add(raw)
                    usage_eo_set_norm.add(raw.upper())
            except Exception:
                continue
        # 代表性的 usage_eo（用于存表展示）；如有多个，取排序后的第一个（保留原始大小写）
        usage_eo = sorted(list(usage_eo_originals))[0] if usage_eo_originals else None
        eo_override = False
        needs_eo_attention = False
        eo_norm = (eo or '').strip().upper()
        if eo and usage_eo_set_norm:
            if eo_norm not in usage_eo_set_norm:
                eo_override = True
        elif not eo and usage_eo_set_norm:
            needs_eo_attention = True
            eo = None
        elif not usage_eo_set_norm:
            # UsageList 中不存在该品番
            needs_eo_attention = True
        
        # 更新信息
        product.vehicle_name = vehicle_name
        product.part_number = part_number
        product.lot_number = lot_number
        if hasattr(product, 'eo'):
            product.eo = eo
        product.quantity = quantity
        product.remark = remark
        if hasattr(product, 'eo_override'):
            product.eo_override = eo_override
        if hasattr(product, 'usage_eo'):
            product.usage_eo = usage_eo
        if hasattr(product, 'needs_eo_attention'):
            product.needs_eo_attention = needs_eo_attention
        # 记录修改者信息
        current_user_id = session.get('user_id')
        if hasattr(product, 'updated_by'):
            product.updated_by = current_user_id
        product.update_time = datetime.now()
        

        
        db.session.commit()
        # 操作记录：更新成品（带字段明细）
        try:
            # 仅记录发生变化的字段：旧值 -> 新值
            after_snapshot = {
                'vehicle_name': _safe_get(product, 'vehicle_name'),
                'part_number': _safe_get(product, 'part_number'),
                'lot_number': _safe_get(product, 'lot_number'),
                'eo': _safe_get(product, 'eo') if hasattr(product, 'eo') else None,
                'quantity': _safe_get(product, 'quantity'),
                'remark': _safe_get(product, 'remark'),
                'eo_override': _safe_get(product, 'eo_override') if hasattr(product, 'eo_override') else None,
                'usage_eo': _safe_get(product, 'usage_eo') if hasattr(product, 'usage_eo') else None,
                'needs_eo_attention': _safe_get(product, 'needs_eo_attention') if hasattr(product, 'needs_eo_attention') else None,
            }

            def _fmt(v):
                if v is None:
                    return ''
                return str(v)

            changed_items = []
            for k, before_val in before_snapshot.items():
                after_val = after_snapshot.get(k)
                if before_val != after_val:
                    changed_items.append(f"{k}:{_log_sanitize(_fmt(before_val))}->{_log_sanitize(_fmt(after_val))}")

            # 若没有字段变化，则也保留一条简要记录
            if not changed_items:
                changed_items.append("no_field_changed")

            # 主体标识 + 变化详情（用 || 连接）
            header = "|".join([
                _log_sanitize(after_snapshot.get('vehicle_name') or ''),
                _log_sanitize(after_snapshot.get('part_number') or ''),
                _log_sanitize(after_snapshot.get('lot_number') or ''),
                _log_sanitize(after_snapshot.get('eo') or ''),
                _log_sanitize(after_snapshot.get('quantity') or ''),
                _log_sanitize(after_snapshot.get('remark') or ''),
            ]).strip('|')
            detail = header + ("||" + "||".join(changed_items) if changed_items else '')
            log_operation(
                operation_type=OperationType.UPDATE_PRODUCT,
                operation_desc=f"更新成品: {product.vehicle_name}-{product.part_number}",
                target_type=TargetType.PRODUCT,
                target_id=product_id,
                target_name=product.part_number,
                file_path=detail
            )
        except Exception:
            current_app.logger.warning('记录更新成品操作日志失败')
        
        return jsonify({
            'success': True,
            'message': '更新成功',
            'data': product.to_dict()
        })
        
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"更新成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
@main_bp.route('/api/finished_products/<int:product_id>', methods=['DELETE'])
@csrf_exempt_route
def delete_finished_product(product_id):
    """删除成品"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        product = db.session.query(FinishedProductModel).get(product_id)
        if not product:
            return jsonify({'success': False, 'error': '成品不存在'}), 404
        
        db.session.delete(product)
        db.session.commit()
        # 操作记录：删除成品（带字段明细）
        try:
            eo_val = getattr(product, 'eo', None)
            detail = f"{_log_sanitize(product.vehicle_name)}|{_log_sanitize(product.part_number)}|{_log_sanitize(product.lot_number)}|{_log_sanitize(eo_val or '')}|{_log_sanitize(product.quantity)}|{_log_sanitize(product.remark or '')}"
            log_operation(
                operation_type=OperationType.DELETE_PRODUCT,
                operation_desc=f"删除成品: {product.vehicle_name}-{product.part_number}",
                target_type=TargetType.PRODUCT,
                target_id=product_id,
                target_name=product.part_number,
                file_path=detail
            )
        except Exception:
            current_app.logger.warning('记录删除成品操作日志失败')
        
        return jsonify({
            'success': True,
            'message': '删除成功'
        })
        
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"删除成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/batch_delete', methods=['POST'])
@csrf_exempt_route
def batch_delete_finished_products():
    """批量删除成品"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：成品批量删除
    user_id = session['user_id']
    if not check_permission(user_id, 'can_products_batch_delete'):
        return jsonify({'success': False, 'error': '没有批量删除成品的权限'}), 403
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        product_ids = data.get('product_ids', [])
        
        if not product_ids:
            return jsonify({'success': False, 'error': '请选择要删除的成品'}), 400
        
        # 查找要删除的成品
        products_to_delete = db.session.query(FinishedProductModel).filter(FinishedProductModel.id.in_(product_ids)).all()
        
        if not products_to_delete:
            return jsonify({'success': False, 'error': '未找到要删除的成品'}), 404
        
        # 删除成品
        for product in products_to_delete:
            db.session.delete(product)
        
        db.session.commit()
        # 操作记录：批量删除成品（写入明细，含字段）
        try:
            detail_lines = []
            for prod in products_to_delete:
                try:
                    eo_val = getattr(prod, 'eo', None)
                    detail_lines.append(f"{_log_sanitize(prod.vehicle_name)}|{_log_sanitize(prod.part_number)}|{_log_sanitize(prod.lot_number)}|{_log_sanitize(eo_val or '')}|{_log_sanitize(prod.quantity)}|{_log_sanitize(prod.remark or '')}")
                except Exception:
                    continue
            # 清理：去除尾随分隔符与空行
            detail_lines = [dl.strip('|') for dl in detail_lines if dl and dl.strip('|')]
            file_path = '||'.join(detail_lines[:2000]) if detail_lines else None
            log_operation(
                operation_type=OperationType.BATCH_DELETE_PRODUCT,
                operation_desc=f"批量删除成品: {len(products_to_delete)} 条",
                target_type=TargetType.PRODUCT,
                file_path=file_path
            )
        except Exception:
            current_app.logger.warning('记录批量删除成品操作日志失败')
        
        return jsonify({
            'success': True,
            'message': f'成功删除 {len(products_to_delete)} 条成品数据'
        })
        
    except Exception as e:

        db.session.rollback()
        current_app.logger.error(f"批量删除成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
@main_bp.route('/api/finished_products/upload_excel', methods=['POST'])
@main_bp.route('/api/finished_products/import_excel', methods=['POST'])  # 添加别名路由
@csrf_exempt_route
def upload_finished_products_excel():
    """Excel导入成品数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': '请选择文件'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': '请选择文件'}), 400
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return jsonify({'success': False, 'error': '请选择Excel文件'}), 400
        
        # 读取Excel文件 - 优化：指定数据类型和只读取需要的列
        try:
            # 一次性读取所需列（含可选的备注列），避免为取备注列而二次全量解析整个文件
            usecols = ['车种', '品番', 'LOT', 'EO', '数量', '备注']
            try:
                df = pd.read_excel(file, usecols=usecols, dtype=str)
            except Exception:
                # 备注列可能不存在：退化为仅读必需列，再补空备注
                df = pd.read_excel(file, usecols=['车种', '品番', 'LOT', 'EO', '数量'], dtype=str)
                df['备注'] = ''
        except Exception as e:
            # 如果指定列失败，回退到读取所有列
            df = pd.read_excel(file)
            # 确保备注列存在
            if '备注' not in df.columns:
                df['备注'] = ''
        
        # 验证列名
        required_columns = ['车种', '品番', 'LOT', 'EO', '数量', '备注']
        if not all(col in df.columns for col in required_columns[:4]):  # 备注可选
            return jsonify({'success': False, 'error': 'Excel文件格式错误，需要包含：车种、品番、LOT、EO、数量列（车种可以为空）'}), 400
        
        # 优化：使用向量化操作处理数据，避免逐行循环
        current_app.logger.info(f"开始处理Excel数据，共 {len(df)} 行")
        
        # 清理数据：去除空值并转换为字符串
        df = df.fillna('').astype(str)
        
        # 过滤有效行：品番和LOT都不为空
        valid_mask = (df['品番'].str.strip() != '') & (df['LOT'].str.strip() != '')
        df_valid = df[valid_mask].copy()
        
        if len(df_valid) == 0:
            return jsonify({'success': False, 'error': 'Excel文件中没有有效数据'}), 400
        
        current_app.logger.info(f"过滤后有效数据 {len(df_valid)} 行")
        
        # 向量化处理：批量处理所有数据
        df_valid['vehicle_name'] = df_valid['车种'].str.strip()
        df_valid['part_number'] = df_valid['品番'].str.strip()
        df_valid['lot_number'] = df_valid['LOT'].str.strip()
        df_valid['eo'] = df_valid['EO'].str.strip()
        df_valid['remark'] = df_valid['备注'].str.strip()
        
        # 验证数量字段
        try:
            df_valid['quantity'] = pd.to_numeric(df_valid['数量'], errors='coerce')
            # 过滤掉无效数量（负数或无法转换的）
            df_valid = df_valid[df_valid['quantity'].notna() & (df_valid['quantity'] >= 0)]
            df_valid['quantity'] = df_valid['quantity'].astype(int)
        except Exception as e:
            current_app.logger.warning(f"数量字段处理失败: {str(e)}")
            return jsonify({'success': False, 'error': '数量字段格式错误，请确保为有效数字'}), 400
        
        if len(df_valid) == 0:
            return jsonify({'success': False, 'error': 'Excel文件中没有有效的数量数据'}), 400
        
        current_app.logger.info(f"数量验证后有效数据 {len(df_valid)} 行")
        
        # 转换为列表格式（保持兼容性）
        # 用 to_dict('records') + index 对齐替代 iterrows，避免逐行构造 Series
        excel_data_list = []
        _valid_rows = df_valid[['vehicle_name', 'part_number', 'lot_number', 'eo', 'quantity', 'remark']].to_dict('records')
        for _idx, row in zip(df_valid.index, _valid_rows):
            excel_data_list.append({
                'index': _idx + 2,  # Excel行号从2开始
                'vehicle_name': row['vehicle_name'],
                'part_number': row['part_number'],
                'lot_number': row['lot_number'],
                'eo': row['eo'],
                'quantity': int(row['quantity']),
                'remark': row['remark'],
                'has_vehicle_name': bool(row['vehicle_name'])
            })
        
        if not excel_data_list:
            return jsonify({'success': False, 'error': 'Excel文件中没有有效数据'}), 400
        
        # 优化：使用pandas向量化操作检查LOT号重复
        current_app.logger.info("开始检查LOT号重复...")
        
        # 使用pandas的duplicated方法快速找到重复的LOT号
        lot_duplicates = df_valid[df_valid['lot_number'].duplicated(keep=False)]
        
        excel_duplicates = []
        excel_duplicates_with_part_conflicts = []  # 兼容保留，实际不再使用
        
        if len(lot_duplicates) > 0:
            # 按LOT号分组处理重复数据
            for lot_number, group in lot_duplicates.groupby('lot_number'):
                duplicate_rows = []
                # to_dict('records') + 索引对齐替代 iterrows（保留原 Excel 行号 row.name）
                _grp_rows = group[['vehicle_name', 'part_number', 'lot_number', 'eo', 'quantity', 'remark']].to_dict('records')
                for _gidx, row in zip(group.index, _grp_rows):
                    duplicate_rows.append({
                        'index': _gidx + 2,  # Excel行号从2开始
                        'vehicle_name': row['vehicle_name'],
                        'part_number': row['part_number'],
                        'lot_number': row['lot_number'],
                        'eo': row['eo'],
                        'quantity': int(row['quantity']),
                        'remark': row['remark'],
                        'has_vehicle_name': bool(row['vehicle_name'])
                    })
                    
                    # 分析重复行的差异
                    vehicle_names = [row['vehicle_name'] for row in duplicate_rows if row['vehicle_name']]
                    unique_vehicle_names = list(set(vehicle_names))
                    has_empty_vehicle = any(not row['vehicle_name'] for row in duplicate_rows)
                    
                # 生成重复信息
                if len(unique_vehicle_names) > 1 or has_empty_vehicle:
                    if has_empty_vehicle and len(unique_vehicle_names) > 0:
                        message = f"LOT号 {lot_number} 重复出现 {len(duplicate_rows)} 次，包含车种数据差异：{', '.join(unique_vehicle_names)} 和空车种"
                    elif len(unique_vehicle_names) > 1:
                        message = f"LOT号 {lot_number} 重复出现 {len(duplicate_rows)} 次，车种数据不同：{', '.join(unique_vehicle_names)}"
                    else:
                        message = f"LOT号 {lot_number} 重复出现 {len(duplicate_rows)} 次，部分行缺少车种数据"
                else:
                    message = f"LOT号 {lot_number} 重复出现 {len(duplicate_rows)} 次"
                    
                excel_duplicates.append({
                            'lot_number': lot_number,
                            'rows': duplicate_rows,
                            'message': message,
                            'vehicle_names': unique_vehicle_names,
                            'has_empty_vehicle': has_empty_vehicle,
                            'duplicate_type': 'lot_with_vehicle_diff' if (len(unique_vehicle_names) > 1 or has_empty_vehicle) else 'lot_only',
                            'has_part_conflicts': False
                        })
        
        current_app.logger.info(f"LOT重复检查完成，发现 {len(excel_duplicates)} 个重复LOT号")
        
        # 覆盖导入：不检查数据库是否已存在
        db_conflicts = []
        
        # 统一处理所有冲突情况：Excel重复、EO冲突、数据库冲突（不再校验品番是否存在）
        part_number_conflicts = []  # 保持字段名以兼容前端，但只承载EO相关冲突
        valid_data = []
        
        # 性能优化：一次性预取 UsageList 所需字段并构建字典索引，避免 N 次数据库查询
        # 注意：必须与成品计算/展示口径一致（品番级活跃优先，全冻结时冻结兜底，
        # 排除已删除行），否则会出现“提醒的EO实际不存在”的假冲突。
        try:
            usagelist_map_multi = build_usage_eo_map(
                [str(p) for p in df_valid['part_number'].dropna().unique().tolist()]
            )
        except Exception:
            usagelist_map_multi = {}
        
        # 优化：使用pandas向量化操作进行冲突检查
        current_app.logger.info("开始检查品番和EO冲突...")
        
        # 收集重复的LOT号
        duplicate_lots = set()
        if excel_duplicates:
            duplicate_lots.update(dup['lot_number'] for dup in excel_duplicates)
        if excel_duplicates_with_part_conflicts:
            duplicate_lots.update(dup['lot_number'] for dup in excel_duplicates_with_part_conflicts)
        
        # 过滤掉重复的LOT号，只处理非重复数据
        df_non_duplicate = df_valid[~df_valid['lot_number'].isin(duplicate_lots)].copy()
        
        if len(df_non_duplicate) > 0:
            # 检查品番是否存在于UsageList（基于multi map）
            df_non_duplicate['part_exists'] = df_non_duplicate['part_number'].isin(usagelist_map_multi.keys())
            
            # 分离存在和不存在的品番
            df_part_not_exists = df_non_duplicate[~df_non_duplicate['part_exists']]
            df_part_exists = df_non_duplicate[df_non_duplicate['part_exists']]
            
            # 处理品番不存在的冲突
            for _idx, row in zip(df_part_not_exists.index, df_part_not_exists.to_dict('records')):
                part_number_conflicts.append({
                    'row': _idx + 2,  # Excel行号从2开始
                    'part_number': row['part_number'],
                    'vehicle_name': row['vehicle_name'],
                    'lot_number': row['lot_number'],
                    'eo': row['eo'],
                    'quantity': int(row['quantity']),
                    'remark': row['remark'],
                    'conflict_type': 'part_not_exists',
                    'message': f"品番 {row['part_number']} 在UsageList中不存在"
                })
            
            # 处理EO冲突
            if len(df_part_exists) > 0:
                # 获取UsageList中的EO信息（集合）
                df_part_exists['usage_eo_set'] = df_part_exists['part_number'].map(lambda p: usagelist_map_multi.get(p, set()))
                # 方便展示：挑一个代表值（按 EO 版本规则取最新，而非字符串序）
                from utils.eo_utils import get_sorted_eos as _get_sorted_eos
                df_part_exists['usage_eo'] = df_part_exists['usage_eo_set'].map(
                    lambda s: (_get_sorted_eos(sorted(s))[-1] if s else None)
                )
                
                # 检查EO冲突（多EO支持：提供的EO必须在集合中）
                def _eo_in_set(row):
                    eo = (row.get('eo') or '').strip()
                    s = row.get('usage_eo_set') or set()
                    return eo in s if eo and s else False
                eo_mismatch_mask = (df_part_exists['eo'] != '') & (df_part_exists['usage_eo_set'].map(lambda s: bool(s))) & (~df_part_exists.apply(_eo_in_set, axis=1))
                eo_missing_mask = (df_part_exists['eo'] == '') & (df_part_exists['usage_eo_set'].map(lambda s: bool(s)))
                
                # 处理EO不匹配的冲突
                for _idx, row in zip(df_part_exists[eo_mismatch_mask].index,
                                     df_part_exists[eo_mismatch_mask].to_dict('records')):
                    part_number_conflicts.append({
                        'row': _idx + 2,
                        'part_number': row['part_number'],
                        'vehicle_name': row['vehicle_name'],
                        'lot_number': row['lot_number'],
                        'eo': row['eo'],
                        'usage_eo': row['usage_eo'],
                        'quantity': int(row['quantity']),
                        'remark': row['remark'],
                        'conflict_type': 'eo_mismatch',
                        'message': f"品番 {row['part_number']} EO冲突：输入EO({row['eo']}) vs UsageList EO({row['usage_eo']})"
                    })
                
                # 处理EO缺失的冲突
                for _idx, row in zip(df_part_exists[eo_missing_mask].index,
                                     df_part_exists[eo_missing_mask].to_dict('records')):
                    part_number_conflicts.append({
                        'row': _idx + 2,
                        'part_number': row['part_number'],
                        'vehicle_name': row['vehicle_name'],
                        'lot_number': row['lot_number'],
                        'eo': row['eo'],
                        'usage_eo': row['usage_eo'],
                        'quantity': int(row['quantity']),
                        'remark': row['remark'],
                        'conflict_type': 'eo_missing',
                        'message': f"品番 {row['part_number']} 缺少EO：UsageList中有EO({row['usage_eo']})"
                    })
                
                # 无冲突的数据
                no_conflict_mask = ~(eo_mismatch_mask | eo_missing_mask)
                for _idx, row in zip(df_part_exists[no_conflict_mask].index,
                                     df_part_exists[no_conflict_mask].to_dict('records')):
                    valid_data.append({
                        'index': _idx + 2,
                        'vehicle_name': row['vehicle_name'],
                        'part_number': row['part_number'],
                        'lot_number': row['lot_number'],
                        'eo': row['eo'],
                        'quantity': int(row['quantity']),
                        'remark': row['remark'],
                        'has_vehicle_name': bool(row['vehicle_name'])
                    })
        
        current_app.logger.info(f"冲突检查完成：有效数据 {len(valid_data)} 条，品番冲突 {len(part_number_conflicts)} 条")
        
        # 如果有任何类型的冲突，需要用户选择
        if part_number_conflicts or excel_duplicates or excel_duplicates_with_part_conflicts:
            # 将Excel数据保存到session中，供冲突解决时使用
            session['excel_upload_data'] = {
                'excel_data_list': excel_data_list,
                'part_number_conflicts': part_number_conflicts,
                'excel_duplicates': excel_duplicates,
                'excel_duplicates_with_part_conflicts': excel_duplicates_with_part_conflicts,  # 新增
                'valid_data': valid_data,
                'db_conflicts': db_conflicts,
                'summary': {
                    'total_excel_rows': len(excel_data_list),
                    'valid_count': len(valid_data),
                    'conflict_count': len(part_number_conflicts),
                    'duplicate_count': len(excel_duplicates),
                    'duplicate_with_part_conflicts_count': len(excel_duplicates_with_part_conflicts),  # 新增
                    'db_conflict_count': len(db_conflicts),
                    'overwritten_count': 0  # 暂时设为0，在冲突解决时处理
                }
            }
            
            # 构建冲突消息
            conflict_messages = []
            if part_number_conflicts:
                # 统计EO相关的冲突
                eo_mismatch_count = len([c for c in part_number_conflicts if c['conflict_type'] == 'eo_mismatch'])
                eo_missing_count = len([c for c in part_number_conflicts if c['conflict_type'] == 'eo_missing'])
                if eo_mismatch_count > 0:
                    conflict_messages.append(f"{eo_mismatch_count} 个EO不一致")
                if eo_missing_count > 0:
                    conflict_messages.append(f"{eo_missing_count} 个缺少EO")
            if excel_duplicates:
                conflict_messages.append(f"{len(excel_duplicates)} 个Excel内部重复")
            # 覆盖导入：不提示数据库冲突
            
            conflict_message = "，".join(conflict_messages)
            
            return jsonify({
                'success': False,
                'needConflictResolution': True,
                'message': f'发现 {conflict_message}，请选择要导入的数据',
                'part_number_conflicts': part_number_conflicts,
                'excel_duplicates': excel_duplicates,
                'valid_data': valid_data,
                'db_conflicts': [],
                'summary': {
                    'total_excel_rows': len(excel_data_list),
                    'valid_count': len(valid_data),
                    'conflict_count': len(part_number_conflicts),
                    'duplicate_count': len(excel_duplicates),
                    'db_conflict_count': 0,
                    'overwritten_count': 0
                }
            })
        
        # 如果没有冲突，先清空旧数据，再导入所有有效数据（按需求：Excel导入时清空之前的数据）
        try:
            db.session.query(FinishedProductModel).delete(synchronize_session=False)
        except Exception as _:
            pass

        # 然后导入（批量构建字典，使用 bulk_insert_mappings 提升速度）
        success_count = 0
        rows_to_insert = []
        now_ts = datetime.now()
        for excel_data in valid_data:
            try:
                eo_set = usagelist_map_multi.get(excel_data['part_number'], set())
                usage_eo = None
                eo = excel_data.get('eo', '')
                if eo and eo in eo_set:
                    usage_eo = eo  # 提供的EO在集合中，采用该值
                    eo_override = False
                    needs_eo_attention = False
                    eo_value = eo
                else:
                    representative = (sorted(eo_set)[0] if eo_set else None)
                    usage_eo = representative
                    eo_override = bool(eo and representative and eo != representative)
                    needs_eo_attention = bool((not eo) and representative) or (not eo_set)
                    eo_value = None if (not eo and representative) else eo
                # 获取当前用户ID
                current_user_id = session.get('user_id')
                
                row = {
                    'vehicle_name': excel_data['vehicle_name'] or '未指定',
                    'part_number': excel_data['part_number'],
                    'lot_number': excel_data['lot_number'],
                    'quantity': excel_data['quantity'],
                    'remark': excel_data['remark'],
                    'source': 'excel',
                    'create_time': now_ts,
                    'update_time': now_ts,
                    'created_by': current_user_id,
                    'updated_by': current_user_id
                }
                if 'eo' in FinishedProductModel.__table__.columns:
                    row['eo'] = eo_value
                if 'eo_override' in FinishedProductModel.__table__.columns:
                    row['eo_override'] = eo_override
                if 'usage_eo' in FinishedProductModel.__table__.columns:
                    row['usage_eo'] = usage_eo
                if 'needs_eo_attention' in FinishedProductModel.__table__.columns:
                    row['needs_eo_attention'] = needs_eo_attention
                rows_to_insert.append(row)
            except Exception as e:
                current_app.logger.error(f"构建成品行失败: {str(e)}")
                continue
        if rows_to_insert:
            with db.session.no_autoflush:
                db.session.bulk_insert_mappings(FinishedProductModel, rows_to_insert)
            success_count = len(rows_to_insert)
        
        # 提交事务
        if success_count > 0:
            db.session.commit()
            current_app.logger.info(f"直接导入成品成功: {success_count} 条")
            
            # 记录Excel导入操作日志（带明细）
            try:
                from models.operation_log import OperationType, TargetType
                # 将导入的行转为明细（车种|品番|LOT|EO|数量|备注）
                detail_lines = []
                for d in valid_data[:1000]:
                    line = "|".join([
                        _log_sanitize(d.get('vehicle_name','')),
                        _log_sanitize(d.get('part_number','')),
                        _log_sanitize(d.get('lot_number','')),
                        _log_sanitize(d.get('eo','')),
                        _log_sanitize(d.get('quantity','')),
                        _log_sanitize(d.get('remark','')),
                    ]).strip('|')
                    if line:
                        detail_lines.append(line)
                file_path = '||'.join(detail_lines) if detail_lines else None
                log_operation(
                    operation_type=OperationType.BATCH_ADD_PRODUCT,
                    operation_desc=f"Excel导入成品: 成功 {success_count} 条",
                    target_type=TargetType.PRODUCT,
                    file_path=file_path
                )
            except Exception as e:
                current_app.logger.warning(f"记录Excel导入操作日志失败: {str(e)}")
        
        return jsonify({
            'success': True,
            'message': f'Excel导入完成，成功导入 {success_count} 条数据',
            'success_count': success_count,
            'summary': {
                'total_excel_rows': len(excel_data_list),
                'imported_count': success_count,
                'overwritten_count': 0
            }
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"Excel导入成品失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/resolve_conflicts', methods=['POST'])
@csrf_exempt_route
def resolve_finished_products_conflicts():
    """处理Excel导入的数据冲突（包括品番冲突和重复数据冲突）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        selected_data = data.get('selected_data', [])  # 用户选择要导入的数据（旧）
        selected_part_numbers = data.get('selected_part_numbers', [])  # 用户选择要导入的品番冲突数据（旧）
        combined_selected = data.get('combined_selected', [])  # 新：逐行勾选后的数据
        
        # 允许用户不选择任何重复数据，只保留已导入的无重复数据
        if selected_data is None:
            selected_data = []
        if selected_part_numbers is None:
            selected_part_numbers = []
        if combined_selected is None:
            combined_selected = []
        
        # 验证选择的数据
        valid_data = []
        for item in selected_data:
            if all(key in item for key in ['vehicle_name', 'part_number', 'lot_number', 'quantity']):
                valid_data.append(item)
        
        # 验证选择的品番冲突数据
        valid_part_number_data = []
        for item in selected_part_numbers:
            if all(key in item for key in ['vehicle_name', 'part_number', 'lot_number', 'quantity']):
                valid_part_number_data.append(item)

        # 验证新模式逐行勾选的数据
        combined_valid = []
        for item in combined_selected:
            if all(key in item for key in ['vehicle_name', 'part_number', 'lot_number', 'quantity']):
                combined_valid.append(item)
        
        # 允许用户不选择任何重复数据，只保留已导入的无重复数据
        # 当 valid_data 为空数组时，表示用户不选择任何重复数据，这是合法的
        
        # 检查是否有重复的LOT号（用户可能选择了Excel内部重复的数据）
        all_selected_lots = []
        if valid_data:
            all_selected_lots.extend([item['lot_number'] for item in valid_data])
        if valid_part_number_data:
            all_selected_lots.extend([item['lot_number'] for item in valid_part_number_data])
        if combined_valid:
            all_selected_lots.extend([item['lot_number'] for item in combined_valid])
        
        if all_selected_lots and len(all_selected_lots) != len(set(all_selected_lots)):
            return jsonify({
                'success': False, 
                'error': '选择的数据中包含重复的LOT号，请重新选择'
            }), 400
        
        # 全量替换：清空后导入（包含：会话中的非重复有效数据 + 用户在弹窗选择的重复/品番冲突数据）
        excel_session = session.get('excel_upload_data', {}) or {}
        # 优先使用前端显式传来的 base_valid_data，避免大 Cookie 被裁剪导致的会话缺失
        base_valid_data = (data.get('base_valid_data') or []) if isinstance(data, dict) else []
        if not base_valid_data:
            base_valid_data = excel_session.get('valid_data', []) or []
        # 兜底：若会话中未携带 valid_data（可能因会话丢失或前端中断），从 excel_data_list 反推计算
        if not base_valid_data:
            try:
                raw_list = excel_session.get('excel_data_list', []) or []
                conflicts = excel_session.get('part_number_conflicts', []) or []
                dup_list = excel_session.get('excel_duplicates', []) or []
                dup_list2 = excel_session.get('excel_duplicates_with_part_conflicts', []) or []
                conflict_rows = {c.get('row') for c in conflicts if c.get('row')}
                dup_rows = set()
                for grp in dup_list:
                    for r in grp.get('rows', []) or []:
                        idx = r.get('index')
                        if idx:
                            dup_rows.add(idx)
                for grp in dup_list2:
                    for r in grp.get('rows', []) or []:
                        idx = r.get('index')
                        if idx:
                            dup_rows.add(idx)
                base_valid_data = [d for d in raw_list if d.get('index') not in conflict_rows and d.get('index') not in dup_rows]
                current_app.logger.info(f"[ResolveConflicts] fallback reconstructed base_valid_data: {len(base_valid_data)} rows")
            except Exception as _e:
                current_app.logger.warning(f"[ResolveConflicts] fallback reconstruct valid_data failed: {_e}")
        updated_count = 0
        inserted_count = 0
        all_valid_data = base_valid_data + valid_data + valid_part_number_data + combined_valid
        
        if all_valid_data:
            # 清空旧数据
            try:
                db.session.query(FinishedProductModel).delete(synchronize_session=False)
            except Exception as _:
                pass
            
            # 批量处理：先准备所有数据，然后批量插入
            current_app.logger.info(f"开始批量处理 {len(all_valid_data)} 条成品数据")
            
            # 批量查询EO信息，避免N+1查询问题
            # 与上传预览/成品计算口径保持一致：品番级活跃优先，全冻结时冻结兜底，排除已删除行
            current_app.logger.info("批量查询UsageList中的EO信息...")
            part_numbers = [item['part_number'] for item in all_valid_data]
            usage_eo_multi = {}
            if part_numbers:
                usage_eo_multi = build_usage_eo_map(part_numbers)
                current_app.logger.info(f"批量查询EO信息完成，找到 {len(usage_eo_multi)} 个品番的EO信息")
            
            # 禁用自动刷新以提高性能
            with db.session.no_autoflush:
                # 准备批量插入的数据
                insert_data = []
                current_time = datetime.now()
                
                for item in all_valid_data:
                    try:
                        # 从缓存中获取EO信息，避免重复查询
                        part_number = item['part_number']
                        usage_eo_set = usage_eo_multi.get(part_number, set())
                        usage_eo = (sorted(usage_eo_set)[0] if usage_eo_set else None)
                        part_exists = bool(usage_eo_set)
                        
                        eo = item.get('eo', '')
                        eo_override = False
                        needs_eo_attention = False
                        
                        # EO逻辑（支持多EO）：若提供的EO在集合中则视为正常；否则按代表值判定
                        if eo and eo in usage_eo_set:
                            eo_override = False
                            needs_eo_attention = False
                        else:
                            if eo and usage_eo and eo != usage_eo:
                                eo_override = True
                            elif not eo and usage_eo:
                                needs_eo_attention = True
                                eo = None
                            elif not part_exists:
                                needs_eo_attention = True
                        
                        # 获取当前用户ID
                        current_user_id = session.get('user_id')
                        
                        # 构建插入数据字典
                        product_data = {
                            'vehicle_name': item['vehicle_name'],
                            'part_number': part_number,
                            'lot_number': item['lot_number'],
                            'quantity': item['quantity'],
                            'remark': item.get('remark', ''),
                            'source': 'excel',
                            'create_time': current_time,
                            'update_time': current_time,
                            'created_by': current_user_id,
                            'updated_by': current_user_id
                        }
                        
                        # 添加EO相关字段（如果模型支持）
                        if hasattr(FinishedProductModel, 'eo'):
                            product_data['eo'] = eo
                        if hasattr(FinishedProductModel, 'eo_override'):
                            product_data['eo_override'] = eo_override
                        if hasattr(FinishedProductModel, 'usage_eo'):
                            product_data['usage_eo'] = usage_eo
                        if hasattr(FinishedProductModel, 'needs_eo_attention'):
                            product_data['needs_eo_attention'] = needs_eo_attention
                        
                        insert_data.append(product_data)
                        
                    except Exception as e:
                        current_app.logger.error(f"准备成品数据失败: {str(e)}")
                        continue
                
                # 批量插入所有数据
                if insert_data:
                    try:
                        db.session.bulk_insert_mappings(FinishedProductModel, insert_data)
                        inserted_count = len(insert_data)
                        current_app.logger.info(f"批量插入成品数据成功: {inserted_count} 条")
                    except Exception as e:
                        current_app.logger.error(f"批量插入成品数据失败: {str(e)}")
                        # 回退到逐条插入
                        current_app.logger.info("回退到逐条插入模式")
                        for product_data in insert_data:
                            try:
                                new_product = FinishedProductModel(**product_data)
                                db.session.add(new_product)
                                inserted_count += 1
                            except Exception as e2:
                                current_app.logger.error(f"插入单条成品数据失败: {str(e2)}")
        
        # 获取已经导入的数据数量（这些在Excel上传时已经导入）
        already_imported_count = 0
        excel_data = session.get('excel_upload_data')
        if excel_data and 'already_imported_count' in excel_data:
            already_imported_count = excel_data['already_imported_count']
            current_app.logger.info(f"从session获取已导入数量: {already_imported_count}")
        else:
            current_app.logger.warning("session中未找到already_imported_count数据")
        
        # 提交事务
        total_count = updated_count + inserted_count
        if total_count > 0:
            db.session.commit()
            current_app.logger.info(f"冲突解决后导入成品成功: 选择数据 {total_count} 条")
        
        # 清理session中的Excel数据，避免刷新后仍然看到之前的待处理数据
        session.pop('excel_upload_data', None)
        
        # 根据用户是否选择了重复数据来显示不同的消息
        total_processed = updated_count + inserted_count
        if total_processed > 0:
            # 用户选择了重复数据
            message = f'成功处理 {already_imported_count + total_processed} 条数据（不重复数据 {already_imported_count} 条，处理重复数据 {total_processed} 条）'
            if updated_count > 0:
                message += f'，其中更新 {updated_count} 条，新增 {inserted_count} 条'
            # 记录Excel导入操作日志（冲突解决流程），带明细
            try:
                from models.operation_log import OperationType, TargetType
                detail_lines = []
                # 记录本次实际处理的数据明细（合并后）
                for d in (all_valid_data[:1000] if all_valid_data else []):
                    line = "|".join([
                        _log_sanitize(d.get('vehicle_name','')),
                        _log_sanitize(d.get('part_number','')),
                        _log_sanitize(d.get('lot_number','')),
                        _log_sanitize(d.get('eo','')),
                        _log_sanitize(d.get('quantity','')),
                        _log_sanitize(d.get('remark','')),
                    ]).strip('|')
                    if line:
                        detail_lines.append(line)
                file_path = '||'.join(detail_lines) if detail_lines else None
                log_operation(
                    operation_type=OperationType.BATCH_ADD_PRODUCT,
                    operation_desc=f"Excel导入成品(冲突解决): 成功 {total_processed} 条(更新 {updated_count} 新增 {inserted_count})",
                    target_type=TargetType.PRODUCT,
                    file_path=file_path
                )
            except Exception as _:
                current_app.logger.warning('记录Excel导入(冲突解决)操作日志失败')
        else:
            # 用户没有选择重复数据，只导入了不重复的数据
            message = f'成功导入 {already_imported_count} 条数据（不重复数据）'
        
        return jsonify({
            'success': True,
            'message': message,
            'success_count': already_imported_count + total_processed,
            'updated_count': updated_count,
            'inserted_count': inserted_count,
            'already_imported_count': already_imported_count
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"处理数据冲突失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/overwrite_import', methods=['POST'])
@csrf_exempt_route
def overwrite_finished_products_import():
    """覆盖导入Excel数据（删除冲突的数据库记录）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        overwrite = data.get('overwrite', False)
        
        if not overwrite:
            return jsonify({'success': False, 'error': '未确认覆盖操作'}), 400
        
        # 从session中获取之前上传的Excel数据
        excel_data = session.get('excel_upload_data')
        if not excel_data:
            return jsonify({'success': False, 'error': '未找到Excel数据，请重新上传'}), 400
        
        # 删除所有冲突的数据库记录
        conflict_lots = [conflict['excel_data']['lot_number'] for conflict in excel_data.get('db_conflicts', [])]
        if conflict_lots:
            deleted_count = db.session.query(FinishedProductModel).filter(
                FinishedProductModel.lot_number.in_(conflict_lots)
            ).delete(synchronize_session=False)
            current_app.logger.info(f"删除了 {deleted_count} 条冲突的数据库记录")
        
        # 导入所有Excel数据
        success_count = 0
        for excel_data_item in excel_data.get('excel_data_list', []):
            try:
                # 获取当前用户ID
                current_user_id = session.get('user_id')
                
                new_product = FinishedProductModel(
                    vehicle_name=excel_data_item['vehicle_name'],
                    part_number=excel_data_item['part_number'],
                    lot_number=excel_data_item['lot_number'],
                    quantity=excel_data_item['quantity'],
                    remark=excel_data_item.get('remark', ''),
                    source='excel',
                    create_time=datetime.now(),
                    update_time=datetime.now(),
                    created_by=current_user_id,
                    updated_by=current_user_id
                )
                
                db.session.add(new_product)
                success_count += 1
                
            except Exception as e:
                current_app.logger.error(f"创建成品失败: {str(e)}")
                continue
        
        # 提交事务
        if success_count > 0:
            db.session.commit()
            current_app.logger.info(f"覆盖导入成品成功: {success_count} 条")
            
            # 记录Excel导入操作日志（覆盖导入），带明细
            try:
                from models.operation_log import OperationType, TargetType
                # 从session中尽力获取excel_data_list用于明细
                excel_data = session.get('excel_upload_data', {}) or {}
                excel_rows = excel_data.get('excel_data_list', []) or []
                detail_lines = []
                for d in (excel_rows[:1000] if excel_rows else []):
                    line = "|".join([
                        _log_sanitize(d.get('vehicle_name','')),
                        _log_sanitize(d.get('part_number','')),
                        _log_sanitize(d.get('lot_number','')),
                        _log_sanitize(d.get('eo','')),
                        _log_sanitize(d.get('quantity','')),
                        _log_sanitize(d.get('remark','')),
                    ]).strip('|')
                    if line:
                        detail_lines.append(line)
                file_path = '||'.join(detail_lines) if detail_lines else None
                log_operation(
                    operation_type=OperationType.BATCH_ADD_PRODUCT,
                    operation_desc=f"Excel导入成品(覆盖导入): 成功 {success_count} 条",
                    target_type=TargetType.PRODUCT,
                    file_path=file_path
                )
            except Exception:
                current_app.logger.warning('记录Excel导入(覆盖导入)操作日志失败')
            
            # 清理session中的Excel数据
            session.pop('excel_upload_data', None)
        
        return jsonify({
            'success': True,
            'message': f'成功覆盖导入 {success_count} 条数据',
            'success_count': success_count
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"覆盖导入失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500
@main_bp.route('/api/finished_products/import_valid_data_only', methods=['POST'])
@csrf_exempt_route
def import_valid_data_only():
    """只导入有效数据，跳过品番冲突数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        data = request.get_json()
        valid_data = data.get('valid_data', [])
        skip_part_number_conflicts = data.get('skip_part_number_conflicts', True)
        
        if not valid_data:
            return jsonify({'success': False, 'error': '没有有效数据需要导入'}), 400
        
        # 从session中获取之前上传的Excel数据（用于获取品番冲突数量）
        excel_data = session.get('excel_upload_data', {})
        part_number_conflicts = excel_data.get('part_number_conflicts', [])
        
        success_count = 0
        
        # 全量替换：清空后导入有效数据
        try:
            db.session.query(FinishedProductModel).delete(synchronize_session=False)
        except Exception:
            pass
        # 导入有效数据
        for excel_data_item in valid_data:
            try:
                # 检查LOT号是否已经存在
                # 获取当前用户ID
                current_user_id = session.get('user_id')
                
                new_product = FinishedProductModel(
                    vehicle_name=excel_data_item['vehicle_name'] or '未指定',
                    part_number=excel_data_item['part_number'],
                    lot_number=excel_data_item['lot_number'],
                    quantity=excel_data_item['quantity'],
                    remark=excel_data_item.get('remark', ''),
                    source='excel',
                    create_time=datetime.now(),
                    update_time=datetime.now(),
                    created_by=current_user_id,
                    updated_by=current_user_id
                )
                db.session.add(new_product)
                success_count += 1
                
            except Exception as e:
                current_app.logger.error(f"处理有效数据失败: {str(e)}")
                # 如果出现错误，回滚事务并重新开始
                db.session.rollback()
                continue
        
        # 提交事务
        if success_count > 0:
            db.session.commit()
            current_app.logger.info(f"只导入有效数据成功: {success_count} 条")
            # 记录Excel导入操作日志（只导入有效数据），带明细
            try:
                from models.operation_log import OperationType, TargetType
                detail_lines = []
                for d in (valid_data[:1000] if valid_data else []):
                    line = "|".join([
                        _log_sanitize(d.get('vehicle_name','')),
                        _log_sanitize(d.get('part_number','')),
                        _log_sanitize(d.get('lot_number','')),
                        _log_sanitize(d.get('eo','')),
                        _log_sanitize(d.get('quantity','')),
                        _log_sanitize(d.get('remark','')),
                    ]).strip('|')
                    if line:
                        detail_lines.append(line)
                file_path = '||'.join(detail_lines) if detail_lines else None
                log_operation(
                    operation_type=OperationType.BATCH_ADD_PRODUCT,
                    operation_desc=f"Excel导入成品(仅有效数据): 成功 {success_count} 条",
                    target_type=TargetType.PRODUCT,
                    file_path=file_path
                )
            except Exception:
                current_app.logger.warning('记录Excel导入(仅有效数据)操作日志失败')
        
        # 清理session中的Excel数据
        session.pop('excel_upload_data', None)
        
        return jsonify({
            'success': True,
            'message': f'成功导入 {success_count} 条有效数据，跳过 {len(part_number_conflicts)} 条品番冲突数据',
            'success_count': success_count,
            'skipped_count': len(part_number_conflicts)
        })
        
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"导入有效数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/finished_products/download', methods=['GET'])
@csrf_exempt_route
def download_finished_products():
    """下载成品数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        from models.finished_product import get_finished_product_model
        FinishedProductModel = get_finished_product_model()
        
        # 获取查询参数
        vehicle_name = request.args.get('vehicle_name', '').strip()
        part_number = request.args.get('part_number', '').strip()
        lot_number = request.args.get('lot_number', '').strip()
        eo = request.args.get('eo', '').strip()
        
        # 构建查询
        query = db.session.query(FinishedProductModel)
        
        if vehicle_name:
            query = query.filter(FinishedProductModel.vehicle_name.like(f'%{vehicle_name}%'))
        if part_number:
            query = query.filter(FinishedProductModel.part_number.like(f'%{part_number}%'))
        if lot_number:
            query = query.filter(FinishedProductModel.lot_number.like(f'%{lot_number}%'))
        if eo:
            query = query.filter(FinishedProductModel.eo.like(f'%{eo}%'))
        
        # 按创建时间倒序排列（M-3：流式分批，避免整表 ORM 实例化）
        q = query.order_by(FinishedProductModel.create_time.desc())

        # 创建Excel文件（write_only 流式写出，内存占用与行数解耦）
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, Alignment, PatternFill
        from io import BytesIO

        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="成品数据")

        # 设置表头（增加 参考EO 列，位于 EO 之前）
        headers = ['序号', '车种', '品番', 'LOT', '参考EO', 'EO', 'EO状态', '数量', '备注', '数据来源', '创建时间']
        header_cells = []
        for header in headers:
            c = WriteOnlyCell(ws, value=header)
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal='center', vertical='center')
            c.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            header_cells.append(c)
        ws.append(header_cells)

        # 填充数据
        from models.usage_list import UsageList

        # 性能优化：原实现在每个成品循环内查询 UsageList（N+1 且因列被函数包裹导致索引失效）。
        # 改为一次性拉取「规范化品番 -> EO 集合」，后续在内存中查表，查询次数由 N 次降为 1 次。
        def _norm_pno(raw):
            """规范化品番：去空格、全角横线转半角、转大写，与页面匹配口径一致。"""
            return (raw or '').strip().upper().replace(' ', '').replace('－', '-').replace('–', '-').replace('—', '-')

        eo_map = {}
        try:
            usage_q = db.session.query(UsageList.SubPno, UsageList.EoKsc)
            if getattr(UsageList, 'deleted', None) is not None:
                usage_q = usage_q.filter(UsageList.deleted == False)
            if getattr(UsageList, 'is_frozen', None) is not None:
                usage_q = usage_q.filter(UsageList.is_frozen == False)
            # 仅两列走游标分批，避免一次性拉全表
            for subpno, eoksc in usage_q.yield_per(2000):
                key = _norm_pno(subpno)
                val = (eoksc or '').strip()
                if key and val:
                    eo_map.setdefault(key, set()).add(val)
        except Exception:
            eo_map = {}

        # 逐行流式写出
        row_no = 0
        for product in q.yield_per(500):
            row_no += 1
            # 参考EO集合（与页面一致的规范化匹配，忽略deleted）
            try:
                pn_norm = _norm_pno(getattr(product, 'part_number', ''))
                eo_set = eo_map.get(pn_norm, set())
                eo_set_text = '|'.join(sorted(eo_set)) if eo_set else ''
            except Exception:
                eo_set_text = ''
            ws.append([
                row_no,
                product.vehicle_name,
                product.part_number,
                product.lot_number,
                eo_set_text,
                product.get_display_eo(),
                'EO覆盖' if product.eo_override else ('EO注意' if product.needs_eo_attention else 'EO正常'),
                product.quantity,
                product.remark or '',
                product.source,
                product.create_time.strftime('%Y-%m-%d %H:%M:%S') if product.create_time else '',
            ])

        # 调整列宽
        column_widths = [8, 30, 20, 20, 20, 20, 12, 10, 30, 12, 20]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + col)].width = width
        
        # 保存到内存
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        from flask import send_file
        # 操作记录：下载成品数据
        try:
            log_file_download('成品数据.xlsx', 'file')
        except Exception:
            current_app.logger.warning('记录下载成品数据日志失败')
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'成品数据_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
        
    except Exception as e:
        current_app.logger.error(f"下载成品数据失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/material_summary/update', methods=['POST'])
@csrf_exempt_route
def update_material_summary():
    """更新资材量汇总数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：需具备资材量页面更新权限
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download_update:
            return jsonify({'success': False, 'error': '权限不足'}), 403
    
    # 使用后台线程执行，立即返回，避免连接被浏览器/代理中断
    global _material_update_state

    # 允许前端传入 force 重置卡住的状态
    req_json = request.get_json(silent=True) or {}
    force = bool(req_json.get('force')) or (request.args.get('force', '').lower() == 'true')

    # 自恢复：若标记为 running 但出现 error 或已运行超时（> 10 分钟），则自动清理状态
    try:
        started_iso = _material_update_state.get('started_at')
        started_dt = _dt.fromisoformat(started_iso) if started_iso else None
        too_long = started_dt and (_dt.now() - started_dt).total_seconds() > 600
        has_error = bool(_material_update_state.get('error'))
        if _material_update_state.get('running') and (force or has_error or too_long):
            _material_update_state.update({'running': False})
    except Exception:
        pass

    if _material_update_state.get('running'):
        return jsonify({'success': False, 'error': '已有更新任务在执行中'}), 409

    app_obj = current_app._get_current_object()

    def _do_update(app_obj):
        from models.material_summary import MaterialSummary
        from models.finished_product import get_finished_product_model
        from models.usage_list import UsageList
        from collections import defaultdict
        from sqlalchemy import text
        
                # 在线程中使用应用上下文
        with app_obj.app_context():
            current_app.logger.info('[MaterialSummary] update START (async)')
            _material_update_state.update({'running': True, 'started_at': _dt.now().isoformat(), 'finished_at': None, 'updated': 0, 'error': None, 'progress': 0, 'stage': '开始更新'})
            
            try:
                FinishedProductModel = get_finished_product_model()
                MaterialSummaryModel = MaterialSummary
                
                # 准备更新数据，使用upsert逻辑处理唯一约束
                _material_update_state.update({'progress': 10, 'stage': '准备更新数据'})
        
                # 使用SQL JOIN查询替代嵌套循环，大幅提升性能
                current_app.logger.info('[MaterialSummary] 开始执行优化查询...')
                _material_update_state.update({'progress': 20, 'stage': '执行数据库查询'})
                
                # 构建高效的SQL查询 - 优化版本
                # 先创建临时表存储规范化的品番，避免在JOIN中重复计算
                current_app.logger.info('[MaterialSummary] 创建临时规范化表...')
                
                # 先删除可能存在的旧临时表，确保每次都使用最新数据
                db.session.execute(text("DROP TABLE IF EXISTS temp_finished_normalized"))
                db.session.execute(text("DROP TABLE IF EXISTS temp_usage_normalized"))
                
                db.session.execute(text("""
                    CREATE TEMP TABLE temp_finished_normalized AS
                    SELECT 
                        id,
                        vehicle_name,
                        part_number,
                        eo,
                        quantity,
                        remark,
                        REPLACE(REPLACE(REPLACE(REPLACE(UPPER(part_number), ' ', ''), '－', '-'), '–', '-'), '—', '-') as part_number_normalized,
                        UPPER(TRIM(eo)) as eo_normalized
                    FROM finished_product 
                    WHERE eo IS NOT NULL AND eo != ''
                """))
                # 成品品番索引：供后续 usage 过滤与 JOIN 使用
                db.session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_tmp_fin_pn ON temp_finished_normalized(part_number_normalized)"
                ))
                
                db.session.execute(text("""
                    CREATE TEMP TABLE temp_usage_normalized AS
                    SELECT 
                        id,
                        SubPno,
                        EoKsc,
                        Comp_Kind,
                        Detail_Kind,
                        KscPno,
                        Part_Name,
                        Usage,
                        Length,
                        REPLACE(REPLACE(REPLACE(REPLACE(UPPER(SubPno), ' ', ''), '－', '-'), '–', '-'), '—', '-') as subpno_normalized,
                        UPPER(TRIM(EoKsc)) as eoksc_normalized
                    FROM usage_list 
                    WHERE deleted = 0 AND EoKsc IS NOT NULL AND EoKsc != ''
                      -- 仅保留成品实际引用的品番，避免对百万级全表做字符串规范化（大幅提速）
                      AND REPLACE(REPLACE(REPLACE(REPLACE(UPPER(SubPno), ' ', ''), '－', '-'), '–', '-'), '—', '-')
                          IN (SELECT part_number_normalized FROM temp_finished_normalized)
                """))

                # 构建「品番 -> 最新EO版本」映射：成品EO在资料库无对应版本时回退使用。
                # 候选范围：排除已删除行，含冻结版本（与用户口径一致）。
                # 纯 SQL 一次构建（避免逐条 Python INSERT 造成卡顿）。
                # 版本排序键：字母部分 + 数字部分（a1<a9<b1...），非标准格式排最后。
                db.session.execute(text("DROP TABLE IF EXISTS temp_part_latest_eo"))
                db.session.execute(text("""
                    CREATE TEMP TABLE temp_part_latest_eo AS
                    SELECT subpno_normalized, eoksc_normalized AS latest_eo_normalized
                    FROM (
                        SELECT subpno_normalized, eoksc_normalized,
                               ROW_NUMBER() OVER (
                                   PARTITION BY subpno_normalized
                                   ORDER BY
                                       CASE WHEN eoksc_normalized GLOB '[A-Z][0-9]*'
                                            THEN 0 ELSE 1 END,
                                       substr(eoksc_normalized, 1, 1),
                                       CASE WHEN eoksc_normalized GLOB '[A-Z][0-9]*'
                                            THEN CAST(substr(eoksc_normalized, 2) AS INTEGER)
                                            ELSE 0 END
                                   DESC
                               ) AS rn
                        FROM (SELECT DISTINCT subpno_normalized, eoksc_normalized
                              FROM temp_usage_normalized
                              WHERE subpno_normalized IS NOT NULL
                                AND eoksc_normalized IS NOT NULL) t
                    ) ranked
                    WHERE rn = 1
                """))
                db.session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_tmp_latest_eo ON temp_part_latest_eo(subpno_normalized)"
                ))
                # 为 JOIN 关联列建索引，避免全表扫描
                db.session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_tmp_usage_sp_eo ON temp_usage_normalized(subpno_normalized, eoksc_normalized)"
                ))
                db.session.execute(text(
                    "CREATE INDEX IF NOT EXISTS idx_tmp_fin_sp_eo ON temp_finished_normalized(part_number_normalized, eo_normalized)"
                ))
                _latest_count = db.session.execute(
                    text("SELECT COUNT(*) FROM temp_part_latest_eo")
                ).scalar()
                current_app.logger.info(
                    f'[MaterialSummary] 最新EO映射构建完成，品番数: {_latest_count}'
                )
                _material_update_state.update({'progress': 30, 'stage': f'构建最新EO映射完成（{_latest_count}个品番）'})
                
                # 使用规范化后的表进行JOIN，性能大幅提升
                sql_query = text("""
                    SELECT 
                        COALESCE(u.KscPno, u.SubPno) as sub_pno,
                        u.EoKsc as eo_ksc,
                        u.Comp_Kind as comp_kind,
                        u.Detail_Kind as detail_kind,
                        u.KscPno as ksc_pno,
                        u.Part_Name as part_name,
                        u.Usage as usage,
                        u.Length as length,
                        f.vehicle_name,
                        f.part_number,
                        f.quantity as product_quantity,
                        f.remark,
                        CASE 
                            WHEN u.Usage IS NOT NULL AND u.Usage != '' 
                            THEN CAST(u.Usage AS INTEGER) 
                            ELSE 1 
                        END as usage_quantity
                    FROM (
                        -- 在成品侧一次性算出「有效EO」：有精确匹配用成品EO，否则回退该品番最新EO。
                        -- 成品行数远小于用量行数，避免在 JOIN 中用逐行相关子查询导致卡顿。
                        SELECT f.*,
                               CASE
                                   WHEN EXISTS (
                                       SELECT 1 FROM temp_usage_normalized u2
                                       WHERE u2.subpno_normalized = f.part_number_normalized
                                         AND u2.eoksc_normalized = f.eo_normalized
                                   ) THEN f.eo_normalized
                                   ELSE (SELECT l.latest_eo_normalized
                                           FROM temp_part_latest_eo l
                                          WHERE l.subpno_normalized = f.part_number_normalized)
                               END AS effective_eo
                        FROM temp_finished_normalized f
                    ) f
                    INNER JOIN temp_usage_normalized u 
                        ON f.part_number_normalized = u.subpno_normalized
                        AND f.effective_eo = u.eoksc_normalized
                """)
                
                # 执行查询并记录性能
                import time
                query_start_time = time.time()
                result = db.session.execute(sql_query)
                rows = result.fetchall()
                query_end_time = time.time()
                query_duration = query_end_time - query_start_time
                
                # 在清空数据前，先记录匹配到的记录数，用于前端显示
                matched_rows_count = len(rows)
                current_app.logger.info(f'[MaterialSummary] 查询完成，匹配记录数: {matched_rows_count}，耗时: {query_duration:.3f}秒')
                _material_update_state.update({'progress': 40, 'stage': f'查询完成，匹配记录数: {matched_rows_count}，耗时: {query_duration:.3f}秒', 'matched_rows': matched_rows_count})
                
                # 使用字典聚合数据，确保每个sub_pno只有一个记录
                material_summaries_dict = defaultdict(lambda: {
            'vehicle_names': set(),
            'part_numbers': set(),
            'vehicle_details': {},  # 存储每个车种的详细用量信息
            'eo_ksc': None,
            'comp_kind': None,
            'detail_kind': None,
            'ksc_pno': None,
            'part_name': None,
            'usage': None,
            'length': None,
            'total_quantity': 0,
            'unit': '个',
            'remarks': set(),  # 使用集合存储所有相关成品的备注
                    'has_usage_info': False
                })
                
                # 处理查询结果
                _material_update_state.update({'progress': 50, 'stage': '处理查询结果'})
                processed_count = 0
                total_rows = len(rows)
                
                for row in rows:
                    sub_pno = row.sub_pno
                    key = sub_pno  # 只使用sub_pno作为键，确保每个sub_pno只有一个记录
                    
                    # 计算总数量
                    product_material_quantity = row.product_quantity * row.usage_quantity
                    
                    # 聚合数据
                    material_summaries_dict[key]['vehicle_names'].add(row.vehicle_name)
                    material_summaries_dict[key]['part_numbers'].add(row.part_number)
                    material_summaries_dict[key]['total_quantity'] += product_material_quantity
                    material_summaries_dict[key]['has_usage_info'] = True
                    
                    # 记录每个车种的详细用量信息
                    vehicle_key = f"{row.vehicle_name}_{row.part_number}"
                    if vehicle_key not in material_summaries_dict[key]['vehicle_details']:
                        material_summaries_dict[key]['vehicle_details'][vehicle_key] = {
                            'vehicle_name': row.vehicle_name,
                            'part_number': row.part_number,
                            'product_quantity': 0,
                            'usage_quantity': row.usage_quantity,
                            'subtotal': 0
                        }
                    material_summaries_dict[key]['vehicle_details'][vehicle_key]['product_quantity'] += row.product_quantity
                    material_summaries_dict[key]['vehicle_details'][vehicle_key]['subtotal'] += product_material_quantity
                    
                    # 保存第一个匹配的用量信息
                    if material_summaries_dict[key]['eo_ksc'] is None:
                        material_summaries_dict[key]['eo_ksc'] = row.eo_ksc
                        material_summaries_dict[key]['comp_kind'] = row.comp_kind
                        material_summaries_dict[key]['detail_kind'] = row.detail_kind
                        material_summaries_dict[key]['ksc_pno'] = row.ksc_pno
                        material_summaries_dict[key]['part_name'] = row.part_name
                        material_summaries_dict[key]['usage'] = row.usage
                        material_summaries_dict[key]['length'] = row.length
                    
                    # 添加成品备注到集合中
                    if row.remark and row.remark.strip():
                        material_summaries_dict[key]['remarks'].add(row.remark.strip())
                    
                    processed_count += 1
                    # 每处理1000条记录更新一次进度
                    if processed_count % 1000 == 0:
                        progress = 50 + int((processed_count / total_rows) * 30)
                        _material_update_state.update({'progress': progress, 'stage': f'处理中... {processed_count}/{total_rows}'})
                
                current_app.logger.info(f'[MaterialSummary] 数据聚合完成，汇总记录数: {len(material_summaries_dict)}')
                _material_update_state.update({'progress': 80, 'stage': f'数据聚合完成，汇总记录数: {len(material_summaries_dict)}'})
                
                # 创建MaterialSummary对象
                material_summaries = []
                for sub_pno, summary_data in material_summaries_dict.items():
                    vehicle_names_str = ', '.join(sorted(summary_data['vehicle_names']))
                    part_numbers_str = ', '.join(sorted(summary_data['part_numbers']))
                    
                    # 生成车种详细用量备注
                    vehicle_details_list = []
                    for detail in summary_data['vehicle_details'].values():
                        vehicle_details_list.append(
                            f"{detail['vehicle_name']}({detail['part_number']}): "
                            f"成品{detail['product_quantity']}个×用量{detail['usage_quantity']}个={detail['subtotal']}个"
                        )
                    vehicle_details_str = '; '.join(vehicle_details_list)
                    
                    # 合并所有备注为一个字符串
                    remark_str = '; '.join(sorted(summary_data['remarks'])) if summary_data['remarks'] else None
                    
                    summary = MaterialSummaryModel(
                        vehicle_name=vehicle_names_str,
                        part_number=part_numbers_str,
                        sub_pno=sub_pno,
                        eo_ksc=summary_data['eo_ksc'],
                        comp_kind=summary_data['comp_kind'],
                        detail_kind=summary_data['detail_kind'],
                        ksc_pno=summary_data['ksc_pno'],
                        part_name=summary_data['part_name'],
                        usage=summary_data['usage'],
                        length=summary_data['length'],
                        total_quantity=summary_data['total_quantity'],
                        unit=summary_data['unit'],
                        vehicle_details=vehicle_details_str,
                        remark=remark_str
                    )
                    material_summaries.append(summary)
                
                # 记录最终更新的记录数
                total_updated = len(material_summaries)
                
                # 使用upsert逻辑保存数据，处理唯一约束
                _material_update_state.update({'progress': 90, 'stage': '保存数据到数据库', 'updated': total_updated})
                if material_summaries:
                    from sqlalchemy import text
                    
                    # 将删除和插入操作放在同一个事务中，避免数据短暂为0
                    # 先清空现有数据
                    db.session.query(MaterialSummaryModel).delete()
                    
                    # 然后批量插入新数据
                    # 转换为字典列表，使用bulk_insert_mappings提高性能
                    material_dicts = [{
                        'vehicle_name': ms.vehicle_name,
                        'part_number': ms.part_number,
                        'sub_pno': ms.sub_pno,
                        'eo_ksc': ms.eo_ksc,
                        'comp_kind': ms.comp_kind,
                        'detail_kind': ms.detail_kind,
                        'ksc_pno': ms.ksc_pno,
                        'part_name': ms.part_name,
                        'usage': ms.usage,
                        'length': ms.length,
                        'total_quantity': ms.total_quantity,
                        'unit': ms.unit,
                        'vehicle_details': ms.vehicle_details,
                        'remark': ms.remark,
                        'last_update_time': ms.last_update_time,
                        'create_time': ms.create_time
                    } for ms in material_summaries]
                    db.session.bulk_insert_mappings(MaterialSummaryModel, material_dicts)
                    
                    # 提交事务，确保删除和插入在同一个事务中完成
                    db.session.commit()
                    
                    # 数据更新完成后，使缓存失效，确保后续查询获取最新数据
                    try:
                        query_cache_manager.invalidate_list_cache()
                    except Exception as e:
                        current_app.logger.warning(f'[MaterialSummary] 缓存失效失败: {e}')
                    
                    _material_update_state.update({
                        'running': False, 
                        'finished_at': _dt.now().isoformat(), 
                        'updated': len(material_summaries), 
                        'error': None,
                        'progress': 100,
                        'stage': f'更新完成，共处理 {len(material_summaries)} 条记录',
                        'matched_rows': len(material_summaries)
                    })
                    current_app.logger.info(f"[MaterialSummary] update DONE count={len(material_summaries)}")
                else:
                    # 如果没有新数据，清空现有数据
                    db.session.query(MaterialSummaryModel).delete()
                    db.session.commit()
                    
                    # 数据清空后，使缓存失效
                    try:
                        query_cache_manager.invalidate_list_cache()
                    except Exception as e:
                        current_app.logger.warning(f'[MaterialSummary] 缓存失效失败: {e}')
                    
                    _material_update_state.update({
                        'running': False, 
                        'finished_at': _dt.now().isoformat(), 
                        'updated': 0, 
                        'error': None,
                        'progress': 100,
                        'stage': '更新完成，无匹配数据',
                        'matched_rows': 0
                    })
                    current_app.logger.info("[MaterialSummary] update DONE count=0 (no matching data)")
                
            except Exception as e:
                current_app.logger.error(f"[MaterialSummary] update ERROR: {str(e)}")
                _material_update_state.update({
                    'running': False, 
                    'finished_at': _dt.now().isoformat(), 
                    'updated': 0, 
                    'error': str(e)
                })

    threading.Thread(target=_do_update, args=(app_obj,), daemon=True).start()
    return jsonify({'success': True, 'message': '更新任务已启动'}), 202


@main_bp.route('/api/material_summary/list', methods=['GET'])
@csrf_exempt_route
def get_material_summary_list():
    """获取资材量汇总列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 访问权限：需具备资材量页面访问权限
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download:
            return jsonify({'success': False, 'error': '权限不足'}), 403
    
    try:
        from models.material_summary import MaterialSummary
        
        MaterialSummaryModel = MaterialSummary
        
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        # 服务器端分页兜底，防止一次性返回过多数据
        if per_page is None or per_page <= 0:
            per_page = 10
        per_page = min(per_page, 100)
        vehicle_name = request.args.get('vehicle_name', '').strip()
        sub_pno = request.args.get('sub_pno', '').strip()
        quantity_min = request.args.get('quantity_min', '').strip()
        quantity_max = request.args.get('quantity_max', '').strip()
        
        # 构建查询
        query = MaterialSummaryModel.query
        
        if vehicle_name:
            query = query.filter(MaterialSummaryModel.vehicle_name.like(f'%{vehicle_name}%'))
        if sub_pno:
            query = query.filter(MaterialSummaryModel.sub_pno.like(f'%{sub_pno}%'))
        if quantity_min:
            try:
                min_quantity = int(quantity_min)
                query = query.filter(MaterialSummaryModel.total_quantity >= min_quantity)
            except ValueError:
                pass
        if quantity_max:
            try:
                max_quantity = int(quantity_max)
                query = query.filter(MaterialSummaryModel.total_quantity <= max_quantity)
            except ValueError:
                pass
        
        # 分页查询
        pagination = query.order_by(MaterialSummaryModel.last_update_time.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        
        # 转换为字典格式
        materials = [material.to_dict() for material in pagination.items]
        
        return jsonify({
            'success': True,
            'data': {
                'materials': materials,
                'pagination': {
                    'page': pagination.page,
                    'per_page': pagination.per_page,
                    'total': pagination.total,
                    'pages': pagination.pages,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next
                }
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取资材量汇总列表失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/material_summary/quantity_sum', methods=['GET'])
@csrf_exempt_route
def get_material_summary_quantity_sum():
    """按当前筛选条件返回资材量总数量（SUM）。

    性能优化：原前端用 per_page=10000 拉全量记录再在前端 reduce 求和，
    这里直接下推为 SQL SUM 聚合，只返回一个数字。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download:
            return jsonify({'success': False, 'error': '权限不足'}), 403

    try:
        from models.material_summary import MaterialSummary
        from sqlalchemy import func

        vehicle_name = request.args.get('vehicle_name', '').strip()
        sub_pno = request.args.get('sub_pno', '').strip()
        quantity_min = request.args.get('quantity_min', '').strip()
        quantity_max = request.args.get('quantity_max', '').strip()

        query = db.session.query(func.coalesce(func.sum(MaterialSummary.total_quantity), 0))
        if vehicle_name:
            query = query.filter(MaterialSummary.vehicle_name.like(f'%{vehicle_name}%'))
        if sub_pno:
            query = query.filter(MaterialSummary.sub_pno.like(f'%{sub_pno}%'))
        if quantity_min:
            try:
                query = query.filter(MaterialSummary.total_quantity >= int(quantity_min))
            except ValueError:
                pass
        if quantity_max:
            try:
                query = query.filter(MaterialSummary.total_quantity <= int(quantity_max))
            except ValueError:
                pass

        total_quantity = int(query.scalar() or 0)
        return jsonify({'success': True, 'total_quantity': total_quantity})
    except Exception as e:
        current_app.logger.error(f"获取资材量总数量失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/material_summary/statistics', methods=['GET'])
@csrf_exempt_route
def get_material_summary_statistics():
    """获取资材量汇总统计信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 访问权限：需具备资材量页面访问权限
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download:
            return jsonify({'success': False, 'error': '权限不足'}), 403
    
    try:
        from models.material_summary import MaterialSummary
        from sqlalchemy import func
        
        MaterialSummaryModel = MaterialSummary
        
        # 获取更新任务状态（异步）
        try:
            status = _material_update_state.copy()  # noqa
        except Exception:
            status = None
        
        # 检查是否有正在运行的更新任务
        has_running_task = status and status.get('running', False)
        is_finished = status and not status.get('running', False) and status.get('finished_at') is not None
        
        # 如果有正在运行的更新任务，使用更新状态中的信息
        if has_running_task:
            # 从状态中获取预期的记录数
            matched_rows = status.get('matched_rows', 0)
            updated_count = status.get('updated', 0)
            
            # 优先使用matched_rows作为预期数量
            total_materials = matched_rows if matched_rows > 0 else updated_count
            
            # 正在更新中，total_quantity保持为0（数据尚未完全更新）
            total_quantity = 0
            
            # 从状态中获取处理阶段信息，尝试提取记录数
            stage = status.get('stage', '')
            import re
            match = re.search(r'匹配记录数: (\d+)', stage)
            if match:
                total_materials = int(match.group(1))
        else:
            # 没有运行中的更新任务（含更新刚完成的情况），从数据库查询真实数据
            # 更新完成后，数据库数据已是最新，直接查询真实数据
            total_materials = MaterialSummaryModel.query.count()
            total_quantity = db.session.query(func.sum(MaterialSummaryModel.total_quantity)).scalar() or 0
        
        # 获取最后更新时间
        last_update = db.session.query(func.max(MaterialSummaryModel.last_update_time)).scalar()
        
        # 获取本月新增数量
        from datetime import datetime, timedelta
        start_of_month = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_count = MaterialSummaryModel.query.filter(
            MaterialSummaryModel.create_time >= start_of_month
        ).count()
        
        # 构建返回的状态信息
        response_status = None
        if status:
            response_status = {
                'running': status.get('running', False),
                'progress': status.get('progress', 0),
                'stage': status.get('stage', ''),
                'updated': status.get('updated', 0),
                'error': status.get('error'),
                'started_at': status.get('started_at'),
                'finished_at': status.get('finished_at'),
                'is_finished': is_finished
            }
        
        return jsonify({
            'success': True,
            'data': {
                'total_materials': total_materials,
                'total_quantity': total_quantity,
                'last_update_time': last_update.strftime('%Y-%m-%d %H:%M:%S') if last_update else None,
                'monthly_new': monthly_count,
                'update_status': response_status
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取资材量汇总统计失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/material_summary/download', methods=['GET'])
@csrf_exempt_route
def download_material_summary():
    """下载资材量汇总数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：需具备下载权限
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download_download:
            return jsonify({'success': False, 'error': '权限不足'}), 403
    
    try:
        from models.material_summary import MaterialSummary
        
        MaterialSummaryModel = MaterialSummary
        
        # 获取查询参数
        vehicle_name = request.args.get('vehicle_name', '').strip()
        sub_pno = request.args.get('sub_pno', '').strip()
        quantity_min = request.args.get('quantity_min', '').strip()
        quantity_max = request.args.get('quantity_max', '').strip()
        
        # 构建查询
        query = MaterialSummaryModel.query
        
        if vehicle_name:
            query = query.filter(MaterialSummaryModel.vehicle_name.like(f'%{vehicle_name}%'))
        if sub_pno:
            query = query.filter(MaterialSummaryModel.sub_pno.like(f'%{sub_pno}%'))
        if quantity_min:
            try:
                min_quantity = int(quantity_min)
                query = query.filter(MaterialSummaryModel.total_quantity >= min_quantity)
            except ValueError:
                pass
        if quantity_max:
            try:
                max_quantity = int(quantity_max)
                query = query.filter(MaterialSummaryModel.total_quantity <= max_quantity)
            except ValueError:
                pass
        
        # 获取所有数据（M-3：流式分批，避免一次性 ORM 实例化整表造成内存峰值）
        q = query.order_by(MaterialSummaryModel.last_update_time.desc())

        # 创建Excel文件（write_only 流式写出，内存占用与行数解耦）
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, Alignment, PatternFill
        from io import BytesIO

        wb = Workbook(write_only=True)
        ws = wb.create_sheet(title="资材量汇总")

        # 设置表头
        headers = ['序号', '车种', '资材品番', '组件类型', '数量', '备注', '最后更新时间']
        header_cells = []
        for header in headers:
            c = WriteOnlyCell(ws, value=header)
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal='center', vertical='center')
            c.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            header_cells.append(c)
        ws.append(header_cells)

        # 填充数据：每 500 行一批从游标读取，边读边写，避免全量驻留内存
        row_no = 0
        for material in q.yield_per(500):
            row_no += 1
            last_time_str = (
                material.last_update_time.strftime('%Y-%m-%d %H:%M:%S')
                if getattr(material, 'last_update_time', None) else ''
            )
            ws.append([
                row_no,
                material.vehicle_name,
                material.sub_pno,
                getattr(material, 'comp_kind', None),
                material.total_quantity,
                getattr(material, 'remark', '') or '',
                last_time_str,
            ])

        # 调整列宽
        column_widths = [8, 20, 20, 14, 12, 30, 20]
        for col, width in enumerate(column_widths, 1):
            ws.column_dimensions[chr(64 + col)].width = width
        
        # 保存到内存
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        from flask import send_file
        # 操作记录：下载资材量汇总
        try:
            log_file_download('资材量汇总.xlsx', 'file')
        except Exception:
            current_app.logger.warning('记录下载资材量汇总日志失败')
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'资材量汇总_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
        
    except Exception as e:
        current_app.logger.error(f"下载资材量汇总失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/material_summary/download_details', methods=['GET'])
@csrf_exempt_route
def download_vehicle_details():
    """下载车种详细用量明细数据"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    # 权限：需具备下载权限
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        permission = PermissionInfo.query.filter_by(user_id=session['user_id']).first()
        if not permission or not permission.can_material_download_download:
            return jsonify({'success': False, 'error': '权限不足'}), 403
    
    try:
        from models.material_summary import MaterialSummary
        
        MaterialSummaryModel = MaterialSummary
        
        # 获取查询参数
        vehicle_name = request.args.get('vehicle_name', '').strip()
        sub_pno = request.args.get('sub_pno', '').strip()
        quantity_min = request.args.get('quantity_min', '').strip()
        quantity_max = request.args.get('quantity_max', '').strip()
        
        # 构建查询
        query = MaterialSummaryModel.query
        
        if vehicle_name:
            query = query.filter(MaterialSummaryModel.vehicle_name.like(f'%{vehicle_name}%'))
        if sub_pno:
            query = query.filter(MaterialSummaryModel.sub_pno.like(f'%{sub_pno}%'))
        if quantity_min:
            try:
                min_quantity = int(quantity_min)
                query = query.filter(MaterialSummaryModel.total_quantity >= min_quantity)
            except ValueError:
                pass
        if quantity_max:
            try:
                max_quantity = int(quantity_max)
                query = query.filter(MaterialSummaryModel.total_quantity <= max_quantity)
            except ValueError:
                pass
        
        # 获取所有数据
        materials = query.order_by(MaterialSummaryModel.last_update_time.desc()).all()
        
        # 创建Excel文件
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from io import BytesIO
        
        wb = Workbook()
        ws = wb.active
        ws.title = "车种详细用量明细"
        
        # 设置表头
        headers = ['序号', '资材品番', '车种', '成品品番', '成品数量', '单成品用量', '总用量', '组件类型', '备注', '最后更新时间']
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = Font(bold=True)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
        
        # 解析并填充详细数据
        row_num = 2
        for material in materials:
            vehicle_details = getattr(material, 'vehicle_details', '') or ''
            if vehicle_details and vehicle_details != '-':
                # 解析车种详细用量备注格式：车种(成品品番): 成品数量个×用量数量个=总数量个; ...
                details = vehicle_details.split(';')
                for detail in details:
                    detail = detail.strip()
                    if detail:
                        # 匹配格式：车种(成品品番): 成品数量个×用量数量个=总数量个
                        import re
                        match = re.match(r'^(.+?)\(([^)]+?)\):\s*成品(\d+)个×用量(\d+)个=(\d+)个$', detail)
                        if match:
                            vehicle = match.group(1).strip()
                            product = match.group(2).strip()
                            product_count = int(match.group(3))
                            usage_per_product = int(match.group(4))
                            total_quantity = int(match.group(5))
                            
                            # 填充数据
                            ws.cell(row=row_num, column=1, value=row_num-1)
                            ws.cell(row=row_num, column=2, value=material.sub_pno)
                            ws.cell(row=row_num, column=3, value=vehicle)
                            ws.cell(row=row_num, column=4, value=product)
                            ws.cell(row=row_num, column=5, value=product_count)
                            ws.cell(row=row_num, column=6, value=usage_per_product)
                            ws.cell(row=row_num, column=7, value=total_quantity)
                            ws.cell(row=row_num, column=8, value=getattr(material, 'comp_kind', None))
                            ws.cell(row=row_num, column=9, value=getattr(material, 'remark', '') or '')
                            # 最后更新时间（格式：YYYY-MM-DD HH:MM:SS）
                            last_time_str = material.last_update_time.strftime('%Y-%m-%d %H:%M:%S') if getattr(material, 'last_update_time', None) else ''
                            cell_time = ws.cell(row=row_num, column=10, value=last_time_str)
                            cell_time.alignment = Alignment(horizontal='center')
                            
                            row_num += 1
        
        # 调整列宽
        column_widths = [8, 20, 20, 20, 12, 12, 12, 14, 30, 20]
        for col, width in enumerate(column_widths, 1):
            if col <= len(column_widths):
                ws.column_dimensions[chr(64 + col)].width = width
        
        # 保存到内存
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        from flask import send_file
        # 操作记录：下载车种详细用量明细
        try:
            log_file_download('车种详细用量明细.xlsx', 'file')
        except Exception:
            current_app.logger.warning('记录下载车种详细用量明细日志失败')
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'车种详细用量明细_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        )
    except Exception as e:
        current_app.logger.error(f"下载车种详细用量明细失败: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# 多文件 SubList 合并对比（基准 + 其余文件），按"合并单元列对比"规则
@main_bp.route('/api/file_compare_multi', methods=['POST'])
@login_required
def api_file_compare_multi():
    try:
        data = request.get_json() or {}
        baseline_id = data.get('baseline_file_id')
        file_ids = data.get('file_ids') or []
        if not baseline_id or not file_ids or baseline_id not in file_ids:
            return jsonify({'success': False, 'error': '请选择基准文件且包含在文件列表中'}), 400

        # 取文件
        baseline = db.session.get(FileInfo, int(baseline_id))
        files = FileInfo.query.filter(FileInfo.id.in_(file_ids)).all()
        if not baseline or not files:
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        # 限定 SubList
        for f in files:
            if (f.file_type or '').lower() != 'sublist':
                return jsonify({'success': False, 'error': f'仅支持SubList，对象 {f.filename} 类型不符'}), 400

        # 读取所有DataFrame（优先从磁盘，不存在则从数据库读取）
        dfs = {}
        for f in files:
            df = _read_file_data(f)
            df = df.fillna('').astype(str)
            dfs[f.id] = df

        # 标准化列（按照用户要求的顺序）
        required_cols = [
            'SetCode','Circuit','Wire','WireCode','Color','SQ','Length_Cut2','Tml1','Tml2',
            'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
            'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
        ]
        context_cols = ['SubPno','EoKsc','GroupNo','GroupOrder','SetCode','CutCode']

        def _normalize_setcode(val: str) -> str:
            """将 SetCode 规整为用于合并判断的前缀：只保留前两段+末尾下划线。
            
            例：CN7PFM_AP24_18 -> CN7PFM_AP24_
            若只有一段：ABC -> ABC_
            为空或非字符串时返回空串。
            """
            try:
                s = str(val or '').strip()
                if not s:
                    return ''
                parts = s.split('_')
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1]}_"
                # 只有一段时也补一个下划线，保持格式一致
                return f"{parts[0]}_"
            except Exception:
                return ''

        def norm_df(df: pd.DataFrame) -> pd.DataFrame:
            dfx = df.copy()
            for c in required_cols + context_cols:
                if c not in dfx.columns:
                    dfx[c] = ''
            dfx = dfx.fillna('').astype(str)
            for c in required_cols + context_cols:
                dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
            
            # 组装 key：按照用户要求，直接合并指定列
            dfx['__key__'] = dfx.apply(lambda row: '|'.join([
                row[c] for c in required_cols  # 按照指定顺序合并列
            ]), axis=1)
            
            return dfx

        dfs = {fid: norm_df(df) for fid, df in dfs.items()}

        bl = dfs[baseline.id]
        bl_keys = set(bl['__key__'])

        # 按照合并单元列对比的逻辑，计算每个文件与基准文件的差异
        def row_by_key(df, key):
            try:
                return df[df['__key__'] == key].iloc[0]
            except Exception:
                return None

        # 计算每个文件的对比结果（仅返回计数，明细按需/下载时再算）
        file_comparisons = []
        all_added_keys = set()
        all_removed_keys = set()
        all_modified_keys = set()
        
        for f in files:
            if f.id == baseline.id:
                continue  # 跳过基准文件
            
            # 获取该文件的键集合
            file_keys = set(dfs[f.id]['__key__'])
            
            # 按照合并单元列对比的逻辑计算差异
            # 新增：基准文件有但该文件没有的键
            file_added_keys = bl_keys - file_keys
            # 删除：该文件有但基准文件没有的键  
            file_removed_keys = file_keys - bl_keys
            # 公共键
            common_keys = file_keys & bl_keys
            
            # 统计数量
            file_added = len(file_added_keys)
            file_removed = len(file_removed_keys)
            file_modified = 0
            
            # 检查修改（在公共键上，比较必需列是否一致）
            file_modified_details = []
            for k in common_keys:
                base_row = row_by_key(bl, k)
                file_row = row_by_key(dfs[f.id], k)
                if base_row is not None and file_row is not None:
                    for c in required_cols:
                        if str(base_row[c]).strip() != str(file_row[c]).strip():
                            file_modified += 1
                            file_modified_details.append({
                                'key': k,
                                'column': c,
                                'base_value': str(base_row[c]).strip(),
                                'compare_value': str(file_row[c]).strip(),
                                'context': {col: str(base_row[col]).strip() for col in context_cols if col in base_row}
                            })
                            break
            
            file_comparisons.append({
                'file_id': f.id,
                'filename': f.filename,
                'added': file_added,
                'removed': file_removed,
                'modified': file_modified
            })
            
            # 收集所有差异键用于总体统计
            all_added_keys.update(file_added_keys)
            all_removed_keys.update(file_removed_keys)
            for detail in file_modified_details:
                all_modified_keys.add(detail['key'])

        # 轻量返回：不包含 added/removed/modified 的明细，下载时再计算

        result = {
            'success': True,
            'baseline_file': {'id': int(baseline.id), 'name': baseline.filename},
            'summary': {
                'total_keys': len(bl_keys),
                'added': len(all_added_keys),
                'removed': len(all_removed_keys),
                'modified': len(all_modified_keys)
            },
            'file_comparisons': file_comparisons,
            'added': [],
            'removed': [],
            'modified': []
        }
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"多文件对比失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '处理失败'}), 500


@main_bp.route('/api/usage_compare_multi', methods=['POST'])
@login_required
def api_usage_compare_multi():
    try:
        data = request.get_json() or {}
        baseline_id = data.get('baseline_file_id')
        file_ids = data.get('file_ids') or []
        if not baseline_id or not file_ids or baseline_id not in file_ids:
            return jsonify({'success': False, 'error': '请选择基准文件且包含在文件列表中'}), 400

        # 取文件
        baseline = db.session.get(FileInfo, int(baseline_id))
        files = FileInfo.query.filter(FileInfo.id.in_(file_ids)).all()
        if not baseline or not files:
            return jsonify({'success': False, 'error': '文件不存在'}), 404
        # 限定 UsageList
        for f in files:
            if (f.file_type or '').lower() != 'usagelist':
                return jsonify({'success': False, 'error': f'仅支持UsageList，对象 {f.filename} 类型不符'}), 400

        # 读取所有DataFrame（优先从磁盘，不存在则从数据库读取）
        dfs = {}
        for f in files:
            df = _read_file_data(f)
            df = df.fillna('').astype(str)
            dfs[f.id] = df

        # UsageList的必需列，用于生成复合键
        required_cols = [
            'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'
        ]
        context_cols = ['SubPno', 'EoKsc', 'VehicleType']

        def norm_df(df: pd.DataFrame) -> pd.DataFrame:
            dfx = df.copy()
            for c in required_cols + context_cols:
                if c not in dfx.columns:
                    dfx[c] = ''
            dfx = dfx.fillna('').astype(str)
            for c in required_cols + context_cols:
                dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
            
            # 组装 key：通过连接必需列生成复合键
            dfx['__key__'] = dfx.apply(lambda row: '|'.join([
                row[c] for c in required_cols  # 连接所有必需列
            ]), axis=1)
            
            return dfx

        dfs = {fid: norm_df(df) for fid, df in dfs.items()}

        bl = dfs[baseline.id]
        bl_keys = set(bl['__key__'])

        # 按照合并单元列对比的逻辑，计算每个文件与基准文件的差异
        def row_by_key(df, key):
            try:
                return df[df['__key__'] == key].iloc[0]
            except Exception:
                return None

        # 计算每个文件的对比结果（仅返回计数，明细按需/下载时再算）
        file_comparisons = []
        all_added_keys = set()
        all_removed_keys = set()
        all_modified_keys = set()
        
        for f in files:
            if f.id == baseline.id:
                continue  # 跳过基准文件
            
            # 获取该文件的键集合
            file_keys = set(dfs[f.id]['__key__'])
            
            # 按照合并单元列对比的逻辑计算差异
            # 新增：基准文件有但该文件没有的键
            file_added_keys = bl_keys - file_keys
            # 删除：该文件有但基准文件没有的键  
            file_removed_keys = file_keys - bl_keys
            # 公共键
            common_keys = file_keys & bl_keys
            
            # 统计数量
            file_added = len(file_added_keys)
            file_removed = len(file_removed_keys)
            file_modified = 0
            
            # 检查修改（在公共键上，比较必需列是否一致）
            file_modified_details = []
            for k in common_keys:
                base_row = row_by_key(bl, k)
                file_row = row_by_key(dfs[f.id], k)
                if base_row is not None and file_row is not None:
                    for c in required_cols:
                        if str(base_row[c]).strip() != str(file_row[c]).strip():
                            file_modified += 1
                            file_modified_details.append({
                                'key': k,
                                'column': c,
                                'base_value': str(base_row[c]).strip(),
                                'compare_value': str(file_row[c]).strip(),
                                'context': {col: str(base_row[col]).strip() for col in context_cols if col in base_row}
                            })
                            break
            
            file_comparisons.append({
                'file_id': f.id,
                'filename': f.filename,
                'added': file_added,
                'removed': file_removed,
                'modified': file_modified
            })
            
            # 收集所有差异键用于总体统计
            all_added_keys.update(file_added_keys)
            all_removed_keys.update(file_removed_keys)
            for detail in file_modified_details:
                all_modified_keys.add(detail['key'])

        # 轻量返回：不包含 added/removed/modified 的明细，下载时再算

        result = {
            'success': True,
            'baseline_file': {'id': int(baseline.id), 'name': baseline.filename},
            'summary': {
                'total_keys': len(bl_keys),
                'added': len(all_added_keys),
                'removed': len(all_removed_keys),
                'modified': len(all_modified_keys)
            },
            'file_comparisons': file_comparisons,
            'added': [],
            'removed': [],
            'modified': []
        }
        return jsonify(result)
    except Exception as e:
        current_app.logger.error(f"UsageList多文件对比失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '处理失败'}), 500


@main_bp.route('/api/usage_compare_multi/download', methods=['POST'])
@login_required
def api_usage_compare_multi_download():
    try:
        data = request.get_json() or {}
        payload = data.get('payload')
        mode = data.get('mode', 'summary')  # 默认为汇总模式
        file_id = data.get('file_id')  # 单文件模式时使用
        
        if not payload:
            return jsonify({'success': False, 'error': '无数据'}), 400

        # 完全复制UsageList多文件对比的下载逻辑
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        import tempfile
        import os
        from datetime import datetime
        import urllib.parse
        import pandas as pd
        from models.file_info import FileInfo
        from openpyxl.utils import get_column_letter
        
        # 定义UsageList的必需列，用于生成复合键
        required_cols = [
            'Comp_Kind', 'Detail_Kind', 'KscPno', 'Part_Name', 'Usage'
        ]
        context_cols = ['SubPno', 'EoKsc', 'VehicleType']
        headers_out = context_cols + required_cols

        # 工具函数：按UsageList多文件对比规则现算差异
        def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
            dfx = df.copy()
            for c in required_cols + context_cols:
                if c not in dfx.columns:
                    dfx[c] = ''
            dfx = dfx.fillna('').astype(str)
            for c in required_cols + context_cols:
                dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
            dfx['__key__'] = dfx.apply(lambda row: '|'.join([row[c] for c in required_cols]), axis=1)
            return dfx

        def _row_by_key(df: pd.DataFrame, key: str):
            try:
                return df[df['__key__'] == key].iloc[0]
            except Exception:
                return None

        def compute_usage_diff_for_pair(baseline_id: int, compare_id: int):
            base_file = db.session.get(FileInfo, int(baseline_id))
            cmp_file = db.session.get(FileInfo, int(compare_id))
            if not base_file or not cmp_file:
                return [], [], []
            bl_df = _norm_df(_read_file_data(base_file))
            cm_df = _norm_df(_read_file_data(cmp_file))
            bl_keys = set(bl_df['__key__'])
            cm_keys = set(cm_df['__key__'])
            added_keys = bl_keys - cm_keys
            deleted_keys = cm_keys - bl_keys
            common = bl_keys & cm_keys

            added_details = []
            for k in added_keys:
                r = _row_by_key(bl_df, k)
                if r is not None:
                    added_details.append({'key': k, 'context': {c: str(r[c]).strip() for c in context_cols}, 'values': {c: str(r[c]).strip() for c in required_cols}})
            removed_details = []
            for k in deleted_keys:
                r = _row_by_key(cm_df, k)
                if r is not None:
                    removed_details.append({'key': k, 'context': {c: str(r[c]).strip() for c in context_cols}, 'values': {c: str(r[c]).strip() for c in required_cols}})
            modified_details_local = []
            for k in common:
                br = _row_by_key(bl_df, k)
                cr = _row_by_key(cm_df, k)
                if br is None or cr is None:
                    continue
                diffs = []
                # 差异对比范围：必需列 + 上下文列
                compare_cols = required_cols + context_cols
                for c in compare_cols:
                    if str(br[c]).strip() != str(cr[c]).strip():
                        diffs.append({'column': c, 'base_value': str(br[c]).strip(), 'file_value': str(cr[c]).strip()})
                if diffs:
                    modified_details_local.append({'key': k, 'file_id': int(compare_id), 'filename': cmp_file.filename, 'context': {cc: str(br[cc]).strip() for cc in context_cols}, 'differences': diffs})
            return added_details, removed_details, []

        # 获取数据
        baseline_file = payload.get('baseline_file', {})
        summary = payload.get('summary', {})
        file_comparisons = payload.get('file_comparisons', [])
        modified_details = payload.get('modified', [])
        
        # 根据模式处理数据
        if mode == 'single_file':
            # 单文件模式：后端现算该文件与基准文件的差异
            if not file_id:
                return jsonify({'success': False, 'error': '缺少文件ID'}), 400
            baseline_id = baseline_file.get('id') or baseline_file.get('file_id')
            if not baseline_id:
                return jsonify({'success': False, 'error': '缺少基准文件ID'}), 400

            # 即时计算差异
            added_details, removed_details, modified_details = compute_usage_diff_for_pair(int(baseline_id), int(file_id))

            # 扁平化 added/removed 用于"增加/删除"两段
            added = []
            for detail in (added_details or []):
                row = {}
                row.update(detail.get('context', {}))
                row.update(detail.get('values', {}))
                added.append(row)

            deleted = []
            for detail in (removed_details or []):
                row = {}
                row.update(detail.get('context', {}))
                row.update(detail.get('values', {}))
                deleted.append(row)

            # 标题用数据库中的文件名（兜底用 payload 内的）
            try:
                from models.file_info import FileInfo
                cmp_obj = db.session.get(FileInfo, int(file_id))
                comparison_file = cmp_obj.filename if cmp_obj else None
            except Exception:
                comparison_file = None
            if not comparison_file:
                for comp in file_comparisons:
                    if comp.get('file_id') == file_id:
                        comparison_file = comp.get('filename')
                        break
            
            # 生成Excel文件
            wb = Workbook()
            ws = wb.active
            ws.title = 'UsageList变更记录'

            base_name = baseline_file.get("name", "未知").replace('_UsageList.xls', '').replace('_UsageList.xlsx', '')
            cmp_name = (comparison_file or '未知').replace('_UsageList.xls', '').replace('_UsageList.xlsx', '')
            header_line = f'{cmp_name}  --->  {base_name}   +{len(added)}  -{len(deleted)}'
            ws.append([header_line])
            last_col = get_column_letter(len(headers_out))
            ws.merge_cells(f'A1:{last_col}1')
            ws['A1'].font = Font(size=14, bold=True)
            ws['A1'].alignment = Alignment(horizontal='center', vertical='center')

            # 空一行
            ws.append([])
            # 表头
            ws.append(headers_out)
            for col in range(1, len(headers_out) + 1):
                cell = ws.cell(row=3, column=col)
                cell.font = Font(bold=True, color='FFFFFFFF')
                cell.alignment = Alignment(horizontal='center')
                cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
                cell.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

            # 构建复合键映射
            added_map = {str(r.get('Comp_Kind', '')).strip() + str(r.get('Detail_Kind', '')).strip() + str(r.get('KscPno', '')).strip(): r for r in (added or [])}
            deleted_map = {str(r.get('Comp_Kind', '')).strip() + str(r.get('Detail_Kind', '')).strip() + str(r.get('KscPno', '')).strip(): r for r in (deleted or [])}

            # 增加记录
            if added:
                ws.append(['增加记录'])
                title_row = ws.max_row
                ws[f'A{title_row}'].font = Font(size=12, bold=True)
                ws[f'A{title_row}'].alignment = Alignment(horizontal='left')
                for col in range(1, len(headers_out) + 1):
                    c = ws.cell(row=title_row, column=col)
                    c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                    c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                for i, rd in enumerate(added):
                    ws.append([rd.get(h, '') for h in headers_out])
                    row_num = title_row + 1 + i
                    for col in range(1, len(headers_out) + 1):
                        c = ws.cell(row=row_num, column=col)
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                        c.fill = PatternFill(fill_type='solid', fgColor='FFE6FFED')
                        header_name = headers_out[col - 1]
                        key_val = str(rd.get('Comp_Kind', '')).strip() + str(rd.get('Detail_Kind', '')).strip() + str(rd.get('KscPno', '')).strip()
                        counterpart = deleted_map.get(key_val) if key_val else None
                        if counterpart is not None:
                            c.font = Font(color='0000FF', bold=True)

            # 删除记录
            if deleted:
                ws.append(['删除记录'])
                title_row = ws.max_row
                ws[f'A{title_row}'].font = Font(size=12, bold=True)
                ws[f'A{title_row}'].alignment = Alignment(horizontal='left')
                for col in range(1, len(headers_out) + 1):
                    c = ws.cell(row=title_row, column=col)
                    c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                    c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                for i, rd in enumerate(deleted):
                    ws.append([rd.get(h, '') for h in headers_out])
                    row_num = title_row + 1 + i
                    for col in range(1, len(headers_out) + 1):
                        c = ws.cell(row=row_num, column=col)
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                        c.fill = PatternFill(fill_type='solid', fgColor='FFFDECEA')
                        header_name = headers_out[col - 1]
                        key_val = str(rd.get('Comp_Kind', '')).strip() + str(rd.get('Detail_Kind', '')).strip() + str(rd.get('KscPno', '')).strip()
                        counterpart = added_map.get(key_val) if key_val else None
                        if counterpart is not None:
                            c.font = Font(color='0000FF', bold=True)

            # 保存到临时文件
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
            wb.save(tmp_file.name)
            tmp_file.close()
            
            # 返回Excel文件，添加日期时间戳
            from flask import send_file
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            response = send_file(tmp_file.name, as_attachment=True, download_name=f'{comparison_file}_对比明细_{timestamp}.xlsx')
            
            # 清理临时文件
            import threading
            def cleanup():
                os.unlink(tmp_file.name)
            threading.Thread(target=cleanup).start()
            
            return response
        
        elif mode == 'split_zip':
            # 拆分模式：为每个文件各自生成一个Excel并打包为ZIP
            import zipfile

            tmp_dir = tempfile.mkdtemp(prefix='usage_multi_cmp_')
            excel_files = []

            # 工具函数：根据当前 added/deleted/modified 生成一个Excel并返回路径
            def _build_excel_file(added_rows, deleted_rows, modified_rows, comparison_name_str):
                wb_local = Workbook()
                ws_local = wb_local.active
                ws_local.title = 'UsageList变更记录'

                base_name = baseline_file.get("name", "未知").replace('_UsageList.xls', '').replace('_UsageList.xlsx', '')
                cmp_name = (comparison_name_str or '未知').replace('_UsageList.xls', '').replace('_UsageList.xlsx', '')
                header_line_local = f'{cmp_name}  --->  {base_name}   +{len(added_rows)}  -{len(deleted_rows)}'
                ws_local.append([header_line_local])
                last_col_l = get_column_letter(len(headers_out))
                ws_local.merge_cells(f'A1:{last_col_l}1')
                ws_local['A1'].font = Font(size=14, bold=True)
                ws_local['A1'].alignment = Alignment(horizontal='center', vertical='center')

                # 空一行
                ws_local.append([])
                # 表头
                ws_local.append(headers_out)
                for col in range(1, len(headers_out) + 1):
                    cell = ws_local.cell(row=3, column=col)
                    cell.font = Font(bold=True, color='FFFFFFFF')
                    cell.alignment = Alignment(horizontal='center')
                    cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
                    cell.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                # 构建复合键映射
                added_map = {str(r.get('Comp_Kind', '')).strip() + str(r.get('Detail_Kind', '')).strip() + str(r.get('KscPno', '')).strip(): r for r in (added_rows or [])}
                deleted_map = {str(r.get('Comp_Kind', '')).strip() + str(r.get('Detail_Kind', '')).strip() + str(r.get('KscPno', '')).strip(): r for r in (deleted_rows or [])}

                # 增加记录
                if added_rows:
                    ws_local.append(['增加记录'])
                    title_row_l = ws_local.max_row
                    ws_local[f'A{title_row_l}'].font = Font(size=12, bold=True)
                    ws_local[f'A{title_row_l}'].alignment = Alignment(horizontal='left')
                    for col in range(1, len(headers_out) + 1):
                        c = ws_local.cell(row=title_row_l, column=col)
                        c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                    for i, rd in enumerate(added_rows):
                        ws_local.append([rd.get(h, '') for h in headers_out])
                        row_num_l = title_row_l + 1 + i
                        for col in range(1, len(headers_out) + 1):
                            c = ws_local.cell(row=row_num_l, column=col)
                            c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                            c.fill = PatternFill(fill_type='solid', fgColor='FFE6FFED')
                            header_name_l = headers_out[col - 1]
                            key_val = str(rd.get('Comp_Kind', '')).strip() + str(rd.get('Detail_Kind', '')).strip() + str(rd.get('KscPno', '')).strip()
                            counterpart = deleted_map.get(key_val) if key_val else None
                            if counterpart is not None:
                                c.font = Font(color='0000FF', bold=True)

                # 删除记录
                if deleted_rows:
                    ws_local.append(['删除记录'])
                    title_row_l = ws_local.max_row
                    ws_local[f'A{title_row_l}'].font = Font(size=12, bold=True)
                    ws_local[f'A{title_row_l}'].alignment = Alignment(horizontal='left')
                    for col in range(1, len(headers_out) + 1):
                        c = ws_local.cell(row=title_row_l, column=col)
                        c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                    for i, rd in enumerate(deleted_rows):
                        ws_local.append([rd.get(h, '') for h in headers_out])
                        row_num_l = title_row_l + 1 + i
                        for col in range(1, len(headers_out) + 1):
                            c = ws_local.cell(row=row_num_l, column=col)
                            c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                            c.fill = PatternFill(fill_type='solid', fgColor='FFFDECEA')
                            header_name_l = headers_out[col - 1]
                            key_val = str(rd.get('Comp_Kind', '')).strip() + str(rd.get('Detail_Kind', '')).strip() + str(rd.get('KscPno', '')).strip()
                            counterpart = added_map.get(key_val) if key_val else None
                            if counterpart is not None:
                                c.font = Font(color='0000FF', bold=True)

                # 不再生成"修改记录"

                # 保存到临时文件
                temp_xlsx = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx', dir=tmp_dir)
                wb_local.save(temp_xlsx.name)
                temp_xlsx.close()
                return temp_xlsx.name

            # 为每个文件生成一个Excel
            b_id = baseline_file.get('id') or baseline_file.get('file_id') or 0
            for comp in (file_comparisons or []):
                a_details, d_details, m_details = compute_usage_diff_for_pair(b_id, comp.get('file_id'))
                # 展平为行
                added_list = []
                for d in a_details:
                    r = {}
                    r.update(d.get('context', {}))
                    r.update(d.get('values', {}))
                    added_list.append(r)
                deleted_list = []
                for d in d_details:
                    r = {}
                    r.update(d.get('context', {}))
                    r.update(d.get('values', {}))
                    deleted_list.append(r)
                cmp_name = comp.get('filename', '')
                xlsx_path = _build_excel_file(added_list, deleted_list, m_details, cmp_name)
                excel_files.append((cmp_name, xlsx_path))

            # 打包为ZIP
            zip_path = os.path.join(tmp_dir, 'UsageList多文件对比_拆分.zip')
            with zipfile.ZipFile(zip_path, 'w') as zipf:
                for filename, path in excel_files:
                    arcname = f"{filename.replace('_UsageList.xls', '').replace('_UsageList.xlsx', '')}_对比.xlsx"
                    zipf.write(path, arcname)
            
            # 读取ZIP文件内容
            with open(zip_path, 'rb') as f:
                zip_content = f.read()
            
            # 清理临时文件
            for _, path in excel_files:
                os.unlink(path)
            os.unlink(zip_path)
            os.rmdir(tmp_dir)
            
            # 返回ZIP文件
            from flask import send_file
            import io
            zip_io = io.BytesIO(zip_content)
            # 添加日期时间戳
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            return send_file(zip_io, as_attachment=True, download_name=f'UsageList多文件对比_拆分_{timestamp}.zip', mimetype='application/zip')
            
        # 默认模式：汇总模式
        else:
            # 构建汇总Excel
            wb = Workbook()
            ws = wb.active
            ws.title = 'UsageList多文件对比汇总'
            
            # 标题
            ws.append(['UsageList多文件对比汇总'])
            ws.merge_cells('A1:F1')
            ws['A1'].font = Font(size=16, bold=True)
            ws['A1'].alignment = Alignment(horizontal='center')
            
            # 基准文件信息
            ws.append(['基准文件:', baseline_file.get('name', '未知')])
            ws.append(['对比时间:', datetime.now().strftime('%Y-%m-%d %H:%M:%S')])
            ws.append([])
            
            # 汇总统计
            ws.append(['对比文件', '新增记录数', '删除记录数', '修改记录数'])
            for comp in file_comparisons:
                ws.append([comp.get('filename', ''), comp.get('added', 0), comp.get('removed', 0), comp.get('modified', 0)])
            
            # 保存到临时文件
            tmp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
            wb.save(tmp_file.name)
            tmp_file.close()
            
            # 返回Excel文件，添加日期时间戳
            from flask import send_file
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            response = send_file(tmp_file.name, as_attachment=True, download_name=f'UsageList多文件对比_汇总_{timestamp}.xlsx')
            
            # 清理临时文件
            import threading
            def cleanup():
                os.unlink(tmp_file.name)
            threading.Thread(target=cleanup).start()
            
            return response
        
    except Exception as e:
        current_app.logger.error(f"UsageList多文件对比下载失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '下载失败: ' + str(e)}), 500


@main_bp.route('/api/file_compare_multi/download', methods=['POST'])
@login_required
def api_file_compare_multi_download():
    try:
        data = request.get_json() or {}
        payload = data.get('payload')
        mode = data.get('mode', 'summary')  # 默认为汇总模式
        file_id = data.get('file_id')  # 单文件模式时使用
        
        if not payload:
            return jsonify({'success': False, 'error': '无数据'}), 400

        # 完全复制合并单元列对比的下载逻辑
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        import tempfile
        import os
        from datetime import datetime
        import urllib.parse
        
        # 定义按照用户要求的顺序（包含SetCode作为必需列）
        required_cols = [
            'SetCode','Circuit','Wire','WireCode','Color','SQ','Length_Cut2','Tml1','Tml2',
            'Strip1','Strip2','Rel1','Rel2','Seal1','Seal2','SubComp1','SubComp2',
            'M_ST1','M_ST2','M_ST3','M_ST4','Joint_Points','Remark1','Remark2'
        ]
        context_cols = ['SubPno','EoKsc','GroupNo','GroupOrder','SetCode','CutCode']
        headers_out = context_cols + required_cols

        # 工具函数：按"合并单元列对比"规则现算差异
        import pandas as pd
        from models.file_info import FileInfo

        def _normalize_setcode(val: str) -> str:
            try:
                s = str(val or '').strip()
                if not s:
                    return ''
                parts = s.split('_')
                if len(parts) >= 2:
                    return f"{parts[0]}_{parts[1]}_"
                return f"{parts[0]}_"
            except Exception:
                return ''

        def _norm_df(df: pd.DataFrame) -> pd.DataFrame:
            dfx = df.copy()
            for c in required_cols + context_cols:
                if c not in dfx.columns:
                    dfx[c] = ''
            dfx = dfx.fillna('').astype(str)
            for c in required_cols + context_cols:
                dfx[c] = dfx[c].map(lambda x: x.strip() if isinstance(x, str) else x)
            # 按照用户要求，直接合并所有必需列生成__key__
            dfx['__key__'] = dfx.apply(lambda row: '|'.join([row[c] for c in required_cols]), axis=1)
            return dfx

        def _row_by_key(df: pd.DataFrame, key: str):
            try:
                return df[df['__key__'] == key].iloc[0]
            except Exception:
                return None

        def compute_diff_for_pair(baseline_id: int, compare_id: int):
            base_file = db.session.get(FileInfo, int(baseline_id))
            cmp_file = db.session.get(FileInfo, int(compare_id))
            if not base_file or not cmp_file:
                return [], [], []
            bl_df = _norm_df(_read_file_data(base_file))
            cm_df = _norm_df(_read_file_data(cmp_file))
            bl_keys = set(bl_df['__key__'])
            cm_keys = set(cm_df['__key__'])
            added_keys = bl_keys - cm_keys
            deleted_keys = cm_keys - bl_keys
            common = bl_keys & cm_keys

            added_details = []
            for k in added_keys:
                r = _row_by_key(bl_df, k)
                if r is not None:
                    added_details.append({'key': k, 'context': {c: str(r[c]).strip() for c in context_cols}, 'values': {c: str(r[c]).strip() for c in required_cols}})
            removed_details = []
            for k in deleted_keys:
                r = _row_by_key(cm_df, k)
                if r is not None:
                    removed_details.append({'key': k, 'context': {c: str(r[c]).strip() for c in context_cols}, 'values': {c: str(r[c]).strip() for c in required_cols}})
            modified_details_local = []
            for k in common:
                br = _row_by_key(bl_df, k)
                cr = _row_by_key(cm_df, k)
                if br is None or cr is None:
                    continue
                diffs = []
                # 差异对比范围：必需列 + 上下文列（但 SubPno 不标识）
                compare_cols = required_cols + [c for c in context_cols if c != 'SubPno']
                for c in compare_cols:
                    if str(br[c]).strip() != str(cr[c]).strip():
                        diffs.append({'column': c, 'base_value': str(br[c]).strip(), 'file_value': str(cr[c]).strip()})
                if diffs:
                    modified_details_local.append({'key': k, 'file_id': int(compare_id), 'filename': cmp_file.filename, 'context': {cc: str(br[cc]).strip() for cc in context_cols}, 'differences': diffs})
            return added_details, removed_details, []

        # 获取数据
        baseline_file = payload.get('baseline_file', {})
        summary = payload.get('summary', {})
        file_comparisons = payload.get('file_comparisons', [])
        modified_details = payload.get('modified', [])
        
        # 根据模式处理数据
        if mode == 'single_file':
            # 单文件模式：后端现算该文件与基准文件的差异
            if not file_id:
                return jsonify({'success': False, 'error': '缺少文件ID'}), 400
            baseline_id = baseline_file.get('id') or baseline_file.get('file_id')
            if not baseline_id:
                return jsonify({'success': False, 'error': '缺少基准文件ID'}), 400

            # 即时计算差异
            added_details, removed_details, modified_details = compute_diff_for_pair(int(baseline_id), int(file_id))

            # 扁平化 added/removed 用于"增加/删除回路"两段
            added = []
            for detail in (added_details or []):
                row = {}
                row.update(detail.get('context', {}))
                row.update(detail.get('values', {}))
                added.append(row)

            deleted = []
            for detail in (removed_details or []):
                row = {}
                row.update(detail.get('context', {}))
                row.update(detail.get('values', {}))
                deleted.append(row)

            # 标题用数据库中的文件名（兜底用 payload 内的）
            try:
                from models.file_info import FileInfo
                cmp_obj = db.session.get(FileInfo, int(file_id))
                comparison_file = cmp_obj.filename if cmp_obj else None
            except Exception:
                comparison_file = None
            if not comparison_file:
                for comp in file_comparisons:
                    if comp.get('file_id') == file_id:
                        comparison_file = comp.get('filename')
                        break
            
        elif mode == 'split_zip':
            # 拆分模式：为每个文件各自生成一个Excel并打包为ZIP
            import tempfile
            import zipfile
            from openpyxl import Workbook
            from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
            from openpyxl.utils import get_column_letter

            tmp_dir = tempfile.mkdtemp(prefix='multi_cmp_')
            excel_files = []

            # 工具函数：根据当前 added/deleted/modified 生成一个Excel并返回路径
            def _build_excel_file(added_rows, deleted_rows, modified_rows, comparison_name_str):
                wb_local = Workbook()
                ws_local = wb_local.active
                ws_local.title = '回路变更记录'

                base_name = baseline_file.get("name", "未知").replace('_SubList.xls', '').replace('_SubList.xlsx', '')
                cmp_name = (comparison_name_str or '未知').replace('_SubList.xls', '').replace('_SubList.xlsx', '')
                header_line_local = f'{cmp_name}  --->  {base_name}   +{len(added_rows)}  -{len(deleted_rows)}'
                ws_local.append([header_line_local])
                last_col_l = get_column_letter(len(headers_out))
                ws_local.merge_cells(f'A1:{last_col_l}1')
                ws_local['A1'].font = Font(size=14, bold=True)
                ws_local['A1'].alignment = Alignment(horizontal='center', vertical='center')

                # 空一行
                ws_local.append([])
                # 表头
                ws_local.append(headers_out)
                for col in range(1, len(headers_out) + 1):
                    cell = ws_local.cell(row=3, column=col)
                    cell.font = Font(bold=True, color='FFFFFFFF')
                    cell.alignment = Alignment(horizontal='center')
                    cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
                    cell.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                # 构建 Circuit 映射
                added_map = {str(r.get('Circuit', '')).strip(): r for r in (added_rows or []) if str(r.get('Circuit', '')).strip()}
                deleted_map = {str(r.get('Circuit', '')).strip(): r for r in (deleted_rows or []) if str(r.get('Circuit', '')).strip()}

                # 增加回路
                if added_rows:
                    ws_local.append(['增加回路'])
                    title_row_l = ws_local.max_row
                    ws_local[f'A{title_row_l}'].font = Font(size=12, bold=True)
                    ws_local[f'A{title_row_l}'].alignment = Alignment(horizontal='left')
                    for col in range(1, len(headers_out) + 1):
                        c = ws_local.cell(row=title_row_l, column=col)
                        c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                    for i, rd in enumerate(added_rows):
                        ws_local.append([rd.get(h, '') for h in headers_out])
                        row_num_l = title_row_l + 1 + i
                        for col in range(1, len(headers_out) + 1):
                            c = ws_local.cell(row=row_num_l, column=col)
                            c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                            c.fill = PatternFill(fill_type='solid', fgColor='FFE6FFED')
                            header_name_l = headers_out[col - 1]
                            circuit_val = str(rd.get('Circuit', '')).strip()
                            counterpart = deleted_map.get(circuit_val) if circuit_val else None
                            if header_name_l == 'Circuit':
                                if counterpart is not None and circuit_val:
                                    c.font = Font(color='00800080', bold=True)
                                else:
                                    c.font = Font(color='0000FF', bold=True)
                            else:
                                if header_name_l == 'SubPno':
                                    c.font = Font()
                                elif counterpart is not None:
                                    left_val = str(rd.get(header_name_l, '')).strip()
                                    right_val = str(counterpart.get(header_name_l, '')).strip()
                                    c.font = Font(color='0000FF', bold=True) if left_val != right_val else Font()
                                else:
                                    c.font = Font()

                # 删除回路
                if deleted_rows:
                    ws_local.append(['删除回路'])
                    title_row_l = ws_local.max_row
                    ws_local[f'A{title_row_l}'].font = Font(size=12, bold=True)
                    ws_local[f'A{title_row_l}'].alignment = Alignment(horizontal='left')
                    for col in range(1, len(headers_out) + 1):
                        c = ws_local.cell(row=title_row_l, column=col)
                        c.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')
                        c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

                    for i, rd in enumerate(deleted_rows):
                        ws_local.append([rd.get(h, '') for h in headers_out])
                        row_num_l = title_row_l + 1 + i
                        for col in range(1, len(headers_out) + 1):
                            c = ws_local.cell(row=row_num_l, column=col)
                            c.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))
                            c.fill = PatternFill(fill_type='solid', fgColor='FFFDECEA')
                            header_name_l = headers_out[col - 1]
                            circuit_val = str(rd.get('Circuit', '')).strip()
                            counterpart = added_map.get(circuit_val) if circuit_val else None
                            if header_name_l == 'Circuit':
                                if counterpart is not None and circuit_val:
                                    c.font = Font(color='00800080', bold=True)
                                else:
                                    c.font = Font(color='0000FF', bold=True)
                            else:
                                if header_name_l == 'SubPno':
                                    c.font = Font()
                                elif counterpart is not None:
                                    left_val = str(rd.get(header_name_l, '')).strip()
                                    right_val = str(counterpart.get(header_name_l, '')).strip()
                                    c.font = Font(color='0000FF', bold=True) if left_val != right_val else Font()
                                else:
                                    c.font = Font()

                # 不再生成"修改回路"

                # 保存到临时文件
                temp_xlsx = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx', dir=tmp_dir)
                wb_local.save(temp_xlsx.name)
                temp_xlsx.close()
                return temp_xlsx.name

            # 为每个文件生成一个Excel
            b_id = baseline_file.get('id') or baseline_file.get('file_id') or 0
            for comp in (file_comparisons or []):
                a_details, d_details, m_details = compute_diff_for_pair(b_id, comp.get('file_id'))
                # 展平为行
                added_list = []
                for d in a_details:
                    r = {}
                    r.update(d.get('context', {}))
                    r.update(d.get('values', {}))
                    added_list.append(r)
                deleted_list = []
                for d in d_details:
                    r = {}
                    r.update(d.get('context', {}))
                    r.update(d.get('values', {}))
                    deleted_list.append(r)
                cmp_name = comp.get('filename', '')
                xlsx_path = _build_excel_file(added_list, deleted_list, m_details, cmp_name)
                excel_files.append((cmp_name, xlsx_path))

            # 打包为ZIP
            zip_tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
            zip_tmp_path = zip_tmp.name
            zip_tmp.close()
            with zipfile.ZipFile(zip_tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for cmp_name, xlsx_path in excel_files:
                    safe_name = (cmp_name or '对比结果').replace('/', '_').replace('\\', '_')
                    arcname = f"{safe_name.replace('_SubList.xls','').replace('_SubList.xlsx','')}_对比_{baseline_file.get('name','基准')}.xlsx"
                    zf.write(xlsx_path, arcname)

            # 清理单个xlsx
            for _, p in excel_files:
                try:
                    os.unlink(p)
                except Exception:
                    pass

            # 发送ZIP（读入内存后删除临时文件，避免提前删除导致客户端下载失败）
            from io import BytesIO
            with open(zip_tmp_path, 'rb') as f:
                zip_bytes = f.read()
            try:
                os.unlink(zip_tmp_path)
            except Exception:
                pass
            # 添加日期时间戳
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            return send_file(BytesIO(zip_bytes), as_attachment=True, download_name=f'SubList多文件对比_拆分_{timestamp}.zip', mimetype='application/zip')

        elif mode == 'overview':
            # 数据汇总模式：导出表格中的"新增/减少/回路合并"统计
            from openpyxl import Workbook
            wb = Workbook()
            ws = wb.active
            ws.title = '数据汇总'
            ws.append(['对比', '新增', '减少', '回路合并'])
            # 表头样式
            for col in range(1, 5):
                cell = ws.cell(row=1, column=col)
                cell.font = Font(bold=True, color='FFFFFFFF')
                cell.alignment = Alignment(horizontal='center')
                cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
                cell.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

            # 填充每行
            for comp in (file_comparisons or []):
                clean_base = baseline_file.get('name','').replace('_SubList.xls','').replace('_SubList.xlsx','')
                clean_cmp = (comp.get('filename','') or '').replace('_SubList.xls','').replace('_SubList.xlsx','')
                title = f"{clean_cmp} VS {clean_base}"
                ws.append([title, comp.get('added',0), comp.get('removed',0), comp.get('added',0)+comp.get('removed',0)])

            # 边框
            max_row = ws.max_row
            for r in range(2, max_row+1):
                for c in range(1, 4+1):
                    cell = ws.cell(row=r, column=c)
                    cell.border = Border(top=Side(style='thin'), left=Side(style='thin'), bottom=Side(style='thin'), right=Side(style='thin'))

            # 自动列宽
            try:
                from openpyxl.utils import get_column_letter as _gcl
                col_max = [0,0,0,0]
                for r in range(1, ws.max_row+1):
                    for c in range(1, 4+1):
                        val = str(ws.cell(row=r, column=c).value or '')
                        if len(val) > col_max[c-1]:
                            col_max[c-1] = len(val)
                for i, m in enumerate(col_max, start=1):
                    ws.column_dimensions[_gcl(i)].width = min(max(m + 2, 10), 60)
            except Exception:
                pass

            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
            wb.save(tmp.name)
            tmp.close()
            from io import BytesIO
            with open(tmp.name, 'rb') as f:
                data_bytes = f.read()
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
            # 添加日期时间戳
            from datetime import datetime
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            return send_file(BytesIO(data_bytes), as_attachment=True, download_name=f'SubList多文件对比_数据汇总_{timestamp}.xlsx', mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        else:
            # 汇总模式：若payload没有明细则现算
            added_raw = payload.get('added', [])
            deleted_raw = payload.get('removed', [])
            modified_details = []

            if not added_raw and not deleted_raw:
                b_id = baseline_file.get('id') or baseline_file.get('file_id') or 0
                all_added, all_deleted = [], []
                for comp in (file_comparisons or []):
                    a_details, d_details, m_details = compute_diff_for_pair(b_id, comp.get('file_id'))
                    all_added.extend(a_details)
                    all_deleted.extend(d_details)
                    modified_details.extend(m_details)
                # 按 key 去重
                seen_a, seen_d = set(), set()
                added_raw = [x for x in all_added if not (x['key'] in seen_a or seen_a.add(x['key']))]
                deleted_raw = [x for x in all_deleted if not (x['key'] in seen_d or seen_d.add(x['key']))]

            # 转为扁平行（只保留需要字段）
            added = []
            for d in added_raw:
                row = {}
                row.update(d.get('context', {}))
                row.update(d.get('values', {}))
                added.append({col: row.get(col, '') for col in headers_out})
            deleted = []
            for d in deleted_raw:
                row = {}
                row.update(d.get('context', {}))
                row.update(d.get('values', {}))
                deleted.append({col: row.get(col, '') for col in headers_out})
            comparison_file = None

        # 构建 Circuit 到行数据的映射，便于"增删两边同时存在时"的对比与着色
        try:
            added_by_circuit = {}
            for _r in (added or []):
                cval = str(_r.get('Circuit', '')).strip()
                if cval and cval not in added_by_circuit:
                    added_by_circuit[cval] = _r
            deleted_by_circuit = {}
            for _r in (deleted or []):
                cval = str(_r.get('Circuit', '')).strip()
                if cval and cval not in deleted_by_circuit:
                    deleted_by_circuit[cval] = _r
        except Exception:
            added_by_circuit = {}
            deleted_by_circuit = {}

        # 创建工作簿
        wb = Workbook()
        
        # 创建工作表 - 完全按照前端逻辑
        ws = wb.active
        ws.title = '回路变更记录'

        # 顶部总览标题行 - 根据模式构建正确的文件名显示
        baseline_name = baseline_file.get("name", "未知").replace('_SubList.xls', '').replace('_SubList.xlsx', '')
        
        if mode == 'single_file':
            # 单文件模式：显示具体的对比文件名
            comparison_name = comparison_file.replace('_SubList.xls', '').replace('_SubList.xlsx', '') if comparison_file else "未知"
            # 与前端展示保持一致：对比文件在前，基准在后
            header_line = f'{comparison_name}  --->  {baseline_name}   +{len(added)}  -{len(deleted)}'
        else:
            # 汇总模式：显示所有对比文件
            comparison_names = []
            for comp in file_comparisons:
                comp_name = comp.get('filename', '').replace('_SubList.xls', '').replace('_SubList.xlsx', '')
                if comp_name:
                    comparison_names.append(comp_name)
            
            if comparison_names:
                if len(comparison_names) == 1:
                    header_line = f'{comparison_names[0]}  --->  {baseline_name}   +{len(added)}  -{len(deleted)}'
                else:
                    # 多个文件时，显示文件数量
                    header_line = f'{len(comparison_names)}个文件对比  --->  {baseline_name}   +{len(added)}  -{len(deleted)}'
            else:
                header_line = f'多文件对比  --->  {baseline_name}   +{len(added)}  -{len(deleted)}'
        
        ws.append([header_line])
        
        # 合并第一行的所有列并设置样式
        from openpyxl.utils import get_column_letter
        last_col = get_column_letter(len(headers_out))
        ws.merge_cells(f'A1:{last_col}1')  # 合并A1到最后一列
        ws['A1'].font = Font(size=14, bold=True)
        ws['A1'].alignment = Alignment(horizontal='center', vertical='center')
        
        # 空一行
        ws.append([])

        # 添加表头（仅一次，供"增加/删除"两段共用）
        ws.append(headers_out)
        # 设置表头样式 - 完全按照前端样式
        for col in range(1, len(headers_out) + 1):
            cell = ws.cell(row=3, column=col)
            cell.font = Font(bold=True, color='FFFFFFFF')
            cell.alignment = Alignment(horizontal='center')
            cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')  # 深蓝
            cell.border = Border(
                top=Side(style='thin'),
                left=Side(style='thin'),
                bottom=Side(style='thin'),
                right=Side(style='thin')
            )

        # 增加回路（仅在有数据时渲染）
        if added:
            # 添加"增加回路"标题行
            ws.append(['增加回路'])
            title_row = ws.max_row
            ws[f'A{title_row}'].font = Font(size=12, bold=True)
            ws[f'A{title_row}'].alignment = Alignment(horizontal='left')
            # 设置标题行背景色：浅黄色
            for col in range(1, len(headers_out) + 1):
                cell = ws.cell(row=title_row, column=col)
                cell.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')  # 浅黄
                cell.border = Border(
                    top=Side(style='thin'),
                    left=Side(style='thin'),
                    bottom=Side(style='thin'),
                    right=Side(style='thin')
                )
            
            # 添加增加回路数据
            for i, row_data in enumerate(added):
                ws.append([row_data.get(h, '') for h in headers_out])
                # 设置数据行样式
                row_num = title_row + 1 + i
                for col in range(1, len(headers_out) + 1):
                    cell = ws.cell(row=row_num, column=col)
                    cell.border = Border(
                        top=Side(style='thin'),
                        left=Side(style='thin'),
                        bottom=Side(style='thin'),
                        right=Side(style='thin')
                    )
                    # 新增行背景色：浅绿色
                    cell.fill = PatternFill(fill_type='solid', fgColor='FFE6FFED')
                    
                    # 关键列/差异标识逻辑 + Circuit 同时存在于增删两边时的紫色与蓝色对比
                    header_name = headers_out[col - 1]
                    circuit_value = str(row_data.get('Circuit', '')).strip()
                    counterpart = deleted_by_circuit.get(circuit_value) if circuit_value else None
                    if header_name == 'Circuit':
                        if counterpart is not None and circuit_value:
                            # 同时存在于增加与删除：紫色
                            cell.font = Font(color="00800080", bold=True)  # 紫色粗体（ARGB: 00 80 00 80 → openpyxl用RGB）
                        else:
                            # 普通关键列：蓝色
                            cell.font = Font(color="0000FF", bold=True)
                    else:
                        if header_name == 'SubPno':
                            # SubPno 不需要标识
                            cell.font = Font()
                            continue
                        if counterpart is not None:
                            # 双边均有该 Circuit，对比同列
                            left_val = str(row_data.get(header_name, '')).strip()
                            right_val = str(counterpart.get(header_name, '')).strip()
                            if left_val != right_val:
                                cell.font = Font(color="0000FF", bold=True)  # 差异置蓝
                            else:
                                cell.font = Font()
                        else:
                            # 单边新增行：保持默认
                            cell.font = Font()

        # 删除回路（仅在有数据时渲染）
        if deleted:
            # 添加"删除回路"标题行
            ws.append(['删除回路'])
            title_row = ws.max_row
            ws[f'A{title_row}'].font = Font(size=12, bold=True)
            ws[f'A{title_row}'].alignment = Alignment(horizontal='left')
            # 设置标题行背景色：浅黄色
            for col in range(1, len(headers_out) + 1):
                cell = ws.cell(row=title_row, column=col)
                cell.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')  # 浅黄
                cell.border = Border(
                    top=Side(style='thin'),
                    left=Side(style='thin'),
                    bottom=Side(style='thin'),
                    right=Side(style='thin')
                )
            
            # 添加删除回路数据
            for i, row_data in enumerate(deleted):
                ws.append([row_data.get(h, '') for h in headers_out])
                # 设置数据行样式
                row_num = title_row + 1 + i
                for col in range(1, len(headers_out) + 1):
                    cell = ws.cell(row=row_num, column=col)
                    cell.border = Border(
                        top=Side(style='thin'),
                        left=Side(style='thin'),
                        bottom=Side(style='thin'),
                        right=Side(style='thin')
                    )
                    # 删除行背景色：浅红色
                    cell.fill = PatternFill(fill_type='solid', fgColor='FFFDECEA')
                    
                    # 关键列/差异标识逻辑 + Circuit 同时存在于增删两边时的紫色与蓝色对比
                    header_name = headers_out[col - 1]
                    circuit_value = str(row_data.get('Circuit', '')).strip()
                    counterpart = added_by_circuit.get(circuit_value) if circuit_value else None
                    if header_name == 'Circuit':
                        if counterpart is not None and circuit_value:
                            # 同时存在于增加与删除：紫色
                            cell.font = Font(color="00800080", bold=True)
                        else:
                            # 普通关键列：蓝色
                            cell.font = Font(color="0000FF", bold=True)
                    else:
                        if header_name == 'SubPno':
                            # SubPno 不需要标识
                            cell.font = Font()
                            continue
                        if counterpart is not None:
                            # 双边均有该 Circuit，对比同列
                            left_val = str(row_data.get(header_name, '')).strip()
                            right_val = str(counterpart.get(header_name, '')).strip()
                            if left_val != right_val:
                                cell.font = Font(color="0000FF", bold=True)  # 差异置蓝
                            else:
                                cell.font = Font()
                        else:
                            # 单边删除行：保持默认
                            cell.font = Font()
        # 不再生成"修改回路"
        if False and modified_details:
            ws.append(['修改回路'])
            title_row = ws.max_row
            ws[f'A{title_row}'].font = Font(size=12, bold=True)
            ws[f'A{title_row}'].alignment = Alignment(horizontal='left')
            # 设置标题行背景色：浅黄色
            for col in range(1, len(headers_out) + 1):
                cell = ws.cell(row=title_row, column=col)
                cell.fill = PatternFill(fill_type='solid', fgColor='FFF9F2CC')  # 浅黄
                cell.border = Border(
                    top=Side(style='thin'),
                    left=Side(style='thin'),
                    bottom=Side(style='thin'),
                    right=Side(style='thin')
                )
            
            # 创建修改回路的表头，按照合并单元列对比的格式
            modified_headers = []
            for header in headers_out:
                modified_headers.append(f"{header} (基准)")
                modified_headers.append(f"{header} (对比)")
                modified_headers.append("差异")
            
            ws.append(modified_headers)
            
            # 设置表头样式
            header_row = ws.max_row
            for col in range(1, len(modified_headers) + 1):
                cell = ws.cell(row=header_row, column=col)
                cell.font = Font(bold=True, color='FFFFFFFF')
                cell.alignment = Alignment(horizontal='center')
                cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')  # 深蓝
                cell.border = Border(
                    top=Side(style='thin'),
                    left=Side(style='thin'),
                    bottom=Side(style='thin'),
                    right=Side(style='thin')
                )
            
            # 添加修改回路数据
            for detail in modified_details:
                # 构建基础行数据
                base_row = {}
                base_row.update(detail.get('context', {}))
                
                # 创建差异字典，便于快速查找
                diff_dict = {diff['column']: diff for diff in detail.get('differences', [])}
                
                # 创建对比行数据
                row_data = []
                for header in headers_out:
                    if header == 'SubPno':
                        # SubPno 不标识差异：显示值但不出 ✓/✗
                        base_val = base_row.get(header, '')
                        file_val = diff_dict.get(header, {}).get('file_value', base_val)
                        row_data.extend([base_val, file_val, ""])  # 差异列留空
                    else:
                        if header in diff_dict:
                            # 有差异的列
                            diff = diff_dict[header]
                            row_data.extend([diff['base_value'], diff['file_value'], "✗"])
                        else:
                            # 无差异的列
                            value = base_row.get(header, '')
                            row_data.extend([value, value, "✓"])
                
                ws.append(row_data)
                
                # 设置数据行样式
                row_num = ws.max_row
                for col in range(1, len(row_data) + 1):
                    cell = ws.cell(row=row_num, column=col)
                    cell.border = Border(
                        top=Side(style='thin'),
                        left=Side(style='thin'),
                        bottom=Side(style='thin'),
                        right=Side(style='thin')
                    )
                    
                    # 差异标记样式
                    if col % 3 == 0:  # 每3列中的第3列是差异标记
                        if cell.value == "✗":
                            cell.font = Font(color="FF0000", bold=True)  # 红色粗体
                            
                            # 标记对应的两个值单元格
                            val_col1 = col - 2  # 基准值列
                            val_col2 = col - 1  # 对比值列
                            
                            # 获取对应的列名
                            header_idx = (col - 1) // 3
                            header_name = headers_out[header_idx]
                            
                            # 标记有差异的值单元格
                            ws.cell(row=row_num, column=val_col1).font = Font(color="FF0000", bold=True)
                            ws.cell(row=row_num, column=val_col2).font = Font(color="FF0000", bold=True)
                            
                            # 如果是关键列（如Circuit），保持蓝色字体
                            if header_name == 'Circuit':
                                ws.cell(row=row_num, column=val_col1).font = Font(color="0000FF", bold=True, underline="single")
                                ws.cell(row=row_num, column=val_col2).font = Font(color="0000FF", bold=True, underline="single")
                        else:
                            # 相同标记使用默认样式
                            cell.font = Font()
                    else:
                        # 获取对应列的表头
                        header_idx = (col - 1) // 3
                        if header_idx < len(headers_out):
                            header_name = headers_out[header_idx]
                            if header_name == 'Circuit':
                                cell.font = Font(color="0000FF", bold=True)  # 蓝色粗体
                            else:
                                cell.font = Font()  # 默认样式

        # 保存并返回
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        wb.save(temp_file.name)
        temp_file.close()

        # 处理中文文件名编码问题
        filename = f'SubList多文件对比_{datetime.now().strftime("%Y%m%d%H%M%S")}.xlsx'
        
        response = send_file(
            temp_file.name,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=urllib.parse.quote(filename)
        )
        
        # 清理临时文件
        try:
            os.unlink(temp_file.name)
        except OSError:
            pass
            
        return response

    except Exception as e:
        current_app.logger.error(f"多文件对比下载失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '下载失败'}), 500


# ==================== 操作记录相关路由 ====================

@main_bp.route('/operation_logs')
@login_required
def operation_logs():
    """操作记录页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    
    # 检查权限：只有管理员可以查看操作记录
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        flash('你没有查看操作记录的权限', 'error')
        return forbidden_page('你没有查看操作记录的权限')
    
    return render_template('operation_logs.html')
@main_bp.route('/api/operation_logs', methods=['GET'])
@login_required
def api_operation_logs():
    """获取操作记录列表"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 检查权限：只有管理员可以查看操作记录
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        return jsonify({'success': False, 'error': '没有查看操作记录的权限'}), 403
    
    try:
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 25, type=int)
        operation_type = request.args.get('operation_type', '')
        user_id = request.args.get('user_id', '')
        start_date = request.args.get('start_date', '')
        end_date = request.args.get('end_date', '')
        
        # 使用模型查询，利用已配置的lazy='joined'加载用户信息
        query = OperationLog.query
        
        # 应用筛选条件
        if operation_type:
            query = query.filter(OperationLog.operation_type == operation_type)
        if user_id:
            try:
                query = query.filter(OperationLog.user_id == int(user_id))
            except Exception:
                pass
        # 日期区间筛选（将字符串转换为 datetime）
        from datetime import datetime, time
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                start_dt = datetime.combine(start_dt.date(), time.min)
                query = query.filter(OperationLog.created_at >= start_dt)
            except Exception:
                pass
        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                end_dt = datetime.combine(end_dt.date(), time.max)
                query = query.filter(OperationLog.created_at <= end_dt)
            except Exception:
                pass
        
        # 按时间倒序排列
        query = query.order_by(OperationLog.created_at.desc())
        
        # 分页
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        
        # 转换为字典格式
        logs = []
        for log in pagination.items:
            d = log.to_dict()
            d.pop('file_path', None)  # 移除文件路径以减小响应
            logs.append(d)
        
        return jsonify({
            'success': True,
            'logs': logs,
            'pagination': {
                'page': pagination.page,
                'per_page': pagination.per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next,
                'total_pages': pagination.pages
            }
        })
        
    except Exception as e:
        current_app.logger.error(f"获取操作记录失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '获取操作记录失败'}), 500


@main_bp.route('/api/operation_logs/<int:log_id>/details', methods=['GET'])
@login_required
def api_operation_log_details(log_id):
    """获取单条操作记录的明细（用于批量操作的文件名列表），支持分页与查询"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    # 只有管理员可以查看操作记录
    user = db.session.get(UserInfo, session['user_id'])
    if not user or not user.is_admin:
        return jsonify({'success': False, 'error': '没有查看操作记录的权限'}), 403

    try:
        log = db.session.get(OperationLog, log_id)
        if not log:
            return jsonify({'success': False, 'error': '记录不存在'}), 404

        # 仅对批量类操作或文件名集合提供明细
        raw = log.file_path or ''
        
        
        # 检查是否是新的分类格式（SUCCESS:和DUPLICATE:）
        if 'SUCCESS:' in raw or 'DUPLICATE:' in raw:
            # 分类显示成功和重复文件
            items = []
            
            # 优先尝试新格式（|||分隔符）
            if '|||' in raw:
                parts = raw.split('|||')
                for part in parts:
                    if part.startswith('SUCCESS:'):
                        success_files = part[8:].split('||') if part[8:] else []
                        items.extend([f"✅ {f}" for f in success_files if f.strip()])
                    elif part.startswith('DUPLICATE:'):
                        duplicate_files = part[10:].split('||') if part[10:] else []
                        items.extend([f"⚠️ {f}" for f in duplicate_files if f.strip()])
            else:
                # 兼容旧格式（|分隔符）- 需要特殊处理
                # 找到SUCCESS:和DUPLICATE:的位置
                success_start = raw.find('SUCCESS:')
                duplicate_start = raw.find('DUPLICATE:')
                
                # 处理SUCCESS部分
                if success_start != -1:
                    if duplicate_start != -1:
                        # 有SUCCESS和DUPLICATE两部分
                        success_part = raw[success_start:duplicate_start]
                    else:
                        # 只有SUCCESS部分
                        success_part = raw[success_start:]
                    
                    if success_part.startswith('SUCCESS:'):
                        success_files = success_part[8:].split('||') if success_part[8:] else []
                        items.extend([f"✅ {f}" for f in success_files if f.strip()])
                
                # 处理DUPLICATE部分
                if duplicate_start != -1:
                    if success_start != -1:
                        # 有SUCCESS和DUPLICATE两部分
                        duplicate_part = raw[duplicate_start:]
                    else:
                        # 只有DUPLICATE部分
                        duplicate_part = raw[duplicate_start:]
                    
                    if duplicate_part.startswith('DUPLICATE:'):
                        duplicate_files = duplicate_part[10:].split('||') if duplicate_part[10:] else []
                        items.extend([f"⚠️ {f}" for f in duplicate_files if f.strip()])
        else:
            # 旧格式：兼容处理
            import re as _re
            parts = []
            if '||' in raw:
                tmp = [p.strip() for p in raw.split('||')]
                tmp = [_re.sub(r'\|+$', '', p) for p in tmp]
                parts = [p for p in tmp if p]
            else:
                # 若原始字符串中含有竖线并且看起来像"结构化一行"（至少4个竖线=5列），则按单行返回，避免被'|'误切成多条
                if ('|' in raw) and (raw.count('|') >= 4):
                    parts = [raw.strip().strip('|')]
                else:
                    tmp = raw.replace('\r', '\n')
                    for sep in ['\n', ',', ';', '|']:
                        if sep in tmp and not parts:
                            parts = [p.strip() for p in tmp.split(sep)]
                    if not parts and tmp.strip():
                        parts = [tmp.strip()]

            # 过滤空元素
            items = [p for p in parts if p]

        # 查询参数：分页与关键字搜索
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        # 对于文件上传类型，允许更大的per_page值
        if log.operation_type == 'upload_file' or '文件上传' in (log.operation_desc or ''):
            per_page = max(10, min(per_page, 10000))  # 文件上传允许最多10000条
        else:
            per_page = max(10, min(per_page, 200))  # 其他类型保持200条限制
        keyword = request.args.get('q', '').strip()

        if keyword:
            lowered = keyword.lower()
            items = [it for it in items if lowered in it.lower()]

        # 性能优化：支持 all=1 一次返回全部明细（文件上传场景），避免前端先请求 per_page=25 再请求 per_page=10000 的两次往返。
        if request.args.get('all', '').strip() in ('1', 'true', 'True'):
            per_page = max(len(items), 1) if items else 1
        total = len(items)
        pages = (total + per_page - 1) // per_page if per_page > 0 else 1
        page = max(1, min(page, max(pages, 1)))
        start = (page - 1) * per_page
        end = start + per_page
        page_items = items[start:end]

        return jsonify({
            'success': True,
            'log_id': log.id,
            'operation_type': log.operation_type,
            'operation_desc': log.operation_desc,
            'items': page_items,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': total,
                'pages': pages,
                'has_prev': page > 1,
                'has_next': page < pages
            }
        })
    except Exception as e:
        current_app.logger.error(f"获取操作记录明细失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '获取明细失败'}), 500


@main_bp.route('/api/operation_logs/stats', methods=['GET'])
@login_required
def api_operation_logs_stats():
    """获取操作记录统计信息"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    # 检查权限：只有管理员可以查看操作记录
    user = db.session.get(UserInfo, session['user_id'])
    if not user.is_admin:
        return jsonify({'success': False, 'error': '没有查看操作记录的权限'}), 403
    
    try:
        from datetime import datetime
        # 简单内存缓存（60s）
        cache_key = 'op_stats_cache'
        cache_ts_key = 'op_stats_cache_ts'
        now = datetime.now()
        cached = current_app.config.get(cache_key)
        cached_ts = current_app.config.get(cache_ts_key)
        if cached and cached_ts and (now - cached_ts).total_seconds() < 60:
            return jsonify({'success': True, 'stats': cached})

        # 计算统计
        today = datetime.now().date()
        total_operations = OperationLog.query.count()
        today_operations = OperationLog.query.filter(OperationLog.created_at >= today).count()
        upload_count = OperationLog.query.filter(OperationLog.operation_type.in_(['upload_file'])).count()
        download_count = OperationLog.query.filter(OperationLog.operation_type.in_(['download_file', 'download_result'])).count()
        delete_count = OperationLog.query.filter(OperationLog.operation_type.in_(['delete_file', 'batch_delete_file'])).count()

        stats = {
            'total_operations': total_operations,
            'today_operations': today_operations,
            'upload_count': upload_count,
            'download_count': download_count,
            'delete_count': delete_count
        }
        current_app.config[cache_key] = stats
        current_app.config[cache_ts_key] = now
        return jsonify({'success': True, 'stats': stats})

    except Exception as e:
        current_app.logger.error(f"获取操作记录统计失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '获取统计信息失败'}), 500


@main_bp.route('/api/users', methods=['GET'])
@login_required
def api_users():
    """获取用户列表（用于操作记录筛选）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    
    try:
        users = UserInfo.query.filter_by(is_active=True).all()
        user_list = []
        for user in users:
            user_list.append({
                'id': user.id,
                'username': user.username,
                'is_admin': user.is_admin
            })
        
        return jsonify({
            'success': True,
            'users': user_list
        })
        
    except Exception as e:
        current_app.logger.error(f"获取用户列表失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '获取用户列表失败'}), 500


# 统一汇总上传日志（AJAX分片上传完成后由前端一次性调用）
@main_bp.route('/api/upload_logs/finalize', methods=['POST'])
@csrf_exempt_route
def finalize_upload_logs():
    try:
        current_app.logger.info("finalize_upload_logs 被调用")
        if 'user_id' not in session:
            return jsonify({'success': False, 'error': '未登录'}), 401
        
        user_id = session['user_id']  # 获取用户ID
        data = request.get_json(silent=True) or {}
        current_app.logger.info(f"finalize_upload_logs 接收数据: {data}")
        filenames = data.get('filenames') or []
        total = data.get('total')
        success_count = data.get('success_count', 0)
        duplicate_count = data.get('duplicate_count', 0)
        failed_count = data.get('failed_count', 0)
        success_files_data = data.get('success_files', [])
        duplicate_files = data.get('duplicate_files', [])
        failed_files = data.get('failed_files', [])
        
        try:
            total_int = int(total) if total is not None else None
        except Exception:
            total_int = None
        # 去重并过滤空白
        filenames = [str(x).strip() for x in filenames if x and str(x).strip()]
        filenames = sorted(set(filenames))
        
        # 提取成功和重复文件名（在if块外定义，避免作用域问题）
        success_files = []
        duplicate_files_list = []
        
        # 处理成功文件
        if success_files_data:
            success_files = [f for f in success_files_data if f and str(f).strip()]
        
        # 处理重复文件
        if duplicate_files:
            duplicate_files_list = [f for f in duplicate_files if f and str(f).strip()]
        
        
        if filenames:
            try:
                log_batch_file_upload(
                    filenames, 
                    count=total_int if (isinstance(total_int, int) and total_int > 0) else len(filenames),
                    success_files=success_files,
                    duplicate_files=duplicate_files_list,
                    user_id=user_id
                )
            except Exception as e:
                current_app.logger.warning(f'finalize_upload_logs 写入失败: {e}')
        
        # 设置统一的flash消息
        msg_success = f'成功上传 {success_count} 个文件' if success_count > 0 else ''
        
        # 重复文件显示逻辑：1个文件显示文件名，多个文件显示"第一个文件等X个文件"
        if duplicate_count > 0:
            if duplicate_count == 1:
                first_file = duplicate_files_list[0] if duplicate_files_list and duplicate_files_list[0] else "未知文件"
                msg_warning = f'跳过重复文件: {first_file}'
            else:
                first_file = duplicate_files_list[0] if duplicate_files_list and duplicate_files_list[0] else "未知文件"
                msg_warning = f'跳过重复文件: {first_file} 等{duplicate_count}个文件'
        else:
            msg_warning = ''
        
        msg_error = f'上传失败 {failed_count} 个文件' if failed_count > 0 else ''
        
        # 设置flash消息
        if msg_error:
            flash(msg_error, 'error')
        elif msg_warning:
            flash('；'.join(filter(None, [msg_success, msg_warning])), 'warning')
        elif msg_success:
            flash(msg_success, 'success')
        
        return jsonify({'success': True, 'logged_count': len(filenames)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== 资材入库模块 ====================
# 页面1：资材每日入库（明细上传/录入/筛选/导出）
# 页面2：资材入库（扫码/搜索 → 填数量 → 提交）

# 资材入库模块权限分组常量（用于页面与接口鉴权）
_MIB_PAGES = {
    'material_inbound_daily': 'can_material_inbound_daily',
    'material_inbound': 'can_material_inbound',
}

# Excel 列头别名 → 标准字段（自动识别上传模板）
_MIB_COLUMN_ALIASES = {
    'part_no': {'京信pno', '京信pno号', '资材p/n', '资材pn', 'p/n', 'pn', 'partno', 'part_no', 'partnumber', '部品番号', '品番', '零件号'},
    'quantity': {'数量', 'qty', 'quantity', '入库数量', '计划数量'},
    'location': {'位置', '库位', '货架', '货架号', 'location', '储位'},
    'remark': {'备注', 'remark', '说明', 'note', 'comment'},
    'inbound_date': {'入库日期', '入库日', 'inbounddate', 'inbound_date', '计划入库日期'},
}


def _mib_normalize_header(name) -> str:
    """列头归一化：去空格、转小写、去全角括号差异，便于别名匹配"""
    return re.sub(r'[\s\-_/]+', '', str(name or '')).lower()


def _mib_map_columns(columns):
    """将上传文件的列头映射为标准字段，返回 {标准字段: 原始列名}"""
    mapping = {}
    normalized = {_mib_normalize_header(c): c for c in columns}
    for field, aliases in _MIB_COLUMN_ALIASES.items():
        for alias in aliases:
            key = _mib_normalize_header(alias)
            if key in normalized:
                mapping[field] = normalized[key]
                break
    return mapping


def _mib_parse_qty(value):
    """数量解析：兼容 '1,000'、'10.0'、空值；失败返回 None"""
    try:
        s = str(value or '').strip().replace(',', '').replace('，', '')
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _mib_parse_date(value):
    """入库日期解析：兼容 'YYYY-MM-DD'、'YYYY/MM/DD'、'YYYY.MM.DD'、Excel 序列号；失败返回 None"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    s = s.split(' ')[0].split('T')[0].replace('/', '-').replace('.', '-')
    for fmt in ('%Y-%m-%d', '%Y-%m', '%Y%m%d'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:  # Excel 日期序列号（1900 基准）
        return (datetime(1899, 12, 30) + timedelta(days=int(float(s)))).date()
    except Exception:
        return None


def _mib_operator_names(user_ids):
    """批量查询用户名，避免逐行查库（返回 {user_id: username}）"""
    ids = {int(i) for i in user_ids if i}
    if not ids:
        return {}
    rows = UserInfo.query.filter(UserInfo.id.in_(ids)).all()
    return {u.id: u.username for u in rows}


def _mib_export_row(it, names):
    """构建资材每日入库导出的单行数据（供流式写出复用）"""
    return [
        it.plan_date.strftime('%Y-%m-%d') if it.plan_date else '',
        it.part_no, int(it.quantity or 0), it.location or '', it.remark or '',
        {'done': '已入库', 'short': '入库不足'}.get(it.status, '未入库'),
        int(it.inbound_qty or 0) if it.status in ('done', 'short') else '',
        it.inbound_date.strftime('%Y-%m-%d') if it.inbound_date else '',
        format_utc8_time(it.inbound_at) if it.inbound_at else '',
        names.get(it.inbound_by, '') if it.inbound_by else '',
    ]


@main_bp.route('/material/inbound_daily')
@login_required
def material_inbound_daily():
    """资材每日入库页面（页面1）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_inbound_daily'):
        return forbidden_page('您没有资材每日入库页面的访问权限')
    # 显式计算各操作权限并传入模板，避免依赖 session 中可能缺失的权限键（如管理员 is_admin 未同步）
    uid = session.get('user_id')
    perms = {
        'is_admin': bool(session.get('is_admin')),
        'can_upload': check_permission(uid, 'can_material_inbound_daily_upload'),
        'can_add': check_permission(uid, 'can_material_inbound_daily_add'),
        'can_edit': check_permission(uid, 'can_material_inbound_daily_edit'),
        'can_delete': check_permission(uid, 'can_material_inbound_daily_delete'),
        'can_download': check_permission(uid, 'can_material_inbound_daily_download'),
    }
    return render_template('material_inbound_daily.html', **perms)


@main_bp.route('/material/inbound')
@login_required
def material_inbound():
    """资材入库页面（页面2）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_inbound'):
        return forbidden_page('您没有资材入库页面的访问权限')
    return render_template('material_inbound.html')


@main_bp.route('/material/inbound_daily/data', methods=['GET'])
@login_required
def material_inbound_daily_data():
    """每日入库明细查询：按日期（默认当天）+ 状态 + 关键字筛选"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_daily'):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        plan_date = request.args.get('plan_date', '').strip()
        plan_date_end = request.args.get('plan_date_end', '').strip()
        status = request.args.get('status', '').strip()
        keyword = request.args.get('keyword', '').strip()

        query = MaterialInboundDaily.query
        if plan_date:
            try:
                query = query.filter(MaterialInboundDaily.plan_date >= datetime.strptime(plan_date, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400
        if plan_date_end:
            try:
                query = query.filter(MaterialInboundDaily.plan_date <= datetime.strptime(plan_date_end, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'error': '结束日期格式应为 YYYY-MM-DD'}), 400
        if status in ('pending', 'short', 'done'):
            query = query.filter(MaterialInboundDaily.status == status)
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(MaterialInboundDaily.part_no.like(like),
                                        MaterialInboundDaily.location.like(like),
                                        MaterialInboundDaily.remark.like(like)))

        items = query.order_by(MaterialInboundDaily.part_no.asc(), MaterialInboundDaily.id.asc()).all()
        names = _mib_operator_names([it.inbound_by for it in items] + [it.created_by for it in items])

        done_count = sum(1 for it in items if it.status == 'done')
        short_count = sum(1 for it in items if it.status == 'short')
        return jsonify({
            'success': True,
            'items': [it.to_dict(names) for it in items],
            'summary': {
                'total': len(items),
                'done': done_count,
                'short': short_count,
                'pending': len(items) - done_count - short_count,
                'total_qty': sum(int(it.quantity or 0) for it in items),
                'done_qty': sum(int(it.inbound_qty or 0) for it in items if it.status in ('done', 'short')),
            }
        })
    except Exception as e:
        current_app.logger.error(f"资材每日入库查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/material/inbound_daily/upload', methods=['POST'])
@login_required
@csrf_exempt_route
def material_inbound_daily_upload():
    """Excel 上传每日入库明细（自动识别 P/N、数量、位置、备注、入库日期 列头）

    模板中的「入库日期」即明细的归属日（plan_date），必填且需为有效日期；
    同一文件可包含多个日期，按日期拆分为各自日期的清单。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_daily_upload'):
        return jsonify({'success': False, 'error': '您没有上传明细的权限'}), 403

    try:
        file = request.files.get('file')
        if file is None or not file.filename:
            return jsonify({'success': False, 'error': '未选择文件'}), 400

        # 覆盖模式：overwrite=同P/N覆盖数量；skip=同P/N跳过；add=同P/N累加数量（默认覆盖）
        mode = request.form.get('mode', 'overwrite').strip()

        df = read_and_convert_to_string(file)
        if df is None or df.empty:
            return jsonify({'success': False, 'error': '文件无有效数据'}), 400

        mapping = _mib_map_columns(df.columns)
        if 'part_no' not in mapping:
            return jsonify({'success': False, 'error': '未识别到「京信PNO」列，请检查表头'}), 400
        if 'quantity' not in mapping:
            return jsonify({'success': False, 'error': '未识别到「数量」列，请检查表头'}), 400
        if 'inbound_date' not in mapping:
            return jsonify({'success': False, 'error': '未识别到「入库日期」列，请使用最新模板并填写入库日期'}), 400

        # 按「入库日期」聚合：{日期: {P/N: {数量/位置/备注}}}，多日期文件自动拆分
        grouped = {}
        invalid_rows = []       # 数量无效的行号
        missing_date_rows = []  # 缺少/非法的入库日期行号
        # to_dict('records') + 索引对齐替代 iterrows（保留原 Excel 行号 idx+2）
        _mib_rows = df.to_dict('records')
        for _idx, row in zip(df.index, _mib_rows):
            part_no = str(row.get(mapping['part_no'], '') or '').strip()
            if not part_no:
                continue
            excel_row = _idx + 2     # Excel 行号（含表头）
            row_date = _mib_parse_date(row.get(mapping['inbound_date']))
            if row_date is None:
                missing_date_rows.append(excel_row)
                continue
            qty = _mib_parse_qty(row.get(mapping['quantity']))
            if qty is None or qty < 0:
                invalid_rows.append(excel_row)
                continue
            incoming = grouped.setdefault(row_date, {})
            item = incoming.setdefault(part_no, {'quantity': 0, 'location': '', 'remark': ''})
            item['quantity'] += qty
            if 'location' in mapping:
                loc = str(row.get(mapping['location'], '') or '').strip()
                if loc:
                    item['location'] = loc
            if 'remark' in mapping:
                rmk = str(row.get(mapping['remark'], '') or '').strip()
                if rmk:
                    item['remark'] = rmk

        if missing_date_rows:
            return jsonify({
                'success': False,
                'error': f'有 {len(missing_date_rows)} 行缺少有效的「入库日期」，请补全后重新上传',
                'missing_date_rows': missing_date_rows[:20],
                'invalid_rows': invalid_rows,
            }), 400
        if not grouped:
            return jsonify({'success': False, 'error': '没有可导入的有效数据',
                            'invalid_rows': invalid_rows}), 400

        user_id = session['user_id']
        now = get_utc8_time()
        created, updated, skipped, locked = 0, 0, 0, 0

        for plan_date, incoming in sorted(grouped.items()):
            existing = {it.part_no: it for it in
                        MaterialInboundDaily.query.filter_by(plan_date=plan_date).all()}
            for part_no, data in incoming.items():
                cur = existing.get(part_no)
                if cur is None:
                    db.session.add(MaterialInboundDaily(
                        plan_date=plan_date, part_no=part_no, quantity=data['quantity'],
                        location=data['location'] or None, remark=data['remark'] or None,
                        status='pending', created_at=now, created_by=user_id,
                    ))
                    created += 1
                elif cur.status in ('done', 'short'):
                    locked += 1  # 已入库/入库不足不做覆盖，保护实绩
                elif mode == 'skip':
                    skipped += 1
                elif mode == 'add':
                    cur.quantity = int(cur.quantity or 0) + data['quantity']
                    if data['location']:
                        cur.location = data['location']
                    if data['remark']:
                        cur.remark = data['remark']
                    updated += 1
                else:  # overwrite
                    cur.quantity = data['quantity']
                    if data['location']:
                        cur.location = data['location']
                    if data['remark']:
                        cur.remark = data['remark']
                    updated += 1

        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.UPLOAD_INBOUND,
                       f"上传资材每日入库明细（文件: {file.filename}）：新增 {created}、更新 {updated}、跳过 {skipped}、锁定 {locked}",
                       TargetType.INBOUND, None, file.filename)
        except Exception as _le:
            current_app.logger.warning(f"记录资材入库上传日志失败: {_le}")
        return jsonify({
            'success': True,
            'dates': [d.strftime('%Y-%m-%d') for d in sorted(grouped.keys())],
            'created': created, 'updated': updated, 'skipped': skipped,
            'locked': locked, 'invalid_rows': invalid_rows,
        })
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材每日入库上传失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'上传失败: {str(e)}'}), 500


@main_bp.route('/material/inbound_daily/save', methods=['POST'])
@login_required
@csrf_exempt_route
def material_inbound_daily_save():
    """手动新增/编辑单条明细（前端逐条提交，简单可靠）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401

    try:
        data = request.get_json(silent=True) or {}
        item_id = data.get('id')
        perm = 'can_material_inbound_daily_edit' if item_id else 'can_material_inbound_daily_add'
        if not check_permission(session.get('user_id'), perm):
            return jsonify({'success': False, 'error': '您没有该操作权限'}), 403

        part_no = str(data.get('part_no', '') or '').strip()
        if not part_no:
            return jsonify({'success': False, 'error': '京信PNO不能为空'}), 400
        qty = _mib_parse_qty(data.get('quantity'))
        if qty is None or qty < 0:
            return jsonify({'success': False, 'error': '数量必须为非负整数'}), 400

        plan_date_str = str(data.get('plan_date', '') or '').strip()
        try:
            plan_date = datetime.strptime(plan_date_str, '%Y-%m-%d').date() if plan_date_str else datetime.now().date()
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400

        location = str(data.get('location', '') or '').strip() or None
        remark = str(data.get('remark', '') or '').strip() or None
        # 入库日期必填：即明细的归属日，用于在入库页面按日期显示
        inbound_date = _mib_parse_date(data.get('inbound_date'))
        if inbound_date is None:
            return jsonify({'success': False, 'error': '请填写有效的入库日期（YYYY-MM-DD）'}), 400

        if item_id:
            item = db.session.get(MaterialInboundDaily, int(item_id))
            if item is None:
                return jsonify({'success': False, 'error': '明细不存在'}), 404
            if item.status in ('done', 'short'):
                return jsonify({'success': False, 'error': '该明细已入库/入库不足，请先撤销入库后再修改'}), 400
            item.plan_date, item.part_no, item.quantity = plan_date, part_no, qty
            item.location, item.remark = location, remark
            item.inbound_date = inbound_date
        else:
            db.session.add(MaterialInboundDaily(
                plan_date=plan_date, part_no=part_no, quantity=qty,
                location=location, remark=remark, inbound_date=inbound_date, status='pending',
                created_at=get_utc8_time(), created_by=session['user_id'],
            ))

        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            if item_id:
                log_action(OperationType.SAVE_INBOUND, f"编辑入库明细: {part_no}（数量 {qty}）",
                           TargetType.INBOUND, int(item_id), part_no)
            else:
                log_action(OperationType.SAVE_INBOUND, f"新增入库明细: {part_no}（数量 {qty}）",
                           TargetType.INBOUND, None, part_no)
        except Exception as _le:
            current_app.logger.warning(f"记录入库明细保存日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材每日入库保存失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'保存失败: {str(e)}'}), 500


@main_bp.route('/material/inbound_daily/delete', methods=['POST'])
@login_required
@csrf_exempt_route
def material_inbound_daily_delete():
    """删除明细（单条或批量）；已入库明细需先撤销"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_daily_delete'):
        return jsonify({'success': False, 'error': '您没有删除明细的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        ids = data.get('ids') or ([data.get('id')] if data.get('id') else [])
        ids = [int(i) for i in ids if i]
        if not ids:
            return jsonify({'success': False, 'error': '未指定要删除的明细'}), 400

        items = MaterialInboundDaily.query.filter(MaterialInboundDaily.id.in_(ids)).all()
        done_items = [it for it in items if it.status in ('done', 'short')]
        if done_items:
            return jsonify({'success': False,
                            'error': f'有 {len(done_items)} 条明细已入库/入库不足，请先撤销入库'}), 400

        for it in items:
            db.session.delete(it)
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.DELETE_INBOUND, f"删除入库明细 {len(items)} 条",
                       TargetType.INBOUND, None, f"{len(items)}条明细")
        except Exception as _le:
            current_app.logger.warning(f"记录入库明细删除日志失败: {_le}")
        return jsonify({'success': True, 'deleted': len(items)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材每日入库删除失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '删除失败'}), 500


@main_bp.route('/material/inbound_daily/template')
@login_required
def material_inbound_daily_template():
    """下载上传模板（标准列头 + 示例行）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))

    wb = Workbook()
    ws = wb.active
    ws.title = '资材每日入库'
    headers = ['京信PNO', '数量', '位置', '备注', '入库日期']
    ws.append(headers)
    for col in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = Font(bold=True, color='FFFFFFFF')
        cell.alignment = Alignment(horizontal='center')
        cell.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
    # 入库日期为必填列（即明细归属日），用醒目底色提示
    date_cell = ws.cell(row=1, column=len(headers))
    date_cell.fill = PatternFill(fill_type='solid', fgColor='FFC00000')
    ws.append(['EXAMPLE-001', 100, 'A区-01架', '示例行，请删除', datetime.now().strftime('%Y-%m-%d')])
    ws.append(['EXAMPLE-002', 50, 'B区-02架', '入库日期必填，格式 YYYY-MM-DD', datetime.now().strftime('%Y-%m-%d')])
    ws.append([])
    ws.append(['说明：每行的「入库日期」为必填项，该日期即明细的归属日；'
               '同一文件可包含多个日期，系统会按日期分别生成对应日期的入库清单。'])
    for i, width in enumerate([24, 12, 20, 34, 16], start=1):
        ws.column_dimensions[chr(64 + i)].width = width

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
    wb.save(tmp.name)
    tmp.close()
    with open(tmp.name, 'rb') as f:
        data_bytes = f.read()
    try:
        os.unlink(tmp.name)
    except OSError:
        pass
    return send_file(io.BytesIO(data_bytes), as_attachment=True,
                     download_name='资材每日入库导入模板.xlsx',
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')


@main_bp.route('/material/inbound_daily/export')
@login_required
def material_inbound_daily_export():
    """导出当天/筛选条件下的明细（含入库状态、入库时间、操作人员）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_material_inbound_daily_download'):
        return forbidden_page('您没有导出明细的权限')

    try:
        plan_date = request.args.get('plan_date', '').strip()
        plan_date_end = request.args.get('plan_date_end', '').strip()
        status = request.args.get('status', '').strip()
        keyword = request.args.get('keyword', '').strip()

        query = MaterialInboundDaily.query
        if plan_date:
            query = query.filter(MaterialInboundDaily.plan_date >= datetime.strptime(plan_date, '%Y-%m-%d').date())
        if plan_date_end:
            query = query.filter(MaterialInboundDaily.plan_date <= datetime.strptime(plan_date_end, '%Y-%m-%d').date())
        if status in ('pending', 'short', 'done'):
            query = query.filter(MaterialInboundDaily.status == status)
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(MaterialInboundDaily.part_no.like(like),
                                        MaterialInboundDaily.location.like(like),
                                        MaterialInboundDaily.remark.like(like)))

        # 流式导出：不一次性 .all() 全量加载，改用 yield_per 分块读取，降低内存峰值
        from openpyxl.cell import WriteOnlyCell
        q = query.order_by(MaterialInboundDaily.part_no.asc(), MaterialInboundDaily.id.asc()).yield_per(500)

        wb = Workbook(write_only=True)
        ws = wb.create_sheet('资材每日入库')
        headers = ['入库日期', '京信PNO', '计划数量', '位置', '备注', '入库状态', '实收数量', '实际入库日期', '入库时间', '操作人员']
        # write-only 模式下无法回读单元格，需用 WriteOnlyCell 预构建带样式表头
        header_cells = []
        for h in headers:
            c = WriteOnlyCell(ws, value=h)
            c.font = Font(bold=True, color='FFFFFFFF')
            c.alignment = Alignment(horizontal='center')
            c.fill = PatternFill(fill_type='solid', fgColor='FF003E7E')
            header_cells.append(c)
        ws.append(header_cells)

        exported = 0
        # 使用 yield_per 迭代，逐批解析操作人员名称（按批去重查询），避免一次性收集全表 inbound_by
        batch = []
        names = {}
        for it in q:
            batch.append(it)
            if len(batch) >= 500:
                names.update(_mib_operator_names([b.inbound_by for b in batch]))
                for b in batch:
                    ws.append(_mib_export_row(b, names))
                    exported += 1
                batch = []
        if batch:
            names.update(_mib_operator_names([b.inbound_by for b in batch]))
            for b in batch:
                ws.append(_mib_export_row(b, names))
                exported += 1

        for i, width in enumerate([14, 24, 12, 18, 26, 12, 12, 14, 20, 14], start=1):
            ws.column_dimensions[get_column_letter(i)].width = width

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.xlsx')
        wb.save(tmp.name)
        tmp.close()
        with open(tmp.name, 'rb') as f:
            data_bytes = f.read()
        try:
            os.unlink(tmp.name)
        except OSError:
            pass

        filename = f"资材每日入库_{(plan_date or datetime.now().strftime('%Y-%m-%d'))}.xlsx"
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.EXPORT_INBOUND, f"导出资材每日入库明细 {exported} 条",
                       TargetType.INBOUND, None, filename)
        except Exception as _le:
            current_app.logger.warning(f"记录资材入库导出日志失败: {_le}")
        return send_file(io.BytesIO(data_bytes), as_attachment=True,
                         download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"资材每日入库导出失败: {str(e)}", exc_info=True)
        return forbidden_page(f'导出失败: {str(e)}')


@main_bp.route('/material/inbound/query', methods=['GET'])
@login_required
def material_inbound_query():
    """扫码/搜索定位：按 P/N 前缀/关键字返回当日待入库明细"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_scan'):
        return jsonify({'success': False, 'error': '您没有扫码/搜索权限'}), 403

    try:
        keyword = request.args.get('keyword', '').strip()
        plan_date_str = request.args.get('plan_date', '').strip()
        try:
            plan_date = datetime.strptime(plan_date_str, '%Y-%m-%d').date() if plan_date_str else datetime.now().date()
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400

        query = MaterialInboundDaily.query.filter(MaterialInboundDaily.plan_date == plan_date)
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(MaterialInboundDaily.part_no.like(like),
                                        MaterialInboundDaily.location.like(like)))

        items = query.order_by(MaterialInboundDaily.status.asc(),
                               MaterialInboundDaily.part_no.asc()).all()
        names = _mib_operator_names([it.inbound_by for it in items])
        return jsonify({'success': True, 'plan_date': plan_date.strftime('%Y-%m-%d'),
                        'items': [it.to_dict(names) for it in items]})
    except Exception as e:
        current_app.logger.error(f"资材入库查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/material/inbound/submit', methods=['POST'])
@login_required
@csrf_exempt_route
def material_inbound_submit():
    """提交入库：支持累计补录；实收<应入库记为「入库不足」，凑足后转「已入库」"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_submit'):
        return jsonify({'success': False, 'error': '您没有提交入库的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        item_id = data.get('id')
        if not item_id:
            return jsonify({'success': False, 'error': '缺少明细ID'}), 400

        item = db.session.get(MaterialInboundDaily, int(item_id))
        if item is None:
            return jsonify({'success': False, 'error': '明细不存在'}), 404
        if item.status == 'done':
            return jsonify({'success': False, 'error': '该明细已入库，请勿重复提交'}), 400

        qty = _mib_parse_qty(data.get('inbound_qty'))
        if qty is None or qty < 0:
            return jsonify({'success': False, 'error': '实收数量必须为非负整数'}), 400

        # 累计补录：入库不足的明细可继续补入，数量累加
        total_qty = int(item.inbound_qty or 0) + qty if item.status == 'short' else qty

        # 入库日期 = 明细的归属日（模板中的入库日期），保证与清单日期一致
        inbound_date = item.plan_date or get_utc8_time().date()

        item.inbound_qty = total_qty
        # 实收 >= 应入库 → 已入库；否则 → 入库不足（促进仓库继续找货）
        item.status = 'done' if total_qty >= int(item.quantity or 0) else 'short'
        item.inbound_date = inbound_date
        item.inbound_at = get_utc8_time()
        item.inbound_by = session['user_id']
        # 操作人员姓名快照：账号删除后记录仍可显示文本
        item.inbound_by_name = session.get('username')
        db.session.commit()

        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            status_text = '已入库' if item.status == 'done' else '入库不足'
            log_action(OperationType.SUBMIT_INBOUND,
                       f"提交入库: {item.part_no}（实收 {total_qty}/{int(item.quantity or 0)}，{status_text}）",
                       TargetType.INBOUND, int(item_id), item.part_no)
        except Exception as _le:
            current_app.logger.warning(f"记录资材入库提交日志失败: {_le}")

        names = _mib_operator_names([item.inbound_by])
        return jsonify({'success': True, 'item': item.to_dict(names)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材入库提交失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'提交失败: {str(e)}'}), 500


@main_bp.route('/material/inbound/revoke', methods=['POST'])
@login_required
@csrf_exempt_route
def material_inbound_revoke():
    """撤销入库：将明细恢复为未入库，便于纠错重提"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_material_inbound_revoke'):
        return jsonify({'success': False, 'error': '您没有撤销入库的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        item_id = data.get('id')
        if not item_id:
            return jsonify({'success': False, 'error': '缺少明细ID'}), 400

        item = db.session.get(MaterialInboundDaily, int(item_id))
        if item is None:
            return jsonify({'success': False, 'error': '明细不存在'}), 404
        if item.status not in ('done', 'short'):
            return jsonify({'success': False, 'error': '该明细尚未入库'}), 400

        item.status = 'pending'
        item.inbound_qty = 0
        item.inbound_date = None
        item.inbound_at = None
        item.inbound_by = None
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.REVOKE_INBOUND, f"撤销入库: {item.part_no}",
                       TargetType.INBOUND, int(item_id), item.part_no)
        except Exception as _le:
            current_app.logger.warning(f"记录资材入库撤销日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材入库撤销失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '撤销失败'}), 500


# ==================== 盘点管理模块 ====================
# 页面1：盘点数据登录（维护盘点班组，后续实现）
# 页面2：盘点实查（移动端）：选班组 → 选品番(KscPno) → 填盘点/抽查数量 → 自动算差异 → 提交
# 页面3：数据汇总（盘点实况大屏，后续实现）


def _inv_parse_qty(value):
    """数量解析：兼容 '1,000'、'10.0'、空值；失败返回 None"""
    try:
        s = str(value if value is not None else '').strip().replace(',', '').replace('，', '')
        if not s:
            return None
        return int(float(s))
    except Exception:
        return None


def _inv_operator_names(user_ids):
    """批量查询用户名，避免逐行查库（返回 {user_id: username}）"""
    ids = {int(i) for i in user_ids if i}
    if not ids:
        return {}
    rows = UserInfo.query.filter(UserInfo.id.in_(ids)).all()
    return {u.id: u.username for u in rows}


@main_bp.route('/inventory/check')
@login_required
def inventory_check():
    """盘点实查页面（页面2，移动端适配）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    if not check_permission(session.get('user_id'), 'can_inventory_check'):
        return forbidden_page('您没有盘点实查页面的访问权限')
    # 显式计算操作权限传入模板，避免依赖 session 中可能缺失的权限键
    uid = session.get('user_id')
    perms = {
        'is_admin': bool(session.get('is_admin')),
        'can_edit_team': check_permission(uid, 'can_inventory_login_edit'),
        'can_submit': check_permission(uid, 'can_inventory_check_submit'),
    }
    return render_template('inventory_check.html', **perms)


@main_bp.route('/inventory/login')
@login_required
def inventory_login():
    """盘点数据登录页面（页面1：盘点班组维护）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    uid = session.get('user_id')
    if not check_permission(uid, 'can_inventory_login'):
        return forbidden_page('您没有盘点数据登录页面的访问权限')
    can_edit = bool(session.get('is_admin') or check_permission(uid, 'can_inventory_login_edit'))
    return render_template('inventory_login.html', can_edit=can_edit)


@main_bp.route('/inventory/summary')
@login_required
def inventory_summary():
    """数据汇总页面（页面3：盘点实况大屏 + 班组盘点柱状图）"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    uid = session.get('user_id')
    if not check_permission(uid, 'can_inventory_summary'):
        return forbidden_page('您没有数据汇总页面的访问权限')
    return render_template('inventory_summary.html',
                           is_admin=bool(session.get('is_admin')),
                           can_delete=check_permission(uid, 'can_inventory_summary_delete'))


@main_bp.route('/inventory/teams', methods=['GET'])
@login_required
def inventory_teams():
    """盘点班组列表（页面1维护与页面2下拉共用）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    uid = session.get('user_id')
    if not (session.get('is_admin') or check_permission(uid, 'can_inventory_login')
            or check_permission(uid, 'can_inventory_check')
            or check_permission(uid, 'can_inventory_summary')):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        include_inactive = request.args.get('all') == '1'
        query = InventoryTeam.query
        if not include_inactive:
            query = query.filter(InventoryTeam.is_active == True)  # noqa: E712
        teams = query.order_by(InventoryTeam.sort_no.asc(), InventoryTeam.id.asc()).all()
        return jsonify({'success': True, 'items': [t.to_dict() for t in teams]})
    except Exception as e:
        current_app.logger.error(f"盘点班组查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/inventory/team/save', methods=['POST'])
@login_required
@csrf_exempt_route
def inventory_team_save():
    """新增/编辑盘点班组（页面1「盘点数据登录」维护）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_login_edit'):
        return jsonify({'success': False, 'error': '您没有编辑盘点班组的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        team_id = data.get('id')
        name = str(data.get('name', '') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': '班组名称不能为空'}), 400

        remark = str(data.get('remark', '') or '').strip() or None
        sort_no = _inv_parse_qty(data.get('sort_no'))
        if sort_no is None:
            sort_no = 0
        # is_active 未传时默认启用（新增场景）
        is_active = bool(data.get('is_active', True))

        # 名称唯一性校验（排除自身）
        dup = InventoryTeam.query.filter(InventoryTeam.name == name)
        if team_id:
            dup = dup.filter(InventoryTeam.id != int(team_id))
        if dup.first():
            return jsonify({'success': False, 'error': '该班组名称已存在'}), 400

        if team_id:
            team = db.session.get(InventoryTeam, int(team_id))
            if team is None:
                return jsonify({'success': False, 'error': '班组不存在'}), 404
            team.name, team.sort_no = name, sort_no
            team.remark, team.is_active = remark, is_active
        else:
            db.session.add(InventoryTeam(name=name, sort_no=sort_no,
                                         remark=remark, is_active=is_active))
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            act = "编辑" if team_id else "新增"
            log_action(OperationType.SAVE_INVENTORY_TEAM, f"{act}盘点班组: {name}",
                       TargetType.INVENTORY, int(team_id) if team_id else None, name)
        except Exception as _le:
            current_app.logger.warning(f"记录盘点班组保存日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"盘点班组保存失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'保存失败: {str(e)}'}), 500


@main_bp.route('/inventory/team/delete', methods=['POST'])
@login_required
@csrf_exempt_route
def inventory_team_delete():
    """删除盘点班组；已有盘点记录的班组禁止删除，改为停用"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_login_edit'):
        return jsonify({'success': False, 'error': '您没有编辑盘点班组的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        team_id = data.get('id')
        if not team_id:
            return jsonify({'success': False, 'error': '缺少班组ID'}), 400

        team = db.session.get(InventoryTeam, int(team_id))
        if team is None:
            return jsonify({'success': False, 'error': '班组不存在'}), 404
        if InventoryCheck.query.filter_by(team_id=team.id).first():
            return jsonify({'success': False, 'error': '该班组已有盘点记录，不能删除，可将其停用'}), 400

        team_name = team.name
        db.session.delete(team)
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.DELETE_INVENTORY_TEAM, f"删除盘点班组: {team_name}",
                       TargetType.INVENTORY, int(team_id), team_name)
        except Exception as _le:
            current_app.logger.warning(f"记录盘点班组删除日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"盘点班组删除失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '删除失败'}), 500


@main_bp.route('/inventory/ksc/search', methods=['GET'])
@login_required
def inventory_ksc_search():
    """资材品番搜索：从品番字典表 inventory_pno_dict 检索（带关键字模糊匹配，限量）

    性能要点：字典表仅约 1.4 千行（usage_list 去重结果），全表扫描成本可忽略；
    启动时由后台线程从 usage_list 增量同步新规品番，故无需实时查 usage_list 的 53 万行。
    字典表为空时降级到 usage_list 实时查询，保证功能可用。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_check'):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        keyword = request.args.get('keyword', '').strip()
        try:
            limit = min(int(request.args.get('limit', 50)), 200)
        except ValueError:
            limit = 50

        # 字典表为空时，先实时同步一次（首次部署或表被清空的兜底）
        if InventoryPnoDict.query.first() is None:
            InventoryPnoDict.sync_from_usage_list(current_app._get_current_object())

        query = InventoryPnoDict.query
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(InventoryPnoDict.ksc_pno.like(like),
                                        InventoryPnoDict.part_name.like(like)))
        rows = query.order_by(InventoryPnoDict.ksc_pno.asc()).limit(limit).all()
        items = [{'ksc_pno': r.ksc_pno, 'part_name': r.part_name or ''} for r in rows]
        return jsonify({'success': True, 'items': items, 'count': len(items)})
    except Exception as e:
        current_app.logger.error(f"资材品番搜索失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '搜索失败'}), 500


@main_bp.route('/inventory/pno/sync', methods=['POST'])
@login_required
@csrf_exempt_route
def inventory_pno_sync():
    """手动同步资材品番字典：从 usage_list 增量补入新规品番

    用于刚导入大批数据后立即生效，无需等待下次启动的后台同步。
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not (session.get('is_admin') or check_permission(session.get('user_id'), 'can_inventory_check')):
        return jsonify({'success': False, 'error': '您没有同步品番字典的权限'}), 403

    try:
        added, updated, total = InventoryPnoDict.sync_from_usage_list(current_app._get_current_object())
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.SYNC_PNO, f"同步资材品番字典：新增 {added}、更新 {updated}、共 {total}",
                       TargetType.INVENTORY, None, f"{total}条品番")
        except Exception as _le:
            current_app.logger.warning(f"记录品番同步日志失败: {_le}")
        return jsonify({'success': True, 'added': added, 'updated': updated,
                        'total': total, 'message': f'同步完成：新增 {added} 条，更新 {updated} 条，共 {total} 条'})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"资材品番字典同步失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'同步失败: {str(e)}'}), 500


@main_bp.route('/inventory/check/submit', methods=['POST'])
@login_required
@csrf_exempt_route
def inventory_check_submit():
    """提交盘点实查记录：差异自动计算 = 盘点数量 - 抽查数量"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_check_submit'):
        return jsonify({'success': False, 'error': '您没有提交盘点实查的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        team_id = data.get('team_id')
        ksc_pno = str(data.get('ksc_pno', '') or '').strip()
        if not team_id:
            return jsonify({'success': False, 'error': '请选择盘点班组'}), 400
        if not ksc_pno:
            return jsonify({'success': False, 'error': '请选择资材品番'}), 400

        team = db.session.get(InventoryTeam, int(team_id))
        if team is None:
            return jsonify({'success': False, 'error': '盘点班组不存在'}), 404

        check_qty = _inv_parse_qty(data.get('check_qty'))
        sample_qty = _inv_parse_qty(data.get('sample_qty'))
        if check_qty is None or check_qty < 0:
            return jsonify({'success': False, 'error': '盘点数量必须为非负整数'}), 400
        if sample_qty is None or sample_qty < 0:
            return jsonify({'success': False, 'error': '抽查数量必须为非负整数'}), 400

        # 盘点日期：默认当天（UTC+8）
        date_str = str(data.get('check_date', '') or '').strip()
        try:
            check_date = datetime.strptime(date_str, '%Y-%m-%d').date() if date_str else get_utc8_time().date()
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400

        part_name = str(data.get('part_name', '') or '').strip() or None
        remark = str(data.get('remark', '') or '').strip() or None

        record = InventoryCheck(
            check_date=check_date, team_id=team.id, team_name=team.name,
            ksc_pno=ksc_pno, part_name=part_name,
            check_qty=check_qty, sample_qty=sample_qty,
            diff_qty=check_qty - sample_qty, remark=remark,
            created_at=get_utc8_time(), created_by=session['user_id'],
        )
        db.session.add(record)
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.SUBMIT_INVENTORY_CHECK,
                       f"提交盘点实查: {ksc_pno}（班组 {team.name}，盘点 {check_qty}、抽查 {sample_qty}、差异 {check_qty - sample_qty}）",
                       TargetType.INVENTORY, record.id, ksc_pno)
        except Exception as _le:
            current_app.logger.warning(f"记录盘点实查提交日志失败: {_le}")
        return jsonify({'success': True, 'item': record.to_dict()})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"盘点实查提交失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'提交失败: {str(e)}'}), 500


@main_bp.route('/inventory/check/data', methods=['GET'])
@login_required
def inventory_check_data():
    """盘点实查记录查询：按日期（默认当天）+ 班组 + 关键字筛选"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_check'):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        date_str = request.args.get('check_date', '').strip()
        team_id = request.args.get('team_id', '').strip()
        keyword = request.args.get('keyword', '').strip()

        query = InventoryCheck.query
        if date_str:
            try:
                query = query.filter(InventoryCheck.check_date == datetime.strptime(date_str, '%Y-%m-%d').date())
            except ValueError:
                return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400
        if team_id:
            query = query.filter(InventoryCheck.team_id == int(team_id))
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(InventoryCheck.ksc_pno.like(like),
                                        InventoryCheck.part_name.like(like),
                                        InventoryCheck.remark.like(like)))

        items = query.order_by(InventoryCheck.id.desc()).limit(500).all()
        names = _inv_operator_names([it.created_by for it in items])
        return jsonify({
            'success': True,
            'items': [it.to_dict(names) for it in items],
            'summary': {
                'total': len(items),
                'check_qty': sum(int(it.check_qty or 0) for it in items),
                'sample_qty': sum(int(it.sample_qty or 0) for it in items),
                'diff_qty': sum(int(it.diff_qty or 0) for it in items),
                'diff_count': sum(1 for it in items if int(it.diff_qty or 0) != 0),
            }
        })
    except Exception as e:
        current_app.logger.error(f"盘点实查记录查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


# ==================== 不良品登录（盘点查询 - 移动端） ====================

def _defective_open_access():
    """读取免登录开关（异常时回落到需登录）"""
    try:
        from models.system_setting import is_defective_open_access
        return is_defective_open_access()
    except Exception:
        return False


def _defective_is_admin():
    """当前会话是否为管理员账号（用于显示免登开关按钮）"""
    return bool(session.get('is_admin'))


def _defective_allow_access():
    """页面/接口放行判断：管理员 or 免登开启 or 具备该页权限"""
    if _defective_is_admin():
        return True
    if _defective_open_access():
        return True
    uid = session.get('user_id')
    return bool(uid) and check_permission(uid, 'can_defective_login')


@main_bp.route('/inventory/defective')
def defective_login_page():
    """不良品登录页面（移动端适配）

    访问规则：管理员始终可访问；开启「免登录」后任何人可直接访问；
    否则需登录且具备 can_defective_login 权限。
    """
    if not _defective_allow_access():
        if 'user_id' not in session:
            return redirect(url_for('main.login', next=request.url))
        return forbidden_page('您没有不良品登录页面的访问权限')
    # 免登录模式下，未登录用户获得与登录用户一致的提交/删除操作能力
    open_access = _defective_open_access()
    uid = session.get('user_id')
    return render_template(
        'defective_login.html',
        is_admin=_defective_is_admin(),
        open_access=open_access,
        can_submit=open_access or (bool(uid) and check_permission(uid, 'can_defective_login_submit')),
        can_delete=open_access or (bool(uid) and check_permission(uid, 'can_defective_login_delete')),
        reasons=list(DEFECTIVE_REASONS),
    )


@main_bp.route('/inventory/defective/access_mode', methods=['GET', 'POST'])
@csrf_exempt_route
def defective_login_access_mode():
    """免登录访问开关（仅管理员）：GET 查询 / POST 切换"""
    if not _defective_is_admin():
        return jsonify({'success': False, 'error': '仅管理员可修改访问方式'}), 403
    from models.system_setting import set_defective_open_access
    if request.method == 'GET':
        return jsonify({'success': True, 'open_access': _defective_open_access()})
    try:
        data = request.get_json(silent=True) or {}
        enabled = bool(data.get('open_access'))
        set_defective_open_access(enabled)
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            mode_text = '免登录' if enabled else '需登录'
            log_action(OperationType.SET_DEFECTIVE_ACCESS_MODE,
                       f"设置不良品登录访问方式为：{mode_text}",
                       TargetType.DEFECTIVE, None, mode_text)
        except Exception as _le:
            current_app.logger.warning(f"记录不良品访问方式日志失败: {_le}")
        return jsonify({'success': True, 'open_access': enabled})
    except Exception as e:
        current_app.logger.error(f"切换不良品登录访问方式失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '切换失败'}), 500


@main_bp.route('/inventory/defective/teams', methods=['GET'])
def defective_login_teams():
    """不良品登录页班组下拉（免登场景可用，仅返回启用中的班组）"""
    if not _defective_allow_access():
        return jsonify({'success': False, 'error': '没有访问权限'}), 403
    try:
        teams = InventoryTeam.query.filter(InventoryTeam.is_active == True)\
            .order_by(InventoryTeam.sort_no.asc(), InventoryTeam.id.asc()).all()
        return jsonify({'success': True, 'items': [t.to_dict() for t in teams]})
    except Exception as e:
        current_app.logger.error(f"不良品班组查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/inventory/defective/search', methods=['GET'])
def defective_login_search():
    """资材品番搜索：与盘点实查共用同一数据源（品番字典表）"""
    if not _defective_allow_access():
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        keyword = request.args.get('keyword', '').strip()
        try:
            limit = min(int(request.args.get('limit', 50)), 200)
        except ValueError:
            limit = 50

        query = InventoryPnoDict.query
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(InventoryPnoDict.ksc_pno.like(like),
                                        InventoryPnoDict.part_name.like(like)))
        rows = query.order_by(InventoryPnoDict.ksc_pno.asc()).limit(limit).all()

        items = [{'ksc_pno': r.ksc_pno, 'part_name': r.part_name or ''} for r in rows]
        return jsonify({'success': True, 'items': items, 'count': len(items)})
    except Exception as e:
        current_app.logger.error(f"不良品资材搜索失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '搜索失败'}), 500


@main_bp.route('/inventory/defective/submit', methods=['POST'])
@csrf_exempt_route
def defective_login_submit():
    """提交不良品登录（支持一次提交多条资材明细）"""
    open_access = _defective_open_access()
    uid = session.get('user_id')
    if not _defective_allow_access():
        return jsonify({'success': False, 'error': '没有访问权限'}), 403
    if not (open_access or (bool(uid) and check_permission(uid, 'can_defective_login_submit'))):
        return jsonify({'success': False, 'error': '您没有提交不良品登录的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        reporter_name = str(data.get('reporter_name', '') or '').strip()
        team_id = data.get('team_id')
        reason = str(data.get('reason', '') or '').strip()
        remark = str(data.get('remark', '') or '').strip() or None

        if not reporter_name:
            return jsonify({'success': False, 'error': '请填写登记人姓名'}), 400
        if not team_id:
            return jsonify({'success': False, 'error': '请选择班组'}), 400
        if not reason:
            return jsonify({'success': False, 'error': '请选择废弃原因'}), 400

        team = db.session.get(InventoryTeam, int(team_id))
        if team is None:
            return jsonify({'success': False, 'error': '班组不存在'}), 404

        date_str = str(data.get('login_date', '') or '').strip()
        try:
            login_date = (datetime.strptime(date_str, '%Y-%m-%d').date()
                          if date_str else get_utc8_time().date())
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400

        details = data.get('items') or []
        if not isinstance(details, list) or not details:
            return jsonify({'success': False, 'error': '请至少选择一条资材数据'}), 400

        records = []
        for it in details:
            ksc_pno = str(it.get('ksc_pno', '') or '').strip()
            if not ksc_pno:
                return jsonify({'success': False, 'error': '存在未选择资材品番的数据行'}), 400
            scrap_qty = _inv_parse_qty(it.get('scrap_qty'))
            if scrap_qty is None or scrap_qty < 0:
                return jsonify({'success': False, 'error': f'{ksc_pno} 的废弃数量必须为非负整数'}), 400
            rec = DefectiveLogin(
                login_date=login_date, reporter_name=reporter_name,
                team_id=team.id, team_name=team.name,
                ksc_pno=ksc_pno,
                part_name=str(it.get('part_name', '') or '').strip() or None,
                scrap_qty=scrap_qty, reason=reason, remark=remark,
                created_at=get_utc8_time(), created_by=uid,
                # 登记人姓名快照：账号删除后记录仍可显示文本
                created_by_name=session.get('username'),
            )
            db.session.add(rec)
            records.append(rec)

        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            detail = "||".join(f"{r.ksc_pno}:{r.scrap_qty}" for r in records)
            log_action(OperationType.SUBMIT_DEFECTIVE,
                       f"提交不良品登录 {len(records)} 条（登记人 {reporter_name}，班组 {team.name}，原因 {reason}）",
                       TargetType.DEFECTIVE, None, f"{len(records)}条不良品", detail)
        except Exception as _le:
            current_app.logger.warning(f"记录不良品提交日志失败: {_le}")
        return jsonify({'success': True,
                        'items': [r.to_dict() for r in records],
                        'count': len(records)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"不良品登录提交失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'提交失败: {str(e)}'}), 500


@main_bp.route('/inventory/defective/data', methods=['GET'])
def defective_login_data():
    """不良品登记记录查询：按日期区间/单日 + 班组 + 关键字筛选"""
    if not _defective_allow_access():
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        date_str = request.args.get('login_date', '').strip()
        start_str = request.args.get('start_date', '').strip()
        end_str = request.args.get('end_date', '').strip()
        team_id = request.args.get('team_id', '').strip()
        keyword = request.args.get('keyword', '').strip()

        query = DefectiveLogin.query
        try:
            if start_str:
                query = query.filter(DefectiveLogin.login_date >=
                                     datetime.strptime(start_str, '%Y-%m-%d').date())
            if end_str:
                query = query.filter(DefectiveLogin.login_date <=
                                     datetime.strptime(end_str, '%Y-%m-%d').date())
            if (not start_str and not end_str) and date_str:
                query = query.filter(DefectiveLogin.login_date ==
                                     datetime.strptime(date_str, '%Y-%m-%d').date())
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400
        if team_id:
            try:
                query = query.filter(DefectiveLogin.team_id == int(team_id))
            except ValueError:
                return jsonify({'success': False, 'error': '班组参数不合法'}), 400
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(DefectiveLogin.ksc_pno.like(like),
                                        DefectiveLogin.part_name.like(like),
                                        DefectiveLogin.reporter_name.like(like),
                                        DefectiveLogin.remark.like(like)))

        items = query.order_by(DefectiveLogin.id.desc()).limit(500).all()
        names = _inv_operator_names([it.created_by for it in items])
        return jsonify({
            'success': True,
            'items': [it.to_dict(names) for it in items],
            'summary': {
                'total': len(items),
                'scrap_qty': sum(int(it.scrap_qty or 0) for it in items),
            }
        })
    except Exception as e:
        current_app.logger.error(f"不良品登记记录查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/inventory/defective/delete', methods=['POST'])
@csrf_exempt_route
def defective_login_delete():
    """删除不良品登记记录"""
    open_access = _defective_open_access()
    uid = session.get('user_id')
    if not _defective_allow_access():
        return jsonify({'success': False, 'error': '没有访问权限'}), 403
    if not (open_access or (bool(uid) and check_permission(uid, 'can_defective_login_delete'))):
        return jsonify({'success': False, 'error': '您没有删除不良品记录的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        rec_id = data.get('id')
        if not rec_id:
            return jsonify({'success': False, 'error': '缺少记录ID'}), 400
        rec = db.session.get(DefectiveLogin, int(rec_id))
        if rec is None:
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        rec_desc = f"{rec.ksc_pno}（数量 {rec.scrap_qty}）"
        db.session.delete(rec)
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.DELETE_DEFECTIVE, f"删除不良品记录: {rec_desc}",
                       TargetType.DEFECTIVE, int(rec_id), rec_desc)
        except Exception as _le:
            current_app.logger.warning(f"记录不良品删除日志失败: {_le}")
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"不良品记录删除失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '删除失败'}), 500


# ==================== 不良品清单导出（盘点查询） ====================

def _defective_export_query():
    """按查询参数构建不良品记录查询（起始日期/结束日期/班组/关键字）"""
    query = DefectiveLogin.query
    start_str = request.args.get('start_date', '').strip()
    end_str = request.args.get('end_date', '').strip()
    team_id = request.args.get('team_id', '').strip()
    keyword = request.args.get('keyword', '').strip()
    if start_str:
        query = query.filter(DefectiveLogin.login_date >= datetime.strptime(start_str, '%Y-%m-%d').date())
    if end_str:
        query = query.filter(DefectiveLogin.login_date <= datetime.strptime(end_str, '%Y-%m-%d').date())
    if team_id:
        query = query.filter(DefectiveLogin.team_id == int(team_id))
    if keyword:
        like = f'%{keyword}%'
        query = query.filter(db.or_(DefectiveLogin.ksc_pno.like(like),
                                    DefectiveLogin.part_name.like(like),
                                    DefectiveLogin.reporter_name.like(like),
                                    DefectiveLogin.remark.like(like)))
    return query.order_by(DefectiveLogin.login_date.asc(), DefectiveLogin.id.asc())


@main_bp.route('/inventory/defective/export')
@login_required
def defective_export_page():
    """不良品清单导出页面"""
    if 'user_id' not in session:
        return redirect(url_for('main.login', next=request.url))
    uid = session.get('user_id')
    if not check_permission(uid, 'can_defective_export'):
        return forbidden_page('您没有不良品导出页面的访问权限')
    return render_template('defective_export.html',
                           is_admin=bool(session.get('is_admin')),
                           can_export=check_permission(uid, 'can_defective_export_download'))


@main_bp.route('/inventory/defective/export/download', methods=['GET'])
@login_required
def defective_export_download():
    """导出不良品清单 Excel（明细 sheet + 汇总 sheet）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_defective_export_download'):
        return jsonify({'success': False, 'error': '您没有导出不良品清单的权限'}), 403

    try:
        from io import BytesIO
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from collections import defaultdict

        start_str = request.args.get('start_date', '').strip()
        end_str = request.args.get('end_date', '').strip()
        try:
            items = _defective_export_query().limit(20000).all()
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400

        # 汇总数据准备
        by_team = defaultdict(int)     # 班组 -> 废弃数量
        by_reason = defaultdict(int)   # 废弃原因 -> 废弃数量
        by_pno = defaultdict(int)      # 资材品番 -> 废弃数量
        for it in items:
            q = int(it.scrap_qty or 0)
            by_team[it.team_name or '-'] += q
            by_reason[it.reason or '-'] += q
            by_pno[it.ksc_pno or '-'] += q

        wb = Workbook()
        thin = Side(style='thin', color='B0B0B0')
        border = Border(left=thin, right=thin, top=thin, bottom=thin)
        head_fill = PatternFill(start_color='D9E1F2', end_color='D9E1F2', fill_type='solid')
        head_font = Font(bold=True)
        center = Alignment(horizontal='center', vertical='center')

        def style_header(ws, ncol):
            for c in range(1, ncol + 1):
                cell = ws.cell(row=1, column=c)
                cell.fill = head_fill
                cell.font = head_font
                cell.alignment = center
                cell.border = border
            ws.freeze_panes = 'A2'

        # Sheet1 明细
        ws = wb.active
        ws.title = '不良品明细'
        headers = ['序号', '登记日期', '登记人', '班组', '资材品番', '品名',
                   '废弃数量', '废弃原因', '备注', '提交时间']
        ws.append(headers)
        for idx, it in enumerate(items, 1):
            ws.append([idx,
                       it.login_date.strftime('%Y-%m-%d') if it.login_date else '',
                       it.reporter_name or '', it.team_name or '', it.ksc_pno or '',
                       it.part_name or '', int(it.scrap_qty or 0),
                       it.reason or '', it.remark or '',
                       format_utc8_time(it.created_at) if it.created_at else ''])
        style_header(ws, len(headers))
        widths = [6, 12, 10, 12, 18, 22, 10, 18, 24, 18]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

        # Sheet2 汇总
        ws2 = wb.create_sheet('汇总统计')
        total_qty = sum(int(it.scrap_qty or 0) for it in items)
        ws2.append(['汇总范围',
                    (start_str or '不限') + ' ~ ' + (end_str or '不限')])
        ws2.append(['记录总条数', len(items)])
        ws2.append(['废弃总数量', total_qty])
        ws2.append([])
        row_start = ws2.max_row + 1
        ws2.append(['按班组汇总', '', '按废弃原因汇总', '', '按资材品番汇总(前50)', ''])
        for c in range(1, 7):
            ws2.cell(row=row_start, column=c).fill = head_fill
            ws2.cell(row=row_start, column=c).font = head_font
        team_rows = sorted(by_team.items(), key=lambda x: -x[1])
        reason_rows = sorted(by_reason.items(), key=lambda x: -x[1])
        pno_rows = sorted(by_pno.items(), key=lambda x: -x[1])[:50]
        maxn = max(len(team_rows), len(reason_rows), len(pno_rows))
        for i in range(maxn):
            tr = team_rows[i] if i < len(team_rows) else ('', '')
            rr = reason_rows[i] if i < len(reason_rows) else ('', '')
            pr = pno_rows[i] if i < len(pno_rows) else ('', '')
            ws2.append([tr[0], tr[1], rr[0], rr[1], pr[0], pr[1]])
        for i, w in enumerate([22, 12, 22, 12, 22, 12], 1):
            ws2.column_dimensions[get_column_letter(i)].width = w

        output = BytesIO()
        wb.save(output)
        output.seek(0)
        fname = '不良品清单_' + get_utc8_time().strftime('%Y%m%d_%H%M%S') + '.xlsx'
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.EXPORT_DEFECTIVE, f"导出不良品清单 {len(items)} 条",
                       TargetType.DEFECTIVE, None, fname)
        except Exception as _le:
            current_app.logger.warning(f"记录不良品导出日志失败: {_le}")
        return send_file(output, as_attachment=True, download_name=fname,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        current_app.logger.error(f"不良品清单导出失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': f'导出失败: {str(e)}'}), 500


@main_bp.route('/inventory/summary/data', methods=['GET'])
@login_required
def inventory_summary_data():
    """数据汇总：按日期范围聚合各班组盘点情况（供柱状图使用）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_summary'):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        start_str = request.args.get('start_date', '').strip()
        end_str = request.args.get('end_date', '').strip()

        # 日期解析与校验
        def _parse(s):
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        try:
            start_date = _parse(start_str)
            end_date = _parse(end_str)
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400
        # 未传日期则默认当天
        if not start_date and not end_date:
            start_date = end_date = datetime.now().date()
        elif start_date and not end_date:
            end_date = start_date
        elif end_date and not start_date:
            start_date = end_date
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        # 限制跨度，避免超大范围拖慢查询
        if (end_date - start_date).days > 366:
            return jsonify({'success': False, 'error': '日期范围不能超过 366 天'}), 400

        # 单次聚合查询：按班组分组统计（避免把明细全量取回内存）
        rows = db.session.query(
            InventoryCheck.team_id,
            InventoryCheck.team_name,
            db.func.count(InventoryCheck.id).label('record_count'),
            db.func.coalesce(db.func.sum(InventoryCheck.check_qty), 0).label('check_qty'),
            db.func.coalesce(db.func.sum(InventoryCheck.sample_qty), 0).label('sample_qty'),
            db.func.coalesce(db.func.sum(InventoryCheck.diff_qty), 0).label('diff_qty'),
        ).filter(
            InventoryCheck.check_date >= start_date,
            InventoryCheck.check_date <= end_date,
        ).group_by(
            InventoryCheck.team_id, InventoryCheck.team_name
        ).all()

        # 差异笔数：单独聚合（SUM 无法表达「非零条数」）
        diff_rows = db.session.query(
            InventoryCheck.team_id,
            db.func.count(InventoryCheck.id).label('diff_count'),
        ).filter(
            InventoryCheck.check_date >= start_date,
            InventoryCheck.check_date <= end_date,
            InventoryCheck.diff_qty != 0,
        ).group_by(InventoryCheck.team_id).all()
        diff_map = {r.team_id: int(r.diff_count or 0) for r in diff_rows}

        # 排序：与班组维护的排序一致，未维护的排末尾
        team_order = {t.id: (t.sort_no or 0, t.name)
                      for t in InventoryTeam.query.all()}
        items = []
        for r in rows:
            items.append({
                'team_id': r.team_id,
                'team_name': r.team_name or '未命名班组',
                'record_count': int(r.record_count or 0),
                'check_qty': int(r.check_qty or 0),
                'sample_qty': int(r.sample_qty or 0),
                'diff_qty': int(r.diff_qty or 0),
                'diff_count': diff_map.get(r.team_id, 0),
            })
        items.sort(key=lambda x: (team_order.get(x['team_id'], (9999, x['team_name'])), x['team_name']))

        return jsonify({
            'success': True,
            'range': {'start_date': start_date.strftime('%Y-%m-%d'),
                      'end_date': end_date.strftime('%Y-%m-%d')},
            'items': items,
            'summary': {
                'team_count': len(items),
                'record_count': sum(i['record_count'] for i in items),
                'check_qty': sum(i['check_qty'] for i in items),
                'sample_qty': sum(i['sample_qty'] for i in items),
                'diff_qty': sum(i['diff_qty'] for i in items),
                'diff_count': sum(i['diff_count'] for i in items),
            }
        })
    except Exception as e:
        current_app.logger.error(f"盘点数据汇总查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/inventory/summary/detail', methods=['GET'])
@login_required
def inventory_summary_detail():
    """数据汇总：指定日期范围内的盘点清单（明细，含盘点时间与盘点人账号）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_summary'):
        return jsonify({'success': False, 'error': '没有访问权限'}), 403

    try:
        start_str = request.args.get('start_date', '').strip()
        end_str = request.args.get('end_date', '').strip()
        team_id = request.args.get('team_id', '').strip()
        keyword = request.args.get('keyword', '').strip()
        try:
            page = max(int(request.args.get('page', 1) or 1), 1)
            page_size = min(max(int(request.args.get('page_size', 50) or 50), 1), 200)
        except ValueError:
            return jsonify({'success': False, 'error': '分页参数不合法'}), 400

        # 日期范围解析（与汇总接口同规则，保证图表与清单口径一致）
        def _parse(s):
            return datetime.strptime(s, '%Y-%m-%d').date() if s else None
        try:
            start_date = _parse(start_str)
            end_date = _parse(end_str)
        except ValueError:
            return jsonify({'success': False, 'error': '日期格式应为 YYYY-MM-DD'}), 400
        if not start_date and not end_date:
            start_date = end_date = datetime.now().date()
        elif start_date and not end_date:
            end_date = start_date
        elif end_date and not start_date:
            start_date = end_date
        if start_date > end_date:
            start_date, end_date = end_date, start_date
        if (end_date - start_date).days > 366:
            return jsonify({'success': False, 'error': '日期范围不能超过 366 天'}), 400

        query = InventoryCheck.query.filter(
            InventoryCheck.check_date >= start_date,
            InventoryCheck.check_date <= end_date,
        )
        if team_id:
            try:
                query = query.filter(InventoryCheck.team_id == int(team_id))
            except ValueError:
                return jsonify({'success': False, 'error': '班组参数不合法'}), 400
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(db.or_(InventoryCheck.ksc_pno.like(like),
                                        InventoryCheck.part_name.like(like),
                                        InventoryCheck.remark.like(like)))

        total = query.count()
        items = query.order_by(InventoryCheck.check_date.desc(),
                               InventoryCheck.created_at.desc(),
                               InventoryCheck.id.desc()) \
                     .offset((page - 1) * page_size).limit(page_size).all()
        # 批量取用户名，避免逐行查库
        names = _inv_operator_names([it.created_by for it in items])

        return jsonify({
            'success': True,
            'items': [it.to_dict(names) for it in items],
            'page': page,
            'page_size': page_size,
            'total': total,
            'pages': (total + page_size - 1) // page_size if page_size else 0,
        })
    except Exception as e:
        current_app.logger.error(f"盘点清单查询失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '查询失败'}), 500


@main_bp.route('/inventory/check/delete', methods=['POST'])
@login_required
@csrf_exempt_route
def inventory_check_delete():
    """删除盘点实查记录（纠错用）"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': '未登录'}), 401
    if not check_permission(session.get('user_id'), 'can_inventory_summary_delete'):
        return jsonify({'success': False, 'error': '您没有删除盘点记录的权限'}), 403

    try:
        data = request.get_json(silent=True) or {}
        ids = data.get('ids') or ([data.get('id')] if data.get('id') else [])
        ids = [int(i) for i in ids if i]
        if not ids:
            return jsonify({'success': False, 'error': '未指定要删除的记录'}), 400

        items = InventoryCheck.query.filter(InventoryCheck.id.in_(ids)).all()
        for it in items:
            db.session.delete(it)
        db.session.commit()
        try:
            from utils.operation_logger import log_action
            from models.operation_log import OperationType, TargetType
            log_action(OperationType.DELETE_INVENTORY_CHECK, f"删除盘点实查记录 {len(items)} 条",
                       TargetType.INVENTORY, None, f"{len(items)}条记录")
        except Exception as _le:
            current_app.logger.warning(f"记录盘点记录删除日志失败: {_le}")
        return jsonify({'success': True, 'deleted': len(items)})
    except Exception as e:
        db.session.rollback()
        current_app.logger.error(f"盘点实查记录删除失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'error': '删除失败'}), 500