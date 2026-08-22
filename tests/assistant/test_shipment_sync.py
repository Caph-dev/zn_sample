from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, SampleCase, ShipmentSnapshot
from assistant.services.shipment_service import ShipmentService


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
