#!/usr/bin/env bash
# ============================================================
#  BOM3 资料管理系统 - 一键启动脚本 (Linux)
#
#  功能:
#      1. 自动检测并激活 .venv 虚拟环境
#      2. 检查端口占用，必要时停止旧服务
#      3. 若服务已运行，直接打开浏览器
#      4. 若未运行，后台启动 waitress 服务
#      5. 等待就绪后打开浏览器，显示访问信息
#
#  用法:
#      bash start.sh                     # 一键启动
#      bash start.sh --port 8080         # 指定端口
#      bash start.sh --no-browser        # 不自动打开浏览器
#      bash start.sh --help              # 查看帮助
# ============================================================
set -euo pipefail

# ---------- 常量 ----------
BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 默认端口与 app.py 中 PORT 保持一致 (5004)，可用 APP_PORT 环境变量覆盖
DEFAULT_PORT=5004
PORT="$DEFAULT_PORT"
NO_BROWSER=false
TIMEOUT=90
LOG_FILE="$BASE_DIR/server.log"
VENV_DIR="$BASE_DIR/.venv"

# ---------- 颜色 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

# ---------- 帮助信息 ----------
usage() {
    cat <<EOF
BOM3 资料管理系统 - 一键启动脚本 (Linux)

用法:
    bash start.sh [选项]

选项:
    --port <端口号>       指定服务端口 (默认: $DEFAULT_PORT)
    --no-browser          启动后不自动打开浏览器
    --timeout <秒数>      等待服务就绪超时秒数 (默认: 90)
    --help, -h            显示此帮助信息

示例:
    bash start.sh                     # 一键启动
    bash start.sh --port 8080         # 指定端口 8080
    bash start.sh --no-browser        # 不自动打开浏览器
EOF
}

# ---------- 参数解析 ----------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port)
            if [[ -z "${2:-}" ]] || ! [[ "$2" =~ ^[0-9]+$ ]]; then
                echo -e "${RED}[错误] --port 需要一个数字参数${NC}"
                exit 1
            fi
            PORT="$2"
            shift 2
            ;;
        --no-browser)
            NO_BROWSER=true
            shift
            ;;
        --timeout)
            if [[ -z "${2:-}" ]] || ! [[ "$2" =~ ^[0-9]+$ ]]; then
                echo -e "${RED}[错误] --timeout 需要一个数字参数${NC}"
                exit 1
            fi
            TIMEOUT="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo -e "${RED}[错误] 未知参数: $1${NC}"
            usage
            exit 1
            ;;
    esac
done

# ---------- 工具函数 ----------

print_banner() {
    echo ""
    echo "  ____  ____  __  __   ___ "
    echo " | __ )| __ )|  \/  | |_ _|"
    echo " |  _ \|  _ \| |\/| |  | | "
    echo " | |_) | |_) | |  | |  | | "
    echo " |____/|____/|_|  |_| |___|"
    echo ""
    echo "============================================================"
    echo "  BOM3 资料管理系统 - 一键启动 (Linux)"
    echo "============================================================"
    echo ""
}

# 探测服务是否已就绪 (通过访问 /login)
is_server_up() {
    local port=$1
    if command -v curl &>/dev/null; then
        curl -sf -o /dev/null --max-time 2 "http://127.0.0.1:$port/login" 2>/dev/null
        return $?
    elif command -v wget &>/dev/null; then
        wget -q --spider --timeout=2 --tries=1 "http://127.0.0.1:$port/login" 2>/dev/null
        return $?
    else
        # 回退: 使用 bash 内建 /dev/tcp
        (echo > /dev/tcp/127.0.0.1/"$port") 2>/dev/null
        return $?
    fi
}

# 查找占用端口的 PID 列表
find_port_pids() {
    local port=$1
    local pids=()

    if command -v lsof &>/dev/null; then
        while IFS= read -r pid; do
            [[ -n "$pid" ]] && pids+=("$pid")
        done < <(lsof -ti :"$port" 2>/dev/null || true)
    fi

    if [[ ${#pids[@]} -eq 0 ]] && command -v ss &>/dev/null; then
        while IFS= read -r pid; do
            [[ -n "$pid" && "$pid" != "0" ]] && pids+=("$pid")
        done < <(ss -tlnp "sport = :$port" 2>/dev/null \
                 | grep -oP 'pid=\K[0-9]+' || true)
    fi

    if [[ ${#pids[@]} -eq 0 ]] && command -v fuser &>/dev/null; then
        while IFS= read -r pid; do
            [[ -n "$pid" ]] && pids+=("$pid")
        done < <(fuser "$port"/tcp 2>/dev/null || true)
    fi

    if [[ ${#pids[@]} -gt 0 ]]; then
        printf '%s\n' "${pids[@]}" | sort -un
    fi
}

# 打开默认浏览器
open_browser() {
    local port=$1
    local url="http://localhost:$port"
    if command -v xdg-open &>/dev/null; then
        xdg-open "$url" &>/dev/null &
    elif command -v gnome-open &>/dev/null; then
        gnome-open "$url" &>/dev/null &
    elif command -v open &>/dev/null; then
        open "$url" &>/dev/null &
    fi
}

# 显示 server.log 最后 N 行
show_log_tail() {
    local lines=${1:-25}
    if [[ -f "$LOG_FILE" ]]; then
        echo ""
        echo "  === server.log (最后 $lines 行) ==="
        tail -n "$lines" "$LOG_FILE" 2>/dev/null | sed 's/^/  /'
    fi
}

# ---------- 主流程 ----------

print_banner

# [1/5] 检查 Python 环境
echo -e "${CYAN}[1/5]${NC} 检查运行环境..."

PYTHON=""

# 优先使用 .venv 虚拟环境
if [[ -d "$VENV_DIR" ]]; then
    if [[ -x "$VENV_DIR/bin/python" ]]; then
        PYTHON="$VENV_DIR/bin/python"
        echo -e "      虚拟环境: ${GREEN}已找到${NC} ($VENV_DIR)"
    elif [[ -x "$VENV_DIR/bin/python3" ]]; then
        PYTHON="$VENV_DIR/bin/python3"
        echo -e "      虚拟环境: ${GREEN}已找到${NC} ($VENV_DIR)"
    else
        echo -e "      ${YELLOW}[警告]${NC} .venv 目录存在但未找到 Python 可执行文件"
    fi
fi

# 回退: 尝试激活 venv
if [[ -z "$PYTHON" ]] && [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
    if command -v python3 &>/dev/null; then
        PYTHON="$(command -v python3)"
        echo -e "      虚拟环境: ${GREEN}已激活${NC} (通过 source activate)"
    fi
fi

# 回退: 系统 Python（自动选择最高版本）
if [[ -z "$PYTHON" ]]; then
    BEST_PYTHON=""
    BEST_VER=0

    # 收集所有可能的 python3.x 可执行文件
    for candidate in $(compgen -c python3 | grep -E '^python3(\.[0-9]+)?$' | sort -t. -k2 -n -r); do
        cpath="$(command -v "$candidate" 2>/dev/null)" || continue
        [[ -x "$cpath" ]] || continue
        # 获取次版本号
        minor=$("$cpath" -c 'import sys; print(sys.version_info[1])' 2>/dev/null) || continue
        major=$("$cpath" -c 'import sys; print(sys.version_info[0])' 2>/dev/null) || continue
        [[ "$major" -eq 3 && "$minor" -ge 10 ]] || continue
        # 用  minor*1000 做比较，确保高版本优先
        score=$((minor * 1000))
        if [[ $score -gt $BEST_VER ]]; then
            BEST_VER=$score
            BEST_PYTHON="$cpath"
        fi
    done

    # 如果没找到 versioned 的，尝试 python3 / python
    if [[ -z "$BEST_PYTHON" ]]; then
        for cmd in python3 python; do
            cpath="$(command -v "$cmd" 2>/dev/null)" || continue
            [[ -x "$cpath" ]] || continue
            minor=$("$cpath" -c 'import sys; print(sys.version_info[1])' 2>/dev/null) || continue
            major=$("$cpath" -c 'import sys; print(sys.version_info[0])' 2>/dev/null) || continue
            if [[ "$major" -eq 3 && "$minor" -ge 10 ]]; then
                BEST_PYTHON="$cpath"
                break
            fi
        done
    fi

    if [[ -n "$BEST_PYTHON" ]]; then
        PYTHON="$BEST_PYTHON"
        echo -e "      ${YELLOW}[警告]${NC} 未找到 .venv，使用系统 Python: $PYTHON"
    else
        echo -e "      ${RED}[错误]${NC} 未找到 Python 3.10+，请先安装或创建 .venv 虚拟环境"
        echo "      当前系统可用的 Python:"
        for candidate in $(compgen -c python3 | grep -E '^python3' | sort -u); do
            cpath="$(command -v "$candidate" 2>/dev/null)" || continue
            [[ -x "$cpath" ]] || continue
            ver=$("$cpath" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null) || ver="unknown"
            echo "        $cpath -> $ver"
        done
        exit 1
    fi
fi

# 检查 Python 版本
PY_VERSION=$("$PYTHON" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info[0])')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info[1])')

if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 10 ]]; }; then
    echo -e "      ${RED}[错误]${NC} Python 版本 $PY_VERSION 过低，需要 3.10+"
    exit 1
fi
echo -e "      Python 版本: ${GREEN}$PY_VERSION${NC}"

# 检查关键依赖
echo "      依赖检测..."
MISSING_DEPS=false
for mod in flask flask_sqlalchemy flask_login pandas openpyxl waitress ezdxf; do
    if "$PYTHON" -c "import $mod" 2>/dev/null; then
        ver=$("$PYTHON" -c "import $mod; print(getattr($mod, '__version__', 'OK'))" 2>/dev/null || echo "OK")
        echo "        [$mod] 已安装 (v$ver)"
    else
        echo -e "        ${RED}[$mod] 未安装${NC}"
        MISSING_DEPS=true
    fi
done

if $MISSING_DEPS; then
    echo ""
    echo -e "      ${RED}[错误]${NC} 缺少依赖，请先执行:"
    echo "      $VENV_DIR/bin/pip install -r requirements.txt"
    echo "      或: $PYTHON -m pip install -r requirements.txt"
    exit 1
fi
echo -e "      依赖检查: ${GREEN}OK${NC}"
echo ""

# [2/5] 检查端口
echo -e "${CYAN}[2/5]${NC} 检查端口 $PORT..."

EXISTING_PIDS=($(find_port_pids "$PORT"))

if [[ ${#EXISTING_PIDS[@]} -gt 0 ]]; then
    echo "      端口 $PORT 已被占用 (PID: ${EXISTING_PIDS[*]})"

    # 检查是否是我们的服务
    if is_server_up "$PORT"; then
        echo -e "      ${GREEN}检测到服务已在运行中${NC}"
        echo ""

        echo -e "${CYAN}[3/5]${NC} 跳过启动 (服务已运行)"
        echo -e "${CYAN}[4/5]${NC} 服务就绪"
        echo ""
        echo "============================================================"
        echo -e "  ${GREEN}服务已在运行!${NC}"
        echo ""
        echo "  访问地址 : http://localhost:$PORT"
        echo "  管理员   : admin / admin123!"
        echo "  日志文件 : server.log"
        echo "  停止服务 : 结束进程 PID 或重启本脚本自动清理端口"
        echo "============================================================"
        echo ""

        if ! $NO_BROWSER; then
            echo "      正在打开浏览器..."
            open_browser "$PORT"
        fi
        exit 0
    else
        echo "      端口被其他程序占用，尝试停止旧服务..."
        # 调用 stop.sh 或直接 kill
        for pid in "${EXISTING_PIDS[@]}"; do
            kill "$pid" 2>/dev/null || true
        done
        sleep 2
        # 检查是否释放
        REMAINING=($(find_port_pids "$PORT"))
        if [[ ${#REMAINING[@]} -gt 0 ]]; then
            for pid in "${REMAINING[@]}"; do
                kill -9 "$pid" 2>/dev/null || true
            done
            sleep 1
        fi
        # 最终检查
        FINAL=($(find_port_pids "$PORT"))
        if [[ ${#FINAL[@]} -gt 0 ]]; then
            echo -e "      ${RED}[错误]${NC} 端口 $PORT 仍被占用，请手动释放"
            exit 1
        fi
        echo "      旧服务已停止"
    fi
else
    echo -e "      端口空闲: ${GREEN}OK${NC}"
fi
echo ""

# [3/5] 启动服务
echo -e "${CYAN}[3/5]${NC} 启动 waitress 服务..."

APP_PATH="$BASE_DIR/app.py"
if [[ ! -f "$APP_PATH" ]]; then
    echo -e "      ${RED}[错误]${NC} 未找到 $APP_PATH"
    exit 1
fi

# 设置环境变量
export FLASK_CONFIG=production
export PYTHONUNBUFFERED=1

# 预编译 .pyc：把源码字节码缓存提前生成，减少首次冷启动时逐模块编译的开销。
# 增量执行（已最新的文件会被跳过），失败不阻断启动。
echo "      预编译字节码..."
if "$PYTHON" -m compileall -q "$BASE_DIR/app.py" "$BASE_DIR/app_factory.py" \
        "$BASE_DIR/routes.py" "$BASE_DIR/models" "$BASE_DIR/utils" 2>/dev/null; then
    echo -e "      预编译: ${GREEN}OK${NC}"
else
    echo -e "      预编译: ${YELLOW}跳过(非致命)${NC}"
fi

# 后台启动，日志输出到 server.log
# 启动前做大小轮转：server.log 超过 10MB 则归档为 server-<时间戳>.log，
# 避免 nohup 追加导致单文件无限增长（Python 无法轮转被 shell 重定向的 fd）
MAX_LOG_BYTES=$((10 * 1024 * 1024))
if [[ -f "$LOG_FILE" ]] && [[ $(wc -c < "$LOG_FILE" 2>/dev/null || echo 0) -gt $MAX_LOG_BYTES ]]; then
    ARCHIVE_LOG="$BASE_DIR/server-$(date +%Y%m%d-%H%M%S).log"
    mv "$LOG_FILE" "$ARCHIVE_LOG" 2>/dev/null \
        && echo "      日志轮转: server.log -> $(basename "$ARCHIVE_LOG")"
    # 仅保留最近 5 个归档日志
    ls -1t "$BASE_DIR"/server-*.log 2>/dev/null | tail -n +6 | xargs -r rm -f
fi

nohup "$PYTHON" "$APP_PATH" >> "$LOG_FILE" 2>&1 &
SERVER_PID=$!
echo "      服务进程已启动 (PID: $SERVER_PID)"
echo "      日志输出: $LOG_FILE"
echo ""

# [4/5] 等待服务就绪
echo -e "${CYAN}[4/5]${NC} 等待服务就绪 (最多 $TIMEOUT 秒)..."

ELAPSED=0
while [[ $ELAPSED -lt $TIMEOUT ]]; do
    if is_server_up "$PORT"; then
        echo ""
        echo -e "      ${GREEN}服务已就绪${NC} (${ELAPSED}s)"
        break
    fi

    # 检查进程是否还活着
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo ""
        echo -e "      ${RED}[错误]${NC} 服务进程已退出"
        show_log_tail 20
        exit 1
    fi

    sleep 1
    ELAPSED=$((ELAPSED + 1))

    # 进度提示
    if [[ $((ELAPSED % 10)) -eq 0 ]]; then
        echo "      等待中... ${ELAPSED}s"
    fi
done

if ! is_server_up "$PORT"; then
    echo ""
    echo -e "      ${RED}[错误]${NC} 服务启动超时 ($TIMEOUT 秒)"
    show_log_tail 25
    exit 1
fi

# [5/5] 启动完成
echo -e "${CYAN}[5/5]${NC} 启动完成"
echo ""
echo "============================================================"
echo -e "  ${GREEN}${BOLD}启动成功!${NC}"
echo ""
echo "  访问地址 : http://localhost:$PORT"
echo "  管理员   : admin / admin123!"
echo "  日志文件 : server.log"
echo "  停止服务 : 结束进程 PID 或重启本脚本自动清理端口"
echo "  进程 PID : $SERVER_PID"
echo "============================================================"
echo ""

if ! $NO_BROWSER; then
    echo "      正在打开浏览器..."
    open_browser "$PORT"
fi
