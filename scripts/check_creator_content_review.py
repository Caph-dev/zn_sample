#!/usr/bin/env python3
"""内容审核（SOP 第 5 步）单达人只读自测。

给定 TikTok 达人用户名，走与正式链路相同的 review_creator_rows：
采集近期带货视频 → 7×24h 窗口 + 相关类目计数 → 稀疏视觉证据（ffmpeg 抽帧 + ARK 视觉）。

只读：不批准、不发私信、不写飞书、不写平台。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from lib.app_config import load_content_review_settings  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402
from lib.creator_video_review import (  # noqa: E402
    review_creator_rows,
    validate_content_review,
)

logger = logging.getLogger(__name__)


def _mask(value: str) -> str:
    if not value:
        return "<未配置>"
    if len(value) <= 8:
        return f"<已配置,len={len(value)}>"
    return f"<已配置,len={len(value)}>...{value[-4:]}"


def _print_settings(settings: dict) -> None:
    logger.info("[设置] enabled=%s", settings.get("enabled"))
    logger.info("[设置] TIKHUB_API_KEY=%s", _mask(settings.get("tikhub_api_key") or ""))
    logger.info("[设置] ARK_API_KEY=%s", _mask(settings.get("ark_api_key") or ""))
    logger.info("[设置] ARK_MODEL=%s", settings.get("ark_model") or "<默认>")
    logger.info("[设置] ffmpeg_path=%s", _mask(settings.get("ffmpeg_path") or ""))
    logger.info(
        "[设置] visual 预算=max_visual_videos=%s run_timeout=%ss",
        settings.get("max_visual_videos"),
        settings.get("run_timeout_seconds"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="单达人内容审核只读自测")
    parser.add_argument(
        "--creator-name",
        required=True,
        help="TikTok 达人用户名（可带 @，1-24 位字母数字._）",
    )
    parser.add_argument(
        "--seed-video-id",
        default="",
        help="可选：该达人某支视频 ID（15-25 位数字），用于立即核对作者身份",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="仅输出结果 JSON，不打印过程日志",
    )
    args = parser.parse_args()

    if args.json:
        configure_logging(console_level=logging.WARNING)
    else:
        configure_logging()

    settings = load_content_review_settings()
    _print_settings(settings)

    row = {
        "creator_name": args.creator_name,
        "sales_eligible": True,
        "content_review_seed_video_id": args.seed_video_id,
    }
    logger.info("开始审核 %s ...（采集 + 抽帧可能耗时数分钟）", args.creator_name)
    try:
        reviewed = review_creator_rows([row], now=None)[0]
    except Exception as error:  # noqa: BLE001 - 自测脚本兜底展示
        logger.error("审核异常: %s", error)
        return 1

    verdict = {
        "creator_name": reviewed.get("creator_name"),
        "handle": reviewed.get("content_review_handle"),
        "content_review_status": reviewed.get("content_review_status"),
        "content_review_reason": reviewed.get("content_review_reason"),
        "content_review_related_count": reviewed.get("content_review_related_count"),
        "content_review_complete": reviewed.get("content_review_complete"),
        "content_review_evidence_path": reviewed.get("content_review_evidence_path"),
        "content_review_video_ids": reviewed.get("content_review_video_ids") or [],
        "eligible": reviewed.get("eligible"),
    }
    approved, gate_reason = validate_content_review(reviewed)
    verdict["approval_gate"] = approved
    verdict["approval_gate_reason"] = gate_reason

    if args.json:
        print(json.dumps(verdict, ensure_ascii=True, indent=2))
    else:
        logger.info("")
        logger.info("===== 结果 =====")
        logger.info("达人: %s (handle=%s)", verdict["creator_name"], verdict["handle"])
        logger.info("审核状态: %s", verdict["content_review_status"])
        logger.info("原因: %s", verdict["content_review_reason"])
        logger.info("相关视频数(7×24h 下界): %s", verdict["content_review_related_count"])
        logger.info("采集完整: %s", verdict["content_review_complete"])
        logger.info(
            "证据路径: %s", verdict["content_review_evidence_path"] or "<无>"
        )
        logger.info("视频 ID (%s):", len(verdict["content_review_video_ids"]))
        for video_id in verdict["content_review_video_ids"]:
            logger.info("  %s", video_id)
        logger.info("批准门禁(validate_content_review): %s (%s)", approved, gate_reason)
        logger.info("行 eligible: %s", verdict["eligible"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
