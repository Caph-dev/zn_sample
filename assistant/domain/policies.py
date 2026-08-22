"""Selection and idempotency policies for follow-up work."""
from __future__ import annotations

from datetime import date

from scripts.lib.detect_lang import detect_creator_lang


def choose_followup_creator_type(
    *,
    manual_type: str | None,
    is_video_creator: str = "",
    is_live_creator: str = "",
) -> dict:
    """Choose one creator type, leaving ambiguous exports for human review."""
    if manual_type in {"video", "live"}:
        return {"creator_type": manual_type, "needs_review": False}

    is_video = is_video_creator == "是"
    is_live = is_live_creator == "是"
    if is_video and not is_live:
        return {"creator_type": "video", "needs_review": False}
    if is_live and not is_video:
        return {"creator_type": "live", "needs_review": False}
    return {"creator_type": None, "needs_review": True}


def choose_followup_language(
    *,
    bio: str | None,
    feishu_lang: str | None = None,
    manual_lang: str | None = None,
) -> dict:
    """Choose English or Spanish using manual, Feishu, then bio priority."""
    if manual_lang in {"en", "es"}:
        return {
            "lang": manual_lang,
            "needs_review": False,
            "reason": "manual_override",
        }

    feishu_language_codes = {"英语": "en", "西班牙语": "es"}
    if feishu_lang in feishu_language_codes:
        return {
            "lang": feishu_language_codes[feishu_lang],
            "needs_review": False,
            "reason": "feishu_language",
        }

    detected_language = detect_creator_lang(bio)
    if detected_language["confidence"] == "high":
        return {
            "lang": detected_language["lang"],
            "needs_review": False,
            "reason": detected_language["reason"],
        }
    return {
        "lang": "en",
        "needs_review": True,
        "reason": detected_language["reason"],
    }


def followup_task_idempotency_key(
    sample_case_id: str,
    stage: str,
    scheduled_for: date,
) -> str:
    """Return the stable identity for one scheduled follow-up task."""
    return f"{sample_case_id}|{stage}|{scheduled_for:%Y-%m-%d}"


def message_send_idempotency_key(
    store_id: str,
    creator_id: str,
    product_id: str,
    stage: str,
    scheduled_for: date,
) -> str:
    """Return the stable identity for one platform message send."""
    return (
        f"{store_id}|{creator_id}|{product_id}|{stage}|"
        f"{scheduled_for:%Y-%m-%d}"
    )


def feishu_update_idempotency_key(
    feishu_record_id: str,
    target_status: str,
    content_evidence_id: str,
) -> str:
    """Return the stable identity for one Feishu status transition."""
    return f"{feishu_record_id}|{target_status}|{content_evidence_id}"
