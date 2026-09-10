from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.app import create_app  # noqa: E402
from assistant.database.engine import create_database_engine  # noqa: E402
from assistant.database.models import (  # noqa: E402
    Base,
    FollowupTask,
    Job,
    SampleCase,
    Store,
)
from assistant.jobs.handlers import followup_send as handler  # noqa: E402
from assistant.jobs.registry import HandlerFailure  # noqa: E402
from assistant.web.console_pages import followup_detail_data  # noqa: E402


class _CompletedProcess:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


class FollowupSendApiTests(unittest.TestCase):
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
                scheduled_for=date(2026, 9, 10),
                status="pending",
                action_kind="send_message",
                template_key="arrival_hero_video_en",
                message_preview="Hi creator! Just checking in.",
            )
            session.add(task)
            session.commit()
            self.task_id = task.id
        self.client = TestClient(
            create_app(port=8765), base_url="http://127.0.0.1:8765"
        )
        self.app = self.client.app
        self.app.state.session_factory = self.session_factory

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def _post(self, data: dict) -> object:
        return self.client.post(
            "/api/jobs/followups/send",
            data=data,
            headers={"Origin": "http://127.0.0.1:8765"},
        )

    def test_confirmation_is_required(self) -> None:
        response = self._post({"task_id": str(self.task_id)})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "operator-confirmation-required")

    def test_task_id_is_required(self) -> None:
        response = self._post({"confirmation": "y"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "followup-task-required")

    def test_unknown_task_is_rejected(self) -> None:
        response = self._post({"confirmation": "y", "task_id": "999999"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "followup-task-not-found")

    def test_valid_request_creates_fixed_job_and_deduplicates(self) -> None:
        response = self._post({"confirmation": "y", "task_id": str(self.task_id)})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["deduplicated"])
        with self.session_factory() as session:
            job = session.get(Job, payload["job_id"])
            self.assertEqual(job.job_type, "operator_followup_send")
            self.assertEqual(
                json.loads(job.result_summary),
                {"task_id": self.task_id},
            )
        second = self._post({"confirmation": "y", "task_id": str(self.task_id)})
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.json()["deduplicated"])
        self.assertEqual(second.json()["job_id"], payload["job_id"])

    def test_pending_job_for_another_task_is_rejected(self) -> None:
        first = self._post({"confirmation": "y", "task_id": str(self.task_id)})
        self.assertEqual(first.status_code, 200)
        second = self._post({"confirmation": "y", "task_id": str(self.task_id + 1000)})
        # 不存在的任务先命中 404；构造真实存在的第二个任务再验证 409。
        self.assertEqual(second.status_code, 404)
        with self.session_factory() as session:
            case = session.query(SampleCase).first()
            task = FollowupTask(
                sample_case_id=case.id,
                stage="arrival",
                scheduled_for=date(2026, 9, 9),
                status="pending",
                action_kind="send_message",
                template_key="arrival_hero_video_en",
                message_preview="Hi again!",
            )
            session.add(task)
            session.commit()
            other_task_id = task.id
        busy = self._post({"confirmation": "y", "task_id": str(other_task_id)})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual(busy.json()["detail"], "followup-send-busy")


class FollowupSendHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "test.sqlite3"
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            session.add(
                Job(
                    id="job-1",
                    job_type="operator_followup_send",
                    store_id=None,
                    status="running",
                    result_summary=json.dumps({"task_id": 42}),
                )
            )
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_handler_runs_the_fixed_argv(self) -> None:
        with (
            patch.object(
                handler,
                "ensure_user_dirs",
                return_value=Path(self.temporary_directory.name),
            ),
            patch.object(
                handler.subprocess,
                "run",
                return_value=_CompletedProcess(0),
            ) as run_process,
        ):
            result = handler.run_followup_send("job-1", self.session_factory)
        payload = json.loads(result)
        self.assertEqual(payload, {"mode": "followup_send", "task_id": 42})
        argv = run_process.call_args.args[0]
        self.assertEqual(argv[0], sys.executable)
        self.assertTrue(argv[1].endswith("scripts/send_followup_message.py"))
        self.assertEqual(
            argv[2:],
            [
                "--execute",
                "--yes",
                "--task-id",
                "42",
                "--ignore-job-id",
                "job-1",
            ],
        )
        self.assertFalse(run_process.call_args.kwargs["shell"])
        with self.session_factory() as session:
            job = session.get(Job, "job-1")
            self.assertTrue(job.log_path.endswith(".log"))

    def test_handler_fails_when_the_script_fails(self) -> None:
        with (
            patch.object(
                handler,
                "ensure_user_dirs",
                return_value=Path(self.temporary_directory.name),
            ),
            patch.object(
                handler.subprocess,
                "run",
                return_value=_CompletedProcess(4),
            ),
        ):
            with self.assertRaises(HandlerFailure) as context:
                handler.run_followup_send("job-1", self.session_factory)
        self.assertEqual(context.exception.error_code, "followup-send-failed-4")

    def test_handler_rejects_a_missing_task_id(self) -> None:
        with self.session_factory() as session:
            job = session.get(Job, "job-1")
            job.result_summary = "{}"
            session.commit()
        with self.assertRaises(HandlerFailure) as context:
            handler.run_followup_send("job-1", self.session_factory)
        self.assertEqual(context.exception.error_code, "followup-send-request-invalid")


class FollowupSendPayloadTests(unittest.TestCase):
    def _task(self, **overrides) -> SimpleNamespace:
        values = {
            "id": 1,
            "status": "pending",
            "sent_at": None,
            "send_result": "",
            "requires_manual_confirmation": False,
            "scheduled_for": date(2026, 9, 10),
            "template_key": "arrival_hero_video_en",
            "message_preview": "Hi creator! Just checking in.",
            "action_kind": "send_message",
            "stage": "arrival",
            "language": "en",
            "creator_type": "video",
            "suppressed_reason": "",
            "review_reason": "",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _case(self, **overrides) -> SimpleNamespace:
        values = {
            "creator_name": "creator",
            "platform_status": "processing",
            "platform_status_label": "处理中",
            "platform_status_stale": False,
            "product_id": "1732414717062320994",
            "main_order_id": "123456789012345",
            "creator_id": "creator-1",
            "feishu_record_id": "",
            "curr_status": 40,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_due_pending_task_is_send_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="2026-09-10",
            )
        self.assertTrue(payload["send_ready"])

    def test_future_or_reviewed_or_completed_tasks_are_not_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            future = followup_detail_data(
                self._task(scheduled_for=date(2026, 9, 11)),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="",
            )
            reviewed = followup_detail_data(
                self._task(status="needs_review", requires_manual_confirmation=True),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="",
            )
            completed = followup_detail_data(
                self._task(send_result="platform-sent"),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="",
            )
            claimed = followup_detail_data(
                self._task(send_result="sending"),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="",
            )
            shipped = followup_detail_data(
                self._task(),
                self._case(platform_status="shipped"),
                None,
                attachment_url="",
                scheduled_label="",
            )
        for payload in (future, reviewed, completed, claimed, shipped):
            self.assertFalse(payload["send_ready"])

    def test_content_found_is_ready_even_when_case_completed(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(
                    stage="content_found",
                    action_kind="acknowledge_content",
                ),
                self._case(platform_status="completed"),
                None,
                attachment_url="",
                scheduled_label="",
            )
        self.assertTrue(payload["send_ready"])


if __name__ == "__main__":
    unittest.main()
