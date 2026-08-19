#!/usr/bin/env python3
"""脚本 1：通过 GUI + ZClaw 打开样品筛查目标店铺。"""
from __future__ import annotations

import logging

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.app_log import configure_logging  # noqa: E402
from lib.store_launcher import (  # noqa: E402
    DEFAULT_PREPARE_STORE_ID,
    DEFAULT_PREPARE_STORE_NAME,
    ensure_sample_store_open,
    prepare_sample_store_debug,
    resolve_store_id_for_prepare,
)
from lib.zclaw import resolve_store_id  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> int:
    configure_logging()
    argument_parser = argparse.ArgumentParser(
        description=(
            "通过 GUI + ZClaw 打开店铺。"
            "默认：目标店已运行时不会关闭或重新打开。"
            "加 --reopen：已开则先关再开（带 debugPort），未开则 open_store。"
        )
    )
    argument_parser.add_argument(
        "--store-id",
        default=None,
        help="目标紫鸟店铺 storeId；只开着一家时可省略",
    )
    argument_parser.add_argument(
        "--store-name",
        default="",
        help="店铺名称；--reopen 且无 running 时也可在账号列表里精确匹配",
    )
    argument_parser.add_argument(
        "--wait",
        type=float,
        default=2.0,
        help="新开店后探活前等待秒数（默认 2）",
    )
    argument_parser.add_argument(
        "--reopen",
        action="store_true",
        help="目标已运行时先关闭再 open_store（带 debugPort）。0 号脚本使用此开关。",
    )
    argument_parser.add_argument(
        "--no-default-store",
        action="store_true",
        help=(
            "禁止 0 号在无 running 时默认打开 "
            f"{DEFAULT_PREPARE_STORE_NAME}（{DEFAULT_PREPARE_STORE_ID}）"
        ),
    )
    arguments = argument_parser.parse_args()
    try:
        if arguments.reopen:
            store_id = resolve_store_id_for_prepare(
                store_id=arguments.store_id,
                store_name=arguments.store_name or None,
                default_store_id=(
                    None if arguments.no_default_store else DEFAULT_PREPARE_STORE_ID
                ),
            )
            result = prepare_sample_store_debug(
                store_id,
                store_name=arguments.store_name,
                wait_seconds=max(0.0, arguments.wait),
            )
        else:
            store_id = resolve_store_id(
                store_id=arguments.store_id,
                store_name=arguments.store_name or None,
                default_store_id=None,
            )
            result = ensure_sample_store_open(
                store_id,
                store_name=arguments.store_name,
                wait_seconds=max(0.0, arguments.wait),
            )
    except Exception as error:
        logger.error(f"打开店铺失败: {error}")
        return 1

    if arguments.reopen:
        action = "店铺已关闭并重新打开（带调试口）" if result.get("reopened") else "店铺已打开（带调试口）"
    else:
        action = "店铺已运行，未重新打开" if result["already_running"] else "店铺已打开"
    probe = result.get("probe") or {}
    href = str(probe.get("href") or "")
    logger.info(f"[{action}] storeId={result['store_id']}")
    logger.info(
        f"[页面探活] ready={probe.get('ready')} href={href[:160]}"
    )
    if href in {"", "about:blank"} or "login" in href.lower():
        logger.info("调试口已通，但窗口还没登录。请在这个店铺窗口登录 TikTok Shop。")
    else:
        logger.info("调试口已通。请确认已登录商家中心（任意页即可，不必停在首页）。")
    logger.info("随后双击「1-只出名单」，或运行 screen_sample_requests.py --from-seller-home。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
