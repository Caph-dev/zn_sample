from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.zclaw_cli import CLI_NOT_FOUND  # noqa: E402
from lib.operator_launch import (  # noqa: E402
    BEIJING as LAUNCH_BEIJING,
    INTRO_SCRIPT,
    OPEN_SCRIPT,
    OPERATOR_EXECUTE_LIMIT,
    PLATFORM_WAIT_SECONDS,
    SCREEN_SCRIPT,
    TRACKING_SCRIPT,
    OperatorLaunchError,
    ask_yes_no,
    before_four_pm_beijing,
    build_job_argv,
    choose_report,
    clickable_hint,
    confirm_mode,
    count_export_approved,
    default_out_prefix,
    parse_force,
    parse_mode,
    parse_yes_no,
    parse_ziniao_status,
    pipeline_prefixes,
    precheck,
    precheck_shop_page,
    require_afternoon,
    require_macos_gui,
    require_single_running_store,
    reveal_report,
    run_operator_mode,
    store_label,
    wait_platform_refresh,
    _humanize_bridge_error,
    _humanize_page_error,
)

BEIJING = ZoneInfo("Asia/Shanghai")


class ParseAndArgvTests(unittest.TestCase):
    def test_unknown_mode_is_chinese(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "双击"):
            parse_mode("approve")

    def test_prepare_argv_reopens_without_out_or_store_id(self) -> None:
        out = PROJECT_ROOT / "exports" / "sample_open_test"
        argv = build_job_argv("prepare", python="/usr/bin/python3", out_prefix=out)
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertEqual(Path(argv[1]), OPEN_SCRIPT)
        self.assertIn("--reopen", argv)
        self.assertNotIn("--out", argv)
        self.assertNotIn("--store-id", argv)
        self.assertNotIn("--execute", argv)

    def test_screen_argv_is_dry_run_without_store_id(self) -> None:
        out = PROJECT_ROOT / "exports" / "sample_screen_test"
        argv = build_job_argv("screen", python="/usr/bin/python3", out_prefix=out)
        self.assertEqual(argv[0], "/usr/bin/python3")
        self.assertEqual(Path(argv[1]), SCREEN_SCRIPT)
        self.assertNotIn("\\", argv[1])
        self.assertNotIn("\\", argv[argv.index("--out") + 1])
        self.assertIn("--from-seller-home", argv)
        self.assertIn("--with-detail", argv)
        self.assertIn("--require-detail", argv)
        self.assertEqual(argv[argv.index("--data-source") + 1], "auto")
        self.assertNotIn("--execute", argv)
        self.assertNotIn("--store-id", argv)
        self.assertNotIn("--store-name", argv)

    def test_pipeline_is_not_a_single_argv(self) -> None:
        self.assertEqual(OPERATOR_EXECUTE_LIMIT, 50)
        with self.assertRaisesRegex(OperatorLaunchError, "不是单步"):
            build_job_argv(
                "pipeline",
                python="python3",
                out_prefix=PROJECT_ROOT / "exports" / "x",
            )

    def test_tracking_argv_always_writes_and_sends(self) -> None:
        out = PROJECT_ROOT / "exports" / "sample_shipped_test"
        argv = build_job_argv("tracking", python="python3", out_prefix=out)
        self.assertEqual(Path(argv[1]), TRACKING_SCRIPT)
        self.assertIn("--write-feishu", argv)
        self.assertIn("--send-tracking", argv)
        self.assertIn("--execute", argv)
        self.assertIn("--yes", argv)
        self.assertEqual(argv[argv.index("--execute-limit") + 1], "0")
        self.assertNotIn("--force", argv)

    def test_tracking_argv_can_force_time_gate(self) -> None:
        out = PROJECT_ROOT / "exports" / "sample_shipped_test"
        argv = build_job_argv("tracking", python="python3", out_prefix=out, force=True)
        self.assertIn("--force", argv)
        self.assertIn("--write-feishu", argv)
        self.assertIn("--send-tracking", argv)

    def test_out_prefix_shape(self) -> None:
        prefix = default_out_prefix("screen", now=datetime(2026, 8, 16, 15, 4, 5))
        self.assertEqual(prefix.name, "sample_screen_20260816_150405")
        self.assertEqual(prefix.parent, PROJECT_ROOT / "exports")

    def test_pipeline_prefixes(self) -> None:
        prefixes = pipeline_prefixes(now=datetime(2026, 8, 16, 15, 4, 5))
        self.assertEqual(prefixes["screen"].name, "sample_screen_20260816_150405")
        self.assertEqual(prefixes["approve"].name, "sample_screen_20260816_150405_approved")
        self.assertEqual(prefixes["intro"].name, "sample_intro_20260816_150405")

    def test_count_approved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rows.json"
            path.write_text(
                json.dumps(
                    [
                        {"approve_status": "approved"},
                        {"approve_status": "skipped"},
                        {"eligible": True},
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(count_export_approved(path), 1)
            self.assertEqual(count_export_approved(Path(tmp) / "missing.json"), 0)


class ZiniaoStatusTests(unittest.TestCase):
    def test_parse_gui(self) -> None:
        self.assertEqual(parse_ziniao_status("mode: GUI\npid: 1\n"), "GUI")

    def test_parse_missing(self) -> None:
        self.assertEqual(parse_ziniao_status("pid: 1\n"), "UNKNOWN")

    def test_require_gui_translates_webdriver(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "没有工作台窗口"):
            require_macos_gui("WEBDRIVER")

    def test_require_gui_translates_off(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "紫鸟没打开"):
            require_macos_gui("OFF")

    def test_require_gui_accepts_gui(self) -> None:
        require_macos_gui("GUI")


class RunningStoreTests(unittest.TestCase):
    def test_resolves_cli_before_listing_when_using_real_lister(self) -> None:
        def boom() -> list[str]:
            raise RuntimeError(CLI_NOT_FOUND)

        with self.assertRaisesRegex(OperatorLaunchError, "还没配完"):
            require_single_running_store(resolve_cli_fn=boom)

    def test_windows_missing_cli_is_humanized(self) -> None:
        text = _humanize_bridge_error(
            FileNotFoundError("[WinError 2] The system cannot find the file specified: 'ziniao-cli'")
        )
        self.assertIn("还没配完", text)

    def test_zero_running(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "还没有打开的店"):
            require_single_running_store(lambda: [])

    def test_multiple_running_uses_names_not_ids(self) -> None:
        with self.assertRaises(OperatorLaunchError) as ctx:
            require_single_running_store(
                lambda: [
                    {"storeId": "111", "storeName": "甲店"},
                    {"storeId": "222", "storeName": "乙店"},
                ]
            )
        message = str(ctx.exception)
        self.assertIn("多家店", message)
        self.assertIn("甲店", message)
        self.assertIn("乙店", message)
        self.assertNotIn("111", message)
        self.assertNotIn("222", message)

    def test_single_running(self) -> None:
        store = require_single_running_store(
            lambda: [{"storeId": "sid-1", "storeName": "一号店"}]
        )
        self.assertEqual(store["storeId"], "sid-1")
        self.assertEqual(store_label(store), "一号店")

    def test_skips_rows_without_store_id(self) -> None:
        store = require_single_running_store(
            lambda: [{"storeName": "空"}, {"storeId": "sid-1", "storeName": "一号店"}]
        )
        self.assertEqual(store["storeId"], "sid-1")


class PagePrecheckTests(unittest.TestCase):
    def test_blank_page(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "空白页"):
            precheck_shop_page(
                "sid-1",
                execute_script_fn=lambda *a, **k: {
                    "href": "about:blank",
                    "page_type": "unknown",
                },
            )

    def test_login_page(self) -> None:
        with self.assertRaisesRegex(OperatorLaunchError, "登录页"):
            precheck_shop_page(
                "sid-1",
                execute_script_fn=lambda *a, **k: {
                    "href": "https://seller-us.tiktok.com/account/login",
                    "page_type": "login",
                },
            )

    def test_seller_center_ok(self) -> None:
        page_type = precheck_shop_page(
            "sid-1",
            execute_script_fn=lambda *a, **k: {
                "href": "https://seller.us.tiktokshopglobalselling.com/homepage",
                "page_type": "seller-center",
            },
        )
        self.assertEqual(page_type, "seller-center")

    def test_seller_order_and_product_pages_ok(self) -> None:
        for href in (
            "https://seller.us.tiktokshopglobalselling.com/order?tab=all",
            "https://seller.us.tiktokshopglobalselling.com/product/list",
        ):
            page_type = precheck_shop_page(
                "sid-1",
                execute_script_fn=lambda *a, href=href, **k: {
                    "href": href,
                    "page_type": "seller-center",
                },
            )
            self.assertEqual(page_type, "seller-center")

    def test_page_errors_do_not_require_homepage(self) -> None:
        self.assertNotIn("首页", _humanize_page_error("店铺页面仍是 about:blank；请先登录商家中心"))
        self.assertNotIn("首页", _humanize_page_error("检测到 TikTok Shop 登录页；请先完成登录"))
        self.assertNotIn("首页", _humanize_page_error("当前页无法确认是已登录的 TikTok Shop"))
        self.assertIn("登录", _humanize_page_error("检测到 TikTok Shop 登录页；请先完成登录"))


class PrecheckWiringTests(unittest.TestCase):
    def test_macos_requires_gui_before_listing_stores(self) -> None:
        listed = Mock(side_effect=AssertionError)
        with self.assertRaisesRegex(OperatorLaunchError, "没有工作台窗口"):
            precheck(
                platform="darwin",
                status_fn=lambda: "WEBDRIVER",
                list_running_stores_fn=listed,
                inspect_fn=Mock(side_effect=AssertionError),
            )
        listed.assert_not_called()

    def test_prepare_allows_zero_running_and_skips_page_inspect(self) -> None:
        inspect = Mock(side_effect=AssertionError)
        result = precheck(
            platform="win32",
            status_fn=Mock(side_effect=AssertionError),
            list_running_stores_fn=lambda: [],
            inspect_fn=inspect,
            require_running_store=False,
            inspect_page=False,
        )
        self.assertEqual(result["store"], {})
        self.assertEqual(result["page_type"], "")
        inspect.assert_not_called()

    def test_prepare_refuses_multiple_running_without_inspect(self) -> None:
        inspect = Mock(side_effect=AssertionError)
        with self.assertRaisesRegex(OperatorLaunchError, "多家店"):
            precheck(
                platform="win32",
                list_running_stores_fn=lambda: [
                    {"storeId": "1", "storeName": "甲店"},
                    {"storeId": "2", "storeName": "乙店"},
                ],
                inspect_fn=inspect,
                require_running_store=False,
                inspect_page=False,
            )
        inspect.assert_not_called()

    def test_windows_skips_process_mode(self) -> None:
        result = precheck(
            platform="win32",
            status_fn=Mock(side_effect=AssertionError),
            list_running_stores_fn=lambda: [
                {"storeId": "sid-1", "storeName": "一号店"}
            ],
            inspect_fn=lambda sid: "seller-center",
        )
        self.assertEqual(result["page_type"], "seller-center")
        self.assertEqual(result["store"]["storeId"], "sid-1")


class GateTests(unittest.TestCase):
    def test_beijing_offset_is_plus_eight(self) -> None:
        offset = LAUNCH_BEIJING.utcoffset(datetime(2026, 8, 16, 15, 0))
        self.assertEqual(offset.total_seconds(), 8 * 3600)

    def test_afternoon_gate(self) -> None:
        self.assertTrue(
            before_four_pm_beijing(datetime(2026, 8, 16, 15, 59, tzinfo=BEIJING))
        )
        self.assertFalse(
            before_four_pm_beijing(datetime(2026, 8, 16, 16, 0, tzinfo=BEIJING))
        )
        with self.assertRaisesRegex(OperatorLaunchError, "16:00"):
            require_afternoon(now=datetime(2026, 8, 16, 10, 0, tzinfo=BEIJING))
        require_afternoon(now=datetime(2026, 8, 16, 16, 1, tzinfo=BEIJING))

    def test_wait_is_hardcoded_ten_minutes(self) -> None:
        self.assertEqual(PLATFORM_WAIT_SECONDS, 600)

    def test_wait_ticks_without_real_sleep(self) -> None:
        clock = {"t": 0.0}
        lines: list[str] = []

        def now() -> float:
            return clock["t"]

        def sleep(seconds: float) -> None:
            clock["t"] += seconds

        wait_platform_refresh(
            seconds=3,
            sleep_fn=sleep,
            progress_fn=lines.append,
            clock_fn=now,
        )
        self.assertIn("00:00 / 00:03", lines[0])
        self.assertEqual(lines[-1], "")
        self.assertTrue(any("00:03 / 00:03" in line for line in lines))


class ReportAndConfirmTests(unittest.TestCase):
    def test_windows_reveal_selects_in_explorer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample_screen_x.csv"
            path.write_text("a", encoding="utf-8")
            with patch("lib.operator_launch.sys.platform", "win32"):
                with patch("lib.operator_launch.subprocess.run") as run:
                    reveal_report(path)
            argv = run.call_args[0][0]
            self.assertEqual(argv[0], "explorer")
            self.assertEqual(argv[1], "/select,")
            self.assertEqual(Path(argv[2]), path.resolve())

    def test_choose_report_prefers_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "sample_screen_x"
            prefix.with_suffix(".csv").write_text("a", encoding="utf-8")
            prefix.with_suffix(".xlsx").write_bytes(b"x")
            chosen = choose_report(prefix)
            assert chosen is not None
            self.assertEqual(chosen.suffix, ".csv")

    def test_choose_report_falls_back_to_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "sample_intro_x"
            prefix.with_suffix(".csv").write_text("a", encoding="utf-8")
            chosen = choose_report(prefix)
            assert chosen is not None
            self.assertEqual(chosen.suffix, ".csv")

    def test_force_parser_is_exact(self) -> None:
        self.assertTrue(parse_force("FORCE"))
        self.assertTrue(parse_force(" FORCE "))
        self.assertFalse(parse_force("force"))
        self.assertFalse(parse_force("Force"))
        self.assertFalse(parse_force("y"))
        self.assertFalse(parse_force(""))

    def test_yes_no_parser(self) -> None:
        self.assertTrue(parse_yes_no("y"))
        self.assertTrue(parse_yes_no("Y"))
        self.assertTrue(parse_yes_no(" yes "))
        self.assertFalse(parse_yes_no("n"))
        self.assertFalse(parse_yes_no(""))
        self.assertFalse(parse_yes_no("确定"))
        self.assertFalse(
            ask_yes_no(
                "筛查批准写飞书发私信",
                "继续？",
                no_means="退出",
                ask=lambda kind, title, message: "n",
            )
        )

    def test_pipeline_uses_yesno(self) -> None:
        pipeline = parse_mode("pipeline")
        self.assertEqual(pipeline.confirm, "yesno")
        self.assertTrue(confirm_mode(pipeline, ask=lambda kind, title, message: "y"))
        self.assertFalse(confirm_mode(pipeline, ask=lambda kind, title, message: "n"))
        self.assertIn("不必停在首页", pipeline.confirm_message)
        self.assertIn("已登录商家中心", pipeline.confirm_message)

    def test_screen_skips_confirm(self) -> None:
        self.assertTrue(
            confirm_mode(
                parse_mode("screen"),
                ask=Mock(side_effect=AssertionError),
            )
        )


class RunOperatorModeTests(unittest.TestCase):
    def _ok_precheck(self) -> dict:
        return {
            "store": {"storeId": "sid-1", "storeName": "一号店"},
            "page_type": "seller-center",
        }

    def test_prepare_runs_without_report(self) -> None:
        opened: list[Path] = []
        ran: list[list[str]] = []
        printed: list[str] = []

        code = run_operator_mode(
            "prepare",
            python="python3",
            precheck_fn=lambda: {"store": {}, "page_type": ""},
            confirm_fn=Mock(side_effect=AssertionError),
            run_job_fn=lambda argv: ran.append(list(argv)) or 0,
            open_report_fn=opened.append,
            printer=printed.append,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(ran), 1)
        self.assertEqual(Path(ran[0][1]), OPEN_SCRIPT)
        self.assertIn("--reopen", ran[0])
        self.assertNotIn("--out", ran[0])
        self.assertEqual(opened, [])
        self.assertTrue(any("没有打开的店" in line and "2 号店" in line for line in printed))
        self.assertTrue(any("1-只出名单" in line for line in printed))

    def test_screen_runs_and_opens_report(self) -> None:
        opened: list[Path] = []
        ran: list[list[str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            prefix = Path(tmp) / "sample_screen_20260816_120000"

            def run_job(argv):
                ran.append(list(argv))
                out = Path(argv[argv.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".csv").write_text("ok", encoding="utf-8")
                return 0

            code = run_operator_mode(
                "screen",
                python="python3",
                out_prefix=prefix,
                precheck_fn=self._ok_precheck,
                confirm_fn=Mock(side_effect=AssertionError),
                run_job_fn=run_job,
                open_report_fn=opened.append,
                printer=lambda _line: None,
                alert_fn=lambda _t, _m: None,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(ran), 1)
        self.assertNotIn("--execute", ran[0])
        self.assertEqual(opened[0].name, "sample_screen_20260816_120000.csv")

    def test_pipeline_cancel_does_not_run(self) -> None:
        run_job = Mock(side_effect=AssertionError)
        printed: list[str] = []
        code = run_operator_mode(
            "pipeline",
            precheck_fn=self._ok_precheck,
            confirm_fn=lambda _mode: False,
            run_job_fn=run_job,
            printer=printed.append,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 0)
        run_job.assert_not_called()
        joined = "\n".join(printed)
        self.assertIn("没有输入 y", joined)
        self.assertIn("没有批准", joined)

    def test_pipeline_runs_four_steps_and_waits(self) -> None:
        waited = {"n": 0}
        ran: list[list[str]] = []
        opened: list[Path] = []

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "pipe"

            def run_job(argv):
                ran.append(list(argv))
                out = Path(argv[argv.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".csv").write_text("ok", encoding="utf-8")
                if "--execute" in argv and "--confirm-export" not in argv and Path(argv[1]) == SCREEN_SCRIPT:
                    out.with_suffix(".json").write_text(
                        json.dumps([{"approve_status": "approved", "eligible": True}]),
                        encoding="utf-8",
                    )
                elif "--with-detail" in argv:
                    out.with_suffix(".json").write_text("[]", encoding="utf-8")
                    out.with_suffix(".xlsx").write_bytes(b"x")
                else:
                    out.with_suffix(".json").write_text("[]", encoding="utf-8")
                return 0

            code = run_operator_mode(
                "pipeline",
                python="python3",
                out_prefix=base,
                precheck_fn=self._ok_precheck,
                confirm_fn=lambda _mode: True,
                run_job_fn=run_job,
                wait_fn=lambda: waited.__setitem__("n", waited["n"] + 1),
                open_report_fn=opened.append,
                printer=lambda _line: None,
                alert_fn=lambda _t, _m: None,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(ran), 4)
        self.assertEqual(waited["n"], 1)
        self.assertIn("--with-detail", ran[0])
        self.assertNotIn("--execute", ran[0])
        self.assertIn("--execute", ran[1])
        self.assertEqual(ran[1][ran[1].index("--execute-limit") + 1], str(OPERATOR_EXECUTE_LIMIT))
        self.assertIn("--confirm-export", ran[2])
        self.assertNotIn("--execute", ran[2])
        self.assertEqual(Path(ran[3][1]), INTRO_SCRIPT)
        self.assertIn("--execute", ran[3])
        self.assertIn("--write-feishu", ran[3])
        self.assertEqual(opened, [base.parent / f"{base.name}_confirm.csv"])

    def test_pipeline_skips_wait_when_nothing_approved(self) -> None:
        waited = Mock(side_effect=AssertionError)
        ran: list[list[str]] = []

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "pipe"

            def run_job(argv):
                ran.append(list(argv))
                out = Path(argv[argv.index("--out") + 1])
                out.parent.mkdir(parents=True, exist_ok=True)
                out.with_suffix(".json").write_text(
                    json.dumps([{"approve_status": "skipped"}]),
                    encoding="utf-8",
                )
                out.with_suffix(".xlsx").write_bytes(b"x")
                return 0

            code = run_operator_mode(
                "pipeline",
                python="python3",
                out_prefix=base,
                precheck_fn=self._ok_precheck,
                confirm_fn=lambda _mode: True,
                run_job_fn=run_job,
                wait_fn=waited,
                open_report_fn=lambda _path: None,
                printer=lambda _line: None,
                alert_fn=lambda _t, _m: None,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(ran), 2)
        waited.assert_not_called()

    def test_unknown_mode_returns_2_without_job(self) -> None:
        run_job = Mock(side_effect=AssertionError)
        code = run_operator_mode(
            "execute",
            run_job_fn=run_job,
            printer=lambda _line: None,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 2)
        run_job.assert_not_called()

    def test_precheck_failure_skips_job(self) -> None:
        alerts: list[tuple[str, str]] = []
        run_job = Mock(side_effect=AssertionError)
        code = run_operator_mode(
            "screen",
            precheck_fn=Mock(side_effect=OperatorLaunchError("关掉其它店，只留一家。")),
            run_job_fn=run_job,
            printer=lambda _line: None,
            alert_fn=lambda title, message: alerts.append((title, message)),
        )
        self.assertEqual(code, 2)
        run_job.assert_not_called()
        self.assertEqual(alerts[0][1], "关掉其它店，只留一家。")

    def test_tracking_before_four_enter_exits(self) -> None:
        run_job = Mock(side_effect=AssertionError)
        printed: list[str] = []
        code = run_operator_mode(
            "tracking",
            now=datetime(2026, 8, 16, 15, 0, tzinfo=BEIJING),
            precheck_fn=self._ok_precheck,
            force_fn=lambda: False,
            confirm_fn=Mock(side_effect=AssertionError),
            run_job_fn=run_job,
            printer=printed.append,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 0)
        run_job.assert_not_called()
        joined = "\n".join(printed)
        self.assertIn("没有输入 FORCE", joined)
        self.assertIn("15:00", joined)

    def test_tracking_before_four_force_adds_flag(self) -> None:
        ran: list[list[str]] = []
        code = run_operator_mode(
            "tracking",
            python="python3",
            now=datetime(2026, 8, 16, 15, 0, tzinfo=BEIJING),
            precheck_fn=self._ok_precheck,
            force_fn=lambda: True,
            confirm_fn=lambda _mode: True,
            run_job_fn=lambda argv: ran.append(list(argv)) or 0,
            open_report_fn=lambda _path: None,
            printer=lambda _line: None,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 0)
        self.assertIn("--force", ran[0])
        self.assertIn("--write-feishu", ran[0])
        self.assertIn("--send-tracking", ran[0])

    def test_tracking_confirm_then_write_and_send(self) -> None:
        ran: list[list[str]] = []
        code = run_operator_mode(
            "tracking",
            python="python3",
            now=datetime(2026, 8, 16, 16, 5, tzinfo=BEIJING),
            precheck_fn=self._ok_precheck,
            confirm_fn=lambda mode: mode.confirm == "yesno",
            run_job_fn=lambda argv: ran.append(list(argv)) or 0,
            open_report_fn=lambda _path: None,
            printer=lambda _line: None,
            alert_fn=lambda _t, _m: None,
        )
        self.assertEqual(code, 0)
        self.assertIn("--write-feishu", ran[0])
        self.assertIn("--send-tracking", ran[0])
        self.assertIn("--execute", ran[0])
        self.assertIn("--yes", ran[0])
        self.assertNotIn("--force", ran[0])


class StubFileTests(unittest.TestCase):
    def test_clickable_hint_lists_all(self) -> None:
        text = clickable_hint()
        self.assertIn("0-打开店铺", text)
        self.assertIn("1-只出名单", text)
        self.assertIn("2-筛查批准写飞书发私信", text)
        self.assertIn("3-获取物流信息写飞书发单号", text)

    def test_macos_stubs_call_matching_modes(self) -> None:
        mapping = {
            "0-打开店铺.command": "prepare",
            "1-只出名单.command": "screen",
            "2-筛查批准写飞书发私信.command": "pipeline",
            "3-获取物流信息写飞书发单号.command": "tracking",
        }
        for name, mode in mapping.items():
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("run_launcher.sh", text)
            self.assertIn(mode, text)
            for other in set(mapping.values()) - {mode}:
                self.assertNotIn(f" {other}", text)

    def test_windows_stubs_match_scan_bat_skeleton(self) -> None:
        mapping = {
            "0-打开店铺.bat": "prepare",
            "1-只出名单.bat": "screen",
            "2-筛查批准写飞书发私信.bat": "pipeline",
            "3-获取物流信息写飞书发单号.bat": "tracking",
        }
        for name, mode in mapping.items():
            text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("set \"ROOT=%~dp0\"", text)
            self.assertIn("cd /d \"%ROOT%\"", text)
            self.assertIn("run_launcher.bat", text)
            self.assertIn(mode, text)

    def test_windows_stubs_pair_with_macos_commands(self) -> None:
        pairs = [
            ("0-打开店铺.command", "0-打开店铺.bat", "prepare"),
            ("1-只出名单.command", "1-只出名单.bat", "screen"),
            ("2-筛查批准写飞书发私信.command", "2-筛查批准写飞书发私信.bat", "pipeline"),
            ("3-获取物流信息写飞书发单号.command", "3-获取物流信息写飞书发单号.bat", "tracking"),
        ]
        for command_name, bat_name, mode in pairs:
            command = (PROJECT_ROOT / command_name).read_text(encoding="utf-8")
            bat = (PROJECT_ROOT / bat_name).read_text(encoding="utf-8")
            self.assertIn(f'run_launcher.sh" {mode}', command)
            self.assertIn(f'run_launcher.bat" {mode}', bat)
            comment = next(
                line[1:].strip()
                for line in command.splitlines()
                if line.startswith("#") and not line.startswith("#!")
            )
            rem = next(
                line.lstrip()[4:].strip()
                if line.lstrip().upper().startswith("REM ")
                else line.lstrip()[3:].strip()
                for line in bat.splitlines()
                if line.lstrip().upper().startswith("REM")
            )
            self.assertEqual(comment, rem, f"{command_name} vs {bat_name}")
            for line in bat.splitlines():
                if line.lstrip().upper().startswith("REM"):
                    continue
                self.assertTrue(
                    line.isascii(),
                    f"{bat_name} 可执行行必须是 ASCII，避免无 BOM 时被 GBK 解析坏：{line!r}",
                )

    def test_gitattributes_forces_bat_crlf(self) -> None:
        text = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.bat text eol=crlf", text)

    def test_windows_bats_use_crlf(self) -> None:
        bats = [
            PROJECT_ROOT / "0-打开店铺.bat",
            PROJECT_ROOT / "1-只出名单.bat",
            PROJECT_ROOT / "2-筛查批准写飞书发私信.bat",
            PROJECT_ROOT / "3-获取物流信息写飞书发单号.bat",
            PROJECT_ROOT / "scripts" / "run_launcher.bat",
        ]
        for path in bats:
            data = path.read_bytes()
            crlf = data.count(b"\r\n")
            lf_only = data.count(b"\n") - crlf
            self.assertGreater(crlf, 0, path.name)
            self.assertEqual(lf_only, 0, path.name)

    def test_console_cmd_launches_assistant_with_windows_paths(self) -> None:
        data = (PROJECT_ROOT / "启动操作台.cmd").read_bytes()
        self.assertTrue(
            data.startswith(b"\xef\xbb\xbf"),
            "启动操作台.cmd 须带 UTF-8 BOM，避免中文 echo 被 GBK 解析坏",
        )
        crlf = data.count(b"\r\n")
        self.assertGreater(crlf, 0)
        self.assertEqual(data.count(b"\n") - crlf, 0, "cmd.exe 读 .cmd 要 CRLF")

        text = data.decode("utf-8-sig")
        self.assertIn('cd /d "%~dp0"', text)
        self.assertIn('%~dp0.venv\\Scripts\\python.exe', text)
        self.assertIn('%~dp0scripts\\launch_assistant.py', text)
        self.assertIn("chcp 65001", text)
        self.assertIn("PYTHONUTF8=1", text)
        self.assertIn("PYTHONIOENCODING=utf-8", text)
        self.assertNotRegex(text, r"(?i)\bgoto\b")
        self.assertNotIn("PATH=", text)

        attributes = (PROJECT_ROOT / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.cmd text eol=crlf", attributes)

    def test_run_launcher_bat_has_no_goto_or_labels(self) -> None:
        launcher = PROJECT_ROOT / "scripts" / "run_launcher.bat"
        text = launcher.read_text(encoding="utf-8-sig")
        self.assertNotRegex(text, r"(?i)\bgoto\b")
        for line in text.splitlines():
            stripped = line.lstrip()
            self.assertFalse(stripped.startswith(":"), stripped)
        self.assertIn("py -3", text)
        self.assertIn('%~dp0launch_sample.py', text)
        self.assertIn("pipeline", text)
        self.assertIn("prepare", text)
        self.assertNotIn("approve", text)
        self.assertIn("chcp 65001", text)
        self.assertIn("PYTHONUTF8=1", text)
        self.assertIn("PYTHONIOENCODING=utf-8", text)
        self.assertIn("Python 版本太旧", text)
        self.assertIn("代号 %CODE%", text)
        self.assertNotIn("PATH=", text)
        self.assertNotIn("%APPDATA%", text)
        self.assertTrue(
            (PROJECT_ROOT / "scripts" / "run_launcher.bat").read_bytes().startswith(
                b"\xef\xbb\xbf"
            )
        )


if __name__ == "__main__":
    unittest.main()
