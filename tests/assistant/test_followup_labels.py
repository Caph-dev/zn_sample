from __future__ import annotations

import unittest
from datetime import datetime, timezone

from assistant.domain.followup_labels import (
    followup_action_display,
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
            "已发跟进私信",
        )
        self.assertEqual(
            followup_action_display(
                "send_message",
                sent_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
            ),
            "已发跟进私信",
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


if __name__ == "__main__":
    unittest.main()
