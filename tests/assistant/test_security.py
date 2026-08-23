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
        self.app = create_app(port=8765)

        @self.app.post("/test-post")
        def protected_post(request: Request) -> dict:
            return {"ok": True}

        self.client = TestClient(self.app, base_url="http://127.0.0.1:8765")

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_non_local_host_is_rejected(self) -> None:
        response = self.client.get("/api/health", headers={"Host": "evil.example"})
        self.assertEqual(response.status_code, 400)

    def test_home_renders_without_session(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])

    def test_post_requires_local_origin(self) -> None:
        self.assertEqual(self.client.post("/test-post").status_code, 403)
        self.assertEqual(
            self.client.post(
                "/test-post", headers={"Origin": "http://evil.example"}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/test-post",
                headers={"Origin": "http://localhost:not-a-port"},
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(
                "/test-post",
                headers={"Origin": "http://localhost:8765"},
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

    def test_secret_redaction_handles_structured_and_punctuation_boundaries(self) -> None:
        redaction_cases = (
            (
                "app_secret=flat_app_secret_sentinel",
                "app_secret=[redacted]",
            ),
            (
                "access_token:flat_access_token_sentinel",
                "access_token:[redacted]",
            ),
            (
                '{"tenant_access_token": "json_tenant_access_token_sentinel"}',
                '{"tenant_access_token": "[redacted]"}',
            ),
            (
                "{'cookie': 'dict_cookie_sentinel'}",
                "{'cookie': '[redacted]'}",
            ),
            (
                "cookie=comma_cookie_sentinel, ordinary=value",
                "cookie=[redacted], ordinary=value",
            ),
            (
                "access_token=parenthesis_access_token_sentinel)",
                "access_token=[redacted])",
            ),
        )
        synthetic_secret_values = (
            "flat_app_secret_sentinel",
            "flat_access_token_sentinel",
            "json_tenant_access_token_sentinel",
            "dict_cookie_sentinel",
            "comma_cookie_sentinel",
            "parenthesis_access_token_sentinel",
        )

        redacted_outputs = [redact_text(source_text) for source_text, _ in redaction_cases]
        for redacted_output, (_, expected_output) in zip(redacted_outputs, redaction_cases):
            self.assertEqual(redacted_output, expected_output)
        for synthetic_secret_value in synthetic_secret_values:
            self.assertTrue(
                all(synthetic_secret_value not in redacted_output for redacted_output in redacted_outputs)
            )

        ordinary_text = "Ordinary text without a credential assignment remains unchanged."
        self.assertEqual(redact_text(ordinary_text), ordinary_text)


if __name__ == "__main__":
    unittest.main()
