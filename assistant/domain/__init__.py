"""Public pure-function interface for the follow-up domain."""

from .followup_stage import (
    FEISHU_UNFULFILLED_COOPERATION_STATUS,
    days_since_delivery,
    latest_due_unpublished_stage,
    plan_followup_mutation,
)
from .message_templates import (
    TEMPLATE_VERSION,
    choose_template_key,
    render_followup_message,
)
from .policies import (
    choose_followup_creator_type,
    choose_followup_language,
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
    "choose_followup_creator_type",
    "choose_followup_language",
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
