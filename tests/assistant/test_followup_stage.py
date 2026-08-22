from __future__ import annotations

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.domain.followup_stage import (
    FEISHU_UNFULFILLED_COOPERATION_STATUS,
    days_since_delivery,
    latest_due_unpublished_stage,
    plan_followup_mutation,
)
from assistant.domain.timeutil import beijing_date, beijing_now
from scripts.lib.feishu_bitable import COOPERATION_STATUS_UNPUBLISHED


class FollowupStageTests(unittest.TestCase):
    def test_beijing_natural_day_handles_aware_and_naive_datetimes(self) -> None:
        utc_time = datetime(2026, 8, 21, 16, 30, tzinfo=timezone.utc)
        self.assertEqual(beijing_date(utc_time), date(2026, 8, 22))

        naive_beijing_time = datetime(2026, 8, 22, 1, 0)
        self.assertEqual(beijing_now(naive_beijing_time).hour, 1)
        self.assertEqual(
            days_since_delivery(naive_beijing_time, today=date(2026, 8, 25)),
            3,
        )

    def test_stage_calendar_has_only_latest_due_stage(self) -> None:
        expected_stages = {
            0: ("arrival", 0),
            3: ("day_3", 3),
            5: ("day_3", 3),
            7: ("day_7", 7),
            10: ("day_10_list", 10),
            11: ("day_10_list", 10),
            15: ("unfulfilled", 15),
            19: ("unfulfilled", 15),
        }
        for elapsed_days, expected_stage in expected_stages.items():
            with self.subTest(elapsed_days=elapsed_days):
                self.assertEqual(
                    latest_due_unpublished_stage(elapsed_days), expected_stage
                )
        self.assertIsNone(latest_due_unpublished_stage(-1))

    def test_first_generation_at_day_six_creates_only_day_three(self) -> None:
        mutation = plan_followup_mutation(
            existing_unpublished=[], days=6, has_confirmed_content=False
        )
        self.assertEqual([task["stage"] for task in mutation["create"]], ["day_3"])
        self.assertEqual(mutation["suppress"], [])

    def test_later_stage_suppresses_pending_earlier_stage(self) -> None:
        today = beijing_date(beijing_now())
        arrival_date = today - timedelta(days=3)
        mutation = plan_followup_mutation(
            existing_unpublished=[
                {
                    "stage": "arrival",
                    "scheduled_for": arrival_date.isoformat(),
                    "status": "pending",
                }
            ],
            days=3,
            has_confirmed_content=False,
        )
        self.assertEqual(mutation["create"][0]["stage"], "day_3")
        self.assertEqual(mutation["suppress"][0]["stage"], "arrival")
        self.assertEqual(
            mutation["suppress"][0]["reason"], "superseded_by_later_stage"
        )

    def test_existing_current_stage_is_not_duplicated_or_suppressed(self) -> None:
        today = beijing_date(beijing_now())
        mutation = plan_followup_mutation(
            existing_unpublished=[
                {"stage": "day_7", "scheduled_for": today, "status": "ready"}
            ],
            days=7,
            has_confirmed_content=False,
        )
        self.assertEqual(mutation, {"create": [], "suppress": []})

    def test_confirmed_content_suppresses_unsent_tasks_but_not_sent(self) -> None:
        scheduled_for = date(2026, 8, 22)
        mutation = plan_followup_mutation(
            existing_unpublished=[
                {
                    "stage": "day_10_list",
                    "scheduled_for": scheduled_for,
                    "status": "needs_review",
                },
                {
                    "stage": "day_7",
                    "scheduled_for": scheduled_for,
                    "status": "sent",
                },
            ],
            days=11,
            has_confirmed_content=True,
        )
        self.assertEqual(mutation["create"], [])
        self.assertEqual(len(mutation["suppress"]), 1)
        self.assertEqual(mutation["suppress"][0]["stage"], "day_10_list")
        self.assertEqual(mutation["suppress"][0]["reason"], "content_confirmed")

    def test_unfulfilled_status_matches_feishu_constant(self) -> None:
        self.assertEqual(
            FEISHU_UNFULFILLED_COOPERATION_STATUS,
            COOPERATION_STATUS_UNPUBLISHED,
        )
        self.assertNotEqual(FEISHU_UNFULFILLED_COOPERATION_STATUS, "待发布")


if __name__ == "__main__":
    unittest.main()
