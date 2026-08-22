from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.page_api import (  # noqa: E402
    SAMPLE_LIST_ENDPOINT,
    AffiliatePageContext,
    PageApiError,
    post_read_json,
)
from lib.sample_api import (  # noqa: E402
    build_pending_list_request,
    check_pending_application_api,
    locate_pending_application,
    parse_pending_list_payload,
    scrape_pending_list_api,
    scrape_processing_list_api,
    scrape_shipped_list_api,
)
from lib.sample_data_source import compare_pending_rows  # noqa: E402

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "sample_group_list.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


class SampleApiParserTests(unittest.TestCase):
    def test_maps_aggregate_to_existing_dom_row_shape(self) -> None:
        rows = parse_pending_list_payload(
            load_fixture(),
            list_href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
        )

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["apply_id"], "apply-test-001")
        self.assertEqual(row["apply_ids"], ["apply-test-001", "apply-test-002"])
        self.assertEqual(row["product_id"], "product-test-001")
        self.assertEqual(row["creator_id"], "creator-test-001")
        self.assertEqual(row["creator_name"], "creator_test")
        self.assertEqual(row["follower_num"], "3200")
        self.assertTrue(row["can_be_approved"])
        self.assertEqual(row["_data_source"], "api")

    def test_request_contract_uses_pending_tab_and_server_pagination(self) -> None:
        request = build_pending_list_request(3, 25)

        self.assertEqual(request["tab"], 10)
        self.assertEqual(request["cur_page"], 3)
        self.assertEqual(request["page_size"], 25)

    def test_shipped_scraper_uses_shipped_tab_and_keeps_order_context(self) -> None:
        payload = load_fixture()
        payload["agg_info"][0]["apply_deatil"]["apply_info"].update(
            {
                "curr_status": 30,
                "main_order_id": "order-test-001",
            }
        )
        request_bodies: list[dict] = []

        def request_json(store_id: str, endpoint: str, body: dict, **kwargs) -> dict:
            request_bodies.append(body)
            return payload

        rows = scrape_shipped_list_api(
            "store-test",
            max_pages=1,
            request_json=request_json,
            context=AffiliatePageContext(
                href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                shop_id="shop-test",
                shop_region="US",
            ),
        )

        self.assertEqual(request_bodies[0]["tab"], 30)
        self.assertEqual(rows[0]["main_order_id"], "order-test-001")
        self.assertEqual(rows[0]["_shop_id"], "shop-test")

    def test_processing_scraper_forces_tab_without_pending_navigation(self) -> None:
        request_bodies: list[dict] = []

        def request_json(store_id: str, endpoint: str, body: dict, **kwargs) -> dict:
            request_bodies.append(body)
            return {"code": 0, "agg_info": [], "total_count": 0, "has_more": False}

        with patch("lib.sample_api.assert_on_pending_list") as assert_pending:
            rows = scrape_processing_list_api(
                "store-test",
                max_pages=1,
                request_json=request_json,
                context=AffiliatePageContext(
                    href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request",
                    shop_id="shop-test",
                    shop_region="US",
                ),
                ensure_page=True,
            )

        self.assertEqual(rows, [])
        self.assertEqual(request_bodies[0]["tab"], 40)
        assert_pending.assert_not_called()

    def test_scraper_follows_has_more_and_deduplicates(self) -> None:
        first_payload = load_fixture()
        first_payload["has_more"] = True
        second_payload = copy.deepcopy(first_payload)
        second_payload["has_more"] = False
        second_payload["agg_info"][0]["apply_deatil"]["apply_info"]["apply_id"] = (
            "apply-test-003"
        )
        second_payload["agg_info"][0]["apply_deatil"]["apply_info"]["product_id"] = (
            "product-test-003"
        )
        second_payload["agg_info"][0]["apply_group"]["apply_ids"] = [
            "apply-test-003"
        ]
        calls: list[int] = []

        def request_json(
            store_id: str,
            endpoint: str,
            body: dict,
            **kwargs,
        ) -> dict:
            self.assertEqual(store_id, "store-test")
            self.assertEqual(endpoint, SAMPLE_LIST_ENDPOINT)
            calls.append(body["cur_page"])
            return first_payload if body["cur_page"] == 1 else second_payload

        rows = scrape_pending_list_api(
            "store-test",
            max_pages=5,
            ensure_page=False,
            request_json=request_json,
            context=AffiliatePageContext(
                href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                shop_id="shop-test",
                shop_region="US",
            ),
        )

        self.assertEqual(calls, [1, 2])
        self.assertEqual([row["apply_id"] for row in rows], [
            "apply-test-001",
            "apply-test-003",
        ])

    def test_locates_pending_application_and_checks_identity(self) -> None:
        rows = parse_pending_list_payload(load_fixture(), list_href="test")

        status = locate_pending_application(
            rows,
            "apply-test-002",
            expected_creator_id="creator-test-001",
            expected_product_id="product-test-001",
        )

        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "pending-approvable")

    def test_pending_preflight_rejects_identity_change(self) -> None:
        rows = parse_pending_list_payload(load_fixture(), list_href="test")

        status = locate_pending_application(
            rows,
            "apply-test-001",
            expected_creator_id="different-creator",
            expected_product_id="product-test-001",
        )

        self.assertFalse(status["ok"])
        self.assertEqual(status["state"], "identity-mismatch")
        self.assertEqual(status["mismatched_fields"], ["creator_id"])

    def test_pending_preflight_requires_explicit_approvable_state(self) -> None:
        rows = parse_pending_list_payload(load_fixture(), list_href="test")
        rows[0]["can_be_approved"] = None

        status = locate_pending_application(rows, "apply-test-001")

        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "pending-not-approvable")

    def test_pending_preflight_reports_missing_application(self) -> None:
        rows = parse_pending_list_payload(load_fixture(), list_href="test")

        status = locate_pending_application(rows, "apply-test-missing")

        self.assertTrue(status["ok"])
        self.assertEqual(status["state"], "not-found-in-pending")

    def test_pending_preflight_stops_after_target_page(self) -> None:
        first_payload = load_fixture()
        first_payload["has_more"] = True
        second_payload = copy.deepcopy(first_payload)
        second_payload["has_more"] = False
        second_payload["agg_info"][0]["apply_deatil"]["apply_info"]["apply_id"] = (
            "target-apply-id"
        )
        second_payload["agg_info"][0]["apply_group"]["apply_ids"] = [
            "target-apply-id"
        ]
        requested_pages: list[int] = []

        def request_json(store_id: str, endpoint: str, body: dict, **kwargs) -> dict:
            requested_pages.append(body["cur_page"])
            return first_payload if body["cur_page"] == 1 else second_payload

        with (
            patch("lib.sample_api.assert_on_pending_list"),
            patch(
                "lib.sample_api.get_affiliate_page_context",
                return_value=AffiliatePageContext(
                    href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                    shop_id="shop-test",
                    shop_region="US",
                ),
            ),
            patch("lib.sample_api.post_read_json", side_effect=request_json),
        ):
            status = check_pending_application_api(
                "store-test",
                "target-apply-id",
                expected_creator_id="creator-test-001",
                expected_product_id="product-test-001",
            )

        self.assertEqual(requested_pages, [1, 2])
        self.assertEqual(status["state"], "pending-approvable")
        self.assertEqual(status["page"], 2)


class PageApiTransportTests(unittest.TestCase):
    def test_transport_uses_only_allowlisted_endpoint_without_session_extraction(self) -> None:
        with patch(
            "lib.page_api.zclaw_exec",
            side_effect=[
                {"ok": True, "started": True, "request_id": "request-test"},
                {
                    "done": True,
                    "ok": True,
                    "status": 200,
                    "payload": {"code": 0, "agg_info": [], "total_count": 0},
                },
            ],
        ) as execute:
            payload = post_read_json(
                "store-test",
                SAMPLE_LIST_ENDPOINT,
                build_pending_list_request(1),
                context=AffiliatePageContext(
                    href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                    shop_id="shop-test",
                    shop_region="US",
                ),
            )

        start_script = execute.call_args_list[0].args[1]
        poll_script = execute.call_args_list[1].args[1]
        self.assertNotIn("document.cookie", start_script.lower())
        self.assertNotIn("cookie_enabled", start_script.lower())
        self.assertIn("oec_seller_id=shop-test", start_script)
        self.assertIn("fetch(requestUrl", start_script)
        self.assertNotIn("XMLHttpRequest", start_script)
        self.assertIn("delete requestStates[requestId]", poll_script)
        self.assertEqual(payload["code"], 0)

    def test_transport_reports_async_response_size_failure(self) -> None:
        with patch(
            "lib.page_api.zclaw_exec",
            side_effect=[
                {"ok": True, "started": True, "request_id": "request-test"},
                {
                    "done": True,
                    "ok": False,
                    "status": 200,
                    "error_type": "response-too-large",
                },
            ],
        ):
            with self.assertRaisesRegex(PageApiError, "response-too-large"):
                post_read_json(
                    "store-test",
                    SAMPLE_LIST_ENDPOINT,
                    build_pending_list_request(1),
                    context=AffiliatePageContext(
                        href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                        shop_id="shop-test",
                        shop_region="US",
                    ),
                    retries=0,
                    poll_interval_seconds=0.01,
                )

    def test_transport_rejects_unregistered_endpoint(self) -> None:
        with self.assertRaises(PageApiError):
            post_read_json(
                "store-test",
                "/api/v1/affiliate/sample/approve",
                {},
                context=AffiliatePageContext(
                    href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                    shop_id="shop-test",
                    shop_region="US",
                ),
            )


class ShadowComparisonTests(unittest.TestCase):
    def test_matching_rows_pass(self) -> None:
        rows = parse_pending_list_payload(load_fixture(), list_href="test")
        report = compare_pending_rows(rows, copy.deepcopy(rows))

        self.assertTrue(report["ok"])
        self.assertEqual(report["field_mismatches"], [])

    def test_mismatch_report_does_not_expose_apply_id(self) -> None:
        api_rows = parse_pending_list_payload(load_fixture(), list_href="test")
        dom_rows = copy.deepcopy(api_rows)
        dom_rows[0]["gmv"] = "$9,999"

        report = compare_pending_rows(dom_rows, api_rows)
        serialized_report = json.dumps(report, ensure_ascii=False)
        self.assertFalse(report["ok"])
        self.assertEqual(report["field_mismatches"][0]["fields"], ["gmv"])
        self.assertNotIn("apply-test-001", serialized_report)
        self.assertNotIn("creator_test", serialized_report)


if __name__ == "__main__":
    unittest.main()
