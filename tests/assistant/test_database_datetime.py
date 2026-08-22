from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.database.models import Base, SampleCase, Shipment, ShipmentSnapshot, Store
from assistant.domain.timeutil import beijing_date


class ShipmentDatetimeRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "shipment-datetime.sqlite3"
        )
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_logistics_timestamps_round_trip_as_aware_utc(self) -> None:
        timestamp_cases = (
            (datetime(2026, 8, 21, 15, 59, tzinfo=timezone.utc), date(2026, 8, 21)),
            (datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc), date(2026, 8, 22)),
            (datetime(2026, 8, 21, 23, 59, tzinfo=timezone.utc), date(2026, 8, 22)),
        )

        with self.session_factory() as session:
            store = Store(ziniao_store_id="datetime-store", store_name="datetime-store")
            session.add(store)
            session.flush()

            for timestamp_index, (persisted_timestamp, _expected_beijing_date) in enumerate(
                timestamp_cases
            ):
                sample_case = SampleCase(
                    store_id=store.id,
                    creator_id=f"creator-{timestamp_index}",
                    creator_name=f"creator-{timestamp_index}",
                    apply_id=f"apply-{timestamp_index}",
                    product_id="1732414717062320994",
                )
                session.add(sample_case)
                session.flush()
                shipment = Shipment(
                    sample_case_id=sample_case.id,
                    estimated_delivery_at=persisted_timestamp,
                    delivered_at=persisted_timestamp,
                    last_event_at=persisted_timestamp,
                )
                session.add(shipment)
                session.flush()
                session.add(
                    ShipmentSnapshot(
                        shipment_id=shipment.id,
                        estimated_delivery_at=persisted_timestamp,
                        delivered_at=persisted_timestamp,
                        last_event_at=persisted_timestamp,
                    )
                )
            session.commit()

        with self.session_factory() as session:
            shipments = session.scalars(select(Shipment).order_by(Shipment.id)).all()
            snapshots = session.scalars(
                select(ShipmentSnapshot).order_by(ShipmentSnapshot.id)
            ).all()

        shipment_timestamp_fields = (
            "estimated_delivery_at",
            "delivered_at",
            "last_event_at",
        )
        snapshot_timestamp_fields = (
            "estimated_delivery_at",
            "delivered_at",
            "last_event_at",
        )
        for (
            persisted_timestamp,
            expected_beijing_date,
        ), shipment, snapshot in zip(timestamp_cases, shipments, snapshots):
            with self.subTest(timestamp=persisted_timestamp, model="shipment"):
                for timestamp_field in shipment_timestamp_fields:
                    returned_timestamp = getattr(shipment, timestamp_field)
                    self.assertEqual(returned_timestamp, persisted_timestamp)
                    self.assertIs(returned_timestamp.tzinfo, timezone.utc)
                    self.assertEqual(beijing_date(returned_timestamp), expected_beijing_date)
            with self.subTest(timestamp=persisted_timestamp, model="snapshot"):
                for timestamp_field in snapshot_timestamp_fields:
                    returned_timestamp = getattr(snapshot, timestamp_field)
                    self.assertEqual(returned_timestamp, persisted_timestamp)
                    self.assertIs(returned_timestamp.tzinfo, timezone.utc)
                    self.assertEqual(beijing_date(returned_timestamp), expected_beijing_date)

    def test_aware_values_are_normalized_to_utc_before_storage(self) -> None:
        beijing_midnight = datetime(
            2026,
            8,
            22,
            0,
            0,
            tzinfo=timezone(timedelta(hours=8)),
        )
        expected_utc = datetime(2026, 8, 21, 16, 0, tzinfo=timezone.utc)

        with self.session_factory() as session:
            store = Store(ziniao_store_id="normalization-store", store_name="normalization-store")
            session.add(store)
            session.flush()
            sample_case = SampleCase(
                store_id=store.id,
                creator_id="normalization-creator",
                creator_name="normalization-creator",
                apply_id="normalization-apply",
                product_id="1732414717062320994",
            )
            session.add(sample_case)
            session.flush()
            session.add(
                Shipment(
                    sample_case_id=sample_case.id,
                    delivered_at=beijing_midnight,
                )
            )
            session.commit()

        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment))
            self.assertEqual(shipment.delivered_at, expected_utc)
            self.assertIs(shipment.delivered_at.tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
