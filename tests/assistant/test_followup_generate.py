from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, SampleCase, Shipment, Store
from assistant.services.followup_service import FollowupService
from assistant.app import create_app


class FollowupGenerateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temporary_directory.name) / "test.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_case(self, *, curr_status: int, delivered_at=None, needs_confirmation=False, product_id="1732414717062320994", **values) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="store", store_name="store")
            session.add(store); session.flush()
            case = SampleCase(store_id=store.id, creator_id="creator", creator_name="creator", apply_id=str(curr_status), product_id=product_id, curr_status=curr_status, **values)
            session.add(case); session.flush()
            session.add(Shipment(sample_case_id=case.id, status_category="delivered", delivered_at=delivered_at, needs_delivery_time_confirmation=needs_confirmation))
            session.commit()

    def test_missing_delivery_time_creates_one_sentinel_confirmation(self) -> None:
        self.add_case(curr_status=40, needs_confirmation=True)
        service = FollowupService(self.session_factory)
        service.generate(); service.generate()
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].stage, "confirm_delivery_time")
            self.assertEqual(str(tasks[0].scheduled_for), "1970-01-01")

    def test_shipped_status_cannot_enter_day_10_calendar(self) -> None:
        self.add_case(curr_status=30, delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual(session.scalars(select(FollowupTask)).all(), [])

    def test_calendar_uses_latest_due_stage_and_only_processing_cases(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 6, tzinfo=timezone.utc), is_video_creator="是", language="en")
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual([task.stage for task in session.scalars(select(FollowupTask)).all()], ["day_3"])

    def test_generate_reuses_injected_today_for_stage_and_scheduled_for(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 6, 16, 0, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
        )
        frozen_now = datetime(2026, 8, 12, 0, 0, tzinfo=timezone.utc)
        planner_default_now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        service = FollowupService(self.session_factory)

        with patch(
            "assistant.domain.followup_stage.beijing_now",
            return_value=planner_default_now,
        ) as planner_clock:
            first_result = service.generate(frozen_now)
            second_result = service.generate(frozen_now)

        planner_clock.assert_not_called()
        self.assertEqual(first_result, {"created": 1})
        self.assertEqual(second_result, {"created": 0})
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0].stage, "day_3")
            self.assertEqual(tasks[0].scheduled_for, date(2026, 8, 10))
            self.assertNotEqual(tasks[0].status, "suppressed")

    def test_day_11_processing_gets_list_but_shipped_does_not(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc), is_video_creator="是", language="en")
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual([task.stage for task in session.scalars(select(FollowupTask)).all()], ["day_10_list"])

    def test_wrong_product_and_b005_sku_cannot_authorize_task_or_image(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), product_id="wrong", resolved_sku="B005")
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual(session.scalars(select(FollowupTask)).all(), [])

    def test_ambiguous_creator_flags_require_review(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), is_video_creator="是", is_live_creator="是", language="en")
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertTrue(task.requires_manual_confirmation)
            self.assertEqual(task.status, "needs_review")

    def test_readonly_feishu_spanish_language_requires_matched_option(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), is_video_creator="是")
        with (
            patch("lib.app_config.load_bitable_settings", return_value={"app_id": "app", "app_secret": "secret"}),
            patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"),
            patch("lib.feishu_bitable.list_sample_product_options", return_value=["B005（商品）"]),
            patch("lib.feishu_bitable.find_duplicate_record", return_value={"fields": {"使用语言": "西班牙语"}}) as find_record,
        ):
            FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.language, "es")
        find_record.assert_called_once()

    def test_no_product_option_skips_record_lookup(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), is_video_creator="是")
        with (
            patch("lib.app_config.load_bitable_settings", return_value={"app_id": "app", "app_secret": "secret"}),
            patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"),
            patch("lib.feishu_bitable.list_sample_product_options", return_value=[]),
            patch("lib.feishu_bitable.find_duplicate_record") as find_record,
        ):
            FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        find_record.assert_not_called()

    def test_language_lookup_failure_warns_and_keeps_successful_review_task(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), is_video_creator="是")
        warnings = []
        with (
            patch("lib.app_config.load_bitable_settings", return_value={"app_id": "app", "app_secret": "secret"}),
            patch("lib.feishu_bitable.get_bitable_access_token", side_effect=RuntimeError("offline")),
        ):
            result = FollowupService(self.session_factory, warning=warnings.append).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(result["created"], 1)
            self.assertTrue(task.requires_manual_confirmation)
        self.assertEqual(len(warnings), 1)

    def test_followup_filters_and_local_form_controls_refresh_detail(self) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="ui-store", store_name="UI")
            session.add(store); session.flush()
            processing_case = SampleCase(store_id=store.id, creator_id="ui-40", creator_name="visible_creator", apply_id="ui-40", product_id="1732414717062320994", curr_status=40, language="en")
            shipped_case = SampleCase(store_id=store.id, creator_id="ui-30", creator_name="hidden_creator", apply_id="ui-30", product_id="1732414717062320994", curr_status=30)
            session.add_all([processing_case, shipped_case]); session.flush()
            session.add(Shipment(sample_case_id=processing_case.id, tracking_display="tracking-visible"))
            session.add(FollowupTask(sample_case_id=processing_case.id, stage="arrival", scheduled_for=datetime(2026, 8, 12).date(), status="needs_review", language="en", message_preview="This preview is safely longer than twelve characters."))
            session.add(FollowupTask(sample_case_id=shipped_case.id, stage="day_3", scheduled_for=datetime(2026, 8, 12).date(), status="pending", language="es"))
            session.commit()
            task_id = session.scalar(select(FollowupTask.id).where(FollowupTask.sample_case_id == processing_case.id))
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            headers = {"Origin": "http://127.0.0.1:8765"}
            filtered = client.get("/followups?stage=arrival&status=needs_review&language=en&curr_status=40")
            self.assertIn("visible_creator", filtered.text)
            self.assertNotIn("hidden_creator", filtered.text)
            detail = client.get(f"/followups/{task_id}")
            self.assertIn("This preview is safely longer", detail.text)
            self.assertIn("set-type", detail.text)
            self.assertIn("set-lang", detail.text)
            self.assertNotIn("app_secret", detail.text)
            self.assertNotIn("message-send", detail.text)
            self.assertNotIn(">发送</button>", detail.text)
            type_response = client.post(f"/api/followups/{task_id}/set-type", data={"creator_type": "video"}, headers=headers, follow_redirects=False)
            language_response = client.post(f"/api/followups/{task_id}/set-lang", data={"lang": "es"}, headers=headers, follow_redirects=False)
            skip_response = client.post(f"/api/followups/{task_id}/skip", data={}, headers=headers, follow_redirects=False)
            self.assertEqual((type_response.status_code, language_response.status_code, skip_response.status_code), (303, 303, 303))
        with self.session_factory() as session:
            sample_case = session.get(SampleCase, processing_case.id)
            task = session.get(FollowupTask, task_id)
            self.assertEqual(sample_case.creator_type, "video")
            self.assertEqual(sample_case.language, "es")
            self.assertEqual(task.status, "skipped")
