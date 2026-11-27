# 大焱资料管理系统 — 代码结构说明

> 适用版本：2026-11 现行版本。本文档描述实际代码结构，便于二次开发与维护。

## 1. 蓝图划分

应用入口为 `app.py` → `app_factory.create_app()`，按模块注册下列蓝图：

| 蓝图 | 变量名 | 模块 | 前缀 | 职责 |
|------|--------|------|------|------|
| main | `main_bp` | `routes.py` | — | 核心业务：资料、线束、投入、资材入库、在库/出入库、MES 入口、盘点、标签打印、菜单管理、关于与在线更新 |
| cad | `cad_bp` | `routes_cad.py` | — | RW 图纸核对与 DXF 标记产出 |
| rbac | `rbac_bp` | `routes_rbac.py` | `/rbac` | 权限项、权限组、用户覆盖 |
| nas | `nas_bp` | `routes_nas.py` | `/nas` | 共享盘文件检索 |
| mes | `mes_bp` | `routes_mes.py` | `/mes` | MES 主流程（计划、生产入库、品质、单价、结算） |
| mes_mod | `mes_mod_bp` | `routes_mes_modules.py` | `/mes` | MES 子模块接口 |
| user_mgmt | `user_mgmt_bp` | `routes_user_mgmt.py` | `/api/user-mgmt` | 用户增删改、批量导入、权限分配 |

## 2. 关键目录

```
bom3/
├── app.py                  # 启动入口（端口、管理员账号、密钥初始化）
├── app_factory.py          # 应用工厂：蓝图注册、CSRF、日志、结构升级、缓存预热、备份调度
├── config.py               # 开发/生产/测试配置
├── models/                 # SQLAlchemy 模型（资料、线束、资材入库、库存、标签模版、盘点等）
├── utils/                  # 工具库
│   ├── sidebar_menu.py     # 侧边栏菜单定义、分组布局与迁移
│   ├── updater.py          # 在线更新（版本检查、git/dulwich 克隆、覆盖、代理/加速源）
│   ├── permission_catalog.py # 权限项目录（页面/按钮）
│   ├── operation_logger.py # 操作日志
│   └── time_utils.py       # UTC+8 时间格式化
├── scripts/
│   ├── restart_helper.py   # 更新后等待旧进程退出并重启
│   └── check_deploy.sh     # 部署前环境检查
├── templates/              # Jinja2 页面
├── static/                 # 静态资源（uploads 为上传文件目录）
├── instance/               # app.db、admin_initial_password.txt、update_config.json、update_state.json
├── logs/                   # dayan.log、server.log、update_restart.log
└── docs/                   # SETUP.md / USER_GUIDE.md
```

## 3. 权限体系

采用 RBAC，由五张表组成：`Permission`（权限项）、`PermissionGroup`（权限组）、`GroupPermission`（组-权限）、`UserGroup`（用户-组）、`UserPermissionOverride`（用户级 allow/deny）。最终权限 = 权限组并集 + 用户覆盖；管理员恒为全部权限。

- 权限项集中定义于 `utils/permission_catalog.py`，页面与按钮均按 `can_*` 编码登记。
- 路由内使用 `check_permission(user_id, 'can_xxx')`；模板使用 `session.get('is_admin')` 或路由显式传入的权限变量。
- 新增页面时需同时：注册菜单项（`utils/sidebar_menu.py`）、登记权限项、在用户管理页展示对应开关。

## 4. 侧边栏菜单

- `ENTRY_DEFS` 定义全部页面（当前 39 项，均按二级扁平项渲染），`DEFAULT_GROUPS` 定义默认分组。
- 布局保存在数据库/配置中（版本 `v: 3`），`normalize_layout()` 负责补齐缺失分组、清理空分组。
- 菜单管理页（`/menu_management`）通过 `/api/sidebar_menu` 读写布局，支持拖拽分组与条目。
- 前端 `static/js/sidebar.js` 负责分组折叠、展开状态记忆（`appSidebarOpenSections`）与当前页自动展开。

## 5. 在线更新模块

| 文件 | 职责 |
|------|------|
| `utils/updater.py` | 读取本地/远端版本、检查更新、执行更新、进度状态、变更历史、代理与加速源配置 |
| `scripts/restart_helper.py` | 独立进程等待旧进程退出，按原解释器与启动参数重启 |
| `templates/about.html` | 关于页：系统信息、进度、变更历史、代理/加速源设置 |
| `routes.py` | `/about` 与 `/api/about/*` 路由（info/check/update/status/history/proxy/accelerator） |

更新策略：
1. Git 仓库 + git：`fetch` + `--ff-only` 合并；
2. 无 `.git` 但有 git/dulwich：浅克隆后覆盖源码；
3. 最后回退压缩包下载（校验 gzip/zip 文件头）。

覆盖时固定保留 `instance/`、`logs/`、`backups/`、`.venv/`、`.git/`，并先备份到 `backups/auto_update_<时间戳>/`。

## 6. 已知问题与建议

1. `routes.py` 体量很大（约 1MB），建议按业务（资料/线束/资材/库存/盘点/关于）继续拆分为子蓝图。
2. `routes.py` 顶部 `main_bp = Blueprint('main', __name__)` 定义重复（第 185、201 行），结果等价但建议删除其一。
3. 新增数据库字段优先通过 `app_factory` 的结构升级逻辑或 Flask-Migrate 完成，变更前先做全量备份。

## 7. 维护约定

- 时间统一 UTC+8：入库/操作时间用 `format_utc8_time()`，业务日期用 `yyyy-MM-dd`。
- 大数据表（如 `usage_list`）查询必须命中既定索引（`idx_usage_list_kscpno`），避免全表扫描。
- 提交信息遵循 Conventional Commits：`<type>(<scope>): <subject>`（中文主题）。
- `.secret_key`、`instance/`、`logs/`、`backups/`、`.venv/` 不得提交到代码仓库。
