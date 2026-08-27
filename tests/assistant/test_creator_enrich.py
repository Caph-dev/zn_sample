from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, SampleCase, Store
from assistant.services.creator_enrich_service import (
    CreatorEnrichService,
    is_missing_creator_type,
    is_missing_language,
)


class CreatorEnrichTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temporary_directory.name) / "test.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.session_factory() as session:
            store = Store(
                ziniao_store_id="store",
                store_name="store",
                shop_id="shop-test",
            )
            session.add(store)
            session.commit()

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def add_case(self, **values) -> SampleCase:
        with self.session_factory() as session:
            store_id = session.scalar(select(Store.id))
            values.setdefault("apply_id", f"apply-{len(session.scalars(select(SampleCase)).all())}")
            values.setdefault("creator_id", "creator-one")
            values.setdefault("creator_name", "Creator One")
            values.setdefault("product_id", "1732414717062320994")
            values.setdefault("curr_status", 40)
            values.setdefault("platform_status", "processing")
            values.setdefault("sample_product_option", "B005（商品）")
            case = SampleCase(store_id=store_id, **values)
            session.add(case)
            session.commit()
            session.expunge(case)
            return case

    def test_missing_checks(self) -> None:
        empty = self.add_case()
        self.assertTrue(is_missing_creator_type(empty))
        self.assertTrue(is_missing_language(empty))

        typed = self.add_case(
            apply_id="typed",
            creator_id="creator-typed",
            is_video_creator="是",
            language="en",
        )
        self.assertFalse(is_missing_creator_type(typed))
        self.assertFalse(is_missing_language(typed))

        bio_only = self.add_case(
            apply_id="bio",
            creator_id="creator-bio",
            bio="Hola, soy creadora.",
        )
        self.assertFalse(is_missing_language(bio_only))

    def test_enrich_fills_type_batch_then_skips_language_for_typed(self) -> None:
        # 样本类型缺失但语言已手动指定：先导航回样品申请页，再走类型 API，不跳详情页。
        case = self.add_case(
            apply_id="fill-type",
            creator_id="creator-fill-type",
            language="en",
        )
        with (
            patch(
                "lib.creator_api.fetch_creator_detail_api",
                return_value={
                    "ok": True,
                    "detail": {"video_gpm_n": 15.0, "live_gpm_n": None},
                },
            ) as fetch_api,
            patch(
                "lib.sample_navigation.ensure_sample_request_context",
                return_value={"ok": True, "already": True},
            ) as ensure_context,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            result = CreatorEnrichService(
                self.session_factory,
                store_id="store-test",
            ).enrich()

        ensure_context.assert_called_once()
        fetch_api.assert_called_once()
        navigate.assert_not_called()
        self.assertEqual(result["type_filled"], 1)
        with self.session_factory() as session:
            updated = session.get(SampleCase, case.id)
            self.assertEqual(updated.is_video_creator, "是")
            self.assertEqual(updated.is_live_creator, "")

    def test_enrich_fills_language_from_feishu_without_detail_navigation(self) -> None:
        case = self.add_case(
            apply_id="fill-lang",
            creator_id="creator-fill-lang",
            is_video_creator="是",
        )
        with (
            patch(
                "lib.app_config.load_bitable_settings",
                return_value={"app_id": "app", "app_secret": "secret"},
            ),
            patch("lib.feishu_bitable.get_bitable_access_token", return_value="token"),
            patch(
                "lib.feishu_bitable.find_duplicate_record",
                return_value={"fields": {"使用语言": "西班牙语"}},
            ) as find_record,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            result = CreatorEnrichService(
                self.session_factory,
                store_id="store-test",
            ).enrich()

        find_record.assert_called_once()
        navigate.assert_not_called()
        self.assertEqual(result["language_filled"], 1)
        with self.session_factory() as session:
            updated = session.get(SampleCase, case.id)
            self.assertEqual(updated.feishu_lang, "西班牙语")

    def test_enrich_falls_back_to_detail_page(self) -> None:
        case = self.add_case(
            apply_id="fill-bio",
            creator_id="creator-fill-bio",
            is_video_creator="是",
        )
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch(
                "lib.creator_detail.extract_creator_detail",
                return_value={"bio": "Hola, soy una creadora de Mexico."},
            ) as extract_detail,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            result = CreatorEnrichService(
                self.session_factory,
                store_id="store-test",
            ).enrich()

        extract_detail.assert_called_once()
        self.assertEqual(navigate.call_count, 2)
        self.assertEqual(result["language_filled"], 1)
        with self.session_factory() as session:
            updated = session.get(SampleCase, case.id)
            self.assertIn("Hola", updated.bio)

    def test_detail_page_also_fills_missing_type(self) -> None:
        case = self.add_case(apply_id="fill-both", creator_id="creator-fill-both")
        with (
            patch(
                "lib.creator_api.fetch_creator_detail_api",
                return_value={"ok": False, "error": "api-unavailable"},
            ),
            patch(
                "lib.sample_navigation.ensure_sample_request_context",
                return_value={"ok": True, "already": True},
            ),
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch(
                "lib.creator_detail.extract_creator_detail",
                return_value={
                    "bio": "Hola a todos, me encanta la moda.",
                    "video_gpm_n": 20.0,
                    "live_gpm_n": None,
                },
            ),
            patch("lib.sample_navigation.navigate_to_url"),
        ):
            result = CreatorEnrichService(
                self.session_factory,
                store_id="store-test",
            ).enrich()

        with self.session_factory() as session:
            updated = session.get(SampleCase, case.id)
            self.assertEqual(updated.is_video_creator, "是")
            self.assertIn("Hola", updated.bio)

    def test_no_store_skips_both_fetches(self) -> None:
        self.add_case(apply_id="no-store", creator_id="creator-no-store")
        warnings = []
        with (
            patch("lib.app_config.load_bitable_settings", return_value={}),
            patch("lib.creator_api.fetch_creator_detail_api") as fetch_api,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            result = CreatorEnrichService(
                self.session_factory,
                warning=warnings.append,
            ).enrich()

        fetch_api.assert_not_called()
        navigate.assert_not_called()
        self.assertEqual(result["missing"], 1)
        self.assertEqual(result["failed"], 2)
        self.assertTrue(any("没有唯一运行中的店铺" in message for message in warnings))
        self.assertTrue(any("达人类型补齐失败" in message for message in warnings))

    def test_complete_case_is_skipped(self) -> None:
        self.add_case(
            apply_id="complete",
            creator_id="creator-complete",
            is_video_creator="是",
            language="en",
        )
        with (
            patch("lib.creator_api.fetch_creator_detail_api") as fetch_api,
            patch("lib.sample_navigation.navigate_to_url") as navigate,
        ):
            result = CreatorEnrichService(
                self.session_factory,
                store_id="store-test",
            ).enrich()

        fetch_api.assert_not_called()
        navigate.assert_not_called()
        self.assertEqual(result["missing"], 0)

    def test_handler_registered(self) -> None:
        from assistant.jobs.registry import REGISTERED_JOB_TYPES, get_handler

        self.assertIn("creator_enrich", REGISTERED_JOB_TYPES)
        self.assertIsNotNone(get_handler("creator_enrich"))

    def test_enrich_api_endpoint(self) -> None:
        from fastapi.testclient import TestClient

        from assistant.app import create_app

        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            response = client.post(
                "/api/jobs/creator-enrich",
                headers={"Origin": "http://127.0.0.1:8765"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["job_id"])


if __name__ == "__main__":
    unittest.main()
