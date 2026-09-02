from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.im_dom import (  # noqa: E402
    CLICK_NEW_MESSAGE_RESULT_JS_TMPL,
    composer_ready,
    conversation_matches,
    INSPECT_IM_JS,
    im_thread_text,
    inspect_current_thread,
    open_conversation_via_new_message,
    thread_has_named_intro,
    thread_looks_stale,
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

    def test_conversation_matches_ignores_whole_page_text(self) -> None:
        probe = {
            "hasComposer": True,
            "onIm": True,
            "text": "dani_lynn_colors\nHola rayma2691, gracias por solicitar nuestra muestra",
            "selected_preview": "rayma2691\nHola rayma2691,...",
            "selected_user_id": "111",
        }
        self.assertFalse(
            conversation_matches(probe, "dani_lynn_colors", "7496160321889208868")
        )

    def test_named_intro_ignores_other_creator_thread_and_page_blob(self) -> None:
        leftover = {
            "text": (
                "dani_lynn_colors\n"
                "Hola rayma2691, ¡gracias por solicitar nuestra muestra de lencería!"
            ),
            "thread_text": (
                "Hola rayma2691, ¡gracias por solicitar nuestra muestra de lencería!"
            ),
        }
        self.assertTrue(thread_looks_stale(leftover, "dani_lynn_colors"))
        self.assertFalse(thread_has_named_intro(leftover, "dani_lynn_colors"))
        empty_thread = {
            "text": leftover["text"],
            "thread_text": "无法提供超过 365 天的消息。\n发送消息",
        }
        self.assertFalse(thread_has_named_intro(empty_thread, "dani_lynn_colors"))
        self.assertEqual(im_thread_text(empty_thread), empty_thread["thread_text"])
        own = {
            "thread_text": (
                "Hi dani_lynn_colors, thanks for requesting our lingerie sample!"
            )
        }
        self.assertTrue(thread_has_named_intro(own, "dani_lynn_colors"))

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.inspect_im")
    def test_inspect_current_thread_waits_out_stale_intro(self, inspect, _sleep) -> None:
        inspect.side_effect = [
            {
                "thread_text": (
                    "Hola rayma2691, ¡gracias por solicitar nuestra muestra de lencería!"
                )
            },
            {"thread_text": "无法提供超过 365 天的消息。"},
        ]
        probe = inspect_current_thread("sid", "dani_lynn_colors", wait=1.0)
        self.assertEqual(inspect.call_count, 2)
        self.assertFalse(thread_has_named_intro(probe, "dani_lynn_colors"))

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

    def test_conversation_matches_current_composer_identity(self) -> None:
        probe = {
            "hasComposer": True,
            "current_user_id": "7493993174308326234",
            "current_conversation_id": "7657571010698543373",
            "current_screen_name": "ana_abeilleugc0",
        }
        self.assertTrue(
            conversation_matches(probe, "different-name", "7493993174308326234")
        )
        self.assertTrue(
            conversation_matches(probe, "ana_abeilleugc0", "unknown-id")
        )

    def test_conversation_matches_never_uses_thread_text_as_identity(self) -> None:
        probe = {
            "hasComposer": True,
            "thread_text": "ana_abeilleugc0 你好！你的样品已送达。",
            "current_user_id": "7495628628007553405",
            "current_screen_name": "daimarismendoza20",
        }
        self.assertFalse(
            conversation_matches(probe, "ana_abeilleugc0", "7493993174308326234")
        )

    def test_new_message_scripts_capture_result_and_current_identities(self) -> None:
        self.assertIn("dataSource", CLICK_NEW_MESSAGE_RESULT_JS_TMPL)
        self.assertIn("result_creator_id", CLICK_NEW_MESSAGE_RESULT_JS_TMPL)
        self.assertIn("current_user_id", INSPECT_IM_JS)
        self.assertIn("current_conversation_id", INSPECT_IM_JS)
        self.assertIn("current_screen_name", INSPECT_IM_JS)

    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_open_conversation_via_new_message_uses_sample_page_path(
        self, execute, _sleep, inspect
    ) -> None:
        inspect.side_effect = [
            # 初次检查：已经在样品申请页。
            {"onIm": False, "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=7496019476093176674"},
            # 第一次轮询仍是旧会话。
            {
                "onIm": False,
                "hasComposer": True,
                "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
                "current_user_id": "old-user",
                "current_screen_name": "old-creator",
                "thread_text": "旧会话正文中偶然出现 alice",
            },
            # 后续轮询才看到新消息路径切换后的 composer identity。
            {
                "onIm": False,
                "hasComposer": True,
                "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
                "current_user_id": "alice-id",
                "current_conversation_id": "conversation-alice",
                "current_screen_name": "alice",
            },
        ]
        execute.side_effect = [
            # OPEN_CHAT_PANEL_JS
            {"ok": True, "already": True},
            # CLICK_NEW_MESSAGE_BTN_JS
            {"ok": True, "via": "react-onClick", "depth": 0},
            # FILL_NEW_MESSAGE_SEARCH_JS
            {"ok": True, "value": "alice"},
            # CLICK_NEW_MESSAGE_RESULT_JS
            {"ok": True, "via": "react-onClick", "depth": 0},
        ]

        result = open_conversation_via_new_message(
            "store-test", creator_id="", creator_name="alice"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "new-message")
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(inspect.call_count, 3)
        self.assertEqual(result["confirmation"]["poll_count"], 2)

    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_open_conversation_falls_back_to_handle_within_new_message_path(
        self, execute, _sleep, inspect
    ) -> None:
        inspect.side_effect = [
            {
                "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
            },
            {
                "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
                "hasComposer": True,
                "current_user_id": "creator-id",
                "current_screen_name": "creator_handle",
            },
        ]
        execute.side_effect = [
            {"ok": True, "already": True},
            {"ok": True},
            {"ok": True, "value": "creator-id"},
            {"ok": False, "reason": "no-result-row"},
            {"ok": True, "already": True},
            {"ok": True},
            {"ok": True, "value": "creator_handle"},
            {
                "ok": True,
                "via": "react-onClick",
                "result_creator_id": "creator-id",
            },
        ]

        result = open_conversation_via_new_message(
            "store-test",
            creator_id="creator-id",
            creator_name="creator_handle",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(execute.call_count, 8)
        self.assertEqual(inspect.call_count, 2)

    @patch("lib.im_dom.inspect_im")
    def test_open_conversation_via_new_message_rejects_non_sample_page(
        self, inspect
    ) -> None:
        inspect.side_effect = [
            {"onIm": True, "href": "https://affiliate.tiktokshopglobalselling.com/seller/im?shop_id=7496019476093176674"},
        ]

        result = open_conversation_via_new_message(
            "store-test", creator_id="", creator_name="alice"
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "必须停在样品申请页")


if __name__ == "__main__":
    unittest.main()
