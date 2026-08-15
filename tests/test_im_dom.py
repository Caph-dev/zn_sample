from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.im_dom import (  # noqa: E402
    composer_ready,
    conversation_matches,
    open_im_from_detail,
    open_target_conversation,
)


class ImOpenPathTests(unittest.TestCase):
    def test_composer_ready_accepts_overlay_without_im_url(self) -> None:
        self.assertTrue(composer_ready({"hasComposer": True, "onIm": False}))
        self.assertFalse(composer_ready({"hasComposer": False, "onIm": False}))
        self.assertFalse(composer_ready(None))

    def test_conversation_matches_uses_selected_card_not_page_header(self) -> None:
        probe = {
            "hasComposer": True,
            "onIm": False,
            "text": "达人详情\ninformateymas0721\n邀请\n发送消息",
            "selected_preview": "brittanynorton1002\nHi",
            "selected_user_id": "111",
        }
        self.assertFalse(
            conversation_matches(probe, "informateymas0721", "7495187564109793532")
        )
        probe["selected_preview"] = "informateymas0721\nHola"
        probe["selected_user_id"] = "7495187564109793532"
        self.assertTrue(
            conversation_matches(probe, "informateymas0721", "7495187564109793532")
        )

    def test_conversation_matches_creator_id_even_if_preview_truncated(self) -> None:
        probe = {
            "hasComposer": True,
            "onIm": False,
            "selected_preview": "informa...\nHola",
            "selected_user_id": "7495187564109793532",
        }
        self.assertTrue(
            conversation_matches(probe, "informateymas0721", "7495187564109793532")
        )

    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.zclaw_exec")
    def test_open_im_from_detail_accepts_existing_overlay(self, execute, inspect) -> None:
        inspect.return_value = {
            "hasComposer": True,
            "onIm": False,
            "href": "https://example/creator/detail",
        }

        result = open_im_from_detail("store-test")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "already-open-overlay")
        execute.assert_not_called()

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.zclaw_exec")
    def test_open_im_from_detail_uses_handleclick_then_overlay(
        self, execute, inspect, _sleep
    ) -> None:
        inspect.side_effect = [
            {"hasComposer": False, "onIm": False, "hasDock": True},
            {"hasComposer": True, "onIm": False, "hasDock": True},
        ]
        execute.return_value = {"ok": True, "via": "handleClick", "opened": []}

        result = open_im_from_detail("store-test")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "overlay")
        execute.assert_called_once()
        self.assertIn("handleClick", execute.call_args.args[1])

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.zclaw_exec")
    def test_open_im_from_detail_expands_dock_only_after_message_click(
        self, execute, inspect, _sleep
    ) -> None:
        inspect.side_effect = [
            {"hasComposer": False, "onIm": False, "hasDock": True},
            {"hasComposer": False, "onIm": False, "hasDock": True},
            {"hasComposer": True, "onIm": False, "hasDock": True},
        ]
        execute.side_effect = [
            {"ok": True, "via": "handleClick", "opened": []},
            {"ok": True, "via": "react-onClick"},
        ]

        result = open_im_from_detail("store-test")

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "overlay-expanded")
        self.assertEqual(execute.call_count, 2)
        self.assertIn("handleClick", execute.call_args_list[0].args[1])
        self.assertIn("entryWrapper", execute.call_args_list[1].args[1])

    @patch("lib.im_dom.search_and_open_conversation")
    @patch("lib.im_dom.open_im_inbox")
    @patch("lib.im_dom.open_im_from_detail")
    def test_open_target_conversation_uses_detail_overlay(
        self, from_detail, inbox, search
    ) -> None:
        from_detail.return_value = {"ok": True, "via": "overlay"}
        search.return_value = {
            "ok": True,
            "via": "already-selected",
            "click": {"ok": True, "conversation_id": "conv-1"},
        }

        result = open_target_conversation(
            "store-test",
            "informateymas0721",
            creator_id="7495187564109793532",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "detail-direct")
        inbox.assert_not_called()


if __name__ == "__main__":
    unittest.main()
