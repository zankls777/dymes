# 大焱资料管理系统 - 蓝图文档

## 1. 蓝图概览

### 1.1 主蓝图 (main_bp)

**名称**: `main`
**模块**: `routes.py`
**功能**: 系统的主要路由集合，包含所有核心功能的访问入口

## 2. 路由列表

### 2.1 认证相关路由

| 路由 | 方法 | 功能描述 | 权限要求 |
|------|------|----------|----------|
| `/login` | GET/POST | 用户登录页面 | 无 |
| `/logout` | GET | 用户登出 | 已登录 |

### 2.2 核心功能路由

| 路由 | 方法 | 功能描述 | 权限要求 |
|------|------|----------|----------|
| `/` | GET | 系统首页 | 已登录 |
| `/sub_list` | GET/POST | SubList管理页面 | 管理员或有 `can_sub_list` 权限 |
| `/usage_list` | GET/POST | UsageList管理页面 | 管理员或有 `can_usage_list` 权限 |
| `/file_management` | GET/POST | 文件管理页面 | 管理员或有 `can_file_management` 权限 |
| `/file_comparison` | GET | 数据差异对比页面 | 管理员或有 `can_file_comparison` 权限 |
| `/rw_comparison` | GET | RW核对页面 | 管理员或有 `can_file_comparison` 权限 |
| `/products` | GET/POST | 成品管理页面 | 管理员或有 `can_products` 权限 |
| `/material_download` | GET/POST | 资材量下载页面 | 管理员或有 `can_material_download` 权限 |
| `/material_calculator` | GET | 资材换算页面 | 已登录 |
| `/material_calculator/tape` | GET | 胶带换算页面 | 已登录 |
| `/material_calculator/sheet` | GET | 皮子换算页面 | 已登录 |
| `/vehicle_types` | GET/POST | 车种管理页面 | 管理员或有 `can_vehicle_types` 权限 |

### 2.3 管理员路由

| 路由 | 方法 | 功能描述 | 权限要求 |
|------|------|----------|----------|
| `/user_management` | GET/POST | 用户与权限管理页面 | 管理员 |
| `/permission_management` | GET/POST | 权限管理页面 | 管理员 |
| `/operation_logs` | GET | 操作记录页面 | 管理员 |

### 2.4 API路由

| 路由 | 方法 | 功能描述 | 权限要求 |
|------|------|----------|----------|
| `/api/files` | GET | 获取上传文件列表 | 已登录 |
| `/api/files/ids` | GET | 获取所有文件ID | 已登录 |
| `/api/file_headers` | POST | 获取文件表头信息 | 已登录 |
| `/api/file_compare` | POST | 执行文件对比 | 已登录 |
| `/api/rw_comparison/upload` | POST | 上传RW对比文件 | 已登录 |
| `/api/rw_comparison/manual_add` | POST | 手动添加RW对比数据 | 已登录 |
| `/api/rw_comparison/download` | GET | 下载RW对比结果 | 已登录 |
| `/api/vehicle_types/add` | POST | 添加单个车种 | 已登录 |
| `/api/vehicle_types/batch_add` | POST | 批量添加车种 | 已登录 |
| `/api/vehicle_types/delete` | POST | 删除车种 | 已登录 |
| `/api/vehicle_types/update` | POST | 更新车种 | 已登录 |
| `/api/vehicle_types/search` | GET | 搜索车种 | 已登录 |
| `/api/products/add` | POST | 添加单个成品 | 已登录 |
| `/api/products/batch_add` | POST | 批量添加成品 | 已登录 |
| `/api/products/delete` | POST | 删除成品 | 已登录 |
| `/api/products/update` | POST | 更新成品 | 已登录 |
| `/api/products/search` | GET | 搜索成品 | 已登录 |
| `/api/products/import` | POST | 导入成品(Excel) | 已登录 |
| `/api/users/<int:user_id>/permissions` | GET | 获取用户权限 | 已登录 |
| `/api/users/<int:user_id>/permissions` | POST | 更新用户权限 | 管理员 |
| `/api/tape_calculator/upload` | POST | 上传胶带换算文件 | 已登录 |
| `/api/tape_calculator/skip` | POST | 跳过胶带换算资料上传 | 已登录 |
| `/api/tape_calculator/get` | GET | 获取胶带换算结果 | 已登录 |
| `/api/tape_calculator/delete` | DELETE | 删除胶带换算结果 | 已登录 |
| `/api/sheet_calculator/upload` | POST | 上传皮子换算文件 | 已登录 |
| `/api/sheet_calculator/skip` | POST | 跳过皮子换算资料上传 | 已登录 |
| `/api/sheet_calculator/get` | GET | 获取皮子换算结果 | 已登录 |
| `/api/sheet_calculator/delete` | DELETE | 删除皮子换算结果 | 已登录 |

## 3. 权限系统

### 3.1 权限模型

权限信息存储在 `PermissionInfo` 模型中，与用户为一对一关系。主要权限字段包括：

| 权限字段 | 描述 | 默认值 |
|----------|------|--------|
| `can_upload` | 上传权限 | False |
| `can_delete` | 删除权限 | False |
| `can_batch_delete` | 批量删除权限 | False |
| `can_page` | 页面访问权限 | False |
| `can_vehicle_types` | 车种管理页面访问权限 | False |
| `can_products` | 成品管理页面访问权限 | False |
| `can_material_download` | 资材量下载页面访问权限 | False |
| `can_file_management` | 文件管理页面访问权限 | False |
| `can_file_comparison` | 数据对比页面访问权限 | False |
| `can_sub_list` | SubList页面访问权限 | False |
| `can_usage_list` | UsageList页面访问权限 | False |
| `can_system_management` | 系统管理权限 | False |

### 3.2 权限检查

权限检查主要通过以下方式实现：

1. **装饰器检查**：使用 `@login_required` 装饰器确保用户已登录
2. **代码检查**：在路由函数中检查用户是否为管理员或具有特定权限
3. **模板检查**：在模板中通过 `session.get('is_admin')` 或 `permissions.get('can_xxx')` 检查权限

## 4. 蓝图结构优化建议

### 4.1 现有问题

1. **单一蓝图过大**：所有路由都集中在一个 `main_bp` 蓝图中，导致 `routes.py` 文件过大，难以维护
2. **路由组织混乱**：路由按功能分组不明显，查找和管理不便
3. **蓝图重复定义**：`main_bp` 在 `routes.py` 中被定义了两次

### 4.2 优化建议

1. **拆分为多个蓝图**：
   - `auth_bp`：认证相关路由
   - `core_bp`：核心业务功能路由
   - `admin_bp`：管理员功能路由
   - `api_bp`：API接口路由
   - `calculator_bp`：资材换算相关路由

2. **目录结构调整**：
   ```
   routes/
   ├── __init__.py
   ├── auth.py          # 认证路由
   ├── core.py          # 核心功能路由
   ├── admin.py         # 管理员路由
   ├── api.py           # API路由
   └── calculator.py    # 资材换算路由
   ```

3. **路由注册优化**：
   - 在 `routes/__init__.py` 中集中注册所有蓝图
   - 使用蓝图前缀区分不同功能模块

## 5. 路由处理流程

### 5.1 典型请求流程

1. **请求到达**：Flask接收HTTP请求
2. **蓝图匹配**：根据URL匹配到对应的蓝图和路由
3. **权限检查**：执行登录验证和权限检查
4. **业务处理**：执行路由函数中的业务逻辑
5. **响应返回**：返回HTML页面或JSON数据

### 5.2 错误处理

系统使用装饰器 `handle_db_errors` 统一处理数据库操作错误，确保：
- 错误信息被记录到日志
- 数据库事务被正确回滚
- 向用户返回友好的错误提示

## 6. 总结

当前系统使用单一蓝图管理所有路由，虽然实现简单，但在系统规模扩大时会带来维护困难。通过合理拆分蓝图，可以提高代码的可维护性和可读性，同时为未来的功能扩展做好准备。

蓝图文档将随着系统的发展不断更新，确保与实际代码保持一致。