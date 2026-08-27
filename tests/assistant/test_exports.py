from __future__ import annotations

import csv
import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import assistant.api.exports as exports_api
from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, Job, SampleCase, Shipment, Store
from assistant.jobs.locks import create_or_get_pending_job
from assistant.jobs.worker import _claim_next_job
from assistant.services.export_service import (
    EXPORT_FIELDS,
    ExportService,
    serialize_csv_cell_value,
)


class ExportTests(unittest.TestCase):
    def test_day_10_export_excludes_shipped_and_keeps_ids_separate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                store = Store(ziniao_store_id="store", store_name="store"); session.add(store); session.flush()
                for status in (30, 40):
                    case = SampleCase(
                        store_id=store.id,
                        creator_id=str(status),
                        creator_name="creator",
                        apply_id=str(status),
                        product_id="1732414717062320994",
                        curr_status=status,
                        platform_status="processing" if status == 40 else "shipped",
                        main_order_id=f"order-{status}",
                    )
                    session.add(case); session.flush()
                    session.add(Shipment(sample_case_id=case.id, tracking_display=f"track-{status}"))
                    session.add(FollowupTask(sample_case_id=case.id, stage="day_10_list", scheduled_for=date(2026, 8, 1)))
                session.commit()
            path = ExportService(factory, exports_directory=root / "exports").export("day_10_list")
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("order-40", text); self.assertIn("track-40", text)
            self.assertNotIn("order-30", text)
            engine.dispose()

    def test_day_10_export_filename_uses_beijing_yyyymmdd(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with patch(
                "assistant.services.export_service.beijing_now",
                return_value=datetime(2026, 8, 27, 9, 30),
            ):
                path = ExportService(
                    factory, exports_directory=root / "exports"
                ).export("day_10_list")
            self.assertEqual(path.name, "处理中超过10天_20260827.csv")
            today_path = ExportService(
                factory, exports_directory=root / "exports"
            ).export("today")
            self.assertRegex(today_path.name, r"^today_\d{8}T\d{6}Z\.csv$")
            engine.dispose()

    def test_export_serializes_formula_prefix_cells_as_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            dangerous_prefix_values = {
                "store_name": "=formula_store_name",
                "creator_name": "+formula_creator_name",
                "creator_id": "-formula_creator_id",
                "product_id": "@formula_product_id",
            }
            ordinary_apply_id = "ordinary-apply-id"
            with factory() as session:
                store = Store(
                    ziniao_store_id="store",
                    store_name=dangerous_prefix_values["store_name"],
                )
                session.add(store)
                session.flush()
                sample_case = SampleCase(
                    store_id=store.id,
                    creator_id=dangerous_prefix_values["creator_id"],
                    creator_name=dangerous_prefix_values["creator_name"],
                    apply_id=ordinary_apply_id,
                    product_id=dangerous_prefix_values["product_id"],
                    curr_status=40,
                    platform_status="processing",
                    main_order_id="ordinary-order-id",
                )
                session.add(sample_case)
                session.flush()
                session.add(
                    FollowupTask(
                        sample_case_id=sample_case.id,
                        stage="day_10_list",
                        scheduled_for=date(2026, 8, 1),
                    )
                )
                session.commit()

            export_service = ExportService(factory, exports_directory=root / "exports")
            source_rows = export_service._rows("day_10_list")
            self.assertEqual(len(source_rows), 1)
            source_row = source_rows[0]
            self.assertEqual(tuple(source_row), EXPORT_FIELDS)
            self.assertEqual(len(source_row), len(EXPORT_FIELDS))
            for field_name, dangerous_value in dangerous_prefix_values.items():
                self.assertEqual(source_row[field_name], dangerous_value)
            self.assertEqual(source_row["apply_id"], ordinary_apply_id)
            self.assertEqual(source_row["curr_status"], 40)
            self.assertEqual(
                serialize_csv_cell_value(source_row["curr_status"]),
                source_row["curr_status"],
            )
            self.assertEqual(serialize_csv_cell_value(ordinary_apply_id), ordinary_apply_id)

            output_path = export_service.export("day_10_list")
            with output_path.open("r", encoding="utf-8-sig", newline="") as output_file:
                reader = csv.DictReader(output_file)
                exported_rows = list(reader)

            self.assertEqual(reader.fieldnames, list(EXPORT_FIELDS))
            self.assertEqual(len(exported_rows), 1)
            exported_row = exported_rows[0]
            self.assertEqual(len(exported_row), len(EXPORT_FIELDS))
            for field_name, dangerous_value in dangerous_prefix_values.items():
                self.assertEqual(exported_row[field_name], f"'{dangerous_value}")
            self.assertEqual(exported_row["apply_id"], ordinary_apply_id)
            self.assertEqual(exported_row["curr_status"], "40")
            engine.dispose()

    def test_json_and_form_job_creation_and_origin_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            app = create_app(port=8765)
            app.state.session_factory = factory
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                headers = {"Origin": "http://127.0.0.1:8765"}
                self.assertEqual(client.post("/api/jobs/report-export", json={"kind": "today"}).status_code, 403)
                json_response = client.post("/api/jobs/report-export", json={"kind": "today"}, headers=headers)
                form_response = client.post("/api/jobs/report-export", data={"kind": "needs_review"}, headers=headers)
                unsupported = client.post("/api/jobs/report-export", data={"kind": "feishu_update"}, headers=headers)
                self.assertEqual(json_response.status_code, 200)
                self.assertEqual(form_response.status_code, 200)
                self.assertEqual(unsupported.status_code, 400)
                self.assertEqual(unsupported.json()["detail"]["error"], "unsupported-export-kind")
            engine.dispose()

    def test_report_export_job_has_kind_before_worker_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            claimed_result_summaries: list[str] = []

            def create_and_claim_job(
                session_factory,
                *,
                job_type: str,
                store_id: str | None,
                result_summary: str = "",
            ) -> tuple[str, bool]:
                job_id, deduplicated = create_or_get_pending_job(
                    session_factory,
                    job_type=job_type,
                    store_id=store_id,
                    result_summary=result_summary,
                )
                self.assertEqual(_claim_next_job(session_factory), job_id)
                with session_factory() as session:
                    claimed_job = session.get(Job, job_id)
                    if claimed_job is None:
                        self.fail("worker claim did not persist the export job")
                    claimed_result_summaries.append(claimed_job.result_summary)
                return job_id, deduplicated

            app = create_app(port=8765)
            app.state.session_factory = factory
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                headers = {"Origin": "http://127.0.0.1:8765"}
                with patch.object(
                    exports_api,
                    "create_or_get_pending_job",
                    side_effect=create_and_claim_job,
                ):
                    response = client.post(
                        "/api/jobs/report-export",
                        json={"kind": "today"},
                        headers=headers,
                    )

            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()["deduplicated"])
            self.assertEqual(
                [json.loads(summary) for summary in claimed_result_summaries],
                [{"kind": "today"}],
            )
            engine.dispose()

    def test_pending_job_deduplication_preserves_first_result_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            first_result_summary = json.dumps({"kind": "today"})
            duplicate_result_summary = json.dumps({"kind": "needs_review"})

            job_id, deduplicated = create_or_get_pending_job(
                factory,
                job_type="report_export",
                store_id="shared-store",
                result_summary=first_result_summary,
            )
            duplicate_job_id, duplicate_request = create_or_get_pending_job(
                factory,
                job_type="report_export",
                store_id="shared-store",
                result_summary=duplicate_result_summary,
            )

            self.assertFalse(deduplicated)
            self.assertTrue(duplicate_request)
            self.assertEqual(duplicate_job_id, job_id)
            with factory() as session:
                job = session.get(Job, job_id)
                if job is None:
                    self.fail("first export job was not persisted")
                self.assertEqual(job.result_summary, first_result_summary)
            engine.dispose()

    def test_download_is_confined_to_canonical_csv_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical_directory = root / "user" / "exports"
            canonical_directory.mkdir(parents=True)
            allowed = canonical_directory / "allowed.csv"
            allowed.write_text("safe", encoding="utf-8")
            non_csv = canonical_directory / "note.txt"
            non_csv.write_text("not csv", encoding="utf-8")
            alternate = root / "alternate" / "exports"
            alternate.mkdir(parents=True)
            external = alternate / "external.csv"
            external.write_text("private", encoding="utf-8")
            escaping_symlink = canonical_directory / "escaping.csv"
            escaping_symlink.symlink_to(external)
            app = create_app(port=8765)
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                with patch("assistant.paths.user_data_dir", return_value=root / "user"):
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "allowed.csv"}).status_code, 200)
                    self.assertEqual(client.get("/api/exports/download", params={"path": str(external)}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "note.txt"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "../outside.csv"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "escaping.csv"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "."}).status_code, 404)
