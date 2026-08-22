from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.domain.policies import (
    choose_followup_creator_type,
    choose_followup_language,
    feishu_update_idempotency_key,
    followup_task_idempotency_key,
    message_send_idempotency_key,
)


class FollowupPolicyTests(unittest.TestCase):
    def test_manual_creator_type_has_priority(self) -> None:
        result = choose_followup_creator_type(
            manual_type="live",
            is_video_creator="是",
            is_live_creator="",
        )
        self.assertEqual(result, {"creator_type": "live", "needs_review": False})

    def test_single_export_flag_selects_creator_type(self) -> None:
        self.assertEqual(
            choose_followup_creator_type(
                manual_type=None,
                is_video_creator="是",
                is_live_creator="",
            ),
            {"creator_type": "video", "needs_review": False},
        )
        self.assertEqual(
            choose_followup_creator_type(
                manual_type=None,
                is_video_creator="",
                is_live_creator="是",
            ),
            {"creator_type": "live", "needs_review": False},
        )

    def test_ambiguous_creator_flags_require_review(self) -> None:
        for video_flag, live_flag in (("是", "是"), ("", "")):
            with self.subTest(video_flag=video_flag, live_flag=live_flag):
                result = choose_followup_creator_type(
                    manual_type=None,
                    is_video_creator=video_flag,
                    is_live_creator=live_flag,
                )
                self.assertIsNone(result["creator_type"])
                self.assertTrue(result["needs_review"])

    def test_empty_bio_recommends_english_for_review(self) -> None:
        result = choose_followup_language(bio="")
        self.assertEqual(result["lang"], "en")
        self.assertTrue(result["needs_review"])
        self.assertEqual(result["reason"], "empty-bio-default-en")

    def test_feishu_spanish_overrides_empty_bio(self) -> None:
        result = choose_followup_language(bio="", feishu_lang="西班牙语")
        self.assertEqual(result["lang"], "es")
        self.assertFalse(result["needs_review"])

    def test_manual_language_overrides_feishu_language(self) -> None:
        result = choose_followup_language(
            bio="Hola, gracias por tu apoyo",
            feishu_lang="西班牙语",
            manual_lang="en",
        )
        self.assertEqual(result["lang"], "en")
        self.assertFalse(result["needs_review"])
        self.assertEqual(result["reason"], "manual_override")

    def test_high_confidence_spanish_bio_is_selected(self) -> None:
        result = choose_followup_language(
            bio="Hola, gracias por tu contenido y colaboración."
        )
        self.assertEqual(result["lang"], "es")
        self.assertFalse(result["needs_review"])

    def test_idempotency_keys_use_only_stable_business_fields(self) -> None:
        scheduled_for = date(2026, 8, 22)
        self.assertEqual(
            followup_task_idempotency_key("case-1", "day_3", scheduled_for),
            "case-1|day_3|2026-08-22",
        )
        self.assertEqual(
            message_send_idempotency_key(
                "store-1", "creator-1", "product-1", "day_3", scheduled_for
            ),
            "store-1|creator-1|product-1|day_3|2026-08-22",
        )
        self.assertEqual(
            feishu_update_idempotency_key("record-1", "未发布", "evidence-1"),
            "record-1|未发布|evidence-1",
        )


if __name__ == "__main__":
    unittest.main()
