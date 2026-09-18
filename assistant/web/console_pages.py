"""Console page bootstrap payloads for the Astryx React shell.

服务端只负责把页面数据整理成 JSON（页面 key + data），渲染交给
``frontend/src/console/`` 的 React + Astryx 组件。表单仍提交到既有
``/api/jobs/*`` 与 ``/api/followups/*``，不新增业务写路径。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from assistant.domain.followup_labels import (
    COMPLETED_RESULTS,
    CREATOR_TYPE_LABELS,
    FOLLOWUP_ACTION_LABELS,
    FOLLOWUP_LANGUAGE_LABELS,
    FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS,
    FOLLOWUP_STAGE_LABELS,
    FOLLOWUP_STAGE_LIST_ORDER,
    FOLLOWUP_STAGE_TONES,
    FOLLOWUP_STATUS_LABELS,
    FOLLOWUP_STATUS_TONES,
    REVIEW_REASON_LABELS,
    SENDING_RESULT,
    SUPERSEDED_REASON,
    followup_action_completed,
    followup_action_display,
    followup_action_tone,
    followup_language_label,
    followup_send_result_label,
    followup_status_display,
    followup_status_label,
)
from assistant.domain.followup_stage import (
    ACTION_KIND_ACKNOWLEDGE_CONTENT,
    ACTION_KIND_SEND_MESSAGE,
    days_since_delivery,
    followup_task_completed,
    is_stale_followup_stage,
    latest_due_unpublished_stage,
)
from assistant.domain.platform_status import PLATFORM_STATUS_PROCESSING
from assistant.domain.timeutil import beijing_now


JOB_TYPE_LABELS = {
    "shipment_sync": "物流同步",
    "creator_enrich": "补齐达人资料",
    "followup_generate": "跟进待办生成",
    "content_thanks_preview": "预演已完成感谢私信",
    "daily_refresh": "今日更新",
    "environment_check": "店铺连接检查",
    "report_export": "报表导出",
    "operator_prepare": "打开店铺",
    "operator_screen": "只出名单",
    "operator_pipeline": "筛查批准写飞书发私信",
    "operator_tracking": "获取物流信息写飞书发单号",
    "operator_followup_send": "发送跟进私信",
    "auto_approval_preview": "自动审批 · 只读筛查",
    "auto_approval_execute": "自动审批 · 执行批准",
    "auto_approval_reconcile": "自动审批 · 补写核对",
    "auto_approval_order_backfill": "自动审批 · 补写订单号",
}

JOB_STATUS_LABELS = {
    "pending": "等待执行",
    "running": "正在执行",
    "succeeded": "已完成",
    "failed": "执行失败",
    "cancelled": "已取消",
    "interrupted": "已中断",
}

JOB_STATUS_TONES = {
    "pending": "warning",
    "running": "info",
    "succeeded": "success",
    "failed": "error",
    "cancelled": "neutral",
    "interrupted": "neutral",
}

SHIPMENT_STATUS_LABELS = {
    "delivered": "已送达",
    "in_transit": "运输中",
    "out_for_delivery": "派送中",
    "label_created": "已创建面单",
    "pending_pickup": "待揽收",
    "exception": "物流异常",
    "returned": "已退回",
    "lost": "物流丢失",
    "unknown": "待确认",
}

SHIPMENT_STATUS_TONES = {
    "delivered": "success",
    "in_transit": "blue",
    "out_for_delivery": "cyan",
    "label_created": "warning",
    "pending_pickup": "warning",
    "exception": "error",
    "returned": "error",
    "lost": "error",
    "unknown": "neutral",
}

REPORT_KINDS = [
    ("today", "今日待办"),
    ("day_10_list", "D+10 待出名单"),
    ("logistics_exception", "物流异常"),
    ("needs_review", "需要人工确认"),
]

# Astryx 的 StatusDot 只有 success / warning / error / accent / neutral 五种语义色，
# 旧页面的 teal/blue/purple/orange 等在这里收敛到最接近的一种。
_ASTRYX_TONES = {
    "success": "success",
    "warning": "warning",
    "orange": "warning",
    "error": "error",
    "red": "error",
    "info": "accent",
    "blue": "accent",
    "cyan": "accent",
    "teal": "accent",
    "purple": "accent",
    "pink": "accent",
    "neutral": "neutral",
}


def astryx_tone(tone: str) -> str:
    return _ASTRYX_TONES.get(tone or "", "neutral")


def _display(value: datetime | date | None) -> str:
    return "" if value is None else str(value)


def _job_label(job_type: str) -> str:
    return JOB_TYPE_LABELS.get(job_type, job_type or "")


def job_row(job: Any) -> dict:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "job_label": _job_label(job.job_type),
        "status": job.status,
        "status_label": JOB_STATUS_LABELS.get(job.status, job.status),
        "status_tone": astryx_tone(JOB_STATUS_TONES.get(job.status, "neutral")),
        "created_at": _display(job.created_at),
    }


def job_detail_data(job: Any) -> dict:
    payload = job_row(job)
    payload.update(
        {
            "progress_current": job.progress_current,
            "progress_total": job.progress_total,
            "progress_message": job.progress_message or "",
            "error_summary": job.error_summary or "",
            "error_code": job.error_code or "",
            "is_operator_job": job.job_type
            in {
                "operator_prepare",
                "operator_screen",
                "operator_pipeline",
                "operator_tracking",
                "operator_followup_send",
            },
            "is_active": job.status in {"pending", "running"},
        }
    )
    return payload


def shipment_row(shipment: Any, sample_case: Any) -> dict:
    status_category = shipment.status_category or "unknown"
    needs_confirmation = bool(shipment.needs_delivery_time_confirmation) or status_category == "delivered"
    return {
        "id": shipment.id,
        "creator_name": sample_case.creator_name,
        "main_order_id": sample_case.main_order_id,
        "tracking_display": shipment.tracking_display,
        "status_category": status_category,
        "status_label": SHIPMENT_STATUS_LABELS.get(status_category, status_category),
        "status_tone": astryx_tone(SHIPMENT_STATUS_TONES.get(status_category, "neutral")),
        "estimated_delivery_at": _display(shipment.estimated_delivery_at),
        "delivered_at": _display(shipment.delivered_at),
        "needs_delivery_confirmation": needs_confirmation,
    }


def shipments_data(rows: Iterable[Sequence[Any]], status_filter: str) -> dict:
    return {
        "rows": [shipment_row(shipment, sample_case) for shipment, sample_case in rows],
        "status_filter": status_filter,
        "quick_filters": [
            {"value": "", "label": "全部"},
            {"value": "delivered", "label": "已送达"},
            {"value": "in_transit", "label": "运输中"},
            {"value": "out_for_delivery", "label": "派送中"},
            {"value": "exception", "label": "异常"},
        ],
    }


def shipment_detail_data(shipment: Any, sample_case: Any) -> dict:
    payload = shipment_row(shipment, sample_case)
    payload["creator_id"] = sample_case.creator_id
    return payload


def _followup_row(task: Any, sample_case: Any) -> dict:
    action_completed = followup_action_completed(
        sent_at=task.sent_at,
        send_result=task.send_result,
    )
    status_display = followup_status_display(
        task.status,
        suppressed_reason=task.suppressed_reason,
    )
    if status_display == "-":
        status_tone = "neutral"
    elif task.status == "needs_review":
        status_tone = "orange"
    else:
        status_tone = FOLLOWUP_STATUS_TONES.get(task.status, "neutral")
    return {
        "id": task.id,
        "creator_name": sample_case.creator_name,
        "stage": task.stage,
        "stage_label": FOLLOWUP_STAGE_LABELS.get(task.stage, task.stage),
        "stage_tone": astryx_tone(FOLLOWUP_STAGE_TONES.get(task.stage, "neutral")),
        "action_kind": task.action_kind,
        "action_label": followup_action_display(
            task.action_kind,
            sent_at=task.sent_at,
            send_result=task.send_result,
            status=task.status,
            suppressed_reason=task.suppressed_reason,
        ),
        "action_tone": astryx_tone(
            followup_action_tone(
                task.action_kind,
                sent_at=task.sent_at,
                send_result=task.send_result,
                status=task.status,
                suppressed_reason=task.suppressed_reason,
            )
        ),
        "action_completed": action_completed,
        "status": task.status,
        "status_label": status_display,
        "status_tone": astryx_tone(status_tone),
        "status_tooltip": task.review_reason if task.status == "needs_review" else "",
        "language_label": followup_language_label(task.language),
        "platform_status_label": (
            sample_case.platform_status_label
            or FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS.get(
                sample_case.platform_status,
                sample_case.platform_status,
            )
        ),
    }


def followups_data(
    rows: Iterable[Sequence[Any]],
    *,
    filters: dict,
    filter_options: dict[str, list[tuple[str, str]]],
) -> dict:
    return {
        "rows": [_followup_row(task, sample_case) for task, sample_case in rows],
        "filters": filters,
        "operator_groups": operator_groups("followups"),
        "filter_options": {
            "stages": [{"value": value, "label": label} for value, label in filter_options["stages"]],
            "statuses": [
                {"value": value, "label": label} for value, label in filter_options["statuses"]
            ],
            "languages": [
                {"value": value, "label": label} for value, label in filter_options["languages"]
            ],
            "platform_statuses": [
                {"value": value, "label": label}
                for value, label in filter_options["platform_statuses"]
            ],
        },
    }


def _followup_send_ready(task: Any, sample_case: Any, shipment: Any | None) -> bool:
    """Mirror the send script selector so the real-send button only shows when valid.

    The script re-validates everything before sending; this is display-only.
    """
    if str(task.status) != "pending":
        return False
    if task.sent_at is not None:
        return False
    send_result = str(task.send_result or "")
    if send_result in COMPLETED_RESULTS or send_result == SENDING_RESULT:
        return False
    if bool(task.requires_manual_confirmation):
        return False
    if task.scheduled_for is None or task.scheduled_for > beijing_now().date():
        return False
    if not str(task.template_key or "").strip():
        return False
    if not str(task.message_preview or "").strip():
        return False
    if str(task.action_kind or "") not in {
        ACTION_KIND_SEND_MESSAGE,
        ACTION_KIND_ACKNOWLEDGE_CONTENT,
    }:
        return False
    if str(task.stage) == "content_found":
        return True
    if _followup_stage_stale(task, shipment):
        return False
    return (
        str(sample_case.platform_status or "") == PLATFORM_STATUS_PROCESSING
        and not bool(sample_case.platform_status_stale)
    )


def _followup_stage_stale(task: Any, shipment: Any | None) -> bool:
    """True when a later calendar node is already due for this task's stage."""
    delivered_at = getattr(shipment, "delivered_at", None) if shipment else None
    days = days_since_delivery(delivered_at) if delivered_at is not None else None
    return is_stale_followup_stage(stage=str(task.stage or ""), days=days)


def _followup_preview_status(task: Any) -> tuple[str, str, str]:
    """Last preview/attempt outcome for the detail page (display only).

    ``send_confirmation == "dry-run"`` is written only by the preview endpoint,
    so it can be reported as a successful preview. ``already-sent`` means the
    conversation already carries this copy (preview or send-time check), which
    must be shown as a warning instead of an invitation to send again. Any
    ``last_error`` is surfaced as-is, because it may come from a preview or
    from a send attempt.
    """
    send_confirmation = str(task.send_confirmation or "")
    last_error = str(task.last_error or "")
    previewed_at = getattr(task, "previewed_at", None)
    preview_at = (
        previewed_at.strftime("%Y-%m-%d %H:%M")
        if isinstance(previewed_at, datetime)
        else ""
    )
    if send_confirmation == "already-sent" and not last_error:
        return (
            "issue",
            "会话里已有我方发出的消息（本阶段窗口内）或同一话术："
            "视为已发送，不要重复发送",
            preview_at,
        )
    if send_confirmation == "dry-run" and not last_error:
        return "ok", "预演成功：已打开会话并核对身份，未发送", preview_at
    if last_error:
        return "issue", f"最近一次尝试未通过：{last_error}", preview_at
    return "", "", ""


def _followup_current_stage(
    task: Any,
    sample_case: Any,
    shipment: Any | None,
    case_tasks: Iterable[dict] | None,
) -> tuple[str, str, str, str]:
    """Return (label, url, note, due) for the case's current calendar node.

    The detail page shows the stage of one task record; this tells the operator
    which node the case should actually be worked on right now, so a superseded
    record cannot be mistaken for the current step. ``due`` spells out the
    scheduled date and how late that node is, so a node name such as D+3 is not
    read as "today is day 3".
    """
    if str(getattr(sample_case, "platform_status", "") or "") != PLATFORM_STATUS_PROCESSING:
        return "", "", "", ""
    delivered_at = getattr(shipment, "delivered_at", None) if shipment else None
    if delivered_at is None:
        return "", "", "", ""
    days = days_since_delivery(delivered_at)
    latest = latest_due_unpublished_stage(days)
    if latest is None:
        return "", "", "", ""
    stage, offset_days = latest
    label = FOLLOWUP_STAGE_LABELS.get(stage, stage)
    overdue_days = days - offset_days
    due_date = beijing_now().date() - timedelta(days=overdue_days)
    if overdue_days > 0:
        due = f"应做 {due_date:%m-%d} · 已逾期 {overdue_days} 天"
    elif overdue_days == 0:
        due = f"应做 {due_date:%m-%d}（今天）"
    else:
        due = f"应做 {due_date:%m-%d}"
    candidates = sorted(
        (
            item
            for item in (case_tasks or [])
            if str(item.get("stage") or "") == stage
        ),
        key=lambda item: (str(item.get("scheduled_for") or ""), int(item.get("id") or 0)),
    )
    pending = [item for item in candidates if not followup_task_completed(item)]
    if pending:
        target = pending[-1]
        if int(target.get("id") or 0) == int(getattr(task, "id", 0) or 0):
            return label, "", "就是本条（当前应做的一步）", due
        return label, f"/followups/{target['id']}", "打开当前应做的一步", due
    if candidates:
        return label, "", "本节点已处理", due
    return label, "", "尚未生成：先点「生成今日跟进待办」", due


def _followup_delivered_text(shipment: Any | None) -> str:
    """Beijing-time delivery display with the elapsed natural days.

    The raw column is UTC; showing it as-is made the day count (computed in
    Beijing time) look wrong.
    """
    delivered_at = getattr(shipment, "delivered_at", None) if shipment else None
    if delivered_at is None:
        return ""
    local = beijing_now(delivered_at)
    days = days_since_delivery(delivered_at)
    return f"{local:%Y-%m-%d %H:%M}（北京时间）· 到货已 {days} 天"


def followup_detail_data(
    task: Any,
    sample_case: Any,
    shipment: Any | None,
    *,
    attachment_url: str,
    scheduled_label: str,
    case_tasks: Iterable[dict] | None = None,
) -> dict:
    payload = _followup_row(task, sample_case)
    preview_state, preview_label, preview_at = _followup_preview_status(task)
    stage_stale = _followup_stage_stale(task, shipment)
    current_label, current_url, current_note, current_due = _followup_current_stage(
        task,
        sample_case,
        shipment,
        case_tasks,
    )
    payload.update(
        {
            "product_id": sample_case.product_id,
            "main_order_id": sample_case.main_order_id,
            "tracking_display": shipment.tracking_display if shipment else "",
            "delivered_at": _display(shipment.delivered_at) if shipment else "",
            "delivered_text": _followup_delivered_text(shipment),
            "scheduled_label": scheduled_label,
            "platform_status_text": sample_case.platform_status_label or str(sample_case.curr_status),
            "creator_type_label": CREATOR_TYPE_LABELS.get(
                task.creator_type,
                task.creator_type or "未定",
            ),
            "message_preview": task.message_preview or "",
            "attachment_url": attachment_url,
            "note": _followup_note(task, stale_stage=stage_stale),
            "current_stage_label": current_label,
            "current_stage_due": current_due,
            "current_task_url": current_url,
            "current_stage_note": current_note,
            "review_label": REVIEW_REASON_LABELS.get(
                task.review_reason,
                task.review_reason or "需要人工确认后再继续。",
            ),
            "send_result": task.send_result or "",
            "send_result_label": followup_send_result_label(task.send_result),
            "preview_state": preview_state,
            "preview_label": preview_label,
            "preview_at": preview_at,
            "send_ready": _followup_send_ready(task, sample_case, shipment),
            "can_send": task.action_kind == "send_message",
            "can_acknowledge": task.action_kind == ACTION_KIND_ACKNOWLEDGE_CONTENT,
            "can_list": task.action_kind == "list_only",
            "is_unfulfilled_stage": task.stage == "unfulfilled",
        }
    )
    return payload


def _followup_note(task: Any, *, stale_stage: bool = False) -> str:
    if task.status == "suppressed" and task.suppressed_reason == SUPERSEDED_REASON:
        return "此前生成的跟进步骤已由更新的日历节点取代，不再需要处理。"
    if task.status == "suppressed":
        return f"此前生成的跟进步骤已不再适用（{followup_status_label(task.status, suppressed_reason=task.suppressed_reason)}）。"
    if task.status == "needs_review":
        return REVIEW_REASON_LABELS.get(task.review_reason, task.review_reason or "需要人工确认后再继续。")
    if stale_stage:
        return "该阶段已过期（已有更晚的日历节点到期），不会自动发送：请先「生成今日跟进待办」，或人工跳过本条。"
    return ""


def operator_groups(page: str) -> list[dict]:
    """按页面返回操作面板结构（含确认口令与提交地址），React 只负责渲染。

    正式 SOP 的「筛查-批准-写飞书-私信」已从网页移除，只保留脚本入口；
    网页只登记 prepare / screen / tracking 这些固定任务，不新增参数。
    """
    groups = {
        "prepare": [
            {
                "key": "prepare",
                "heading": "打开店铺",
                "note": "运行自动化脚本时，只保留 1 家店铺（2 号店），请勿同时操作店铺页面或者关闭紫鸟浏览器。",
                "items": [
                    {
                        "title": "打开店铺",
                        "description": "已运行的目标店会先关闭再打开，并带上调试口；不会筛查、批准、写飞书或发私信。",
                        "action": "/api/jobs/operator/prepare",
                        "job_label": "打开店铺",
                        "button_label": "打开店铺",
                        "variant": "primary",
                        "index": "",
                        "kind": "",
                        "is_prepare": True,
                        "href": "",
                        "confirm": None,
                    }
                ],
            }
        ],
        "approval": [
            {
                "key": "standard_screen",
                "heading": "标准 SOP 只读名单",
                "note": "正式筛查与批准只走脚本（0/1/2/3）；这里只导出待批准名单，不会批准、写飞书或发送私信。",
                "items": [
                    {
                        "title": '只出"待批准名单"（测试用）',
                        "description": "从已登录商家中心进入待审核，按正式条件完成初筛和详情复筛，仅导出待批准名单；不会批准、写飞书或发送私信。",
                        "action": "/api/jobs/operator/screen",
                        "job_label": "只出名单",
                        "button_label": "执行",
                        "variant": "secondary",
                        "index": "1",
                        "kind": "只读",
                        "is_prepare": False,
                        "href": "",
                        "confirm": None,
                    }
                ],
            }
        ],
        "followups": [
            {
                "key": "logistics",
                "heading": "物流与单号",
                "note": "物流写回和私信默认 16:00 前拒绝；已发私信无法撤回。",
                "items": [
                    {
                        "title": "同步物流与到货状态",
                        "description": "读取免费样品物流和 Delivered 到货时间并更新本地数据；不受 16:00 限制，不写飞书、不发私信。",
                        "action": "/api/jobs/shipment-sync",
                        "job_label": "物流同步",
                        "button_label": "执行",
                        "variant": "secondary",
                        "index": "",
                        "kind": "只读",
                        "is_prepare": False,
                        "href": "",
                        "confirm": None,
                    },
                    {
                        "title": "更新物流信息到飞书表格-给达人发私信",
                        "description": "读取已发货记录，把 TikTok 物流单号写回飞书并发送给达人；北京时间 16:00 前默认拒绝。",
                        "action": "/api/jobs/operator/tracking",
                        "job_label": "获取物流信息写飞书发单号",
                        "button_label": "执行",
                        "variant": "primary",
                        "index": "",
                        "kind": "danger",
                        "is_prepare": False,
                        "href": "",
                        "confirm": {
                            "token": "y",
                            "title": "确认写飞书并发送物流单号",
                            "description": "本轮物流私信不限条数。只有飞书回写成功或已有相同值后才发送，已发私信无法撤回。",
                            "action": "写入并发送",
                        },
                        "force_gate": "tracking",
                    },
                ],
            },
            {
                "key": "creator",
                "heading": "达人跟进",
                "note": "",
                "items": [
                    {
                        "title": "补齐达人资料",
                        "description": "对处理中达人补齐缺失的视频/直播类型与语言（飞书使用语言优先，无值则读详情简介缓存本地）；只读，不批准、不发私信、不写飞书。",
                        "action": "/api/jobs/creator-enrich",
                        "job_label": "补齐达人资料",
                        "button_label": "执行",
                        "variant": "secondary",
                        "index": "",
                        "kind": "只读",
                        "is_prepare": False,
                        "href": "",
                        "confirm": None,
                    },
                    {
                        "title": "生成今日跟进待办（只读）",
                        "description": "使用本地到货数据生成 D0、D+3、D+7、D+10、D+15 待办和话术预览。确认内容或未履约后，只有勾选写飞书开关才会改合作状态；默认不发私信。",
                        "action": "/api/jobs/followup-generate",
                        "job_label": "跟进待办生成（只读）",
                        "button_label": "执行",
                        "variant": "secondary",
                        "index": "",
                        "kind": "本地",
                        "is_prepare": False,
                        "href": "",
                        "confirm": None,
                    },
                    {
                        "title": "预演已完成感谢私信（只读）",
                        "description": "读取飞书近 30 天「待发布」达人，在免费样品【已完成】按达人搜到达人后，用平台内容接口读出视频/直播数量并按此选话术。当前只预演打开会话，不发送、不改飞书。只有能确认过去 14 天内已经发过感谢类私信才跳过，其余情况仍预演要发。",
                        "action": "/api/jobs/content-thanks-preview",
                        "job_label": "预演已完成感谢私信（只读）",
                        "button_label": "执行",
                        "variant": "secondary",
                        "index": "",
                        "kind": "预演",
                        "is_prepare": False,
                        "href": "",
                        "confirm": None,
                    },
                ],
            },
        ],
    }
    return groups.get(page, [])


def overview_data(
    *,
    data_directory: str,
    database_ready: bool,
    store_summary: dict,
    dashboard: dict,
) -> dict:
    """总览页：只读计数 + 准备状态摘要，不放任何操作入口。"""
    queue_items = [
        {"label": "等待确认送达或仍在运输", "href": "/shipments", "count": dashboard.get("waiting_delivery", 0), "tone": "neutral"},
        {"label": "今日到货提醒", "href": "/followups?stage=arrival", "count": dashboard.get("arrival", 0), "tone": "neutral"},
        {"label": "D+3 跟进", "href": "/followups?stage=day_3", "count": dashboard.get("day_3", 0), "tone": "neutral"},
        {"label": "D+7 跟进", "href": "/followups?stage=day_7", "count": dashboard.get("day_7", 0), "tone": "neutral"},
        {"label": "D+10 待出名单", "href": "/followups?stage=day_10_list", "count": dashboard.get("day_10_list", 0), "tone": "neutral"},
        {"label": "D+15 未发布", "href": "/followups?stage=unfulfilled", "count": dashboard.get("unfulfilled", 0), "tone": "neutral"},
        {"label": "需要人工确认", "href": "/followups?status=needs_review", "count": dashboard.get("needs_review", 0), "tone": "warning"},
        {"label": "物流异常", "href": "/shipments", "count": dashboard.get("logistics_exceptions", 0), "tone": "error"},
        {"label": "失败任务", "href": "/jobs", "count": dashboard.get("failed_jobs", 0), "tone": "error"},
    ]
    return {
        "data_directory": data_directory,
        "database_ready": database_ready,
        "store_summary": store_summary,
        "queue_items": queue_items,
    }


def prepare_data(
    *,
    data_directory: str,
    database_ready: bool,
    store_summary: dict,
) -> dict:
    """运行准备页：调试口状态 + 检查环境 + 打开店铺。"""
    return {
        "data_directory": data_directory,
        "database_ready": database_ready,
        "store_summary": store_summary,
        "operator_groups": operator_groups("prepare"),
    }


def approval_page_data() -> dict:
    """自动批准页顶部注入的标准 SOP 只读名单入口。"""
    groups = operator_groups("approval")
    return {
        "screen_group": groups[0] if groups else None,
        "prepare_href": "/prepare",
    }


def diagnostics_data(
    *,
    python_version: str,
    cli_state: str,
    bridge_state: str,
    config_exists: bool,
    feishu_configured: bool,
    store_summary: dict,
) -> dict:
    return {
        "python_version": python_version,
        "cli_state": cli_state,
        "bridge_state": bridge_state,
        "config_exists": config_exists,
        "feishu_configured": feishu_configured,
        "stores": store_summary.get("stores", []),
        "store_error": store_summary.get("error", ""),
    }


def reports_data() -> dict:
    return {
        "report_kinds": [{"value": value, "label": label} for value, label in REPORT_KINDS],
    }


def followup_filter_labels() -> dict:
    """给前端 Selector 用的标签表（避免前端再抄一份中文）。"""
    return {
        "stages": {value: FOLLOWUP_STAGE_LABELS[value] for value in FOLLOWUP_STAGE_LIST_ORDER},
        "statuses": FOLLOWUP_STATUS_LABELS,
        "languages": FOLLOWUP_LANGUAGE_LABELS,
        "platform_statuses": FOLLOWUP_PLATFORM_STATUS_FILTER_LABELS,
        "actions": FOLLOWUP_ACTION_LABELS,
    }
