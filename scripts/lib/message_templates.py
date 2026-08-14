#!/usr/bin/env python3
"""SOP 第 6 / 第 9 步话术。仅英语、西班牙语。"""
from __future__ import annotations

from .detect_lang import LANG_EN, LANG_ES

INTRO_FINGERPRINTS = (
    "thanks for requesting our lingerie sample",
    "gracias por solicitar nuestra muestra",
)
TRACKING_FINGERPRINTS = (
    "here is the tracking number",
    "aquí tienes el número seguimiento",
    "aqui tienes el numero seguimiento",
)


def intro_message(lang: str, creator_name: str) -> str:
    name = (creator_name or "").strip()
    if lang == LANG_ES:
        greet = f"Hola {name}" if name else "Hola"
        return (
            f"{greet}, ¡gracias por solicitar nuestra muestra de lencería!\n"
            "\n"
            "Estamos muy emocionados de trabajar contigo. Para coordinar mejor "
            "el envío y hablar sobre la dirección creativa del contenido, "
            "¿podrías compartirme tu WhatsApp o Gmail?\n"
            "\n"
            "Además, querida, ¿estarías abierta a hacer transmisiones en vivo "
            "de nuestros productos? En cuanto tenga el número de seguimiento, "
            "te lo envío inmediatamente."
        )
    greet = f"Hi {name}" if name else "Hi"
    return (
        f"{greet}, thanks for requesting our lingerie sample! "
        "We’re excited to work with you.\n"
        "\n"
        "To better coordinate the shipping and discuss the creative direction "
        "for the content, could you please share your WhatsApp or Gmail?\n"
        "\n"
        "Once the tracking number is available, I will send it to you right away."
    )


def tracking_message(lang: str, tracking_no: str) -> str:
    number = (tracking_no or "").strip()
    if lang == LANG_ES:
        return f"Estimada, aquí tienes el número seguimiento:{number}"
    return f"Dear, here is the tracking number:{number}"


def looks_like_intro(text: str) -> bool:
    blob = (text or "").lower()
    return any(token in blob for token in INTRO_FINGERPRINTS)


def looks_like_tracking(text: str, tracking_no: str = "") -> bool:
    blob = (text or "").lower()
    if any(token in blob for token in TRACKING_FINGERPRINTS):
        return True
    number = (tracking_no or "").strip().lower()
    return bool(number) and number in blob.replace(" ", "")
