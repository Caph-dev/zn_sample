from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.run_summary import (  # noqa: E402
    display_path,
    format_ascii_box,
    format_confirm_aborted,
    format_confirm_prompt,
    format_force_afternoon_aborted,
    format_force_afternoon_prompt,
    format_job_summary,
    format_platform_wait_banner,
    format_start_banner,
    format_wait_progress,
    pad_visual,
    visual_width,
)


class VisualWidthTests(unittest.TestCase):
    def test_ascii_and_cjk(self) -> None:
        self.assertEqual(visual_width("CSV"), 3)
        self.assertEqual(visual_width("可清理"), 6)
        self.assertEqual(visual_width("A可"), 3)

    def test_pad_reaches_width(self) -> None:
        self.assertEqual(visual_width(pad_visual("可清理", 10)), 10)


class AsciiBoxTests(unittest.TestCase):
    def test_all_lines_same_length(self) -> None:
        text = format_ascii_box(["短", "this is a longer line"])
        lines = text.splitlines()
        widths = {visual_width(line) for line in lines}
        self.assertEqual(len(widths), 1)
        self.assertTrue(lines[0].startswith("+"))
        self.assertTrue(lines[1].startswith("|"))
        self.assertTrue(lines[-1].startswith("+"))


class StartBannerTests(unittest.TestCase):
    def test_each_mode_uses_same_box_style(self) -> None:
        cases = {
            "screen": ("只出名单", "只会出名单"),
            "pipeline": ("筛查批准写飞书发私信", "输入 y 或 n"),
            "tracking": ("获取物流信息写飞书发单号", "输入 y 或 n"),
        }
        for key, (title, extra) in cases.items():
            text = format_start_banner(key, title)
            lines = text.splitlines()
            self.assertTrue(lines[0].startswith("+"))
            self.assertTrue(lines[1].startswith("|"))
            self.assertIn(f"「{title}」", text)
            self.assertIn(extra, text)
            self.assertIn("请不要关闭这个窗口。", text)
            self.assertEqual(len({visual_width(line) for line in lines}), 1)

    def test_confirm_aborted_says_not_eligible_zero(self) -> None:
        text = format_confirm_aborted(
            "批准写飞书",
            idle_message="没有批准任何申请，也没有写飞书。",
            not_this="这不是「名单里没有可批准的人」。",
        )
        self.assertIn("没有输入 y", text)
        self.assertIn("没有批准任何申请", text)
        self.assertIn("这不是「名单里没有可批准的人」", text)

    def test_force_afternoon_prompt_recommends_waiting(self) -> None:
        text = format_force_afternoon_prompt("14:21")
        self.assertIn("14:21", text)
        self.assertIn("FORCE", text)
        self.assertIn("不推荐", text)
        self.assertIn("直接回车", text)
        aborted = format_force_afternoon_aborted("14:21")
        self.assertIn("没有输入 FORCE", aborted)
        self.assertIn("推荐", aborted)

    def test_confirm_prompt_asks_yn(self) -> None:
        text = format_confirm_prompt(
            "批准写飞书",
            "将批准筛查通过的申请。",
            no_means="退出，不改店铺、不写飞书、不发私信",
        )
        self.assertIn("输入 y 然后回车", text)
        self.assertIn("输入 n 或直接回车", text)
        self.assertIn("不改店铺", text)


class JobSummaryTests(unittest.TestCase):
    def test_screen_zero_pass_box(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exports = root / "exports"
            exports.mkdir()
            csv_path = exports / "sample_screen_20260816_102507.csv"
            json_path = exports / "sample_screen_20260816_102507.json"
            csv_path.write_text("x", encoding="utf-8")
            json_path.write_text("[]", encoding="utf-8")
            text = format_job_summary(
                title="「筛查名单」完成",
                stats=["通过   : 0", "已查看 : 12", "模式   : 筛查（不会批准）"],
                csv_path=csv_path,
                json_path=json_path,
                xlsx_path=None,
                root=root,
                hint="若要批准并发介绍，双击「2-筛查批准写飞书发私信」。",
            )
        self.assertIn("「筛查名单」完成", text)
        self.assertIn("通过   : 0", text)
        self.assertIn("已查看 : 12", text)
        self.assertIn("exports/sample_screen_20260816_102507.csv", text)
        self.assertIn("2-筛查批准写飞书发私信", text)
        self.assertNotIn(str(csv_path.resolve()), text)
        lines = text.splitlines()
        self.assertEqual(len({visual_width(line) for line in lines}), 1)

    def test_display_path_falls_back_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            other = Path(tmp) / "other.csv"
            other.write_text("x", encoding="utf-8")
            self.assertEqual(display_path(other, root=root), str(other.resolve()))


class WaitProgressTests(unittest.TestCase):
    def test_banner_blames_platform(self) -> None:
        text = format_platform_wait_banner()
        self.assertIn("商家中心", text)
        self.assertIn("平台侧", text)
        self.assertIn("不是脚本卡住", text)
        self.assertEqual(len({visual_width(line) for line in text.splitlines()}), 1)

    def test_progress_bounds(self) -> None:
        empty = format_wait_progress(0, 600, bar_width=10)
        full = format_wait_progress(600, 600, bar_width=10)
        self.assertIn("00:00 / 10:00", empty)
        self.assertIn("10:00 / 10:00", full)
        self.assertIn("[----------]", empty)
        self.assertIn("[##########]", full)


if __name__ == "__main__":
    unittest.main()
