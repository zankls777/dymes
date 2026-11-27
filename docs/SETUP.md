# BOM3 大焱资料管理系统 — 搭建说明

> 适用版本：2026-10 现行版本（Python 3.12 生产实测通过）
> 兼容系统：Windows 10/11、Linux（Win7 无法运行 Python 3.10+，仅可作为浏览器访问端）

---

## 1. 系统概述

大焱资料管理系统是一套基于 Flask 的 BOM/资料管理 Web 应用，涵盖：

- **资料管理**：SubList / UsageList 上传、查询、对比、冻结（EO 版本管控）
- **线束业务**：LOT 登录、扫描录入（扫码枪 + 标签拍照识别 OCR）、数据下载
- **投入计算**：投入计划书、胶带/皮子/管换算、资材量下载
- **资材入库**：资材每日入库（上传/手动/批量入库/扫码入库）、在库管理、移动端出入库、标签打印
- **生产业务**：MES（计划登录、生产入库、品质检查、单价登录、结算清单）、盘点
- **图纸工具**：RW 核对、RW 图纸产出（DXF 回路自动标记）
- **系统管理**：用户/权限/权限组、侧边栏菜单管理（拖拽分组、折叠）、数据备份（含自动备份到网盘）、操作日志
- **关于与更新**：版本信息、变更历史、在线更新（GitHub/Gitee、代理、加速源、自动重启）

## 2. 环境要求

| 项目 | 要求 |
|------|------|
| Python | **3.10+**（生产实测 3.12，推荐 `py -3.12`） |
| 操作系统 | Windows 10/11 或 Linux |
| 数据库 | SQLite（默认，零配置）／可选 MySQL |
| 浏览器 | Chrome / Edge / Firefox（访问端可为 Win7 + Chrome） |
| 磁盘 | 建议 ≥ 2GB（含数据库与上传文件） |
| 网络 | 可离线运行；OCR 识别为本地离线推理，无需联网 |

## 3. 目录结构

```
bom3/
├── app.py               # 启动入口（端口/管理员账号集中配置）
├── app_factory.py       # 应用工厂（蓝图注册、缓存预热、备份调度）
├── config.py            # 开发/生产/测试配置与数据库 URI
├── requirements.txt     # 依赖清单（含版本说明）
├── start.bat            # Windows 一键启动脚本
├── start.sh             # Linux 一键启动脚本
├── routes*.py           # 路由（主业务 / CAD / MES / NAS / RBAC / 用户管理）
├── utils/               # 工具（标签识别、DXF 标记、侧边栏菜单、在线更新 updater.py 等）
├── scripts/             # 运维脚本（回填、迁移、打包、restart_helper.py 更新重启助手）
├── models/              # 数据模型
├── templates/           # 页面模板
├── static/              # 静态资源（static/uploads 为上传文件目录）
├── instance/            # app.db、update_config.json（代理/加速源）、初始口令文件
├── logs/                # 运行日志（含 update_restart.log）
└── docs/                # 本文档
```

## 4. 安装步骤

### 4.1 安装 Python

1. 下载并安装 [Python 3.12](https://www.python.org/downloads/)（勾选 **Add Python to PATH**）。
2. 验证：`py -3.12 --version`

### 4.2 创建虚拟环境（推荐）

```bat
:: Windows
cd /d f:\TA\bom3
py -3.12 -m venv .venv
.venv\Scripts\activate
```

```bash
# Linux
cd /path/to/bom3
python3.12 -m venv .venv
source .venv/bin/activate
```

### 4.3 安装依赖

```bash
pip install -r requirements.txt

# 网络较慢时使用清华镜像：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4.4 安装 OCR 识别四件套（拍照识别功能必需）

> **关键**：四件套必须装到「运行服务的同一个解释器」中。
> 缺任何一个，标签拍照识别会全字段「未识别」（服务不报错，易误判为照片问题）。

在线机器：

```bash
py -3.12 -m pip install zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow
```

内网离线机器（先在有网机器下载）：

```bash
# 有网机：下载到 pkgs 目录（注意与目标机同平台同 Python 版本）
py -m pip download zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow -d ./pkgs

# 内网机：从本地目录安装
py -m pip install --no-index --find-links=./pkgs zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow
```

> Linux/NAS 说明：必须用 `opencv-python-headless`。普通 `opencv-python` 依赖桌面库，
> 无桌面环境会报 `libxcb.so.1` / `libGL.so.1: cannot open shared object file`，
> 表现为「识别引擎异常」而非缺包。若已装普通版，先 `pip uninstall -y opencv-python` 再装 headless。

验证（在运行服务的解释器下执行，四项都能打印版本即 OK）：

```bash
python -c "import zxingcpp, rapidocr_onnxruntime, cv2, PIL; print('OK')"
```

## 5. 配置说明

所有配置均有默认值，**零配置即可启动**。需要调整时通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `APP_PORT` | `5004` | 服务端口 |
| `ADMIN_USERNAME` | `admin` | 初始管理员账号 |
| `ADMIN_PASSWORD` | 自动生成 | 未设置时生成 16 位强随机口令，写入 `instance/admin_initial_password.txt`（重启复用） |
| `FLASK_CONFIG` | `development` | `production`（Waitress 多线程）/ `development`（Flask 热重载调试） |
| `DATABASE_TYPE` | `sqlite` | `sqlite` / `mysql` |
| `SQLITE_PATH` | `instance/app.db` | SQLite 库文件路径（建议绝对路径） |
| `MYSQL_HOST` 等 | — | DATABASE_TYPE=mysql 时生效（MYSQL_HOST/PORT/USER/PASSWORD/DATABASE） |
| `SECRET_KEY` | 自动生成 | 未设置时生成并持久化到根目录 `.secret_key`（勿提交仓库） |
| `SESSION_LIFETIME` | `43200` | 会话有效期（秒，默认 12 小时） |

端口与管理员账号也可直接在 [app.py](file:///f:/TA/bom3/app.py) 顶部修改。

## 6. 启动与停止

### 6.1 Windows（推荐双击 `start.bat`）

`start.bat` 自动完成：Python 版本检查 → 依赖检查 → 端口占用清理 → 生产模式启动 → 自动打开浏览器。

```bat
start.bat
```

### 6.2 Linux

```bash
bash start.sh                     # 一键启动（自动预热、等待就绪、打开浏览器）
bash start.sh --port 8080         # 指定端口
bash start.sh --no-browser        # 不打开浏览器
```

停止服务：`ps -ef | grep app.py` 找到 PID 后 `kill <PID>`；重启脚本会自动清理端口。

### 6.3 手动启动

```bash
python app.py                    # 默认 development 模式（调试热重载）
# 生产模式：
#   Windows:  set FLASK_CONFIG=production && python app.py
#   Linux:    FLASK_CONFIG=production python app.py
```

### 6.4 验证

浏览器访问 `http://localhost:5004`，使用管理员账号登录即搭建完成。

- 默认账号：`admin`；密码为 `ADMIN_PASSWORD` 的值，未设置时见 `instance/admin_initial_password.txt`（**上线前请立即修改密码**）
- 首次启动自动建库建表（`instance/app.db`），并预热数据缓存

## 7. 在线更新与网络配置（可选）

系统内置“关于”页（`/about`）与在线更新能力，适合内网 / NAS 环境一键升级。

### 7.1 更新机制

| 场景 | 行为 |
|------|------|
| 目录含 `.git` 且系统有 git | `git fetch` + `--ff-only` 快进合并 |
| 无 `.git`（部署包），有 git | 浅克隆源码后覆盖（GitHub → Gitee 回退） |
| 无 git，已安装 dulwich | 纯 Python 浅克隆后覆盖 |
| 以上均不可用 | 下载 GitHub/Gitee 压缩包覆盖（校验文件头，防止保存到登录页） |

- 覆盖更新始终保留 `instance/`、`logs/`、`backups/`、`.venv/`、`.git/` 等运行数据，覆盖前自动备份到 `backups/auto_update_<时间戳>/`。
- 更新完成后由 `scripts/restart_helper.py` 等待旧进程退出再拉起服务，日志见 `logs/update_restart.log`。

### 7.2 网络加速（GitHub 无法直连时）

在“关于 → 网络与加速设置”中配置，保存在 `instance/update_config.json`：

| 配置 | 示例 | 说明 |
|------|------|------|
| 代理地址 | `http://172.16.17.190:7890` | 检查更新、git/dulwich 拉取、压缩包下载全部走代理 |
| GitHub 加速源 | `https://gh-proxy.com/` | 拼接为 `https://gh-proxy.com/https://github.com/...`，失败自动回退直连与 Gitee |

- 页面提供“测试连接 / 测试加速”按钮，可先验证再保存。
- 代理软件需开启“允许局域网连接”，且服务器能访问代理机端口。
- 也可通过环境变量 `HTTP_PROXY` / `HTTPS_PROXY` 提供默认代理（页面配置优先）。

### 7.3 NAS 首次启用在线更新

1. 将 `utils/updater.py`、`scripts/restart_helper.py`、`templates/about.html` 等新增文件同步到 NAS（部署包升级时可用整包覆盖）。
2. 确认 `git --version` 可用；否则在虚拟环境执行 `./.venv/bin/python -m pip install dulwich`。
3. `bash start.sh` 启动后，用管理员登录“关于”页，配置代理 / 加速源并测试。
4. 点击“检查更新 → 更新并重启”，观察进度与 `logs/update_restart.log`。

## 8. 常见问题（搭建类）

| 现象 | 原因与处理 |
|------|-----------|
| 启动报 `ModuleNotFoundError` | 依赖装错解释器。确认用同一解释器执行 `pip install -r requirements.txt` 并用同一解释器启动 |
| 拍照识别全字段「未识别」 | OCR 四件套未装进运行服务的解释器，见 4.4 验证命令 |
| 端口 5004 被占用 | `start.bat`/`start.sh` 会自动清理旧进程；手动处理：`netstat -ano | findstr :5004` 后 `taskkill /F /PID <pid>` |
| 改了模板/代码不生效 | 生产模式（Waitress）启动时缓存 Jinja 模板，**必须重启服务**才生效 |
| 冷启动较慢（首次请求 10s+） | 属正常现象，应用启动时后台线程自动预热缓存，之后热请求为毫秒级 |
| 想升级/迁移数据库 | 使用 `scripts/` 下脚本或 Flask-Migrate；升级前先在「数据备份管理」做全量备份 |
| 在线更新超时 / 连接重置 | 在“关于”页配置代理或 GitHub 加速源并测试；无 git 时安装 git 或 `pip install dulwich` |
| 在线更新提示“不是压缩包” | 归档下载被登录页/网关拦截，改用 git/dulwich 拉取，或配置代理后重试 |
| 更新后服务未自动拉起 | 查看 `logs/update_restart.log`，确认 `scripts/restart_helper.py` 存在 |
| Win7 部署 | 不支持（Python 3.10+ 不再兼容 Win7）。Win7 机器仅作为浏览器访问端使用 |

## 9. 数据备份建议

- 系统内置两套备份：**数据备份管理**页面的手动备份/恢复，以及**自动备份到网盘**（SQLite 在线快照全量 + 增量 Excel，定时调度）。
- 建议开启自动备份并指向网盘/UNC 目录，保留份数按需设置（默认全量 30 份、增量 90 份）。
- 迁移机器：拷贝整个项目目录（重点是 `instance/app.db` 与 `static/uploads/`）即可。
