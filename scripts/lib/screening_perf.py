"""Run-local screening observations; never inspect business payloads or deadlines."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
LOG_PREFIX = "[筛查耗时]"

STAGES = frozenset({
    "preview_total", "navigation", "hero_load", "list_scan", "detail_enrichment",
    "rule_evaluation", "content_review", "detail_context", "detail_profile",
    "detail_api", "detail_dom", "detail_delay", "video_page_fetch",
    "video_detail_fetch", "media_download", "frame_extract", "vision_model",
    "visual_cache",
})
COUNT_NAMES = frozenset({
    "list_rows", "detail_target_rows", "detail_collections", "detail_context_calls",
    "detail_profile_calls", "profile_type_2", "profile_type_3", "profile_type_4",
    "profile_type_5", "detail_api_calls", "detail_api_success", "detail_api_failure",
    "detail_dom_calls", "detail_dom_success", "detail_dom_failure", "detail_delay_calls",
    "detail_cache_hit", "detail_prefilter_skipped", "detail_circuit_skipped",
    "rule_rows", "content_target_rows", "creator_collections", "creator_reuse",
    "proof_reuse", "video_page_calls", "video_page_success", "video_page_failure",
    "video_returned", "video_in_window", "video_outside_window", "video_detail_calls",
    "media_download_attempts", "media_download_bytes", "ffmpeg_calls", "frames",
    "visual_cache_hit", "visual_cache_miss", "visual_attempts", "vision_model_calls",
})
REASONS = frozenset({
    "disabled", "no_targets", "no_rows", "exit_error", "api_fallback", "direct_dom",
    "shadow_dom", "profile_business_error", "profile_schema_error", "cancelled",
    "unexpected_error", "missing_tikhub_key", "missing_ark_key", "request_deadline",
    "run_deadline", "provider_http_error", "provider_unavailable", "metadata_provider_error",
    "video_detail_missing", "creator_identity_mismatch", "creator_identity_missing",
    "media_url_missing", "unsafe_media_url", "non_public_media_address",
    "dns_resolution_unavailable", "response_byte_limit", "redirect_rejected",
    "invalid_redirect", "redirect_limit", "invalid_json", "invalid_json_object",
    "duplicate_json_key", "invalid_json_number", "ffmpeg_unavailable",
    "frame_extraction_failed", "frame_byte_limit", "visual_response_incomplete",
    "visual_response_missing", "visual_schema_invalid", "video_list_missing",
    "video_time_missing", "pagination_stalled", "page_limit", "video_limit",
    "complete", "positive_evidence", "stopped", "missing_creator_id",
})
REASON_COUNTS = frozenset(f"reason_{reason}" for reason in REASONS)


def safe_error_code(error: BaseException) -> str:
    """Only exact registered reason codes or known exception types are exported."""
    exception_name = type(error).__name__
    if exception_name == "ReviewUnavailable" and len(error.args) == 1:
        reason = error.args[0]
        if isinstance(reason, str) and reason in REASONS:
            return reason
    return {
        "PageApiBusinessError": "profile_business_error",
        "PageApiSchemaError": "profile_schema_error",
        "OperationCancelled": "cancelled",
    }.get(exception_name, "unexpected_error")


@dataclass
class ScreeningRecorder:
    run_id: str
    clock: Callable[[], float] = time.monotonic
    counts: dict[str, int] = field(default_factory=dict)
    sequence: int = 0

    def emit(self, event: str, observation: StageObservation, elapsed_ms: float) -> None:
        self.sequence += 1
        if observation.summary and event == "end":
            counts = dict.fromkeys(sorted(COUNT_NAMES), 0)
            counts.update(self.counts)
        else:
            counts = dict(observation.counts)
        logger.info("%s%s", LOG_PREFIX, json.dumps({
            "event": event,
            "run_id": self.run_id,
            "stage": observation.stage,
            "elapsed_ms": round(max(0.0, elapsed_ms), 3),
            "status": observation.status,
            "counts": counts,
            "sequence": self.sequence,
        }, ensure_ascii=True, allow_nan=False, separators=(",", ":")))


@dataclass
class StageObservation:
    stage: str
    recorder: ScreeningRecorder | None = None
    summary: bool = False
    status: str = "success"
    counts: dict[str, int] = field(default_factory=dict)

    def add(self, name: str, amount: int = 1) -> None:
        if self.recorder is None:
            return
        if name not in COUNT_NAMES and name not in REASON_COUNTS:
            raise ValueError("Unregistered screening count")
        if type(amount) is not int or amount < 0:
            raise ValueError("Screening counts must be nonnegative integers")
        self.counts[name] = self.counts.get(name, 0) + amount
        self.recorder.counts[name] = self.recorder.counts.get(name, 0) + amount

    def fail(self, reason: str = "unexpected_error") -> None:
        self.status = "error"
        self.add(f"reason_{reason if reason in REASONS else 'unexpected_error'}")

    def skip(self, reason: str) -> None:
        self.status = "skipped"
        self.add(f"reason_{reason if reason in REASONS else 'unexpected_error'}")


_recorder: ContextVar[ScreeningRecorder | None] = ContextVar("screening_recorder", default=None)
_observation: ContextVar[StageObservation | None] = ContextVar("screening_stage", default=None)


def screening_run_active() -> bool:
    return _recorder.get() is not None


def current_screening_stage() -> StageObservation | None:
    return _observation.get()


@contextmanager
def screening_run(
    run_id: str | None = None,
    *,
    clock: Callable[[], float] = time.monotonic,
    reuse: bool = False,
) -> Iterator[ScreeningRecorder]:
    existing = _recorder.get()
    if reuse and existing is not None:
        yield existing
        return
    recorder = ScreeningRecorder(run_id or uuid.uuid4().hex, clock)
    recorder_token = _recorder.set(recorder)
    observation_token = _observation.set(None)
    try:
        yield recorder
    finally:
        _observation.reset(observation_token)
        _recorder.reset(recorder_token)


@contextmanager
def screening_stage(stage: str, *, summary: bool = False) -> Iterator[StageObservation]:
    recorder = _recorder.get()
    observation = StageObservation(stage, recorder, summary)
    if recorder is None:
        yield observation
        return
    if stage not in STAGES:
        raise ValueError("Unregistered screening stage")
    started = recorder.clock()
    token = _observation.set(observation)
    try:
        recorder.emit("begin", observation, 0.0)
        try:
            yield observation
        except BaseException as error:
            observation.fail(safe_error_code(error))
            raise
    finally:
        try:
            recorder.emit("end", observation, (recorder.clock() - started) * 1000)
        finally:
            _observation.reset(token)


def screening_count(name: str, amount: int = 1) -> None:
    observation = _observation.get()
    if observation is not None:
        observation.add(name, amount)
    elif _recorder.get() is not None:
        StageObservation("", _recorder.get()).add(name, amount)
