"""Public pure-function interface for the follow-up domain."""

from .followup_stage import (
    ACTIVE_TASK_STATUSES,
    FEISHU_COMPLETED_COOPERATION_STATUS,
    FEISHU_UNFULFILLED_COOPERATION_STATUS,
    action_kind_for_stage,
    days_since_delivery,
    followup_task_completed,
    latest_due_unpublished_stage,
    plan_followup_mutation,
)
from .platform_status import (
    is_processing_followup_case,
    normalize_platform_status,
)
from .followup_labels import (
    FOLLOWUP_ACTION_LABELS,
    FOLLOWUP_STAGE_LABELS,
    FOLLOWUP_STATUS_LABELS,
    followup_action_display,
    followup_action_tone,
    followup_status_display,
    followup_status_label,
)
from .message_templates import (
    TEMPLATE_VERSION,
    choose_template_key,
    render_followup_message,
)
from .policies import (
    choose_followup_creator_type,
    choose_followup_language,
    followup_message_creator_type,
    feishu_update_idempotency_key,
    followup_task_idempotency_key,
    message_send_idempotency_key,
)
from .shipment_status import normalize_shipment_status
from .sku_images import FOLLOWUP_IMAGE_PRODUCT_ID, resolve_followup_attachment
from .timeutil import beijing_date, beijing_now


__all__ = [
    "beijing_now",
    "beijing_date",
    "normalize_shipment_status",
    "days_since_delivery",
    "latest_due_unpublished_stage",
    "plan_followup_mutation",
    "action_kind_for_stage",
    "followup_task_completed",
    "ACTIVE_TASK_STATUSES",
    "FEISHU_COMPLETED_COOPERATION_STATUS",
    "normalize_platform_status",
    "is_processing_followup_case",
    "FOLLOWUP_ACTION_LABELS",
    "FOLLOWUP_STAGE_LABELS",
    "FOLLOWUP_STATUS_LABELS",
    "followup_action_display",
    "followup_action_tone",
    "followup_status_display",
    "followup_status_label",
    "choose_followup_creator_type",
    "choose_followup_language",
    "followup_message_creator_type",
    "followup_task_idempotency_key",
    "message_send_idempotency_key",
    "feishu_update_idempotency_key",
    "resolve_followup_attachment",
    "choose_template_key",
    "render_followup_message",
    "TEMPLATE_VERSION",
    "FOLLOWUP_IMAGE_PRODUCT_ID",
    "FEISHU_UNFULFILLED_COOPERATION_STATUS",
]
