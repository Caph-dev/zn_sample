from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from assistant.app import create_app
from assistant.database.engine import create_database_engine
from assistant.database.models import Base, FollowupTask, SampleCase, Shipment, Store
from assistant.services.export_service import ExportService


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
                    case = SampleCase(store_id=store.id, creator_id=str(status), creator_name="creator", apply_id=str(status), product_id="1732414717062320994", curr_status=status, main_order_id=f"order-{status}")
                    session.add(case); session.flush()
                    session.add(Shipment(sample_case_id=case.id, tracking_display=f"track-{status}"))
                    session.add(FollowupTask(sample_case_id=case.id, stage="day_10_list", scheduled_for=date(2026, 8, 1)))
                session.commit()
            path = ExportService(factory, exports_directory=root / "exports").export("day_10_list")
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("order-40", text); self.assertIn("track-40", text)
            self.assertNotIn("order-30", text)
            engine.dispose()

    def test_json_and_form_job_creation_and_csrf_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            engine = create_database_engine(root / "test.sqlite3")
            Base.metadata.create_all(engine)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            app = create_app(runtime_directory=root / "runtime", port=8765)
            app.state.session_factory = factory
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                token = app.state.session_manager.issue_bootstrap_token()
                client.get(f"/bootstrap?token={token}", follow_redirects=False)
                session_data = app.state.session_manager.read_session(client.cookies.get("zn_assistant_session"))
                headers = {"Origin": "http://127.0.0.1:8765", "X-CSRF-Token": session_data["csrf"]}
                self.assertEqual(client.post("/api/jobs/report-export", json={"kind": "today"}).status_code, 403)
                json_response = client.post("/api/jobs/report-export", json={"kind": "today"}, headers=headers)
                form_response = client.post("/api/jobs/report-export", data={"kind": "needs_review"}, headers=headers)
                unsupported = client.post("/api/jobs/report-export", data={"kind": "feishu_update"}, headers=headers)
                self.assertEqual(json_response.status_code, 200)
                self.assertEqual(form_response.status_code, 200)
                self.assertEqual(unsupported.status_code, 400)
                self.assertEqual(unsupported.json()["detail"]["error"], "unsupported-export-kind")
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
            app = create_app(runtime_directory=root / "runtime", port=8765)
            with TestClient(app, base_url="http://127.0.0.1:8765") as client:
                token = app.state.session_manager.issue_bootstrap_token()
                client.get(f"/bootstrap?token={token}", follow_redirects=False)
                with patch("assistant.paths.user_data_dir", return_value=root / "user"):
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "allowed.csv"}).status_code, 200)
                    self.assertEqual(client.get("/api/exports/download", params={"path": str(external)}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "note.txt"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "../outside.csv"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "escaping.csv"}).status_code, 404)
                    self.assertEqual(client.get("/api/exports/download", params={"filename": "."}).status_code, 404)
