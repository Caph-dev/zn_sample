from __future__ import annotations

import json
import sys
import tempfile
import unittest
from argparse import Namespace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.database.engine import create_database_engine  # noqa: E402
from assistant.database.models import (  # noqa: E402
    Base,
    FollowupTask,
    Job,
    SampleCase,
    Store,
)
from send_followup_message import (  # noqa: E402
    claim_task,
    find_running_jobs,
    record_send_result,
    release_stale_claims,
    resolve_attachment_path,
    run,
    select_sendable_tasks,
    write_pre_execute_backup,
)

TODAY = date(2026, 9, 10)


class SendFollowupSelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "test.sqlite3"
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_task(
        self,
        *,
        creator_name: str = "creator",
        stage: str = "arrival",
        action_kind: str = "send_message",
        status: str = "pending",
        scheduled_for: date | None = TODAY,
        platform_status: str = "processing",
        platform_status_stale: bool = False,
        send_result: str = "",
        requires_manual_confirmation: bool = False,
        template_key: str = "arrival_hero_video_en",
        message_preview: str = "Hi creator! Just checking in.",
        attachment_key: str = "",
        store_ziniao_id: str = "store-1",
    ) -> None:
        with self.session_factory() as session:
            store = session.scalar(
                select(Store).where(Store.ziniao_store_id == store_ziniao_id)
            )
            if store is None:
                store = Store(ziniao_store_id=store_ziniao_id, store_name="store")
                session.add(store)
                session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id=f"id-{creator_name}",
                creator_name=creator_name,
                apply_id=f"apply-{creator_name}",
                product_id="1732414717062320994",
                platform_status=platform_status,
                platform_status_stale=platform_status_stale,
            )
            session.add(sample_case)
            session.flush()
            session.add(
                FollowupTask(
                    sample_case_id=sample_case.id,
                    stage=stage,
                    scheduled_for=scheduled_for,
                    status=status,
                    action_kind=action_kind,
                    send_result=send_result,
                    requires_manual_confirmation=requires_manual_confirmation,
                    template_key=template_key,
                    message_preview=message_preview,
                    attachment_key=attachment_key,
                )
            )
            session.commit()

    def select(self, **overrides):
        parameters = {
            "today": TODAY,
            "stage": "",
            "creator_id": "",
            "creator_name": "",
            "limit": 50,
        }
        parameters.update(overrides)
        with self.session_factory() as session:
            return select_sendable_tasks(session, **parameters)

    def test_due_pending_arrival_is_selected(self) -> None:
        self.add_task()
        rows = self.select()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["stage"], "arrival")
        self.assertEqual(row["creator_id"], "id-creator")
        self.assertEqual(row["store_ziniao_id"], "store-1")
        self.assertEqual(
            row["idempotency_key"],
            "store-1|id-creator|1732414717062320994|arrival|2026-09-10",
        )

    def test_completed_and_review_tasks_are_not_selected(self) -> None:
        self.add_task(creator_name="sent", send_result="platform-sent")
        self.add_task(creator_name="marked", send_result="marked-sent")
        self.add_task(creator_name="unknown", status="needs_review")
        self.add_task(creator_name="manual", requires_manual_confirmation=True)
        self.assertEqual(self.select(), [])

    def test_future_or_missing_schedule_is_not_selected(self) -> None:
        self.add_task(creator_name="future", scheduled_for=date(2026, 9, 11))
        self.add_task(creator_name="none", scheduled_for=None)
        self.assertEqual(self.select(), [])

    def test_non_processing_or_stale_cases_are_not_selected(self) -> None:
        self.add_task(creator_name="shipped", platform_status="shipped")
        self.add_task(creator_name="stale", platform_status_stale=True)
        self.assertEqual(self.select(), [])

    def test_list_only_and_unfulfilled_stages_are_not_selected(self) -> None:
        self.add_task(creator_name="list", stage="day_10_list", action_kind="list_only")
        self.add_task(
            creator_name="unfulfilled",
            stage="unfulfilled",
            action_kind="mark_unfulfilled",
        )
        self.assertEqual(self.select(), [])

    def test_content_found_is_selected_even_when_case_completed(self) -> None:
        self.add_task(
            creator_name="thanks",
            stage="content_found",
            action_kind="acknowledge_content",
            platform_status="completed",
        )
        rows = self.select()
        self.assertEqual([row["stage"] for row in rows], ["content_found"])

    def test_stage_and_creator_filters_and_limit(self) -> None:
        self.add_task(creator_name="alpha", stage="day_3")
        self.add_task(creator_name="beta", stage="arrival")
        self.add_task(creator_name="gamma", stage="arrival")
        self.assertEqual(
            [row["creator_name"] for row in self.select(stage="arrival")],
            ["beta", "gamma"],
        )
        self.assertEqual(
            [row["creator_name"] for row in self.select(creator_name="beta")],
            ["beta"],
        )
        self.assertEqual(len(self.select(limit=1)), 1)

    def test_claimed_task_is_not_selected(self) -> None:
        self.add_task(creator_name="claimed", send_result="sending")
        self.assertEqual(self.select(), [])

    def test_task_id_filter_selects_one_task(self) -> None:
        self.add_task(creator_name="alpha")
        self.add_task(creator_name="beta")
        with self.session_factory() as session:
            task_ids = {
                row["creator_name"]: row["task_id"] for row in self.select()
            }
        rows = self.select(task_id=task_ids["beta"])
        self.assertEqual([row["creator_name"] for row in rows], ["beta"])


class SendFollowupRecordingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "test.sqlite3"
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            store = Store(ziniao_store_id="store-1", store_name="store")
            session.add(store)
            session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id="creator-1",
                creator_name="creator",
                apply_id="apply-1",
                product_id="1732414717062320994",
            )
            session.add(sample_case)
            session.flush()
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage="arrival",
                scheduled_for=TODAY,
                status="pending",
                action_kind="send_message",
                template_key="arrival_hero_video_en",
                message_preview="Hi creator! Just checking in.",
            )
            session.add(task)
            session.commit()
            self.task_id = task.id

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def load_task(self) -> FollowupTask:
        with self.session_factory() as session:
            return session.get(FollowupTask, self.task_id)

    def test_confirmed_send_marks_platform_sent(self) -> None:
        row = {"task_id": self.task_id}
        record_send_result(
            self.session_factory,
            row,
            {"ok": True, "status": "sent", "image": None},
        )
        task = self.load_task()
        self.assertEqual(task.send_result, "platform-sent")
        self.assertEqual(task.send_confirmation, "api-confirmed")
        self.assertIsNotNone(task.sent_at)
        self.assertEqual(task.status, "pending")

    def test_already_sent_is_recorded_without_resend(self) -> None:
        row = {"task_id": self.task_id}
        record_send_result(
            self.session_factory,
            row,
            {"ok": True, "status": "already-sent"},
        )
        task = self.load_task()
        self.assertEqual(task.send_result, "platform-sent")
        self.assertEqual(task.send_confirmation, "already-sent")

    def test_send_unknown_moves_task_to_review_without_retry(self) -> None:
        row = {"task_id": self.task_id}
        record_send_result(
            self.session_factory,
            row,
            {"ok": False, "status": "send-unknown", "error": "发送后未确认"},
        )
        task = self.load_task()
        self.assertEqual(task.send_result, "send-unknown")
        self.assertEqual(task.status, "needs_review")
        self.assertEqual(task.review_reason, "send_unknown_needs_review")
        self.assertTrue(task.requires_manual_confirmation)
        self.assertIsNone(task.sent_at)

    def test_image_failure_keeps_text_record_and_flags_review(self) -> None:
        row = {"task_id": self.task_id}
        record_send_result(
            self.session_factory,
            row,
            {
                "ok": True,
                "status": "sent",
                "image": {"ok": False, "reason": "im-sdk-provider-not-found"},
            },
        )
        task = self.load_task()
        self.assertEqual(task.send_result, "platform-sent")
        self.assertIsNotNone(task.sent_at)
        self.assertEqual(task.status, "needs_review")
        self.assertEqual(task.review_reason, "image_send_failed")
        self.assertIn("im-sdk-provider-not-found", task.last_error)

    def test_plain_failure_keeps_task_pending(self) -> None:
        row = {"task_id": self.task_id}
        record_send_result(
            self.session_factory,
            row,
            {"ok": False, "error": "target-conversation-not-visible"},
        )
        task = self.load_task()
        self.assertIsNone(task.sent_at)
        self.assertEqual(task.send_result, "")
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.last_error, "target-conversation-not-visible")


class SendFollowupBackupTests(unittest.TestCase):
    def test_backup_file_contains_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            rows = [{"task_id": 1, "creator_name": "creator"}]
            path = write_pre_execute_backup(rows, exports_directory=Path(directory))
            self.assertTrue(path.is_file())
            self.assertIn("_pre_execute.json", path.name)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["rows"], rows)
            self.assertIn("generated_at", payload)

    def test_attachment_path_resolves_repo_relative_key(self) -> None:
        row = {
            "attachment_key": "样品申请筛查sop/图片和附件/2-查看到货+达人跟进-b05.png"
        }
        resolved = resolve_attachment_path(row)
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.is_file())
        self.assertIsNone(resolve_attachment_path({"attachment_key": "missing.png"}))
        self.assertIsNone(resolve_attachment_path({}))


class SendFollowupGateTests(unittest.TestCase):
    def _args(self, **overrides) -> Namespace:
        values = {
            "store_id": None,
            "store_name": None,
            "no_default_store": False,
            "stage": None,
            "creator_id": None,
            "creator_name": None,
            "task_id": 0,
            "ignore_job_id": "",
            "execute": True,
            "yes": False,
            "execute_limit": 1,
            "claim_stale_minutes": 30,
            "write_feishu": False,
            "send_wait": 2.5,
            "verbose": False,
        }
        values.update(overrides)
        return Namespace(**values)

    def test_execute_without_yes_exits_two_before_touching_data(self) -> None:
        import send_followup_message

        with (
            patch.object(send_followup_message, "load_dotenv"),
            patch.object(send_followup_message, "configure_logging"),
            patch.object(send_followup_message, "set_verbose"),
        ):
            self.assertEqual(run(self._args()), 2)


class FollowupClaimTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_file = Path(self.temporary_directory.name) / "test.sqlite3"
        self.engine = create_database_engine(self.database_file)
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            store = Store(ziniao_store_id="store-1", store_name="store")
            session.add(store)
            session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id="creator-1",
                creator_name="creator",
                apply_id="apply-1",
                product_id="1732414717062320994",
                platform_status="processing",
            )
            session.add(sample_case)
            session.flush()
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage="arrival",
                scheduled_for=TODAY,
                status="pending",
                action_kind="send_message",
                template_key="arrival_hero_video_en",
                message_preview="Hi creator! Just checking in.",
            )
            session.add(task)
            session.commit()
            self.task_id = task.id

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def load_task(self) -> FollowupTask:
        with self.session_factory() as session:
            return session.get(FollowupTask, self.task_id)

    def make_claim_stale(self, *, hours: int = 2) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, self.task_id)
            task.send_result = "sending"
            task.updated_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
                hours=hours
            )
            session.commit()


class SendFollowupClaimTests(FollowupClaimTestBase):
    def test_claim_wins_once_and_blocks_a_second_runner(self) -> None:
        self.assertTrue(claim_task(self.session_factory, self.task_id))
        self.assertEqual(self.load_task().send_result, "sending")
        self.assertFalse(claim_task(self.session_factory, self.task_id))

    def test_claim_rejects_completed_task(self) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, self.task_id)
            task.send_result = "platform-sent"
            session.commit()
        self.assertFalse(claim_task(self.session_factory, self.task_id))

    def test_plain_failure_releases_the_claim(self) -> None:
        self.assertTrue(claim_task(self.session_factory, self.task_id))
        record_send_result(
            self.session_factory,
            {"task_id": self.task_id},
            {"ok": False, "error": "conversation-not-found"},
        )
        task = self.load_task()
        self.assertEqual(task.send_result, "")
        self.assertEqual(task.status, "pending")
        self.assertEqual(task.last_error, "conversation-not-found")

    def test_stale_claim_moves_to_review_without_resend(self) -> None:
        self.make_claim_stale(hours=2)
        stale = release_stale_claims(self.session_factory, stale_minutes=30, apply=True)
        self.assertEqual([item["task_id"] for item in stale], [self.task_id])
        task = self.load_task()
        self.assertEqual(task.send_result, "send-unknown")
        self.assertEqual(task.status, "needs_review")
        self.assertEqual(task.review_reason, "send_interrupted")
        self.assertTrue(task.requires_manual_confirmation)
        self.assertIn("进程中断", task.last_error)

    def test_fresh_claim_is_not_released(self) -> None:
        self.assertTrue(claim_task(self.session_factory, self.task_id))
        stale = release_stale_claims(self.session_factory, stale_minutes=30, apply=True)
        self.assertEqual(stale, [])
        self.assertEqual(self.load_task().send_result, "sending")

    def test_dry_run_reports_stale_claim_without_modifying_it(self) -> None:
        self.make_claim_stale(hours=2)
        stale = release_stale_claims(self.session_factory, stale_minutes=30, apply=False)
        self.assertEqual(len(stale), 1)
        self.assertEqual(self.load_task().send_result, "sending")


class SendFollowupMutexTests(FollowupClaimTestBase):
    def add_job(self, job_type: str, status: str) -> None:
        with self.session_factory() as session:
            session.add(
                Job(id=f"job-{job_type}-{status}", job_type=job_type, status=status)
            )
            session.commit()

    def test_page_jobs_block_but_local_jobs_do_not(self) -> None:
        self.add_job("operator_pipeline", "running")
        self.add_job("shipment_sync", "pending")
        self.add_job("report_export", "running")
        jobs = find_running_jobs(self.session_factory)
        self.assertEqual(
            {job["job_type"] for job in jobs},
            {"operator_pipeline", "shipment_sync"},
        )

    def test_finished_jobs_do_not_block(self) -> None:
        self.add_job("operator_pipeline", "succeeded")
        self.assertEqual(find_running_jobs(self.session_factory), [])

    def test_run_returns_three_when_the_store_page_is_busy(self) -> None:
        import send_followup_message

        args = SendFollowupGateTests()._args(execute=False)
        with (
            patch.object(send_followup_message, "load_dotenv"),
            patch.object(send_followup_message, "configure_logging"),
            patch.object(send_followup_message, "set_verbose"),
            patch.object(
                send_followup_message,
                "find_running_jobs",
                return_value=[
                    {"id": "job-1", "job_type": "operator_pipeline", "status": "running"}
                ],
            ),
            patch(
                "assistant.database.engine.create_database_engine",
                return_value=self.engine,
            ),
            patch(
                "assistant.paths.database_path",
                return_value=self.database_file,
            ),
        ):
            self.assertEqual(run(args), 3)

    def test_unknown_task_exits_four(self) -> None:
        import send_followup_message

        args = SendFollowupGateTests()._args(execute=False, task_id=999999)
        with (
            patch.object(send_followup_message, "load_dotenv"),
            patch.object(send_followup_message, "configure_logging"),
            patch.object(send_followup_message, "set_verbose"),
            patch.object(send_followup_message, "find_running_jobs", return_value=[]),
            patch(
                "assistant.database.engine.create_database_engine",
                return_value=self.engine,
            ),
            patch(
                "assistant.paths.database_path",
                return_value=self.database_file,
            ),
        ):
            self.assertEqual(run(args), 4)

    def test_running_jobs_can_ignore_its_own_job(self) -> None:
        self.add_job("operator_followup_send", "running")
        self.assertEqual(len(find_running_jobs(self.session_factory)), 1)
        self.assertEqual(
            find_running_jobs(
                self.session_factory,
                ignore_job_id="job-operator_followup_send-running",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
