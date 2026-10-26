#!/bin/bash
# ============================================================
# BOM3 生产机部署自查脚本
#
# 用途：
#   1. 检查生产机现有代码是否存在版本错配（重点：RBAC 相关模块）
#   2. 若已上传 dist 部署包，可与包内文件逐一比对，定位差异文件
#   3. 输出一份「是否可安全启动」的结论
#
# 用法：
#   bash check_deploy.sh                                  # 仅检查当前目录
#   bash check_deploy.sh /volume1/docker/BOM6.0.26.9.28   # 指定部署目录
#   bash check_deploy.sh /volume1/docker/BOM6.0.26.9.28 /volume1/docker/bom3_deploy.tar.gz
#                                                         # 指定部署目录 + 部署包（做比对）
#
# 兼容：群晖 DSM / Linux（bash 4+）
# ============================================================

set -u

# ---------- 入参 ----------
DEPLOY_DIR="${1:-$(pwd)}"                       # 部署目录，默认当前目录
PKG_PATH="${2:-}"                               # 部署包路径（可选）

# ---------- 颜色（无 tty 时自动降级） ----------
if [ -t 1 ]; then
    C_OK=$'\033[0;32m'; C_ERR=$'\033[0;31m'; C_WARN=$'\033[0;33m'
    C_INFO=$'\033[0;36m'; C_END=$'\033[0m'
else
    C_OK=''; C_ERR=''; C_WARN=''; C_INFO=''; C_END=''
fi

PASS=0   # 通过计数
FAIL=0   # 失败计数
WARN=0   # 警告计数

ok()   { echo "${C_OK}[通过]${C_END} $1"; PASS=$((PASS + 1)); }
bad()  { echo "${C_ERR}[失败]${C_END} $1"; FAIL=$((FAIL + 1)); }
warn() { echo "${C_WARN}[警告]${C_END} $1"; WARN=$((WARN + 1)); }
info() { echo "${C_INFO}$1${C_END}"; }

# ============================================================
# 检查项 1：RBAC 模型类是否齐全（本次故障的直接原因）
# 正确版本 models/rbac.py 应包含 5 个类，共 175 行左右
# ============================================================
check_rbac_model() {
    echo
    info "===== [1] 检查 models/rbac.py 模型类完整性 ====="

    local f="$DEPLOY_DIR/models/rbac.py"
    if [ ! -f "$f" ]; then
        bad "文件不存在: $f"
        return
    fi

    # 逐个类检查（大小写/缩进无关，只匹配类定义行）
    local cls
    for cls in Permission UserPermissionOverride PermissionGroup GroupPermission UserGroup; do
        if grep -q "^class ${cls}(db.Model)" "$f"; then
            ok "models/rbac.py 含 class ${cls}"
        else
            bad "models/rbac.py 缺少 class ${cls}"
        fi
    done

    # 行数粗判（正确版约 175 行）
    local lines
    lines=$(wc -l < "$f")
    if [ "$lines" -ge 150 ]; then
        ok "models/rbac.py 行数=$lines（符合新版）"
    else
        warn "models/rbac.py 行数=$lines，明显偏少，疑为旧版本"
    fi
}

# ============================================================
# 检查项 2：rbac_service.py 期望导入的类是否都能在 models.rbac 中找到
# 这是本次 ImportError 的根因：旧 model + 新 service
# ============================================================
check_rbac_service_consistency() {
    echo
    info "===== [2] 检查 utils/rbac_service.py 与 models/rbac.py 是否配套 ====="

    local svc="$DEPLOY_DIR/utils/rbac_service.py"
    local mdl="$DEPLOY_DIR/models/rbac.py"
    if [ ! -f "$svc" ] || [ ! -f "$mdl" ]; then
        bad "缺少文件: $svc 或 $mdl"
        return
    fi

    # 提取 service 从 models.rbac 导入的符号（形如 from models.rbac import A, B, C）
    local missing=""
    local sym
    for sym in PermissionGroup GroupPermission UserGroup UserPermissionOverride Permission; do
        # service 里引用了该符号，但 model 里没定义 => 版本错配
        if grep -q "\b${sym}\b" "$svc" && ! grep -q "^class ${sym}(db.Model)" "$mdl"; then
            missing="${missing} ${sym}"
        fi
    done

    if [ -z "$missing" ]; then
        ok "rbac_service 引用的模型类在 models/rbac.py 中均存在（配套正确）"
    else
        bad "版本错配！rbac_service 需要但 models/rbac.py 缺失:${missing}"
        bad "  => 这就是 ImportError 的根因，需用新版 models/rbac.py 覆盖"
    fi
}

# ============================================================
# 检查项 3：关键运行文件是否齐全（缺一即启动失败）
# ============================================================
check_core_files() {
    echo
    info "===== [3] 检查核心运行文件齐全性 ====="

    local f
    for f in \
        app.py app_factory.py config.py db.py query_cache.py \
        routes.py routes_cad.py routes_rbac.py routes_nas.py \
        routes_mes.py routes_mes_modules.py routes_user_mgmt.py \
        data_management_service.py db_maintenance.py auto_backup_service.py \
        invest_summary_engine.py auto_loading_engine.py \
        corrugated_location_service.py utils/rbac_service.py \
        utils/permission_catalog.py models/rbac.py; do
        if [ -f "$DEPLOY_DIR/$f" ]; then
            ok "$f"
        else
            bad "缺失: $f"
        fi
    done
}

# ============================================================
# 检查项 4（可选）：与 dist 部署包逐一比对关键文件
# 仅在传入第 2 个参数（部署包路径）时执行
# ============================================================
check_against_pkg() {
    echo
    info "===== [4] 与部署包比对（$PKG_PATH） ====="

    if [ -z "$PKG_PATH" ]; then
        warn "未提供部署包路径，跳过比对（如需请作为第 2 个参数传入）"
        return
    fi
    if [ ! -f "$PKG_PATH" ]; then
        bad "部署包不存在: $PKG_PATH"
        return
    fi

    # 解包到临时目录
    local tmp
    tmp=$(mktemp -d /tmp/bom3_pkg.XXXXXX) || { bad "无法创建临时目录"; return; }
    if ! tar -xzf "$PKG_PATH" -C "$tmp"; then
        bad "解包失败: $PKG_PATH"
        rm -rf "$tmp"
        return
    fi
    ok "部署包解包成功: $tmp/bom3"

    # 逐文件比对（先比大小，再比 md5；仅提示差异，不自动覆盖）
    local rel
    for rel in \
        app.py app_factory.py routes.py routes_rbac.py \
        models/rbac.py utils/rbac_service.py utils/permission_catalog.py \
        routes_user_mgmt.py data_management_service.py; do
        local src="$tmp/bom3/$rel" dst="$DEPLOY_DIR/$rel"
        if [ ! -f "$src" ]; then
            warn "包内无 $rel（跳过）"
            continue
        fi
        if [ ! -f "$dst" ]; then
            bad "生产机缺失 $rel（包内有）"
            continue
        fi
        if cmp -s "$src" "$dst"; then
            ok "$rel 与包内一致"
        else
            bad "$rel 与包内不一致（包内版本可能已修复此问题）"
        fi
    done

    echo
    info "提示：如需用包内文件覆盖，执行（先备份！）："
    echo "  cp -rf $tmp/bom3/. $DEPLOY_DIR/"
    echo "（临时目录 $tmp 处理完可删除：rm -rf $tmp）"
}

# ============================================================
# 主流程
# ============================================================
main() {
    echo "============================================================"
    echo " BOM3 生产机部署自查"
    echo " 部署目录: $DEPLOY_DIR"
    echo " 部署包  : ${PKG_PATH:-（未指定）}"
    echo "============================================================"

    if [ ! -d "$DEPLOY_DIR" ]; then
        bad "部署目录不存在: $DEPLOY_DIR"
        exit 1
    fi

    check_rbac_model
    check_rbac_service_consistency
    check_core_files
    check_against_pkg

    # ---------- 总结论 ----------
    echo
    echo "============================================================"
    echo " 自查结论：通过 $PASS 项，失败 $FAIL 项，警告 $WARN 项"
    if [ "$FAIL" -eq 0 ]; then
        echo " ${C_OK}>>> 可安全启动（bash start.sh）${C_END}"
    else
        echo " ${C_ERR}>>> 存在问题，请先修复失败项再启动${C_END}"
    fi
    echo "============================================================"
    return "$FAIL"
}

main
