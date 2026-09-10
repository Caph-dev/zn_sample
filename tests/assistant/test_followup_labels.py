from __future__ import annotations

import unittest
from datetime import datetime, timezone

from assistant.domain.followup_labels import (
    followup_action_display,
    followup_action_tone,
    followup_language_label,
    followup_send_result_label,
    followup_stage_list_rank,
    followup_status_display,
)


class FollowupLabelTests(unittest.TestCase):
    def test_pending_and_superseded_status_display_as_dash(self) -> None:
        self.assertEqual(followup_status_display("pending"), "-")
        self.assertEqual(
            followup_status_display(
                "suppressed",
                suppressed_reason="superseded_by_later_stage",
            ),
            "-",
        )
        self.assertEqual(followup_status_display("needs_review"), "需人工确认")
        self.assertEqual(
            followup_status_display(
                "suppressed",
                suppressed_reason="content_confirmed",
            ),
            "达人已出内容",
        )

    def test_action_display_splits_todo_and_done(self) -> None:
        self.assertEqual(
            followup_action_display("send_message"),
            "待发跟进私信",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                send_result="marked-sent",
            ),
            "已发跟进私信（本地标记）",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                send_result="platform-sent",
            ),
            "已发跟进私信（平台确认）",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                sent_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            ),
            "已发跟进私信",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                send_result="send-unknown",
            ),
            "待发跟进私信",
        )
        self.assertEqual(
            followup_action_display("list_only"),
            "待出名单给业务",
        )
        self.assertEqual(
            followup_action_display("list_only", send_result="listed"),
            "已出名单给业务",
        )
        self.assertNotIn("D+3", followup_action_display("send_message"))
        self.assertNotIn("D+7", followup_action_display("send_message"))

    def test_suppressed_action_is_not_shown_as_pending(self) -> None:
        self.assertEqual(
            followup_action_display(
                "send_message",
                status="suppressed",
                suppressed_reason="superseded_by_later_stage",
            ),
            "不再发送（已由新阶段取代）",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                status="suppressed",
                suppressed_reason="content_confirmed",
            ),
            "不再发送（达人已出内容）",
        )
        self.assertEqual(
            followup_action_display("send_message", status="suppressed"),
            "不再发送",
        )
        self.assertEqual(
            followup_action_tone(
                "send_message",
                status="suppressed",
                suppressed_reason="superseded_by_later_stage",
            ),
            "neutral",
        )

    def test_send_result_labels_distinguish_platform_and_local(self) -> None:
        self.assertEqual(followup_send_result_label("platform-sent"), "平台已确认发送")
        self.assertEqual(followup_send_result_label("marked-sent"), "本地人工标记")
        self.assertEqual(followup_send_result_label("send-unknown"), "发送结果未确认")
        self.assertEqual(followup_send_result_label(""), "")
        self.assertEqual(followup_send_result_label("custom"), "custom")

    def test_list_order_puts_later_calendar_stages_first(self) -> None:
        self.assertLess(
            followup_stage_list_rank("unfulfilled"),
            followup_stage_list_rank("day_10_list"),
        )
        self.assertLess(
            followup_stage_list_rank("day_10_list"),
            followup_stage_list_rank("day_7"),
        )
        self.assertLess(
            followup_stage_list_rank("day_7"),
            followup_stage_list_rank("day_3"),
        )
        self.assertLess(
            followup_stage_list_rank("day_3"),
            followup_stage_list_rank("arrival"),
        )
        self.assertEqual(followup_language_label("en"), "英语")
        self.assertEqual(followup_language_label("es"), "西班牙语")


if __name__ == "__main__":
    unittest.main()
