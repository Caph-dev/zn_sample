from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.im_dom import (  # noqa: E402
    CHAT_PANEL_READY_JS,
    CLICK_NEW_MESSAGE_BTN_JS,
    CLICK_NEW_MESSAGE_RESULT_JS_TMPL,
    composer_identity_matches,
    composer_ready,
    conversation_matches,
    FILL_NEW_MESSAGE_SEARCH_JS_TMPL,
    INSPECT_IM_JS,
    im_thread_text,
    inspect_current_thread,
    inspected_messages,
    NEW_MESSAGE_DRAWER_READY_JS,
    OPEN_CHAT_PANEL_JS,
    open_conversation_via_new_message,
    parse_chat_time_text,
    search_result_identity_from_data_source,
    self_message_in_window_predicate,
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

    def test_composer_identity_matches_id_or_handle_and_refuses_card_substring(
        self,
    ) -> None:
        composer = {
            "hasComposer": True,
            "current_user_id": "7493993174308326234",
            "current_screen_name": "ana_abeilleugc0",
        }
        self.assertTrue(
            composer_identity_matches(
                composer, "different-name", "7493993174308326234"
            )
        )
        self.assertTrue(
            composer_identity_matches(composer, "ana_abeilleugc0", "unknown-id")
        )
        card_only = {
            "hasComposer": True,
            "selected_preview": "ana_abeilleugc0\nHi ana_abeilleugc0, thanks",
            "selected_user_id": "7493993174308326234",
            "thread_text": "Hi ana_abeilleugc0, thanks for requesting our sample!",
        }
        self.assertFalse(
            composer_identity_matches(
                card_only, "ana_abeilleugc0", "7493993174308326234"
            )
        )
        self.assertTrue(
            conversation_matches(card_only, "ana_abeilleugc0", "7493993174308326234")
        )
        missing = {"hasComposer": True, "thread_text": "", "selected_preview": ""}
        self.assertFalse(
            composer_identity_matches(missing, "ana_abeilleugc0", "7493993174308326234")
        )

    def test_new_message_scripts_capture_result_and_current_identities(self) -> None:
        self.assertIn("dataSource", CLICK_NEW_MESSAGE_RESULT_JS_TMPL)
        self.assertIn("result_creator_id", CLICK_NEW_MESSAGE_RESULT_JS_TMPL)
        self.assertIn("current_user_id", INSPECT_IM_JS)
        self.assertIn("current_conversation_id", INSPECT_IM_JS)
        self.assertIn("current_screen_name", INSPECT_IM_JS)

    def test_message_scan_tolerates_a_page_without_composer(self) -> None:
        """导航后还没开会话时 input 为 null，逐条消息扫描不能抛错。"""
        self.assertIn("const messageScope = (() => {\n    if (!input) return null;", INSPECT_IM_JS)
        self.assertIn("if (messageScope) {", INSPECT_IM_JS)
        self.assertIn("threadMessages", INSPECT_IM_JS)

    def test_overlay_lookups_require_a_visible_node(self) -> None:
        """弹层关闭后 DOM 还在（宽高 0）：只看文字会把旧面板当成还开着。"""
        scripts = {
            "open_chat_panel": OPEN_CHAT_PANEL_JS,
            "click_new_message_btn": CLICK_NEW_MESSAGE_BTN_JS,
            "fill_new_message_search": FILL_NEW_MESSAGE_SEARCH_JS_TMPL,
            "chat_panel_ready": CHAT_PANEL_READY_JS,
            "new_message_drawer_ready": NEW_MESSAGE_DRAWER_READY_JS,
            "click_new_message_result": CLICK_NEW_MESSAGE_RESULT_JS_TMPL,
        }
        for name, script in scripts.items():
            with self.subTest(script=name):
                self.assertIn("getBoundingClientRect().width > 0", script)

    def test_new_message_result_identity_binds_array_item_to_row(self) -> None:
        script = CLICK_NEW_MESSAGE_RESULT_JS_TMPL
        self.assertNotIn(
            "Array.isArray(dataSource) ? dataSource[0] : dataSource",
            script,
        )
        self.assertIn("dataSource.length === 1", script)
        self.assertIn("rowOwnsItem", script)
        self.assertIn("firstLine", script)
        self.assertIn("identityFromItem", script)
        data_source = [
            {"creator_oecuid": "id-alice", "handle": "alice", "nickname": "Alice"},
            {"creator_oecuid": "id-bob", "handle": "bob", "nickname": "Bob"},
        ]
        self.assertEqual(
            search_result_identity_from_data_source(
                data_source, "bob\nHi alice, thanks"
            )["creatorId"],
            "id-bob",
        )
        self.assertEqual(
            search_result_identity_from_data_source(
                data_source, "alice\nLast message"
            )["creatorId"],
            "id-alice",
        )
        self.assertEqual(
            search_result_identity_from_data_source(
                data_source, "nobody\nHi bob"
            )["creatorId"],
            "",
        )
        self.assertEqual(
            search_result_identity_from_data_source(
                [data_source[0]], "bob\nHi"
            )["creatorId"],
            "id-alice",
        )
        self.assertEqual(
            search_result_identity_from_data_source(
                data_source, "alic...\nHola"
            )["creatorId"],
            "id-alice",
        )

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

        with patch("lib.im_dom._wait_for_page_flag", return_value=True):
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

        with (
            patch("lib.im_dom._wait_for_page_flag", return_value=True),
            # 结果行重试的超时归零：每个候选只点一次，重试行为另有专门测试。
            patch("lib.im_dom.NEW_MESSAGE_RESULT_TIMEOUT_SECONDS", 0.0),
        ):
            result = open_conversation_via_new_message(
                "store-test",
                creator_id="creator-id",
                creator_name="creator_handle",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(execute.call_count, 8)
        self.assertEqual(inspect.call_count, 2)

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_result_row_click_retries_until_the_row_renders(self, execute, _sleep) -> None:
        """连续发送时结果行异步刷新：点空要重试，不能直接中断整批。"""
        from lib.im_dom import _click_new_message_result

        execute.side_effect = [
            {"ok": False, "reason": "no-result-row"},
            {"ok": False, "reason": "no-result-row"},
            {"ok": True, "via": "react-onClick", "result_creator_id": "creator-1"},
        ]
        result = _click_new_message_result("store", "creator_handle", timeout=30)
        self.assertTrue(result["ok"])
        self.assertEqual(execute.call_count, 3)

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_result_row_click_gives_up_at_the_deadline(self, execute, _sleep) -> None:
        from lib.im_dom import _click_new_message_result

        execute.side_effect = [{"ok": False, "reason": "no-result-row"}] * 3
        result = _click_new_message_result("store", "creator_handle", timeout=0)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason"), "no-result-row")
        self.assertEqual(execute.call_count, 1)

    @patch("lib.im_dom.inspect_im")
    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_open_conversation_reports_a_panel_that_never_renders(
        self, execute, _sleep, inspect
    ) -> None:
        """面板点了但一直不出现时要报明确错误，不能继续去点 edit 按钮。"""
        inspect.return_value = {
            "href": "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
        }
        execute.side_effect = [
            {"ok": True},  # OPEN_CHAT_PANEL_JS 点击成功
        ]
        with patch("lib.im_dom._wait_for_page_flag", return_value=False):
            result = open_conversation_via_new_message(
                "store-test", creator_id="", creator_name="alice"
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "新消息路径未找到会话")
        self.assertEqual(
            result["attempts"][0]["detail"]["error"], "聊天数面板未出现"
        )
        self.assertEqual(execute.call_count, 1)

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_wait_for_page_flag_polls_until_ready(self, execute, _sleep) -> None:
        import lib.im_dom as im_dom

        execute.side_effect = [{"ok": False}, {"ok": False}, {"ok": True}]
        self.assertTrue(
            im_dom._wait_for_page_flag("store", "probe", timeout=5.0, interval=0.0)
        )
        self.assertEqual(execute.call_count, 3)

    @patch("lib.im_dom.time.sleep", return_value=None)
    @patch("lib.im_dom.zclaw_exec")
    def test_wait_for_page_flag_gives_up_at_the_deadline(self, execute, _sleep) -> None:
        import lib.im_dom as im_dom

        execute.side_effect = lambda *args, **kwargs: {"ok": False}
        self.assertFalse(
            im_dom._wait_for_page_flag("store", "probe", timeout=0.0, interval=0.0)
        )
        self.assertEqual(execute.call_count, 1)

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


class ImMessageWindowTests(unittest.TestCase):
    """逐条消息的方向 + 时间，用于「窗口内已有我方消息」这种与措辞无关的去重。"""

    def test_parses_month_day_labels_without_a_year(self) -> None:
        now = datetime(2026, 9, 10, 12, 0)

        self.assertEqual(
            parse_chat_time_text("Sep 3 5:55 PM", now=now),
            datetime(2026, 9, 3, 17, 55),
        )
        self.assertEqual(
            parse_chat_time_text("September 3, 2026 5:55 PM", now=now),
            datetime(2026, 9, 3, 17, 55),
        )
        self.assertEqual(
            parse_chat_time_text("Sep 3 12:05 AM", now=now),
            datetime(2026, 9, 3, 0, 5),
        )

    def test_parses_weekday_and_relative_labels(self) -> None:
        now = datetime(2026, 9, 10, 12, 0)  # Thursday

        self.assertEqual(
            parse_chat_time_text("Tuesday, 4:55 AM", now=now),
            datetime(2026, 9, 8, 4, 55),
        )
        self.assertEqual(
            parse_chat_time_text("Yesterday 4:55 AM", now=now),
            datetime(2026, 9, 9, 4, 55),
        )
        self.assertEqual(
            parse_chat_time_text("今天 4:55 PM", now=now),
            datetime(2026, 9, 10, 16, 55),
        )

    def test_future_month_day_rolls_back_one_year(self) -> None:
        self.assertEqual(
            parse_chat_time_text("Dec 30 9:00 AM", now=datetime(2027, 1, 3, 8, 0)),
            datetime(2026, 12, 30, 9, 0),
        )

    def test_labels_without_a_clock_or_a_date_stay_unknown(self) -> None:
        now = datetime(2026, 9, 10, 12, 0)

        self.assertIsNone(parse_chat_time_text("昨天", now=now))
        self.assertIsNone(parse_chat_time_text("", now=now))
        self.assertIsNone(parse_chat_time_text("Sep 45 9:00 AM", now=now))

    def test_clock_only_label_means_the_page_local_today(self) -> None:
        # 平台对「今天」的消息只给时刻：``12:41 AM`` / ``上午1:43``。
        self.assertEqual(
            parse_chat_time_text("12:41 AM", now=datetime(2026, 9, 10, 12, 0)),
            datetime(2026, 9, 10, 0, 41),
        )
        self.assertEqual(
            parse_chat_time_text("上午1:43", now=datetime(2026, 9, 10, 12, 0)),
            datetime(2026, 9, 10, 1, 43),
        )

    def test_parses_chinese_labels_measured_on_the_store(self) -> None:
        # 2026-09-10 实测（2 号店中文界面，页面时区 GMT-0700）：
        # 上午1:43 / 昨天 上午5:46 / 星期二, 4:55 上午 / 9月1日 7:41 / 2025年9月16日 6:28
        beijing_now = datetime(2026, 9, 10, 16, 45)

        self.assertEqual(
            parse_chat_time_text("上午1:43", now=beijing_now),
            datetime(2026, 9, 10, 1, 43),
        )
        self.assertEqual(
            parse_chat_time_text("昨天 上午5:46", now=beijing_now, page_offset_minutes=420),
            datetime(2026, 9, 9, 20, 46),
        )
        self.assertEqual(
            parse_chat_time_text("星期二, 4:55 上午", now=beijing_now, page_offset_minutes=420),
            datetime(2026, 9, 8, 19, 55),
        )
        self.assertEqual(
            parse_chat_time_text("9月1日 7:41", now=beijing_now, page_offset_minutes=420),
            datetime(2026, 9, 1, 22, 41),
        )
        self.assertEqual(
            parse_chat_time_text("2025年9月16日 6:28", now=beijing_now, page_offset_minutes=420),
            datetime(2025, 9, 16, 21, 28),
        )
        self.assertEqual(
            parse_chat_time_text("下午11:30", now=beijing_now, page_offset_minutes=420),
            datetime(2026, 9, 11, 14, 30),
        )
        self.assertIsNone(parse_chat_time_text("9月45日 7:41", now=beijing_now))

    def test_page_timezone_offset_shifts_the_label_into_beijing(self) -> None:
        # 页面本地 01:43（GMT-7）= 北京 16:43：不带偏移会算成前一天/同一自然日之外。
        shifted = parse_chat_time_text(
            "上午1:43", now=datetime(2026, 9, 10, 16, 43), page_offset_minutes=420
        )
        self.assertEqual(shifted, datetime(2026, 9, 10, 16, 43))

        unshifted = parse_chat_time_text("上午1:43", now=datetime(2026, 9, 10, 16, 43))
        self.assertEqual(unshifted, datetime(2026, 9, 10, 1, 43))

    def test_spanish_weekday_abbreviation_is_recognized(self) -> None:
        self.assertEqual(
            parse_chat_time_text("mié, 4:55", now=datetime(2026, 9, 10, 12, 0)),
            datetime(2026, 9, 9, 4, 55),
        )

    def test_inspected_messages_ignores_malformed_payloads(self) -> None:
        self.assertEqual(inspected_messages(None), [])
        self.assertEqual(inspected_messages({"messages": "nope"}), [])
        self.assertEqual(
            inspected_messages({"messages": [{"is_self": True}, "bad", 3]}),
            [{"is_self": True, "page_offset_minutes": None}],
        )

    def test_inspected_messages_carries_the_page_timezone_offset(self) -> None:
        self.assertEqual(
            inspected_messages(
                {"page_offset_minutes": 420, "messages": [{"is_self": True}]}
            ),
            [{"is_self": True, "page_offset_minutes": 420}],
        )
        self.assertEqual(
            inspected_messages({"page_offset_minutes": "junk", "messages": [{"is_self": True}]}),
            [{"is_self": True, "page_offset_minutes": None}],
        )

    def test_window_predicate_matches_only_self_messages_inside_the_window(self) -> None:
        predicate = self_message_in_window_predicate(
            stage="arrival",
            delivered_on=date(2026, 9, 8),
            now=datetime(2026, 9, 10, 12, 0),
        )
        self.assertIsNotNone(predicate)

        self.assertEqual(
            predicate([{"is_self": True, "time_text": "Sep 9 7:00 AM", "text": "Hi!"}]),
            "self-message-in-window",
        )
        self.assertEqual(
            predicate([{"is_self": True, "time_text": "Sep 8 7:00 AM"}]),
            "self-message-in-window",
        )
        # 窗口外（更早的介绍话术 / 更晚的消息）、对方消息、无时间标签都不算。
        self.assertEqual(predicate([{"is_self": True, "time_text": "Sep 1 7:00 AM"}]), "")
        self.assertEqual(predicate([{"is_self": True, "time_text": "Sep 20 7:00 AM"}]), "")
        self.assertEqual(predicate([{"is_self": False, "time_text": "Sep 9 7:00 AM"}]), "")
        self.assertEqual(predicate([{"is_self": True, "time_text": ""}]), "")
        self.assertEqual(predicate([]), "")

    def test_window_predicate_reads_chinese_labels_with_page_timezone(self) -> None:
        """2026-09-10 实测：中文界面 + 页面 GMT-7 下同样的消息仍要判成窗口内。"""
        chatty = self_message_in_window_predicate(
            stage="arrival",
            delivered_on=date(2026, 9, 9),
            now=datetime(2026, 9, 10, 16, 45),
        )
        # 259：人工发的文案 + 图，页面显示「昨天 上午5:46」。
        self.assertEqual(
            chatty(
                [
                    {"is_self": False, "time_text": "9月1日 7:57", "page_offset_minutes": 420},
                    {"is_self": True, "time_text": "9月2日 5:23", "page_offset_minutes": 420},
                    {
                        "is_self": True,
                        "time_text": "昨天 上午5:46",
                        "has_image": True,
                        "page_offset_minutes": 420,
                    },
                ]
            ),
            "self-message-in-window",
        )

        # 265：页面显示「星期二, 4:55 上午」（到货 9-08，窗口 9-08..9-10）。
        yenyel = self_message_in_window_predicate(
            stage="arrival",
            delivered_on=date(2026, 9, 8),
            now=datetime(2026, 9, 10, 16, 45),
        )
        self.assertEqual(
            yenyel(
                [
                    {
                        "is_self": True,
                        "time_text": "星期二, 4:55 上午",
                        "page_offset_minutes": 420,
                    }
                ]
            ),
            "self-message-in-window",
        )
        # 更早的介绍/发货通知仍在窗口外。
        self.assertEqual(
            yenyel(
                [
                    {"is_self": True, "time_text": "9月2日 5:37", "page_offset_minutes": 420},
                    {"is_self": True, "time_text": "9月3日 5:27", "page_offset_minutes": 420},
                ]
            ),
            "",
        )

    def test_window_predicate_skips_labels_it_cannot_date(self) -> None:
        predicate = self_message_in_window_predicate(
            stage="arrival",
            delivered_on=date(2026, 9, 9),
            now=datetime(2026, 9, 10, 12, 0),
        )
        self.assertEqual(predicate([{"is_self": True, "time_text": "昨天"}]), "")
        self.assertEqual(
            predicate([{"is_self": True, "time_text": "sep 31 9:00"}]), ""
        )

    def test_window_predicate_covers_each_stage_once(self) -> None:
        now = datetime(2026, 9, 20, 12, 0)
        delivered_on = date(2026, 9, 1)
        # arrival: 09-01..09-03, day_3: 09-04..09-07, day_7: 09-08..09-10。
        windows = {
            "arrival": ("Aug 30 8:00 AM", "Sep 2 8:00 AM"),
            "day_3": ("Sep 2 8:00 AM", "Sep 5 8:00 AM"),
            "day_7": ("Sep 5 8:00 AM", "Sep 9 8:00 AM"),
        }
        for stage, (outside_label, inside_label) in windows.items():
            predicate = self_message_in_window_predicate(
                stage=stage, delivered_on=delivered_on, now=now
            )
            with self.subTest(stage=stage):
                self.assertEqual(
                    predicate([{"is_self": True, "time_text": inside_label}]),
                    "self-message-in-window",
                )
                self.assertEqual(
                    predicate([{"is_self": True, "time_text": outside_label}]),
                    "",
                )

    def test_stages_without_a_delivery_calendar_have_no_predicate(self) -> None:
        self.assertIsNone(
            self_message_in_window_predicate(
                stage="content_found", delivered_on=date(2026, 9, 1)
            )
        )
        self.assertIsNone(
            self_message_in_window_predicate(stage="arrival", delivered_on=None)
        )


if __name__ == "__main__":
    unittest.main()
