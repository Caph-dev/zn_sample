from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.feishu_hero import match_hero, parse_hero_grid  # noqa: E402
from lib.filters import Criteria, evaluate_row  # noqa: E402


HERO_GRID = [
    ["图", "名", "产品货号", "卖点", "佣金", "x", "是否主推", "商品ID"],
    ["", "", "328", "前扣文胸", "0.12", "", "是", "1732060411527205730"],
    ["", "", "P001", "冰丝内裤", "0.1", "", "否", "1732117543281857378"],
    ["", "", "2024", "push up", "0.1", "", "是", "1731459851503571810"],
]


class ParseHeroGridTests(unittest.TestCase):
    def test_hero_keys_only_include_is_hero_rows(self) -> None:
        parsed = parse_hero_grid(HERO_GRID)
        self.assertIn("328", parsed["hero_skus"])
        self.assertIn("2024", parsed["hero_skus"])
        self.assertNotIn("P001", parsed["hero_skus"])
        self.assertIn("1732060411527205730", parsed["hero_product_ids"])
        self.assertNotIn("1732117543281857378", parsed["hero_product_ids"])
        keys = {str(key) for key in parsed["hero_keys"]}
        self.assertIn("328", keys)
        self.assertIn("1732060411527205730", keys)
        self.assertNotIn("p001", keys)
        self.assertNotIn("1732117543281857378", keys)


class MatchHeroTests(unittest.TestCase):
    keys = {"328", "1732060411527205730", "2024", "1731459851503571810"}

    def test_exact_product_id_matches(self) -> None:
        ok, reason = match_hero({"product_id": "1732060411527205730"}, self.keys)
        self.assertTrue(ok)
        self.assertEqual(reason, "精确匹配:1732060411527205730")

    def test_exact_seller_sku_matches(self) -> None:
        ok, reason = match_hero({"seller_sku": "328"}, self.keys)
        self.assertTrue(ok)
        self.assertEqual(reason, "精确匹配:328")

    def test_product_id_containing_hero_sku_does_not_match(self) -> None:
        ok, reason = match_hero(
            {"product_id": "1732117543281857378"},
            self.keys,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "未精确匹配货号/商品ID")

    def test_title_or_sku_desc_containing_sku_does_not_match(self) -> None:
        ok, _ = match_hero(
            {
                "product_id": "999",
                "product_title": "Hicloth 328 wireless bra",
                "sku_desc": "328, Extra Large",
                "sku_id": "1732392671832937314",
            },
            self.keys,
        )
        self.assertFalse(ok)

    def test_empty_keys_returns_none(self) -> None:
        ok, reason = match_hero({"product_id": "1732060411527205730"}, set())
        self.assertIsNone(ok)
        self.assertEqual(reason, "无主推名单")


class EvaluateHeroGateTests(unittest.TestCase):
    def test_empty_hero_keys_fail(self) -> None:
        row = evaluate_row(
            {"product_id": "1732060411527205730"},
            criteria=Criteria(),
            hero_keys=set(),
        )
        self.assertFalse(row["is_hero"])
        self.assertFalse(row["eligible"])
        self.assertTrue(
            any("未加载飞书主推表" in item for item in row["fail_reasons"])
        )

    def test_non_hero_product_id_fails_even_if_sku_digits_overlap(self) -> None:
        row = evaluate_row(
            {"product_id": "1732117543281857378"},
            criteria=Criteria(),
            hero_keys={"328", "1732060411527205730"},
        )
        self.assertFalse(row["is_hero"])
        self.assertTrue(any("非主推款" in item for item in row["fail_reasons"]))

    def test_active_product_allowlist_rejects_other_hero(self) -> None:
        row = evaluate_row(
            {"product_id": "1732060411527205730"},
            criteria=Criteria(),
            hero_keys={"1732060411527205730", "1732414717062320994"},
            allowed_product_ids={"1732414717062320994"},
        )
        self.assertTrue(row["is_hero"])
        self.assertTrue(any("非当前跟进款" in item for item in row["fail_reasons"]))

    def test_active_product_allowlist_accepts_listed_id(self) -> None:
        row = evaluate_row(
            {"product_id": "1732414717062320994"},
            criteria=Criteria(),
            hero_keys={"1732414717062320994"},
            allowed_product_ids={"1732414717062320994"},
        )
        self.assertTrue(row["is_hero"])
        self.assertFalse(any("非当前跟进款" in item for item in row["fail_reasons"]))

    def test_empty_allowlist_does_not_restrict_hero_products(self) -> None:
        row = evaluate_row(
            {"product_id": "1732060411527205730"},
            criteria=Criteria(),
            hero_keys={"1732060411527205730"},
            allowed_product_ids=set(),
        )
        self.assertTrue(row["is_hero"])
        self.assertFalse(any("非当前跟进款" in item for item in row["fail_reasons"]))


if __name__ == "__main__":
    unittest.main()
