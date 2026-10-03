from __future__ import annotations

import copy
import json
import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.creator_api import (  # noqa: E402
    PROFILE_TYPES,
    build_creator_profile_request,
    fetch_creator_detail_api,
    parse_creator_profile_payloads,
)
from lib.page_api import (  # noqa: E402
    CREATOR_PROFILE_ENDPOINT,
    AffiliatePageContext,
    PageApiBusinessError,
    PageApiSchemaError,
)
from lib.operation_cancel import OperationCancelled  # noqa: E402
from lib.screening_perf import screening_run  # noqa: E402

FIXTURE_PATH = PROJECT_ROOT / "tests" / "fixtures" / "creator_profile_types.json"


def load_fixture() -> dict[int, dict]:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return {int(profile_type): payload for profile_type, payload in fixture.items()}


class CreatorProfileParserTests(unittest.TestCase):
    def test_maps_core_metrics_to_dom_detail_contract(self) -> None:
        detail = parse_creator_profile_payloads(load_fixture())

        self.assertEqual(detail["video_gpm"], "$12.5")
        self.assertEqual(detail["video_gpm_n"], 12.5)
        self.assertEqual(detail["live_gpm_n"], 0.0)
        self.assertEqual(detail["overall_gpm_n"], 13.24)
        self.assertEqual(detail["avg_video_views_n"], 460.0)
        self.assertEqual(detail["avg_live_views_n"], 810.0)
        self.assertEqual(detail["video_engagement_n"], 2.5)
        self.assertEqual(detail["live_engagement_n"], 12.5)
        self.assertEqual(detail["est_post_rate_n"], 91.25)
        self.assertEqual(detail["aov_detail_n"], 18.75)
        self.assertEqual(detail["creator_type"], "视频达人")
        self.assertEqual(detail["extract_via"], "api-profile-types-2-3-4-5")

    def test_gpm_range_uses_conservative_minimum(self) -> None:
        payloads = load_fixture()
        gpm_range = payloads[2]["creator_profile"]["ec_video_gpm"]["value"]
        gpm_range.update(
            {
                "minimal": "11",
                "minimal_format": "$11",
                "maximum": "19",
                "maximum_format": "$19",
            }
        )

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_gpm_n"], 11.0)
        self.assertEqual(detail["video_gpm"], "$11")

    def test_live_only_creator_type(self) -> None:
        payloads = load_fixture()
        payloads[2]["creator_profile"]["ec_video_gpm"]["value"].update(
            {
                "minimal": "0",
                "minimal_format": "$0.00",
                "maximum": "0",
                "maximum_format": "$0.00",
            }
        )
        payloads[2]["creator_profile"]["ec_live_gpm"]["value"].update(
            {
                "minimal": "14",
                "minimal_format": "$14",
                "maximum": "14",
                "maximum_format": "$14",
            }
        )

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_gpm_n"], 0.0)
        self.assertEqual(detail["live_gpm_n"], 14.0)
        self.assertEqual(detail["creator_type"], "直播达人")

    def test_zero_gpm_is_valid_inactive_profile(self) -> None:
        payloads = load_fixture()
        for field_name in ("ec_video_gpm", "ec_live_gpm"):
            payloads[2]["creator_profile"][field_name]["value"].update(
                {
                    "minimal": "0",
                    "minimal_format": "$0.00",
                    "maximum": "0",
                    "maximum_format": "$0.00",
                }
            )

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_gpm_n"], 0.0)
        self.assertEqual(detail["live_gpm_n"], 0.0)
        self.assertEqual(detail["creator_type"], "")

    def test_unauthorized_optional_metric_remains_missing(self) -> None:
        payloads = load_fixture()
        payloads[2]["creator_profile"]["video_engagement"][
            "is_authorized"
        ] = False

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_engagement"], "")
        self.assertIsNone(detail["video_engagement_n"])
        self.assertEqual(detail["creator_type"], "视频达人")

    def test_unauthorized_core_gpm_is_complete_api_result(self) -> None:
        payloads = load_fixture()
        payloads[2]["creator_profile"]["ec_video_gpm"]["is_authorized"] = False
        payloads[2]["creator_profile"]["ec_live_gpm"]["is_authorized"] = False

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_gpm"], "")
        self.assertIsNone(detail["video_gpm_n"])
        self.assertEqual(detail["live_gpm"], "")
        self.assertIsNone(detail["live_gpm_n"])
        self.assertEqual(detail["creator_type"], "")

    def test_one_missing_gpm_side_keeps_the_authorized_side(self) -> None:
        payloads = load_fixture()
        payloads[2]["creator_profile"]["ec_live_gpm"]["is_authorized"] = False

        detail = parse_creator_profile_payloads(payloads)

        self.assertEqual(detail["video_gpm_n"], 12.5)
        self.assertIsNone(detail["live_gpm_n"])
        self.assertEqual(detail["creator_type"], "视频达人")

    def test_missing_core_gpm_is_not_a_transport_error(self) -> None:
        payloads = load_fixture()
        del payloads[2]["creator_profile"]["ec_video_gpm"]
        del payloads[2]["creator_profile"]["ec_live_gpm"]

        detail = parse_creator_profile_payloads(payloads)

        self.assertIsNone(detail["video_gpm_n"])
        self.assertIsNone(detail["live_gpm_n"])
        self.assertEqual(detail["creator_type"], "")

    def test_request_contract_allows_only_known_profile_types(self) -> None:
        request = build_creator_profile_request("creator-test-001", 3)
        self.assertEqual(request, {
            "creator_oec_id": "creator-test-001",
            "profile_types": [3],
        })

        with self.assertRaises(PageApiSchemaError):
            build_creator_profile_request("creator-test-001", 9)


class CreatorProfileClientTests(unittest.TestCase):
    def test_client_requests_all_profile_types_separately(self) -> None:
        fixture = load_fixture()
        requested_types: list[int] = []

        def request_json(
            store_id: str,
            endpoint: str,
            body: dict,
            **kwargs,
        ) -> dict:
            self.assertEqual(store_id, "store-test")
            self.assertEqual(endpoint, CREATOR_PROFILE_ENDPOINT)
            profile_type = body["profile_types"][0]
            requested_types.append(profile_type)
            return copy.deepcopy(fixture[profile_type])

        result = fetch_creator_detail_api(
            "store-test",
            {"creator_id": "creator-test-001"},
            request_json=request_json,
            context=AffiliatePageContext(
                href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                shop_id="shop-test",
                shop_region="US",
            ),
        )

        self.assertTrue(result["ok"])
        self.assertEqual(requested_types, list(PROFILE_TYPES))
        self.assertEqual(result["opened"]["via"], "api")

    def test_business_failure_is_classified_without_becoming_empty_metrics(self) -> None:
        def request_json(*_args, **_kwargs) -> dict:
            raise PageApiBusinessError(
                CREATOR_PROFILE_ENDPOINT,
                100000,
                "",
            )

        result = fetch_creator_detail_api(
            "store-test",
            {"creator_id": "creator-test-001"},
            request_json=request_json,
            context=AffiliatePageContext(
                href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                shop_id="shop-test",
                shop_region="US",
            ),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["read_status"], "failed")
        self.assertEqual(result["error_type"], "profile-business-error")
        self.assertEqual(result["business_code"], 100000)
        self.assertIn("code=100000", result["error"])

    def test_cancellation_is_not_converted_to_profile_failure(self) -> None:
        def request_json(*_args, **_kwargs) -> dict:
            raise OperationCancelled("test cancellation")

        with self.assertRaises(OperationCancelled):
            fetch_creator_detail_api(
                "store-test",
                {"creator_id": "creator-test-001"},
                request_json=request_json,
                context=AffiliatePageContext(
                    href="https://affiliate.tiktokshopglobalselling.com/affiliate/sample/sample-request?shop_id=shop-test&shop_region=US",
                    shop_id="shop-test",
                    shop_region="US",
                ),
            )


@pytest.mark.parametrize("use_context", [True, False])
def test_observed_context_and_profiles_match_logical_requests(caplog, use_context):
    caplog.set_level(logging.INFO)
    context = AffiliatePageContext(href="https://example.test/", shop_id="test", shop_region="US")
    fixture = load_fixture()
    requested_types = []

    def request_json(store_id, endpoint, body, **kwargs):
        requested_types.append(body["profile_types"][0])
        return copy.deepcopy(fixture[requested_types[-1]])

    with (
        patch("lib.creator_api.get_affiliate_page_context", return_value=context) as get_context,
        screening_run("profile-test", clock=Mock(side_effect=[index * 0.1 for index in range(10)])) as recorder,
    ):
        result = fetch_creator_detail_api(
            "test", {"creator_id": "CREATOR_SECRET"}, request_json=request_json,
            context=context if use_context else None,
        )
    assert result["detail"] == parse_creator_profile_payloads(fixture)
    assert requested_types == [2, 3, 4, 5]
    assert get_context.call_count == int(not use_context)
    assert recorder.counts.get("detail_context_calls", 0) == int(not use_context)
    assert recorder.counts["detail_profile_calls"] == 4
    assert all(recorder.counts[f"profile_type_{profile_type}"] == 1 for profile_type in PROFILE_TYPES)
    assert "CREATOR_SECRET" not in caplog.text


def test_observed_profile_business_error_ends_without_leaking_message(caplog):
    caplog.set_level(logging.INFO)
    request = Mock(return_value={"code": 100000, "message": "Authorization=SECRET_SENTINEL"})
    with screening_run("profile-error"):
        result = fetch_creator_detail_api(
            "test", {"creator_id": "test"}, request_json=request,
            context=AffiliatePageContext(href="https://example.test/", shop_id="test", shop_region="US"),
        )
    assert not result["ok"]
    assert request.call_count == 1
    events = [json.loads(record.getMessage().removeprefix("[筛查耗时]")) for record in caplog.records]
    assert events[-1]["event"] == "end"
    assert events[-1]["status"] == "error"
    assert events[-1]["counts"]["reason_profile_business_error"] == 1
    assert "SECRET_SENTINEL" not in caplog.text


if __name__ == "__main__":
    unittest.main()
