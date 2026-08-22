# Plan 004: 单 worker 持久任务与 SSE 进度

> **Executor instructions**: Follow this plan step by step. Run every
> verification command and confirm the expected result before moving to the
> next step. If anything in the "STOP conditions" section occurs, stop and
> report — do not improvise. When done, update the status row for this plan
> in `plans/README.md` — unless a reviewer dispatched you and told you they
> maintain the index.
>
> **Drift check (run first)**:
> ```bash
> git rev-parse --short HEAD
> git diff --stat a23807c -- assistant/app.py assistant/database/models.py assistant/database/engine.py assistant/security/csrf.py assistant/lifecycle.py
> git status --short -- assistant
> ```
> If those files do not exist, 003 is not done — STOP. Do **not** use `git diff a23807c..HEAD`. If they changed, compare "Current state" against live code.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/003-assistant-skeleton.md
- **Category**: dx
- **Planned at**: commit `a23807c`, reviewed 2026-08-22

## Why this matters

物流同步会跑很多分钟，请求线程里直接调紫鸟会卡住页面、刷新丢失进度、重复点击会开并行 `zclaw_exec`。FastAPI `BackgroundTasks` 没有持久状态和中断恢复。本计划用 SQLite `jobs` 表 + 进程内 **一条** worker 线程：同一时刻一个紫鸟任务、心跳、interrupted 恢复、SSE 可断线续传。本计划只实现 `environment_check` 一种 handler（只读）。不写飞书、不发私信、不批准、不自动重试写操作（本计划也没有写操作）。

## Current state

003 应已提供：

- FastAPI app + 本机会话 + CSRF
- SQLAlchemy models：`jobs`、`job_events`（若 003 漏表，本计划可 **只 ADD** 这两张表，不要重命名 003 的其它表）
- SQLite WAL + `check_same_thread=False`
- `list_running_stores()` 诊断路径

紫鸟执行必须串行：`zclaw_exec` 不是线程安全的产品约定。Worker 领取任务用单线程循环即可，不要用线程池跑多个 ziniao job。

日志：入口 `configure_logging`，库 `getLogger`。

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| 测试 | `.venv/bin/python -m unittest discover -s tests/assistant -q` | `OK` |
| 回归 | `.venv/bin/python -m unittest discover -s tests -q` | `OK` |

## Scope

**In scope**:

- `assistant/jobs/__init__.py`
- `assistant/jobs/worker.py`
- `assistant/jobs/registry.py`
- `assistant/jobs/progress.py`
- `assistant/jobs/locks.py`
- `assistant/jobs/handlers/__init__.py`
- `assistant/jobs/handlers/environment_check.py`
- `assistant/api/jobs.py`
- `assistant/web/templates/jobs.html`
- `assistant/web/templates/job_detail.html`
- `assistant/web/routes.py`（只加 jobs 页面路由）
- `assistant/lifecycle.py`（启动 worker、关闭时 stop claiming）
- `assistant/database/models.py`（仅当 003 缺 jobs 列时补齐，见下）
- `tests/assistant/test_jobs.py`
- `plans/README.md` status 行

**Out of scope**:

- `shipment_sync` / `followup_generate` / `message_send` / `feishu_update` handlers（005 才加只读 sync + 只读「使用语言」；本计划 registry 仍不得登记写操作）
- FastAPI `BackgroundTasks` 作为执行器
- Celery / Redis / 多进程 worker
- 改 `zclaw.py` 的 open_store/close_store
- 任何 `--execute` 写路径

`jobs` 表若 003 已建，列应包括：`id, job_type, store_id, status, progress_current, progress_total, progress_message, requested_by, created_at, started_at, heartbeat_at, finished_at, error_code, error_summary, result_summary, log_path`。缺列才迁移。

## Git workflow

- Branch: `advisor/004-jobs-worker-sse`
- Commit message example: `Add a single-thread SQLite job worker with SSE progress.`
- Do NOT push unless asked.

## Steps

### Step 1: 领取、心跳、interrupted

`assistant/jobs/worker.py`：

- 应用启动时：把 `status==running` 且 `heartbeat_at` 早于 now-60s 的行改为 `interrupted`。**不要**自动重新执行。
- 把循环体抽成 `worker_loop_once(session_factory) -> str | None`（返回处理的 job id 或 None）。测试只调这个函数，不要 `sleep` 心跳。daemon thread 里 `while not stop_event: worker_loop_once(...); stop_event.wait(0.2)`。
- 领取：`SELECT` 一条 `status==pending` 按 `created_at`，原子更新为 `running`（`BEGIN IMMEDIATE`）。
- 每 5s 写 `heartbeat_at`。
- 正常结束：`succeeded` 或 `failed`，写 `finished_at`。
- 关闭：`lifecycle` 设 `stop_event`；worker 不再领取；当前 **只读** 任务可在当前步骤后退出；不要 `kill` 线程里的 zclaw。
- `job_type` 不在 registry → 立即 `failed`，`error_code=unknown-job-type`。

本计划允许的 `job_type` 仅 `environment_check`。其它类型插入后应失败而不是瞎跑。

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_jobs.JobWorkerTests.test_stale_running_becomes_interrupted -q` → `OK`（类名/方法名可微调，但本步结束时必须有这一断言且能单独跑。）

### Step 2: 店铺锁与去重

`assistant/jobs/locks.py`：内存 + DB 均可，但必须满足：

- 同一 `store_id` 不能同时 `running` 两个需要紫鸟的 job（本计划 environment_check 算需要紫鸟）
- `store_id` 为空的 environment_check 全局互斥（仍占那一条 worker）
- POST 创建：若已有同 `job_type`+同 `store_id` 且 status in `{pending, running}` → 返回已有 `job_id`，不插第二行（重复点击不堆队列）

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_jobs.JobApiTests.test_duplicate_environment_check_returns_same_job -q` → `OK`

### Step 3: 进度事件与 SSE

`assistant/jobs/progress.py`：`append_event(job_id, *, level, event_type, message, payload_summary=None)` 写入 `job_events.sequence` 单调递增。

SSE：`GET /api/jobs/{job_id}/stream?after=N`

- 需要 session
- `text/event-stream`
- 从 `sequence > after` 读库，推送后等待新行（测试可用短 timeout + 插入事件）
- event 名：`job.started` `job.progress` `job.warning` `job.completed` `job.failed`
- 每条 SSE data 为 JSON，含 `sequence`
- 不要在 SSE 里放 config 内容或 token

`GET /api/jobs/{job_id}` JSON 固定：

```python
{
  "id": str,
  "job_type": str,
  "status": str,
  "store_id": str | None,
  "progress_current": int,
  "progress_total": int,
  "progress_message": str,
  "error_code": str,
  "error_summary": str,
  "result_summary": str,
  "created_at": str,  # ISO
  "finished_at": str | None,
}
```

页面 `GET /jobs`、`GET /jobs/{id}`：HTMX 每 2s 轮询 `GET /api/jobs/{id}` 作为 SSE 降级。刷新后从 DB 恢复进度。

SSE 测试：用 `TestClient` 的 `with client.stream("GET", url) as response` 读到至少 2 个 `data:` 行后 close；或不用真长连：直接调内部 `list_events(job_id, after=1)` 断言只返回 sequence>1 的行（优先这条，更稳）。

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_jobs -q` 含 `test_list_events_after_sequence` → `OK`

### Step 4: environment_check handler

`assistant/jobs/handlers/environment_check.py`：

只读。步骤写 job_events：

1. Python 版本
2. `resolve_ziniao_cli_command()` 成功/失败（失败则 job failed，中文摘要）
3. `list_running_stores()`：0 家或多家 → job **succeeded**（检查完成了），`result_summary` JSON 或纯文本必须含 `running-not-unique`；1 家记录 storeId/storeName。不要标 failed（那留给 CLI 找不到）。
4. `config.toml` 是否存在（`resolve_config_path()`），不读 secret
5. `probe_store_page` 仅当 running 恰好 1 家时调用；失败记 warning，不要 close/open 店

禁止：`open_store`、`close_store`、`page extract --mode running`、任何飞书 HTTP。

`GET /api/stores`（003 已存在）若当前有 `job_type` 需要紫鸟且 `status==running` 的 job：不要再调 `list_running_stores`，返回 DB 里 `stores` 缓存 + `ok: false, error: "ziniao-busy"`。无 running ziniao job 时才允许只读 GET 调 `list_running_stores`。这是 README 里「请求线程 zclaw」的唯一例外。

POST ` /api/jobs/environment-check`（CSRF）：创建 pending job，立即返回 `{job_id}`。不要在 request 线程跑 handler。

**Verify**: mock `list_running_stores` 返回 []，`worker_loop_once` → `status=="succeeded"` 且 `result_summary` 含 `running-not-unique`；patch `lib.zclaw.open_store` 断言 `assert_not_called`。

### Step 5: 取消规则（只读）

`POST /api/jobs/{id}/cancel`：

- `pending` → `cancelled`
- `running` 且 handler 尚未进入不可逆写（本计划无写）→ 设置 cancel flag，handler 在步骤间隙检查后 `cancelled`
- 已 `succeeded/failed/interrupted` → 409

**Verify**: pending job cancel 后 worker 不再执行它。

### Step 6: 接上 lifecycle

启动：create_engine 之后 `start_worker(app.state)`。
关闭：`stop_event.set`，join 超时 5s。

不要用 `BackgroundTasks`。

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_jobs -q` → `OK`  
`.venv/bin/python -m unittest discover -s tests -q` → `OK`

## Test plan

`tests/assistant/test_jobs.py` 用临时 sqlite，只调用 `worker_loop_once`（不要 `run_once` 这个名字）。测试不要 sleep 真 5s 心跳。

用例：

- 过期 heartbeat → interrupted，不自动重跑
- 重复 POST 去重
- environment_check 0 店 / 1 店
- 未 mock 的 `open_store` 不被调用
- SSE after 序号
- CSRF 缺省 POST 403
- 未知 job_type failed
- cancel pending

Verification: `.venv/bin/python -m unittest tests.assistant.test_jobs -q` → all pass.

## Done criteria

- [ ] `.venv/bin/python -m unittest discover -s tests -q` exits 0
- [ ] `rg -n "BackgroundTasks" assistant/` 无匹配
- [ ] `rg -n "open_store|close_store" assistant/jobs/` 无匹配
- [ ] `rg -n "page extract --mode running" assistant/` 无匹配
- [ ] registry 不含 `message_send` / `feishu_update` / `approve`
- [ ] No files outside the in-scope list
- [ ] `plans/README.md` **只更新 004 那一行** Status

## STOP conditions

Stop and report back (do not improvise) if:

- 003 的 models/engine/session 对不上本计划假设（没有 jobs 表且没有 Alembic 流程）。
- 发现必须用多线程并行 zclaw 才能让测试过。
- 003 的 `jobs.id` 不是 UUID str，或 `sample_cases.store_id` 被建成紫鸟 storeId 字符串。
- 有人要求 worker 在 interrupted 后自动重试 environment_check 以外的类型。
- SQLite 在测试里锁死超过一次合理修复（检查是否开了多个 writer 进程）。

## Maintenance notes

- 005 新增 handler 必须登记 registry，默认无 handler = fail。
- 写操作 handler（未来）必须：进入平台写入后忽略 cancel；失败分类 `unknown` 不得自动重试。
- Reviewer 检查 request 线程是否仍直接调用 zclaw。
