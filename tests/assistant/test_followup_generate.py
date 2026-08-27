from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

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
            platform_status = "processing" if curr_status == 40 else "shipped" if curr_status == 30 else ""
            case = SampleCase(
                store_id=store.id,
                creator_id="creator",
                creator_name="creator",
                apply_id=str(curr_status),
                product_id=product_id,
                curr_status=curr_status,
                platform_status=platform_status,
                platform_status_label="处理中" if curr_status == 40 else "已发货",
                **values,
            )
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
            self.assertIsNone(tasks[0].scheduled_for)
            self.assertEqual(tasks[0].action_kind, "confirm_delivery_date")
            self.assertEqual(tasks[0].status, "needs_review")

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
        self.assertEqual(first_result, {"created": 1, "refreshed": 0})
        self.assertEqual(second_result, {"created": 0, "refreshed": 0})
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
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertEqual([task.stage for task in tasks], ["day_10_list"])
            self.assertEqual(tasks[0].action_kind, "list_only")
            self.assertEqual(tasks[0].status, "pending")
            self.assertFalse(tasks[0].requires_manual_confirmation)

    def test_non_hero_product_generates_task_without_image(self) -> None:
        # 跟进不分商品：非 B005 也生成任务；刚到货不配图，走 arrival_other 话术。
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            product_id="1732060411527205730",
            is_video_creator="是",
            language="en",
        )
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertIsNotNone(task)
            self.assertEqual(task.stage, "arrival")
            self.assertEqual(task.attachment_key, "")
            self.assertIn("arrival_other", task.template_key)
            self.assertIn("excited to see your content", task.message_preview)

    def test_dual_marked_creator_uses_video_followup_template(self) -> None:
        self.add_case(curr_status=40, delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc), is_video_creator="是", is_live_creator="是", language="en")
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertFalse(task.requires_manual_confirmation)
            self.assertEqual(task.status, "pending")
            self.assertEqual(task.creator_type, "both")
            self.assertEqual(task.review_reason, "")
            self.assertIn("arrival_hero_video", task.template_key)
            self.assertIn("#Hicloth", task.message_preview)

    def test_generate_refreshes_existing_dual_marked_review_task(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            is_live_creator="是",
            language="en",
        )
        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            session.add(
                FollowupTask(
                    sample_case_id=sample_case.id,
                    stage="arrival",
                    scheduled_for=date(2026, 8, 12),
                    status="needs_review",
                    action_kind="send_message",
                    creator_type="both",
                    review_reason="ambiguous_creator_type",
                    requires_manual_confirmation=True,
                    language="en",
                    template_key="",
                )
            )
            session.commit()
        result = FollowupService(self.session_factory).generate(
            datetime(2026, 8, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["refreshed"], 1)
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.status, "pending")
            self.assertFalse(task.requires_manual_confirmation)
            self.assertEqual(task.review_reason, "")
            self.assertEqual(task.creator_type, "both")
            self.assertIn("arrival_hero_video", task.template_key)

    def test_stale_processing_case_does_not_enter_calendar(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
            platform_status_stale=True,
        )
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            self.assertEqual(session.scalars(select(FollowupTask)).all(), [])

    def test_stale_processing_case_still_refreshes_existing_tasks(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            is_live_creator="是",
            bio="Mujer Guerrero siempre saliendo adelante",
            platform_status_stale=True,
        )
        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            session.add(
                FollowupTask(
                    sample_case_id=sample_case.id,
                    stage="day_7",
                    scheduled_for=date(2026, 8, 19),
                    status="needs_review",
                    action_kind="send_message",
                    creator_type="both",
                    review_reason="",
                    requires_manual_confirmation=True,
                    language="en",
                    template_key="",
                )
            )
            session.commit()
        with patch(
            "assistant.domain.policies.detect_creator_lang",
            return_value={
                "lang": "es",
                "confidence": "low",
                "reason": "spanish-bio",
            },
        ):
            result = FollowupService(self.session_factory).generate(
                datetime(2026, 8, 26, tzinfo=timezone.utc)
            )
        self.assertEqual(result["created"], 0)
        self.assertEqual(result["refreshed"], 1)
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            self.assertEqual(len(tasks), 1)
            task = tasks[0]
            self.assertEqual(task.stage, "day_7")
            self.assertEqual(task.status, "pending")
            self.assertFalse(task.requires_manual_confirmation)
            self.assertEqual(task.language, "es")
            self.assertEqual(task.creator_type, "both")
            self.assertIn("unpublished_7_video_es", task.template_key)

    def test_confirm_content_creates_thanks_preview_without_writing_feishu(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
            feishu_record_id="rec-test",
        )
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task_id = session.scalar(select(FollowupTask.id))
        with patch(
            "lib.feishu_bitable.update_record_cooperation_status"
        ) as write_status:
            result = FollowupService(self.session_factory).confirm_content(
                task_id,
                content_type="video",
                content_url="https://example.com/video",
                write_feishu=False,
            )
        write_status.assert_not_called()
        self.assertEqual(result["feishu"]["status"], "not-requested")
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            thanks = [task for task in tasks if task.stage == "content_found"]
            suppressed = [task for task in tasks if task.status == "suppressed"]
            self.assertEqual(len(thanks), 1)
            self.assertEqual(thanks[0].action_kind, "acknowledge_content")
            self.assertIn("video_found", thanks[0].template_key)
            self.assertTrue(suppressed)
            sample_case = session.scalar(select(SampleCase))
            self.assertEqual(sample_case.platform_status, "completed")

    def test_confirm_content_writes_feishu_only_when_switch_is_on(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
            feishu_record_id="rec-test",
        )
        FollowupService(self.session_factory).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task_id = session.scalar(select(FollowupTask.id))
        with (
            patch("lib.app_config.load_bitable_settings", return_value={"app_id": "app", "app_secret": "secret"}),
            patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"),
            patch(
                "lib.feishu_bitable.update_record_cooperation_status",
                return_value={"status": "written", "target_status": "已完成"},
            ) as write_status,
        ):
            result = FollowupService(self.session_factory).confirm_content(
                task_id,
                content_type="live",
                write_feishu=True,
            )
        write_status.assert_called_once()
        self.assertEqual(result["feishu"]["status"], "written")
        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            self.assertEqual(sample_case.feishu_cooperation_status, "已完成")

    def test_mark_unfulfilled_does_not_write_feishu_by_default(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
            feishu_record_id="rec-test",
        )
        FollowupService(self.session_factory).generate(datetime(2026, 8, 16, tzinfo=timezone.utc))
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.stage, "unfulfilled")
            self.assertEqual(task.action_kind, "mark_unfulfilled")
            self.assertEqual(task.status, "pending")
            task_id = task.id
        with patch(
            "lib.feishu_bitable.update_record_cooperation_status"
        ) as write_status:
            result = FollowupService(self.session_factory).mark_unfulfilled(task_id)
        write_status.assert_not_called()
        self.assertEqual(result["feishu"]["status"], "not-requested")

    def test_readonly_feishu_spanish_language_requires_matched_option(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            sample_product_option="B005（商品）",
        )
        with (
            patch("lib.app_config.load_bitable_settings", return_value={"app_id": "app", "app_secret": "secret"}),
            patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"),
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
            self.assertEqual(task.language, "en")
            self.assertFalse(task.requires_manual_confirmation)
        self.assertTrue(any("飞书语言只读查询失败" in message for message in warnings))

    def test_bio_language_detection_used_when_feishu_missing(self) -> None:
        # 飞书无值 + 有 running 店铺 → 拉详情简介 → 用 LLM lang，不看置信度。
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
        )
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch("lib.sample_navigation.navigate_to_url") as navigate,
            patch(
                "lib.creator_detail.extract_creator_detail",
                return_value={
                    "bio": (
                        "Hola a todos, soy una creadora de contenido de Mexico. "
                        "Me encanta la moda y quiero compartir mis videos con "
                        "ustedes. Gracias por su apoyo, muy pronto habra mas."
                    )
                },
            ) as extract_detail,
            patch(
                "assistant.domain.policies.detect_creator_lang",
                return_value={
                    "lang": "es",
                    "confidence": "high",
                    "reason": "spanish-greeting-and-collab",
                },
            ),
        ):
            FollowupService(
                self.session_factory,
                store_id="store-test",
            ).generate(datetime(2026, 8, 12, tzinfo=timezone.utc))

        self.assertEqual(navigate.call_count, 2)
        extract_detail.assert_called_once()
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.language, "es")
            self.assertFalse(task.requires_manual_confirmation)
            self.assertEqual(task.status, "pending")

    def test_no_store_skips_bio_detection_and_keeps_review(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
        )
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            FollowupService(self.session_factory).generate(
                datetime(2026, 8, 12, tzinfo=timezone.utc)
            )

        navigate.assert_not_called()
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.language, "en")
            self.assertFalse(task.requires_manual_confirmation)

    def test_followup_filters_and_local_form_controls_refresh_detail(self) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="ui-store", store_name="UI")
            session.add(store); session.flush()
            processing_case = SampleCase(store_id=store.id, creator_id="ui-40", creator_name="visible_creator", apply_id="ui-40", product_id="1732414717062320994", curr_status=40, platform_status="processing", language="en")
            shipped_case = SampleCase(store_id=store.id, creator_id="ui-30", creator_name="hidden_creator", apply_id="ui-30", product_id="1732414717062320994", curr_status=30, platform_status="shipped")
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
            self.assertIn("按视频达人跟进", detail.text)
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

    def test_default_followup_list_hides_superseded_and_shows_action_progress(self) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="list-store", store_name="List")
            session.add(store)
            session.flush()
            current_case = SampleCase(
                store_id=store.id,
                creator_id="current",
                creator_name="current_creator",
                apply_id="current",
                product_id="1732414717062320994",
                curr_status=40,
                platform_status="processing",
            )
            superseded_case = SampleCase(
                store_id=store.id,
                creator_id="old",
                creator_name="superseded_creator",
                apply_id="old",
                product_id="1732414717062320994",
                curr_status=40,
                platform_status="processing",
            )
            session.add_all([current_case, superseded_case])
            session.flush()
            session.add(
                FollowupTask(
                    sample_case_id=current_case.id,
                    stage="day_3",
                    scheduled_for=date(2026, 8, 15),
                    status="pending",
                    action_kind="send_message",
                    language="en",
                )
            )
            session.add(
                FollowupTask(
                    sample_case_id=superseded_case.id,
                    stage="arrival",
                    scheduled_for=date(2026, 8, 12),
                    status="suppressed",
                    suppressed_reason="superseded_by_later_stage",
                    action_kind="send_message",
                    language="en",
                )
            )
            session.commit()
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            listing = client.get("/followups")
            table_body = listing.text.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
            self.assertIn("current_creator", listing.text)
            self.assertNotIn("superseded_creator", listing.text)
            self.assertIn("待发跟进私信", listing.text)
            self.assertNotIn("已由新阶段取代", listing.text)
            self.assertNotIn("待处理", table_body)
            self.assertIn('option value="pending"', listing.text)

    def test_followup_list_orders_by_stage_then_creator_id_and_uses_select_filters(self) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="sort-store", store_name="Sort")
            session.add(store)
            session.flush()
            rows = (
                ("zeta", "arrival", "pending"),
                ("mike", "day_10_list", "pending"),
                ("beta", "unfulfilled", "pending"),
                ("Alpha", "unfulfilled", "pending"),
                ("alpha-day3", "day_3", "pending"),
            )
            for creator_id, stage, status in rows:
                sample_case = SampleCase(
                    store_id=store.id,
                    creator_id=creator_id,
                    creator_name=creator_id,
                    apply_id=creator_id,
                    product_id="1732414717062320994",
                    curr_status=40,
                    platform_status="processing",
                    platform_status_label="处理中",
                    language="en",
                )
                session.add(sample_case)
                session.flush()
                session.add(
                    FollowupTask(
                        sample_case_id=sample_case.id,
                        stage=stage,
                        scheduled_for=date(2026, 8, 12),
                        status=status,
                        action_kind="send_message",
                        language="en",
                    )
                )
            session.commit()
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            listing = client.get("/followups")
            names = [
                "Alpha",
                "beta",
                "mike",
                "alpha-day3",
                "zeta",
            ]
            positions = [listing.text.index(name) for name in names]
            self.assertEqual(positions, sorted(positions))
            self.assertIn(">序号<", listing.text)
            self.assertIn('class="table-index">1<', listing.text)
            self.assertIn('class="table-index">5<', listing.text)
            self.assertIn("<select name=\"stage\">", listing.text)
            self.assertIn("<select name=\"status\">", listing.text)
            self.assertIn("<select name=\"language\">", listing.text)
            self.assertIn("<select name=\"platform_status\">", listing.text)
            self.assertIn("到货后第 15 天未履约", listing.text)
            self.assertIn("需人工确认", listing.text)
            self.assertIn("英语", listing.text)
            self.assertIn("处理中", listing.text)
            filtered = client.get("/followups?platform_status=processing&language=en")
            self.assertIn("Alpha", filtered.text)
            self.assertIn('option value="en" selected', filtered.text)

    def test_mark_sent_is_local_only(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
        )
        FollowupService(self.session_factory).generate(
            datetime(2026, 8, 12, tzinfo=timezone.utc)
        )
        with self.session_factory() as session:
            task = session.scalar(select(FollowupTask))
            self.assertEqual(task.action_kind, "send_message")
            task_id = task.id
        with (
            patch("lib.feishu_bitable.update_record_cooperation_status") as write_status,
            patch("lib.im_api.send_message_via_sdk") as send_message,
        ):
            result = FollowupService(self.session_factory).mark_message_sent(task_id)
        write_status.assert_not_called()
        send_message.assert_not_called()
        self.assertEqual(result["send_result"], "marked-sent")
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            self.assertEqual(task.send_result, "marked-sent")
            self.assertIsNotNone(task.sent_at)
            self.assertEqual(task.status, "pending")
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            listing = client.get("/followups")
            self.assertIn("已发跟进私信", listing.text)
            detail = client.get(f"/followups/{task_id}")
            self.assertIn("已在本地标记为「已发跟进私信」", detail.text)
            self.assertNotIn(">发送</button>", detail.text)

    def test_mark_listed_is_local_only(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
        )
        FollowupService(self.session_factory).generate(
            datetime(2026, 8, 12, tzinfo=timezone.utc)
        )
        with self.session_factory() as session:
            list_task = session.scalar(select(FollowupTask))
            self.assertEqual(list_task.stage, "day_10_list")
            list_task_id = list_task.id
        with (
            patch("lib.feishu_bitable.update_record_cooperation_status") as write_status,
            patch("lib.im_api.send_message_via_sdk") as send_message,
        ):
            listed = FollowupService(self.session_factory).mark_list_handed_over(
                list_task_id
            )
        write_status.assert_not_called()
        send_message.assert_not_called()
        self.assertEqual(listed["send_result"], "listed")
        with self.session_factory() as session:
            list_task = session.get(FollowupTask, list_task_id)
            self.assertEqual(list_task.send_result, "listed")
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            listing = client.get("/followups")
            self.assertIn("已出名单给业务", listing.text)
            detail = client.get(f"/followups/{list_task_id}")
            self.assertIn("已在本地标记为「已出名单给业务」", detail.text)
            self.assertNotIn(">发送</button>", detail.text)

    def test_generate_keeps_locally_sent_arrival_when_day_3_is_due(self) -> None:
        self.add_case(
            curr_status=40,
            delivered_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
            is_video_creator="是",
            language="en",
        )
        FollowupService(self.session_factory).generate(
            datetime(2026, 8, 12, tzinfo=timezone.utc)
        )
        with self.session_factory() as session:
            arrival = session.scalar(select(FollowupTask))
            FollowupService(self.session_factory).mark_message_sent(arrival.id)
        FollowupService(self.session_factory).generate(
            datetime(2026, 8, 15, tzinfo=timezone.utc)
        )
        with self.session_factory() as session:
            tasks = session.scalars(select(FollowupTask)).all()
            by_stage = {task.stage: task for task in tasks}
            self.assertEqual(by_stage["arrival"].send_result, "marked-sent")
            self.assertNotEqual(by_stage["arrival"].status, "suppressed")
            self.assertEqual(by_stage["day_3"].action_kind, "send_message")
            self.assertEqual(by_stage["day_3"].status, "pending")
