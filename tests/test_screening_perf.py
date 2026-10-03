"""Deterministic observations, isolation and redaction without business I/O."""

import asyncio
import json
import logging
from unittest.mock import Mock

import pytest

from scripts.lib import screening_perf as perf
from scripts.lib.tiktok_creator_videos import ReviewUnavailable


def observations(caplog):
    return [
        json.loads(record.getMessage()[len(perf.LOG_PREFIX):])
        for record in caplog.records
        if record.getMessage().startswith(perf.LOG_PREFIX)
    ]


def test_fake_clock_nested_stages_do_not_double_count(caplog):
    caplog.set_level(logging.INFO)
    clock = Mock(side_effect=[0.0, 1.0, 1.125, 1.5])
    with perf.screening_run("preview-test", clock=clock) as recorder:
        with perf.screening_stage("preview_total", summary=True):
            perf.screening_count("list_rows", 8)
            with perf.screening_stage("detail_profile") as stage:
                stage.add("detail_profile_calls")
                stage.add("profile_type_2")
    events = observations(caplog)
    assert [(event["event"], event["stage"]) for event in events] == [
        ("begin", "preview_total"), ("begin", "detail_profile"),
        ("end", "detail_profile"), ("end", "preview_total"),
    ]
    assert events[2]["elapsed_ms"] == 125.0
    assert events[3]["elapsed_ms"] == 1500.0
    assert events[2]["counts"] == {"detail_profile_calls": 1, "profile_type_2": 1}
    assert events[3]["counts"]["detail_profile_calls"] == 1
    assert events[3]["counts"]["vision_model_calls"] == 0
    assert recorder.counts["list_rows"] == 8
    assert [event["sequence"] for event in events] == [1, 2, 3, 4]
    assert all(set(event) == {
        "event", "run_id", "stage", "elapsed_ms", "status", "counts", "sequence",
    } for event in events)


def test_nested_and_sequential_runs_restore_counts_and_stage(caplog):
    caplog.set_level(logging.INFO)
    with perf.screening_run("outer") as outer:
        with perf.screening_stage("content_review"):
            perf.screening_count("creator_collections")
            with perf.screening_run("inner") as inner:
                perf.screening_count("creator_reuse", 3)
            with perf.screening_run(reuse=True) as reused:
                assert reused is outer
                perf.screening_count("creator_reuse")
    with perf.screening_run("later") as later:
        perf.screening_count("creator_collections", 2)
    assert outer.counts == {"creator_collections": 1, "creator_reuse": 1}
    assert inner.counts == {"creator_reuse": 3}
    assert later.counts == {"creator_collections": 2}
    assert not perf.screening_run_active()
    assert observations(caplog)[-1]["counts"]["creator_reuse"] == 1


def test_concurrent_runs_are_context_local():
    async def run_batch(run_id, amount):
        with perf.screening_run(run_id) as recorder:
            await asyncio.sleep(0)
            perf.screening_count("list_rows", amount)
            return recorder

    async def gather_batches():
        return await asyncio.gather(run_batch("first", 2), run_batch("second", 7))

    first, second = asyncio.run(gather_batches())
    assert first.counts == {"list_rows": 2}
    assert second.counts == {"list_rows": 7}
    assert not perf.screening_run_active()


@pytest.mark.parametrize("error,reason", [
    (ReviewUnavailable("provider_unavailable"), "provider_unavailable"),
    (ReviewUnavailable("SIGNED_URL_SECRET Authorization=TOKEN_SECRET"), "unexpected_error"),
    (RuntimeError("HANDLE_SECRET SEC_UID_SECRET PROMPT_SECRET BASE64_SECRET"), "unexpected_error"),
])
def test_errors_end_rethrow_and_never_export_payload(caplog, error, reason):
    caplog.set_level(logging.INFO)
    with pytest.raises(type(error)) as caught:
        with perf.screening_run("safe-id", clock=Mock(side_effect=[3.0, 3.05])):
            with perf.screening_stage("media_download"):
                raise error
    assert caught.value is error
    events = observations(caplog)
    assert events[-1]["status"] == "error"
    assert events[-1]["elapsed_ms"] == 50.0
    assert events[-1]["counts"] == {f"reason_{reason}": 1}
    assert "SECRET" not in caplog.text
    assert not perf.screening_run_active()


def test_skip_and_whitelists(caplog):
    caplog.set_level(logging.INFO)
    with perf.screening_run("safe"):
        with perf.screening_stage("detail_enrichment") as stage:
            stage.add("detail_cache_hit", 0)
            stage.skip("disabled")
        with perf.screening_stage("content_review") as stage:
            stage.skip("UNSAFE_SECRET")
        with pytest.raises(ValueError):
            with perf.screening_stage("UNSAFE_STAGE_SECRET"):
                pass
        with perf.screening_stage("list_scan") as stage:
            with pytest.raises(ValueError):
                stage.add("UNSAFE_COUNT_SECRET")
            with pytest.raises(ValueError):
                stage.add("list_rows", -1)
    events = observations(caplog)
    assert events[1]["status"] == "skipped"
    assert events[1]["counts"] == {"detail_cache_hit": 0, "reason_disabled": 1}
    assert "SECRET" not in caplog.text


def test_without_run_is_noop(monkeypatch):
    forbidden_log = Mock(side_effect=AssertionError("Unexpected observation"))
    monkeypatch.setattr(perf.logger, "info", forbidden_log)
    with perf.screening_stage("list_scan") as stage:
        stage.add("list_rows", 4)
        perf.screening_count("detail_api_calls")
        stage.skip("disabled")
    assert stage.counts == {}
    forbidden_log.assert_not_called()
