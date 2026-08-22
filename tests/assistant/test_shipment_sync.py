from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
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

    @staticmethod
    def build_sample_row(
        *, apply_id: str, creator_id: str, order_id: str
    ) -> dict[str, object]:
        return {
            "apply_id": apply_id,
            "creator_id": creator_id,
            "creator_name": f"name-{creator_id}",
            "product_id": "1732414717062320994",
            "main_order_id": order_id,
            "fulfill_unit_ids": [],
        }

    @staticmethod
    def build_logistics_details(tracking_number: str) -> dict[str, object]:
        return {
            "tracking_no": tracking_number,
            "tracking_raw": tracking_number,
            "carrier": "carrier",
            "status_label": "In transit",
            "status_category": "in_transit",
            "estimated_delivery_at": None,
            "delivered_at": None,
            "last_event_at": None,
            "last_event_text": "In transit",
            "package_count": 1,
            "needs_delivery_time_confirmation": False,
            "raw_payload_hash": f"hash-{tracking_number}",
            "via": "api",
        }

    def test_processing_overrides_shipped_and_persists_snapshot(self) -> None:
        row = {"apply_id": "apply", "creator_id": "creator", "creator_name": "name", "product_id": "1732414717062320994", "main_order_id": "123456789012345", "fulfill_unit_ids": []}
        details = {"tracking_no": "track", "tracking_raw": "track", "carrier": "carrier", "status_label": "Delivered", "status_category": "delivered", "estimated_delivery_at": None, "delivered_at": None, "last_event_at": None, "last_event_text": "Delivered", "package_count": 1, "needs_delivery_time_confirmation": True, "raw_payload_hash": "hash", "via": "api"}
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[row]),
            patch("lib.order_api.fetch_tiktok_logistics_details_api", return_value=details),
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
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

    def test_detail_failure_does_not_block_later_rows(self) -> None:
        first_row = self.build_sample_row(
            apply_id="first-apply",
            creator_id="first-creator",
            order_id="123456789012345",
        )
        second_row = self.build_sample_row(
            apply_id="second-apply",
            creator_id="second-creator",
            order_id="223456789012345",
        )
        warnings = []
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.fetch_tiktok_logistics_details_api",
                side_effect=[
                    PageApiSchemaError("synthetic detail failure"),
                    self.build_logistics_details("second-tracking"),
                ],
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            result = ShipmentService(
                self.session_factory,
                warning=warnings.append,
            ).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            shipments = session.scalars(select(Shipment)).all()
            self.assertEqual(len(shipments), 1)
            self.assertEqual(shipments[0].tracking_number, "second-tracking")
        self.assertEqual(result["synchronized"], 1)
        self.assertEqual(fetch_details.call_count, 2)
        self.assertEqual(len(warnings), 1)
        self.assertIn("123456789012345", warnings[0])
        self.assertNotIn("synthetic detail failure", warnings[0])

    def test_detail_failure_preserves_existing_shipment_facts(self) -> None:
        existing_delivered_at = datetime(2026, 8, 21, 12, 30, tzinfo=timezone.utc)
        row = self.build_sample_row(
            apply_id="existing-apply",
            creator_id="existing-creator",
            order_id="323456789012345",
        )
        with self.session_factory() as session:
            store = Store(ziniao_store_id="ziniao", store_name="store")
            session.add(store)
            session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id="existing-creator",
                creator_name="existing-name",
                apply_id="existing-apply",
                product_id="1732414717062320994",
                curr_status=30,
                main_order_id="323456789012345",
            )
            session.add(sample_case)
            session.flush()
            shipment = Shipment(
                sample_case_id=sample_case.id,
                tracking_number="known-tracking",
                tracking_display="Known Carrier, known-tracking",
                carrier="Known Carrier",
                status_label="Delivered",
                status_category="delivered",
                delivered_at=existing_delivered_at,
                last_event_text="Known delivery event",
                source="api",
            )
            session.add(shipment)
            session.flush()
            session.add(
                ShipmentSnapshot(
                    shipment_id=shipment.id,
                    status_label="Delivered",
                    status_category="delivered",
                    delivered_at=existing_delivered_at,
                    last_event_text="Known delivery event",
                    raw_payload_hash="known-hash",
                    source="api",
                )
            )
            session.commit()

        warnings = []
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.fetch_tiktok_logistics_details_api",
                side_effect=PageApiSchemaError("temporary schema failure"),
            ),
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            ShipmentService(
                self.session_factory,
                warning=warnings.append,
            ).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            snapshots = session.scalars(select(ShipmentSnapshot)).all()
            self.assertEqual(shipment.tracking_number, "known-tracking")
            self.assertEqual(shipment.status_category, "delivered")
            self.assertEqual(shipment.delivered_at, existing_delivered_at)
            self.assertEqual(len(snapshots), 1)
        self.assertEqual(len(warnings), 1)

    def test_invalid_order_id_still_persists_unknown_details(self) -> None:
        row = self.build_sample_row(
            apply_id="invalid-apply",
            creator_id="invalid-creator",
            order_id="not-an-order-id",
        )
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch("lib.order_api.fetch_tiktok_logistics_details_api") as fetch_details,
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            snapshot = session.scalar(select(ShipmentSnapshot))
            self.assertEqual(shipment.status_category, "unknown")
            self.assertEqual(shipment.source, "invalid-order-id")
            self.assertEqual(snapshot.status_category, "unknown")
        self.assertEqual(result["synchronized"], 1)
        fetch_details.assert_not_called()

    def test_navigation_failure_stops_before_next_detail_request(self) -> None:
        first_row = self.build_sample_row(
            apply_id="navigation-first-apply",
            creator_id="navigation-first-creator",
            order_id="423456789012345",
        )
        second_row = self.build_sample_row(
            apply_id="navigation-second-apply",
            creator_id="navigation-second-creator",
            order_id="523456789012345",
        )
        warnings = []
        with (
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.fetch_tiktok_logistics_details_api",
                return_value=self.build_logistics_details("first-tracking"),
            ) as fetch_details,
            patch(
                "lib.sample_navigation.navigate_to_sample_request",
                return_value={"ok": False},
            ) as navigate_to_sample_request,
        ):
            with self.assertRaises(HandlerFailure) as failure:
                ShipmentService(
                    self.session_factory,
                    warning=warnings.append,
                ).synchronize_shipments(
                    {"storeId": "ziniao", "storeName": "store"},
                    job_progress=lambda *args: None,
                )

        self.assertEqual(failure.exception.error_code, "sample-navigation-failed")
        self.assertEqual(fetch_details.call_count, 1)
        self.assertEqual(navigate_to_sample_request.call_count, 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("423456789012345", warnings[0])
        self.assertNotIn("False", warnings[0])

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
