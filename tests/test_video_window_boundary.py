"""Replay unverified ordering through the production, conservative collector."""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.lib.screening_perf import screening_run
from scripts.lib.tiktok_creator_videos import ReviewUnavailable, TikHubClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tiktok_video_ordering_cases.json"
ORDERING_FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_ordering_fixture_has_only_synthetic_declarative_fields():
    assert set(ORDERING_FIXTURE) == {
        "schema_version", "source", "baseline_utc", "window_days",
        "handle", "sec_uid", "cases",
    }
    assert ORDERING_FIXTURE["schema_version"] == 1
    assert ORDERING_FIXTURE["source"].startswith("Synthetic ")
    assert ORDERING_FIXTURE["window_days"] == 7
    assert ORDERING_FIXTURE["handle"] == "synthetic_creator"
    assert ORDERING_FIXTURE["sec_uid"] == "synthetic-sec-uid"
    baseline = datetime.fromisoformat(ORDERING_FIXTURE["baseline_utc"])
    assert baseline.utcoffset() == timedelta(0)
    cases = ORDERING_FIXTURE["cases"]
    assert cases
    assert len({case["name"] for case in cases}) == len(cases)
    for case in cases:
        assert {"name", "trusted_contract", "pages", "expected"} <= set(case)
        assert set(case) <= {
            "name", "trusted_contract", "synthetic_pinned_video_ids",
            "settings", "pages", "expected",
        }
        # A fixture annotation cannot authorize production trust or invent pin fields.
        assert case["trusted_contract"] is False
        for setting_name, limit in case.get("settings", {}).items():
            assert setting_name in {"max_pages", "max_videos"}
            assert type(limit) is int and limit > 0
        all_video_ids = set()
        assert case["pages"]
        for page in case["pages"]:
            assert {"videos", "has_more"} <= set(page)
            assert set(page) <= {"videos", "has_more", "max_cursor"}
            assert type(page["has_more"]) is int and page["has_more"] in (0, 1)
            if page["has_more"]:
                assert type(page["max_cursor"]) is int and page["max_cursor"] >= 0
            assert isinstance(page["videos"], list)
            for video in page["videos"]:
                assert {"video_id", "age_seconds"} <= set(video)
                assert set(video) <= {"video_id", "age_seconds", "author_override"}
                assert re.fullmatch(r"700000000000000\d{4}", video["video_id"])
                assert type(video["age_seconds"]) is int
                all_video_ids.add(video["video_id"])
                for field, value in video.get("author_override", {}).items():
                    assert field in {"unique_id", "sec_uid"}
                    assert value.startswith("synthetic")
        assert set(case.get("synthetic_pinned_video_ids", [])) <= all_video_ids
        expected = case["expected"]
        assert expected["request_cursors"][0] == 0
        assert all(type(cursor) is int for cursor in expected["request_cursors"])
        assert len(expected["request_cursors"]) <= len(case["pages"])
        if "error" in expected:
            assert set(expected) == {"error", "request_cursors"}
            assert expected["error"] in {
                "creator_identity_mismatch", "invalid_video_metadata",
                "conflicting_video_metadata",
            }
        else:
            assert set(expected) == {
                "in_window_ids", "complete", "stop_reason", "request_cursors",
            }
            assert type(expected["complete"]) is bool
            assert expected["stop_reason"] in {
                "complete", "pagination_limit", "video_limit", "pagination_stalled",
            }
            assert expected["complete"] == (expected["stop_reason"] == "complete")
            assert len(set(expected["in_window_ids"])) == len(expected["in_window_ids"])
            assert set(expected["in_window_ids"]) <= all_video_ids


def build_provider_page(page, baseline):
    videos = []
    for video in page["videos"]:
        videos.append({
            "aweme_id": video["video_id"],
            "create_time": int(
                (baseline - timedelta(seconds=video["age_seconds"])).timestamp()
            ),
            "author": {
                "unique_id": ORDERING_FIXTURE["handle"],
                "sec_uid": ORDERING_FIXTURE["sec_uid"],
                **video.get("author_override", {}),
            },
            "anchors": [],
        })
    return {
        "aweme_list": videos,
        "has_more": page["has_more"],
        **({"max_cursor": page["max_cursor"]} if "max_cursor" in page else {}),
    }


@pytest.mark.parametrize(
    "case", ORDERING_FIXTURE["cases"], ids=lambda case: case["name"]
)
def test_unverified_window_boundary_replays_conservative_collection(monkeypatch, case):
    baseline = datetime.fromisoformat(ORDERING_FIXTURE["baseline_utc"]).astimezone(UTC)
    settings = {"max_pages": 10, "max_videos": 200, **case.get("settings", {})}
    client = TikHubClient(settings, time.monotonic() + 60)
    pages = iter(case["pages"])
    requests = []

    def fetch_page(endpoint, parameters):
        assert endpoint == "fetch_user_post_videos"
        requests.append(dict(parameters))
        return build_provider_page(next(pages), baseline)

    monkeypatch.setattr(client, "fetch", fetch_page)
    expected = case["expected"]
    with screening_run("synthetic") as recorder:
        if "error" in expected:
            with pytest.raises(ReviewUnavailable, match=f"^{expected['error']}$"):
                client.collect(ORDERING_FIXTURE["handle"], baseline - timedelta(days=7), baseline)
        else:
            result = client.collect(
                ORDERING_FIXTURE["handle"], baseline - timedelta(days=7), baseline
            )
            assert [video["aweme_id"] for video in result["videos"]] == expected["in_window_ids"]
            assert result["complete"] is expected["complete"]
            assert result["stop_reason"] == expected["stop_reason"]
            assert result["sec_uid"] == ORDERING_FIXTURE["sec_uid"]
            observation_reason = (
                "page_limit" if result["stop_reason"] == "pagination_limit"
                else result["stop_reason"]
            )
            assert recorder.counts[f"reason_{observation_reason}"] == 1
    assert [request["max_cursor"] for request in requests] == expected["request_cursors"]
    assert recorder.counts["video_page_calls"] == len(expected["request_cursors"])
    for request_index, request in enumerate(requests):
        assert request == {
            "unique_id": ORDERING_FIXTURE["handle"] if request_index == 0 else "",
            "sec_user_id": "" if request_index == 0 else ORDERING_FIXTURE["sec_uid"],
            "max_cursor": expected["request_cursors"][request_index],
            "count": 20,
            "sort_type": 0,
        }
