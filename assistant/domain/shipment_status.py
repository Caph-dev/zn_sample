"""Fail-closed normalization for shipment status labels."""
from __future__ import annotations

import re
from datetime import datetime


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


def normalize_shipment_status(
    *,
    status_label: str | None,
    delivered_at: datetime | None,
) -> dict:
    """Normalize a platform status without inferring delivery from shipment."""
    original_label = status_label or ""
    folded_label = re.sub(r"\s+", " ", original_label.strip().lower())

    if delivered_at is not None:
        status_category = "delivered"
    elif "已送达" in original_label or folded_label in {"delivered", "delivered."}:
        status_category = "delivered"
    elif folded_label in {"label created", "label_created", "packed", "order placed"}:
        status_category = "label_created"
    elif "待揽收" in original_label or folded_label in {"pending pickup", "pending_pickup"}:
        status_category = "pending_pickup"
    elif "运输中" in original_label or folded_label in {"in transit", "in_transit"}:
        status_category = "in_transit"
    elif "派送中" in original_label or folded_label in {"out for delivery", "out_for_delivery"}:
        status_category = "out_for_delivery"
    elif "退回" in original_label or folded_label in {"returned", "return received"}:
        status_category = "returned"
    elif "丢失" in original_label or folded_label == "lost":
        status_category = "lost"
    elif "异常" in original_label or folded_label in {
        "exception",
        "delivery canceled",
        "couldn't deliver",
        "could not deliver",
    }:
        status_category = "exception"
    else:
        status_category = "unknown"

    return {
        "status_category": status_category,
        "delivered_at": delivered_at,
        "needs_delivery_time_confirmation": (
            status_category == "delivered" and delivered_at is None
        ),
    }
