from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, SampleCase, ShipmentSnapshot
from assistant.services.shipment_service import ShipmentService
from assistant.jobs.handlers.shipment_sync import run_shipment_sync
from assistant.jobs.registry import HandlerFailure
from lib.page_api import PageApiSchemaError, SellerPageContext
from assistant.app import create_app
from assistant.database.models import Shipment, Store
from tests.assistant.console_payload import console_data


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
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[row]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=details,
            ),
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
            self.assertEqual(sample_case.platform_status, "processing")
            self.assertFalse(sample_case.platform_status_stale)
            self.assertEqual(sample_case.store_id, 1)
            # 同一申请同时出现在 tab 30/40：本轮只持久化一次，只生成一次 Snapshot。
            self.assertEqual(len(session.scalars(select(ShipmentSnapshot)).all()), 1)
        enter_page.assert_called_once()
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
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                side_effect=[
                    PageApiSchemaError("synthetic detail failure"),
                    self.build_logistics_details("second-tracking"),
                ],
            ) as fetch_details,
            patch(
                "lib.sample_navigation.current_page_href",
                return_value="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            ),
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}) as navigate_back,
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
        enter_page.assert_called_once()
        navigate_back.assert_called_once()
        self.assertEqual(len(warnings), 1)
        # 用户可见信息不再携带完整订单号，改为脱敏后缀 + 阶段 + 耗时。
        self.assertIn("****2345", warnings[0])
        self.assertIn("阶段=", warnings[0])
        self.assertIn("耗时=", warnings[0])
        self.assertNotIn("123456789012345", warnings[0])
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
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                side_effect=PageApiSchemaError("temporary schema failure"),
            ),
            patch(
                "lib.sample_navigation.current_page_href",
                return_value="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
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
        enter_page.assert_called_once()
        self.assertEqual(len(warnings), 1)

    def test_invalid_order_id_still_persists_unknown_details(self) -> None:
        row = self.build_sample_row(
            apply_id="invalid-apply",
            creator_id="invalid-creator",
            order_id="not-an-order-id",
        )
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch("lib.order_api.enter_seller_order_page") as enter_page,
            patch("lib.order_api.fetch_logistics_details_in_seller_context") as fetch_details,
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
        enter_page.assert_not_called()

    def test_return_navigation_failure_stops_batch_with_handler_failure(self) -> None:
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
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
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
        self.assertEqual(fetch_details.call_count, 2)
        self.assertEqual(navigate_to_sample_request.call_count, 1)
        self.assertEqual(len(warnings), 1)
        self.assertNotIn("False", warnings[0])

    def test_batch_enters_order_page_once_for_many_unique_orders(self) -> None:
        rows = [
            self.build_sample_row(
                apply_id=f"apply-{index}",
                creator_id=f"creator-{index}",
                order_id=f"1{index:018d}",
            )
            for index in range(100)
        ]
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=rows),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=self.build_logistics_details("tracking"),
            ) as fetch_details,
            patch(
                "lib.sample_navigation.navigate_to_sample_request",
                return_value={"ok": True},
            ) as navigate_back,
        ):
            ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        enter_page.assert_called_once()
        navigate_back.assert_called_once()
        self.assertEqual(fetch_details.call_count, 100)

    def test_mixed_batch_isolates_failure_and_skips_invalid_order(self) -> None:
        row_a = self.build_sample_row(apply_id="a", creator_id="ca", order_id="123456789012345")
        row_b = self.build_sample_row(apply_id="b", creator_id="cb", order_id="223456789012345")
        row_c = self.build_sample_row(apply_id="c", creator_id="cc", order_id="323456789012345")
        row_d = self.build_sample_row(apply_id="d", creator_id="cd", order_id="0")
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row_a, row_b, row_c, row_d]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                side_effect=[
                    self.build_logistics_details("tracking-a"),
                    PageApiSchemaError("order B timeout"),
                    self.build_logistics_details("tracking-c"),
                ],
            ) as fetch_details,
            patch(
                "lib.sample_navigation.current_page_href",
                return_value="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            ),
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}) as navigate_back,
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            shipments = session.scalars(select(Shipment)).all()
            self.assertEqual(len(shipments), 3)
            by_case = {
                session.get(SampleCase, shipment.sample_case_id).apply_id: shipment.tracking_number
                for shipment in shipments
            }
        self.assertEqual(by_case["a"], "tracking-a")
        self.assertEqual(by_case["c"], "tracking-c")
        self.assertEqual(by_case["d"], "")
        enter_page.assert_called_once()
        navigate_back.assert_called_once()
        self.assertEqual(fetch_details.call_count, 3)
        self.assertEqual(result["synchronized"], 3)

    def test_same_order_across_tabs_queries_once_and_keeps_status_monotonic(self) -> None:
        row = self.build_sample_row(
            apply_id="cross-tab-apply",
            creator_id="cross-tab-creator",
            order_id="123456789012345",
        )
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[row]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=self.build_logistics_details("cross-tab-tracking"),
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            sample_case = session.scalar(select(SampleCase))
            snapshots = session.scalars(select(ShipmentSnapshot)).all()
            self.assertEqual(sample_case.curr_status, 40)
            self.assertEqual(len(snapshots), 1)
        fetch_details.assert_called_once()
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["unique_orders"], 1)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertEqual(result["synchronized"], 1)

    def test_same_order_multiple_cases_query_once_and_persist_each(self) -> None:
        first_row = self.build_sample_row(
            apply_id="multi-apply-one",
            creator_id="multi-creator-one",
            order_id="123456789012345",
        )
        second_row = self.build_sample_row(
            apply_id="multi-apply-two",
            creator_id="multi-creator-two",
            order_id="123456789012345",
        )
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=self.build_logistics_details("multi-tracking"),
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        with self.session_factory() as session:
            self.assertEqual(len(session.scalars(select(Shipment)).all()), 2)
        fetch_details.assert_called_once()
        self.assertEqual(result["unique_orders"], 1)
        self.assertEqual(result["duplicate_rows"], 1)
        self.assertEqual(result["synchronized"], 2)

    def test_overlapping_fulfill_unit_ids_merge_stably(self) -> None:
        first_row = self.build_sample_row(
            apply_id="merge-apply-one",
            creator_id="merge-creator-one",
            order_id="123456789012345",
        )
        first_row["fulfill_unit_ids"] = ["unit-1", "unit-2"]
        second_row = self.build_sample_row(
            apply_id="merge-apply-two",
            creator_id="merge-creator-two",
            order_id="123456789012345",
        )
        second_row["fulfill_unit_ids"] = ["unit-2", " unit-3 ", "", None]
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=self.build_logistics_details("merge-tracking"),
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        fetch_details.assert_called_once()
        self.assertEqual(
            fetch_details.call_args.kwargs["fulfill_unit_ids"],
            ("unit-1", "unit-2", "unit-3"),
        )

    def test_different_orders_are_not_merged(self) -> None:
        first_row = self.build_sample_row(
            apply_id="distinct-apply-one",
            creator_id="distinct-creator-one",
            order_id="123456789012345",
        )
        second_row = self.build_sample_row(
            apply_id="distinct-apply-two",
            creator_id="distinct-creator-two",
            order_id="999999999999999",
        )
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=self.build_logistics_details("distinct-tracking"),
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            result = ShipmentService(self.session_factory).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )

        self.assertEqual(fetch_details.call_count, 2)
        self.assertEqual(result["unique_orders"], 2)
        self.assertEqual(result["duplicate_rows"], 0)

    FIXED_NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

    def _seed_shipment(
        self,
        *,
        apply_id: str,
        creator_id: str,
        order_id: str,
        shipment_kwargs: dict,
    ) -> None:
        with self.session_factory() as session:
            store = Store(ziniao_store_id="ziniao", store_name="store")
            session.add(store)
            session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id=creator_id,
                creator_name=f"name-{creator_id}",
                apply_id=apply_id,
                product_id="1732414717062320994",
                curr_status=30,
                main_order_id=order_id,
            )
            session.add(sample_case)
            session.flush()
            session.add(Shipment(sample_case_id=sample_case.id, **shipment_kwargs))
            session.commit()

    def _run_sync(self, rows, *, fetch_details, clock=None):
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=rows),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ) as enter_page,
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                return_value=fetch_details,
            ) as fetch_details_mock,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            result = ShipmentService(
                self.session_factory,
                clock=clock,
            ).synchronize_shipments(
                {"storeId": "ziniao", "storeName": "store"},
                job_progress=lambda *args: None,
            )
        return result, enter_page, fetch_details_mock

    def test_complete_delivered_shipment_skips_remote_query(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="delivered-apply",
            creator_id="delivered-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "tracking_display": "Known Carrier, known-tracking",
                "carrier": "Known Carrier",
                "status_category": "delivered",
                "status_label": "Delivered",
                "delivered_at": self.FIXED_NOW - timedelta(days=2),
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=1),
                "request_fingerprint": fingerprint,
                "needs_delivery_time_confirmation": False,
            },
        )
        row = self.build_sample_row(
            apply_id="delivered-apply",
            creator_id="delivered-creator",
            order_id=order_id,
        )

        result, enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("other"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_not_called()
        enter_page.assert_not_called()
        self.assertEqual(result["unique_orders"], 1)
        self.assertEqual(result["fetched"], 0)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)

    def test_active_shipment_ttl_expiry_triggers_refresh(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="ttl-apply",
            creator_id="ttl-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "in_transit",
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=7),
                "request_fingerprint": fingerprint,
            },
        )
        row = self.build_sample_row(
            apply_id="ttl-apply",
            creator_id="ttl-creator",
            order_id=order_id,
        )

        result, enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("fresh-tracking"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_called_once()
        enter_page.assert_called_once()
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["skipped"], 0)

    def test_recent_active_shipment_within_ttl_is_skipped(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="recent-apply",
            creator_id="recent-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "in_transit",
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=2),
                "request_fingerprint": fingerprint,
            },
        )
        row = self.build_sample_row(
            apply_id="recent-apply",
            creator_id="recent-creator",
            order_id=order_id,
        )

        result, enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("other"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_not_called()
        enter_page.assert_not_called()
        self.assertEqual(result["fetched"], 0)
        self.assertEqual(result["skipped"], 1)

    def test_fingerprint_change_forces_refresh_even_when_delivered(self) -> None:
        order_id = "123456789012345"
        self._seed_shipment(
            apply_id="fingerprint-apply",
            creator_id="fingerprint-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "delivered",
                "delivered_at": self.FIXED_NOW - timedelta(days=1),
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=1),
                "request_fingerprint": "stale-fingerprint",
                "needs_delivery_time_confirmation": False,
            },
        )
        row = self.build_sample_row(
            apply_id="fingerprint-apply",
            creator_id="fingerprint-creator",
            order_id=order_id,
        )
        row["fulfill_unit_ids"] = ["unit-new"]

        result, enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("fresh-tracking"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_called_once()
        enter_page.assert_called_once()
        self.assertEqual(result["fetched"], 1)

    def test_last_failed_shipment_is_requeried_immediately(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="failed-apply",
            creator_id="failed-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "in_transit",
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=1),
                "request_fingerprint": fingerprint,
                "last_error": "TimeoutExpired: boom",
                "last_error_at": self.FIXED_NOW - timedelta(minutes=5),
            },
        )
        row = self.build_sample_row(
            apply_id="failed-apply",
            creator_id="failed-creator",
            order_id=order_id,
        )

        result, enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("recovered-tracking"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_called_once()
        enter_page.assert_called_once()
        self.assertEqual(result["fetched"], 1)

    def test_delivered_without_time_is_requeried(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="confirm-apply",
            creator_id="confirm-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "delivered",
                "delivered_at": None,
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=1),
                "request_fingerprint": fingerprint,
                "needs_delivery_time_confirmation": True,
            },
        )
        row = self.build_sample_row(
            apply_id="confirm-apply",
            creator_id="confirm-creator",
            order_id=order_id,
        )

        result, _enter_page, fetch_mock = self._run_sync(
            [row],
            fetch_details=self.build_logistics_details("known-tracking"),
            clock=lambda: self.FIXED_NOW,
        )

        fetch_mock.assert_called_once()
        self.assertEqual(result["fetched"], 1)

    def test_unknown_result_does_not_downgrade_confirmed_delivery(self) -> None:
        delivered_at = self.FIXED_NOW - timedelta(days=2)
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="downgrade-apply",
            creator_id="downgrade-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "tracking_display": "Known Carrier, known-tracking",
                "carrier": "Known Carrier",
                "status_category": "delivered",
                "status_label": "Delivered",
                "delivered_at": delivered_at,
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=8),
                "request_fingerprint": fingerprint,
                "needs_delivery_time_confirmation": False,
            },
        )
        row = self.build_sample_row(
            apply_id="downgrade-apply",
            creator_id="downgrade-creator",
            order_id=order_id,
        )
        unknown_details = {
            "ok": True,
            "tracking_no": "",
            "tracking_raw": "",
            "carrier": "",
            "status_label": "",
            "status_category": "unknown",
            "estimated_delivery_at": None,
            "delivered_at": None,
            "last_event_at": None,
            "last_event_text": "",
            "package_count": 0,
            "needs_delivery_time_confirmation": False,
            "raw_payload_hash": "unknown-hash",
            "via": "details-no-tracking",
        }

        self._run_sync(
            [row],
            fetch_details=unknown_details,
            clock=lambda: self.FIXED_NOW,
        )

        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            self.assertEqual(shipment.status_category, "delivered")
            self.assertEqual(shipment.delivered_at, delivered_at)
            self.assertEqual(shipment.tracking_number, "known-tracking")
            self.assertEqual(shipment.carrier, "Known Carrier")

    def test_empty_tracking_result_does_not_clear_existing_tracking(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="keep-apply",
            creator_id="keep-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "in_transit",
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=7),
                "request_fingerprint": fingerprint,
            },
        )
        row = self.build_sample_row(
            apply_id="keep-apply",
            creator_id="keep-creator",
            order_id=order_id,
        )
        empty_tracking_details = self.build_logistics_details("")
        empty_tracking_details["tracking_no"] = ""

        self._run_sync(
            [row],
            fetch_details=empty_tracking_details,
            clock=lambda: self.FIXED_NOW,
        )

        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            self.assertEqual(shipment.tracking_number, "known-tracking")

    def test_unchanged_sync_does_not_add_snapshot(self) -> None:
        order_id = "123456789012345"
        fingerprint = ShipmentService._request_fingerprint(order_id, ())
        self._seed_shipment(
            apply_id="snapshot-apply",
            creator_id="snapshot-creator",
            order_id=order_id,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "tracking_display": "Carrier, known-tracking",
                "carrier": "Carrier",
                "status_category": "in_transit",
                "status_label": "In transit",
                "package_count": 1,
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=7),
                "request_fingerprint": fingerprint,
            },
        )
        row = self.build_sample_row(
            apply_id="snapshot-apply",
            creator_id="snapshot-creator",
            order_id=order_id,
        )
        identical_details = {
            "ok": True,
            "tracking_no": "known-tracking",
            "tracking_raw": "Carrier, known-tracking",
            "carrier": "Carrier",
            "status_label": "In transit",
            "status_category": "in_transit",
            "estimated_delivery_at": None,
            "delivered_at": None,
            "last_event_at": None,
            "last_event_text": "",
            "package_count": 1,
            "needs_delivery_time_confirmation": False,
            "raw_payload_hash": "hash-same",
            "via": "api",
        }
        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            session.add(
                ShipmentSnapshot(
                    shipment_id=shipment.id,
                    status_label="In transit",
                    status_category="in_transit",
                    last_event_text="",
                    source="api",
                )
            )
            session.commit()

        result, _enter, _fetch = self._run_sync(
            [row],
            fetch_details=identical_details,
            clock=lambda: self.FIXED_NOW,
        )

        with self.session_factory() as session:
            snapshots = session.scalars(select(ShipmentSnapshot)).all()
            self.assertEqual(len(snapshots), 1)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(result["synchronized_cases"], 1)

    def test_result_reports_incremental_metrics(self) -> None:
        delivered_order = "111456789012345"
        delivered_fingerprint = ShipmentService._request_fingerprint(delivered_order, ())
        self._seed_shipment(
            apply_id="metric-delivered",
            creator_id="metric-delivered",
            order_id=delivered_order,
            shipment_kwargs={
                "tracking_number": "known-tracking",
                "status_category": "delivered",
                "delivered_at": self.FIXED_NOW - timedelta(days=2),
                "last_successful_sync_at": self.FIXED_NOW - timedelta(hours=1),
                "request_fingerprint": delivered_fingerprint,
                "needs_delivery_time_confirmation": False,
            },
        )
        fresh_order = "222456789012345"
        rows = [
            self.build_sample_row(
                apply_id="metric-delivered",
                creator_id="metric-delivered",
                order_id=delivered_order,
            ),
            self.build_sample_row(
                apply_id="metric-fresh",
                creator_id="metric-fresh",
                order_id=fresh_order,
            ),
        ]

        result, _enter, _fetch = self._run_sync(
            rows,
            fetch_details=self.build_logistics_details("fresh-tracking"),
            clock=lambda: self.FIXED_NOW,
        )

        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["unique_orders"], 2)
        self.assertEqual(result["fetched"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["synchronized_cases"], 1)
        self.assertEqual(result["changed"], 1)

    @patch("assistant.services.shipment_service.time.monotonic")
    def test_batch_deadline_exceeded_stops_before_next_order(self, monotonic) -> None:
        from lib.sync_errors import BatchDeadlineExceeded

        first_row = self.build_sample_row(
            apply_id="deadline-apply-one",
            creator_id="deadline-creator-one",
            order_id="123456789012345",
        )
        second_row = self.build_sample_row(
            apply_id="deadline-apply-two",
            creator_id="deadline-creator-two",
            order_id="223456789012345",
        )
        clock = {"now": 0.0}
        monotonic.side_effect = lambda: clock["now"]

        def fetch_consuming_time(_store_id, _order_id, **_kwargs):
            # 模拟一次物流 GET 消耗 400 秒真实墙钟。
            clock["now"] += 400
            return self.build_logistics_details("slow-tracking")

        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
            patch("lib.sample_api.scrape_shipped_list_api", return_value=[first_row, second_row]),
            patch("lib.sample_api.scrape_processing_list_api", return_value=[]),
            patch(
                "lib.order_api.enter_seller_order_page",
                return_value=SellerPageContext(
                    href="https://seller.us.tiktokshopglobalselling.com/order?tab=all",
                    shop_id="shop",
                    shop_region="US",
                ),
            ),
            patch(
                "lib.order_api.fetch_logistics_details_in_seller_context",
                side_effect=fetch_consuming_time,
            ) as fetch_details,
            patch("lib.sample_navigation.navigate_to_sample_request", return_value={"ok": True}),
        ):
            with self.assertRaises(BatchDeadlineExceeded):
                ShipmentService(self.session_factory).synchronize_shipments(
                    {"storeId": "ziniao", "storeName": "store"},
                    job_progress=lambda *args: None,
                )

        # 批次 deadline = 45+120+70*2+30 = 335s；第一个订单就耗掉 400s，
        # 第二个订单不允许再启动。
        self.assertEqual(fetch_details.call_count, 1)

    def test_processing_schema_failure_never_uses_shipped_dom_fallback(self) -> None:
        warnings = []
        with (
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
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
            patch(
                "lib.shipped_dom.ensure_sample_page_loaded",
                return_value={"ok": True, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop&shop_region=US"},
            ),
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
        app = create_app(port=8765)
        app.state.session_factory = self.session_factory
        with TestClient(app, base_url="http://127.0.0.1:8765") as client:
            response = client.get("/shipments")
        shipment_data = console_data(response)
        self.assertEqual(len(shipment_data["rows"]), 1)
        row = shipment_data["rows"][0]
        self.assertEqual(row["delivered_at"], "")
        self.assertFalse(row["needs_delivery_confirmation"])
        self.assertEqual(row["status_label"], "运输中")
        self.assertIn("order-visible", response.text)
        self.assertIn("tracking-visible", response.text)
