from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.domain import message_templates
from assistant.domain.message_templates import (
    choose_template_key,
    render_followup_message,
)
from assistant.domain.policies import message_send_idempotency_key


EXPECTED_TEMPLATE_KEYS = {
    "arrival_hero_video_en",
    "arrival_hero_video_es",
    "arrival_hero_live_en",
    "arrival_hero_live_es",
    "arrival_other_video_en",
    "arrival_other_video_es",
    "arrival_other_live_en",
    "arrival_other_live_es",
    "unpublished_3_video_en",
    "unpublished_3_video_es",
    "unpublished_3_live_en",
    "unpublished_3_live_es",
    "unpublished_7_video_en",
    "unpublished_7_video_es",
    "unpublished_7_live_en",
    "unpublished_7_live_es",
    "video_found_en",
    "video_found_es",
    "live_found_en",
    "live_found_es",
}


class FollowupMessageTemplateTests(unittest.TestCase):
    def test_all_twenty_templates_render(self) -> None:
        self.assertEqual(set(message_templates._TEMPLATES), EXPECTED_TEMPLATE_KEYS)
        self.assertEqual(len(EXPECTED_TEMPLATE_KEYS), 20)
        for template_key in EXPECTED_TEMPLATE_KEYS:
            with self.subTest(template_key=template_key):
                rendered_message = render_followup_message(
                    template_key,
                    creator_name="Creator",
                    content_url="https://example.test/video",
                )
                self.assertIn("Creator", rendered_message)

    def test_arrival_hero_video_mentions_required_hashtag(self) -> None:
        template_key = choose_template_key(
            stage="arrival",
            creator_type="video",
            lang="en",
            is_hero_sku=True,
        )
        self.assertEqual(template_key, "arrival_hero_video_en")
        rendered_message = render_followup_message(
            template_key,
            creator_name="Creator",
        )
        self.assertIn("#Hicloth", rendered_message)

    def test_spanish_live_day_three_uses_sop_promotion_copy(self) -> None:
        template_key = choose_template_key(
            stage="day_3",
            creator_type="live",
            lang="es",
            is_hero_sku=True,
        )
        rendered_message = render_followup_message(
            template_key,
            creator_name="Creadora",
        )
        self.assertIn("promoción", rendered_message)

    def test_video_found_includes_content_url_without_extra_copy(self) -> None:
        content_url = "https://example.test/content/123"
        rendered_message = render_followup_message(
            "video_found_en",
            creator_name="Creator",
            content_url=content_url,
        )
        self.assertIn(content_url, rendered_message)
        self.assertNotIn("投流码", rendered_message)

    def test_thank_you_templates_do_not_add_traffic_code_copy(self) -> None:
        for template_key in (
            "video_found_en",
            "video_found_es",
            "live_found_en",
            "live_found_es",
        ):
            with self.subTest(template_key=template_key):
                self.assertNotIn(
                    "投流码",
                    render_followup_message(template_key, creator_name="Creator"),
                )

    def test_non_message_stages_and_unknown_types_have_no_template(self) -> None:
        for stage in ("day_10_list", "unfulfilled"):
            self.assertIsNone(
                choose_template_key(
                    stage=stage,
                    creator_type="video",
                    lang="en",
                    is_hero_sku=True,
                )
            )
        self.assertIsNone(
            choose_template_key(
                stage="arrival",
                creator_type=None,
                lang="en",
                is_hero_sku=True,
            )
        )

    def test_dual_marked_creator_uses_video_templates(self) -> None:
        self.assertEqual(
            choose_template_key(
                stage="arrival",
                creator_type="both",
                lang="en",
                is_hero_sku=True,
            ),
            "arrival_hero_video_en",
        )
        self.assertEqual(
            choose_template_key(
                stage="day_3",
                creator_type="both",
                lang="es",
                is_hero_sku=False,
            ),
            "unpublished_3_video_es",
        )
        self.assertEqual(
            choose_template_key(
                stage="content_found",
                creator_type="both",
                lang="en",
                is_hero_sku=True,
            ),
            "video_found_en",
        )

    def test_found_templates_ignore_creator_type(self) -> None:
        self.assertEqual(
            choose_template_key(
                stage="video_found",
                creator_type=None,
                lang="es",
                is_hero_sku=False,
            ),
            "video_found_es",
        )

    def test_unknown_template_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            render_followup_message("missing", creator_name="Creator")

    def test_message_idempotency_key_uses_stable_business_key_format(self) -> None:
        self.assertEqual(
            message_send_idempotency_key(
                "store", "creator", "product", "day_3", date(2026, 8, 22)
            ),
            "store|creator|product|day_3|2026-08-22",
        )


if __name__ == "__main__":
    unittest.main()
