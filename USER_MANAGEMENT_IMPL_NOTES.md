# 用户与权限管理：问题修复与测试记录

## 一、问题描述
1. 删除用户时报「服务器内部错误」（HTTP 500）。
2. 用户与权限管理页面缺少基于角色的页面访问与操作权限控制。

## 二、根因分析
- 数据库启用了外键约束（`PRAGMA foreign_keys = ON`，见 `app_factory.py`）。
- 删除用户时原代码仅删除权限记录，未解除其他表对 `user_info.id` 的引用：
  - `operation_logs.user_id`：**非空外键**，存在操作日志时直接导致删除失败（500）。
  - `user_info`（`created_by`/`updated_by` 自引用）、`vehicle_type`、`finished_product`、
    `file_info`（`created_by`/`updated_by`/`frozen_by`）、`wire_scan_record`（`created_by`）、
    `lot_login_info`（`created_by`）、`data_upload_batch`（`uploaded_by`）均引用该用户。
- 已通过数据库探测脚本确认上述表与列在生产库 `instance/app.db` 中真实存在。

## 三、实现内容
### 1. 删除用户修复（`routes.py` `user_management` 的 `delete_user` 分支）
删除用户前在同一事务中解除所有外键引用，再删除，最后提交；失败时回滚并给出友好提示：
- `DELETE FROM operation_logs WHERE user_id = :uid`（非空外键，无法置空）
- 将各业务表引用该用户的 `created_by`/`updated_by`/`frozen_by`/`uploaded_by` 置为 `NULL`
- 删除 `permission_info` 权限记录
- 删除 `user_info` 记录

### 2. 页面访问权限控制（`routes.py` 路由入口）
- 原仅 `is_admin` 才可访问，现改为：未登录一律重定向登录页；
  登录用户需为管理员 **或** 拥有 `can_system_management` 系统管理权限，否则重定向登录页。
- 导航入口（`_navbar.html`、`_sidebar.html`）同步按 `can_system_management` 显示/隐藏，
  未授权用户不出现入口。

### 3. 操作级权限校验
为以下操作增加对应权限校验（管理员自动通过）：
| 操作 | 所需权限 |
|------|---------|
| 添加用户 | `can_user_mgmt_add_user` |
| 修改密码 | `can_user_mgmt_change_password` |
| 重置密码 | `can_user_mgmt_reset_password` |
| 权限管理 | `can_user_mgmt_manage_permissions` |
| 删除权限 | `can_user_mgmt_manage_permissions` |
| 删除用户 | `can_user_mgmt_delete_user` |

### 4. 前端按钮级权限（`user_management.html`）
- 按当前登录用户权限动态隐藏「添加用户」「修改密码」「重置密码」「权限管理」「删除」按钮。
- 内嵌权限分配模态框新增「系统管理」权限分组，可授予
  `can_system_management` 及各类 `can_user_mgmt_*` 操作权限，补齐权限分配闭环。

## 四、测试与验证结果
在隔离的临时 SQLite 库上执行集成测试（真实启动应用、使用 test client、真实外键约束）：
| # | 用例 | 结果 |
|---|------|------|
| 01 | 无权限用户访问 `/user_management` 被 302 重定向 | PASS |
| 02 | 拥有 `can_system_management` 用户可访问（200） | PASS |
| 03 | 有系统管理权限但无 `can_user_mgmt_delete_user` 时删除被拒 | PASS |
| 04 | 授权拒绝后目标用户未被删除 | PASS |
| 05 | 管理员删除用户成功（无 500，含成功提示） | PASS |
| 06 | 删除后关联数据已清理（用户/权限/操作日志均失效） | PASS |

结果：**6/6 全部通过**。
- 语法校验：`python -m py_compile routes.py` 通过。
- VS Code 诊断：`routes.py` 无错误。

## 五、相关文件
- `routes.py`：删除修复 + 页面访问权限 + 操作级权限校验
- `templates/user_management.html`：按钮权限显隐 + 系统管理权限分组
- `templates/_navbar.html`、`templates/_sidebar.html`：导航入口权限
