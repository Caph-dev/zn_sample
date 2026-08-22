# Plan 005: 只读物流同步、今日待办、话术预览与 CSV 导出

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
> git diff --stat a23807c -- assistant scripts/lib/order_api.py scripts/lib/sample_api.py scripts/sync_shipped_tracking.py scripts/lib/filters.py scripts/lib/feishu_bitable.py AGENTS.md README.md
> git status --short -- assistant scripts/lib/sample_api.py scripts/lib/filters.py
> python3 -c "from assistant.domain.shipment_status import normalize_shipment_status; from lib.order_api import parse_logistics_details; from lib.filters import ACTIVE_HERO_PRODUCT_ID; print(ACTIVE_HERO_PRODUCT_ID)"
> ```
> If 001–004 files/imports are missing, STOP. Do **not** use `git diff a23807c..HEAD`. Compare Current state excerpts on mismatch.

## Status

- **Priority**: P1
- **Effort**: L
- **Risk**: MED
- **Depends on**: plans/001-followup-domain.md, plans/002-logistics-status-parser.md, plans/003-assistant-skeleton.md, plans/004-jobs-worker-sse.md
- **Category**: direction
- **Planned at**: commit `a23807c`, reviewed 2026-08-22

## Why this matters

这是原规格自己划的第一版上线点（M3）：业务员能同步免费样品的物流、看到是否送达、生成跟进待办、复制话术、导出处理中超过 10 天名单。价值来自 SOP 2（到货跟进），不是 SOP 7–9（16:00 后写飞书快递单号并发物流私信）。现有 `sync_shipped_tracking.py` 不能当 handler 子进程调用：它默认带 16:00 门，且代码路径会写飞书/发私信。本计划用列表 API + `parse_logistics_details` 只读入库，再用 001 的纯函数生成待办。

## Current state

已发货列表（`scripts/lib/sample_api.py`）：

- `PENDING_TAB = 10`、`READY_TO_SHIP_TAB = 20`、`SHIPPED_TAB = 30`
- **没有** `PROCESSING_TAB`。处理中实测 `tab=40` / `curr_status=40`。
- `scrape_shipped_list_api` 内部：`scrape_pending_list_api(..., tab=SHIPPED_TAB, ensure_page=False)`。
- `scrape_pending_list_api` 默认 `ensure_page=True` → `assert_on_pending_list` **会点「待审核」tab**。扫 tab=40 必须 `ensure_page=False`。
- 行字段含 `apply_id`, `product_id`, `sku_id`, `main_order_id`, `fulfill_unit_ids`, `creator_id`, `creator_name`, `nick_name`, `curr_status`。**没有**「视频达人/直播达人」列，也没有简介。

物流 GET（`scripts/lib/order_api.py` + `page_api.py`）：

- endpoint `/api/v1/fulfillment/na/logistic_detail/list`
- 002 应提供 `parse_logistics_details` / `fetch_tiktok_logistics_details_api`
- 2026-08-21 2 号店（免费样品）：
  - **已发货 `tab=30`**：58/58 在途（In transit/Packed/Out for delivery），0 Delivered。
  - **处理中 `tab=40`**：有订单 49 单物流全部最新 title=`Delivered`，事件 `time` 可当 D0；仍无 `delivered_at` JSON 键。UI 角标 53 vs API total 52 vs 有订单 49——角标按申请条数、`total_count` 按达人组，**不要当验收失败**。
  - **跟进日历与 D+10 名单只扫 tab=40**。禁止扫已完成（tab=53/55 抽样里虽有旧 Delivered）。
  - **不扫买返**。买返是另一套 UI；点了买返后 `group/list` 不变。
  - ETA 仍是 `predict_delivery_time_text` 秒戳，禁止当送达。

指定跟进款（`scripts/lib/filters.py` 约 70–72 行，**不要改**）：

```python
ACTIVE_HERO_PRODUCT_ID = "1732414717062320994"
```

SOP 7–9 脚本头（`scripts/sync_shipped_tracking.py` 1–8 行）—— **不要调用它**：

```python
"""SOP 第 7–9 步：已发货 → TikTok 物流单号 → 回写飞书 → 可选发私信。
北京时间 16:00 前拒绝跑，测试加 --force。
"""
```

语言只读调用（以函数签名为准，不要抄 `sync_shipped_tracking.py` 478–492 的「已有 `_feishu_record`」顺序）：

- `lib.feishu_bitable.find_duplicate_record`（约 179 行）：`creator_handle` + `sample_product`
- `lib.feishu_bitable._field_plain(record["fields"].get("使用语言"))`
- 值为「英语」「西班牙语」才用。M3 **不刷详情简介**；无飞书语言则 `en` + needs_review。

飞书写函数（禁止 import 到 handler）：`update_record_fields`、`create_creator_relation_record`、`update_record_order_no`。允许只读：`find_duplicate_record`、`search_relation_records`、`get_record_fields`、`_field_plain`、`list_sample_product_options`、`match_sample_product_option`。红人ID = `creator_name`。

寄样产品键（列表 API **没有** `resolved_sku` / `sample_product_option`）：

1. 若 `sample_cases` 上已有非空 `sample_product_option`（筛查导出灌入）→ 用它。
2. 否则只读 `list_sample_product_options`，再 `match_sample_product_option("B005", options)`（该函数支持前缀，见 `feishu_bitable.py` 约 146–165 行）。命中则写入 `sample_cases.sample_product_option`，货号写入 `resolved_sku="B005"`。
3. 匹配失败或无凭证 → **不要**用空字符串调 `find_duplicate_record`；跳过飞书语言，needs_review。
4. M3 **不要**为配图或语言去打主推 wiki（`load_hero_from_feishu`）。指定 B005 用固定货号字符串 `"B005"` 对选项做前缀匹配即可。

001 应提供：`latest_due_unpublished_stage`、`plan_followup_mutation`、`choose_template_key`、`render_followup_message`、`resolve_followup_attachment(*, product_id, resolved_sku=None)`、`FOLLOWUP_IMAGE_PRODUCT_ID`、`FEISHU_UNFULFILLED_COOPERATION_STATUS`、语言/类型 policy。

003 应已有 `sample_cases.curr_status` / `is_video_creator` / `is_live_creator` / `feishu_lang`。若缺这些列：本计划 **只许 ADD COLUMN**，不要改名。

004 应提供：单 worker、registry、SSE、CSRF POST 创建 job。

筛查导出「视频达人/直播达人」=「是」或空，可同时为是；**≠** 该侧已过 SOP。类型选择必须走 001 `policies.py`，禁止 GPM 兜底、禁止 `derive_creator_type_flags`。

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| assistant 测试 | `.venv/bin/python -m unittest discover -s tests/assistant -q` | `OK` |
| 全量 | `.venv/bin/python -m unittest discover -s tests -q` | `OK` |
| 确认未改 SOP7 门闩 | `rg -n "_before_four_pm_beijing" scripts/sync_shipped_tracking.py assistant/` | 仅 scripts 侧命中，assistant 无此门 |

## Scope

**In scope**:

- `scripts/lib/sample_api.py`（**仅**追加 `PROCESSING_TAB = 40` 与 `scrape_processing_list_api`：内部 `scrape_pending_list_api(..., tab=PROCESSING_TAB, ensure_page=False)`。禁止改 pending/shipped 默认行为。）
- `tests/test_sample_api.py`（只为上面包装加用例：请求 body `tab==40`；`assert_on_pending_list` 不被调用）
- `assistant/services/store_service.py`
- `assistant/services/shipment_service.py`
- `assistant/services/followup_service.py`
- `assistant/services/export_service.py`
- `assistant/jobs/handlers/shipment_sync.py`
- `assistant/jobs/handlers/followup_generate.py`
- `assistant/jobs/handlers/report_export.py`
- `assistant/jobs/registry.py`（登记上述三个 + 已有 environment_check）
- `assistant/api/dashboard.py`
- `assistant/api/shipments.py`
- `assistant/api/followups.py`
- `assistant/api/exports.py`
- `assistant/web/routes.py` + `assistant/web/templates/home.html`（改 003 首页，不要另起 dashboard.html）+ `shipments.html` + `shipment_detail.html` + `followups.html` + `followup_detail.html` + `reports.html`
- `assistant/domain/followup_stage.py`（**仅**追加 stage 常量 `confirm_delivery_time` 与 generate 分支；不要改 D0 日历公式）
- `assistant/web/static/sop-images/2-查看到货+达人跟进-b05.png`（只拷这一张；不要拷 B006-A）
- `README.md`、`AGENTS.md`（只加操作台入口与「默认只读 / 不发私信」说明，不改 SOP 1 阈值）
- `scripts/launch_assistant.py` 若需从 README 引用（已存在则不改行为）
- `tests/assistant/test_shipment_sync.py`
- `tests/assistant/test_followup_generate.py`
- `tests/assistant/test_exports.py`
- `tests/assistant/test_dashboard.py`
- `plans/README.md` status 行

**Out of scope**:

- `message_send`、`prepare-send`、`confirm-send`
- 飞书 **写入**（合作状态「未发布」「已完成」、新建达人行、快递单号、订单号）。只读「使用语言」除外。
- `content_evidence` CRUD、自动扫 TikTok.com
- 批准 / 拒绝 / 邀请
- 调用 `sync_shipped_tracking.py` / `send_sample_intro.py` / `screen_sample_requests.py` 子进程
- 16:00 门（不要加到 shipment_sync）
- 切店、`open_store`、`close_store`、`page extract --mode running`
- 自动打开达人详情刷简介
- 买返样品、已完成 tab、待审核 tab
- PyInstaller
- 改四个 `.command` 的默认行为（可以另加第五个 `.command` 启动操作台，可选；不要改 0/1/2/3）

## Git workflow

- Branch: `advisor/005-readonly-followup-desk`
- Commit message example: `Add read-only shipment sync and follow-up preview to the assistant.`
- Do NOT push unless asked.

## Steps

### Step 1: 处理中列表包装 + Service 层，禁止子进程

在 `scripts/lib/sample_api.py` 追加（放在 `SHIPPED_TAB` 旁）：

```python
PROCESSING_TAB = 40  # 免费样品【处理中】= 已到货。D+10 只出这个 tab。

def scrape_processing_list_api(store_id, **kwargs):
    kwargs.setdefault("ensure_page", False)
    kwargs["tab"] = PROCESSING_TAB
    kwargs["ensure_page"] = False  # 再写死一次：禁止落到默认 True
    return scrape_pending_list_api(store_id, **kwargs)
```

不要改 `assert_on_pending_list`。不要在包装里点任何 tab。

`StoreService.resolve_unique_running_store()`：`list_running_stores()`，len!=1 则返回 error 结构，不抛到 500，不使用 `default_store_id`。

`ShipmentService.synchronize_shipments(store_id, *, job_progress)`：

1. 拉两份免费样品列表：
   - `scrape_shipped_list_api`（tab=30，在途展示）
   - `scrape_processing_list_api`（tab=40，跟进主数据）
   不要拉 tab=10/20，不要点买返，不要猜已完成 tab。
2. 跟进待办 **只保留** `product_id == lib.filters.ACTIVE_HERO_PRODUCT_ID`（from import，不要抄数字）。其它货号可以 upsert 物流行，但 `FollowupService.generate` 必须跳过。日后扩品：允许集合改成空或配置项（与 `--all-hero-products` 同语义），不要写死删除分支。
3. 每行先 upsert `stores`（`ziniao_store_id` = 紫鸟 `storeId`），再用 `stores.id` 写 `sample_cases.store_id`。UNIQUE 键 `(stores.id, creator_id, product_id, apply_id)`。禁止把紫鸟 `storeId` 字符串塞进 `sample_cases.store_id`。写入 `curr_status`：来自哪次列表就写 30 或 40（同一 apply 若两份都有，以 **40 覆盖 30**）。缺 `apply_id` 或 `creator_id` 或 `product_id`：记 job warning，跳过，不要用达人名当键。
4. `main_order_id` 空或 `0` 或不是 15–20 位：跳过物流详情，shipment.status_category=`unknown`。
5. 对有效 `main_order_id` 调 `fetch_tiktok_logistics_details_api`（测试 mock）。写入 `shipments` 当前聚合 + 追加 `shipment_snapshots`（只存 002 返回的筛选字段和 hash，不存 raw payload）。
6. 识别变化：status_category 或 tracking_no 或 delivered_at 变化才算变化。
7. **全程不写飞书、不发 IM、不 import `update_record_fields` / `fill_or_send_message` / `send_message_via_sdk` / `create_creator_relation_record`。**
8. 不要调用 `_before_four_pm_beijing`。
9. 导航：物流 GET 的订单页跳转留在 `fetch_tiktok_logistics_details_api`（002）内部。每处理完 **一个** `main_order_id` 后，**只**调用 `lib.sample_navigation.navigate_to_sample_request(store_id)` 回到样品申请，再读下一单。禁止 `shipped_dom.ensure_on_sample_page`（可能点已发货 tab）。异步 `location.replace` + 短轮询已封装在该函数内；禁止阻塞 `visit_page`。不要改 `sample_navigation.py`。测试 mock `navigate_to_sample_request`。
10. DOM 回退：**仅 tab=30** 在 API 抛 `PageApiSchemaError` 时复用已有 `scrape_shipped_list`（仍只读）。**tab=40 API 失败 → job warning 或 failed，跳过处理中列表，禁止调用 `scrape_shipped_list`**（那是已发货 DOM，会把在途当跟进主数据）。不要新写 tab=40 刮取器，不要点同意。

**Verify**: 单测 mock 列表 2 行 + 详情 1 行 delivered_at 缺失，断言 snapshots 行数 ≥1，并且：

```python
@patch("lib.feishu_bitable.update_record_fields")
@patch("lib.feishu_bitable.create_creator_relation_record")
@patch("lib.im_api.send_message_via_sdk")
@patch("lib.sample_dom.assert_on_pending_list")
```

前三个 `assert_not_called`。处理中路径上 `assert_on_pending_list` 也 `assert_not_called`。不要 import 真实发送函数到 handler 模块。

### Step 2: 待办生成

`FollowupService.generate(now=None)`：DB + 可选只读飞书语言 + 001 函数。

对每个 `sample_cases` 联 shipments：

- **跳过** `product_id != ACTIVE_HERO_PRODUCT_ID`（允许集合空时除外；M3 默认不空）。
- **D+10 / unfulfilled 只对 `curr_status == 40`**。`curr_status == 30` 即使物流已 Delivered（实测不应出现）也只允许 `confirm_delivery_time`，不进 D+10 名单。不根据「已完成」猜测值放行。
- `needs_delivery_time_confirmation` 或 category==delivered 但无 delivered_at：upsert **一条** `stage="confirm_delivery_time"`。**不要**把该 stage 传入 `plan_followup_mutation`。UNIQUE 含 `scheduled_for`，跨日若用「今天」会再插一行——禁止。查任意 `scheduled_for` 的已有 `confirm_delivery_time` 行并更新；没有才 insert，`scheduled_for` 固定为 `date(1970, 1, 1)`（哨兵，保证每案一行）。页面不要显示 1970，显示「待确认送达日」。`status="needs_review"`，`requires_manual_confirmation=True`。有了可靠 `delivered_at` 后把该行 `suppress`（reason=`delivery_time_known`）。不要用 ETA 生成 D0，也不要为此建 `arrival`。
- 有可靠 `delivered_at` 且 `curr_status==40`：`days_since_delivery` + `plan_followup_mutation` upsert。首页只展示每案最新 unpublished（`status in pending,ready,needs_review`）。
- `days == 10..14`：`day_10_list`（名单，无话术）。`days >= 15`：`unfulfilled`（飞书目标 `FEISHU_UNFULFILLED_COOPERATION_STATUS`「未发布」；**M3 不写飞书**）。无 D+5、无隔天循环。
- 语言：`choose_followup_language(bio=None, feishu_lang=sample_cases.feishu_lang or 本次只读结果, manual_lang=sample_cases.language or None)`。
  - 本次只读：config 有飞书凭证时 `find_duplicate_record(token, creator_handle=creator_name, sample_product=resolved_sku or sample_product_option)`；`_field_plain(fields.get("使用语言"))`。无凭证、无匹配、HTTP 失败 → 记 job warning，不 fail 整 job，不当成写失败。
  - 无人工语言且无飞书语言：`en` + `requires_manual_confirmation=True`。不要为语言批量打开详情。
- 类型：`choose_followup_creator_type(manual_type=sample_cases.creator_type or None, is_video_creator=sample_cases.is_video_creator, is_live_creator=sample_cases.is_live_creator)`。两列都「是」或都空 → `needs_review`。不要读详情页、不要用 GPM、不要调用 `derive_creator_type_flags`。
- 模板：`is_hero_sku=resolve_followup_attachment(...)["matched"]`，再 `choose_template_key(..., is_hero_sku=is_hero_sku)` + `render_followup_message`；None 则 `needs_review`，仍建任务（`day_10_list` / `unfulfilled` 允许无模板）。
- 图片：`resolve_followup_attachment(product_id=sample_cases.product_id, resolved_sku=sample_cases.resolved_sku)`。只认 product_id。`followup_tasks.attachment_key` **存 001 返回的 repo-relative `path`**（`FOLLOWUP_IMAGE_PATH`），不要改 001 常量。页面 `<img src="/static/sop-images/2-查看到货+达人跟进-b05.png">`：若 `Path(attachment_key).name` 等于该文件名则用 static URL。禁止把仓库绝对路径或 `file://` 写进 HTML。Step 4 拷贝的 static 文件只为 HTTP 服务。
- 筛查导出自动灌列（**要写代码，运行时文件可缺**）：`followup_generate` 开头 `try: path = lib.export_util.latest_screen_export()` except `FileNotFoundError`：跳过。有文件则读 json/csv，按 `apply_id+product_id` 匹配本地 `sample_cases`，仅当本地对应列为空时拷贝 `is_video_creator` / `is_live_creator` / `resolved_sku` / `sample_product_option` / `feishu_record_id`。列名对齐 `scripts/lib/export_util.py` 的 `EXPORT_FIELDS` 与 `CN_HEADERS`（「视频达人」「直播达人」）。**禁止**调用 `derive_creator_type_flags`，禁止读 GPM 列来填类型。无导出则类型保持空 → 人工。不要做上传 UI。

**Verify**: 固定 `delivered_at` 为北京时间今天-6 天、`curr_status=40`、`product_id=ACTIVE_HERO_PRODUCT_ID`，generate 后只有 `day_3` pending，没有 arrival。同一数据 `curr_status=30` 时 **没有** day_3 / day_10_list。

### Step 3: Job handlers 与 API

Registry 增加：

- `POST /api/jobs/shipment-sync` → `shipment_sync`
- `POST /api/jobs/followup-generate` → `followup_generate`（可只读飞书语言；不打紫鸟；仍走 worker，避免与 sync 并行写 SQLite）
- `POST /api/jobs/report-export` → JSON body `{"kind": "today"|"day_10_list"|"logistics_exception"|"needs_review"}`。`kind=overdue_10` 可作为 `day_10_list` 别名。`kind=write_audit` 或未知 kind → HTTP 400 `{"error":"unsupported-export-kind"}`。不要导出空审计表。

所有 POST 要 CSRF + session。创建后立即返回 job_id。

`shipment_sync` 开始时若 running 店不是唯一，job failed，中文摘要「请只开一家店」，不切店。

人工确认（本地 DB，不发 IM）：

- `POST /api/followups/{id}/skip`：`status="skipped"`，`send_result="manual"`
- `POST /api/followups/{id}/set-type` body `{creator_type: "video"|"live"}` 写入 `sample_cases.creator_type` 并刷新该任务模板
- `POST /api/followups/{id}/set-lang` body `{lang: "en"|"es"}` 同理写 `sample_cases.language`

不要做「确认自动发送」。

**Verify**: TestClient POST 无 CSRF → 403；有 CSRF 后 jobs 表多一行 `shipment_sync`。

### Step 4: 页面

中文 UI，简洁错误，技术细节折叠（job id、log path、error_code）。

首页 `/` 计数（不要用旧规格的 D+5 / 循环）：

- 环境：紫鸟 CLI、running 店、上次 shipment_sync / followup_generate 时间
- 等待送达（`curr_status==30` 且非 delivered）
- 今日到货 D0、D+3、D+7
- D+10 名单（仅 `curr_status==40`）
- D+15 未发布标记（本地任务；文案写「未发布」，不要写「待发布」）
- 需要人工确认、物流异常、最近失败 job
- 不要「一键全部发送」按钮
- 飞书合作状态：**固定显示「未同步」**，不要读合作状态列

`/shipments`：列表 status_category、tracking_display、ETA、delivered_at、missing 标记。订单号与运单号分列。

`/followups`：筛选 stage/status/language/`curr_status`；默认无批量写操作。多选导出可以。

`/followups/{id}`：左侧事实（达人、产品、订单号、物流单号分开显示、送达、`curr_status`）；右侧 `<textarea readonly>` 话术预览、图片用 `/static/sop-images/...`（相对站点，不要 `file://`）、跳过/设类型/设语言按钮。不要「确认自动发送」。

`job_progress` 回调签名：`def job_progress(current: int, total: int, message: str) -> None`，内部调 `append_event(..., event_type="job.progress")`。

`/reports`：触发 export job，下载 CSV 从用户数据目录 `exports/`。

**Verify**: TestClient 登录后 GET `/followups/{id}` 200；HTML 含该任务 `message_preview` 的一段连续 12+ 字符子串，且 `'app_secret' not in html.lower()`，且不含「一键全部发送」。首页 HTML 不含「D+5」或「每隔两天」。

### Step 5: CSV 导出

`ExportService` 写到 `user_data_dir()/exports/`，UTF-8。种类：

- 今日待办
- D+10 名单（`stage==day_10_list` 且 `curr_status==40`；文件名/表头用「处理中超过 10 天」，不要写成已完成）
- 物流异常（status_category in exception,returned,lost 或 needs_delivery_time_confirmation）
- 需要人工确认
- 单次 job 结果 JSON（已有 result_summary 即可）

列至少：store_name, creator_name, creator_id, product_id, apply_id, curr_status, main_order_id, tracking_raw, delivered_at, stage, language, creator_type, template_key, reason。`main_order_id` 与 tracking 分开，避免业务员把订单号发给达人。

**Verify**: 生成 `curr_status=40` 的 day_10_list 一行后 export，CSV 含该 creator_id；`curr_status=30` 的同类 stage **不出现**。

### Step 6: 文档

`README.md` 增加一小节「操作台（只读预览）」：

```bash
python3 scripts/launch_assistant.py
```

写明：默认不同步以外的写操作；批准/介绍/物流私信仍用 0/1/2/3；操作台物流同步 **不受 16:00 限制**，也 **不会** 写飞书合作状态；可只读「使用语言」；跟进只扫免费样品处理中 + 指定 B005。

`AGENTS.md` 能力表加一行：操作台 M3 = 只读同步 + 待办；禁止从操作台批准。保持「默认禁止写操作」。不要改 SOP 1 阈值数字。

**Verify**: `rg -n "launch_assistant" README.md` 有匹配；`rg -n "16:00" README.md` 仍描述命令 3，且操作台段落写明不受该门约束。

### Step 7: 回归

`.venv/bin/python -m unittest discover -s tests -q` → `OK`

再确认：

```bash
rg -n "subprocess" assistant/jobs/handlers assistant/services
rg -n "assert_on_pending_list" assistant/
```

不应出现对 `sync_shipped_tracking.py` / `send_sample_intro.py` 的 Popen。assistant 不要调用 `assert_on_pending_list`。

## Test plan

全部 mock 紫鸟与飞书 HTTP。模仿 `tests/test_order_api.py` 与 `tests/test_sample_api.py`。

必须：

- 已发货 + 未知状态 ≠ 已送达
- 已送达无时间 → confirm_delivery_time，不生成 D+3
- D+6 且 curr_status=40 只出现 day_3
- D+11 且 curr_status=40 出现 day_10_list；curr_status=30 不出现
- 视频+直播类型（两列都「是」）→ needs_review，详情页无「一键发送」
- 仅 `product_id==ACTIVE_HERO_PRODUCT_ID` 配 B005 图；`resolved_sku="B005"` 但错误 product_id 不配图
- shipment_sync 不调用飞书写/IM mock，不调用 `assert_on_pending_list`
- 16:00 之前跑 shipment_sync 仍成功（把 now mock 成北京 10:00）
- running 两家 → job failed 且 open_store 未调用
- 导出 CSV 含 curr_status=40 的 day_10_list，不含 30
- 飞书只读语言：先 mock `list_sample_product_options` 返回含 `B005（高腰）` 之类前缀项，再 mock `find_duplicate_record` 返回「西班牙语」→ 任务 lang=es；options 为空时 **不** 用 `sample_product=""` 调用 `find_duplicate_record`
- mock 抛错 → job 仍 succeeded 且 needs_review
- 两次 generate（today 与 today+1）对同一缺 `delivered_at` 案只有 1 行 `confirm_delivery_time`
- tab=40 `PageApiSchemaError` 时 `scrape_shipped_list` 不被调用
- 现有 `tests/test_execute_safety.py` 与 `tests/test_operator_launch.py` 仍过

Verification: `.venv/bin/python -m unittest discover -s tests -q` → all pass.

## Done criteria

- [ ] `.venv/bin/python -m unittest discover -s tests -q` exits 0
- [ ] `rg -n "send_message_via_sdk|fill_or_send_message|update_record_fields|create_creator_relation_record|click_approve" assistant/` 无匹配
- [ ] `rg -n "sync_shipped_tracking|_before_four_pm_beijing" assistant/` 无匹配
- [ ] `rg -n "一键全部发送|confirm-send|prepare-send" assistant/web/` 无匹配
- [ ] `rg -n "D\\+5|每隔两天" assistant/web/` 无匹配
- [ ] `rg -n "assert_on_pending_list" assistant/` 无匹配
- [ ] jobs registry 仍无 `message_send` / `feishu_update`
- [ ] `scrape_processing_list_api` 存在且源码含 `ensure_page=False`
- [ ] 四个原 `.command`/`.bat` 未改默认 argv
- [ ] No files outside the in-scope list（README/AGENTS 与 sample_api 薄包装允许）
- [ ] `plans/README.md` **只更新 005 那一行** Status

## STOP conditions

Stop and report back (do not improvise) if:

- 001/002/003/004 任一未完成或测试失败。
- `fetch_tiktok_logistics_details_api` 不存在（002 未做）——不要自己猜 payload 字段。
- `ACTIVE_HERO_PRODUCT_ID` 不存在。
- 同步似乎必须先 `close_store`/`open_store` 才能读列表。
- 产品要求 M3 就发私信或写「已完成」/「未发布」到飞书。
- `SHIPPED_TAB` 不再是 30。处理中若不再是 `tab=40` / `curr_status=40`，STOP，不要改去扫待审核或已完成。
- 为了拿语言去批量打开达人详情（那会变成另一个长时间紫鸟任务，需另开计划）。
- `scrape_processing_list_api` 若不写 `ensure_page=False` 就无法通过测试——不要改 `assert_on_pending_list` 来绕。
- 有人要求扫买返或把 tab=53 抽样当 D+10 数据源。
- tab=40 失败后似乎只能靠 `scrape_shipped_list` 才能让测试绿——STOP，不要用已发货 DOM 冒充处理中。

## Maintenance notes

- 真出现 `track_list[0].title == "Delivered"` 时，002 会用该事件 `time` 填 `delivered_at`，日历才会转 D0。不要在 Jinja 里写死 days，也不要等一个不存在的 `delivered_at` JSON 键。
- 下一步应是内容证据（原 M4），不是把 CLI 批准搬进网页。
- 若要把 SOP 7–9 搬进操作台，必须独立 job_type，并 **保留** 16:00 门与 `--execute --yes` 等价物；不要复用 `shipment_sync`。
- 飞书写「未发布」/「已完成」是 M5；本计划只把目标状态字符串留在本地任务上。
- Reviewer 检查：列表同步是否误用待审核 tab；是否把 `main_order_id` 显示成「物流单号」；D+10 CSV 是否混入已发货/已完成；配图是否错误地用了 `resolved_sku`。
