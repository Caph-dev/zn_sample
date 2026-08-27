#!/usr/bin/env python3
"""从达人详情页简介识别语言：仅英语 / 西班牙语。

飞书「使用语言」和人工选定仍优先。简介回退统一走 OpenAI 兼容 Chat Completions
（默认 DeepSeek），要求模型只输出 JSON。空简介不调用模型，默认英语、低置信。
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from functools import lru_cache
from typing import Any

import httpx

from .app_config import load_llm_settings

logger = logging.getLogger(__name__)

LANG_EN = "en"
LANG_ES = "es"
FEISHU_LANG = {
    LANG_EN: "英语",
    LANG_ES: "西班牙语",
}
_SUPPORTED_LANGS = frozenset({LANG_EN, LANG_ES})
_LANG_ALIASES = {
    "en": LANG_EN,
    "english": LANG_EN,
    "eng": LANG_EN,
    "英语": LANG_EN,
    "es": LANG_ES,
    "spanish": LANG_ES,
    "español": LANG_ES,
    "espanol": LANG_ES,
    "spa": LANG_ES,
    "西班牙语": LANG_ES,
}
_CONFIDENCE_ALIASES = {
    "high": "high",
    "medium": "low",
    "low": "low",
}
LLM_TIMEOUT_SECONDS = 30.0
LLM_MAX_BIO_CHARS = 1500
LLM_MAX_TOKENS = 200

LANGUAGE_DETECT_SYSTEM_PROMPT = """
You classify TikTok creator bios for a seller follow-up tool.
Return a single JSON object only. Do not write markdown, explanations, or extra keys.

The only allowed languages are English and Spanish. Ignore other languages,
emoji, hashtags, URLs, and @handles when deciding.

JSON schema:
{
  "lang": "en" | "es",
  "confidence": "high" | "low",
  "reason": "short English machine reason, max 80 characters"
}

Rules:
- lang=en if the bio is mainly English.
- lang=es if the bio is mainly Spanish, including Latin American Spanish.
- Mixed English and Spanish: pick the language the creator would most likely
  understand for a private message. If still mixed, confidence=low.
- Names, emojis, or one-word bios: confidence=low.
- If unsure, still pick en or es, but set confidence=low.

EXAMPLE JSON OUTPUT:
{"lang":"es","confidence":"high","reason":"spanish-greeting-and-collab"}
""".strip()

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_HANDLE_RE = re.compile(r"[@#]\w+")


def _fold(text: str) -> str:
    return "".join(
        ch
        for ch in unicodedata.normalize("NFKC", text or "")
        if unicodedata.category(ch)[0] != "C" or ch in "\n\t "
    )


def normalize_bio(text: str) -> str:
    raw = _fold(text)
    raw = _URL_RE.sub(" ", raw)
    raw = _HANDLE_RE.sub(" ", raw)
    return re.sub(r"\s+", " ", raw).strip()


def _empty_bio_result() -> dict[str, Any]:
    return {
        "lang": LANG_EN,
        "feishu_lang": FEISHU_LANG[LANG_EN],
        "confidence": "low",
        "reason": "empty-bio-default-en",
        "bio": "",
        "source": "empty-bio",
    }


def _result(
    *,
    lang: str,
    confidence: str,
    reason: str,
    bio: str,
    source: str,
) -> dict[str, Any]:
    resolved_lang = lang if lang in _SUPPORTED_LANGS else LANG_EN
    resolved_confidence = "high" if confidence == "high" else "low"
    return {
        "lang": resolved_lang,
        "feishu_lang": FEISHU_LANG[resolved_lang],
        "confidence": resolved_confidence,
        "reason": reason,
        "bio": bio[:300],
        "source": source,
    }


def _normalize_lang(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    return _LANG_ALIASES.get(raw)


def _normalize_confidence(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _CONFIDENCE_ALIASES.get(raw, "low")


def _extract_json_object(content: str) -> Any:
    text = str(content or "").strip()
    if not text:
        raise ValueError("llm-empty-content")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def _parse_language_json(content: str) -> dict[str, Any]:
    payload = _extract_json_object(content)
    if not isinstance(payload, dict):
        raise ValueError("language-json-not-object")
    lang = _normalize_lang(payload.get("lang"))
    if lang is None:
        raise ValueError("language-json-invalid-lang")
    reason = str(payload.get("reason") or "llm-classified").strip()[:80]
    return {
        "lang": lang,
        "confidence": _normalize_confidence(payload.get("confidence")),
        "reason": reason or "llm-classified",
    }


def _chat_completions_url(base_url: str) -> str:
    return str(base_url or "").rstrip("/") + "/chat/completions"


def _request_language_from_llm(bio: str) -> dict[str, Any]:
    settings = load_llm_settings()
    api_key = settings["api_key"]
    if not api_key:
        raise RuntimeError("missing-llm-api-key")
    response = httpx.post(
        _chat_completions_url(settings["base_url"]),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": settings["model_id"],
            "messages": [
                {"role": "system", "content": LANGUAGE_DETECT_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        "Classify this creator bio. Return JSON only.\n"
                        f"{bio[:LLM_MAX_BIO_CHARS]}"
                    ),
                },
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": LLM_MAX_TOKENS,
            "temperature": 0,
            "stream": False,
            "thinking": {"type": "disabled"},
        },
        timeout=LLM_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    content = (
        (((payload.get("choices") or [{}])[0]).get("message") or {}).get("content")
        or ""
    )
    if not str(content).strip():
        raise ValueError("llm-empty-content")
    return _parse_language_json(str(content))


@lru_cache(maxsize=256)
def _cached_language_from_llm(bio: str) -> tuple[str, str, str]:
    parsed = _request_language_from_llm(bio)
    return parsed["lang"], parsed["confidence"], parsed["reason"]


def clear_language_detect_cache() -> None:
    _cached_language_from_llm.cache_clear()


def detect_creator_lang(bio: str | None) -> dict[str, Any]:
    """根据详情页简介判 en / es。空简介或无法判断 → en（低置信）。

    仅支持英语、西班牙语。简介有内容时走 LLM JSON 输出。
    """
    cleaned = normalize_bio(bio or "")
    if not cleaned:
        return _empty_bio_result()
    try:
        lang, confidence, reason = _cached_language_from_llm(cleaned)
        return _result(
            lang=lang,
            confidence=confidence,
            reason=reason,
            bio=cleaned,
            source="llm",
        )
    except Exception as error:
        logger.warning("简介语言 LLM 识别失败：%s", error)
        return _result(
            lang=LANG_EN,
            confidence="low",
            reason="llm-detect-failed",
            bio=cleaned,
            source="llm-failed",
        )
