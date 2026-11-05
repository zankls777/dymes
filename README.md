# BOM3 大焱资料管理系统

基于 Flask 的 BOM / 资料管理 Web 应用，覆盖资料对比、线束业务、投入计算、MES 生产业务、图纸工具与系统权限管理，支持 SQLite / MySQL 双数据库，可离线部署于内网环境。

---

## 目录

- [1. 功能特性](#1-功能特性)
- [2. 技术栈](#2-技术栈)
- [3. 环境依赖要求](#3-环境依赖要求)
- [4. 项目目录结构](#4-项目目录结构)
- [5. 搭建部署步骤](#5-搭建部署步骤)
- [6. 配置说明](#6-配置说明)
- [7. 启动与停止](#7-启动与停止)
- [8. 功能使用方法](#8-功能使用方法)
- [9. 运维脚本](#9-运维脚本)
- [10. 常见问题](#10-常见问题)
- [11. 相关文档](#11-相关文档)

---

## 1. 功能特性

| 模块 | 功能说明 |
|------|----------|
| 资料管理 | SubList / UsageList 上传、查询、单文件与多文件对比、差异导出、EO 版本冻结/解冻管控 |
| 线束业务 | LOT 登录、扫码枪录入、标签拍照识别（二维码 + OCR 离线识别）、数据下载 |
| 投入计算 | 投入计划书、胶带/皮子/管自动换算、资材量下载、资材入库 |
| MES 生产 | 计划登录、生产入库、品质检查、不良登录、单价登录、结算清单、盘点 |
| 图纸工具 | RW 核对、RW 图纸产出（DXF 回路自动标记，基于 ezdxf） |
| 系统管理 | 用户管理、RBAC 权限组（页面/按钮级）、操作日志、数据备份（手动 + 自动备份到网盘） |

## 2. 技术栈

- **后端**：Python + Flask 3（应用工厂模式）、SQLAlchemy 2、Flask-Migrate、Flask-Login、Flask-Caching、Flask-WTF
- **WSGI**：Waitress（生产）/ Flask 内置服务器（开发）
- **数据库**：SQLite（默认，零配置）/ MySQL（可选，通过环境变量切换）
- **数据处理**：pandas、numpy、openpyxl、xlrd、ezdxf
- **离线识别**：zxing-cpp（条码/二维码）、RapidOCR + onnxruntime（文字识别）、OpenCV headless
- **前端**：Jinja2 模板 + 原生 JS + Tailwind CSS（node_modules 仅用于样式构建，非运行必需）

## 3. 环境依赖要求

| 项目 | 要求 |
|------|------|
| Python | **3.10+**（生产实测 3.12，推荐使用 3.12） |
| 操作系统 | Windows 10/11 或 Linux（Win7 无法运行 Python 3.10+，仅可作为浏览器访问端） |
| 数据库 | SQLite（默认，免安装）／MySQL 5.7+（可选） |
| 浏览器 | Chrome / Edge / Firefox |
| 磁盘 | 建议 ≥ 2GB（含数据库与上传文件） |
| 网络 | 可完全离线运行；OCR 为本地推理，无需联网 |

## 4. 项目目录结构

```
bom3/
├── app.py                  # 启动入口（端口 / 管理员账号集中配置）
├── app_factory.py          # 应用工厂（蓝图注册、缓存预热、备份调度）
├── config.py               # 开发 / 生产 / 测试配置与数据库 URI
├── requirements.txt        # Python 依赖清单
├── start.bat               # Windows 一键启动脚本
├── start.sh                # Linux 一键启动脚本
├── routes.py               # 主业务路由
├── routes_cad.py           # CAD / 图纸路由
├── routes_mes.py           # MES 路由
├── routes_mes_modules.py   # MES 子模块路由
├── routes_nas.py           # NAS 文件搜索路由
├── routes_rbac.py          # 权限管理路由
├── routes_user_mgmt.py     # 用户管理路由
├── models/                 # 数据模型（SQLAlchemy）
├── forms/                  # 表单定义（Flask-WTF）
├── utils/                  # 工具库（标签识别、DXF 标记、RBAC、日志、导出等）
├── security/               # 安全组件（防暴力破解等）
├── templates/              # Jinja2 页面模板
├── static/                 # 静态资源（static/uploads 为上传文件目录）
├── instance/               # SQLite 数据库与初始口令文件（运行时生成，勿提交）
├── migrations/             # Flask-Migrate 数据库迁移
├── scripts/                # 运维脚本（回填、迁移、打包、部署检查）
├── tests/                  # 测试用例
├── docs/                   # 搭建与使用详细文档
└── logs/                   # 运行日志（运行时生成）
```

## 5. 搭建部署步骤

### 5.1 安装 Python

1. 下载安装 [Python 3.12](https://www.python.org/downloads/)（Windows 安装时勾选 **Add Python to PATH**）。
2. 验证：`py -3.12 --version`（Windows）或 `python3.12 --version`（Linux）。

### 5.2 获取代码并创建虚拟环境

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

### 5.3 安装依赖

```bash
pip install -r requirements.txt

# 网络较慢时使用清华镜像：
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 5.4 安装 OCR 识别四件套（标签拍照识别功能必需）

> **关键**：必须安装到「运行服务的同一个 Python 解释器」中。
> 缺任何一个，拍照识别会全字段「未识别」，且服务不报错，易误判为照片问题。

在线机器：

```bash
pip install zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow
```

内网离线机器（先在有网机器下载离线包，注意与目标机同平台、同 Python 版本）：

```bash
# 有网机
pip download zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow -d ./pkgs
# 内网机
pip install --no-index --find-links=./pkgs zxing-cpp rapidocr-onnxruntime opencv-python-headless Pillow
```

> Linux / NAS 必须使用 `opencv-python-headless`；普通 `opencv-python` 在无桌面环境下会报 `libGL.so.1` / `libxcb.so.1` 错误。

验证（四项均能导入即正常）：

```bash
python -c "import zxingcpp, rapidocr_onnxruntime, cv2, PIL; print('OK')"
```

### 5.5 初始化与验证

无需手动建库。首次启动自动完成建库建表（`instance/app.db`）、创建管理员账号并预热缓存。

启动后浏览器访问 `http://localhost:5004`，能打开登录页即搭建完成。

## 6. 配置说明

所有配置均有安全默认值，**零配置即可启动**。需要调整时通过环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `APP_PORT` | `5004` | 服务端口 |
| `FLASK_CONFIG` | `development` | `production`（Waitress 多线程）/ `development`（调试热重载） |
| `ADMIN_USERNAME` | `admin` | 初始管理员账号 |
| `ADMIN_PASSWORD` | 自动生成 | 未设置时生成 16 位强随机口令，写入 `instance/admin_initial_password.txt`（重启复用不丢失） |
| `SECRET_KEY` | 自动生成 | 未设置时生成强随机密钥并持久化到根目录 `.secret_key`；生产环境建议显式设置 |
| `DATABASE_TYPE` | `sqlite` | `sqlite` / `mysql` |
| `SQLITE_PATH` | `instance/app.db` | SQLite 库文件路径（建议绝对路径，避免被代码更新覆盖） |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | — | `DATABASE_TYPE=mysql` 时生效 |
| `SESSION_LIFETIME` | `43200` | 会话有效期（秒，默认 12 小时） |
| `SESSION_COOKIE_SECURE` | `false` | 部署 HTTPS 后设为 `true` |

可参考根目录 `.env.example` 创建 `.env` 文件。端口与管理员账号也可直接在 [app.py](app.py) 顶部修改。

> **安全提示**：首次登录后请立即在「用户管理」修改管理员口令；生产环境务必显式设置 `SECRET_KEY` 与强口令。

## 7. 启动与停止

### 7.1 Windows（推荐）

双击 `start.bat`，自动完成：解释器探测 → 依赖检查 → 端口占用清理 → 生产模式（Waitress）启动 → 自动打开浏览器。所有失败路径均会暂停显示原因，窗口不会闪退。

### 7.2 Linux

```bash
chmod +x start.sh                 # 首次使用赋予执行权限
bash start.sh                     # 一键启动（后台运行、等待就绪、打开浏览器）
bash start.sh --port 8080         # 指定端口
bash start.sh --no-browser        # 不自动打开浏览器
bash start.sh --help              # 查看全部选项
```

停止服务：`ps -ef | grep app.py` 找到 PID 后 `kill <PID>`；再次执行启动脚本会自动清理端口占用。

### 7.3 手动启动

```bash
python app.py                                       # 开发模式（默认，热重载）
# 生产模式：
#   Windows:  set FLASK_CONFIG=production && python app.py
#   Linux:    FLASK_CONFIG=production python app.py
```

### 7.4 登录

- 访问 `http://localhost:5004`
- 账号：默认 `admin`
- 密码：设置了 `ADMIN_PASSWORD` 则用其值；否则查看 `instance/admin_initial_password.txt` 中自动生成的初始口令

## 8. 功能使用方法

### 8.1 界面基础

- 系统为**多标签页**设计：点击菜单在新标签打开页面；标签页内按 F5 刷新会关闭当前标签并跳到相邻标签（首页除外）。
- 左侧菜单按权限显示，无权限的菜单自动隐藏；直接访问无权限地址会被拦截。

### 8.2 文件管理与对比（资料管理）

1. **上传**：在文件管理页上传 SubList / UsageList（.xlsx / .xls），支持查看、下载、删除。
2. **版本冻结**：同一品番出现新版 EO 时旧版自动冻结，冻结数据禁止调用；需要时手动「解冻」，支持批量与「一键冻结旧版本」。
3. **对比**：选择单文件或多文件执行对比，输出差异明细并支持导出。

### 8.3 线束录入与拍照识别

1. 在 LOT 登录页建立批次。
2. 扫描录入支持扫码枪；也可拍摄线束标签照片，系统自动识别品番、LOT、机台号、端子与压着高度（二维码 + OCR 双重离线识别）。
3. 若识别全字段「未识别」，按 5.4 节检查 OCR 四件套是否装入运行解释器。

### 8.4 投入计算与 MES

- 投入计算：录入投入计划书，系统自动完成胶带/皮子/管换算，支持资材量下载与资材入库登记。
- MES：按流程使用计划登录 → 生产入库 → 品质检查 → 不良登录 → 单价登录 → 结算清单；支持库存盘点。

### 8.5 图纸工具

- RW 核对：上传图纸与数据核对。
- RW 图纸产出：自动在 DXF 图纸上标记回路并生成结果文件。

### 8.6 系统管理

- **用户与权限**：用户管理页维护账号；权限组支持页面级与按钮级授权，并提供实时权限预览。
- **数据备份**：「数据备份管理」页支持手动备份/恢复；自动备份按调度生成 SQLite 在线快照（全量）与增量 Excel，可指向网盘 / UNC 目录（默认保留全量 30 份、增量 90 份）。
- **操作日志**：记录关键操作，支持审计查询。

## 9. 运维脚本

`scripts/` 目录提供常用运维工具：

| 脚本 | 用途 |
|------|------|
| `backfill_from_uploads.py` / `backfill_wire_snapshot.py` | 历史数据回填 |
| `migrate_uploads_to_subfolders.py` / `sync_existing_uploads.py` | 上传文件目录迁移与同步 |
| `cleanup_na_placeholder.py` | 清理 N/A 占位数据 |
| `check_deploy.sh` | 部署前环境检查（Linux） |
| `pack_to_hk.py` / `pack_to_hk.bat` / `pack_to_hk.sh` | 部署包打包 |
| `build_deploy_pkg.py` | 构建部署包（自动排除日志、缓存等运行时产物） |

数据库结构变更优先使用 Flask-Migrate（`migrations/`）；执行任何迁移前请先在「数据备份管理」做全量备份。

## 10. 常见问题

| 现象 | 原因与处理 |
|------|-----------|
| 启动报 `ModuleNotFoundError` | 依赖装错了解释器。用运行服务的同一解释器重新执行 `pip install -r requirements.txt` |
| 拍照识别全字段「未识别」 | OCR 四件套未装入运行服务的解释器，见 5.4 节验证命令 |
| 端口 5004 被占用 | `start.bat` / `start.sh` 会自动清理旧进程；手动处理：`netstat -ano \| findstr :5004` 后 `taskkill /F /PID <pid>`，或设置 `APP_PORT` 换端口 |
| 管理员密码遗忘 | 未设置 `ADMIN_PASSWORD` 时查看 `instance/admin_initial_password.txt`；也可删除库中管理员后重启重建 |
| 修改模板 / 代码不生效 | 生产模式（Waitress）缓存 Jinja 模板，必须重启服务 |
| 冷启动较慢（首次请求 10 秒以上） | 正常现象，启动时后台线程自动预热缓存，之后为毫秒级响应 |
| Win7 部署 | 不支持（Python 3.10+ 不再兼容 Win7）；Win7 机器仅作为浏览器访问端 |
| SQLite 被 git 覆盖风险 | 设置 `SQLITE_PATH` 指向项目目录外的绝对路径 |

## 11. 相关文档

- 搭建详解：[docs/SETUP.md](docs/SETUP.md)
- 使用详解：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- 蓝图说明：[BLUEPRINT_DOCUMENTATION.md](BLUEPRINT_DOCUMENTATION.md)

---

**License**：见 [LICENSE](LICENSE)
