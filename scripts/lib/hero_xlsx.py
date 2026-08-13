#!/usr/bin/env python3
"""已废弃：本地「tk产品图+货号.xlsx」解析。

主推表现仅从飞书读取，见 ``feishu_hero.load_hero_from_feishu``。
保留本模块仅为避免外部旧 import 立即崩溃；请改用 feishu_hero。
"""
from __future__ import annotations

from pathlib import Path

from .feishu_hero import match_hero

__all__ = ["match_hero", "parse_hero_xlsx"]


def parse_hero_xlsx(path: str | Path) -> dict:
    raise RuntimeError(
        "本地主推 xlsx 已停用。请在 config.toml 填写 [feishu].app_secret，"
        "或 export FEISHU_APP_SECRET，由 feishu_hero.load_hero_from_feishu() 读取飞书表 "
        "https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc"
    )
