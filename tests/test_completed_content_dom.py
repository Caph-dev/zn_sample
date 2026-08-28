from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.completed_content_dom import (  # noqa: E402
    inspect_wanted_completed_creators,
    locate_completed_creator_row,
)


class LocateCompletedCreatorRowTests(unittest.TestCase):
    @patch("lib.completed_content_dom.click_next")
    @patch("lib.completed_content_dom.ensure_completed_tab")
    @patch("lib.completed_content_dom.reset_completed_filters")
    @patch("lib.completed_content_dom._wait_for_creator_row")
    def test_pages_until_unfiltered_row_is_visible(
        self,
        wait_for_row,
        reset_filters,
        ensure_tab,
        click_next,
    ) -> None:
        reset_filters.return_value = {"ok": True, "clicked": True}
        ensure_tab.return_value = {"ok": True}
        wait_for_row.side_effect = [
            {"matched_count": 0, "can_next": True, "data_row_count": 50},
            {"matched_count": 1, "can_next": True, "data_row_count": 50},
        ]
        click_next.return_value = {"ok": True}

        result = locate_completed_creator_row("store-test", "jaxxelyn")

        self.assertTrue(result["ok"])
        self.assertTrue(result["row_visible"])
        self.assertEqual(result["page"], 2)
        click_next.assert_called_once()
        reset_filters.assert_called_once()

    @patch("lib.completed_content_dom.click_next")
    @patch("lib.completed_content_dom.ensure_completed_tab")
    @patch("lib.completed_content_dom.reset_completed_filters")
    @patch("lib.completed_content_dom._wait_for_creator_row")
    def test_stops_when_unfiltered_table_has_no_match(
        self,
        wait_for_row,
        reset_filters,
        ensure_tab,
        click_next,
    ) -> None:
        reset_filters.return_value = {"ok": True, "clicked": True}
        ensure_tab.return_value = {"ok": True}
        wait_for_row.return_value = {
            "matched_count": 0,
            "can_next": False,
            "data_row_count": 12,
        }

        result = locate_completed_creator_row("store-test", "jaxxelyn")

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "row-not-found")
        click_next.assert_not_called()


class InspectWantedCompletedCreatorsTests(unittest.TestCase):
    @patch("lib.completed_content_dom.click_next")
    @patch("lib.completed_content_dom.read_view_content_on_current_page")
    @patch("lib.completed_content_dom.list_visible_completed_creators")
    @patch("lib.completed_content_dom.reset_completed_filters")
    @patch("lib.completed_content_dom.ensure_completed_tab")
    def test_inspects_matching_row_on_the_page_it_appears(
        self,
        ensure_tab,
        reset_filters,
        list_visible,
        read_drawer,
        click_next,
    ) -> None:
        ensure_tab.return_value = {"ok": True}
        reset_filters.return_value = {"ok": True, "clicked": True}
        list_visible.side_effect = [
            {
                "ok": True,
                "names": [{"name": "other", "nick": "Other"}],
                "can_next": True,
            },
            {
                "ok": True,
                "names": [{"name": "jaxxelyn", "nick": "Jaxxelyn"}],
                "can_next": True,
            },
        ]
        read_drawer.return_value = {
            "ok": True,
            "panel_text": "内容详情\n视频\n3\n直播\n0\n在TikTok查看视频",
            "links": [],
        }
        click_next.return_value = {"ok": True}

        result = inspect_wanted_completed_creators(
            "store-test",
            ["jaxxelyn"],
        )

        self.assertTrue(result["jaxxelyn"]["ok"])
        self.assertIn("视频", result["jaxxelyn"]["panel_text"])
        read_drawer.assert_called_once_with("store-test", "jaxxelyn", wait=1.5)
        click_next.assert_called_once()


if __name__ == "__main__":
    unittest.main()
