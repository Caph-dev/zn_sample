# Plan 001: SOP2 跟进领域模型以纯函数落地

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
> git diff --stat a23807c -- 样品申请筛查sop/2-查看到货+达人跟进.md scripts/lib/detect_lang.py scripts/lib/export_util.py scripts/lib/feishu_hero.py scripts/lib/feishu_bitable.py scripts/lib/filters.py scripts/lib/message_templates.py scripts/lib/operator_launch.py
> git status --short -- 样品申请筛查sop/2-查看到货+达人跟进.md scripts/lib/filters.py scripts/lib/feishu_bitable.py
> ```
> Do **not** use `git diff a23807c..HEAD` (it ignores the working tree).
> If Current state excerpts no longer match live files, STOP.
> Expected at review (2026-08-22): HEAD `a23807c`; SOP 2 文件为未跟踪新文件；`filters.py` 含 `ACTIVE_HERO_PRODUCT_ID`；`feishu_bitable.py` 含 `COOPERATION_STATUS_UNPUBLISHED = "未发布"`。

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: direction
- **Planned at**: commit `a23807c`, reviewed 2026-08-22 (working-tree SOP 2 / filters / 未发布)

## Why this matters

操作台第一版的价值是「到货后按自然日生成跟进待办并给出话术」，不是再包一层 CLI。日期、阶段、语言、达人类型、指定 B005 配图、话术必须是无 I/O 的纯函数。现有 `scripts/lib/message_templates.py` 只有 SOP 第 6/9 步，没有 SOP 2 跟进话术。

## Current state

Relevant files (read-only except the in-scope creates):

- `样品申请筛查sop/2-查看到货+达人跟进.md` — **话术与日历原文**。HEAD 没有这个路径（旧合并 SOP 已删）。若文件不存在 → STOP。忽略文中仍可能出现的旧 wiki 插图说明；以文首「项目约定」和「跟进节奏」表为准：无 D+5、无隔天循环、忽略 B006-A。
- `scripts/lib/filters.py` — **不要改**。约 70–72 行：
  ```python
  ACTIVE_HERO_PRODUCT_ID = "1732414717062320994"
  DEFAULT_ACTIVE_HERO_PRODUCT_IDS = frozenset({ACTIVE_HERO_PRODUCT_ID})
  ```
  空 `allowed_product_ids` = 全部主推（CLI `--all-hero-products`）。配图常量必须 import 它，不要再写一遍数字。
- `scripts/lib/feishu_bitable.py` — **不要改**。约 38–41 行：`COOPERATION_STATUS_PENDING_POST = "待发布"` 与 `COOPERATION_STATUS_UNPUBLISHED = "未发布"`。D+15 未履约用后者。
- `scripts/lib/message_templates.py` — 仅 `intro_message` / `tracking_message`（不要改）
- `scripts/lib/detect_lang.py` — `detect_creator_lang`；空简介 → `en` + `confidence=low`
- `scripts/lib/export_util.py` — `derive_creator_type_flags` 会用 GPM 兜底（跟进类型 **禁止** 调用它）
- `scripts/lib/feishu_hero.py` — `_normalize_sku_key` 只去空白并 lower，不去掉连字符
- `scripts/lib/operator_launch.py` — `Asia/Shanghai`，Windows 无 tzdata 时用 `UTC+8`
- `scripts/sync_shipped_tracking.py` 约 485–492 行 — 语言优先级范例（先飞书「使用语言」再 bio）；本计划只在纯函数里复现优先级，不调用该脚本
- `tests/test_tracking_parse.py` — unittest 风格范例

`scripts/lib/message_templates.py` 现状（不要往这里加 SOP 2 模板）：

```python
def intro_message(lang: str, creator_name: str) -> str:
    ...
def tracking_message(lang: str, tracking_no: str) -> str:
    ...
```

`detect_creator_lang` 空简介（`scripts/lib/detect_lang.py` 约 198–213 行）：

```python
if not cleaned:
    return {
        "lang": LANG_EN,
        "feishu_lang": FEISHU_LANG[LANG_EN],
        "confidence": "low",
        "reason": "empty-bio-default-en",
        ...
    }
```

时区约定（复制 `scripts/lib/operator_launch.py` 46–52 行的 fallback，不要 import FastAPI）：

```python
try:
    if ZoneInfo is None:
        raise RuntimeError("zoneinfo unavailable")
    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING = timezone(timedelta(hours=8))
```

仓库惯例：

- 新测试放 `tests/assistant/`，标准库 `unittest`，不要引入 pytest。
- 每个新测试文件头：
  ```python
  PROJECT_ROOT = Path(__file__).resolve().parents[2]  # tests/assistant/ → 仓库根
  sys.path.insert(0, str(PROJECT_ROOT))               # assistant
  sys.path.insert(0, str(PROJECT_ROOT / "scripts"))   # lib
  ```
  `tests/test_tracking_parse.py` 的 `parents[1]` 不能照抄（那个文件在 `tests/` 下一层）。
- 库代码用 `logging.getLogger(__name__)`，不要在库里 `configure_logging`。
- 默认中文注释可以，标识符用英文。
- 不要把密钥写进测试。

## Commands you will need

| Purpose | Command | Expected on success |
|---------|---------|---------------------|
| 现有回归 | `python3 -m unittest discover -s tests -q` | `OK`（审查时 254 tests；以 exit 0 为准） |
| 本计划测试 | `python3 -m unittest discover -s tests/assistant -q` | `OK`，本计划新增用例全过 |
| 确认未改 SOP6/9 | `python3 -m unittest tests.test_tracking_parse tests.test_send_sample_intro -q` | `OK` |

## Scope

**In scope** (the only files you should modify):

- `assistant/__init__.py`（create，可为空）
- `assistant/domain/__init__.py`（create；`__all__` 必须精确为：`beijing_now`, `beijing_date`, `normalize_shipment_status`, `days_since_delivery`, `latest_due_unpublished_stage`, `plan_followup_mutation`, `choose_followup_creator_type`, `choose_followup_language`, `followup_task_idempotency_key`, `message_send_idempotency_key`, `feishu_update_idempotency_key`, `resolve_followup_attachment`, `choose_template_key`, `render_followup_message`, `TEMPLATE_VERSION`, `FOLLOWUP_IMAGE_PRODUCT_ID`, `FEISHU_UNFULFILLED_COOPERATION_STATUS`）
- `assistant/domain/timeutil.py`（create）
- `assistant/domain/shipment_status.py`（create）
- `assistant/domain/followup_stage.py`（create）
- `assistant/domain/policies.py`（create）
- `assistant/domain/sku_images.py`（create）
- `assistant/domain/message_templates.py`（create）
- `tests/assistant/__init__.py`（create，可为空）
- `tests/assistant/test_shipment_status.py`（create）
- `tests/assistant/test_followup_stage.py`（create）
- `tests/assistant/test_policies.py`（create）
- `tests/assistant/test_sku_images.py`（create）
- `tests/assistant/test_message_templates.py`（create）
- `plans/README.md`（本计划 status 行）

**Out of scope** (do NOT touch):

- `scripts/lib/message_templates.py` 及任何 SOP 6/9 调用方
- `scripts/lib/filters.py`、`scripts/lib/feishu_bitable.py`（只 import，不改）
- 筛查阈值、`screen_sample_requests.py`
- `scripts/sync_shipped_tracking.py`、`send_sample_intro.py`
- FastAPI、SQLite、紫鸟、飞书 IO、图片发送
- `plan/plan_workdesk_v1.md`（产品规格，不要改）

## Git workflow

- Branch: `advisor/001-followup-domain`
- Commit per logical unit. Message style from this repo (English sentence, not conventional commits), e.g. `Add SOP2 follow-up domain functions and tests.`
- Do NOT push or open a PR unless the operator instructed it.

## Steps

### Step 1: 时区与物流状态标签映射

Create `assistant/domain/timeutil.py`:

```python
BEIJING  # ZoneInfo("Asia/Shanghai") or timezone(timedelta(hours=8))

def beijing_now(now: datetime | None = None) -> datetime:
    """返回带 tz 的北京时间。now 为 None 则 datetime.now(BEIJING)。
    now 有 tz → astimezone(BEIJING)；naive → replace(tzinfo=BEIJING)，不当 UTC。
    """

def beijing_date(dt: datetime) -> date:
    return beijing_now(dt).date()
```

Create `assistant/domain/shipment_status.py`:

```python
STATUS_CATEGORIES = (
    "unknown",
    "label_created",
    "pending_pickup",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "returned",
    "lost",
)
```

`normalize_shipment_status(*, status_label: str | None, delivered_at: datetime | None) -> dict`

规则（失败关闭，禁止把「已发货」当送达）：

先把 label 做 `folded = re.sub(r"\s+", " ", (status_label or "").strip().lower())`。匹配从上到下 **第一条命中**（`delivered_at` 非空除外，它最高优先）：

| 输入 | `status_category` |
|------|-------------------|
| `delivered_at` 非空 | `delivered`（即使 label 空） |
| 原文含 `已送达`，或 `folded in {"delivered", "delivered."}`（禁止子串命中 `not delivered` / `couldn't deliver`） | `delivered` |
| `folded in {"label created", "label_created", "packed", "order placed"}` | `label_created` |
| 原文含 `待揽收` 或 `folded in {"pending pickup", "pending_pickup"}` | `pending_pickup` |
| 原文含 `运输中` 或 `folded in {"in transit", "in_transit"}` | `in_transit` |
| 原文含 `派送中` 或 `folded in {"out for delivery", "out_for_delivery"}` | `out_for_delivery` |
| 原文含 `退回`，或 `folded in {"returned", "return received"}` | `returned` |
| 原文含 `丢失` 或 `folded in {"lost"}` | `lost` |
| 原文含 `异常`，或 `folded in {"exception", "delivery canceled", "couldn't deliver", "could not deliver"}` | `exception` |
| `已发货`、`shipped`、空、其它 | `unknown`（不要映射成 delivered） |

返回：

```python
{
  "status_category": str,
  "delivered_at": datetime | None,  # 原样；不要用 now() 冒充
  "needs_delivery_time_confirmation": bool,
}
```

`needs_delivery_time_confirmation` 为 True 当且仅当 `status_category == "delivered"` 且 `delivered_at is None`。禁止用 ETA 填 `delivered_at`。本函数不接收 ETA 参数，避免误用。

**Verify**:

```bash
python3 -c "from assistant.domain.shipment_status import normalize_shipment_status as n; print(n(status_label='已发货', delivered_at=None)['status_category'], n(status_label='Packed', delivered_at=None)['status_category'], n(status_label='In transit', delivered_at=None)['status_category'])"
```

→ 打印 `unknown label_created in_transit`

### Step 2: 跟进阶段日历

Create `assistant/domain/followup_stage.py`。

D0 = `delivered_at` 的北京自然日，不是首次观察日。

```python
# Must match lib.feishu_bitable.COOPERATION_STATUS_UNPUBLISHED. Do not use 待发布.
FEISHU_UNFULFILLED_COOPERATION_STATUS = "未发布"

def days_since_delivery(delivered_at: datetime, today: date | None = None) -> int:
    """D0 = beijing_date(delivered_at)。today 默认 beijing_date(beijing_now())。
    返回 (today - D0).days。naive delivered_at 按北京时间解释。
    """

def latest_due_unpublished_stage(days: int) -> tuple[str, int] | None:
    """返回 (stage, offset_days)。days<0 返回 None。"""
```

`offset_days` 用于 `scheduled_for = delivered_date + timedelta(days=offset_days)`。

| days | 返回 |
|------|------|
| < 0 | `None` |
| 0, 1, 2 | `("arrival", 0)` |
| 3, 4, 5, 6 | `("day_3", 3)` |
| 7, 8, 9 | `("day_7", 7)` |
| 10, 11, 12, 13, 14 | `("day_10_list", 10)` 只出名单，无话术 |
| ≥ 15 | `("unfulfilled", 15)` 飞书目标状态=`FEISHU_UNFULFILLED_COOPERATION_STATUS`（「未发布」=未履约）。本计划只返回 stage，不写飞书 |

**不要**实现 `is_overdue_unpublished`。D+10 是名单 stage，不是布尔 overdue。没有 D+5，没有「每隔两天」循环。

`latest_due_unpublished_stage` 返回的 **任何** stage（含 `day_10_list` / `unfulfilled`）都走同一 `create` 路径；只是后两者没有话术。催更集合 `{arrival, day_3, day_7}` 的意思是：它们会发私信（M5）；不要理解成「不 create day_10_list」。`has_confirmed_content` 为 True 时 suppress 未发送的 arrival/day_3/day_7/day_10_list/unfulfilled（reason=`content_confirmed`）。**不要**把 005 的 `confirm_delivery_time` 传进本函数。

`plan_followup_mutation(*, existing_unpublished: list[dict], days: int, has_confirmed_content: bool) -> dict`：

- 每条 existing 字典至少有 `stage`, `scheduled_for` (ISO date str 或 date), `status`。
- `has_confirmed_content` 为 True：所有 `status in {pending, ready, needs_review}` 且 stage 属于 `{arrival, day_3, day_7, day_10_list, unfulfilled}` 的任务 → `suppress`，reason=`content_confirmed`。不创建新催更任务。
- 否则计算 `latest = latest_due_unpublished_stage(days)`。若 None，不创建。
- 创建键 `(stage, scheduled_for)` 已存在则不重复创建。
- 其它仍为 pending/ready/needs_review 的 unpublished 任务（含被更新阶段取代的 arrival/day_3/day_7/day_10_list）→ `suppress`，reason=`superseded_by_later_stage`。
- 已 `sent` / `unknown` / `failed` 的行不要改。

返回：

```python
{
  "create": [{"stage": str, "scheduled_for": date, "status": "pending"}],
  "suppress": [{"stage": str, "scheduled_for": date, "reason": str}],
}
```

**Verify**:

```bash
python3 -c "from assistant.domain.followup_stage import latest_due_unpublished_stage as s; print(s(0), s(3), s(7), s(10), s(11), s(15), s(-1))"
```

→ 打印含 `arrival` `day_3` `day_7` `day_10_list` `day_10_list` `unfulfilled` `None`（tuples 即可，第二项分别为 0/3/7/10/10/15）。

### Step 3: 语言、达人类型、幂等键

Create `assistant/domain/policies.py`。公共函数 **必须** 用这些名字（005 会 import）：

```python
def choose_followup_creator_type(
    *,
    manual_type: str | None,
    is_video_creator: str = "",
    is_live_creator: str = "",
) -> dict:
    """返回 {"creator_type": "video"|"live"|None, "needs_review": bool}。"""

def choose_followup_language(
    *,
    bio: str | None,
    feishu_lang: str | None = None,
    manual_lang: str | None = None,
) -> dict:
    """返回 {"lang": "en"|"es", "needs_review": bool, "reason": str}。
    feishu_lang 为飞书「使用语言」原文：英语/西班牙语。
    """

def followup_task_idempotency_key(
    sample_case_id: str, stage: str, scheduled_for: date
) -> str:
    """`{sample_case_id}|{stage}|{scheduled_for:%Y-%m-%d}`。"""

def message_send_idempotency_key(
    store_id: str, creator_id: str, product_id: str, stage: str, scheduled_for: date
) -> str: ...

def feishu_update_idempotency_key(
    feishu_record_id: str, target_status: str, content_evidence_id: str
) -> str: ...
```

跟进类型只允许 `video` / `live` / `None`（需人工）。输入的 `is_video_creator` / `is_live_creator` 只认筛查导出列的 **「是」或空**（不要在本函数里读 GPM）。优先级：

1. `manual_type` 若为 `video` 或 `live` → 用它，`needs_review=False`
2. 仅 `is_video_creator == "是"` 且 `is_live_creator != "是"` → `video`
3. 仅 `is_live_creator == "是"` 且 `is_video_creator != "是"` → `live`
4. 两个都是「是」，或都不是 → `None`, `needs_review=True`

禁止：用 GPM、标题、或 `derive_creator_type_flags` 自动选型；禁止把「视频+直播」拆成两条待办。

语言（与 `sync_shipped_tracking.py` 485–492 行同一优先级）：

1. `manual_lang` 若为 `en` 或 `es` → 用它，`needs_review=False`（人工覆盖最高）
2. 否则 `feishu_lang` 若为「英语」「西班牙语」→ `en`/`es`，`needs_review=False`
3. 否则 `lib.detect_lang.detect_creator_lang(bio)`
4. `confidence == "high"` → 用其 `lang`，`needs_review=False`；否则推荐 `en` 且 `needs_review=True`

幂等键用 `|` 拼接，禁止把 `template_version` 放进去。`feishu_update_idempotency_key` 的 `target_status` 在 D+15 场景应传入 `FEISHU_UNFULFILLED_COOPERATION_STATUS`（「未发布」），不要传「待发布」或「未履约」。

**Verify**: 本步结束前写好 `tests/assistant/test_policies.py`，然后 `python3 -m unittest tests.assistant.test_policies -q` → `OK`

### Step 4: 指定 B005 配图（唯一商品 ID）

Create `assistant/domain/sku_images.py`。

```python
from lib.filters import ACTIVE_HERO_PRODUCT_ID

FOLLOWUP_IMAGE_PRODUCT_ID = ACTIVE_HERO_PRODUCT_ID  # "1732414717062320994"
FOLLOWUP_IMAGE_PATH = "样品申请筛查sop/图片和附件/2-查看到货+达人跟进-b05.png"
```

禁止把该 19 位数字再写进本文件字面量（测试里断言 `FOLLOWUP_IMAGE_PRODUCT_ID == ACTIVE_HERO_PRODUCT_ID` 即可）。忽略 B006-A，忽略其它 B005 商品 ID，忽略标题/子串。

`resolve_followup_attachment(*, product_id: str | None, resolved_sku: str | None = None) -> dict`：

- `str(product_id).strip() == FOLLOWUP_IMAGE_PRODUCT_ID` → `{matched: True, sku_key: "b005", path: FOLLOWUP_IMAGE_PATH}`
- 否则 `{matched: False, sku_key: "", path: ""}`（即便 `resolved_sku` 是 `B005` / `b005`）
- 货号 B006-A 永不相配。

本计划只返回仓库相对路径，不读二进制、不发送。命中时到货话术用 `arrival_hero_*`；未命中用 `arrival_other_*`（模板要存在；005 默认不为未命中商品建任务）。

**Verify**: `python3 -m unittest tests.assistant.test_sku_images -q` → `OK`

### Step 5: 20 条 SOP 2 话术

Create `assistant/domain/message_templates.py`。

`TEMPLATE_VERSION = 1`

从 `样品申请筛查sop/2-查看到货+达人跟进.md` 复制 **渲染后的可见正文**。把 `（达人名）` 换成 `{creator_name}`，`（视频链接）` 换成 `{content_url}`。不要改 emoji。D+3 用「到货后第 3 天」话术，D+7 用「到货后第 7 天」话术。无 D+5。感谢话术（`video_found_*` / `live_found_*`）严格按「达人出视频」「达人开启直播」两节，**不要追加投流码或原文没有的句子**。

`template_key` 必须是下面 20 个之一：

| key | SOP 段落 |
|-----|----------|
| `arrival_hero_video_en` / `_es` / `_live_en` / `_live_es` | 刚到货 + 指定 B005 商品 ID |
| `arrival_other_video_en` / `_es` / `_live_en` / `_live_es` | 刚到货 + 其它货号（以后扩品用；当前跟进池不用） |
| `unpublished_3_video_en` / `_es` / `_live_en` / `_live_es` | 到货第 3 天 |
| `unpublished_7_video_en` / `_es` / `_live_en` / `_live_es` | 到货第 7 天 |
| `video_found_en` / `video_found_es` | 达人出视频（无 video/live 分裂） |
| `live_found_en` / `live_found_es` | 达人开启直播（无 video/live 分裂） |

`choose_template_key(*, stage, creator_type, lang, is_hero_sku: bool) -> str | None`：

- `lang` 不是 `en`/`es` → `None`
- `stage in {"day_10_list", "unfulfilled"}` → `None`（先于类型检查）
- `stage=="video_found"` → `video_found_{lang}`（忽略 creator_type）
- `stage=="live_found"` → `live_found_{lang}`
- 其余需要类型的 stage：`creator_type` 不是 `video`/`live` → `None`（调用方标 needs_review）
- `stage=="arrival"` 且 `is_hero_sku` → `arrival_hero_{type}_{lang}`
- `stage=="arrival"` 否则 → `arrival_other_{type}_{lang}`
- `stage=="day_3"` → `unpublished_3_{type}_{lang}`
- `stage=="day_7"` → `unpublished_7_{type}_{lang}`
- 其它 stage → `None`

`render_followup_message(template_key, *, creator_name: str, content_url: str = "") -> str`：用 `str.format`。`video_found_*` 必须包含替换后的链接。未知 key 抛 `ValueError`。

**Verify**: `python3 -m unittest tests.assistant.test_message_templates -q` → `OK`

测试至少包括：指定 B005 商品 ID 到货英语视频正文含 `#Hicloth`；西语直播 D+3 含 `promoción` 或 SOP 原文片段；`video_found_en` 含传入的 URL；未知 key 抛错；感谢正文 **不含** `投流码`。

### Step 6: 回归现有测试

**Verify**: `python3 -m unittest discover -s tests -q` → 仍 `OK`，失败数为 0。新增 `tests/assistant/test_*.py` 应被 discover 到（`tests/assistant/__init__.py` 必须存在）。

## Test plan

全部新建在 `tests/assistant/`，结构模仿 `tests/test_tracking_parse.py`，但 `parents[2]` 且两条 `sys.path`。

必须覆盖：

- `label_created` 输入 `"label created"` / `"Packed"` / `"Order placed"` 得到该 category
- `"In transit"` → `in_transit`；`"Out for delivery"` → `out_for_delivery`；`"Delivered"` → `delivered`
- `"Delivery canceled"` / `"Couldn't deliver"` → `exception`；`"Return received"` → `returned`
- 已发货 ≠ 已送达；`"not delivered"` 不得变成 delivered
- 有 label「已送达」无 `delivered_at` → `needs_delivery_time_confirmation True`
- 有 `delivered_at` 不用 label 也对
- D0/D+3/D+7/D+10/D+11/D+15：`latest_due_unpublished_stage` 分别为 arrival / day_3 / day_7 / day_10_list / day_10_list / unfulfilled（无 D+5、无隔天循环）
- 首次生成发生在 D+6：只 create `day_3`，不 create arrival
- 已有 pending arrival 时生成 D+3：create day_3，suppress arrival
- 确认内容后 suppress 未发送 unpublished（含 day_10_list）
- 已 sent 不被 suppress
- **不要**写 `is_overdue_unpublished` / 「D+11 overdue True」——该函数已废除
- `FEISHU_UNFULFILLED_COOPERATION_STATUS == lib.feishu_bitable.COOPERATION_STATUS_UNPUBLISHED` 且 `!= "待发布"`
- 视频+直播 → needs_review，不选模板
- 空 bio 且无飞书语言 → en + needs_review
- `feishu_lang="西班牙语"` 即使 bio 空 → es 且不 review
- `manual_lang="en"` 覆盖飞书西语
- high confidence es bio → es 且不 review
- `FOLLOWUP_IMAGE_PRODUCT_ID is ACTIVE_HERO_PRODUCT_ID`；仅该 product_id 配图；其它 B005 商品 ID、B006-A、`resolved_sku="B005"` 都不配图
- 话术 20 个 key 都能 render；`day_10_list` / `unfulfilled` 的 `choose_template_key` 为 None
- 幂等键不含 template_version；改 version 不改变键

Verification: `python3 -m unittest discover -s tests/assistant -q` → all pass.

## Done criteria

- [ ] `python3 -m unittest discover -s tests -q` exits 0
- [ ] `python3 -m unittest discover -s tests/assistant -q` exits 0 and includes the new test modules
- [ ] `rg -n "def intro_message|def tracking_message" scripts/lib/message_templates.py` still shows those two builders; `looks_like_intro` / `looks_like_tracking` 可保留，不要新增 SOP 2 的 `def`
- [ ] `rg -n "derive_creator_type_flags" assistant/` returns no matches
- [ ] `rg -n "is_overdue_unpublished" assistant/` returns no matches
- [ ] `rg -n "1732414717062320994" assistant/` 仅测试或注释可出现；`sku_images.py` 必须通过 `ACTIVE_HERO_PRODUCT_ID` 引用
- [ ] `rg -n "待发布" assistant/domain/` returns no matches（未履约用「未发布」）
- [ ] No files outside the in-scope list are modified (`git status`)
- [ ] `plans/README.md` **只更新 001 那一行** Status，不要重写整张表

## STOP conditions

Stop and report back (do not improvise) if:

- `样品申请筛查sop/2-查看到货+达人跟进.md` 不存在，或话术段落被删改到无法逐字对应 20 个 key。
- `lib.filters.ACTIVE_HERO_PRODUCT_ID` 不存在或不再是 19 位数字字符串。
- 有人要求把 SOP 2 模板写进 `scripts/lib/message_templates.py`。
- `detect_creator_lang` 签名变了，不再返回 `lang`/`confidence`。
- 测试需要联网、紫鸟或飞书才能过。
- 有人要求实现 D+5、每隔两天、或 `is_overdue_unpublished`。

## Maintenance notes

- 改话术：升 `TEMPLATE_VERSION`，不要改幂等键。
- 以后若周末停发，只改 `latest_due_unpublished_stage`，不要把日历写进 FastAPI route。
- 操作台只应调用这些函数；不要在 Jinja 里重算阶段。
- 跟进类型不要接回 `filters.evaluate_row`：筛查通过 ≠ 跟进用视频模板。
- 扩品跟进：把 005 的允许集合改成空（与 `--all-hero-products` 同语义），不要改本计划的精确 product_id 配图函数；其它货号继续 `matched=False` 除非以后另加图片表。
- Reviewer：确认没有把「未发布」写成「待发布」或「未履约」四个字当飞书选项。
