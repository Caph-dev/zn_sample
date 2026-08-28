from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base
from assistant.domain.content_thanks import (
    classify_completed_content,
    decide_content_thanks_action,
    match_completed_rows,
    parse_im_thread_dates,
    recent_thanks_already_sent,
    render_content_thanks_preview,
)
from assistant.domain.message_templates import (
    looks_like_content_thanks,
    looks_like_uncertain_thanks,
)
from assistant.jobs.registry import REGISTERED_JOB_TYPES, get_handler
from assistant.jobs.registry import HandlerFailure
from assistant.services.content_thanks_service import ContentThanksService


class ContentThanksDecisionTests(unittest.TestCase):
    def test_both_video_and_live_use_video_copy(self) -> None:
        classified = classify_completed_content(
            panel_text="内容详情\n视频\n2\n直播\n1\n在TikTok查看视频\n在TikTok查看直播",
        )
        self.assertEqual(classified["content_type"], "video")
        self.assertEqual(classified["reason"], "both-prefer-video")
        self.assertEqual(classified["video_count"], 2)
        self.assertEqual(classified["live_count"], 1)

    def test_live_only_panel_uses_live_copy(self) -> None:
        classified = classify_completed_content(panel_text="内容详情\n视频\n0\n直播\n1\n在TikTok查看直播")
        self.assertEqual(classified["content_type"], "live")
        self.assertEqual(classified["reason"], "live-only")

    def test_tab_labels_without_counts_are_not_content(self) -> None:
        classified = classify_completed_content(panel_text="免费样品 已完成 视频 直播")
        self.assertEqual(classified["status"], "hold")
        self.assertEqual(classified["content_type"], "")

    def test_measured_jaxxelyn_drawer_is_video_only(self) -> None:
        classified = classify_completed_content(
            panel_text=(
                "内容详情\n视频\n3\n直播\n0\nHicloth 7 Pack\n"
                "发布时间：2026/8/27\n在TikTok查看视频"
            )
        )
        self.assertEqual(classified["content_type"], "video")
        self.assertEqual(classified["reason"], "video-only")
        self.assertEqual(classified["video_count"], 3)
        self.assertEqual(classified["live_count"], 0)

    def test_empty_panel_is_held(self) -> None:
        classified = classify_completed_content(panel_text="暂无数据")
        self.assertEqual(classified["status"], "hold")
        self.assertEqual(classified["content_type"], "")

    def test_ambiguous_completed_rows_are_held(self) -> None:
        matched = match_completed_rows(
            [
                {"creator_name": "alice", "creator_id": "1"},
                {"creator_name": "alice", "creator_id": "2"},
            ],
            creator_handle="alice",
        )
        self.assertEqual(matched["status"], "ambiguous")

    def test_sop_thanks_fingerprint_is_already_sent(self) -> None:
        self.assertTrue(
            looks_like_content_thanks(
                "Hi alice! We just saw the video you created for us and really appreciate"
            )
        )
        self.assertFalse(
            looks_like_uncertain_thanks(
                "Hi alice! We just saw the video you created for us and really appreciate"
            )
        )

    def test_colleague_thanks_without_sop_copy_is_uncertain(self) -> None:
        self.assertTrue(looks_like_uncertain_thanks("Thank you so much for the video!"))
        self.assertFalse(looks_like_content_thanks("Thank you so much for the video!"))

    def test_preview_decision_never_sends_or_writes(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="video",
            content_reason="video-only",
            thread_text="",
            today=date(2026, 8, 27),
        )
        self.assertEqual(decision["decision"], "preview-send")
        self.assertFalse(decision["send"])
        self.assertFalse(decision["write_feishu"])

    def test_thanks_without_recent_date_still_previews_send(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="video",
            thread_text="gracias por el video",
            today=date(2026, 8, 27),
        )
        self.assertEqual(decision["decision"], "preview-send")
        self.assertFalse(decision["send"])

    def test_recent_thanks_with_date_is_already_sent(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="video",
            thread_text="今天 We just saw the video you created for us",
            today=date(2026, 8, 27),
        )
        self.assertEqual(decision["decision"], "already-sent")
        self.assertEqual(decision["reason"], "already-sent-within-14-days")
        self.assertFalse(decision["send"])

    def test_weekday_label_means_recent_live_thanks(self) -> None:
        # 用户实测：IM 里「星期一」标签 + live 感谢原文 = 近期已发。
        thread_text = (
            "星期一：Hi kesha_the_brand! ❤️\n\n"
            "We noticed your recent live stream and wanted to sincerely thank you "
            "for all the effort and support you've put into promoting our brand. "
            "We truly appreciate it! 🥰"
        )
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="live",
            thread_text=thread_text,
            today=date(2026, 8, 28),
        )
        self.assertEqual(decision["decision"], "already-sent")
        self.assertFalse(decision["send"])

    def test_unreadable_thread_holds_instead_of_preview(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="video",
            thread_text="",
            thread_checked=False,
            today=date(2026, 8, 28),
        )
        self.assertEqual(decision["decision"], "hold")
        self.assertEqual(decision["reason"], "thread-unreadable")
        self.assertFalse(decision["send"])

    def test_old_thanks_still_previews_send(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=3,
            completed_match="unique",
            content_type="video",
            thread_text="2026-07-01 Thank you so much for the video!",
            today=date(2026, 8, 27),
        )
        self.assertEqual(decision["decision"], "preview-send")

    def test_missing_outreach_time_still_previews_when_completed_is_unique(self) -> None:
        decision = decide_content_thanks_action(
            feishu_status="待发布",
            outreach_age_days=None,
            completed_match="unique",
            content_type="video",
            today=date(2026, 8, 27),
        )
        self.assertEqual(decision["decision"], "preview-send")

    def test_parse_relative_and_named_thread_dates(self) -> None:
        today = date(2026, 8, 27)
        self.assertIn(today, parse_im_thread_dates("今天 发送消息", today=today))
        self.assertIn(date(2026, 8, 20), parse_im_thread_dates("7天前", today=today))
        self.assertIn(date(2026, 8, 20), parse_im_thread_dates("Aug 20", today=today))
        self.assertTrue(
            recent_thanks_already_sent(
                thread_text="昨天 Thank you so much for the video!",
                today=today,
            )
        )
        self.assertFalse(
            recent_thanks_already_sent(
                thread_text="Thank you so much for the video!",
                today=today,
            )
        )

    def test_user_requested_published_status_is_not_written(self) -> None:
        preview = render_content_thanks_preview(
            content_type="video",
            lang="en",
            creator_name="alice",
            content_url="https://www.tiktok.com/@alice/video/1",
        )
        self.assertEqual(preview["template_key"], "video_found_en")
        self.assertIn("https://www.tiktok.com/@alice/video/1", preview["message"])


class ContentThanksServiceTests(unittest.TestCase):
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

    def test_constructor_rejects_execute_or_write_feishu(self) -> None:
        with self.assertRaises(HandlerFailure):
            ContentThanksService(self.session_factory, execute=True)
        with self.assertRaises(HandlerFailure):
            ContentThanksService(self.session_factory, write_feishu=True)

    def test_preview_uses_view_content_not_stored_creator_type_and_never_sends(self) -> None:
        sent = []
        feishu_writes = []

        def search_feishu(**kwargs):
            return [
                {
                    "record_id": "rec-1",
                    "fields": {
                        "红人ID": "alice",
                        "寄样产品": "B005",
                        "合作状态": ["待发布"],
                        "使用语言": "英语",
                        "建联时间": int(
                            datetime(2026, 8, 20, tzinfo=timezone.utc).timestamp() * 1000
                        ),
                    },
                }
            ]

        def list_completed(store_id, creator_handle=""):
            self.assertEqual(creator_handle, "alice")
            return [
                {
                    "creator_name": "alice",
                    "creator_id": "c1",
                    "apply_id": "apply-1",
                    "is_live_creator": "是",
                }
            ]

        def fetch_performance(store_id, apply_ids):
            self.assertEqual(apply_ids, ["apply-1"])
            return {"ok": True, "video_count": 1, "live_count": 0}

        def send_message(*args, **kwargs):
            sent.append({"args": args, "kwargs": kwargs})
            self.assertFalse(kwargs.get("execute"))
            return {"ok": True, "status": "dry-run"}

        with patch(
            "assistant.services.store_service.StoreService.resolve_unique_running_store",
            return_value={"ok": True, "store": {"storeId": "store-1", "shopId": "shop-1"}},
        ), patch(
            "assistant.services.content_thanks_service.ContentThanksService._preview_thread",
            return_value={"ok": True, "thread_text": ""},
        ), patch(
            "lib.feishu_bitable.update_record_cooperation_status",
            side_effect=lambda *args, **kwargs: feishu_writes.append(kwargs) or {"status": "written"},
        ):
            result = ContentThanksService(
                self.session_factory,
                search_feishu=search_feishu,
                list_completed=list_completed,
                fetch_performance=fetch_performance,
                send_message=send_message,
                clock=lambda: datetime(2026, 8, 27, tzinfo=timezone.utc),
            ).preview()

        self.assertEqual(result["preview"], 1)
        self.assertFalse(result["execute"])
        self.assertFalse(result["write_feishu"])
        self.assertEqual(result["rows"][0]["content_type"], "video")
        self.assertEqual(result["rows"][0]["template_key"], "video_found_en")
        self.assertEqual(result["rows"][0]["planned_feishu_status"], "已完成")
        self.assertFalse(result["rows"][0]["send"])
        self.assertEqual(len(sent), 1)
        self.assertFalse(sent[0]["kwargs"]["execute"])
        self.assertEqual(feishu_writes, [])

    def test_handler_and_api_are_registered(self) -> None:
        self.assertIn("content_thanks_preview", REGISTERED_JOB_TYPES)
        self.assertIsNotNone(get_handler("content_thanks_preview"))
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            home = client.get("/")
            self.assertIn('action="/api/jobs/content-thanks-preview"', home.text)
            self.assertIn("预演已完成感谢私信", home.text)
            response = client.post(
                "/api/jobs/content-thanks-preview",
                headers={"Origin": "http://127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["job_id"])


if __name__ == "__main__":
    unittest.main()
