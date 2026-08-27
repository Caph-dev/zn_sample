"""Selection and idempotency policies for follow-up work."""
from __future__ import annotations

from datetime import date

from scripts.lib.detect_lang import detect_creator_lang


CREATOR_TYPE_VIDEO = "video"
CREATOR_TYPE_LIVE = "live"
CREATOR_TYPE_BOTH = "both"
CREATOR_TYPE_UNKNOWN = "unknown"
FOLLOWUP_CREATOR_TYPES = frozenset(
    {CREATOR_TYPE_VIDEO, CREATOR_TYPE_LIVE, CREATOR_TYPE_BOTH, CREATOR_TYPE_UNKNOWN}
)
MESSAGE_CREATOR_TYPES = frozenset({CREATOR_TYPE_VIDEO, CREATOR_TYPE_LIVE})
UPSTREAM_CREATOR_TYPE_ALIASES = {
    "video": CREATOR_TYPE_VIDEO,
    "live": CREATOR_TYPE_LIVE,
    "both": CREATOR_TYPE_BOTH,
    "unknown": CREATOR_TYPE_UNKNOWN,
    "视频达人": CREATOR_TYPE_VIDEO,
    "直播达人": CREATOR_TYPE_LIVE,
    "视频+直播": CREATOR_TYPE_BOTH,
    "视频达人+直播达人": CREATOR_TYPE_BOTH,
}


def normalize_creator_type(value: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return UPSTREAM_CREATOR_TYPE_ALIASES.get(raw, raw if raw in FOLLOWUP_CREATOR_TYPES else None)


def followup_message_creator_type(creator_type: str | None) -> str | None:
    """Map stored creator type to the template family used for follow-up DMs.

    Dual-marked creators stay ``both`` on the case, but follow-up copy uses the
    video-creator templates unless a person explicitly chose live.
    """
    if creator_type == CREATOR_TYPE_BOTH:
        return CREATOR_TYPE_VIDEO
    return creator_type


def choose_followup_creator_type(
    *,
    manual_type: str | None,
    is_video_creator: str = "",
    is_live_creator: str = "",
) -> dict:
    """Choose the stored creator type for a follow-up task.

    Dual-marked creators stay ``both`` and do not need type review. Follow-up
    message templates treat ``both`` as video unless a person picked live.
    """
    normalized_manual = normalize_creator_type(manual_type)
    if normalized_manual in MESSAGE_CREATOR_TYPES:
        return {
            "creator_type": normalized_manual,
            "needs_review": False,
            "review_reason": "",
        }

    is_video = is_video_creator == "是"
    is_live = is_live_creator == "是"
    if is_video and is_live:
        return {
            "creator_type": CREATOR_TYPE_BOTH,
            "needs_review": False,
            "review_reason": "",
        }
    if is_video and not is_live:
        return {
            "creator_type": CREATOR_TYPE_VIDEO,
            "needs_review": False,
            "review_reason": "",
        }
    if is_live and not is_video:
        return {
            "creator_type": CREATOR_TYPE_LIVE,
            "needs_review": False,
            "review_reason": "",
        }
    if normalized_manual == CREATOR_TYPE_BOTH:
        return {
            "creator_type": CREATOR_TYPE_BOTH,
            "needs_review": False,
            "review_reason": "",
        }
    return {
        "creator_type": CREATOR_TYPE_UNKNOWN,
        "needs_review": True,
        "review_reason": "missing_creator_type",
    }


def choose_followup_language(
    *,
    bio: str | None,
    feishu_lang: str | None = None,
    manual_lang: str | None = None,
) -> dict:
    """Choose English or Spanish using manual, Feishu, then bio priority.

    Bio fallback uses the LLM ``lang`` field as-is. Confidence is ignored.
    Empty bio, LLM failure, or an invalid lang fall back to English.
    """
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
    lang = detected_language.get("lang")
    if lang not in {"en", "es"}:
        lang = "en"
    return {
        "lang": lang,
        "needs_review": False,
        "reason": detected_language.get("reason") or "empty-bio-default-en",
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
