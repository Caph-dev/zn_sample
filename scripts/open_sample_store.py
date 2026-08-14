#!/usr/bin/env python3
"""脚本 1：通过 GUI + ZClaw 打开样品筛查目标店铺。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.store_launcher import ensure_sample_store_open  # noqa: E402


def main() -> int:
    argument_parser = argparse.ArgumentParser(
        description=(
            "通过 GUI + ZClaw 打开店铺；目标店已运行时不会关闭或重新打开。"
        )
    )
    argument_parser.add_argument(
        "--store-id",
        required=True,
        help="目标紫鸟店铺 storeId（必须显式提供，避免误开默认店）",
    )
    argument_parser.add_argument(
        "--store-name",
        default="",
        help="仅用于提示的店铺名称",
    )
    argument_parser.add_argument(
        "--wait",
        type=float,
        default=2.0,
        help="新开店后探活前等待秒数（默认 2）",
    )
    arguments = argument_parser.parse_args()

    try:
        result = ensure_sample_store_open(
            arguments.store_id,
            store_name=arguments.store_name,
            wait_seconds=max(0.0, arguments.wait),
        )
    except Exception as error:
        print(f"打开店铺失败: {error}", file=sys.stderr)
        return 1

    action = "店铺已运行，未重新打开" if result["already_running"] else "店铺已打开"
    probe = result.get("probe") or {}
    print(f"[{action}] storeId={result['store_id']}")
    print(
        f"[页面探活] ready={probe.get('ready')} "
        f"href={str(probe.get('href') or '')[:160]}"
    )
    print("请在店铺窗口登录 TikTok Shop，并停在商家中心首页。")
    print("随后运行 screen_sample_requests.py --from-seller-home 进行筛查。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
