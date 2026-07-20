# 周期任务（每日/每周）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ddp 调度面板"计划启动时间"下方新增周期选项（一次性/每日/每周，每周可指定星期），让一条作业按周期循环触发，日志按 10MB 上限 append，产物在 S3 上重名覆盖。

**Architecture:** 单记录循环模型 — 一条 job 跑完终态后由 `_watch()` 末尾的 rearm 逻辑计算下一次触发时间，重置为 pending + 重建 debug pod + 重排 DateTrigger。保留现有 `DateTrigger` 单次触发模型，不引入 CronTrigger。S3 append 通过"读旧→拼接→裁剪尾部→重传"实现。

**Tech Stack:** Python 3.12 / FastAPI / APScheduler / SQLite / boto3 / TypeScript + Vite (vanilla, no framework)

**Spec:** `docs/superpowers/specs/2026-07-20-recurring-jobs-design.md`

## Global Constraints

- 工作目录：`/mnt/sdb4/k3s/ddp`
- Python venv：`uv run ...`（如 `uv run python -m pytest -q`）
- 前端构建：`cd frontend && bun run build`
- 测试需要 MinIO 可达（`172.16.50.100:9000`），否则 `test_s3.py` / `test_api.py` 自动 skip
- DB 升级走 `init_db()` 的 `ALTER TABLE ... ADD COLUMN` + `try/except sqlite3.OperationalError` 模式（老库平滑升级）
- DB 列名：`repeat_type`（TEXT NOT NULL DEFAULT 'none'）、`repeat_weekdays`（TEXT，nullable）
- ISO weekday：周一=1 … 周日=7（Python `datetime.isoweekday()`）
- 日志 append 上限：`10 * 1024 * 1024`（10MB），超限只留尾部
- `repeat_type` 仅三值：`none` / `daily` / `weekly`；非 weekly 强制清空 weekdays
- 不改动：`docker/ddp-runner/runner.py`、`config/ddp.yaml`、`app/executor.py`（Mock）、`app/admin.py`、`app/auth.py`、`app/gpu.py`、`app/images.py`

## File Structure

| 文件 | 改动类型 | 责任 |
|---|---|---|
| `app/db.py` | 修改 | 加 2 列 ALTER；`create_job` 加 `repeat_type` / `repeat_weekdays` 参数 |
| `app/storage.py` | 修改 | 加 `append_bytes(key, data, cap=10MB)` 方法 |
| `app/timecheck.py` | 修改 | 加 `_compute_next_run(job)` 函数 |
| `app/k8s_executor.py` | 修改 | `execute` 加 skip 守卫；`_watch` 末尾加 rearm；`_collect_logs` 周期走 append |
| `app/main.py` | 修改 | POST/PATCH 加 `repeat_type` / `repeat_weekdays` Form 字段 + 校验；`_apply_job_edits` 加 2 参数 |
| `frontend/index.html` | 修改 | 在 `scheduled_at` 字段下方插入 radio + weekday checkbox |
| `frontend/src/main.ts` | 修改 | radio/checkbox 联动、提交校验、编辑表单回填、详情卡片展示、i18n |
| `tests/test_timecheck.py` | 修改 | 加 `_compute_next_run` 用例 |
| `tests/test_s3.py` | 修改 | 加 `append_bytes` 用例 |
| `tests/test_api.py` | 修改 | 加 repeat 字段提交校验用例 |

---

### Task 1: DB 列与 create_job 透传

**Files:**
- Modify: `app/db.py:43-173`（`init_db` ALTER 块 + `create_job`）
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.create_job(..., repeat_type="none", repeat_weekdays=None)`；新列在所有 `SELECT *` 结果中可见（`get_job` / `list_jobs` 自动带上，无需改）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_db.py` 末尾：

```python
def test_repeat_columns_default(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.create_user("u", "h", "s")
    db.create_job("jid1", user_id=1, name="n", image="img", entry_command="c",
                  scheduled_at="2099-01-01T00:00", timeout_minutes=5)
    job = db.get_job("jid1")
    assert job["repeat_type"] == "none"
    assert job["repeat_weekdays"] is None


def test_create_job_with_repeat(tmp_path, monkeypatch):
    from app import db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "t.db")
    db.init_db()
    db.create_user("u", "h", "s")
    db.create_job("jid2", user_id=1, name="n", image="img", entry_command="c",
                  scheduled_at="2099-01-01T00:00", timeout_minutes=5,
                  repeat_type="weekly", repeat_weekdays="1,3,5")
    job = db.get_job("jid2")
    assert job["repeat_type"] == "weekly"
    assert job["repeat_weekdays"] == "1,3,5"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run python -m pytest tests/test_db.py -v -k repeat`
Expected: FAIL — `repeat_type` 列不存在或 KeyError

- [ ] **Step 3: 在 `init_db()` 加 ALTER，`create_job` 加参数**

在 `app/db.py` 的 `init_db()` 函数中，找到 `try: conn.execute("ALTER TABLE users ADD COLUMN storage_quota_override_gb REAL")` 那块 ALTER 块的末尾（在 `conn.commit()` 之前），追加：

```python
    for ddl in ["ALTER TABLE jobs ADD COLUMN repeat_type TEXT NOT NULL DEFAULT 'none'",
                "ALTER TABLE jobs ADD COLUMN repeat_weekdays TEXT"]:
        try:
            conn.execute(ddl)
        except sqlite3.OperationalError:
            pass
```

修改 `create_job` 函数签名和 SQL（注意：新增的列要加到 INSERT 的列名和 VALUES 占位符中）：

```python
def create_job(job_id, user_id, name, image, entry_command, scheduled_at, timeout_minutes,
               gpus=0, gpu_mem_mb=None, ssh_port=None, ssh_password=None, status="pending",
               output_path="output", cpu=2, memory_gb=4,
               repeat_type="none", repeat_weekdays=None):
    now = now_iso()
    conn = get_db()
    conn.execute("""
        INSERT INTO jobs (id, user_id, name, filename, image, entry_command, scheduled_at, timeout_minutes,
                          gpus, gpu_mem_mb, ssh_port, ssh_password, status, output_path, cpu, memory_gb,
                          repeat_type, repeat_weekdays, created_at)
        VALUES (?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (job_id, user_id, name, image, entry_command, scheduled_at, timeout_minutes,
          gpus, gpu_mem_mb, ssh_port, ssh_password, status, output_path, cpu, memory_gb,
          repeat_type, repeat_weekdays, now))
    conn.commit()
    conn.close()
```

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run python -m pytest tests/test_db.py -v -k repeat`
Expected: PASS

- [ ] **Step 5: 跑全套 DB 测试确认无回归**

Run: `uv run python -m pytest tests/test_db.py -v`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat(db): add repeat_type/repeat_weekdays columns to jobs table"
```

---

### Task 2: `_compute_next_run` 时间计算

**Files:**
- Modify: `app/timecheck.py`（追加函数）
- Test: `tests/test_timecheck.py`

**Interfaces:**
- Consumes: `job["scheduled_at"]` (ISO 字符串)、`job["repeat_type"]`、`job["repeat_weekdays"]`
- Produces: `timecheck._compute_next_run(job: dict) -> datetime`（tz-aware）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_timecheck.py` 末尾：

```python
class TestComputeNextRun:
    def test_daily_advances_one_day(self):
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "daily", "repeat_weekdays": None}
        nxt = timecheck._compute_next_run(job)
        assert nxt.day == 21
        assert nxt.hour == 22
        assert nxt.minute == 0

    def test_weekly_mon_wed_fri_from_mon(self):
        # 2026-07-20 是周一(isoweekday=1), weekdays=1,3,5 -> next is Wed(3)
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "1,3,5"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 3
        assert nxt.day == 22

    def test_weekly_sunday_only_from_mon(self):
        # 2026-07-20 周一 -> next Sunday(7) = 2026-07-26
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "7"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 7
        assert nxt.day == 26

    def test_weekday_order_does_not_matter(self):
        job = {"scheduled_at": "2026-07-20T22:00:00+08:00",
               "repeat_type": "weekly", "repeat_weekdays": "5,3,1"}
        nxt = timecheck._compute_next_run(job)
        assert nxt.isoweekday() == 3
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run python -m pytest tests/test_timecheck.py -v -k ComputeNextRun`
Expected: FAIL — `AttributeError: module 'app.timecheck' has no attribute '_compute_next_run'`

- [ ] **Step 3: 实现 `_compute_next_run`**

追加到 `app/timecheck.py` 末尾：

```python
def _compute_next_run(job: dict) -> datetime:
    """从 job['scheduled_at'] 算下一个 daily/weekly 触发时间点。

    从 scheduled_at 的第二天起找，避免本轮立刻再次触发。
    """
    from . import db as _db
    cur = datetime.fromisoformat(job["scheduled_at"])
    if cur.tzinfo is None:
        cur = cur.replace(tzinfo=_db.get_tz())
    cur = cur.replace(second=0, microsecond=0)
    if job.get("repeat_type") == "daily":
        return cur + timedelta(days=1)
    # weekly
    raw = job.get("repeat_weekdays") or "1"
    days = sorted({int(d) for d in raw.split(",") if d.strip()})
    for offset in range(1, 8):
        cand = cur + timedelta(days=offset)
        if cand.isoweekday() in days:
            return cand
    raise ValueError("No matching weekday within 7 days")
```

注意：`timecheck.py` 顶部已 `from datetime import datetime, timedelta`，无需再 import。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run python -m pytest tests/test_timecheck.py -v -k ComputeNextRun`
Expected: 全 PASS

- [ ] **Step 5: 跑整套 timecheck 测试**

Run: `uv run python -m pytest tests/test_timecheck.py -v`
Expected: 全 PASS

- [ ] **Step 6: 提交**

```bash
git add app/timecheck.py tests/test_timecheck.py
git commit -m "feat(timecheck): add _compute_next_run for daily/weekly recurrence"
```

---

### Task 3: `Storage.append_bytes` 方法

**Files:**
- Modify: `app/storage.py`（追加方法）
- Test: `tests/test_s3.py`

**Interfaces:**
- Produces: `Storage.append_bytes(key: str, data: bytes, cap: int = 10*1024*1024) -> str`
  - key 不存在时按空串处理（首次创建）
  - 超 cap 字节时只保留尾部 cap 字节

- [ ] **Step 1: 写失败测试**

在 `tests/test_s3.py` 的 `TestUploadDownload` 类里追加（复用 `storage` 和 `fresh_key` fixture）：

```python
    def test_append_first_write(self, storage, fresh_key):
        # key 不存在时等价于 upload
        storage.append_bytes(fresh_key, b"first\n")
        assert storage.get_object_bytes(fresh_key) == b"first\n"

    def test_append_concatenates(self, storage, fresh_key):
        storage.append_bytes(fresh_key, b"aaa\n")
        storage.append_bytes(fresh_key, b"bbb\n")
        assert storage.get_object_bytes(fresh_key) == b"aaa\nbbb\n"

    def test_append_truncates_to_tail_when_over_cap(self, storage):
        key = "test/append-truncate"
        cap = 10
        # 第一次写 8 字节，未超 cap
        storage.append_bytes(key, b"01234567", cap=cap)
        # 第二次追加 5 字节，总 13 > cap=10，只保留尾部 10 字节
        storage.append_bytes(key, b"89abc", cap=cap)
        result = storage.get_object_bytes(key)
        assert len(result) == 10
        assert result == b"56789abc" + b"89"  # 尾部 10 字节
        # 清理
        storage.delete_prefix("test/append-truncate")
```

注意：第三个测试断言需要重新计算。`b"01234567" + b"89abc"` = `b"0123456789abc"`（13 字节），尾部 10 字节 = `b"3456789abc"`。修正断言：

```python
        assert result == b"3456789abc"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run python -m pytest tests/test_s3.py -v -k append`
Expected: FAIL — `AttributeError: 'Storage' object has no attribute 'append_bytes'`（或 skip，若 S3 不可达）

- [ ] **Step 3: 实现 `append_bytes`**

在 `app/storage.py` 的 `upload_bytes` 方法后面追加：

```python
    def append_bytes(self, key: str, data: bytes, cap: int = 10 * 1024 * 1024) -> str:
        """S3 不支持原生 append：读旧 → 拼接 → 裁剪尾部 → 重传。

        key 不存在时按空串处理；超 cap 字节时只保留尾部 cap 字节。
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

注意：`ClientError` 已在文件顶部 import（`from botocore.exceptions import ClientError`），无需加 import。

- [ ] **Step 4: 跑测试验证通过**

Run: `uv run python -m pytest tests/test_s3.py -v -k append`
Expected: 全 PASS（若 S3 不可达则 skip，本地运行需先确认 MinIO 可达）

- [ ] **Step 5: 提交**

```bash
git add app/storage.py tests/test_s3.py
git commit -m "feat(storage): add append_bytes with tail-truncation cap"
```

---

### Task 4: 后端提交/PATCH 字段校验

**Files:**
- Modify: `app/main.py:188-261`（`create_job` 路由）、`app/main.py:411-470`（`_apply_job_edits`）、`app/main.py:349-363` 与 `473-487`（两个 PATCH 路由的 Form 参数）

**Interfaces:**
- Consumes: 前端传来的 `repeat_type`（form 字符串）+ `repeat_weekdays`（form 多值）
- Produces: `/api/jobs` 和 `/api/admin/jobs/{id}` PATCH 接受 `repeat_type` / `repeat_weekdays` 字段，校验后透传 DB

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_api.py` 的 `TestJobs` 类中：

```python
    def test_submit_weekly_without_weekdays_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "wk", "image": "ddp-cuda-ssh:latest",
                  "scheduled_at": FUTURE, "repeat_type": "weekly"},
        )
        assert resp.status_code == 400
        assert "weekday" in resp.json()["detail"].lower()

    def test_submit_weekly_bad_weekday_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "wk", "image": "ddp-cuda-ssh:latest",
                  "scheduled_at": FUTURE, "repeat_type": "weekly",
                  "repeat_weekdays": ["1", "8"]},
        )
        assert resp.status_code == 400

    def test_submit_weekly_ok(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "wk", "image": "ddp-cuda-ssh:latest",
                  "scheduled_at": FUTURE, "repeat_type": "weekly",
                  "repeat_weekdays": ["1", "3", "5"]},
        )
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        job = authed_client.get(f"/api/jobs/{job_id}").json()
        assert job["repeat_type"] == "weekly"
        assert job["repeat_weekdays"] == "1,3,5"

    def test_submit_daily_clears_weekdays(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "daily", "image": "ddp-cuda-ssh:latest",
                  "scheduled_at": FUTURE, "repeat_type": "daily",
                  "repeat_weekdays": ["1", "2"]},
        )
        assert resp.status_code == 200
        job = authed_client.get(f"/api/jobs/{resp.json()['id']}").json()
        assert job["repeat_type"] == "daily"
        assert job["repeat_weekdays"] is None

    def test_submit_bad_repeat_type_rejected(self, authed_client):
        resp = authed_client.post(
            "/api/jobs",
            data={"name": "x", "image": "ddp-cuda-ssh:latest",
                  "scheduled_at": FUTURE, "repeat_type": "monthly"},
        )
        assert resp.status_code == 400

    def test_edit_repeat_type(self, authed_client):
        job_id = _submit_job(authed_client)
        resp = authed_client.patch(f"/api/jobs/{job_id}", data={
            "repeat_type": "daily"})
        assert resp.status_code == 200
        assert resp.json()["repeat_type"] == "daily"
```

- [ ] **Step 2: 跑测试验证失败**

Run: `uv run python -m pytest tests/test_api.py -v -k "weekly or daily or repeat"`
Expected: FAIL — 字段未识别 / 校验未实现

- [ ] **Step 3: 抽出公共校验函数**

在 `app/main.py` 的 `_validate_output_path` 函数后面追加：

```python
def _validate_repeat(repeat_type: str | None, repeat_weekdays: list[str] | None):
    """返回 (repeat_type, repeat_weekdays_str_or_None)；非法时 raise HTTPException(400)."""
    repeat_type = (repeat_type or "none").strip()
    if repeat_type not in ("none", "daily", "weekly"):
        raise HTTPException(400, f"Invalid repeat_type: {repeat_type}")
    days: list[int] = []
    if repeat_type == "weekly":
        if not repeat_weekdays:
            raise HTTPException(400, "weekly requires at least one weekday (1-7)")
        for d in repeat_weekdays:
            try:
                n = int(d)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Invalid weekday: {d}")
            if not 1 <= n <= 7:
                raise HTTPException(400, f"Weekday must be 1-7, got {n}")
            days.append(n)
    return repeat_type, (",".join(map(str, sorted(set(days))))) if days else None
```

- [ ] **Step 4: 修改 POST `/api/jobs` 路由**

在 `app/main.py` 的 `create_job` 路由签名末尾加 2 个 Form 参数：

```python
@app.post("/api/jobs")
async def create_job(
    user: dict = Depends(auth.get_current_user),
    name: str = Form(...),
    image: str = Form(...),
    entry_command: str = Form("python main.py"),
    scheduled_at: str = Form(...),
    timeout_minutes: int = Form(60),
    gpus: int = Form(0),
    gpu_mem_mb: int | None = Form(None),
    cpu: float = Form(2),
    memory_gb: float = Form(4),
    output_path: str = Form("output"),
    repeat_type: str = Form("none"),
    repeat_weekdays: list[str] = Form([]),
):
```

在 `db.create_job(...)` 调用中加 2 个参数。找到 `db.create_job(job_id, user["id"], name, image, entry_command, scheduled_utc, ...)` 调用，把它改成：

```python
    rt, rw = _validate_repeat(repeat_type, repeat_weekdays)
    db.create_job(job_id, user["id"], name, image, entry_command, scheduled_utc,
                  timeout_minutes, gpus=gpus, gpu_mem_mb=gpu_mem_mb if gpus else None,
                  ssh_port=ssh_info.get("ssh_port"), ssh_password=ssh_info.get("ssh_password"),
                  status="initializing" if initializing else "pending",
                  output_path=output_path, cpu=cpu, memory_gb=memory_gb,
                  repeat_type=rt, repeat_weekdays=rw)
```

- [ ] **Step 5: 修改 PATCH 路由和 `_apply_job_edits`**

把用户 PATCH 路由 `update_pending_job` 的签名末尾加：

```python
                             repeat_type: str = Form(None),
                             repeat_weekdays: list[str] = Form(None)):
```

并把调用 `_apply_job_edits(...)` 加上 `repeat_type=repeat_type, repeat_weekdays=repeat_weekdays`。

admin PATCH 路由 `admin_update_job` 同样处理。

在 `_apply_job_edits` 函数签名加：

```python
def _apply_job_edits(job, user, name, entry_command, scheduled_at,
                     timeout_minutes, gpus, gpu_mem_mb, output_path,
                     cpu=None, memory_gb=None,
                     repeat_type=None, repeat_weekdays=None):
```

在函数体中（建议在 `output_path` 校验之后、`if not updates:` 之前）加：

```python
    if repeat_type is not None or repeat_weekdays is not None:
        rt = repeat_type or job.get("repeat_type") or "none"
        # weekdays 没传就保留原值（仅当仍是 weekly）
        if repeat_weekdays is None:
            existing_days = (job.get("repeat_weekdays") or "").split(",")
            existing_days = [d for d in existing_days if d.strip()]
            rw_days = existing_days if rt == "weekly" else None
        else:
            rw_days = repeat_weekdays
        rt, rw = _validate_repeat(rt, rw_days if rw_days else None)
        updates["repeat_type"] = rt
        updates["repeat_weekdays"] = rw
```

- [ ] **Step 6: 跑测试验证通过**

Run: `uv run python -m pytest tests/test_api.py -v -k "weekly or daily or repeat or edit_repeat"`
Expected: 全 PASS

- [ ] **Step 7: 跑整套 test_api 无回归**

Run: `uv run python -m pytest tests/test_api.py -v`
Expected: 全 PASS（若 S3 不可达则 skip）

- [ ] **Step 8: 提交**

```bash
git add app/main.py tests/test_api.py
git commit -m "feat(api): accept repeat_type/repeat_weekdays on POST/PATCH /api/jobs"
```

---

### Task 5: k8s_executor rearm + skip 守卫 + 日志 append

**Files:**
- Modify: `app/k8s_executor.py:207-226`（`execute`）、`app/k8s_executor.py:272-280`（`_watch` 末尾）、`app/k8s_executor.py:324-340`（`_collect_logs`）

**Interfaces:**
- Consumes: `timecheck._compute_next_run`、`storage.append_bytes`、`main.scheduler`、`main._not_in_past`
- Produces: 周期任务终态后自动 rearm 为 pending；周期任务的日志按 append 累积

**注意：** 这一步无法用单测覆盖（mock executor 不会进 rearm），由后续集成测试手测验证。但 skip 守卫和 rearm 逻辑的代码必须写完整、可读。

- [ ] **Step 1: `execute` 入口加 skip 守卫**

修改 `app/k8s_executor.py` 的 `execute` 方法：

```python
    async def execute(self, job_id: str):
        job = db.get_job(job_id)
        if not job:
            return
        if job["status"] != "pending":
            # 上一轮还在 running/initializing，或已被 cancelled — 跳过本次触发
            db.log_event("DEBUG", "system",
                         f"Trigger skipped (status={job['status']}): {job_id}")
            return
        db.update_job(job_id, status="running",
                      started_at=db.now_iso())
        db.log_event("DEBUG", "system", f"Job started (k8s): {job_id}")
        name = self._name(job_id)
        # debug pod makes way for the gpu job (same PVC can't serve both)
        await self._ignore_notfound(self.core.delete_namespaced_pod, name, NAMESPACE)
        try:
            await asyncio.to_thread(self.batch.create_namespaced_job, NAMESPACE, self._gpu_job(job))
        except ApiException as e:
            if e.status != 409:
                db.update_job(job_id, status="failed",
                              finished_at=db.now_iso(),
                              error=f"k8s create failed: {e.reason}")
                db.log_event("ERROR", "system", f"Job {job_id} create failed: {e.reason}")
                return
        await self.watch(job_id)
```

- [ ] **Step 2: `_watch` 末尾加 rearm**

修改 `_watch` 方法，在设置终态（`db.update_job(job_id, status=result, ...)`）之后、`db.log_event("DEBUG", "system", f"Job finished:...")` 之前或之后，插入 rearm 逻辑。**关键：rearm 失败不能让 _watch 抛出**（外层 except 会把 status 改成 failed，但 status 已经是 done/timeout 了，反而误导）。所以 rearm 用 try/except 包住：

把 `_watch` 末尾改为：

```python
        await self._collect_logs(job_id)
        await self._collect_outputs(job_id)
        objects = self.storage.list_objects(self.storage.bucket, f"jobs/{job_id}/output/")
        db.update_job(job_id, status=result,
                      finished_at=db.now_iso(),
                      output_count=len(objects),
                      s3_prefix=f"{BUCKET}/jobs/{job_id}/",
                      error=error)
        db.log_event("DEBUG", "system", f"Job finished: {job_id} status={result}")

        # ── rearm recurring jobs (daily/weekly) ──
        try:
            self._maybe_rearm(job_id)
        except Exception as e:
            db.log_event("ERROR", "system", f"Rearm failed for {job_id}: {e}")
```

并在类中新增方法（放在 `_watch` 之后）：

```python
    def _maybe_rearm(self, job_id: str):
        """若 job 是周期任务，计算下一次触发时间并重排调度。"""
        from . import timecheck as _tc
        from .main import scheduler, _not_in_past  # 延迟 import 避免循环
        job = db.get_job(job_id)
        if not job or job.get("repeat_type") not in ("daily", "weekly"):
            return
        next_dt = _tc._compute_next_run(job)
        owner = db.get_user_by_id(job["user_id"]) or {}
        if not owner.get("is_admin"):
            next_dt = _tc.check_scheduled_time(next_dt)
        next_dt = _not_in_past(next_dt)
        db.update_job(job_id,
                      status="pending",
                      scheduled_at=next_dt.isoformat(),
                      started_at=None, finished_at=None,
                      error=None, output_count=0)
        db.log_event("INFO", "job",
                     f"Job rearmed: {job_id} -> {next_dt.isoformat()}",
                     user_id=job["user_id"])
        # debug pod 的异步重建（不阻塞 _watch 返回）
        asyncio.create_task(self._rearm_prepare(job_id, job, owner))

    async def _rearm_prepare(self, job_id: str, job_snapshot: dict, owner: dict):
        """重建 debug pod 并刷新 ssh 信息、重排调度。"""
        from .main import scheduler, _not_in_past
        from datetime import datetime as _dt
        try:
            ssh_info = await self.prepare({
                "id": job_id,
                "user_id": job_snapshot["user_id"],
                "image": job_snapshot["image"],
                "storage_gb": owner.get("storage_quota_override_gb")
                              or db.get_param("storage_default_quota_gb")
                              or 10,
            })
            db.update_job(job_id,
                          ssh_port=ssh_info["ssh_port"],
                          ssh_password=ssh_info["ssh_password"])
            next_dt = _dt.fromisoformat(db.get_job(job_id)["scheduled_at"])
            scheduler.add_job(executor_stub().execute, DateTrigger(run_date=next_dt),
                              args=[job_id], id=job_id, replace_existing=True)
            # wait_ready 异步
            asyncio.create_task(self.wait_ready(job_id))
        except Exception as e:
            db.log_event("ERROR", "system", f"Rearm prepare failed for {job_id}: {e}")
            # prepare 失败 — 标 failed，停止循环
            db.update_job(job_id, status="failed",
                          finished_at=db.now_iso(),
                          error=f"rearm prepare failed: {e}")
```

**等一下** — 上面的 `executor_stub()` 是凭空发明的，不能用。修正：scheduler 添加 job 时要传 `self.execute`。最终正确的 `_rearm_prepare`：

```python
    async def _rearm_prepare(self, job_id: str, job_snapshot: dict, owner: dict):
        """重建 debug pod 并刷新 ssh 信息、重排调度。"""
        from .main import scheduler, _not_in_past
        from .timecheck import _compute_next_run
        try:
            ssh_info = await self.prepare({
                "id": job_id,
                "user_id": job_snapshot["user_id"],
                "image": job_snapshot["image"],
                "storage_gb": owner.get("storage_quota_override_gb")
                              or db.get_param("storage_default_quota_gb")
                              or 10,
            })
            db.update_job(job_id,
                          ssh_port=ssh_info["ssh_port"],
                          ssh_password=ssh_info["ssh_password"])
            # 重新读出 scheduled_at（_maybe_rearm 已写入），并排触发器
            job_now = db.get_job(job_id)
            from datetime import datetime as _dt
            next_dt = _dt.fromisoformat(job_now["scheduled_at"])
            scheduler.add_job(self.execute, DateTrigger(run_date=next_dt),
                              args=[job_id], id=job_id, replace_existing=True)
            asyncio.create_task(self.wait_ready(job_id))
        except Exception as e:
            db.log_event("ERROR", "system", f"Rearm prepare failed for {job_id}: {e}")
            db.update_job(job_id, status="failed",
                          finished_at=db.now_iso(),
                          error=f"rearm prepare failed: {e}")
```

同时简化 `_maybe_rearm`：它只做"算下次时间 + DB 置 pending + 触发异步 prepare"，重排调度移到 `_rearm_prepare` 里（因为 prepare 可能改 status 为 failed，那时不该再排触发器；成功后才排）。最终版本：

```python
    def _maybe_rearm(self, job_id: str):
        """若 job 是周期任务，算下次触发时间、置 pending、异步重建 debug pod。"""
        from . import timecheck as _tc
        from .main import _not_in_past
        job = db.get_job(job_id)
        if not job or job.get("repeat_type") not in ("daily", "weekly"):
            return
        next_dt = _tc._compute_next_run(job)
        owner = db.get_user_by_id(job["user_id"]) or {}
        if not owner.get("is_admin"):
            next_dt = _tc.check_scheduled_time(next_dt)
        next_dt = _not_in_past(next_dt)
        db.update_job(job_id,
                      status="pending",
                      scheduled_at=next_dt.isoformat(),
                      started_at=None, finished_at=None,
                      error=None, output_count=0)
        db.log_event("INFO", "job",
                     f"Job rearmed: {job_id} -> {next_dt.isoformat()}",
                     user_id=job["user_id"])
        asyncio.create_task(self._rearm_prepare(job_id, job, owner))
```

记得在文件顶部确认 `DateTrigger` 已 import：

```python
from apscheduler.triggers.date import DateTrigger
```

- [ ] **Step 3: `_collect_logs` 周期任务走 append**

修改 `_collect_logs` 方法，在 `self.storage.upload_bytes(...)` 那一行替换为条件分支。原代码：

```python
            self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log",
                                      resp.data.decode("utf-8", errors="replace").encode())
```

改为：

```python
            payload = resp.data.decode("utf-8", errors="replace").encode()
            job_row = db.get_job(job_id) or {}
            if job_row.get("repeat_type") in ("daily", "weekly"):
                sep = f"\n\n==== run @ {db.now_iso()} ====\n".encode()
                self.storage.append_bytes(f"jobs/{job_id}/logs/run.log", sep + payload)
            else:
                self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log", payload)
```

- [ ] **Step 4: 静态检查 — import 完整性**

Run: `uv run python -c "from app.k8s_executor import K8sExecutor; print('OK')"`
Expected: 打印 `OK`，无 ImportError

- [ ] **Step 5: 跑 mock 模式测试确认无回归**

Run: `uv run python -m pytest tests/ -v -x`
Expected: 全 PASS（或 skip S3 用例）

mock executor 没改，rearm 只在 k8s executor 里，不会触发。这一步只是确认 import 链没坏。

- [ ] **Step 6: 提交**

```bash
git add app/k8s_executor.py
git commit -m "feat(executor): rearm recurring jobs, skip-if-running, append logs"
```

---

### Task 6: 前端 HTML — 周期字段

**Files:**
- Modify: `frontend/index.html:84-88`（在 `scheduled_at` 字段块之后插入）

- [ ] **Step 1: 在 `scheduled_at` field 下方插入周期字段**

找到 `frontend/index.html` 中的：

```html
        <div class="field">
          <label data-i18n="scheduledStart">Scheduled Start (local time)</label>
          <input type="datetime-local" name="scheduled_at" id="scheduled_at" required />
          <div class="hint" data-i18n="scheduledStartHint">Platform fires within ~30s of this time.</div>
        </div>
```

在它之后、`maxRuntime` 字段之前，插入：

```html
        <div class="field">
          <label data-i18n="repeat">Repeat</label>
          <div class="repeat-options">
            <label class="repeat-radio"><input type="radio" name="repeat_type" value="none" checked /> <span data-i18n="repeatNone">One-off</span></label>
            <label class="repeat-radio"><input type="radio" name="repeat_type" value="daily" /> <span data-i18n="repeatDaily">Daily</span></label>
            <label class="repeat-radio"><input type="radio" name="repeat_type" value="weekly" /> <span data-i18n="repeatWeekly">Weekly</span></label>
          </div>
        </div>
        <div class="field" id="repeat-weekdays-field" style="display:none">
          <label data-i18n="repeatWeekdays">Weekdays</label>
          <div class="weekday-checks">
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="1" /> <span data-i18n="mon">Mon</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="2" /> <span data-i18n="tue">Tue</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="3" /> <span data-i18n="wed">Wed</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="4" /> <span data-i18n="thu">Thu</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="5" /> <span data-i18n="fri">Fri</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="6" /> <span data-i18n="sat">Sat</span></label>
            <label class="weekday"><input type="checkbox" name="repeat_weekdays" value="7" /> <span data-i18n="sun">Sun</span></label>
          </div>
        </div>
```

- [ ] **Step 2: 构建确认无报错**

Run: `cd frontend && bun run build`
Expected: 构建成功，`dist/index.html` 包含新字段

- [ ] **Step 3: 提交**

```bash
git add frontend/index.html frontend/dist
git commit -m "feat(frontend): add repeat_type/weekday fields to submit form"
```

---

### Task 7: 前端 main.ts — i18n + 联动 + 校验 + 回填

**Files:**
- Modify: `frontend/src/main.ts`（多处）

- [ ] **Step 1: 加 i18n 文案**

在 `I18N.en` 对象中，找到 `repeatWeekly: "Weekly"` 那一行（约 114 行），在它附近补充新键。把 en 块的 `repeatDaily/repeatWeekdays/repeatWeekly` 三行扩展为：

```javascript
    repeatNone: "One-off", repeatDaily: "Daily", repeatWeekly: "Weekly",
    repeat: "Repeat", repeatWeekdays: "Weekdays",
    weekdaysRequired: "Select at least one weekday.",
    mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun",
    repeatSummaryNone: "One-off", repeatSummaryDaily: "Daily",
    repeatSummaryWeekly: "Weekly ({days})",
```

在 `I18N.zh` 对象中找到 `repeatWeekly: "每周"`，对应改成：

```javascript
    repeatNone: "一次性", repeatDaily: "每天", repeatWeekly: "每周",
    repeat: "周期", repeatWeekdays: "星期",
    weekdaysRequired: "至少选择一个星期。",
    mon: "周一", tue: "周二", wed: "周三", thu: "周四", fri: "周五", sat: "周六", sun: "周日",
    repeatSummaryNone: "一次性", repeatSummaryDaily: "每天",
    repeatSummaryWeekly: "每周 ({days})",
```

注意要删除原本已存在的 `repeatDaily/repeatWeekdays/repeatWeekly` 旧行（在 `timeWindowRepeat:` 那行附近），避免重复键。

- [ ] **Step 2: Job 接口加字段**

找到 `interface Job { ... }`（约第 7 行），在 `output_path: string;` 之后追加：

```typescript
  repeat_type: string;
  repeat_weekdays: string | null;
```

- [ ] **Step 3: 提交表单联动 + 校验**

找到 `async function submitJob(e: SubmitEvent): Promise<void>` 函数（约第 687 行）。在 `btn.disabled = true;` 之前加校验：

```javascript
async function submitJob(e: SubmitEvent): Promise<void> {
  e.preventDefault();
  const form = e.target as HTMLFormElement;
  const fd = new FormData(form);

  // 周期校验：weekly 必须勾选至少一个 weekday
  if (fd.get('repeat_type') === 'weekly') {
    const days = fd.getAll('repeat_weekdays');
    if (days.length === 0) { alert(t('weekdaysRequired')); return; }
  }

  const btn = $<HTMLButtonElement>('submit-btn');
  btn.disabled = true; btn.textContent = t('scheduling');
  // ... 余下原逻辑保持
```

注意：原 `submitJob` 函数体的剩余部分（fetch、reset、setDefaultTime 等）保持不变，只改开头部分。

- [ ] **Step 4: 加 radio ↔ checkbox 联动**

在 `frontend/src/main.ts` 找到事件绑定区（约第 1045 行附近的 `$('job-form').addEventListener('submit', submitJob);`），在其后加：

```typescript
  document.querySelectorAll<HTMLInputElement>('input[name="repeat_type"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const weeklyField = document.getElementById('repeat-weekdays-field');
      if (weeklyField) {
        weeklyField.style.display = (radio as HTMLInputElement).value === 'weekly' && radio.checked ? '' : 'none';
      }
    });
  });
```

- [ ] **Step 5: 编辑表单加字段回填**

找到 `function showEditForm(jobId: string): void`（约第 915 行）。在编辑表单 HTML 中（在 `scheduled_at` 字段之后、`timeout_minutes` 之前）插入周期字段：

```typescript
function showEditForm(jobId: string): void {
  const job = allJobs.find(j => j.id === jobId);
  if (!job) return;
  const rt = job.repeat_type || 'none';
  const days = (job.repeat_weekdays || '').split(',').filter(Boolean);
  const dayChecked = (n: string) => days.includes(n) ? 'checked' : '';
  $('modal-body').innerHTML = `
    <form id="edit-form" data-job-id="${job.id}">
      <div class="field"><label>${t('jobName')}</label><input name="name" value="${escapeHtml(job.name)}" required /></div>
      <div class="field"><label>${t('entryCommand')}</label><input name="entry_command" value="${escapeHtml(job.entry_command)}" required /></div>
      <div class="field"><label>${t('scheduledStart')}</label><input type="datetime-local" name="scheduled_at" value="${toLocalInput(job.scheduled_at)}" required /></div>
      <div class="field">
        <label>${t('repeat')}</label>
        <div class="repeat-options">
          <label class="repeat-radio"><input type="radio" name="repeat_type" value="none" ${rt === 'none' ? 'checked' : ''} /> <span>${t('repeatNone')}</span></label>
          <label class="repeat-radio"><input type="radio" name="repeat_type" value="daily" ${rt === 'daily' ? 'checked' : ''} /> <span>${t('repeatDaily')}</span></label>
          <label class="repeat-radio"><input type="radio" name="repeat_type" value="weekly" ${rt === 'weekly' ? 'checked' : ''} /> <span>${t('repeatWeekly')}</span></label>
        </div>
      </div>
      <div class="field" id="edit-repeat-weekdays-field" style="display:${rt === 'weekly' ? '' : 'none'}">
        <label>${t('repeatWeekdays')}</label>
        <div class="weekday-checks">
          ${['1','2','3','4','5','6','7'].map(n => `<label class="weekday"><input type="checkbox" name="repeat_weekdays" value="${n}" ${dayChecked(n)} /> <span>${t(['mon','tue','wed','thu','fri','sat','sun'][Number(n)-1])}</span></label>`).join('')}
        </div>
      </div>
      <div class="field"><label>${t('maxRuntime')}</label><input type="number" name="timeout_minutes" value="${job.timeout_minutes}" min="1" max="1440" /></div>
      <div class="field"><label>${t('outputPath')}</label><input name="output_path" value="${escapeHtml(job.output_path || 'output')}" /></div>
      <div class="field"><label>${t('gpus')}</label><input type="number" name="gpus" value="${job.gpus}" min="0" max="${gpuQuota}" /></div>
      <div class="field"><label>${t('cpuCores')}</label><input type="number" name="cpu" value="${job.cpu ?? 2}" min="0.5" step="0.5" max="${cpuQuota}" /></div>
      <div class="field"><label>${t('memoryGb')}</label><input type="number" name="memory_gb" value="${job.memory_gb ?? 4}" min="0.5" step="0.5" max="${memQuota}" /></div>
      <div class="field"><label>${t('gpuMem')}</label><input type="number" name="gpu_mem_mb" value="${job.gpu_mem_mb ?? ''}" min="0" step="1024" /></div>
      <button type="submit" class="btn-submit">${t('save')}</button>
    </form>`;

  // 编辑表单的 radio 联动
  document.querySelectorAll<HTMLInputElement>('#edit-form input[name="repeat_type"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const f = document.getElementById('edit-repeat-weekdays-field');
      if (f) f.style.display = (radio as HTMLInputElement).value === 'weekly' && radio.checked ? '' : 'none';
    });
  });
}
```

- [ ] **Step 6: 详情卡片展示周期**

找到 `openModal` 函数中渲染详情的部分（约第 840 行，`scheduledUtc` 行）。在该行之后追加一行周期展示。找到：

```typescript
        <div class="kv"><span class="k">${t('scheduledUtc')}</span><span class="v">${job.scheduled_at ? new Date(job.scheduled_at).toLocaleString() : '—'}</span></div>
```

在它之后加：

```typescript
        <div class="kv"><span class="k">${t('repeat')}</span><span class="v">${formatRepeat(job)}</span></div>
```

并在文件中（`statusLabel` 函数附近）加一个辅助函数：

```typescript
function formatRepeat(job: Job): string {
  const rt = job.repeat_type || 'none';
  if (rt === 'none') return t('repeatSummaryNone');
  if (rt === 'daily') return t('repeatSummaryDaily');
  if (rt === 'weekly') {
    const names = ['mon','tue','wed','thu','fri','sat','sun'];
    const days = (job.repeat_weekdays || '').split(',').filter(Boolean)
      .map(n => t(names[Number(n) - 1])).join(' ');
    return t('repeatSummaryWeekly').replace('{days}', days);
  }
  return rt;
}
```

- [ ] **Step 7: 提交表单 reset 后清空 weekday**

找到 `submitJob` 函数末尾的 `form.reset();` 语句，在其后加：

```typescript
    form.reset();
    // 手动重置 weekday 可见性（form.reset 不触发 change 事件）
    const wkd = document.getElementById('repeat-weekdays-field');
    if (wkd) wkd.style.display = 'none';
```

- [ ] **Step 8: 构建并人工检查**

Run: `cd frontend && bun run build`
Expected: 构建成功，无 TS 错误

打开 `frontend/dist/index.html` 检查页面渲染（可选，命令行不易做）。

- [ ] **Step 9: 提交**

```bash
git add frontend/src/main.ts frontend/dist
git commit -m "feat(frontend): wire repeat fields with i18n, validation, edit, display"
```

---

### Task 8: 集成手测 + 部署

**Files:** 无（运行时验证）

- [ ] **Step 1: 跑全部单测确认绿**

Run: `cd /mnt/sdb4/k3s/ddp && uv run python -m pytest -v`
Expected: 全 PASS（S3 相关用例视可达性 PASS 或 skip）

- [ ] **Step 2: 构建镜像**

Run（在 `/mnt/sdb4/k3s/ddp`）:

```bash
cd frontend && bun run build && cd ..
docker build --network=host \
  --build-arg HTTP_PROXY=http://127.0.0.1:7891 \
  --build-arg HTTPS_PROXY=http://127.0.0.1:7891 \
  -f docker/server/Dockerfile -t 172.16.50.3:5000/neospark/ddp-server:latest .
docker push 172.16.50.3:5000/neospark/ddp-server:latest
```

Expected: build + push 成功

- [ ] **Step 3: 滚动重启 ddp-server**

Run: `kubectl -n ddp rollout restart deployment ddp-server && kubectl -n ddp rollout status deployment ddp-server`
Expected: rollout 成功

- [ ] **Step 4: 浏览器手测**

访问 http://172.16.50.3:8888 ，登录后：

1. **提交表单**：看到"周期"3 个 radio（一次性/每日/每周）。选"每周"时下方展开 7 个 weekday checkbox。
2. **校验**：选 weekly 不勾任何 weekday → 提交应弹窗"至少选择一个星期"。
3. **提交 weekly**：勾选 周一、周三、周五，提交。作业详情页"周期"一行显示"每周 周一 周三 周五"。
4. **DB 验证**：`kubectl -n ddp exec deploy/ddp-server -- sqlite3 /app/data/ddp.db "SELECT id, repeat_type, repeat_weekdays FROM jobs ORDER BY created_at DESC LIMIT 3"`
5. **编辑**：打开一个 pending 作业的编辑表单，确认 radio/checkbox 回填正确。把"每周"改成"一次性"保存，再次打开应仍是"一次性"。
6. **日志累积（周期任务跑完 2 轮）**：提交一个 daily 任务，timeout=2 分钟。等 2 轮跑完后，下载日志，应看到两段 `==== run @ ... ====` 分隔的内容。
7. **产物覆盖**：在 workspace 写一个固定文件名的产物（如 `output/result.txt`），跑 2 轮后，详情页应只看到 1 个 `result.txt`（第 2 轮覆盖第 1 轮）。
8. **取消**：周期任务 pending 时 DELETE → `scheduler.remove_job` 生效，循环停止。验证方法：删除后 1 分钟内 `scheduled_at` 不再变化。

- [ ] **Step 5: 更新运维备忘**

在 `ddp/AGENTS.md` 的"架构要点"或"已知遗留"附近加一行：

```
- **周期任务**：`repeat_type` ∈ {none, daily, weekly}，weekly 用 `repeat_weekdays`（"1,3,5" 等，ISO 周一=1）。终态后在 `_watch` 末尾 rearm：算下次时间 → 置 pending → 异步重建 debug pod → 重排 DateTrigger。日志走 `Storage.append_bytes`（10MB 上限，超限裁尾），周期任务每轮加分隔符。产物 S3 同 key 覆盖（零改动）。
```

并 `git commit`。

---

## Self-Review 结果

**1. Spec coverage 检查：**
- §3 数据模型 → Task 1 ✓
- §4 前端 → Task 6（HTML）+ Task 7（TS）✓
- §5 后端提交/PATCH → Task 4 ✓
- §6 调度循环 → Task 5 ✓
- §7 日志 append → Task 3（storage）+ Task 5（executor 调用）✓
- §8 产物覆盖 → Task 8 Step 7 手测（零代码改动）✓
- §9 错误处理 → rearm prepare 失败兜底在 Task 5 Step 2 代码中 ✓
- §10 测试 → Task 1/2/3/4 单测 + Task 8 集成 ✓
- §11 部署 → Task 8 ✓

**2. Placeholder 扫描：** 无 TBD/TODO；每一步都有具体代码或命令。

**3. Type 一致性：** 
- `_compute_next_run(job: dict) -> datetime` — Task 2 定义，Task 5 使用 ✓
- `Storage.append_bytes(key, data, cap=...)` — Task 3 定义，Task 5 使用 ✓
- `db.create_job(..., repeat_type=, repeat_weekdays=)` — Task 1 定义，Task 4 使用 ✓
- Job.repeat_type / repeat_weekdays — Task 7 Step 2 接口定义，前后端一致 ✓

无遗漏，计划可执行。
