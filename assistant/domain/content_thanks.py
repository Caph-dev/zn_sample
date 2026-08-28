"""Decide whether a completed-sample thanks DM is safe to preview.

SOP 2: Feishu 待发布 + recent outreach → search 免费样品【已完成】→ open
「查看内容」→ send video/live thanks. Content type comes from the panel,
not from the stored 视频/直播达人 flags. Video+live → video copy.

Dedup: skip only when a thanks-like DM is confirmed inside the past 14
Beijing days. Missing dates, older copy, or colleague-style thanks without
a recent timestamp still preview as send.

Development/test default: never send, never write Feishu.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from assistant.domain.message_templates import (
    choose_template_key,
    looks_like_any_thanks,
    render_followup_message,
)
from assistant.domain.policies import choose_followup_language
from assistant.domain.timeutil import beijing_date, beijing_now
from scripts.lib.feishu_bitable import (
    COOPERATION_STATUS_COMPLETED,
    COOPERATION_STATUS_PENDING_POST,
    COOPERATION_STATUS_PUBLISHED_LEGACY,
)


CONTENT_THANKS_LOOKBACK_DAYS = 30
RECENT_THANKS_WINDOW_DAYS = 14
CONTENT_THANKS_STAGE = "content_found"
FEISHU_CONTENT_THANKS_STATUS = COOPERATION_STATUS_COMPLETED

DECISION_PREVIEW = "preview-send"
DECISION_ALREADY_SENT = "already-sent"
DECISION_HOLD = "hold"
DECISION_SKIP = "skip"

VIDEO_CONTENT_MARKERS = ("视频", "video")
LIVE_CONTENT_MARKERS = ("直播", "live")
_DRAWER_COUNT_PATTERN = re.compile(
    r"视频\s+(\d+)\s+直播\s+(\d+)|video\s+(\d+)\s+live\s+(\d+)",
    re.IGNORECASE,
)


def content_thanks_since(now: datetime | None = None) -> datetime:
    """Return the inclusive Beijing-clock cutoff for the 30-day outreach window."""
    return beijing_now(now) - timedelta(days=CONTENT_THANKS_LOOKBACK_DAYS)


def classify_completed_content(
    *,
    panel_text: str = "",
    links: list[str] | None = None,
    video_count: int | None = None,
    live_count: int | None = None,
) -> dict:
    """Classify 查看内容 from the content-detail drawer, not tab labels.

    Measured drawer copy is ``内容详情 / 视频 N / 直播 M``. Tab titles always
    contain both words, so a bare 视频/直播 match is not evidence.

    ``video_count`` / ``live_count`` come from the sample/performance API and
    take precedence over text parsing when provided.
    """
    raw = str(panel_text or "")
    compact = re.sub(r"\s+", " ", raw).strip()
    hrefs = [str(link or "").lower() for link in (links or [])]
    has_video_link = any("/video/" in href for href in hrefs)
    has_live_link = any("/live" in href for href in hrefs)
    has_view_video = "在tiktok查看视频" in compact.lower() or "view video on tiktok" in compact.lower()
    has_view_live = "在tiktok查看直播" in compact.lower() or "view live on tiktok" in compact.lower()
    if video_count is None or live_count is None:
        count_match = _DRAWER_COUNT_PATTERN.search(compact)
        if count_match:
            if count_match.group(1) is not None:
                video_count = int(count_match.group(1))
                live_count = int(count_match.group(2))
            else:
                video_count = int(count_match.group(3))
                live_count = int(count_match.group(4))
    has_video = has_video_link or has_view_video or (video_count is not None and video_count > 0)
    has_live = has_live_link or has_view_live or (live_count is not None and live_count > 0)
    if has_video and has_live:
        return {
            "status": "classified",
            "content_type": "video",
            "reason": "both-prefer-video",
            "video_count": video_count,
            "live_count": live_count,
        }
    if has_video:
        return {
            "status": "classified",
            "content_type": "video",
            "reason": "video-only",
            "video_count": video_count,
            "live_count": live_count,
        }
    if has_live:
        return {
            "status": "classified",
            "content_type": "live",
            "reason": "live-only",
            "video_count": video_count,
            "live_count": live_count,
        }
    if video_count is not None and live_count is not None and video_count == 0 and live_count == 0:
        return {
            "status": "hold",
            "content_type": "",
            "reason": "no-content-found",
            "video_count": video_count,
            "live_count": live_count,
        }
    return {
        "status": "hold",
        "content_type": "",
        "reason": "content-type-uncertain",
        "video_count": video_count,
        "live_count": live_count,
    }


def match_completed_rows(
    rows: list[dict],
    *,
    creator_handle: str,
    creator_id: str = "",
) -> dict:
    """Require a unique creator match. 0 or mixed identities → hold."""
    handle = str(creator_handle or "").strip().lower()
    wanted_id = str(creator_id or "").strip()
    matched: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("creator_name") or "").strip().lower()
        nickname = str(row.get("nick_name") or "").strip().lower()
        row_creator_id = str(row.get("creator_id") or "").strip()
        if handle and handle in {name, nickname}:
            matched.append(row)
            continue
        if wanted_id and row_creator_id == wanted_id:
            matched.append(row)
    identities = {
        str(row.get("creator_id") or row.get("creator_name") or "").strip().lower()
        for row in matched
        if str(row.get("creator_id") or row.get("creator_name") or "").strip()
    }
    if not matched:
        return {
            "status": "none",
            "rows": [],
            "reason": "not-found-in-completed",
        }
    if len(identities) != 1:
        return {
            "status": "ambiguous",
            "rows": matched,
            "reason": "ambiguous-completed-match",
        }
    return {
        "status": "unique",
        "rows": matched,
        "reason": "",
    }


_MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_RELATIVE_TODAY = re.compile(
    r"刚刚|刚才|今天|today|just now",
    re.IGNORECASE,
)
_RELATIVE_YESTERDAY = re.compile(r"昨天|yesterday", re.IGNORECASE)
_RELATIVE_MINUTES = re.compile(
    r"(\d+)\s*(?:分钟前|mins?|minutes?|m)\b",
    re.IGNORECASE,
)
_RELATIVE_HOURS = re.compile(
    r"(\d+)\s*(?:小时前|hrs?|hours?|h)\b",
    re.IGNORECASE,
)
_RELATIVE_DAYS = re.compile(
    r"(\d+)\s*(?:天前|days?|d)\b",
    re.IGNORECASE,
)

_WEEKDAY_ALIASES = {
    "一": 0, "1": 0, "mon": 0, "monday": 0,
    "二": 1, "2": 1, "tue": 1, "tues": 1, "tuesday": 1,
    "三": 2, "3": 2, "wed": 2, "wednesday": 2,
    "四": 3, "4": 3, "thu": 3, "thur": 3, "thurs": 3, "thursday": 3,
    "五": 4, "5": 4, "fri": 4, "friday": 4,
    "六": 5, "6": 5, "sat": 5, "saturday": 5,
    "日": 6, "天": 6, "7": 6, "sun": 6, "sunday": 6,
}
_RELATIVE_WEEKDAY = re.compile(
    r"(?:星期|礼拜|周)([一二三四五六日天])"
    r"|(?:^|[\s（(])(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tues?|wed|thurs?|fri|sat|sun)[\s，,：:.）)]",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b")
_CHINESE_DATE = re.compile(r"(\d{1,2})月(\d{1,2})日")
_MONTH_NAME_DATE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|"
    r"dec(?:ember)?)\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})\b")


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_with_current_year(month: int, day: int, today: date) -> date | None:
    parsed = _valid_date(today.year, month, day)
    if parsed is None:
        return None
    if parsed > today:
        parsed = _valid_date(today.year - 1, month, day)
    return parsed


def _most_recent_weekday(weekday_number: int, today: date) -> date:
    """最近一次该星期几（含今天），不会落到未来。"""
    days_back = (today.weekday() - weekday_number) % 7
    return today - timedelta(days=days_back)


def parse_im_thread_dates(text: str, *, today: date) -> list[date]:
    """Best-effort Beijing dates visible in a concatenated IM thread blob."""
    blob = str(text or "")
    found: list[date] = []
    if _RELATIVE_TODAY.search(blob) or _RELATIVE_MINUTES.search(blob):
        found.append(today)
    if _RELATIVE_YESTERDAY.search(blob):
        found.append(today - timedelta(days=1))
    for match in _RELATIVE_WEEKDAY.finditer(blob):
        raw = (match.group(1) or match.group(2) or "").strip().lower()
        weekday_number = _WEEKDAY_ALIASES.get(raw)
        if weekday_number is None:
            continue
        found.append(_most_recent_weekday(weekday_number, today))
    for match in _RELATIVE_HOURS.finditer(blob):
        hours = int(match.group(1))
        found.append(today if hours < 24 else today - timedelta(days=hours // 24))
    for match in _RELATIVE_DAYS.finditer(blob):
        found.append(today - timedelta(days=int(match.group(1))))
    for match in _ISO_DATE.finditer(blob):
        parsed = _valid_date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        if parsed is not None:
            found.append(parsed)
    for match in _CHINESE_DATE.finditer(blob):
        parsed = _date_with_current_year(int(match.group(1)), int(match.group(2)), today)
        if parsed is not None:
            found.append(parsed)
    for match in _MONTH_NAME_DATE.finditer(blob):
        month = _MONTH_ALIASES.get(match.group(1).lower().rstrip("."))
        if month is None:
            continue
        parsed = _date_with_current_year(month, int(match.group(2)), today)
        if parsed is not None:
            found.append(parsed)
    iso_spans = {match.span() for match in _ISO_DATE.finditer(blob)}
    for match in _NUMERIC_DATE.finditer(blob):
        if any(start <= match.start() < end for start, end in iso_spans):
            continue
        first = int(match.group(1))
        second = int(match.group(2))
        if first > 12:
            month, day = second, first
        else:
            month, day = first, second
        parsed = _date_with_current_year(month, day, today)
        if parsed is not None:
            found.append(parsed)
    unique_dates: list[date] = []
    for parsed_date in found:
        if parsed_date not in unique_dates:
            unique_dates.append(parsed_date)
    return unique_dates


def recent_thanks_already_sent(
    *,
    thread_text: str = "",
    today: date,
    local_sent_at: datetime | None = None,
) -> bool:
    """True only when a thanks-like DM is confirmed inside the 14-day window."""
    window_start = today - timedelta(days=RECENT_THANKS_WINDOW_DAYS)
    if local_sent_at is not None:
        local_date = beijing_date(local_sent_at)
        if window_start <= local_date <= today:
            return True
    if not looks_like_any_thanks(thread_text):
        return False
    thread_dates = parse_im_thread_dates(thread_text, today=today)
    return any(window_start <= thread_date <= today for thread_date in thread_dates)


def first_content_url(links: list[str] | None) -> str:
    for link in links or []:
        text = str(link or "").strip()
        if "/video/" in text.lower() or "/live" in text.lower():
            return text
    for link in links or []:
        text = str(link or "").strip()
        if text.startswith("http"):
            return text
    return ""


def decide_content_thanks_action(
    *,
    feishu_status: str,
    outreach_age_days: int | None,
    completed_match: str,
    content_type: str = "",
    content_reason: str = "",
    thread_text: str = "",
    today: date | None = None,
    local_sent_at: datetime | None = None,
    thread_checked: bool = True,
    feishu_record_count: int = 1,
) -> dict:
    """Preview thanks unless a recent thanks DM is confirmed, or identity is unsafe.

    ``thread_checked=False`` means the IM conversation could not be opened, so
    the 14-day dedup cannot be verified. Fail closed: hold instead of preview.
    """
    current_status = str(feishu_status or "").strip()
    if current_status in {COOPERATION_STATUS_COMPLETED, COOPERATION_STATUS_PUBLISHED_LEGACY}:
        return {
            "decision": DECISION_SKIP,
            "reason": "feishu-already-finished",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if current_status != COOPERATION_STATUS_PENDING_POST:
        return {
            "decision": DECISION_SKIP,
            "reason": "feishu-not-pending-post",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if outreach_age_days is not None and (
        outreach_age_days < 0 or outreach_age_days > CONTENT_THANKS_LOOKBACK_DAYS
    ):
        return {
            "decision": DECISION_SKIP,
            "reason": "outside-30-day-window",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if feishu_record_count != 1:
        return {
            "decision": DECISION_HOLD,
            "reason": "ambiguous-feishu-rows",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if completed_match == "none":
        return {
            "decision": DECISION_SKIP,
            "reason": "not-found-in-completed",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if completed_match != "unique":
        return {
            "decision": DECISION_HOLD,
            "reason": "ambiguous-completed-match",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    if content_type not in {"video", "live"}:
        return {
            "decision": DECISION_HOLD,
            "reason": content_reason or "content-type-uncertain",
            "content_type": "",
            "send": False,
            "write_feishu": False,
        }
    effective_today = today if today is not None else beijing_now().date()
    if recent_thanks_already_sent(
        thread_text=thread_text,
        today=effective_today,
        local_sent_at=local_sent_at,
    ):
        return {
            "decision": DECISION_ALREADY_SENT,
            "reason": "already-sent-within-14-days",
            "content_type": content_type,
            "send": False,
            "write_feishu": False,
        }
    if not thread_checked:
        return {
            "decision": DECISION_HOLD,
            "reason": "thread-unreadable",
            "content_type": content_type,
            "send": False,
            "write_feishu": False,
        }
    return {
        "decision": DECISION_PREVIEW,
        "reason": content_reason or "ready",
        "content_type": content_type,
        "send": False,
        "write_feishu": False,
    }


def render_content_thanks_preview(
    *,
    content_type: str,
    lang: str,
    creator_name: str,
    content_url: str = "",
) -> dict:
    template_key = choose_template_key(
        stage=CONTENT_THANKS_STAGE,
        creator_type=content_type,
        lang=lang,
        is_hero_sku=False,
    )
    if not template_key:
        return {
            "ok": False,
            "reason": "missing_template",
            "template_key": "",
            "message": "",
            "language": lang,
        }
    return {
        "ok": True,
        "reason": "",
        "template_key": template_key,
        "message": render_followup_message(
            template_key,
            creator_name=creator_name,
            content_url=content_url,
        ),
        "language": lang,
    }


def resolve_content_thanks_language(
    *,
    feishu_lang: str = "",
    manual_lang: str = "",
    bio: str = "",
) -> str:
    return str(
        choose_followup_language(
            bio=bio or None,
            feishu_lang=feishu_lang or None,
            manual_lang=manual_lang or None,
        ).get("lang")
        or "en"
    )
