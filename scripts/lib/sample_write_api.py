#!/usr/bin/env python3
"""样品申请批准 API（窄接口，受 execute 门闩保护）。

批准接口契约来自一次真实的 DOM 批准网络观察：

* endpoint: ``/api/v1/affiliate/sample/group/action``
* ``type=1`` 表示批准；
* 待审核申请的 ``status_type`` 使用当前申请的 ``curr_status``；
* 只允许待审核状态 ``10`` / ``11``，禁止把已发货等后续状态写进批准请求；
* 单条申请使用 ``apply_ids=[apply_id]``、``group_ids=[]``；
* 非跨区域申请使用 ``is_use_cross_regions=false``。

本模块不提供任意 endpoint/body 写入能力。请求不会自动重试；超时或
响应不确定时，只能通过只读状态核验决定结果，不能自动回退 DOM 或重发。
"""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from .page_api import (
    AffiliatePageContext,
    PageApiSchemaError,
    get_affiliate_page_context,
    post_sample_group_action_json,
)
from .sample_api import (
    PENDING_TAB,
    READY_TO_SHIP_TAB,
    check_application_in_tab_api,
    check_pending_application_api,
)

APPROVE_ACTION_TYPE = 1
CREATOR_ORDER_PENDING_STATUSES = frozenset({10, 11})
CREATOR_ORDER_PENDING_STATUS = 11
READY_TO_SHIP_STATUS = 20
DEFAULT_APPROVAL_CONFIRM_ATTEMPTS = 6
DEFAULT_APPROVAL_CONFIRM_WAIT_SECONDS = 1.5

ActionRequest = Callable[..., dict[str, Any]]


def build_approve_application_request(
    apply_id: str,
    *,
    status_type: int,
) -> dict[str, Any]:
    """构造已捕获验证过的单条批准请求体。"""
    normalized_apply_id = str(apply_id or "").strip()
    if not normalized_apply_id:
        raise PageApiSchemaError("批准 API 请求缺少 apply_id")
    if int(status_type) not in CREATOR_ORDER_PENDING_STATUSES:
        raise PageApiSchemaError(
            "批准 API 只接受待审核状态 status_type=10/11，"
            f"实际为 {status_type!r}"
        )
    return {
        "apply_ids": [normalized_apply_id],
        "group_ids": [],
        "is_use_cross_regions": False,
        "status_type": int(status_type),
        "type": APPROVE_ACTION_TYPE,
    }


def _read_non_negative_count(payload: dict[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError) as error:
        raise PageApiSchemaError(
            f"批准 API 响应 {field_name} 不是整数: {value!r}"
        ) from error
    if parsed < 0:
        raise PageApiSchemaError(
            f"批准 API 响应 {field_name} 不能为负数: {parsed}"
        )
    return parsed


def approve_application_api(
    store_id: str,
    apply_id: str,
    *,
    expected_creator_id: str,
    expected_product_id: str,
    context: AffiliatePageContext | None = None,
    preflight_status: dict[str, Any] | None = None,
    request_json: ActionRequest = post_sample_group_action_json,
) -> dict[str, Any]:
    """批准单条申请；执行前强制做只读身份和状态预检。"""
    preflight = preflight_status or check_pending_application_api(
        store_id,
        apply_id,
        expected_creator_id=expected_creator_id,
        expected_product_id=expected_product_id,
    )
    if not preflight.get("ok") or preflight.get("state") != "pending-approvable":
        return {
            "ok": False,
            "state": "preflight-blocked",
            "apply_id": str(apply_id),
            "preflight": preflight,
        }

    raw_status_type = preflight.get("curr_status")
    try:
        status_type = int(raw_status_type)
    except (TypeError, ValueError) as error:
        raise PageApiSchemaError(
            f"批准前预检缺少有效 curr_status: {raw_status_type!r}"
        ) from error

    request_body = build_approve_application_request(
        apply_id,
        status_type=status_type,
    )
    resolved_context = context or get_affiliate_page_context(store_id)
    response_payload = request_json(
        store_id,
        request_body,
        context=resolved_context,
    )
    success_count = _read_non_negative_count(response_payload, "success_count")
    failed_count = _read_non_negative_count(response_payload, "failed_count")
    result = {
        "ok": success_count == 1 and failed_count == 0,
        "state": (
            "action-accepted"
            if success_count == 1 and failed_count == 0
            else "action-failed"
        ),
        "apply_id": str(apply_id),
        "request": request_body,
        "response": {
            "code": response_payload.get("code"),
            "success_count": success_count,
            "failed_count": failed_count,
        },
        "preflight": preflight,
    }
    return result


def confirm_application_approved_api(
    store_id: str,
    apply_id: str,
    *,
    expected_creator_id: str,
    expected_product_id: str,
    context: AffiliatePageContext | None = None,
    max_attempts: int = DEFAULT_APPROVAL_CONFIRM_ATTEMPTS,
    wait_seconds: float = DEFAULT_APPROVAL_CONFIRM_WAIT_SECONDS,
) -> dict[str, Any]:
    """只读确认申请已从待审核转入待发货。

    该函数只轮询列表 API，不会再次调用批准接口。`review_status` 在页面
    列表中可能仍为 0，因此以 `READY_TO_SHIP` tab 和 `curr_status=20` 为准。
    """
    resolved_context = context or get_affiliate_page_context(store_id)
    last_pending: dict[str, Any] = {}
    last_ready: dict[str, Any] = {}

    for attempt in range(max(1, int(max_attempts))):
        last_ready = check_application_in_tab_api(
            store_id,
            apply_id,
            tab=READY_TO_SHIP_TAB,
            expected_creator_id=expected_creator_id,
            expected_product_id=expected_product_id,
            context=resolved_context,
            max_pages=50,
        )
        if (
            last_ready.get("state") != "not-found-in-pending"
            and last_ready.get("curr_status") == READY_TO_SHIP_STATUS
        ):
            return {
                "ok": True,
                "state": "approved",
                "attempt": attempt + 1,
                "ready_to_ship": last_ready,
            }
        if last_ready.get("state") == "identity-mismatch":
            return {
                "ok": False,
                "state": "identity-mismatch",
                "attempt": attempt + 1,
                "ready_to_ship": last_ready,
            }

        last_pending = check_application_in_tab_api(
            store_id,
            apply_id,
            tab=PENDING_TAB,
            expected_creator_id=expected_creator_id,
            expected_product_id=expected_product_id,
            context=resolved_context,
            max_pages=50,
        )
        if last_pending.get("state") == "pending-approvable":
            return {
                "ok": False,
                "state": "still-pending",
                "attempt": attempt + 1,
                "pending": last_pending,
                "ready_to_ship": last_ready,
            }
        if last_pending.get("state") == "identity-mismatch":
            return {
                "ok": False,
                "state": "identity-mismatch",
                "attempt": attempt + 1,
                "pending": last_pending,
                "ready_to_ship": last_ready,
            }

        if attempt + 1 < max(1, int(max_attempts)):
            time.sleep(max(0.1, float(wait_seconds)))

    return {
        "ok": False,
        "state": "unknown",
        "attempt": max(1, int(max_attempts)),
        "pending": last_pending,
        "ready_to_ship": last_ready,
    }
