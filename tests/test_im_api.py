from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.im_api import send_message_via_sdk  # noqa: E402


class ImSdkSendTests(unittest.TestCase):
    @patch("lib.im_api.zclaw_exec")
    def test_rejects_empty_message_without_page_execution(self, execute) -> None:
        result = send_message_via_sdk(
            "store-test",
            "",
            expected_creator_name="creator_test",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "empty-message")
        execute.assert_not_called()

    @patch(
        "lib.im_api.zclaw_exec",
        return_value={"ok": True, "state": "sdk-send-started"},
    )
    def test_uses_page_sdk_handler_without_retry_or_dom_click(self, execute) -> None:
        result = send_message_via_sdk(
            "store-test",
            "Dear, here is the tracking number:TEST123",
            expected_creator_name="creator_test",
            expected_creator_id="creator-id",
            conversation_id="conversation-id",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["expected_creator_id"], "creator-id")
        self.assertEqual(result["conversation_id"], "conversation-id")
        execute.assert_called_once()
        self.assertEqual(execute.call_args.kwargs["retries"], 0)
        script = execute.call_args.args[1]
        self.assertIn("onSendText", script)
        self.assertIn("contactCard", script)
        self.assertIn("selectedText", script)
        self.assertNotIn(".click()", script)
        self.assertNotIn("document.cookie", script.lower())
        self.assertLess(script.index("composer"), script.index("not-on-im-page"))

    @patch("lib.im_api.zclaw_exec")
    def test_rejects_message_longer_than_platform_limit(self, execute) -> None:
        result = send_message_via_sdk(
            "store-test",
            "x" * 2001,
            expected_creator_name="creator_test",
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "message-too-long")
        execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
