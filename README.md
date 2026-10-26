# 大焱资料管理系统使用指南

## 1. 系统概述

大焱资料管理系统是一个用于管理、对比和分析SubList和UsageList文件的Web应用程序，支持单文件和多文件对比，提供详细的差异分析和结果导出功能。

## 2. 环境要求

- Python 3.8+
- 支持的浏览器：Chrome, Firefox, Edge, Safari
- 支持的文件格式：.xlsx, .xls

## 3. 安装指南

### 3.1 创建虚拟环境

推荐使用虚拟环境来运行应用，避免依赖冲突。

#### Windows系统
```cmd
python -m venv SUB
```

#### Linux/macOS系统
```bash
python3 -m venv SUB
```

### 3.2 激活虚拟环境

#### Windows系统
```cmd
SUB\Scripts\activate
```

#### Linux/macOS系统
```bash
source SUB/bin/activate
```

### 3.3 安装依赖

在激活虚拟环境后，安装项目依赖：

```bash
pip install -r requirements.txt
```

## 4. 运行应用

### 4.1 自动启动（推荐）

我们的`start_app.py`脚本现在支持自动创建虚拟环境、安装依赖和启动应用：

#### Windows系统
```cmd
python start_app.py
```

#### Linux/macOS系统
```bash
python3 start_app.py
```

### 4.2 一键启动脚本

我们还提供了专门的一键启动脚本，支持自动检测和激活虚拟环境：

#### Windows系统
```cmd
start_dock.bat
```

#### Linux/macOS系统
```bash
chmod +x start_dock.sh
./start_dock.sh
```

### 4.3 手动启动

#### 激活虚拟环境

首先激活虚拟环境，然后运行应用：

```bash
# Windows
SUB\Scripts\activate
python start_app.py --run-only

# Linux/macOS
source SUB/bin/activate
python3 start_app.py --run-only
```

## 5. 访问应用

应用启动后，在浏览器中访问：

```
http://localhost:5004
```

## 6. 功能说明

### 6.1 文件管理
- 支持本地文件上传和从文件管理选择
- 支持SubList和UsageList文件对比
- 支持单文件和多文件对比

### 6.2 数据对比
- 关键列对比和合并单元列对比
- 实时差异可视化展示
- 支持差异结果导出

### 6.3 权限管理
- 用户管理和权限设置
- 支持页面访问和按钮操作权限
- 实时权限预览

## 7. 脚本说明

### 7.1 启动脚本功能

我们提供的一键启动脚本具有以下功能：

1. **虚拟环境自动检测**：检测是否存在虚拟环境
2. **虚拟环境自动激活**：如果存在虚拟环境，自动激活
3. **系统Python fallback**：如果没有虚拟环境，使用系统Python
4. **数据库配置显示**：显示当前数据库配置
5. **友好的控制台输出**：使用emojis和清晰的格式

### 7.2 脚本配置

脚本支持通过环境变量配置：

- `DATABASE_TYPE`：数据库类型（sqlite或mysql）
- `MYSQL_HOST`：MySQL主机地址
- `MYSQL_PORT`：MySQL端口
- `MYSQL_USER`：MySQL用户名
- `MYSQL_PASSWORD`：MySQL密码
- `MYSQL_DATABASE`：MySQL数据库名

### 7.3 自定义虚拟环境目录

如果你的虚拟环境目录不是`SUB`，可以修改脚本中的`VENV_DIR`变量：

#### Windows系统（start_dock.bat）
```bat
set "VENV_DIR=your_venv_dir"
```

#### Linux/macOS系统（start_dock.sh）
```bash
VENV_DIR="your_venv_dir"
```

## 8. 常见问题

### 8.1 虚拟环境相关

**问题**：脚本提示"未检测到虚拟环境"

**解决方案**：
1. 检查虚拟环境目录是否为`SUB`
2. 如果不是，修改脚本中的`VENV_DIR`变量
3. 或者重新创建虚拟环境：`python -m venv SUB`

**问题**：激活虚拟环境失败

**解决方案**：
1. 检查虚拟环境目录是否存在
2. 检查虚拟环境是否完整
3. 尝试重新创建虚拟环境

### 8.2 依赖相关

**问题**：安装依赖失败

**解决方案**：
1. 确保pip是最新版本：`pip install --upgrade pip`
2. 尝试使用国内镜像源：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`

### 8.3 端口相关

**问题**：端口被占用

**解决方案**：
1. 修改`start_app.py`中的端口号
2. 或者关闭占用该端口的进程



## 10. 技术支持

如有问题或建议，请联系系统管理员。

---

**文档版本**：1.0
**更新时间**：2026-01-18
**适用系统**：大焱资料管理系统