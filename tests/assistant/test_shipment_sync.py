from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, SampleCase, ShipmentSnapshot
from assistant.services.shipment_service import ShipmentService
from assistant.jobs.handlers.shipment_sync import run_shipment_sync
from assistant.jobs.registry import HandlerFailure
from lib.page_api import PageApiSchemaError
from assistant.app import create_app
from assistant.database.models import Shipment, Store


class ShipmentSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(Path(self.temporary_directory.name) / "test.sqlite3")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_processing_overrides_shipped_and_persists_snapshot(self) -> None:
        row = {"apply_id": "apply", "creator_id": "creator", "creator_name": "name", "product_id": "1732414717062320994", "main_order_id": "123456789012345", "fulfill_unit_ids": []}
        details = {"tracking_no": "track", "tracking_raw": "track", "carrier": "carrier", "status_label": "Delivered", "status_category": "delivered", "estimated_delivery_at": None, "delivered_at": None, "last_event_at": None, "last_event_text": "Delivered", "package_count": 1, "needs_delivery_time_confirmation": True, "raw_payload_hash": "hash", "via": "api"}
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[row]),
            patch("lib.order_api.fetch_tiktok_logistics_details_api", return_value=details),
            patch("lib.sample_navigation.navigate_to_sample_request"),
            patch("lib.sample_dom.assert_on_pending_list") as pending,
            patch("lib.feishu_bitable.update_record_fields") as update,
            patch("lib.feishu_bitable.create_creator_relation_record") as create,
            patch("lib.im_api.send_message_via_sdk") as send,
        ):
            ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )
        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            self.assertEqual(sample_case.curr_status, 40)
            self.assertEqual(sample_case.store_id, 1)
            self.assertGreaterEqual(len(session.scalars(select(ShipmentSnapshot)).all()), 1)
        pending.assert_not_called()
        update.assert_not_called()
        create.assert_not_called()
        send.assert_not_called()

    def test_processing_schema_failure_never_uses_shipped_dom_fallback(self) -> None:
        warnings = []
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[]),
            patch("lib.sample_api.scrape_processing_list_api", side_effect=PageApiSchemaError("bad processing")),
            patch("lib.shipped_dom.scrape_shipped_list") as shipped_dom,
        ):
            result = ShipmentService(self.session_factory, warning=warnings.append).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"}, job_progress=lambda *args: None
            )
        self.assertEqual(result["processing"], 0)
        shipped_dom.assert_not_called()
        self.assertEqual(len(warnings), 1)

    def test_two_running_stores_fail_handler_without_opening_store(self) -> None:
        with (
            patch("lib.zclaw.list_running_stores", return_value=[{"storeId": "one"}, {"storeId": "two"}]),
            patch("lib.zclaw.open_store", create=True) as open_store,
        ):
            with self.assertRaises(HandlerFailure):
                run_shipment_sync("unused", self.session_factory)
        open_store.assert_not_called()

    def test_sync_service_has_no_clock_gate(self) -> None:
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"}, job_progress=lambda *args: None
            )
        self.assertEqual(result["synchronized"], 0)

    def test_in_transit_page_does_not_claim_delivery_confirmation(self) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="page-store", store_name="store")
            session.add(store); session.flush()
            sample_case = SampleCase(store_id=store.id, creator_id="page-creator", creator_name="page-creator", apply_id="page", product_id="product", curr_status=30, main_order_id="order-visible")
            session.add(sample_case); session.flush()
            session.add(Shipment(sample_case_id=sample_case.id, tracking_display="tracking-visible", status_category="in_transit", needs_delivery_time_confirmation=False))
            session.commit()
        app = create_app(runtime_directory=Path(self.temporary_directory.name) / "runtime", port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            token = app.state.session_manager.issue_bootstrap_token()
            client.get(f"/bootstrap?token={token}", follow_redirects=False)
            response = client.get("/shipments")
        self.assertIn("未送达", response.text)
        self.assertNotIn("待确认送达日", response.text)
        self.assertIn("order-visible", response.text)
        self.assertIn("tracking-visible", response.text)
