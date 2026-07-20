# DDP 运维备忘

任务调度平台：用户选镜像/GPU → 立即得 SSH 调试 pod → 到点切 GPU Job 跑入口命令 → 产物收割到 S3。

## 部署（生产）

平台本体跑在集群里：**http://172.16.50.3:8888**

- 清单：外层仓库 `config/ddp.yaml`（namespace、`ddp-s3-creds` Secret、SA+Role+RoleBinding、`ddp-server-data` PVC、Deployment+Service）。改配置改这个文件再 `kubectl apply -f config/ddp.yaml`。
- 固定在 `ns-host0`，hostPort 8888，Recreate（数据 PVC 是 RWO）。
- **改代码后的发布流程**：
  ```bash
  # 前端有改动先构建
  cd frontend && bun run build && cd ..
  docker build --network=host \
    --build-arg HTTP_PROXY=http://127.0.0.1:7891 \
    --build-arg HTTPS_PROXY=http://127.0.0.1:7891 \
    -f docker/server/Dockerfile -t 172.16.50.3:5000/neospark/ddp-server:latest .
  docker push 172.16.50.3:5000/neospark/ddp-server:latest
  kubectl -n ddp rollout restart deployment ddp-server
  ```
- SQLite 在 PVC（`/app/data/ddp.db`），重建 pod 不丢数据；**删 PVC 才丢**。

## 本地开发

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # 默认即 k8s 模式，需 ~/.kube/config
DDP_EXECUTOR=mock uv run uvicorn app.main:app            # mock：不碰集群，状态机仿真
uv run python -m pytest -q                               # 需要 MinIO 可达（172.16.50.100:9000）
```

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `DDP_EXECUTOR` | `k8s` | `mock` 为仿真（测试用 conftest 固定 mock） |
| `DDP_S3_*` | MinIO `172.16.50.100:9000` | ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET，pod 内由 Secret 注入 |
| `DDP_HAMI_METRICS` | ClusterIP | GPU 数据源，集群内用 `hami-scheduler.kube-system.svc:31993/metrics` |
| `DDP_HARBOR_*` | `172.16.50.3:5000/neospark` | API/REGISTRY/USER/PASSWORD，镜像下拉列表来源 |
| `DDP_IMAGE_REPOS` | `ddp-cuda-ssh,ddp-pytorch-ssh` | 可选镜像仓库白名单，新 tag 自动出现 |
| `DDP_POD_PROXY` | `http://172.16.50.3:7891` | 注入业务 pod 的 HTTP(S)_PROXY |

## 架构要点（改动前必读）

- **两阶段 pod**：提交即建 debug pod（无 GPU、sshd、NodePort）+ 每用户 PVC；到点删 debug pod → 同 PVC 起 batch Job（用户声明的 `nvidia.com/gpu`/`gpumem`）；终态抓 pod 日志传 S3，再跑 collector Job 收割产物。
- **workspace = 每用户一个 PVC**（`ddp-user-<uid>`），跨作业持久。RWO 首绑节点后该用户所有 pod 粘性调度同节点（读 PVC 的 `selected-node` 注解）。
- **产物收割是"搬走"语义**：collector 上传 `output_path`（默认 `output`，可自定义，必须解析在 /workspace 内）到 S3 后**删除源目录**，防止共享 workspace 下产物串作业。manifest.json 声明的文件只复制不删。
- **状态机**：`initializing`（debug pod ssh 可连前）→ `pending` → `running` → `done/failed/timeout`；另有 `cancelled`。超时=Job `activeDeadlineSeconds`，残留=`ttlSecondsAfterFinished:300`。
- **镜像**：可选列表来自 Harbor 白名单仓库的 tag。`ddp-cuda-ssh`/`ddp-pytorch-ssh` = 基础镜像 + openssh + entry.sh + nvidia-smi shim；`ddp-runner` = slim 收集器。都在 `docker/` 下，改动后要重新 build/push。

## 踩过的坑（别再踩）

1. **cuda 基础镜像没有 python/python3**，入口命令用 `bash` 或先在 debug pod 里装环境。
2. **NGC 镜像 BASH_ENV 递归**：`/bin/sh` 是 sh-wrap→bash，bash 脚本会触发 `BASH_ENV → shinit_v2 → nvidia-smi` 无限递归 fork。shim 必须 `#!/bin/dash`。
3. **GPU 泄露**：NGC 镜像自带 `NVIDIA_VISIBLE_DEVICES=all`，toolkit 无视 K8s 资源限制挂全部卡。debug pod 必须显式 `NVIDIA_VISIBLE_DEVICES=void`。
4. **ssh 会话不继承容器 env**——shim 按 `/dev/nvidia*` 设备存在性判断，别读 env。
5. **DATA_DIR 相对代码布局**：容器内是 `/app/app/`，所以数据卷挂 `/app/data` 不是 `/data`。
6. **kubernetes client 位置参数**：`V1VolumeMount(name=..., mount_path=...)`、`V1Volume(name=...)` 必须关键字传参，第一个位置参数不是 name。
7. **`pkill -f 'uvicorn app.main'` 会匹配到执行它的 shell 自身**（命令行里也有这串），用 `fuser -k 8000/tcp`。
8. **集群离线**：业务 pod 出网靠注入的代理 env；docker build 要 `--network=host` + proxy build-args。
9. **HAMi 调度层面无法按单卡排除**：admin 的 GPU 禁用只影响展示，不做调度隔离。
10. **卡满时 GPU Job 会 Pending 直到 deadline 判 timeout**——这是预期行为不是 bug。

## 已知遗留

- README 和 `.opencode/skills/ddp-backend` 还是 zip 上传时代的文档，待更新。
- GPU 配额只按单作业上限校验，不做并发占用扣减。
- DeadlineExceeded 时未上传已产生的部分产物（见 k8s_executor 注释）。
- admin 重排 pending = 把该集合 scheduled_at 从最早值起按 1 分钟间隔重写。
