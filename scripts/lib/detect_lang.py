#!/usr/bin/env python3
"""从达人详情页简介识别语言：仅英语 / 西班牙语。"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

LANG_EN = "en"
LANG_ES = "es"
FEISHU_LANG = {
    LANG_EN: "英语",
    LANG_ES: "西班牙语",
}

_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
_HANDLE_RE = re.compile(r"[@#]\w+")
_WORD_RE = re.compile(r"[a-záéíóúüñ]+", re.I)

# 强特征：单独出现即可显著加分
_ES_STRONG = {
    "hola",
    "gracias",
    "qué",
    "cómo",
    "también",
    "tambien",
    "está",
    "están",
    "están",
    "estoy",
    "somos",
    "contenido",
    "colaboración",
    "colaboracion",
    "colaborar",
    "muestra",
    "lencería",
    "lenceria",
    "aquí",
    "aqui",
    "tienes",
    "puedes",
    "quería",
    "queria",
    "buenos",
    "buenas",
    "días",
    "dias",
    "noches",
    "porque",
    "después",
    "despues",
    "entonces",
    "nosotros",
    "nosotras",
    "ustedes",
    "hago",
    "hacemos",
    "quiero",
    "quieres",
    "sí",
    "más",
    "información",
    "informacion",
    "canal",
    "podemos",
    "hacer",
    "dios",
    "nada",
    "bendiciones",
    "colaboraciones",
    "productos",
    "tienda",
}
_EN_STRONG = {
    "thanks",
    "thank",
    "thankful",
    "blessed",
    "blessing",
    "collaboration",
    "collaborations",
    "collab",
    "reviews",
    "review",
    "content",
    "honest",
    "dm",
    "email",
    "gmail",
    "whatsapp",
    "hello",
    "hi",
    "hey",
    "please",
    "business",
    "brand",
    "brands",
    "creator",
    "affiliate",
    "life",
    "good",
    "god",
    "love",
    "always",
}

_ES_FUNC = {
    "que",
    "de",
    "la",
    "el",
    "en",
    "y",
    "los",
    "las",
    "un",
    "una",
    "para",
    "con",
    "por",
    "del",
    "al",
    "se",
    "su",
    "sus",
    "mi",
    "mis",
    "tu",
    "tus",
    "te",
    "me",
    "nos",
    "es",
    "son",
    "soy",
    "pero",
    "muy",
    "bien",
    "como",
    "todo",
    "esta",
    "este",
    "hay",
    "sin",
    "sobre",
    "ya",
}
_EN_FUNC = {
    "the",
    "and",
    "for",
    "with",
    "you",
    "your",
    "this",
    "that",
    "from",
    "have",
    "has",
    "are",
    "was",
    "were",
    "been",
    "will",
    "can",
    "our",
    "we",
    "my",
    "me",
    "to",
    "of",
    "in",
    "on",
    "a",
    "is",
    "it",
    "or",
}


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


def detect_creator_lang(bio: str | None) -> dict[str, Any]:
    """根据详情页简介判 en / es。空简介或无法判断 → en（低置信）。

    仅支持英语、西班牙语。不输出波斯语。
    """
    cleaned = normalize_bio(bio or "")
    if not cleaned:
        return {
            "lang": LANG_EN,
            "feishu_lang": FEISHU_LANG[LANG_EN],
            "confidence": "low",
            "reason": "empty-bio-default-en",
            "bio": "",
            "es_score": 0,
            "en_score": 0,
        }

    es_score = 0
    en_score = 0
    if re.search(r"[ñáéíóúü¿¡]", cleaned, re.I):
        es_score += 4

    words = [w.lower() for w in _WORD_RE.findall(cleaned)]
    for word in words:
        if word in _ES_STRONG:
            es_score += 3
        elif word in _ES_FUNC:
            es_score += 1
        if word in _EN_STRONG:
            en_score += 3
        elif word in _EN_FUNC:
            en_score += 1

    if es_score >= 2 and es_score >= en_score + 1:
        lang = LANG_ES
        confidence = "high" if es_score >= 5 else "medium"
        reason = f"es>en ({es_score}>{en_score})"
    elif en_score >= 1 or es_score == 0:
        lang = LANG_EN
        if en_score == 0 and es_score == 0:
            confidence = "low"
            reason = "no-marker-default-en"
        else:
            confidence = "high" if en_score >= 4 else "medium"
            reason = f"en>=es ({en_score}>={es_score})"
    else:
        lang = LANG_ES
        confidence = "medium"
        reason = f"es-tie-break ({es_score}/{en_score})"

    return {
        "lang": lang,
        "feishu_lang": FEISHU_LANG[lang],
        "confidence": confidence,
        "reason": reason,
        "bio": cleaned[:300],
        "es_score": es_score,
        "en_score": en_score,
    }
