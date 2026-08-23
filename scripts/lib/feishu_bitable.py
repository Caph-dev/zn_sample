#!/usr/bin/env python3
"""飞书多维表格：达人关系管理(新) 只读/写入（可选）。

默认目标（项目约定）：
  Base 内部达人建联记录表 CJXSbLIQWahB8esVOiscX7j1nLc
  表 达人关系管理(新) tblWT2SRKJ3CEZ5e
  视图 达人管理总表 vewNtqmTk4

纪律：
  - 禁止新增飞书列；只能写已有字段
  - 去重键：红人ID + 寄样产品（命中则跳过不写）
  - 新建记录默认人员=王良希（技术）、合作状态=待发货、是否已寄样=否
  - 物流单号写入后才允许待发货 → 待发布，不回退后续状态
  - 测试环境默认不调用写接口（由 CLI --write-feishu 显式开启）
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .app_config import AppConfigError, load_bitable_settings
from .tracking_parse import tracking_numbers_equivalent

OPEN_API_BASE = "https://open.feishu.cn/open-apis"

DEFAULT_BITABLE_APP_ID = "cli_a975ba96ae781cd1"
DEFAULT_APP_TOKEN = "CJXSbLIQWahB8esVOiscX7j1nLc"
DEFAULT_TABLE_ID = "tblWT2SRKJ3CEZ5e"
DEFAULT_VIEW_ID = "vewNtqmTk4"
DEFAULT_VIEW_NAME = "达人管理总表"
DEFAULT_RECORD_OWNER = "王良希（技术）"
COOPERATION_STATUS_PENDING_SHIP = "待发货"
COOPERATION_STATUS_PENDING_POST = "待发布"
# SOP 2 D+15「未履约」写这个选项，不要和「待发布」混名。
COOPERATION_STATUS_UNPUBLISHED = "未发布"
FEISHU_LANG_EN = "英语"
FEISHU_LANG_ES = "西班牙语"


class FeishuBitableError(RuntimeError):
    """多维表格读写失败。"""


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    for header_name, header_value in (headers or {}).items():
        request.add_header(header_name, header_value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw_body = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as decode_error:
            raise FeishuBitableError(
                f"HTTP {error.code} 非 JSON: {raw_body[:500]}"
            ) from decode_error
        raise FeishuBitableError(
            f"HTTP {error.code} code={payload.get('code')} msg={payload.get('msg')}"
        ) from error
    except urllib.error.URLError as error:
        raise FeishuBitableError(f"网络错误: {error}") from error

    if not isinstance(payload, dict):
        raise FeishuBitableError(f"响应非对象: {type(payload)}")
    if payload.get("tenant_access_token"):
        return payload
    if payload.get("code") not in (0, None):
        raise FeishuBitableError(
            f"飞书 API 失败 code={payload.get('code')} msg={payload.get('msg')}"
        )
    return payload


def get_bitable_access_token(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    try:
        settings = load_bitable_settings(
            config_path=config_path,
            app_id=app_id,
            app_secret=app_secret,
            default_app_id=DEFAULT_BITABLE_APP_ID,
        )
    except AppConfigError as error:
        raise FeishuBitableError(str(error)) from error

    resolved_app_id = settings.get("app_id")
    resolved_secret = settings.get("app_secret")
    if not resolved_app_id:
        raise FeishuBitableError("缺少 bitable app_id（[feishu.bitable].app_id）")
    if not resolved_secret:
        raise FeishuBitableError(
            "缺少 bitable App Secret：请在 config.toml 的 [feishu.bitable].app_secret 填写"
        )

    payload = _http_json(
        "POST",
        f"{OPEN_API_BASE}/auth/v3/tenant_access_token/internal",
        body={"app_id": resolved_app_id, "app_secret": resolved_secret},
    )
    token = payload.get("tenant_access_token")
    if not token:
        raise FeishuBitableError(f"token 响应无 tenant_access_token: {payload}")
    return str(token)


def list_sample_product_options(
    access_token: str,
    *,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> list[str]:
    payload = _http_json(
        "GET",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    items = ((payload.get("data") or {}).get("items") or [])
    for field in items:
        if field.get("field_name") == "寄样产品":
            options = ((field.get("property") or {}).get("options") or [])
            return [str(option.get("name") or "") for option in options if option.get("name")]
    return []


def match_sample_product_option(sku: str, options: list[str]) -> str | None:
    """将主推表货号映射到「寄样产品」单选选项。

    规则：精确 > 大小写不敏感精确 > 选项以前缀包含货号（优先最短选项名）。
    """
    raw = (sku or "").strip()
    if not raw:
        return None
    if raw in options:
        return raw
    lower_map = {option.lower(): option for option in options}
    if raw.lower() in lower_map:
        return lower_map[raw.lower()]

    candidates: list[str] = []
    for option in options:
        option_stripped = option.strip()
        if not option_stripped:
            continue
        # P002 → P002（高腰内裤）；esp111 → esp111(生理裤)
        if option_stripped.lower().startswith(raw.lower()):
            candidates.append(option_stripped)
            continue
        # 去掉括号后缀再比
        base = re.split(r"[（(]", option_stripped, maxsplit=1)[0].strip()
        if base.lower() == raw.lower():
            candidates.append(option_stripped)
    if not candidates:
        return None
    candidates.sort(key=lambda name: (len(name), name))
    return candidates[0]


def find_duplicate_records(
    access_token: str,
    *,
    creator_handle: str,
    sample_product: str,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> list[dict[str, Any]]:
    """Return every exact 红人ID + 寄样产品 duplicate, across result pages.

    A duplicate is a data-integrity condition, not a signal to pick an
    arbitrary first record.  Callers that intend to write must use
    :func:`resolve_duplicate_record` and require a unique match.
    """
    records: list[dict[str, Any]] = []
    page_token = ""
    while True:
        body: dict[str, Any] = {
            "page_size": 100,
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "红人ID",
                        "operator": "is",
                        "value": [creator_handle],
                    },
                    {
                        "field_name": "寄样产品",
                        "operator": "is",
                        "value": [sample_product],
                    },
                ],
            },
        }
        if page_token:
            body["page_token"] = page_token
        payload = _http_json(
            "POST",
            f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            headers={"Authorization": f"Bearer {access_token}"},
            body=body,
        )
        data = payload.get("data") or {}
        records.extend(
            record
            for record in (data.get("items") or [])
            if isinstance(record, dict)
        )
        if not data.get("has_more"):
            return records
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            raise FeishuBitableError("飞书查重分页返回 has_more 但缺少 page_token")


def resolve_duplicate_record(
    access_token: str,
    *,
    creator_handle: str,
    sample_product: str,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """Resolve a duplicate lookup without choosing between multiple records.

    ``status`` is one of ``no-match``, ``unique-match``, ``ambiguous-match``,
    or ``invalid-match``.  The structured result is safe to include in a
    local report and gives callers enough information to stop before writing.
    """
    records = find_duplicate_records(
        access_token,
        creator_handle=creator_handle,
        sample_product=sample_product,
        app_token=app_token,
        table_id=table_id,
    )
    record_ids = [
        str(record.get("record_id") or "").strip()
        for record in records
    ]
    if not records:
        return {"status": "no-match", "record": None, "record_ids": []}
    if len(records) > 1:
        return {
            "status": "ambiguous-match",
            "record": None,
            "record_ids": record_ids,
        }
    if not record_ids[0]:
        return {
            "status": "invalid-match",
            "record": None,
            "record_ids": record_ids,
        }
    return {
        "status": "unique-match",
        "record": records[0],
        "record_ids": record_ids,
    }


def find_duplicate_record(
    access_token: str,
    *,
    creator_handle: str,
    sample_product: str,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any] | None:
    """Backward-compatible read-only helper that returns only a unique record.

    Existing read-only consumers must not accidentally choose among ambiguous
    rows.  Write paths use ``resolve_duplicate_record`` for explicit outcomes.
    """
    result = resolve_duplicate_record(
        access_token,
        creator_handle=creator_handle,
        sample_product=sample_product,
        app_token=app_token,
        table_id=table_id,
    )
    record = result.get("record")
    return record if result.get("status") == "unique-match" and isinstance(record, dict) else None


def create_creator_relation_record(
    access_token: str,
    *,
    creator_handle: str,
    followers_raw: str | None,
    fulfillment_raw: str | None,
    sample_product: str,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """新增达人关系记录，并按 0814 SOP 填写默认负责人和待发货状态。"""
    fields: dict[str, Any] = {
        "红人ID": creator_handle,
        "人员": DEFAULT_RECORD_OWNER,
        "合作状态": [COOPERATION_STATUS_PENDING_SHIP],
        "寄样产品": sample_product,
        "是否已寄样": False,
    }
    if followers_raw is not None and str(followers_raw).strip() != "":
        fields["粉丝数"] = str(followers_raw)
    if fulfillment_raw is not None and str(fulfillment_raw).strip() != "":
        fields["履约率"] = str(fulfillment_raw)

    payload = _http_json(
        "POST",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"fields": fields},
    )
    record = ((payload.get("data") or {}).get("record") or {})
    return {
        "record_id": record.get("record_id"),
        "fields": fields,
        "raw": payload,
    }


def delete_record(
    access_token: str,
    record_id: str,
    *,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """删除记录（回退飞书写入用）。"""
    return _http_json(
        "DELETE",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/"
        f"{urllib.parse.quote(record_id)}",
        headers={"Authorization": f"Bearer {access_token}"},
    )


def _field_plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                parts.append(
                    str(item.get("text") or item.get("name") or item.get("value") or "")
                )
            else:
                parts.append(str(item))
        return ",".join(part for part in parts if part)
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or value.get("value") or "")
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def search_relation_records(
    access_token: str,
    *,
    creator_handle: str,
    sample_product: str | None = None,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
    page_size: int = 20,
) -> list[dict[str, Any]]:
    """按红人ID（可选再加寄样产品）搜行。"""
    handle = (creator_handle or "").strip()
    if not handle:
        return []
    conditions: list[dict[str, Any]] = [
        {"field_name": "红人ID", "operator": "is", "value": [handle]},
    ]
    product = (sample_product or "").strip()
    if product:
        conditions.append(
            {"field_name": "寄样产品", "operator": "is", "value": [product]}
        )
    payload = _http_json(
        "POST",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
        headers={"Authorization": f"Bearer {access_token}"},
        body={
            "page_size": page_size,
            "filter": {"conjunction": "and", "conditions": conditions},
        },
    )
    return list(((payload.get("data") or {}).get("items") or []))


def pending_ship_lookup_key(creator_handle: str, sample_product: str) -> tuple[str, str]:
    """红人ID + 寄样产品，大小写不敏感。"""
    return (
        (creator_handle or "").strip().lower(),
        (sample_product or "").strip(),
    )


def record_created_ms(record: dict[str, Any]) -> int | None:
    raw = record.get("created_time")
    if raw is None:
        raw = record.get("createdTime")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def search_pending_ship_records(
    access_token: str,
    *,
    since: datetime,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
    page_size: int = 100,
) -> list[dict[str, Any]]:
    """近窗内、合作状态=待发货的行。用系统 created_time，不读业务列当时间。"""
    if since.tzinfo is None:
        raise FeishuBitableError("search_pending_ship_records 需要带时区的 since")
    since_ms = int(since.timestamp() * 1000)
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        body: dict[str, Any] = {
            "page_size": min(max(1, int(page_size)), 500),
            "automatic_fields": True,
            "filter": {
                "conjunction": "and",
                "conditions": [
                    {
                        "field_name": "合作状态",
                        "operator": "is",
                        "value": [COOPERATION_STATUS_PENDING_SHIP],
                    }
                ],
            },
        }
        if page_token:
            body["page_token"] = page_token
        payload = _http_json(
            "POST",
            f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/search",
            headers={"Authorization": f"Bearer {access_token}"},
            body=body,
        )
        data = payload.get("data") or {}
        for record in data.get("items") or []:
            if not isinstance(record, dict):
                continue
            created_ms = record_created_ms(record)
            if created_ms is None or created_ms < since_ms:
                continue
            items.append(record)
        if not data.get("has_more"):
            break
        page_token = str(data.get("page_token") or "").strip()
        if not page_token:
            break
    return items


def index_pending_ship_records(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """红人ID+寄样产品 → 待发货行；同键多行走 pick_shipping_target。"""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = record.get("fields") or {}
        key = pending_ship_lookup_key(
            _field_plain(fields.get("红人ID")),
            _field_plain(fields.get("寄样产品")),
        )
        if not key[0] or not key[1]:
            continue
        grouped.setdefault(key, []).append(record)
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for key, group in grouped.items():
        picked = pick_shipping_target(group)
        if picked:
            indexed[key] = picked
    return indexed


def pick_shipping_target(
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """优先：无快递单号且待发货 → 无快递单号 → 首条。"""
    if not records:
        return None

    def tracking_of(record: dict[str, Any]) -> str:
        fields = record.get("fields") or {}
        return _field_plain(fields.get("快递单号")).strip()

    def status_of(record: dict[str, Any]) -> str:
        fields = record.get("fields") or {}
        return _field_plain(fields.get("合作状态"))

    empty = [item for item in records if not tracking_of(item)]
    if empty:
        pending_ship = [
            item for item in empty if "待发货" in status_of(item)
        ]
        return pending_ship[0] if pending_ship else empty[0]
    return records[0]


def update_record_fields(
    access_token: str,
    record_id: str,
    fields: dict[str, Any],
    *,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """按 record_id 更新已有字段。禁止调用方传不存在的列名。"""
    if not record_id:
        raise FeishuBitableError("缺少 record_id")
    if not fields:
        raise FeishuBitableError("更新字段为空")
    payload = _http_json(
        "PUT",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/"
        f"{urllib.parse.quote(record_id)}",
        headers={"Authorization": f"Bearer {access_token}"},
        body={"fields": fields},
    )
    return {
        "record_id": record_id,
        "fields": fields,
        "raw": payload,
    }


def get_record_fields(
    access_token: str,
    record_id: str,
    *,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """只读单条记录字段，用于回填前核对当前「订单号」。"""
    if not record_id:
        raise FeishuBitableError("缺少 record_id")
    payload = _http_json(
        "GET",
        f"{OPEN_API_BASE}/bitable/v1/apps/{app_token}/tables/{table_id}/records/"
        f"{urllib.parse.quote(record_id)}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    record = ((payload.get("data") or {}).get("record") or {})
    return record.get("fields") or {}


def update_record_order_no(
    access_token: str,
    record_id: str,
    order_no: str,
    *,
    app_token: str = DEFAULT_APP_TOKEN,
    table_id: str = DEFAULT_TABLE_ID,
) -> dict[str, Any]:
    """仅回填「订单号」列；不联动「是否已寄样」/「合作状态」。

    已有不同订单号时不覆盖（物流步骤可用 --overwrite 覆盖）。
    写后会重新读取目标行。飞书当前接口没有条件更新版本号，因此无法消除
    GET/PUT 之间的并发窗口；写后读到意外值时明确返回 ``write-uncertain``，
    由调用方停止自动处理并要求人工核对。

    返回 status: written | unchanged | skipped-existing-different |
    write-uncertain。
    """
    current_fields = get_record_fields(
        access_token,
        record_id,
        app_token=app_token,
        table_id=table_id,
    )
    current_order = _field_plain(current_fields.get("订单号")).strip()
    if current_order:
        if current_order == order_no:
            return {
                "status": "unchanged",
                "record_id": record_id,
                "current_order": current_order,
            }
        return {
            "status": "skipped-existing-different",
            "record_id": record_id,
            "current_order": current_order,
        }
    updated = update_record_fields(
        access_token,
        record_id,
        {"订单号": order_no},
        app_token=app_token,
        table_id=table_id,
    )
    try:
        verified_fields = get_record_fields(
            access_token,
            record_id,
            app_token=app_token,
            table_id=table_id,
        )
    except FeishuBitableError as error:
        return {
            "status": "write-uncertain",
            "record_id": record_id,
            "current_order": "",
            "verification_error": str(error),
            "raw": updated.get("raw"),
        }
    verified_order = _field_plain(verified_fields.get("订单号")).strip()
    if verified_order != order_no:
        return {
            "status": "write-uncertain",
            "record_id": record_id,
            "current_order": verified_order,
            "verification_error": "写后读取的订单号与请求值不一致",
            "raw": updated.get("raw"),
        }
    return {
        "status": "written",
        "record_id": record_id,
        "fields": {"订单号": order_no},
        "raw": updated.get("raw"),
    }


def build_shipping_fields(
    *,
    order_no: str,
    tracking_raw: str,
    language: str | None = None,
    overwrite: bool = False,
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """填写物流后联动寄样和合作状态。

    - 成功写入新物流单号或确认同一单号后，勾选「是否已寄样」；
    - 当前状态为待发货/空时，改成待发布；待发布保持幂等；
    - 已发布等后续状态不回退；
    - 已有不同物流单号且未 overwrite 时，不改任何物流联动字段。
    """
    current_fields = (current or {}).get("fields") or {}
    current_track = _field_plain(current_fields.get("快递单号")).strip()
    current_order = _field_plain(current_fields.get("订单号")).strip()
    current_status = _field_plain(current_fields.get("合作状态")).strip()
    want_track = (tracking_raw or "").strip()
    same_track = bool(current_track) and (
        current_track == want_track or tracking_numbers_equivalent(current_track, want_track)
    )
    skip_tracking = bool(current_track) and (not overwrite) and (not same_track)

    fields: dict[str, Any] = {}
    status_transition = "not-applicable"
    if skip_tracking:
        return {
            "fields": fields,
            "skip_tracking": True,
            "current_track": current_track,
            "current_order": current_order,
            "current_status": current_status,
            "status_transition": "skipped-different-tracking",
        }

    if language in {FEISHU_LANG_EN, FEISHU_LANG_ES}:
        fields["使用语言"] = language
    if order_no and (overwrite or not current_order or current_order == order_no):
        fields["订单号"] = order_no
    if want_track:
        fields["快递单号"] = want_track
        fields["是否已寄样"] = True
        current_status_values = {
            status.strip()
            for status in current_status.split(",")
            if status.strip()
        }
        protected_statuses = current_status_values - {
            COOPERATION_STATUS_PENDING_SHIP,
            COOPERATION_STATUS_PENDING_POST,
        }
        if protected_statuses:
            status_transition = f"preserved:{current_status}"
        elif not current_status_values or COOPERATION_STATUS_PENDING_SHIP in current_status_values:
            fields["合作状态"] = [COOPERATION_STATUS_PENDING_POST]
            status_transition = (
                "待发货->待发布" if current_status else "空->待发布"
            )
        elif COOPERATION_STATUS_PENDING_POST in current_status_values:
            fields["合作状态"] = [COOPERATION_STATUS_PENDING_POST]
            status_transition = "already-pending-post"
        else:
            status_transition = f"preserved:{current_status}"

    return {
        "fields": fields,
        "skip_tracking": False,
        "current_track": current_track,
        "current_order": current_order,
        "current_status": current_status,
        "status_transition": status_transition,
    }


def cooperation_status_unchanged(transition: str) -> bool:
    """合作状态没有实质变化：已是待发布，或已发布等后续状态保持不动。"""
    text = str(transition or "").strip()
    return text == "already-pending-post" or text.startswith("preserved:")


def feishu_shipping_already_current(plan: dict[str, Any], tracking_raw: str) -> bool:
    """单号已一致且合作状态不用改：不要报「飞书已更新」，也不计入写入条数。"""
    if not cooperation_status_unchanged(str(plan.get("status_transition") or "")):
        return False
    current = str(plan.get("current_track") or "").strip()
    want = str(tracking_raw or "").strip()
    if not current or not want:
        return False
    return current == want or tracking_numbers_equivalent(current, want)


def describe_cooperation_transition(transition: str) -> str:
    """把合作状态机码译成业务员能看懂的中文。"""
    text = str(transition or "").strip()
    if text == "already-pending-post":
        return "合作状态已是「待发布」，不用再改"
    if text.startswith("preserved:"):
        current = text.split(":", 1)[1].strip() or "当前值"
        return f"合作状态保持「{current}」，不回退"
    if "->" in text:
        left, right = text.split("->", 1)
        return f"合作状态：{left} → {right}"
    if text == "skipped-different-tracking":
        return "飞书已有不同运单，合作状态未改"
    if text and text != "not-applicable":
        return f"合作状态：{text}"
    return "未改合作状态"


def build_product_id_to_sku_map(
    hero_data: dict[str, Any],
    *,
    hero_only: bool = True,
) -> dict[str, str]:
    """从 feishu_hero.load_hero_from_feishu 结果构建 product_id → 产品货号。

    默认只收录「是否主推=是」。物流回写已有飞书行使 ``hero_only=False``。
    """
    mapping: dict[str, str] = {}
    for row in hero_data.get("rows") or []:
        if not isinstance(row, dict):
            continue
        if hero_only and not row.get("is_hero"):
            continue
        product_id = str(row.get("product_id") or "").strip()
        sku = str(row.get("sku") or "").strip()
        if not product_id or not sku:
            continue
        if not product_id.isdigit() and not re.fullmatch(r"\d+\.0+", product_id):
            # 跳过「下架」等
            digits = re.sub(r"\s+", "", product_id)
            if not digits.isdigit():
                continue
            product_id = digits
        mapping[product_id.split(".", 1)[0]] = sku
    return mapping


def resolve_sample_product_for_row(
    row: dict[str, Any],
    *,
    product_id_to_sku: dict[str, str],
    sample_product_options: list[str],
) -> dict[str, Any]:
    """解析某行应写入的寄样产品选项。

    返回:
      ok, sku, option, reason
    """
    product_id = str(row.get("product_id") or "").strip()
    sku = product_id_to_sku.get(product_id) if product_id else None
    if not sku:
        return {
            "ok": False,
            "sku": None,
            "option": None,
            "reason": (
                f"product_id={product_id} 在主推表无对应主推货号（须是否主推=是）"
            ),
        }
    option = match_sample_product_option(sku, sample_product_options)
    if not option:
        return {
            "ok": False,
            "sku": sku,
            "option": None,
            "reason": f"货号 {sku} 无法匹配「寄样产品」单选选项",
        }
    return {"ok": True, "sku": sku, "option": option, "reason": "ok"}


def format_followers_raw(row: dict[str, Any]) -> str:
    """粉丝数原样字符串（优先列表原始字段）。"""
    for key in ("follower_num", "followers_n", "followers"):
        value = row.get(key)
        if value is None or value == "":
            continue
        return str(value)
    return ""


def format_fulfillment_raw(row: dict[str, Any]) -> str:
    """履约率原样字符串。"""
    for key in ("fulfillment_rate", "fulfillment_n", "cell_fulfill", "est_post_rate"):
        value = row.get(key)
        if value is None or value == "":
            continue
        text = str(value).strip()
        if text:
            return text
    return ""
