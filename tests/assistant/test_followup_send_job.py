from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
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
    Shipment,
    Store,
)
from assistant.domain.followup_labels import followup_action_display  # noqa: E402
from assistant.domain.timeutil import beijing_now  # noqa: E402
from assistant.jobs.handlers import followup_send as handler  # noqa: E402
from assistant.jobs.registry import HandlerFailure  # noqa: E402
from assistant.services.followup_service import FollowupService  # noqa: E402
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

    def test_preview_conflicts_with_a_running_page_job(self) -> None:
        with self.session_factory() as session:
            session.add(
                Job(id="job-9", job_type="operator_tracking", status="running")
            )
            session.commit()
        response = self.client.post(
            f"/api/followups/{self.task_id}/preview-send",
            data={},
            headers={"Origin": "http://127.0.0.1:8765"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"], "store-busy")


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
            "send_confirmation": "",
            "last_error": "",
            "previewed_at": None,
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

    def _shipment(self, *, delivered_days_ago: int = 0) -> SimpleNamespace:
        return SimpleNamespace(
            delivered_at=beijing_now() - timedelta(days=delivered_days_ago),
            tracking_display="",
        )

    def test_due_pending_task_is_send_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                self._shipment(),
                attachment_url="",
                scheduled_label="2026-09-10",
            )
        self.assertTrue(payload["send_ready"])

    def test_future_or_reviewed_or_completed_tasks_are_not_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            shipment = self._shipment()
            future = followup_detail_data(
                self._task(scheduled_for=date(2026, 9, 11)),
                self._case(),
                shipment,
                attachment_url="",
                scheduled_label="",
            )
            reviewed = followup_detail_data(
                self._task(status="needs_review", requires_manual_confirmation=True),
                self._case(),
                shipment,
                attachment_url="",
                scheduled_label="",
            )
            completed = followup_detail_data(
                self._task(send_result="platform-sent"),
                self._case(),
                shipment,
                attachment_url="",
                scheduled_label="",
            )
            claimed = followup_detail_data(
                self._task(send_result="sending"),
                self._case(),
                shipment,
                attachment_url="",
                scheduled_label="",
            )
            shipped = followup_detail_data(
                self._task(),
                self._case(platform_status="shipped"),
                shipment,
                attachment_url="",
                scheduled_label="",
            )
        for payload in (future, reviewed, completed, claimed, shipped):
            self.assertFalse(payload["send_ready"])

    def test_superseded_stage_is_not_send_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
            )
        self.assertFalse(payload["send_ready"])
        self.assertIn("已过期", payload["note"])

    def test_current_stage_stays_send_ready_later_in_the_calendar(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(stage="day_3", template_key="day3_hero_video_en"),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
            )
        self.assertTrue(payload["send_ready"])
        self.assertEqual(payload["note"], "")

    def test_missing_delivery_date_is_not_send_ready(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                None,
                attachment_url="",
                scheduled_label="",
            )
        self.assertFalse(payload["send_ready"])

    def test_current_stage_points_to_the_pending_node_task(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
                case_tasks=[
                    {
                        "id": 1,
                        "stage": "arrival",
                        "scheduled_for": date(2026, 9, 5),
                        "sent_at": None,
                        "send_result": "",
                    },
                    {
                        "id": 9,
                        "stage": "day_3",
                        "scheduled_for": date(2026, 9, 8),
                        "sent_at": None,
                        "send_result": "",
                    },
                ],
            )
        self.assertEqual(payload["current_stage_label"], "到货后第 3 天")
        self.assertEqual(payload["current_task_url"], "/followups/9")
        self.assertIn("打开当前应做", payload["current_stage_note"])

    def test_current_stage_marks_this_task(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(id=9, stage="day_3", template_key="day3_hero_video_en"),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
                case_tasks=[
                    {
                        "id": 9,
                        "stage": "day_3",
                        "scheduled_for": date(2026, 9, 8),
                        "sent_at": None,
                        "send_result": "",
                    }
                ],
            )
        self.assertEqual(payload["current_stage_label"], "到货后第 3 天")
        self.assertEqual(payload["current_task_url"], "")
        self.assertIn("就是本条", payload["current_stage_note"])

    def test_current_stage_without_generated_task(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
                case_tasks=[],
            )
        self.assertEqual(payload["current_stage_label"], "到货后第 3 天")
        self.assertEqual(payload["current_task_url"], "")
        self.assertIn("尚未生成", payload["current_stage_note"])

    def test_current_stage_hidden_for_finished_case(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(stage="content_found", action_kind="acknowledge_content"),
                self._case(platform_status="completed"),
                self._shipment(),
                attachment_url="",
                scheduled_label="",
                case_tasks=[],
            )
        self.assertEqual(payload["current_stage_label"], "")
        self.assertEqual(payload["current_task_url"], "")

    def test_superseded_record_is_not_shown_as_pending(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(
                    status="suppressed",
                    suppressed_reason="superseded_by_later_stage",
                ),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
                case_tasks=[],
            )
        self.assertEqual(payload["action_label"], "不再发送（已由新阶段取代）")
        self.assertFalse(payload["send_ready"])

    def test_delivery_shows_beijing_time_and_elapsed_days(self) -> None:
        shipment = self._shipment(delivered_days_ago=5)
        payload = followup_detail_data(
            self._task(),
            self._case(),
            shipment,
            attachment_url="",
            scheduled_label="",
            case_tasks=[],
        )
        local = beijing_now(shipment.delivered_at)
        self.assertEqual(
            payload["delivered_text"],
            f"{local:%Y-%m-%d %H:%M}（北京时间）· 到货已 5 天",
        )

    def test_current_stage_due_spells_out_the_delay(self) -> None:
        with patch(
            "assistant.web.console_pages.beijing_now",
            return_value=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ):
            payload = followup_detail_data(
                self._task(stage="day_3", template_key="day3_hero_video_en"),
                self._case(),
                self._shipment(delivered_days_ago=5),
                attachment_url="",
                scheduled_label="",
                case_tasks=[],
            )
        self.assertEqual(payload["current_stage_due"], "应做 09-08 · 已逾期 2 天")

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


class FollowupAcknowledgeTests(unittest.TestCase):
    """Content-thanks tasks can be closed locally when the DM was sent elsewhere."""

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
                platform_status="completed",
            )
            session.add(sample_case)
            session.flush()
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage="content_found",
                scheduled_for=date(2026, 9, 10),
                status="pending",
                action_kind="acknowledge_content",
                template_key="video_found_en",
                message_preview="We just saw the video you created for us...",
            )
            session.add(task)
            session.commit()
            self.task_id = task.id
            self.sample_case_id = sample_case.id

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def load_task(self) -> FollowupTask:
        with self.session_factory() as session:
            return session.get(FollowupTask, self.task_id)

    def test_mark_message_sent_accepts_content_thanks_tasks(self) -> None:
        result = FollowupService(self.session_factory).mark_message_sent(self.task_id)
        self.assertTrue(result["ok"])
        task = self.load_task()
        self.assertEqual(task.send_result, "marked-sent")
        self.assertIsNotNone(task.sent_at)
        self.assertEqual(
            followup_action_display(
                "acknowledge_content",
                sent_at=task.sent_at,
                send_result=task.send_result,
            ),
            "已发送内容感谢（本地标记）",
        )

    def test_acknowledge_payload_exposes_local_mark(self) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, self.task_id)
            sample_case = session.get(SampleCase, self.sample_case_id)
            payload = followup_detail_data(
                task,
                sample_case,
                None,
                attachment_url="",
                scheduled_label="",
            )
        self.assertTrue(payload["can_acknowledge"])
        self.assertTrue(payload["send_ready"])

    def test_list_only_tasks_are_still_rejected(self) -> None:
        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage="day_10_list",
                scheduled_for=date(2026, 9, 10),
                status="pending",
                action_kind="list_only",
                template_key="",
                message_preview="名单",
            )
            session.add(task)
            session.commit()
            list_task_id = task.id
        with self.assertRaises(ValueError):
            FollowupService(self.session_factory).mark_message_sent(list_task_id)


class FollowupPreviewStatusTests(unittest.TestCase):
    """The detail page shows the last preview/attempt outcome for a task."""

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
                platform_status="processing",
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
            self.sample_case_id = sample_case.id

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def load_task(self) -> FollowupTask:
        with self.session_factory() as session:
            return session.get(FollowupTask, self.task_id)

    def payload(self, task: FollowupTask | None = None) -> dict:
        with self.session_factory() as session:
            resolved_task = task or session.get(FollowupTask, self.task_id)
            sample_case = session.get(SampleCase, self.sample_case_id)
            return followup_detail_data(
                resolved_task,
                sample_case,
                None,
                attachment_url="",
                scheduled_label="",
            )

    def record(self, **values) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, self.task_id)
            for key, value in values.items():
                setattr(task, key, value)
            session.commit()

    def add_job(self, *, job_type: str, status: str) -> None:
        with self.session_factory() as session:
            session.add(
                Job(
                    id=f"job-{job_type}-{status}",
                    job_type=job_type,
                    status=status,
                    result_summary="{}",
                )
            )
            session.commit()

    def test_untouched_task_has_no_preview_line(self) -> None:
        payload = self.payload()
        self.assertEqual(payload["preview_state"], "")
        self.assertEqual(payload["preview_label"], "")

    def test_successful_preview_is_reported(self) -> None:
        self.record(send_confirmation="dry-run")
        payload = self.payload()
        self.assertEqual(payload["preview_state"], "ok")
        self.assertIn("预演成功", payload["preview_label"])

    def test_failure_reason_is_reported(self) -> None:
        self.record(last_error="target-conversation-not-visible")
        payload = self.payload()
        self.assertEqual(payload["preview_state"], "issue")
        self.assertIn("target-conversation-not-visible", payload["preview_label"])

    def test_preview_success_clears_the_previous_error(self) -> None:
        self.record(last_error="missing-store")
        with patch(
            "lib.im_api.send_direct_message",
            return_value={"ok": True, "status": "dry-run"},
        ):
            result = FollowupService(self.session_factory).preview_followup_message(
                self.task_id
            )
        self.assertTrue(result["ok"])
        task = self.load_task()
        self.assertEqual(task.send_confirmation, "dry-run")
        self.assertEqual(task.last_error, "")
        self.assertEqual(self.payload()["preview_state"], "ok")

    def test_held_preview_records_the_hold_reason(self) -> None:
        with patch(
            "lib.im_api.send_direct_message",
            return_value={
                "ok": False,
                "status": "held",
                "reason": "content-thanks-found",
            },
        ):
            result = FollowupService(self.session_factory).preview_followup_message(
                self.task_id
            )
        self.assertEqual(result["status"], "held")
        task = self.load_task()
        self.assertEqual(task.send_confirmation, "held")
        self.assertIn("content-thanks-found", task.last_error)
        self.assertIn("已有感谢话术", task.last_error)
        payload = self.payload()
        self.assertEqual(payload["preview_state"], "issue")
        self.assertIn("content-thanks-found", payload["preview_label"])

    def test_preview_passes_the_duplicate_guards_to_the_send_helper(self) -> None:
        from lib.im_api import thread_thanks_hold_reason

        captured_kwargs: dict = {}

        def fake_send_direct_message(store_id, body, **kwargs):
            captured_kwargs.update(kwargs)
            captured_kwargs["body"] = body
            return {"ok": True, "status": "dry-run"}

        with patch("lib.im_api.send_direct_message", side_effect=fake_send_direct_message):
            FollowupService(self.session_factory).preview_followup_message(self.task_id)

        already_sent_predicate = captured_kwargs["already_sent_predicate"]
        self.assertTrue(already_sent_predicate(captured_kwargs["body"]))
        self.assertFalse(already_sent_predicate("an unrelated reply from the creator"))
        self.assertIs(captured_kwargs["hold_predicate"], thread_thanks_hold_reason)

    def test_preview_of_an_already_sent_thread_is_flagged(self) -> None:
        with patch(
            "lib.im_api.send_direct_message",
            return_value={"ok": True, "status": "already-sent"},
        ):
            result = FollowupService(self.session_factory).preview_followup_message(
                self.task_id
            )
        self.assertEqual(result["status"], "already-sent")
        task = self.load_task()
        self.assertEqual(task.send_confirmation, "already-sent")
        self.assertEqual(task.last_error, "")
        payload = self.payload()
        self.assertEqual(payload["preview_state"], "issue")
        self.assertIn("不要重复发送", payload["preview_label"])

    def test_preview_passes_the_window_guard_when_a_delivery_date_exists(self) -> None:
        with self.session_factory() as session:
            session.add(
                Shipment(
                    sample_case_id=self.sample_case_id,
                    delivered_at=datetime(2026, 9, 8, 1, 0, tzinfo=timezone.utc),
                    status_category="delivered",
                )
            )
            session.commit()
        captured_kwargs: dict = {}

        def fake_send_direct_message(store_id, body, **kwargs):
            captured_kwargs.update(kwargs)
            return {"ok": True, "status": "dry-run"}

        with patch("lib.im_api.send_direct_message", side_effect=fake_send_direct_message):
            FollowupService(self.session_factory).preview_followup_message(self.task_id)

        window_predicate = captured_kwargs["already_sent_message_predicate"]
        self.assertIsNotNone(window_predicate)
        self.assertEqual(
            window_predicate([{"is_self": True, "time_text": "Sep 9 7:00 AM"}]),
            "self-message-in-window",
        )
        self.assertEqual(
            window_predicate([{"is_self": True, "time_text": "Sep 20 7:00 AM"}]),
            "",
        )

    def test_preview_without_a_delivery_date_passes_no_window_guard(self) -> None:
        captured_kwargs: dict = {}

        def fake_send_direct_message(store_id, body, **kwargs):
            captured_kwargs.update(kwargs)
            return {"ok": True, "status": "dry-run"}

        with patch("lib.im_api.send_direct_message", side_effect=fake_send_direct_message):
            FollowupService(self.session_factory).preview_followup_message(self.task_id)

        self.assertIsNone(captured_kwargs["already_sent_message_predicate"])

    def test_preview_timestamp_is_shown(self) -> None:
        self.record(send_confirmation="dry-run", previewed_at=datetime(2026, 9, 10, 12, 34))
        payload = self.payload()
        self.assertEqual(payload["preview_at"], "2026-09-10 12:34")

    def test_preview_is_blocked_while_a_page_job_runs(self) -> None:
        self.add_job(job_type="operator_tracking", status="running")
        with patch("lib.im_api.send_direct_message") as send_message:
            with self.assertRaises(ValueError) as context:
                FollowupService(self.session_factory).preview_followup_message(
                    self.task_id
                )
        self.assertEqual(str(context.exception), "store-busy")
        send_message.assert_not_called()

    def test_preview_resumes_after_the_job_finishes(self) -> None:
        self.add_job(job_type="operator_tracking", status="succeeded")
        with patch(
            "lib.im_api.send_direct_message",
            return_value={"ok": True, "status": "dry-run"},
        ):
            result = FollowupService(self.session_factory).preview_followup_message(
                self.task_id
            )
        self.assertTrue(result["ok"])

    def test_preview_ignores_jobs_that_do_not_touch_the_page(self) -> None:
        self.add_job(job_type="report_export", status="running")
        with patch(
            "lib.im_api.send_direct_message",
            return_value={"ok": True, "status": "dry-run"},
        ):
            result = FollowupService(self.session_factory).preview_followup_message(
                self.task_id
            )
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
