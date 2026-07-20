# DDP — Delayed Dispatch Platform

选镜像、定时启动的 GPU 作业平台。提交即得一个 SSH 调试 pod（与正式运行同镜像、同工作区），到点自动切换为 GPU Job 执行入口命令，产物收割到 S3。

生产地址：**http://172.16.50.3:8888**（k8s `ddp` 命名空间，见 `AGENTS.md` 运维备忘）

## 使用流程

1. **注册 / 登录** — 首个注册用户自动成为管理员
2. **提交作业** — 选镜像（`ddp-cuda-ssh` / `ddp-pytorch-ssh`，来自 Harbor tag）、入口命令、计划时间、时长、GPU 数量/显存、产物目录
3. **调试环境就绪** — 状态从 `初始化中` 变为 `等待中` 后，详情页可见 SSH 地址和随机密码。`/workspace` 在你名下所有作业间持久共享，装的依赖、写的代码都会带到 GPU 运行
4. **到点执行** — 调试 pod 停止，同工作区拉起 GPU Job 跑入口命令
5. **查看结果** — 日志（ANSI 彩色）和产物（收割自产物目录）在详情页查看下载

## 核心概念

### 工作区（workspace）

每个用户一个 PVC，跨作业持久、不跨用户。`/root/workspace` 软链到 `/workspace`。

产物目录（默认 `output`，可自定义绝对路径或相对 /workspace）在每次运行后**收割**——上传 S3 后从工作区删除。想长期保留的文件放在产物目录之外。

### 状态机

```
initializing → pending → running → done / failed / timeout
                   ↘ cancelled（pending/running 均可取消，pending 可编辑）
```

- `initializing`：调试 pod 拉镜像、起 sshd 中，ssh 可连后自动转 `pending`
- 超时 = K8s Job `activeDeadlineSeconds`；pod 残留由 `ttlSecondsAfterFinished: 300` 清理

### GPU

- vGPU 由 HAMi 切分（`nvidia.com/gpu` + `nvidia.com/gpumem`），配额按用户可配（管理员覆盖或默认）
- 调试 pod 看不到 GPU（`NVIDIA_VISIBLE_DEVICES=void`），`nvidia-smi` 显示 `No devices were found`
- 首页 GPU 面板展示 HAMi 实时分配；管理员可禁用单卡（仅展示层）

### 管理员

- 作业全视图：所有用户的作业，pending 可编辑/删除/拖拽排序（重写 scheduled_at）
- 提交不受运行时间窗限制（普通用户会被顺延到窗口）
- Admin 面板：用户管理（配额/提权/删除）、系统参数（时间窗/默认配额/GPU 开关）、日志、监控

## API 摘要

### Auth

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/auth/register` | 注册（form: username, password）→ session cookie（7 天） |
| POST | `/api/auth/login` | 登录 |
| POST | `/api/auth/logout` | 登出 |
| POST | `/api/auth/password` | 改密（old_password, new_password） |
| GET | `/api/auth/me` | 当前用户 + mode/gpu_quota/images/时间窗/默认配额 |

### Jobs（需登录）

| Method | Path | 说明 |
|--------|------|------|
| POST | `/api/jobs` | 提交（form: name, image, entry_command, scheduled_at, timeout_minutes, gpus, gpu_mem_mb, output_path）→ 含 ssh_port/ssh_password |
| GET | `/api/jobs` | 自己的作业列表 |
| GET/PATCH/DELETE | `/api/jobs/{id}` | 详情 / 编辑（仅 pending）/ 取消（pending,running）或删除（终态） |
| GET | `/api/jobs/{id}/logs` | 日志文本 |
| GET | `/api/jobs/{id}/outputs` | 产物列表（含 download_url） |
| GET | `/api/gpus` | HAMi 实时 GPU 分配 |

### Admin（需管理员）

| Method | Path | 说明 |
|--------|------|------|
| GET | `/api/admin/jobs` | 全部作业（含 username） |
| PATCH/DELETE | `/api/admin/jobs/{id}` | 编辑/取消（仅 pending） |
| POST | `/api/admin/jobs/reorder` | `{ids:[...]}` 重排 pending 队列 |
| GET/PATCH/DELETE | `/api/admin/users[/{id}]` | 用户管理 |
| GET/PUT | `/api/admin/params` | 系统参数 |
| GET | `/api/admin/logs` `/api/admin/monitoring` | 日志 / 监控 |

## 本地开发

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 默认 k8s 模式
DDP_EXECUTOR=mock uv run uvicorn app.main:app            # 仿真模式
cd frontend && bun install && bun run build              # 前端构建
uv run python -m pytest -q                               # 测试（需 MinIO 可达）
```

## 项目结构

```
ddp/
├── app/
│   ├── main.py          路由 + APScheduler 调度
│   ├── k8s_executor.py  两阶段 pod 生命周期（debug → GPU Job → collector）
│   ├── executor.py      Mock 执行器（测试用）
│   ├── db.py            SQLite：jobs/users/sessions/params/logs
│   ├── auth.py          pbkdf2 + 会话
│   ├── admin.py         用户/参数/日志/监控
│   ├── gpu.py           HAMi metrics 抓取
│   ├── images.py        Harbor 镜像 tag 列表
│   └── storage.py       S3 (boto3)
├── frontend/            Vite + TS 单页（中英双语、明暗双主题）
├── docker/
│   ├── server/          ddp-server 镜像（平台本体）
│   ├── images/          ddp-cuda-ssh / ddp-pytorch-ssh（业务镜像）
│   └── ddp-runner/      产物收集器
└── tests/               pytest（conftest 固定 mock 模式）
```
