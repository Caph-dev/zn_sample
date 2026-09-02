from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.im_api import send_direct_message, send_message_via_sdk  # noqa: E402


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
        self.assertIn("expectedCreatorId", script)
        self.assertIn("currentUserId", script)
        self.assertIn("currentScreenName", script)
        self.assertNotIn(".click()", script)
        self.assertNotIn("document.cookie", script.lower())
        self.assertIn("not-in-new-message-conversation", script)
        self.assertNotIn("onImPage", script)

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

    @patch("lib.im_api.send_message_via_sdk")
    @patch("lib.im_dom.fill_or_send_message")
    @patch("lib.im_dom.inspect_current_thread", return_value={"thread_text": ""})
    @patch(
        "lib.im_dom.open_conversation_via_new_message",
        return_value={"ok": True, "click": {"conversation_id": "conv"}},
    )
    def test_send_direct_message_defaults_to_dry_run(
        self,
        open_conversation,
        inspect_thread,
        fill_or_send,
        send_via_sdk,
    ) -> None:
        result = send_direct_message(
            "store-test",
            "SOP follow-up copy",
            creator_name="creator_test",
            creator_id="creator-id",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(result["message"], "SOP follow-up copy")
        open_conversation.assert_called_once()
        inspect_thread.assert_called_once()
        send_via_sdk.assert_not_called()
        fill_or_send.assert_not_called()

    @patch("lib.im_api.time.sleep", return_value=None)
    @patch("lib.im_api.send_message_via_sdk")
    @patch("lib.im_dom.inspect_current_thread", return_value={"thread_text": ""})
    @patch(
        "lib.im_dom.open_conversation_via_new_message",
        return_value={
            "ok": True,
            "click": {"result_creator_id": "result-creator-id"},
        },
    )
    def test_send_direct_message_reuses_result_creator_id(
        self,
        open_conversation,
        inspect_thread,
        send_via_sdk,
        _sleep,
    ) -> None:
        send_via_sdk.return_value = {"ok": True, "state": "sdk-send-started"}

        result = send_direct_message(
            "store-test",
            "SOP follow-up copy",
            creator_name="creator_test",
            execute=True,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        open_conversation.assert_called_once()
        self.assertEqual(
            send_via_sdk.call_args.kwargs["expected_creator_id"],
            "result-creator-id",
        )
        inspect_thread.assert_called()


if __name__ == "__main__":
    unittest.main()
