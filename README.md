# DDP — Delayed Dispatch Platform

提交 Python 项目，定时启动、到点回收，产物自动存入 S3。

当前为 **Mock 模式**：执行器用本地子进程模拟 K8s Job，S3 用本地文件系统模拟。接口设计已对齐生产环境，切换时只需替换两个模块。

## 快速开始

### 环境要求

- Python 3.13+
- Windows / Linux / macOS

### 安装与启动

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

或直接双击 `run.bat`（Windows）。

打开浏览器访问 `http://localhost:8000`。

### 使用流程

1. **注册 / 登录** — 首页注册账号，自动登录并建立会话（7 天有效）
2. **提交作业** — 上传 `.zip` 项目包，填写作业名称、入口命令、计划启动时间、最大运行时长
3. **等待执行** — 到点后调度器自动拉起作业，前端每 5s 刷新状态
4. **查看结果** — 点击作业卡片查看日志和 S3 产物列表，可下载

## 项目结构

```
ddp/
├── app/
│   ├── main.py          FastAPI 路由 + APScheduler 调度
│   ├── db.py            SQLite 作业/用户/会话持久化
│   ├── auth.py          pbkdf2 密码哈希 + 会话管理 + 鉴权依赖
│   ├── executor.py      Mock K8s（子进程 + 超时 kill + 零残留清理）
│   └── storage.py       Mock S3（本地文件系统，接口镜像 boto3）
├── frontend/
│   └── index.html       单页应用（登录/注册 + 作业提交 + 列表/详情）
├── sample_project/      测试用 Python 项目
├── data/                运行时数据（DB、上传、日志、mock-s3）
├── requirements.txt
└── run.bat
```

## 架构

```
┌─────────────┐     ┌──────────────────────────────────┐
│  Web 前端   │────▶│         API 后端 (FastAPI)        │
│ (登录/提交) │ REST│  ┌────────────────────────────┐  │
└─────────────┘     │  │ Scheduler (APScheduler)    │  │
                    │  │ 30s tick, DateTrigger      │  │
                    │  └────────────┬───────────────┘  │
                    │               │ 到点             │
                    │               ▼                  │
                    │  ┌────────────────────────────┐  │  ┌──────────────────────┐
                    │  │ Executor → 子进程          │  │─▶│  执行环境            │
                    │  └────────────┬───────────────┘  │  │  init: 解压 + pip   │
                    │               │ 完成回调          │  │  main: 跑 python    │
                    │  ┌────────────▼───────────────┐  │  │  output/ → 产物     │
                    │  │ Storage → S3 上传          │  │  └──────────────────────┘
                    │  └────────────────────────────┘  │              │
                    └──────────────────────────────────┘              ▼
                                                              ┌──────────────────┐
                                                              │    AWS S3        │
                                                              └──────────────────┘
```

## 核心设计

### 调度模型

- **一次性定时 (one-shot)**：用户指定计划启动时间，APScheduler `DateTrigger` 到点触发
- **分钟级精度**：调度器在计划时间后 ~30s 内拉起作业
- **重启恢复**：服务重启后自动重新调度所有 `pending` 状态的作业

### 回收机制

- **自然结束 + 兜底上限**：作业正常退出后立即回收；超时后强制 kill 进程
- **零残留**：工作目录执行完毕后 `shutil.rmtree` 清除，不遗留临时文件
- **超时处理**：`timeout_minutes` 到期后 kill 进程，已生成的产物仍会被收集

### 产物收集

两种方式并存：

1. **约定目录**：项目中 `./output/` 下的所有文件自动上传
2. **Manifest 声明**：项目根目录的 `manifest.json` 中 `outputs` 字段列出的路径

```json
{
  "outputs": ["report.pdf", "data/final.csv"]
}
```

### 用户系统

- **注册 + 登录**：用户名/密码，pbkdf2_hmac(sha256, 100k iterations) 哈希存储
- **会话管理**：服务端 SQLite 存储，HttpOnly Cookie 下发，7 天有效期
- **作业隔离**：每个用户只能看到和操作自己的作业

## API

### Auth

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/auth/register` | 注册（username, password）→ 下发 session cookie |
| POST | `/api/auth/login` | 登录 → 下发 session cookie |
| POST | `/api/auth/logout` | 登出 → 删除 session |
| GET | `/api/auth/me` | 获取当前用户信息 |

### Jobs（需登录）

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/jobs` | 提交作业（multipart: file, name, entry_command, scheduled_at, timeout_minutes） |
| GET | `/api/jobs` | 列出当前用户的作业 |
| GET | `/api/jobs/{id}` | 获取作业详情 |
| GET | `/api/jobs/{id}/logs` | 获取作业日志 |
| GET | `/api/jobs/{id}/outputs` | 获取作业产物列表 |
| DELETE | `/api/jobs/{id}` | 取消（pending）或删除（已完成）作业 |

## 提交的 Python 项目格式

```
my-project.zip
├── main.py              入口脚本（默认执行 python main.py）
├── requirements.txt     可选，自动 pip install
├── output/              可选，产物输出目录（自动上传）
└── manifest.json        可选，声明额外产物路径
```

## Mock → 生产切换路径

| Mock 实现 | 生产替换 |
|-----------|----------|
| `executor.py` MockExecutor（子进程） | K8s Job：`activeDeadlineSeconds` + `ttlSecondsAfterFinished` + `backoffLimit: 0` |
| `storage.py` Storage（本地文件系统） | `boto3` S3 Client |
| `db.py` SQLite | PostgreSQL / MySQL（接口不变） |
| 裸进程隔离 | K8s Pod + HAMI GPU 切分 |

## 依赖

- `fastapi` — Web 框架
- `uvicorn` — ASGI 服务器
- `apscheduler` — 定时调度
- `python-multipart` — 表单文件上传

无前端构建工具，无数据库驱动，无云 SDK — Mock 模式零外部服务依赖。
