from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.network_observer import (  # noqa: E402
    ARM_NETWORK_OBSERVER_JS,
    arm_network_observer,
    drain_network_observer,
)


class NetworkObserverSafetyTests(unittest.TestCase):
    def test_observer_does_not_read_session_or_request_headers(self) -> None:
        normalized_script = ARM_NETWORK_OBSERVER_JS.lower()

        self.assertNotIn("document." + "cookie", normalized_script)
        self.assertNotIn("request.headers", normalized_script)
        self.assertNotIn("setrequestheader", normalized_script)
        self.assertNotIn("responsetext:", normalized_script)

    def test_observer_only_installs_wrappers_until_page_makes_request(self) -> None:
        with patch(
            "lib.network_observer.zclaw_exec",
            return_value={"ok": True, "installed": True},
        ) as execute:
            result = arm_network_observer("store-test")

        self.assertTrue(result["ok"])
        script = execute.call_args.args[1]
        self.assertIn("window.fetch = function", script)
        self.assertNotIn("fetch('/api", script)
        self.assertNotIn('request.open(\'POST\'', script)

    def test_drain_returns_only_mapping_entries(self) -> None:
        with patch(
            "lib.network_observer.zclaw_exec",
            return_value={
                "ok": True,
                "entries": [
                    {
                        "method": "POST",
                        "endpoint": "/api/v1/example",
                        "body_shape": {"type": "object"},
                    },
                    "invalid-entry",
                ],
            },
        ):
            entries = drain_network_observer("store-test")

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["endpoint"], "/api/v1/example")


if __name__ == "__main__":
    unittest.main()
