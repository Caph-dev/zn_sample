# Plan 002: 物流详情解析失败关闭，不猜字段

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
> git diff --stat a23807c -- scripts/lib/order_api.py scripts/lib/page_api.py tests/test_order_api.py scripts/lib/tracking_parse.py assistant/domain/shipment_status.py
> git status --short -- scripts/lib/order_api.py assistant/domain/shipment_status.py
> ```
> Do **not** use `git diff a23807c..HEAD` (ignores working tree). If 001 is not importable (`from assistant.domain.shipment_status import normalize_shipment_status` fails), STOP. Compare Current state excerpts on mismatch.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: MED
- **Depends on**: plans/001-followup-domain.md（必须已能 `from assistant.domain.shipment_status import normalize_shipment_status`。001 未合入则 STOP，不要在 `order_api.py` 复制状态机。）
- **Category**: bug
- **Planned at**: commit `a23807c`, reviewed 2026-08-22

## Why this matters

操作台要区分「已发货」和「已送达」，并用实际送达日算 D0。当前 `parse_logistics_payload` 只抽运单；真实详情里 **没有** `delivered_at` / `status_name` / `estimated_delivery_at`。状态在 `track_list[].title`，ETA 在 `predict_delivery_time_text`（unix 秒的字符串）。按旧白名单去扫会永远失败关闭；按常识把 ETA 当送达会把日历算错。本计划只读 2026-08-21 在 2 号店对已发货 **58/58** + 处理中有订单 **49/49** 验证过的路径。合成 Delivered fixture 模拟的是 **处理中** payload 形状，不是已发货 tab。

## Current state

- `scripts/lib/page_api.py`：`SELLER_LOGISTICS_ENDPOINT = "/api/v1/fulfillment/na/logistic_detail/list"`（只读 GET 白名单）
- `scripts/lib/order_api.py`：`parse_logistics_payload` / `fetch_tiktok_tracking_api`
- `tests/test_order_api.py`：合成 `data.package_list[].logistics_info.tracking_number` + `provider_name`
- `scripts/lib/tracking_parse.py`：运单 vs 15–20 位订单号；`main_order_id` 不是发给达人的单号
- 仓库 **没有** 脱敏的真实 `logistic_detail` fixture；2026-08-21 对 2 号店已发货 tab **全量 58 单** 只读 GET 已核对键名（见下方「已验证路径」）

当前成功返回形状（`scripts/lib/order_api.py` 97–121 行）：

```python
return {
    "ok": True,
    "order_id": str(order_id),
    "tracking_raw": parsed.get("tracking_raw") or value,
    "tracking_no": parsed.get("tracking_no") or "",
    "via": "api-field",
}
```

当前「有 status 无运单」会失败（`tests/test_order_api.py` 160–165 行）：

```python
def test_rejects_payload_without_tracking_value(self) -> None:
    with self.assertRaisesRegex(RuntimeError, "未找到运单号"):
        parse_logistics_payload(
            {"code": 0, "data": {"package_list": [{"status": "delivered"}]}},
            order_id="order-test-001",
        )
```

`fetch_tiktok_tracking_api` 仍会先 `navigate_to_url` 到 `seller.us` 订单页再 GET。订单 URL 必须继续用 `seller.us`，不用 apex。本计划不改导航。

仓库惯例：页面 API 用异步 fetch + `request_id` 轮询（见 `page_api.py`）。改 parser 不要改传输层。测试用 unittest + mock，不打真实 TikTok。

### 已验证路径（2 号店 / `27506607043054` / shop_id `7496019476093176674` / 2026-08-21）

列表 `POST /api/v1/affiliate/sample/group/list`（页面文案 vs API tab，2 号店 2026-08-21）：

| UI | API `tab` | `curr_status` | 条数 | 物流最新 title |
|---|---|---|---|---|
| 待审核 | 10 | 10 | 636 | 无订单号 |
| 待发货 | 20 | 20 | 2 | `data={}` |
| 已发货 | 30 | 30 | 58 | In transit 43 / Packed 13 / Out for delivery 2；**0 Delivered** |
| 处理中 | 40 | 40 | API 52（有订单 49；UI 显示 53） | **49/49 Delivered** |
| 已完成（UI 无数字） | 未与单一 tab 钉死 | 55 / 53 等 | tab=55 有 184 且均有订单 | 抽 16 单 tab=55：15 Delivery canceled + 1 Return received；抽 5 单 tab=53：**5/5 Delivered**（较早日期）。**D+10 / 跟进日历不要用已完成。** 本解析器仍要能把这些 title 映到 exception/returned/delivered，供物流页展示。 |

`apply_info` **没有**运单/ETA/`delivered_at`，也没有 `fulfill_unit_ids`。处理中行 `fulfillment_status=1`，已发货行是 `0`。

详情 `GET /api/v1/fulfillment/na/logistic_detail/list?main_order_id=`（无需 `fulfill_unit_ids`）。已发货 58/58、处理中 49/49 均 `code=0` 且 `package_list` 长度 1。`parse_logistics_payload` 在已发货 58/58、处理中 49/49 抽出 `tracking_no`。

包裹顶层键（58/58 都有，**仅这些**）：

```text
main_order_id, order_line_ids, package_id, tracking_no, invoice_no,
logistic_supplier, logistic_detail, item_list, order_line_amount,
delivery_option, predict_delivery_time_text, collectMethod,
fulfill_unit_id, main_order_ids, pod_can_update, logistic_delay_tip
```

| 用途 | 路径 | 实测 |
|------|------|------|
| 运单 | `package_list[].tracking_no` | 58/58 非空；**没有** `tracking_number` |
| 承运商 | `package_list[].logistic_supplier.supplier_name` | 58/58 非空。现 `CARRIER_KEY_PATTERN` 不含 `supplier_name`，旧解析无承运商前缀 |
| 状态 | `package_list[].logistic_detail.track_list[0].title` | 列表按时间 **新→旧**。已发货 58：`In transit` 43、`Packed` 13、`Out for delivery` 2。处理中 49/49：最新 **`Delivered`**（英文，不是「已送达」）。历史还出现 `Order placed`。已完成抽样另见 `Delivery canceled` / `Return received` / `Couldn't deliver` |
| 轨迹时间 | `track_list[].time` | unix **秒**（int，约 `1.787e9`） |
| 最近文案 | `track_list[0].content` | 非空字符串 |
| `track_status` | `track_list[].track_status` | 观测值恒为 `1`，**不能**当总状态 |
| ETA | `package_list[].predict_delivery_time_text` | 58/58 为 **十进制秒戳字符串**（如 `"1787630399"`），不是 `08/25/2026` 这类日期文案 |
| 实际送达 | 无独立 JSON 键 | 已发货+处理中合计 **0** 个 `delivered_at`/`status_name` 键。送达时刻 = 最新 title 为 `Delivered` 的那条 `track_list[].time`（unix 秒）。处理中 49 单均有该事件，时间早于 `predict_delivery_time_text`，二者不是同一字段 |
| 包裹 ID | `package_list[].fulfill_unit_id` | 只在详情里；列表 API 没有 |

禁止再把 `status_name`、`delivered_at`、`estimated_delivery_at`、`eta`、`provider_name` 当成「待发现的真实键」去扫。新键只能来自另一次全量抓包。

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| 物流相关测试 | `python3 -m unittest tests.test_order_api tests.test_tracking_parse -q` | `OK` |
| 全量回归 | `python3 -m unittest discover -s tests -q` | `OK`（审查时 254 tests；以 exit 0 为准） |

## Scope

**In scope**:

- `scripts/lib/order_api.py`
- `tests/test_order_api.py`
- `tests/fixtures/logistics_empty_data.json`（create：待发货 `data={}`）
- `tests/fixtures/logistics_in_transit.json`（create：按真实键名的合成 in-transit，无 PII）
- `tests/fixtures/logistics_out_for_delivery.json`（create：最新 title=`Out for delivery`）
- `tests/fixtures/logistics_delivered.json`（create：**合成** title=`Delivered` + `time` 秒戳。形状按处理中 49 单；仓库不提交真实 PII / 真实单号）
- `tests/fixtures/README.md`（create：真实 payload 的脱敏清单；**不要**写入真实地址/电话/token）
- `scripts/capture_logistics_fixture.py`（create，默认 dry-run / 打印路径，不写飞书）
- `plans/README.md`（status 行）

**Out of scope**:

- 不改 `parse_logistics_payload` 的对外返回键（SOP 7–9 依赖 `tracking_no` / `tracking_raw`）
- 不改 `sync_shipped_tracking.py` 的 16:00 门、写飞书、发私信
- 不点「查看物流」以外的写按钮；capture 脚本禁止 `--execute`
- 不把完整平台响应当默认 fixture 提交
- FastAPI / SQLite / 话术

## Git workflow

- Branch: `advisor/002-logistics-status-parser`
- Commit message example: `Parse logistics status without inventing delivery times.`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: 冻结现有 tracking 契约

在 `tests/test_order_api.py` 增加断言：`parse_logistics_payload` 成功时 **没有** `delivered_at` / `status_category` 键（或即便以后有，本函数也不要开始返回它们——保持旧函数只做运单）。旧测试 `test_extracts_tracking_number_from_logistics_payload` 必须继续通过。

**Verify**: `python3 -m unittest tests.test_order_api -q` → `OK`

### Step 2: 新增 `parse_logistics_details`

在 `scripts/lib/order_api.py` 增加 **路径常量**（不要再做「键名含子串 deliver 就当送达」的扫描）：

```python
# 仅下列路径允许读取。来源：2 号店已发货 58/58 只读抓包，2026-08-21。
LOGISTICS_PACKAGE_LIST = "package_list"
LOGISTICS_TRACKING_NO = "tracking_no"
LOGISTICS_SUPPLIER = "logistic_supplier"
LOGISTICS_SUPPLIER_NAME = "supplier_name"
LOGISTICS_DETAIL = "logistic_detail"
LOGISTICS_TRACK_LIST = "track_list"
LOGISTICS_TRACK_TITLE = "title"
LOGISTICS_TRACK_TIME = "time"
LOGISTICS_TRACK_CONTENT = "content"
LOGISTICS_ETA_TEXT = "predict_delivery_time_text"
LOGISTICS_FULFILL_UNIT_ID = "fulfill_unit_id"
```

**禁止**读取或等待这些键（全表不存在）：`status_name`、`status_desc`、`package_status_name`、`delivered_at`、`delivery_time`、`actual_delivery_time`、`estimated_delivery_at`、`estimated_delivery_time`、`eta`、`tracking_number`、`provider_name`、`last_track_desc`、`last_event`、`current_event`。旧测试用的 `logistics_info.tracking_number` 只留给 `parse_logistics_payload`。

`parse_logistics_details(payload, *, order_id: str) -> dict` 行为：

1. `data` 必须是 dict。`data == {}`（待发货实测）→ `ok=True`，`package_count=0`，`status_category="unknown"`，`tracking_no=""`，不要抛。
2. `package_list` 不是 list 或长度为 0：同空 data。
3. 每个包裹：
   - 运单：精确读 `tracking_no`，再 `normalize_tracking`；空则该包裹无单号。不要把 `main_order_id` / `invoice_no` / `package_id` 当运单。
   - 承运商：精确读 `logistic_supplier.supplier_name`，再 `normalize_carrier`。
   - 轨迹：`logistic_detail.track_list` 必须当 **下标 0 = 最新**（实测时间降序）。不要按 `track_status` 判断总状态（实测恒为 1）。
   - `status_label` = 最新事件 `title` 原文。
   - `last_event_text` = 最新事件 `content`，否则 `""`。
   - `last_event_at`：最新 `time` 按 unix **秒**（int/数字字符串；`>= 10**12` 才当毫秒）转 timezone-aware UTC datetime。
   - ETA：`predict_delivery_time_text` 去空白后若全是十进制整数，当 unix 秒 → `estimated_delivery_at`。失败则 None。**绝不**写入 `delivered_at`。
   - `delivered_at`：仅当 `normalize_shipment_status(status_label=最新 title, delivered_at=None)["status_category"] == "delivered"` 时，用该条最新事件的 `time`。否则 None。2 号店 **处理中 tab=40** 已验证 title 正好是 `"Delivered"`；合成 fixture 仍要覆盖。**不要**用 ETA。
4. 多包裹（实测全是 1；规则仍写死）：全部 delivered 才聚合 delivered，`delivered_at=max`；任一未 delivered 则取最弱进度 `unknown < label_created < pending_pickup < in_transit < out_for_delivery < delivered`；exception/returned/lost 与 delivered 并存 → `exception`。
5. `status_category` **必须**调用 `assistant.domain.shipment_status.normalize_shipment_status`。禁止在 `order_api.py` 复制映射表。**禁止模块顶层 `import assistant`。** 在 `parse_logistics_details` 内懒加载，并先插入仓库根：
   ```python
   import sys
   from pathlib import Path
   _REPO_ROOT = Path(__file__).resolve().parents[2]  # scripts/lib → 仓库根
   if str(_REPO_ROOT) not in sys.path:
       sys.path.insert(0, str(_REPO_ROOT))
   from assistant.domain.shipment_status import normalize_shipment_status
   ```
   现有 CLI / `tests/test_*.py` 只把 `scripts/` 放进 `sys.path`。顶层 import assistant 会让 `python3 -m unittest discover -s tests -q` 和 `sync_shipped_tracking.py` 加载失败。`parse_logistics_payload` 路径不得依赖 assistant。
6. `needs_delivery_time_confirmation` 为 True 当且仅当聚合 `status_category=="delivered"` 且 `delivered_at is None`。In transit / Packed / Out for delivery **不是**确认送达任务。
7. `raw_payload_hash`：对 data 递归删除 key 正则 `(?i)(phone|mobile|address|receiver|token|cookie|secret)` 后 sha256(json.dumps(..., sort_keys=True, ensure_ascii=False, default=str).encode())。不要存完整 payload。
8. `via`：有 `tracking_no` 用 `"api-field"`；否则 `"details-no-tracking"`。
9. 不要用 `datetime.now()`。

返回键固定为：

```python
{
  "ok": True,
  "order_id": str,
  "tracking_no": str,
  "tracking_raw": str,
  "carrier": str,
  "package_count": int,
  "status_label": str,
  "status_category": str,
  "estimated_delivery_at": datetime | None,
  "delivered_at": datetime | None,
  "last_event_text": str,
  "last_event_at": datetime | None,
  "fulfill_unit_id": str,
  "needs_delivery_time_confirmation": bool,
  "missing_fields": list[str],  # 如 ["delivered_at","status_label"]
  "raw_payload_hash": str,
  "via": str,
}
```

`fetch_tiktok_tracking_api` **保持原样**（仍返回旧 tracking dict）。新增：

```python
def fetch_tiktok_logistics_payload(...) -> dict:
    """与 fetch_tiktok_tracking_api 相同导航+GET，返回原始 JSON payload（含 data）。
    仅供 capture 脚本和 details fetch 使用。不要把 payload 写入 DB。
    """

def fetch_tiktok_logistics_details_api(...) -> dict:
    return parse_logistics_details(fetch_tiktok_logistics_payload(...), order_id=...)
```

不要扩大 endpoint 白名单。

**Verify**: `python3 -m unittest tests.test_order_api -q` → `OK`

### Step 3: 合成 fixture 与测试

Fixture 必须用 **真实键名**，不要写 `status_name` / `tracking_number` / `delivered_at`。合成 ID 即可，无 PII。

`tests/fixtures/logistics_empty_data.json`：`{"code": 0, "message": "success", "data": {}}`

`tests/fixtures/logistics_in_transit.json` 最小形状：

```json
{
  "code": 0,
  "message": "success",
  "data": {
    "package_list": [
      {
        "tracking_no": "UUS68E5590171628828",
        "logistic_supplier": {"supplier_id": "supplier-test", "supplier_name": "USPS"},
        "predict_delivery_time_text": "1787630399",
        "fulfill_unit_id": "fulfill-test-001",
        "logistic_detail": {
          "track_list": [
            {"title": "In transit", "time": 1787207027, "content": "in-transit-event", "track_status": 1},
            {"title": "Packed", "time": 1787172646, "content": "packed-event", "track_status": 1},
            {"title": "Order placed", "time": 1787102765, "content": "placed-event", "track_status": 1}
          ]
        }
      }
    ]
  }
}
```

`tests/fixtures/logistics_out_for_delivery.json`：同上，但最新 title 改为 `Out for delivery`。

`tests/fixtures/logistics_delivered.json`：最新 title=`Delivered`，`time` 用秒戳。键名与处理中 49 单真实 payload 相同，ID 用合成值。不要另造 `delivered_at` 键。可再加一条 title=`Delivery canceled` 的合成包，映射到 001 的 `exception`/`returned`，避免把取消当送达。

测试：

- empty data → `ok=True`，`package_count=0`，`status_category=="unknown"`，不抛
- in_transit → `status_label=="In transit"`，`status_category=="in_transit"`，`delivered_at is None`，`needs_delivery_time_confirmation is False`，`estimated_delivery_at` 对应 `1787630399` 秒，`carrier` 经 normalize 后含 USPS，`tracking_no` 非空
- Packed 作为最新 title → `status_category=="label_created"`（001 映射）
- Out for delivery → `status_category=="out_for_delivery"`
- 合成 Delivered → `status_category=="delivered"`，`delivered_at` 等于最新 `time` 转出的 datetime，**不是** ETA
- 把 `predict_delivery_time_text` 改成非数字时 ETA 为 None，且不冒充送达
- 旧 `parse_logistics_payload` 对无运单仍抛错
- 18 位 `main_order_id` 不得当作 `tracking_no`
- 多包裹：一个 In transit、一个 Delivered → 聚合不是 delivered

若无法 `from assistant.domain.shipment_status import normalize_shipment_status`：STOP，先完成 001。

**Verify**: `python3 -m unittest tests.test_order_api tests.test_tracking_parse -q` → `OK`

### Step 4: 只读 capture 脚本（默认不跑紫鸟）

Create `scripts/capture_logistics_fixture.py`：

- argparse：`--order-id`（必填才能真正请求）、`--store-id`（可选）、`--out` 默认 `tests/fixtures/logistics_captured_redacted.json`
- 无 `--i-am-on-the-order-page` 时：打印说明并 exit 2，**不**调用紫鸟
- 有开关时：`resolve_store_id` **禁止**传入 `default_store_id`（0 家或多家 running 直接失败）
- 调用 `fetch_tiktok_logistics_payload` 拿原始 JSON，再本地 `parse_logistics_details` 打印 missing_fields；写入文件的是 **脱敏后的 payload**，不是解析 dict
- 把 payload 里 phone/address/token/cookie/name/receiver 键替换为 `"[redacted]"` 后再写文件（递归）
- 打印 `missing_fields` 和 `raw_payload_hash`
- 禁止 import `update_record_fields`、`fill_or_send_message`、`click_approve`

本步骤 **不要默认执行** 带开关的命令。执行器只提交脚本和 `tests/fixtures/README.md`（说明：真实 fixture 由操作员在已登录商家订单页后手动跑；提交前必须再人工看一眼脱敏）。

**Verify**: `python3 scripts/capture_logistics_fixture.py` → exit 2，且进程未调用网络（脚本在 argparse 之后立刻退出即可）

### Step 5: 回归

**Verify**: `python3 -m unittest discover -s tests -q` → `OK`

## Test plan

- 新用例写在 `tests/test_order_api.py`，模仿现有 `OrderApiParserTests`。
- 覆盖：空 data、In transit、Packed、Out for delivery、合成 Delivered、ETA 不冒充送达、多包裹、订单号不是运单。
- 不要再为 `status_name` / `delivered_at` 键写会在真实 payload 上假绿的测试。真出现 `Delivered` 标题时，只加脱敏 fixture，不改路径常量。

Verification: `python3 -m unittest tests.test_order_api tests.test_tracking_parse -q` → all pass.

## Done criteria

- [ ] `python3 -m unittest discover -s tests -q` exits 0
- [ ] `parse_logistics_payload` 的现有测试未删，返回值仍含 `tracking_no` 且旧调用方不破
- [ ] `rg -n "datetime.now" scripts/lib/order_api.py` 无匹配（不要用观察时间冒充送达；测试时钟用注入参数）
- [ ] `rg -n "SELLER_LOGISTICS_ENDPOINT" scripts/lib/page_api.py` 仍只有这一个 logistics GET
- [ ] 无完整未脱敏 payload 被加入 git
- [ ] No files outside the in-scope list are modified
- [ ] `plans/README.md` **只更新 002 那一行** Status
- [ ] `rg -n "from assistant" scripts/lib/order_api.py` 不在模块顶层（懒加载且含 `parents[2]`）

## STOP conditions

Stop and report back (do not improvise) if:

- 新抓包出现本计划未列出的包裹顶层键，且没有它就无法解析状态/ETA/运单——STOP，列出键名，不要猜语义。
- 最新 `title` 出现 `Delivered` 以外的送达文案（如 `Delivered to recipient`）——STOP，先改 001 映射再继续。
- `track_list` 变成时间升序（下标 0 变成最旧）——STOP，不要默默改下标。
- `查看物流` 走的不是 `SELLER_LOGISTICS_ENDPOINT`——不要改 DOM 刮字。
- 改 parser 似乎必须改 `sync_shipped_tracking.py` 行为才能测过。
- 不加仓库根到 `sys.path` 就无法 import assistant——不要改成把 `assistant/` 拷进 `scripts/`，也不要把 `zn-sample` 装成 site-package 当本步验收。

## Maintenance notes

- D0 主数据源是 **免费样品处理中 tab=40**（已送达、等达人出内容），不是已发货 tab=30，也不是已完成。已发货是在途。
- 送达时刻用 `Delivered` 事件的 `time`，不要改 001 日历公式，不要用 ETA。
- SOP 7–9 继续只用 `parse_logistics_payload`。承运商要从 `supplier_name` 补，不要扩大写飞书范围。
- Reviewer 应检查：没有 `if "deliver" in key.lower()` 扫描；没有把 `predict_delivery_time_text` 写入 `delivered_at`；没有把 tab=53 抽样当 D+10 数据源写进注释或测试名。
