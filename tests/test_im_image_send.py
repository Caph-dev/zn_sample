from __future__ import annotations

import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib import im_api  # noqa: E402
from lib import im_dom  # noqa: E402


class ImThreadTextTests(unittest.TestCase):
    def test_missing_probe_returns_empty(self) -> None:
        self.assertEqual(im_dom.im_thread_text_with_tail(None), "")
        self.assertEqual(im_dom.im_thread_text_with_tail({}), "")

    def test_short_thread_returns_head_once(self) -> None:
        body = "Hi creator! Just checking in."
        self.assertEqual(
            im_dom.im_thread_text_with_tail({"thread_text": body, "thread_text_tail": body}),
            body,
        )

    def test_long_thread_keeps_head_and_tail(self) -> None:
        head = "a" * 4000
        tail = "b" * 4000
        combined = im_dom.im_thread_text_with_tail(
            {"thread_text": head, "thread_text_tail": tail}
        )
        self.assertTrue(combined.startswith(head))
        self.assertTrue(combined.endswith(tail))
        self.assertIn("\n", combined)

    def test_overlapping_tail_is_not_duplicated(self) -> None:
        body = "x" * 3000
        self.assertEqual(
            im_dom.im_thread_text_with_tail({"thread_text": body, "thread_text_tail": body[-1000:]}),
            body,
        )


class ImageChunkTests(unittest.TestCase):
    def test_chunks_round_trip_to_original_bytes(self) -> None:
        payload = bytes(range(256)) * 40
        chunks = im_api.image_bytes_to_chunks(payload, chunk_chars=1000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))
        self.assertEqual(base64.b64decode("".join(chunks)), payload)

    def test_chunk_size_has_a_floor(self) -> None:
        chunks = im_api.image_bytes_to_chunks(b"x" * 4000, chunk_chars=1)
        self.assertTrue(all(len(chunk) <= 1000 for chunk in chunks))


class MessageFingerprintTests(unittest.TestCase):
    def test_fingerprint_drops_greeting_and_creator_name(self) -> None:
        fingerprint = im_api.message_text_fingerprint(
            "Hi alice! ❤️ Just a friendly reminder that our product is on promotion."
        )
        self.assertTrue(fingerprint.startswith("just a friendly reminder"))
        self.assertNotIn("alice", fingerprint)

    def test_fingerprint_handles_spanish_greeting_without_space(self) -> None:
        fingerprint = im_api.message_text_fingerprint(
            "Holaana! ❤️ Solo quería recordarte que el producto tiene promoción."
        )
        self.assertTrue(fingerprint.startswith("solo quería recordarte"))

    def test_predicate_matches_the_rendered_message_in_thread(self) -> None:
        body = "Hi bob! ❤️ I just wanted to check in and see if you've received the product yet."
        predicate = im_api.thread_contains_message_predicate(body)
        self.assertTrue(predicate("older message\n" + body + "\nnewer message"))
        self.assertFalse(predicate("Hi bob! ❤️ a totally different message"))
        self.assertFalse(im_api.thread_contains_message_predicate("")(body))


class ThreadThanksHoldTests(unittest.TestCase):
    def test_sop_thanks_copy_holds_the_thread(self) -> None:
        thread_text = (
            "Hi creator! We just saw the video you created for us and really appreciate it."
        )
        self.assertEqual(
            im_api.thread_thanks_hold_reason(thread_text),
            "content-thanks-found",
        )

    def test_uncertain_colleague_thanks_holds_the_thread(self) -> None:
        self.assertEqual(
            im_api.thread_thanks_hold_reason(
                "Thank you for your support on this video campaign."
            ),
            "uncertain-thanks-found",
        )

    def test_plain_thread_does_not_hold(self) -> None:
        self.assertEqual(
            im_api.thread_thanks_hold_reason("Hi! Any updates on the product?"),
            "",
        )


class _FakeZclaw:
    def __init__(self, results: list[dict]) -> None:
        self.results = list(results)
        self.scripts: list[str] = []

    def __call__(self, store_id: str, script: str, **kwargs) -> dict:
        self.scripts.append(script)
        if not self.results:
            raise AssertionError("unexpected zclaw call")
        return self.results.pop(0)


class SendImageSdkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.image_path = Path(self.temporary_directory.name) / "b05.png"
        self.image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 3000)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def chunk_count(self) -> int:
        return len(im_api.image_bytes_to_chunks(self.image_path.read_bytes()))

    def test_dry_run_only_probes_the_provider(self) -> None:
        fake = _FakeZclaw([{"ok": True, "has_send_image": True}])
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk("store", "", self.image_path)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry-run")
        self.assertEqual(result["file_bytes"], self.image_path.stat().st_size)
        self.assertEqual(len(fake.scripts), 1)
        self.assertIn("findImSdkProvider", fake.scripts[0])

    def test_missing_file_returns_error(self) -> None:
        result = im_api.send_image_message_via_sdk(
            "store", "conversation-1", self.image_path.parent / "missing.png"
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "image-not-found")

    def test_oversized_image_is_rejected_before_any_call(self) -> None:
        oversized = Path(self.temporary_directory.name) / "big.png"
        oversized.write_bytes(b"0" * (im_api.MAX_IMAGE_BYTES + 1))
        fake = _FakeZclaw([])
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store", "conversation-1", oversized, execute=True
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "image-too-large")
        self.assertEqual(fake.scripts, [])

    def test_execute_requires_conversation_id(self) -> None:
        fake = _FakeZclaw([])
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store", "", self.image_path, execute=True
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing-conversation-id")
        self.assertEqual(fake.scripts, [])

    def test_assemble_builds_file_without_sending(self) -> None:
        fake = _FakeZclaw(
            [{"ok": True} for _ in range(self.chunk_count())]
            + [{"ok": True, "state": "dry-run-file-assembled"}]
        )
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store",
                "conversation-1",
                self.image_path,
                assemble_file=True,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "dry-run-file-assembled")
        final_script = fake.scripts[-1]
        self.assertIn("new File(", final_script)
        self.assertIn("sendImageMessageWithFiles", final_script)
        self.assertTrue(final_script.rstrip().endswith("false)"))

    def test_execute_polls_state_and_reports_confirmation(self) -> None:
        fake = _FakeZclaw(
            [{"ok": True} for _ in range(self.chunk_count())]
            + [
                {"ok": True, "state": "sdk-image-send-started", "return_type": "object"},
                {"ok": True, "state": {"started": True, "resolved": True}},
            ]
        )
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store",
                "conversation-1",
                self.image_path,
                execute=True,
                poll_attempts=2,
                poll_interval_sec=0.0,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["send_postcheck"], "confirmed")
        self.assertIn("upload_state", result)
        self.assertTrue(fake.scripts[-2].rstrip().endswith("true)"))

    def test_execute_reports_upload_failure(self) -> None:
        fake = _FakeZclaw(
            [{"ok": True} for _ in range(self.chunk_count())]
            + [
                {"ok": True, "state": "sdk-image-send-started"},
                {
                    "ok": True,
                    "state": {
                        "started": True,
                        "resolved": True,
                        "error": "upload rejected",
                    },
                },
            ]
        )
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store",
                "conversation-1",
                self.image_path,
                execute=True,
                poll_attempts=2,
                poll_interval_sec=0.0,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "image-send-failed")
        self.assertIn("upload rejected", result["error"])

    def test_chunk_push_failure_stops_before_send(self) -> None:
        fake = _FakeZclaw([{"ok": False, "reason": "bridge-down"}])
        with patch.object(im_api, "zclaw_exec", fake):
            result = im_api.send_image_message_via_sdk(
                "store",
                "conversation-1",
                self.image_path,
                execute=True,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "image-chunk-push-failed")
        self.assertEqual(len(fake.scripts), 1)


class SendDirectMessageImageHookTests(unittest.TestCase):
    body = "Hi carol! ❤️ Just a friendly reminder about the promotion."

    def test_confirmed_text_sends_image_with_conversation_id(self) -> None:
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch(
                "lib.im_dom.inspect_current_thread",
                side_effect=[
                    {"thread_text": ""},
                    {"thread_text": "x" * 5000, "thread_text_tail": self.body},
                ],
            ),
            patch("lib.im_dom.composer_identity_matches", return_value=True),
            patch("lib.im_api.send_message_via_sdk", return_value={"ok": True}),
            patch(
                "lib.im_api.send_image_message_via_sdk",
                return_value={"ok": True, "status": "sent"},
            ) as send_image,
            patch("lib.im_api.time.sleep"),
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_predicate=im_api.thread_contains_message_predicate(self.body),
                image_path=Path("/tmp/does-not-need-to-exist-b05.png"),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "sent")
        send_image.assert_called_once()
        _, call_kwargs = send_image.call_args
        self.assertTrue(call_kwargs["execute"])
        self.assertEqual(send_image.call_args.args[1], "conv-1")

    def test_already_sent_message_skips_image_send(self) -> None:
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch("lib.im_dom.inspect_current_thread", return_value={"thread_text": self.body}),
            patch("lib.im_api.send_message_via_sdk") as send_text,
            patch("lib.im_api.send_image_message_via_sdk") as send_image,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_predicate=im_api.thread_contains_message_predicate(self.body),
                image_path=Path("/tmp/does-not-need-to-exist-b05.png"),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already-sent")
        self.assertTrue(result["image_planned"])
        self.assertEqual(result["image_skipped"], "already-sent")
        send_text.assert_not_called()
        send_image.assert_not_called()

    def test_window_predicate_short_circuits_before_sending(self) -> None:
        messages = [
            {"is_self": True, "time_text": "Sep 9 7:00 AM", "text": "another wording"},
            {"is_self": False, "time_text": "Sep 9 8:00 AM", "text": "thanks!"},
        ]
        seen: list[list[dict]] = []

        def window_predicate(items: list[dict]) -> str:
            seen.append(items)
            return "self-message-in-window" if items else ""

        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch(
                "lib.im_dom.inspect_current_thread",
                return_value={
                    "thread_text": "unrelated",
                    "messages": messages,
                    "page_offset_minutes": 420,
                },
            ),
            patch("lib.im_api.send_message_via_sdk") as send_text,
            patch("lib.im_api.send_image_message_via_sdk") as send_image,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_message_predicate=window_predicate,
                image_path=Path("/tmp/does-not-need-to-exist-b05.png"),
            )

        # 逐条消息带上探针里的页面时区偏移，窗口判定才能换算成北京日期。
        self.assertEqual(
            seen,
            [
                [
                    {**messages[0], "page_offset_minutes": 420},
                    {**messages[1], "page_offset_minutes": 420},
                ]
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "already-sent")
        self.assertEqual(result["reason"], "self-message-in-window")
        self.assertEqual(result["image_skipped"], "already-sent")
        send_text.assert_not_called()
        send_image.assert_not_called()

    def test_template_hold_wins_over_the_window_predicate(self) -> None:
        window_predicate_calls: list[list[dict]] = []
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch(
                "lib.im_dom.inspect_current_thread",
                return_value={
                    "thread_text": (
                        "Hi carol! We just saw the video you created for us and "
                        "really appreciate it."
                    ),
                    "messages": [
                        {"is_self": True, "time_text": "Sep 9 7:00 AM", "text": ""}
                    ],
                },
            ),
            patch("lib.im_api.send_message_via_sdk") as send_text,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_message_predicate=lambda items: (
                    window_predicate_calls.append(items) or "self-message-in-window"
                ),
                hold_predicate=im_api.thread_thanks_hold_reason,
            )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["reason"], "content-thanks-found")
        self.assertEqual(window_predicate_calls, [])
        send_text.assert_not_called()

    def test_tail_only_thread_still_detects_the_sent_message(self) -> None:
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch(
                "lib.im_dom.inspect_current_thread",
                return_value={
                    "thread_text": "z" * 5000,
                    "thread_text_tail": self.body,
                },
            ),
            patch("lib.im_api.send_message_via_sdk") as send_text,
            patch("lib.im_api.send_image_message_via_sdk") as send_image,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_predicate=im_api.thread_contains_message_predicate(self.body),
            )
        self.assertEqual(result["status"], "already-sent")
        send_text.assert_not_called()
        send_image.assert_not_called()

    def test_thread_with_thanks_copy_holds_the_send(self) -> None:
        thanks_text = (
            "Hi carol! We just saw the video you created for us and really appreciate it."
        )
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={
                    "ok": True,
                    "click": {"conversation_id": "conv-1", "result_creator_id": "cid-1"},
                },
            ),
            patch(
                "lib.im_dom.inspect_current_thread",
                return_value={"thread_text": thanks_text},
            ),
            patch("lib.im_api.send_message_via_sdk") as send_text,
            patch("lib.im_api.send_image_message_via_sdk") as send_image,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                creator_id="cid-1",
                execute=True,
                already_sent_predicate=im_api.thread_contains_message_predicate(self.body),
                hold_predicate=im_api.thread_thanks_hold_reason,
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "held")
        self.assertEqual(result["reason"], "content-thanks-found")
        send_text.assert_not_called()
        send_image.assert_not_called()

    def test_dry_run_never_sends_text_or_image(self) -> None:
        with (
            patch(
                "lib.im_dom.open_conversation_via_new_message",
                return_value={"ok": True, "click": {}},
            ),
            patch("lib.im_dom.inspect_current_thread", return_value={"thread_text": ""}),
            patch("lib.im_api.send_message_via_sdk") as send_text,
            patch("lib.im_api.send_image_message_via_sdk") as send_image,
        ):
            result = im_api.send_direct_message(
                "store",
                self.body,
                creator_name="carol",
                execute=False,
                image_path=Path("/tmp/does-not-need-to-exist-b05.png"),
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "dry-run")
        self.assertTrue(result["image_planned"])
        send_text.assert_not_called()
        send_image.assert_not_called()


if __name__ == "__main__":
    unittest.main()
