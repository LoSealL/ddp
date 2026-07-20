# 周期任务（每日 / 每周）— 设计文档

- **日期**：2026-07-20
- **范围**：ddp-server 后端 + ddp 前端
- **目标**：在提交作业的"计划启动时间"下方新增周期选项，让一条作业按"每日"或"每周（指定周几）"循环触发；周期任务的日志按 append 累积（上限 10MB），产物在 S3 上重名覆盖。

## 1. 背景

当前每个作业走 `DateTrigger` 一次性调度：

```
提交 → initializing → pending → running → done/failed/timeout
```

到点触发后进入终态即结束。用户要做"每天跑一次"必须每天重新提交。

## 2. 用户决策（已确认）

| 决策点 | 选择 |
|---|---|
| 周期任务数据模型 | **单记录循环**：一条 job 反复 pending→running→终态→重排 pending |
| debug pod 生命周期 | **每轮重建**：每轮终态后重新 `prepare()`，用户可在两轮间 ssh 改代码 |
| 上轮未完到下轮触发 | **跳过本轮**：触发时若 status≠pending，直接返回；下一轮按既定节奏 rearm |
| 日志累积策略 | **有上限 append**（默认 10MB），超限只留尾部 |

## 3. 数据模型

### 3.1 新增列（最小改动）

`jobs` 表追加两列，沿用现有 `init_db()` 的 `ALTER TABLE … ADD COLUMN` + `try/except sqlite3.OperationalError` 升级模式：

| 列 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `repeat_type` | TEXT NOT NULL | `'none'` | `'none'` / `'daily'` / `'weekly'` |
| `repeat_weekdays` | TEXT | NULL | ISO 周一=1 … 周日=7，逗号分隔（如 `'1,3,5'`）；仅 `weekly` 用 |

不新建表、不动外键、不动现有索引。老库平滑升级。

### 3.2 字段语义

- `repeat_type='none'`：一次性作业，行为与现状完全一致
- `repeat_type='daily'`：每天到 `scheduled_at` 的时分秒触发一次
- `repeat_type='weekly'`：每周在 `repeat_weekdays` 列出的日子到 `scheduled_at` 时分秒触发

## 4. 前端改动

### 4.1 HTML（index.html）

在 `scheduled_at` 字段块下方插入：

```html
<div class="field">
  <label data-i18n="repeat">Repeat</label>
  <div class="repeat-options">
    <label><input type="radio" name="repeat_type" value="none" checked /> <span data-i18n="repeatNone">One-off</span></label>
    <label><input type="radio" name="repeat_type" value="daily" /> <span data-i18n="repeatDaily">Daily</span></label>
    <label><input type="radio" name="repeat_type" value="weekly" /> <span data-i18n="repeatWeekly">Weekly</span></label>
  </div>
</div>
<div class="field" id="repeat-weekdays-field" style="display:none">
  <label data-i18n="repeatWeekdays">Weekdays</label>
  <div class="weekday-checks">
    <!-- value=1..7 (ISO Monday=1) -->
    <label><input type="checkbox" name="repeat_weekdays" value="1" /> <span data-i18n="mon">Mon</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="2" /> <span data-i18n="tue">Tue</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="3" /> <span data-i18n="wed">Wed</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="4" /> <span data-i18n="thu">Thu</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="5" /> <span data-i18n="fri">Fri</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="6" /> <span data-i18n="sat">Sat</span></label>
    <label><input type="checkbox" name="repeat_weekdays" value="7" /> <span data-i18n="sun">Sun</span></label>
  </div>
</div>
```

### 4.2 main.ts 行为

- 监听 3 个 radio 的 `change` 事件：选 `weekly` 时展开 `#repeat-weekdays-field`，否则收起
- 提交前校验：`repeat_type=weekly` 时必须勾选至少 1 个 weekday，否则 `alert(t('weekdaysRequired'))` 并阻止提交
- FormData 自然把多个同名 `repeat_weekdays` checkbox 传给后端，后端拼成 `'1,3,5'`
- 编辑表单（`showEditForm`）同样插入这两个字段，按 `job.repeat_type` / `job.repeat_weekdays` 回填
- Job 详情卡片加一行展示周期（一次性 / 每天 / 每周 周一三五）

### 4.3 i18n 文案

新增键，中英各一份：

| key | en | zh |
|---|---|---|
| `repeat` | Repeat | 周期 |
| `repeatNone` | One-off | 一次性 |
| `repeatDaily` | Daily | 每天 |
| `repeatWeekly` | Weekly | 每周 |
| `repeatWeekdays` | Weekdays | 星期 |
| `weekdaysRequired` | Select at least one weekday | 至少选择一个星期 |
| `mon`…`sun` | Mon…Sun | 周一…周日 |
| `repeatSummary` | Repeat | 周期 |
| `repeatSummaryNone` | One-off | 一次性 |
| `repeatSummaryDaily` | Daily | 每天 |
| `repeatSummaryWeekly` | Weekly ({days}) | 每周 ({days}) |

（中英文共用一套 `周一 周二 …` 文本即可，不必走 i18n 翻译星期本身）

## 5. 后端改动

### 5.1 提交路由 `POST /api/jobs`

- 新增 Form 字段：
  ```python
  repeat_type: str = Form("none"),
  repeat_weekdays: list[str] = Form([]),  # FastAPI 自动收集同名 checkbox
  ```
- 校验：
  - `repeat_type` 必须是 `none/daily/weekly`，否则 400
  - `weekly` 必须有 ≥1 个 weekday，且都在 `1..7`；否则 400
  - 非 `weekly` 时强制清空 `repeat_weekdays`
- 把 `repeat_type` 与拼好的字符串（`",".join(sorted(set(...)))`）透传 `db.create_job`

### 5.2 PATCH 路由（用户 + admin）

`_apply_job_edits` 同样接收这两个字段，校验同上；只有 pending 可改（沿用现状）。

### 5.3 `db.create_job` / `db.update_job`

- `create_job` 签名加 `repeat_type="none", repeat_weekdays=None`
- INSERT 时带上这两列
- `update_job` 已是 `**kwargs` 通用更新，无需改

### 5.4 `db.list_jobs` / `get_job`

`SELECT *`，自动带新列，无需改。

## 6. 调度循环（核心）

### 6.1 触发器选择

**保留 `DateTrigger`**，不换 CronTrigger。原因：

- 单次触发模型可控，rearm 时一并重建 debug pod（CronTrigger 的多次触发里没有自然挂载点做 pod 生命周期）
- 复用现有 `execute → watch → 终态` 路径，改动小
- Skip-if-running 守卫天然由 status 判断实现

### 6.2 rearm 流程

在 `k8s_executor._watch()` 末尾，设置终态字段之后，追加：

```python
job = db.get_job(job_id)  # 重读，拿到 repeat_type
if job and job.get("repeat_type") in ("daily", "weekly"):
    next_dt = _compute_next_run(job)            # 见 6.3
    # 非 admin 钳到时间窗
    owner = db.get_user_by_id(job["user_id"]) or {}
    if not owner.get("is_admin"):
        next_dt = timecheck.check_scheduled_time(next_dt)
    next_dt = _not_in_past(next_dt)

    db.update_job(job_id,
                  status="pending",
                  scheduled_at=next_dt.isoformat(),
                  started_at=None, finished_at=None, error=None,
                  output_count=0)
    # 重建 debug pod
    ssh_info = await self.prepare({
        "id": job_id, "user_id": job["user_id"],
        "image": job["image"], "storage_gb": owner_storage_gb,
    })
    db.update_job(job_id,
                  ssh_port=ssh_info["ssh_port"],
                  ssh_password=ssh_info["ssh_password"])
    # 重排触发器
    scheduler.add_job(executor.execute, DateTrigger(run_date=next_dt),
                      args=[job_id], id=job_id, replace_existing=True)
    db.log_event("INFO", "job",
                 f"Job rearmed: {job_id} -> {next_dt.isoformat()}",
                 user_id=job["user_id"])
```

**注意**：`_watch()` 在 `MockExecutor` 里没有；mock 模式不实现 rearm（测试不覆盖周期循环的端到端，由单测覆盖 `_compute_next_run` 即可）。

### 6.3 `_compute_next_run`

放在 `app/timecheck.py`：

```python
def _compute_next_run(job: dict) -> datetime:
    """基于 job['scheduled_at']（最近一次触发时间）算下一个 daily/weekly 时间点。

    从 scheduled_at 的**第二天**起找，避免本轮立刻再次触发。
    """
    cur = datetime.fromisoformat(job["scheduled_at"]).replace(tzinfo=db.get_tz())
    cur = cur.replace(second=0, microsecond=0)
    if job["repeat_type"] == "daily":
        return cur + timedelta(days=1)
    # weekly
    days = sorted({int(d) for d in (job.get("repeat_weekdays") or "1").split(",")})
    for offset in range(1, 8):
        cand = cur + timedelta(days=offset)
        if cand.isoweekday() in days:
            return cand
    raise ValueError("No matching weekday in 7 days (should be impossible)")
```

### 6.4 Skip-if-running 守卫

`K8sExecutor.execute()` 入口加：

```python
async def execute(self, job_id: str):
    job = db.get_job(job_id)
    if not job:
        return
    if job["status"] != "pending":
        # 上一轮还在跑（running/initializing）或已被取消 — 跳过
        db.log_event("DEBUG", "system",
                     f"Trigger skipped (status={job['status']}): {job_id}")
        return
    # 原有逻辑：设 running，删 debug pod，起 GPU Job，watch
```

正常情况下，APScheduler 到点触发时 status 必是 pending（上轮终态时已经 rearm 并改回 pending）。守卫只兜底：
- 上轮 timeout 漂到下一轮窗口内
- 用户手工把 status 改成 running 的极端情形

**与 cancelled 的关系**：cancelled 不进 `_watch`（用户从 pending DELETE）也不进 `execute`（已被守卫拦），rearm 分支只跑在 `_watch` 末尾，所以 cancelled 任务根本不会触发 rearm，循环天然停止。

### 6.5 取消

现有 DELETE 路径完全不变：

```python
if job["status"] in ("pending", "initializing"):
    scheduler.remove_job(job_id)   # 摘掉下一发
    ...
    db.update_job(job_id, status="cancelled")  # rearm 检查 status，cancelled 不再 rearm
```

cancelled 是终态但**不在 rearm 分支里**（rearm 只在 `_watch` 终态设完之后立刻判定，cancelled 不会进入 `_watch`），循环自然停止。

### 6.6 启动恢复

`lifespan` 里恢复 pending 任务的循环已经遍历所有 `status in (pending, initializing)` 的 job 并 `scheduler.add_job(DateTrigger(...))` — 周期任务在 rearm 后也是 pending 状态，**天然被覆盖**，无需额外处理。

## 7. 日志累积（有上限 append）

### 7.1 `Storage.append_bytes`

`app/storage.py` 新增方法：

```python
def append_bytes(self, key: str, data: bytes, cap: int = 10 * 1024 * 1024) -> str:
    """S3 不支持原生 append：读旧 → 拼接 → 裁剪尾部 → 重传。

    首次创建时 key 不存在，get_object_bytes 抛 ClientError，按空串处理。
    超过 cap 字节时只保留尾部 cap 字节。
    """
    try:
        existing = self.get_object_bytes(key)
    except ClientError:
        existing = b""
    combined = existing + data
    if len(combined) > cap:
        combined = combined[-cap:]
    self.upload_bytes(key, combined)
    return f"s3://{self.bucket}/{key}"
```

### 7.2 收割路径

`K8sExecutor._collect_logs()`：

```python
# 当前：
self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log", ...)

# 改为：
job = db.get_job(job_id) or {}
if job.get("repeat_type") in ("daily", "weekly"):
    self.storage.append_bytes(f"jobs/{job_id}/logs/run.log", payload)
else:
    self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log", payload)
```

每周期之间用一行分隔，方便阅读：

```python
if job.get("repeat_type") in ("daily", "weekly"):
    sep = f"\n\n==== run @ {db.now_iso()} ====\n".encode()
    self.storage.append_bytes(f"jobs/{job_id}/logs/run.log", sep + payload)
```

### 7.3 Mock executor

`MockExecutor` 不变（测试用，不覆盖周期累积）。

## 8. 产物覆盖（零改动）

`runner.py` 上传到 `jobs/{job_id}/output/{rel}`，S3 同 key 默认覆盖。**这部分一行都不用改**。

注意：周期任务的 `output/` 目录在收割后被 `shutil.rmtree`（当前行为）— 下一轮从干净状态开始，覆盖语义天然成立。

## 9. 错误处理与边界

| 场景 | 行为 |
|---|---|
| 上轮 timeout，触发点已过 | rearm 时 `_not_in_past` 钳到 now+1min；skip 守卫保护不会双发 |
| weekly 但所有 weekday 都被取消（无法发生） | `_compute_next_run` 范围 1..7 必命中，否则 raise（兜底） |
| rearm 时 `prepare()` 失败 | 异常上抛 → `_watch` 外层 `except` 把 status 置 failed；**不继续 rearm**，循环停止（避免雪崩） |
| 周期任务被 admin 改 repeat_type=none | 下次终态时不再 rearm，自然变一次性 |
| 周期任务 storage_gb 在 prepare 时缺字段 | 沿用现有 fallback `or 10` |

## 10. 测试策略

### 10.1 单测（pytest，无集群）

- `tests/test_timecheck.py` 加 `_compute_next_run` 用例：
  - daily：`2026-07-20T22:00` → `2026-07-21T22:00`
  - weekly `'1,3,5'`：`2026-07-20(周一)` → `2026-07-22(周三)`
  - weekly `'7'`（仅周日）：`2026-07-20(周一)` → `2026-07-26(周日)`
- `tests/test_s3.py`（或新文件）加 `Storage.append_bytes` 用例：
  - 首次（key 不存在）
  - 二次拼接
  - 超 cap 裁剪尾部
  （用 MinIO 真连，沿用 conftest 现状）
- `tests/test_api.py` 加提交校验：
  - weekly 无 weekday → 400
  - weekly weekday=8 → 400
  - 正常提交 → DB 里有 repeat_type/repeat_weekdays

### 10.2 集成（手测清单）

- 提交 `daily` 任务，timeout=2 分钟，验证：
  - 首轮跑完 → status 回 pending，scheduled_at +1 天
  - ssh_port/ssh_password 已更新（debug pod 重建）
  - logs 在 S3 上有 `==== run @ ... ====` 分隔的多段
- 提交 `weekly '1,3,5'` 任务，验证 next_dt 命中下一个匹配日
- DELETE 周期任务（pending 状态）→ scheduler.remove_job 生效，循环停止
- 改 `repeat_type=none`（PATCH）→ 下轮跑完不再 rearm

## 11. 部署

- 代码改完按 AGENTS.md 流程：
  ```bash
  cd frontend && bun run build && cd ..
  docker build --network=host \
    --build-arg HTTP_PROXY=http://127.0.0.1:7891 \
    --build-arg HTTPS_PROXY=http://127.0.0.1:7891 \
    -f docker/server/Dockerfile -t 172.16.50.3:5000/neospark/ddp-server:latest .
  docker push 172.16.50.3:5000/neospark/ddp-server:latest
  kubectl -n ddp rollout restart deployment ddp-server
  ```
- SQLite 升级：`init_db()` 自动加列；老作业 `repeat_type` 默认 `'none'`，行为不变
- 无需改 k8s manifest（`config/ddp.yaml`）

## 12. 不在本次范围

- CronTrigger（更复杂的 cron 表达式）
- 周期任务历史归档（保留每轮独立产物副本）
- 周期任务的失败重试策略（如连续失败 N 次自动暂停）
- 非 admin 用户的 quartz 风格 "每 N 分钟" 语法

这些可在后续迭代加，当前设计不阻塞。

## 13. 影响面总结

| 文件 | 改动 |
|---|---|
| `app/db.py` | 加 2 列 ALTER；`create_job` 加 2 个参数 |
| `app/main.py` | POST/PATCH 加 2 个 Form 字段 + 校验；`_apply_job_edits` 加 2 参数 |
| `app/k8s_executor.py` | `execute` 加 skip 守卫；`_watch` 末尾加 rearm；`_collect_logs` 周期走 append |
| `app/storage.py` | 加 `append_bytes` 方法 |
| `app/timecheck.py` | 加 `_compute_next_run` |
| `frontend/index.html` | 加 2 个 field 块（radio + checkbox） |
| `frontend/src/main.ts` | 加 radio/checkbox 联动、提交校验、编辑表单回填、详情卡片展示、i18n |
| 测试 | `test_timecheck.py`、`test_s3.py`、`test_api.py` 各加几例 |

**不改动**：`docker/ddp-runner/runner.py`、`config/ddp.yaml`、`app/executor.py`(Mock)、`app/admin.py`、`app/auth.py`、`app/gpu.py`、`app/images.py`。
