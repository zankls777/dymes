"""
操作记录工具模块
提供统一的操作记录功能
"""

from flask import request, session, current_app
from models.operation_log import OperationLog, OperationType, TargetType
from app_factory import db
from datetime import datetime
from utils.time_utils import get_utc8_time, format_utc8_time
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:
    ZoneInfo = None


def log_operation(operation_type, operation_desc, target_type=None, target_id=None, 
                 target_name=None, file_path=None, user_id=None):
    """
    记录操作日志
    
    Args:
        operation_type: 操作类型（OperationType常量）
        operation_desc: 操作描述
        target_type: 目标类型（TargetType常量）
        target_id: 目标ID
        target_name: 目标名称
        file_path: 文件路径
        user_id: 用户ID（如果不提供则从session获取）
    """
    try:
        # 获取用户ID
        if user_id is None:
            try:
                user_id = session.get('user_id')
            except RuntimeError:
                # 没有请求上下文时使用默认值
                user_id = None
        
        if not user_id:
            return False

        # 解析操作用户名快照：账号后续被删除时，历史日志仍可显示文本
        username_snapshot = None
        try:
            from models.user_info import UserInfo
            u = db.session.get(UserInfo, user_id)
            if u:
                username_snapshot = getattr(u, 'username', None) or getattr(u, 'full_name', None)
        except Exception:
            username_snapshot = None

        # 获取请求信息
        try:
            ip_address = request.remote_addr if request else None
            user_agent = request.headers.get('User-Agent') if request else None
        except RuntimeError:
            # 没有请求上下文时使用默认值
            ip_address = None
            user_agent = None
        
        # 创建操作记录（使用UTC+8时区）
        now_utc8 = get_utc8_time()
        log = OperationLog(
            user_id=user_id,
            username=username_snapshot,
            operation_type=operation_type,
            operation_desc=operation_desc,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            file_path=file_path,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=now_utc8
        )
        
        db.session.add(log)
        db.session.commit()
        # 同步写入应用日志，便于在 dayan-YYYY-MM-DD.log 中检索
        try:
            if current_app and current_app.logger:
                current_app.logger.info(
                    f"OPLOG user_id={user_id} type={operation_type} desc={operation_desc} target={target_type}"
                )
        except Exception:
            pass
        return True
        
    except Exception as e:
        # 增强错误日志记录
        try:
            if current_app and current_app.logger:
                current_app.logger.error(f"记录操作日志失败: {str(e)}", exc_info=True)
                current_app.logger.error(f"操作类型: {operation_type}")
                current_app.logger.error(f"操作描述: {operation_desc}")
                current_app.logger.error(f"用户ID: {user_id}")
        except Exception:
            pass
        print(f"记录操作日志失败: {str(e)}")
        db.session.rollback()
        return False


def log_file_upload(filename, file_type, file_size=None):
    """记录文件上传操作"""
    desc = f"上传文件: {filename}"
    if file_size:
        desc += f" (大小: {file_size} bytes)"
    
    return log_operation(
        operation_type=OperationType.UPLOAD_FILE,
        operation_desc=desc,
        target_type=TargetType.FILE,
        target_name=filename,
        file_path=filename
    )


def log_batch_file_upload(filenames, count=None, success_files=None, duplicate_files=None, user_id=None):
    """记录批量文件上传操作（合并为一条记录）"""
    # 规范化并记录明细，前端可用"查看明细"弹窗分页展示
    if filenames:
        filenames = [fn.strip() for fn in filenames if fn and str(fn).strip()]
        filenames = sorted(set(filenames))
    else:
        filenames = []
    
    # 构建详细描述
    success_count = len(success_files) if success_files else 0
    duplicate_count = len(duplicate_files) if duplicate_files else 0
    
    # 如果没有文件名但有成功或重复文件，从这些列表中获取文件名
    if not filenames and (success_files or duplicate_files):
        all_files = []
        if success_files:
            all_files.extend(success_files)
        if duplicate_files:
            all_files.extend(duplicate_files)
        filenames = sorted(set(all_files))
    
    # 如果仍然没有文件名，但有计数信息，仍然记录日志
    if not filenames and (success_count > 0 or duplicate_count > 0):
        # 使用计数信息创建描述
        if success_count > 0 and duplicate_count > 0:
            desc = f"文件上传: 成功{success_count}个、重复过滤{duplicate_count}个"
        elif success_count > 0:
            desc = f"文件上传: 成功{success_count}个"
        elif duplicate_count > 0:
            desc = f"文件上传: 成功{success_count}个、重复过滤{duplicate_count}个"
        else:
            desc = f"批量上传文件: {count if count is not None else 0}个文件"
        
        # 构建文件路径信息
        file_path_parts = []
        if success_files:
            file_path_parts.append(f"SUCCESS:{'||'.join(success_files)}")
        if duplicate_files:
            file_path_parts.append(f"DUPLICATE:{'||'.join(duplicate_files)}")
        
        file_path = '|||'.join(file_path_parts) if file_path_parts else None
        
        return log_operation(
            operation_type=OperationType.UPLOAD_FILE,
            operation_desc=desc,
            target_type=TargetType.FILE,
            file_path=file_path,
            user_id=user_id
        )
    
    # 如果没有任何文件信息，不记录日志
    if not filenames:
        return False
    
    # 优先显示成功和重复的完整信息
    if success_count > 0 and duplicate_count > 0:
        desc = f"文件上传: 成功{success_count}个、重复过滤{duplicate_count}个"
    elif success_count > 0:
        desc = f"文件上传: 成功{success_count}个"
    elif duplicate_count > 0:
        # 即使只有重复文件，也要显示成功数量（可能为0）
        desc = f"文件上传: 成功{success_count}个、重复过滤{duplicate_count}个"
    else:
        desc = f"批量上传文件: {count if count is not None else len(filenames)}个文件"
    
    # 构建文件路径信息，包含成功和重复文件的分类
    file_path_parts = []
    if success_files:
        file_path_parts.append(f"SUCCESS:{'||'.join(success_files)}")
    if duplicate_files:
        file_path_parts.append(f"DUPLICATE:{'||'.join(duplicate_files)}")
    
    # 使用特殊分隔符来分隔SUCCESS和DUPLICATE部分，避免与文件名中的|冲突
    file_path = '|||'.join(file_path_parts) if file_path_parts else '||'.join(filenames)
    
    
    return log_operation(
        operation_type=OperationType.UPLOAD_FILE,  # 仍按上传统计
        operation_desc=desc,
        target_type=TargetType.FILE,
        file_path=file_path,
        user_id=user_id
    )


def log_batch_file_upload_start(total_count: int) -> int:
    """记录批量上传开始，返回日志ID，后续用 finalize 更新。

    为避免并发冲突，这里直接插入一条记录并返回其ID。
    """
    try:
        uid = session.get('user_id')
        if not uid:
            return 0
        ip_address = request.remote_addr if request else None
        user_agent = request.headers.get('User-Agent') if request else None
        desc = f"批量上传开始: 预估 {int(total_count)} 个文件" if total_count else "批量上传开始"
        # 操作用户名快照：账号删除后日志仍可显示文本
        username_snapshot = None
        try:
            from models.user_info import UserInfo
            u = db.session.get(UserInfo, uid)
            if u:
                username_snapshot = getattr(u, 'username', None) or getattr(u, 'full_name', None)
        except Exception:
            username_snapshot = None
        # 使用中国时区时间
        now_cn = datetime.now(ZoneInfo('Asia/Shanghai')) if ZoneInfo else datetime.now()
        log = OperationLog(
            user_id=uid,
            username=username_snapshot,
            operation_type=OperationType.UPLOAD_FILE,
            operation_desc=desc,
            target_type=TargetType.FILE,
            created_at=now_cn.replace(tzinfo=None),
            ip_address=ip_address,
            user_agent=user_agent,
        )
        db.session.add(log)
        db.session.commit()
        return int(log.id)
    except Exception:
        db.session.rollback()
        return 0


def log_batch_file_upload_finalize(log_id: int, filenames, success_count: int):
    """将开始日志更新为完成状态，并写入文件名明细。如果成功数量为0，则删除该记录。"""
    try:
        if not log_id:
            return False
        
        # 如果成功上传的文件数量为0，删除这个操作记录
        if success_count <= 0:
            log = db.session.get(OperationLog, log_id)
            if log:
                db.session.delete(log)
                db.session.commit()
            return True
        
        filenames = [fn.strip() for fn in (filenames or []) if fn and str(fn).strip()]
        filenames = sorted(set(filenames))
        log = db.session.get(OperationLog, log_id)
        if not log:
            return False
        log.operation_desc = f"批量上传文件: {int(success_count)}个文件"
        log.file_path = '||'.join(filenames) if filenames else None
        db.session.commit()
        return True
    except Exception:
        db.session.rollback()
        return False

def log_file_delete(filename, file_id=None, user_id=None):
    """记录文件删除操作"""
    return log_operation(
        operation_type=OperationType.DELETE_FILE,
        operation_desc=f"删除文件: {filename}",
        target_type=TargetType.FILE,
        target_id=file_id,
        target_name=filename,
        file_path=filename,
        user_id=user_id
    )


def log_batch_file_delete(filenames, count=None, user_id=None):
    """记录批量文件删除操作（跳过0条的尝试记录，避免两条）。"""
    # 规范化文件名列表（去空、去重、排序）
    filenames = [fn.strip() for fn in (filenames or []) if fn and str(fn).strip()]
    filenames = sorted(set(filenames))

    # 若没有任何删除，直接跳过日志，避免出现"0个文件"的多余记录
    if count is None or int(count) <= 0:
        return True

    desc = f"批量删除文件: {int(count)}个文件"

    canonical_path = '||'.join(filenames) if filenames else None

    # 正常（count>0）的批量删除每次都记一条，不做时间窗口抑制

    return log_operation(
        operation_type=OperationType.BATCH_DELETE_FILE,
        operation_desc=desc,
        target_type=TargetType.FILE,
        file_path=canonical_path,
        user_id=user_id
    )


def log_file_download(filename, download_type='file'):
    """记录文件下载操作"""
    operation_type = OperationType.DOWNLOAD_FILE if download_type == 'file' else OperationType.DOWNLOAD_RESULT
    desc = f"下载{download_type}: {filename}"
    
    return log_operation(
        operation_type=operation_type,
        operation_desc=desc,
        target_type=TargetType.FILE,
        target_name=filename,
        file_path=filename
    )


def log_file_compare(file1_name, file2_name, compare_mode='key'):
    """记录文件对比操作"""
    mode_desc = "关键列对比" if compare_mode == 'key' else "合并单元列对比"
    return log_operation(
        operation_type=OperationType.FILE_COMPARE,
        operation_desc=f"文件对比: {file1_name} vs {file2_name} ({mode_desc})",
        target_type=TargetType.FILE
    )


def log_user_operation(operation_type, username, target_id=None):
    """记录用户相关操作"""
    desc_map = {
        OperationType.ADD_USER: f"添加用户: {username}",
        OperationType.DELETE_USER: f"删除用户: {username}",
        OperationType.UPDATE_USER: f"更新用户: {username}",
        OperationType.CHANGE_PASSWORD: f"修改密码: {username}",
        OperationType.RESET_PASSWORD: f"重置密码: {username}",
        OperationType.UPDATE_PERMISSIONS: f"更新权限: {username}"
    }
    
    return log_operation(
        operation_type=operation_type,
        operation_desc=desc_map.get(operation_type, f"用户操作: {username}"),
        target_type=TargetType.USER,
        target_id=target_id,
        target_name=username
    )


def log_vehicle_type_operation(operation_type, vehicle_type_name, target_id=None):
    """记录车种相关操作"""
    desc_map = {
        OperationType.ADD_VEHICLE_TYPE: f"添加车种: {vehicle_type_name}",
        OperationType.DELETE_VEHICLE_TYPE: f"删除车种: {vehicle_type_name}",
        OperationType.UPDATE_VEHICLE_TYPE: f"更新车种: {vehicle_type_name}"
    }
    
    return log_operation(
        operation_type=operation_type,
        operation_desc=desc_map.get(operation_type, f"车种操作: {vehicle_type_name}"),
        target_type=TargetType.VEHICLE_TYPE,
        target_id=target_id,
        target_name=vehicle_type_name
    )


def log_product_operation(operation_type, product_name, target_id=None):
    """记录成品相关操作"""
    desc_map = {
        OperationType.ADD_PRODUCT: f"添加成品: {product_name}",
        OperationType.DELETE_PRODUCT: f"删除成品: {product_name}",
        OperationType.UPDATE_PRODUCT: f"更新成品: {product_name}",
        OperationType.BATCH_ADD_PRODUCT: f"批量添加成品: {product_name}",
        OperationType.BATCH_DELETE_PRODUCT: f"批量删除成品: {product_name}"
    }
    
    return log_operation(
        operation_type=operation_type,
        operation_desc=desc_map.get(operation_type, f"成品操作: {product_name}"),
        target_type=TargetType.PRODUCT,
        target_id=target_id,
        target_name=product_name
    )


# ==================== 通用埋点辅助（供各业务模块复用） ====================

def log_action(operation_type, operation_desc, target_type=None, target_id=None,
               target_name=None, detail=None):
    """通用操作记录：统一 try/except 包裹，失败绝不影响主流程。

    detail: 追加到 file_path 字段的明细字符串（前端"查看明细"可回读）。
    """
    try:
        return log_operation(
            operation_type=operation_type,
            operation_desc=operation_desc,
            target_type=target_type,
            target_id=target_id,
            target_name=target_name,
            file_path=detail,
        )
    except Exception:
        # log_operation 内部已兜底，这里再保一层，确保永不抛出
        return False


def log_login(username, success=True):
    """记录登录操作"""
    desc = f"用户登录: {username}" if success else f"登录失败: {username}"
    return log_operation(
        operation_type=OperationType.LOGIN,
        operation_desc=desc,
        target_type=TargetType.USER,
        target_name=username
    )


def log_logout(username):
    """记录登出操作"""
    return log_operation(
        operation_type=OperationType.LOGOUT,
        operation_desc=f"用户登出: {username}",
        target_type=TargetType.USER,
        target_name=username
    )
