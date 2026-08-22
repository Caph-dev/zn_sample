#!/usr/bin/env python3
"""Capture one explicitly authorized, recursively redacted logistics fixture."""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIRECTORY.parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from lib.app_log import configure_logging  # noqa: E402
from lib.order_api import (  # noqa: E402
    fetch_tiktok_logistics_payload,
    parse_logistics_details,
)
from lib.zclaw import resolve_store_id  # noqa: E402


logger = logging.getLogger(__name__)
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:phone|mobile|address|receiver|name|token|cookie|secret)",
    re.IGNORECASE,
)
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "tests" / "fixtures" / (
    "logistics_captured_redacted.json"
)


def redact_payload(value: Any) -> Any:
    """Replace values under sensitive keys while preserving payload shape."""
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if SENSITIVE_KEY_PATTERN.search(str(key))
                else redact_payload(nested_value)
            )
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    return value


def build_argument_parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(
        description="从已登录商家订单页读取并脱敏一份物流详情 fixture。"
    )
    argument_parser.add_argument(
        "--order-id",
        default="",
        help="要读取的 TikTok main_order_id；真实请求时必填",
    )
    argument_parser.add_argument(
        "--store-id",
        default=None,
        help="紫鸟 storeId；省略时要求 running 恰好一家店",
    )
    argument_parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"脱敏 JSON 输出路径（默认 {DEFAULT_OUTPUT_PATH}）",
    )
    argument_parser.add_argument(
        "--i-am-on-the-order-page",
        action="store_true",
        help="确认当前已登录且位于商家订单页，允许执行只读请求",
    )
    return argument_parser


def main() -> int:
    configure_logging()
    argument_parser = build_argument_parser()
    arguments = argument_parser.parse_args()

    if not arguments.i_am_on_the_order_page:
        logger.error(
            "默认不请求紫鸟。请先人工登录并停在商家订单页，再加 "
            "--order-id 与 --i-am-on-the-order-page。计划输出路径：%s",
            arguments.out,
        )
        return 2

    normalized_order_id = str(arguments.order_id or "").strip()
    if not normalized_order_id:
        argument_parser.error("--i-am-on-the-order-page 要求同时提供 --order-id")

    store_id = resolve_store_id(
        store_id=arguments.store_id,
        default_store_id=None,
    )
    payload = fetch_tiktok_logistics_payload(store_id, normalized_order_id)
    details = parse_logistics_details(payload, order_id=normalized_order_id)
    redacted_payload = redact_payload(payload)

    output_path = arguments.out.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(redacted_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    logger.info("脱敏 fixture 已写入：%s", output_path)
    logger.info("missing_fields=%s", details["missing_fields"])
    logger.info("raw_payload_hash=%s", details["raw_payload_hash"])
    logger.warning("提交前仍须人工检查脱敏文件。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
