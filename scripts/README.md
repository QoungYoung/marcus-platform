# Marcus Platform - 启动脚本说明

## 快速开始

双击运行 `marcus.bat` 进入主菜单，选择对应选项即可。

## 脚本列表

| 脚本 | 功能 |
|------|------|
| `marcus.bat` | 主菜单 (推荐) |
| `start_all.bat` | 启动全部服务 |
| `start_backend.bat` | 仅启动后端 |
| `start_frontend.bat` | 仅启动前端 |
| `stop.bat` | 停止所有服务 |
| `check_system.bat` | 检查系统需求 |
| `install_deps.bat` | 安装依赖 |

## 使用方法

### 1. 首次使用

```
1. 双击 marcus.bat
2. 选择 [6] Install Dependencies 安装依赖
3. 选择 [1] Start All Services 启动服务
```

### 2. 日常启动

```
1. 双击 marcus.bat
2. 选择 [1] Start All Services
```

### 3. 停止服务

```
1. 双击 marcus.bat
2. 选择 [4] Stop All Services
```

## 服务地址

| 服务 | 地址 |
|------|------|
| 前端仪表盘 | http://localhost:3000 |
| 后端 API | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |

## 系统需求

- Python 3.10+
- Node.js 18+
- Windows 10/11 或 Linux/WSL
