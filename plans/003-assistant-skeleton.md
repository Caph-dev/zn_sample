# Plan 003: 本地操作台骨架可双击打开且只监听 127.0.0.1

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
> git diff --stat a23807c -- scripts/launch_sample.py scripts/lib/app_log.py scripts/lib/app_config.py scripts/lib/zclaw.py scripts/lib/zclaw_cli.py pyproject.toml assistant
> git status --short -- pyproject.toml assistant scripts/launch_assistant.py
> ```
> Do **not** use `git diff a23807c..HEAD`. If `assistant/domain/` already exists (001 landed), only append files — do not empty that package. Drift 对 `assistant/` 的比较 **排除** `assistant/domain/`。Compare Current state excerpts on mismatch.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: none（可与 001/002 并行）
- **Category**: dx
- **Planned at**: commit `a23807c`, reviewed 2026-08-22

## Why this matters

业务员现在靠 `.command`/`.bat` + 终端确认跑 SOP 1/7–9。操作台需要一个 **新的** 本地 HTTP 入口：双击后只绑 `127.0.0.1`、单实例、打开系统浏览器、显示紫鸟/店铺/配置是否就绪。本计划不跑物流、不发私信、不写飞书。现有 `0/1/2/3` 入口必须保持原样，作为应急 CLI。

## Current state

仓库 **没有** `pyproject.toml`、没有 FastAPI、没有 `assistant/` 包（001 可能会先创建空 `assistant/`；若已存在，本计划只追加文件，不要清空 domain 模块）。

测试现状：`python3 -m unittest discover -s tests -q` 必须仍全部通过（审查时 254 tests；若 discover 收集数变化，以 exit 0 为准，不要为凑数字去删/加无关测试）。测试用 `sys.path.insert(..., PROJECT_ROOT / "scripts")`。assistant 测试还要 `sys.path.insert(..., PROJECT_ROOT)`。`tests/assistant/` 的 `Path.parents` 是 `[2]`（文件在 `tests/assistant/`），不是 `tests/test_*.py` 的 `[1]`。

启动现状：`0-打开店铺.command` → `scripts/run_launcher.sh prepare` → `scripts/launch_sample.py`。不要改这条链。

日志惯例（`scripts/lib/app_log.py`）：

```python
def configure_logging(...):
    """终端只打 %(message)s。库代码不要调用 configure_*。"""
```

配置（`scripts/lib/app_config.py`）：仓库根 `config.toml` gitignore；密钥不得进 SQLite/页面/日志。

店铺列表（`scripts/lib/zclaw.py` 199–208 行）：

```python
def list_running_stores() -> list[dict]:
    outer = zclaw_invoke("extract_data", {"mode": "running"})
```

禁止改成 `ziniao-cli page extract --mode running`。诊断页调用 `list_running_stores()`，**不要**调用 `resolve_store_id(..., default_store_id=...)`——操作台在 running≠1 时显示错误，不落到 1 号店 `27437742526069` 或 2 号店。

`.gitignore` 已包含 `.venv/`、`config.toml`、`exports/`。

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| 创建 venv | `python3 -m venv .venv && .venv/bin/python -m pip install -U pip` | exit 0 |
| 安装依赖 | `.venv/bin/pip install fastapi 'uvicorn[standard]' jinja2 sqlalchemy alembic python-multipart itsdangerous httpx` | exit 0 |
| 现有测试 | `.venv/bin/python -m unittest discover -s tests -q` | `OK` |
| 本计划测试 | `.venv/bin/python -m unittest discover -s tests/assistant -q` | `OK` |
| 绑定检查 | 见 Step 6 | 进程监听 `127.0.0.1`，无 `0.0.0.0` |

若本机已有 `.venv`，复用，不要另装全局包。

## Scope

**In scope**:

- `pyproject.toml`（create）
- `assistant/__init__.py`（若不存在则 create 空文件；已存在则 keep）
- `tests/assistant/__init__.py`（若不存在则 create 空文件）
- `assistant/database/migrations/env.py`（Alembic）
- `assistant/database/alembic.ini` 或 `alembic.ini` 但 `script_location = assistant/database/migrations`（不要新建仓库根 `migrations/`）
- `assistant/app.py`
- `assistant/bootstrap.py`
- `assistant/settings.py`
- `assistant/lifecycle.py`
- `assistant/paths.py`
- `assistant/api/__init__.py`
- `assistant/api/health.py`
- `assistant/api/stores.py`
- `assistant/web/__init__.py`
- `assistant/web/routes.py`
- `assistant/web/templates/base.html`
- `assistant/web/templates/home.html`
- `assistant/web/templates/diagnostics.html`
- `assistant/web/static/app.css`
- `assistant/web/static/htmx.min.js`（官方发行版文件，不走 CDN）
- `assistant/database/__init__.py`
- `assistant/database/engine.py`
- `assistant/database/models.py`
- `assistant/database/migrations/`（Alembic 初版）
- `assistant/security/local_session.py`
- `assistant/security/csrf.py`
- `assistant/security/secret_redaction.py`
- `scripts/launch_assistant.py`
- `tests/assistant/test_app_skeleton.py`
- `tests/assistant/test_security.py`
- `tests/assistant/test_paths.py`
- `plans/README.md` status 行

**Out of scope**:

- `0-打开店铺.command` 等四个业务员入口、`scripts/launch_sample.py`、`operator_launch.py`
- PyInstaller、`.app`、Windows 安装器
- jobs worker / SSE（计划 004）
- 物流同步、待办、私信、飞书写入
- 改 `scripts/lib/zclaw.py` 的 open/close/visit
- 把数据库放到仓库目录或 `exports/`

## Git workflow

- Branch: `advisor/003-assistant-skeleton`
- Commit message example: `Add a local FastAPI assistant skeleton bound to localhost.`
- Do NOT commit `.venv/`、`config.toml`、HTMX 以外的下载缓存。
- Do NOT push unless asked.

## Steps

### Step 1: pyproject.toml 与 venv

Create `pyproject.toml`：

- `[project] name = "zn-sample"`，`requires-python = ">=3.11"`
- `dependencies`：写入 **pip 实际安装的版本**（`pip freeze` 过滤 fastapi/uvicorn/jinja2/sqlalchemy/alembic/python-multipart/itsdangerous/starlette/httpx/httptools/wsproto 等）。不要手猜版本号。
- 不要把 `scripts` 声明成 setuptools package（会搅乱 `lib` 导入）。`assistant` 用仓库根作为 import path 即可。
- 可选 `[project.optional-dependencies] dev = ["httpx"]`。

**Verify**: `.venv/bin/python -c "import fastapi, uvicorn, jinja2, sqlalchemy, alembic; print('ok')"` → `ok`

然后 `.venv/bin/python -m unittest discover -s tests -q` 仍 `OK`（新增依赖不得破坏现有 unittest）。

### Step 2: 用户数据目录

`assistant/paths.py`：

```python
APP_DIR_NAME = "ZnSampleAssistant"

def user_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path.home() / ".local" / "share" / APP_DIR_NAME
```

子目录：`assistant.sqlite3`、`config/`、`exports/`、`logs/`、`backups/`、`runtime/`。提供 `ensure_user_dirs() -> Path` 一次性 mkdir。

业务代码禁止 `Path("~/Library/...")` 手写拼接。测试用 `tempfile.mkdtemp()` + `unittest.mock.patch` 掉 `user_data_dir`，不要引入 pytest，不要写开发者真实 Application Support。

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_paths -q` → `OK`

### Step 3: SQLite + 模型 + Alembic

`assistant/database/engine.py`：

- `sqlite:///.../assistant.sqlite3`
- `connect_args={"check_same_thread": False}`
- connect 事件：`PRAGMA journal_mode=WAL`、`PRAGMA foreign_keys=ON`
- `create_engine` 用 SQLAlchemy 2.x

`assistant/settings.py`：`APP_NAME = "ZnSampleAssistant"`、`BIND_HOST = "127.0.0.1"`、`SESSION_COOKIE = "zn_assistant_session"`、默认端口 8765、从环境变量只读 `ZN_ASSISTANT_PORT`（可选）。不要读 `HOST=0.0.0.0`。

`assistant/bootstrap.py`：把 **仓库根** 和 `scripts/` 都插入 `sys.path`（根在前）、`configure_logging`、`ensure_user_dirs`、创建 engine、**生产路径必须 `alembic upgrade head`**（测试可用 `create_all`，不要把测试捷径当启动器路径）。

`scripts/launch_assistant.py` 在 import bootstrap 之前：

```python
ROOT = Path(__file__).resolve().parents[1]  # scripts/ → 仓库根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
```

然后只调用 `bootstrap.main()`。`python3 scripts/launch_assistant.py` 时 `sys.path[0]` 默认是 `scripts/`，不插仓库根则 `import assistant` 失败。不要把 assistant 声明成 setuptools package 来绕过。

`assistant/database/models.py` 一次建齐 M3 表（004 **不要改这些列名**，只许 ADD COLUMN。005 也不要改列名；`curr_status` / `is_video_creator` / `is_live_creator` / `feishu_lang` 必须出现在本计划初版，避免 005 再迁列）。SQLAlchemy 2 Mapped。无 cookie/secret 列。无「合作状态」列（M3 不缓存飞书合作状态）。

`stores`：`id` PK integer, `ziniao_store_id` str UNIQUE  # 来自 running 店 dict 的 `storeId`（不是 `store_id`）, `store_name` str  # 来自 `storeName`, `shop_id` str default "", `shop_region` str default "US", `enabled` bool default True, `last_seen_at` datetime|None, `created_at`, `updated_at`

running 店一条的实测形状（`scripts/lib/zclaw.py` `list_running_stores`）：`{"storeId": "27506607043054", "storeName": "跨境2号店", ...}`。写入 `ziniao_store_id` / `store_name`。测试 mock 必须用这两个键。

`sample_cases`：`id` PK, `store_id` FK **`stores.id`（整数 PK，不是紫鸟 storeId）**, `creator_id` str, `creator_name` str, `creator_nickname` str default "", `apply_id` str, `product_id` str, `sku_id` str default "", `resolved_sku` str default "", `sample_product_option` str default "", `main_order_id` str default "", `feishu_record_id` str default "", `curr_status` int default 0  # 30=已发货 40=处理中；D+10 只看 40, `is_video_creator` str default ""  # 筛查导出「视频达人」=「是」或空, `is_live_creator` str default ""  # 同上「直播达人」, `creator_type` str default ""  # video|live|empty（人工确认后才写）, `language` str default ""  # en|es|empty（人工覆盖）, `feishu_lang` str default ""  # 英语|西班牙语|空（只读缓存）, `current_state` str default "", `first_seen_at`, `last_seen_at`, `created_at`, `updated_at`；UNIQUE `(store_id, creator_id, product_id, apply_id)`

005 upsert 时先按 `stores.ziniao_store_id` 找到 `stores.id`，再写 `sample_cases.store_id`。禁止把紫鸟 `storeId` 字符串塞进这个 FK。

`shipments`：`id` PK, `sample_case_id` FK UNIQUE（一案当前一行）, `tracking_number` str, `tracking_display` str, `carrier` str, `status_code` str, `status_label` str, `status_category` str, `estimated_delivery_at` datetime|None, `delivered_at` datetime|None, `last_event_at` datetime|None, `last_event_text` str, `package_count` int default 0, `source` str, `last_checked_at`, `last_error` str, `needs_delivery_time_confirmation` bool default False, `created_at`, `updated_at`

`shipment_snapshots`：`id` PK, `shipment_id` FK, `checked_at`, `status_code`, `status_label`, `status_category`, `estimated_delivery_at`, `delivered_at`, `last_event_at`, `last_event_text`, `raw_payload_hash`, `source`, `error`

`followup_tasks`：`id` PK, `sample_case_id` FK, `stage` str, `scheduled_for` date, `status` str, `language` str, `creator_type` str, `template_key` str, `template_version` int, `message_preview` str, `attachment_key` str, `requires_manual_confirmation` bool, `suppressed_reason` str, `sent_at`, `send_result` str, `send_confirmation` str, `last_error` str, `created_at`, `updated_at`；UNIQUE `(sample_case_id, stage, scheduled_for)`

`jobs`：`id` PK **只选 UUID str**（不要 integer）, `job_type` str, `store_id` str nullable  # 紫鸟 `storeId` 原文，不是 `stores.id`, `status` str, `progress_current` int default 0, `progress_total` int default 0, `progress_message` str, `requested_by` str, `created_at`, `started_at`, `heartbeat_at`, `finished_at`, `error_code` str, `error_summary` str, `result_summary` str, `log_path` str

`job_events`：`id` PK, `job_id` FK, `sequence` int, `level` str, `event_type` str, `message` str, `payload_summary` str, `created_at`；UNIQUE `(job_id, sequence)`

`app_settings`：`key` str PK, `value` str。写入函数 `set_setting(key, value)`：若 key 正则 `(?i)secret|token|password|cookie` 则抛 `ValueError`。不要建单独 repositories 包。

时间列用 timezone-aware datetime。不要把 `app_secret` 映射成列。

Alembic：`assistant/database/migrations/`，`script_location` 指向该目录。初始 revision 与 models 一致。测试用临时 sqlite 文件 `create_all` 或跑 `alembic upgrade head`。

备份函数 `backup_sqlite(engine, dest: Path)` 必须用 SQLite backup API（`sqlite3.Connection.backup`），禁止 `shutil.copy` WAL 文件。升级前调用一次。

**Verify**: `.venv/bin/python -c "from assistant.database.models import Base; print(sorted(Base.metadata.tables))"` → 打印包含 `stores` `jobs` `followup_tasks`

### Step 4: 本机会话、CSRF、密钥脱敏

`assistant/security/secret_redaction.py`：`redact_text(s)` 把 `app_secret`、`access_token`、`cookie`、`tenant_access_token` 的值替换为 `[redacted]`（简单正则即可）。页面渲染和日志都走它。

`assistant/security/local_session.py`：

- 启动时生成 32 字节 urlsafe `bootstrap_token`，写入 `runtime/bootstrap_token`（权限 0600）
- `GET /bootstrap?token=` 校验后设置 `HttpOnly; SameSite=Strict; Path=/` 的 session cookie（`itsdangerous` TimestampSigner，密钥仅内存 + runtime 文件，不进 SQLite）
- 校验成功立即删除 bootstrap 文件并使 token 失效；302 到 `/`（URL 不再含 token）
- 无 session 访问 `/` 或 `/api/*`（除 `/api/health` 的 liveness 若需要——**不要**把店铺信息放进无 session 的 health）返回 401/302 bootstrap 失败页
- cookie 名必须是 `zn_assistant_session`（与 `assistant.settings.SESSION_COOKIE` 相同）

`assistant/security/csrf.py`：

- session 内放 csrf token
- 所有 POST 必须带 `X-CSRF-Token` 或表单字段 `csrf_token`
- 校验 `Origin` 为 `http://127.0.0.1:<port>` 或 `http://localhost:<port>`；缺 Origin 的非 GET 拒绝
- Host 必须是 `127.0.0.1` 或 `localhost`（可带端口）
- 不启用 CORS middleware

**Verify**: `.venv/bin/python -m unittest tests.assistant.test_security -q` → `OK`

测试用 FastAPI `TestClient`：错误 token 不能建 session；二次使用同一 bootstrap token 失败；POST 无 CSRF → 403；`Origin: http://evil.example` → 403。

### Step 5: FastAPI 应用、页面、诊断

`assistant/app.py` 创建 app：`redirect_slashes=False` 随意。挂 session/csrf 中间件。入口调用 `lib.app_log.configure_logging`（先把 `scripts/` 加到 `sys.path`，与现脚本相同）。

页面（Jinja2，中文）：

- `GET /` 首页：应用版本（`importlib.metadata.version` 失败则写 `dev`）、数据目录、DB 是否打开、running 店铺摘要、占位「待办尚未生成」
- `GET /diagnostics`：Python 版本、`ziniao-cli` 是否能 `resolve_ziniao_cli_command()`（catch RuntimeError 显示中文 `CLI_NOT_FOUND`）、Bridge 探活不要用 page extract；`list_running_stores()` 的店名与 storeId；`config.toml` 是否存在（**不要**读出 secret）；飞书只显示「已配置 / 未配置」布尔
- `GET /api/health`：`{"ok": true, "app": "ZnSampleAssistant", "version": "..."}` — 无密钥、无 cookie
- `GET /api/stores`：running 列表；0 家或多家时 `ok: false, error: "running-not-unique"`，不要挑默认店

HTMX：下载 https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js 的内容保存为 **仓库内** `assistant/web/static/htmx.min.js`（提交该文件，页面只引用 `/static/htmx.min.js`）。禁止模板里出现 `unpkg.com` / CDN URL。文件头注释写 `htmx 2.0.4`。

模板不得输出 `app_secret`、`config.toml` 原文。

**Verify**: 见 Step 7 的 TestClient 测试。

### Step 6: 启动器、单实例、只绑 127.0.0.1

`assistant/lifecycle.py` + `scripts/launch_assistant.py`：

1. `ensure_user_dirs()`
2. 获取 `runtime/instance.lock`：POSIX 用 `fcntl.flock`；Windows 用独占打开锁文件。锁已被本应用持有则读取 `runtime/state.json` 的 `port`，请求 `http://127.0.0.1:{port}/api/health`，若 `app==ZnSampleAssistant` 则 `webbrowser.open` 已有 URL 并 exit 0。若端口有人听但 health 对不上，**不要**认为是本应用，exit 2。
3. 选端口：从 8765 起找空闲端口，bind `127.0.0.1`。
4. 写 `state.json`：`{port, pid, started_at}`
5. 生成 bootstrap token，打开 `http://127.0.0.1:{port}/bootstrap?token=...`
6. `uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")` — host 写死，不要读 CLI `--host 0.0.0.0`
7. 退出时释放锁、删 bootstrap token 文件

`scripts/launch_assistant.py` 是入口：`configure_logging` 后调 lifecycle。不要改 `launch_sample.py`。

**Verify（绑定）**：

```bash
.venv/bin/python -c "from assistant.settings import BIND_HOST; assert BIND_HOST == '127.0.0.1'"
```

再写测试：mock `uvicorn.run`，断言 kwargs `host=="127.0.0.1"`。`BIND_HOST` 是唯一绑定来源，不要另造 `bind_host()` 除非它只是返回该常量。

不要在执行器机器上长时间挂起真实服务器。若手动冒烟：另开终端跑启动器，用 `lsof -nP -iTCP -sTCP:LISTEN | grep 8765` 确认是 `127.0.0.1`，然后 Ctrl+C。不要把这次手动过程当作唯一验收。

### Step 7: 测试

`tests/assistant/test_app_skeleton.py`（TestClient + tempfile DB）：

- `/api/health` 200 且无 `secret` 子串
- 无 session 访问 `/` → 不是业务首页（401 或引导页）
- 正确 bootstrap → `/` 200 且 HTML 含「ZnSampleAssistant」或「操作台」
- `/api/stores` 在 mock `list_running_stores` 返回 2 家时 `ok is False`
- mock 1 家时返回该 storeId，且不是硬编码 2 号店 `27506607043054`
- 页面 HTML 经 `redact` 后不含 `app_secret`
- uvicorn host 断言

**Verify**: `.venv/bin/python -m unittest discover -s tests/assistant -q` → `OK`  
然后 `.venv/bin/python -m unittest discover -s tests -q` → `OK`

## Test plan

- 新测试全部 unittest + FastAPI TestClient，模仿 `tests/test_operator_launch.py` 的 mock 风格。
- 覆盖：绑定地址、单实例 health 识别、bootstrap 单次有效、CSRF/Origin、running 非唯一、密钥不出现在 body。
- 不要测 Jinja 美工。

Verification: `.venv/bin/python -m unittest discover -s tests -q` → all pass including new tests.

## Done criteria

- [ ] `pyproject.toml` 存在且 dependency 版本来自 pip freeze，不是手猜
- [ ] `.venv/bin/python -m unittest discover -s tests -q` exits 0
- [ ] `rg -n "0\\.0\\.0\\.0" assistant/ scripts/launch_assistant.py` 无应用监听用途（注释里提禁止可以）
- [ ] `rg -n "page extract --mode running" assistant/` 无匹配
- [ ] `rg -n "unpkg.com|cdn.jsdelivr" assistant/web/templates assistant/web/routes.py` 无匹配（允许执行器曾经从 unpkg **下载** 静态文件，但模板不得引用 CDN）
- [ ] models 无 cookie/secret 列：`rg -n "cookie|app_secret|access_token" assistant/database/models.py` 无匹配
- [ ] `rg -n "curr_status|is_video_creator|is_live_creator|feishu_lang" assistant/database/models.py` 四个名字都出现
- [ ] 四个 `.command`/`.bat` 与 `scripts/launch_sample.py` 未改
- [ ] `plans/README.md` **只更新 003 那一行** Status，不要重写整张表
- [ ] `rg -n "parents\\[1\\]|sys.path.insert" scripts/launch_assistant.py` 显示把仓库根插入 `sys.path`（在 import `assistant` 之前）

## STOP conditions

Stop and report back (do not improvise) if:

- 现有 unittest 在安装 FastAPI 后失败。
- 001 已创建 `assistant/domain/` 且本计划的目录规划 **似乎必须覆盖** 那些文件——STOP，不要清空 domain；只追加本计划文件。
- 不加仓库根到 `sys.path` 就无法 `import assistant`——不要改成把包挪进 `scripts/assistant/`。
- Windows 锁文件实现需要额外原生依赖（不要加 `portalocker` 除非测试证明 fcntl 路径在当前任务 OS 上不存在且本机是 Windows）。
- 有人要求诊断页在无 running 店时自动 `open_store`。

## Maintenance notes

- 以后加写接口必须走 CSRF + session，不要只靠「按钮在本机页面上」。
- 用户数据在 Application Support，升级/卸载不得删库（本计划也不做卸载器）。
- 004 会在同一进程加 worker 线程：engine 已 `check_same_thread=False` + WAL，不要改回默认。
- Reviewer 检查：health 是否泄漏 running 店以外的配置内容；bootstrap token 是否进 access log。
- `sample_cases.curr_status` 是跟进池过滤键；不要用「已完成」的猜测 tab 填这个列。
