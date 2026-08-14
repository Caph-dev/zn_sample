from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from lib.page_api import AffiliatePageContext, post_sample_group_action_json  # noqa: E402
from lib.sample_write_api import (  # noqa: E402
    CREATOR_ORDER_PENDING_STATUS,
    approve_application_api,
    build_approve_application_request,
    confirm_application_approved_api,
)


TEST_CONTEXT = AffiliatePageContext(
    href=(
        "https://affiliate.tiktokshopglobalselling.com/affiliate/sample/"
        "sample-request?shop_id=shop-test&shop_region=US"
    ),
    shop_id="shop-test",
    shop_region="US",
)


class SampleWriteRequestTests(unittest.TestCase):
    def test_builds_captured_single_approval_request(self) -> None:
        request = build_approve_application_request(
            "apply-test-001",
            status_type=CREATOR_ORDER_PENDING_STATUS,
        )

        self.assertEqual(
            request,
            {
                "apply_ids": ["apply-test-001"],
                "group_ids": [],
                "is_use_cross_regions": False,
                "status_type": 11,
                "type": 1,
            },
        )

    def test_approval_requires_captured_pending_status(self) -> None:
        with self.assertRaisesRegex(Exception, "status_type=11"):
            build_approve_application_request("apply-test-001", status_type=20)

    def test_approval_runs_preflight_and_accepts_one_success(self) -> None:
        preflight = {
            "ok": True,
            "state": "pending-approvable",
            "apply_id": "apply-test-001",
            "can_be_approved": True,
            "curr_status": 11,
        }
        requests: list[dict] = []

        def request_json(store_id: str, body: dict, **kwargs) -> dict:
            requests.append(body)
            self.assertEqual(store_id, "store-test")
            self.assertEqual(kwargs["context"], TEST_CONTEXT)
            return {"code": 0, "success_count": 1, "failed_count": 0}

        result = approve_application_api(
            "store-test",
            "apply-test-001",
            expected_creator_id="creator-test-001",
            expected_product_id="product-test-001",
            context=TEST_CONTEXT,
            preflight_status=preflight,
            request_json=request_json,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "action-accepted")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["status_type"], 11)

    def test_approval_blocks_preflight_without_sending(self) -> None:
        request_calls = 0

        def request_json(*args, **kwargs) -> dict:
            nonlocal request_calls
            request_calls += 1
            return {"code": 0, "success_count": 1, "failed_count": 0}

        result = approve_application_api(
            "store-test",
            "apply-test-001",
            expected_creator_id="creator-test-001",
            expected_product_id="product-test-001",
            context=TEST_CONTEXT,
            preflight_status={
                "ok": True,
                "state": "pending-not-approvable",
                "apply_id": "apply-test-001",
                "can_be_approved": False,
            },
            request_json=request_json,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["state"], "preflight-blocked")
        self.assertEqual(request_calls, 0)

    def test_confirmation_accepts_ready_to_ship_transition(self) -> None:
        statuses = [
            {
                "ok": True,
                "state": "pending-approvable",
                "apply_id": "apply-test-001",
                "curr_status": 20,
                "can_be_approved": True,
                "main_order_id": "order-test-001",
                "tab": 20,
            },
        ]

        def check_application(*args, **kwargs) -> dict:
            return statuses.pop(0)

        with patch(
            "lib.sample_write_api.check_application_in_tab_api",
            side_effect=check_application,
        ):
            result = confirm_application_approved_api(
                "store-test",
                "apply-test-001",
                expected_creator_id="creator-test-001",
                expected_product_id="product-test-001",
                context=TEST_CONTEXT,
                max_attempts=1,
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["state"], "approved")
        self.assertEqual(result["ready_to_ship"]["curr_status"], 20)


class SampleWriteTransportTests(unittest.TestCase):
    def test_write_transport_is_allowlisted_and_not_retried(self) -> None:
        with patch(
            "lib.page_api.zclaw_exec",
            side_effect=[
                {"ok": True, "started": True, "request_id": "request-test"},
                {
                    "done": True,
                    "ok": True,
                    "status": 200,
                    "payload": {"code": 0, "success_count": 1, "failed_count": 0},
                },
            ],
        ) as execute:
            payload = post_sample_group_action_json(
                "store-test",
                {
                    "apply_ids": ["apply-test-001"],
                    "group_ids": [],
                    "is_use_cross_regions": False,
                    "status_type": 11,
                    "type": 1,
                },
                context=TEST_CONTEXT,
                poll_interval_seconds=0.01,
            )

        start_script = execute.call_args_list[0].args[1]
        self.assertIn("/api/v1/affiliate/sample/group/action", start_script)
        self.assertIn('"apply-test-001"', start_script)
        self.assertNotIn("document.cookie", start_script.lower())
        self.assertNotIn("XMLHttpRequest", start_script)
        self.assertEqual(execute.call_args_list[0].kwargs["retries"], 0)
        self.assertEqual(payload["success_count"], 1)


if __name__ == "__main__":
    unittest.main()
