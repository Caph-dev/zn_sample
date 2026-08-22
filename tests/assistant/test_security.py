from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from fastapi import Request
from fastapi.testclient import TestClient

from assistant.app import create_app
from assistant.security.secret_redaction import redact_text


class LocalSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.runtime_directory = Path(self.temporary_directory.name) / "runtime"
        self.app = create_app(runtime_directory=self.runtime_directory, port=8765)

        @self.app.post("/test-post")
        def protected_post(request: Request) -> dict:
            return {"ok": True, "csrf": request.state.session["csrf"]}

        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def bootstrap_session(self) -> str:
        token = self.app.state.session_manager.issue_bootstrap_token()
        response = self.client.get(f"/bootstrap?token={token}", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        session = self.app.state.session_manager.read_session(
            self.client.cookies.get("zn_assistant_session")
        )
        return session["csrf"]

    def test_wrong_and_reused_bootstrap_tokens_fail(self) -> None:
        token = self.app.state.session_manager.issue_bootstrap_token()
        self.assertEqual(self.client.get("/bootstrap?token=wrong").status_code, 401)
        self.assertEqual(
            self.client.get(f"/bootstrap?token={token}", follow_redirects=False).status_code,
            302,
        )
        self.assertEqual(self.client.get(f"/bootstrap?token={token}").status_code, 401)
        self.assertFalse(self.app.state.session_manager.bootstrap_path.exists())

    def test_non_local_host_is_rejected(self) -> None:
        response = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)

    def test_post_requires_csrf_and_local_origin(self) -> None:
        csrf_token = self.bootstrap_session()
        self.assertEqual(
            self.client.post(
                "/test-post", headers={"Origin": "http://127.0.0.1:8765"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/test-post",
                headers={
                    "Origin": "http://evil.example",
                    "X-CSRF-Token": csrf_token,
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/test-post",
                headers={
                    "Origin": "http://localhost:8765",
                    "X-CSRF-Token": csrf_token,
                },
            ).status_code,
            200,
        )

    def test_secret_redaction_removes_values(self) -> None:
        redacted = redact_text(
            "app_secret=alpha access_token=beta cookie=gamma tenant_access_token=delta"
        )
        for secret_value in ("alpha", "beta", "gamma", "delta"):
            self.assertNotIn(secret_value, redacted)
        self.assertIn("[redacted]", redacted)


if __name__ == "__main__":
    unittest.main()
