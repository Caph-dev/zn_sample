"""Local, fail-closed creator content review. Never approves or writes platforms.

Sparse vision is positive evidence only: absence in sampled frames is unknown.
Evidence HMAC protects against edited CSV/JSON, not an attacker owning the cache
directory and its local signing key. Approval always revalidates local evidence.
Per-video visual results are cached keyed by (video id, model, version, create
time, anchors) so a later window reuses them without a live download or ARK call.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from .app_config import load_content_review_settings
from .creator_video_contract import (
    REVIEW_VERSION,
    VISUAL_PROMPT,
    has_positive_visual,
    related_products,
    shopping_products,
    validate_visual,
)
from .tiktok_creator_videos import (
    ReviewUnavailable,
    TikHubClient,
    bounded_request,
    normalize_handle,
    strict_json,
    trusted_request,
    verify_author,
)

MAX_PROOF_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 1024 * 1024
MAX_VISUAL_CACHE_JSON = 1024 * 1024
FRAME_INTERVAL_SECONDS = 10


def _utc_now(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None:
        raise ReviewUnavailable("timezone_required")
    return value.astimezone(UTC)


def _is_true(value: object) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


def _canonical(value: dict) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    ).encode("utf-8")


def _safe_path(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ReviewUnavailable("evidence_path_outside_cache")
    return resolved


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        value = source.read(limit + 1)
    if len(value) > limit:
        raise ReviewUnavailable("evidence_byte_limit")
    return value


def _signing_key(root: Path, *, create: bool = False) -> bytes:
    path = root / ".proof-key"
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as output:
                output.write(secrets.token_bytes(32))
    if path.is_symlink():
        raise ReviewUnavailable("invalid_proof_key")
    value = _read_bounded(_safe_path(root, str(path)), 32)
    if len(value) != 32:
        raise ReviewUnavailable("invalid_proof_key")
    return value


def _ffmpeg_base_args(media: Path) -> list[str]:
    # MP4 demuxer + file-only protocols prohibit HLS/network/nested URL inputs.
    return [
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-max_alloc",
        "67108864",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov",
        "-i",
        str(media),
        "-an",
        "-sn",
        "-dn",
    ]


def _run_ffmpeg(executable: str, arguments: list[str], remaining: float) -> bool:
    completed = subprocess.run(
        [str(executable), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=remaining,
        check=False,
    )
    return completed.returncode == 0


def _extract_frames(
    media: Path, directory: Path, settings: dict, deadline: float
) -> tuple[list[Path], int | None]:
    """Sample one frame per 10 seconds; fall back to the very first frame for
    short clips, where fps=1/10 legitimately yields zero frames."""
    executable = settings["ffmpeg_path"] or shutil.which("ffmpeg")
    if not executable:
        raise ReviewUnavailable("ffmpeg_unavailable")
    remaining = min(45, deadline - time.monotonic())
    if remaining <= 0:
        raise ReviewUnavailable("run_deadline")
    sampled = [
        *_ffmpeg_base_args(media),
        "-t",
        str(settings["max_frames"] * FRAME_INTERVAL_SECONDS),
        "-vf",
        f"fps=1/{FRAME_INTERVAL_SECONDS},scale=640:640:force_original_aspect_ratio=decrease",
        "-frames:v",
        str(settings["max_frames"]),
        "-q:v",
        "4",
        str(directory / "frame_%02d.jpg"),
    ]
    _run_ffmpeg(executable, sampled, remaining)
    frames = sorted(directory.glob("frame_*.jpg"))
    interval: int | None = FRAME_INTERVAL_SECONDS
    if not frames:
        first = [
            *_ffmpeg_base_args(media),
            "-vf",
            "scale=640:640:force_original_aspect_ratio=decrease",
            "-frames:v",
            "1",
            "-q:v",
            "4",
            str(directory / "frame_%02d.jpg"),
        ]
        if not _run_ffmpeg(executable, first, remaining):
            raise ReviewUnavailable("frame_extraction_failed")
        interval = None
        frames = sorted(directory.glob("frame_*.jpg"))
    if not frames or len(frames) > settings["max_frames"]:
        raise ReviewUnavailable("frame_extraction_failed")
    for frame in frames:
        if not 1 <= frame.stat().st_size <= MAX_FRAME_BYTES:
            raise ReviewUnavailable("frame_byte_limit")
    return frames, interval


def _analyse_frames(
    frames: list[Path], products: list[dict], settings: dict, deadline: float
) -> dict:
    if not settings["ark_api_key"]:
        raise ReviewUnavailable("missing_ark_key")
    content = [
        {
            "type": "input_text",
            "text": VISUAL_PROMPT + json.dumps(products, ensure_ascii=True),
        }
    ]
    for frame in frames:
        content.append(
            {
                "type": "input_image",
                "image_url": "data:image/jpeg;base64,"
                + base64.b64encode(_read_bounded(frame, MAX_FRAME_BYTES)).decode(
                    "ascii"
                ),
            }
        )
    body = _canonical(
        {
            "model": settings["ark_model"],
            "thinking": {"type": "disabled"},
            "max_output_tokens": 2000,
            "input": [{"role": "user", "content": content}],
        }
    )
    response = strict_json(
        trusted_request(
            "https://ark.cn-beijing.volces.com/api/v3/responses",
            deadline=min(deadline, time.monotonic() + 60),
            max_bytes=128 * 1024,
            body=body,
            headers={
                "Authorization": "Bearer " + settings["ark_api_key"],
                "Content-Type": "application/json",
            },
        )
    )
    if (
        response.get("status") != "completed"
        or response.get("error")
        or response.get("incomplete_details")
    ):
        raise ReviewUnavailable("visual_response_incomplete")
    texts = [
        part["text"]
        for item in response.get("output", [])
        if item.get("type") == "message"
        for part in item.get("content", [])
        if part.get("type") == "output_text"
    ]
    if len(texts) != 1:
        raise ReviewUnavailable("visual_response_missing")
    return validate_visual(strict_json(texts[0]), products, len(frames))


def _visual_cache_dir(settings: dict, video_id: str) -> Path:
    digest = hashlib.sha256(
        (REVIEW_VERSION + ":" + str(settings["ark_model"])).encode("utf-8")
    ).hexdigest()[:12]
    return (
        Path(settings["cache_dir"])
        / REVIEW_VERSION
        / "video_results"
        / f"{video_id}_{digest}"
    )


def _frame_entries(frame_paths: list[Path], interval: int | None) -> list[dict]:
    entries = []
    for index, frame in enumerate(frame_paths, start=1):
        timestamp = (index - 1) * interval if interval is not None else 0.0
        entries.append(
            {
                "path": str(frame),
                "sha256": hashlib.sha256(
                    _read_bounded(frame, MAX_FRAME_BYTES)
                ).hexdigest(),
                "index": index,
                "timestamp_seconds": timestamp,
            }
        )
    return entries


def _save_visual_cache(
    detail: dict,
    products: list[dict],
    settings: dict,
    frames: list[Path],
    interval: int | None,
    result: dict,
) -> dict:
    cache_dir = _visual_cache_dir(settings, str(detail["aweme_id"]))
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cached_frames = []
    for index, frame in enumerate(frames, start=1):
        target = cache_dir / f"frame_{index:02d}.jpg"
        shutil.copyfile(frame, target)
        cached_frames.append(target)
    visual = {
        "interval_seconds": interval,
        "frames": _frame_entries(cached_frames, interval),
        "result": result,
    }
    entry = {
        "version": REVIEW_VERSION,
        "model": settings["ark_model"],
        "video_id": str(detail["aweme_id"]),
        "create_time": detail["create_time"],
        "anchors": detail.get("anchors"),
        "products": products,
        "visual": visual,
    }
    entry["hmac_sha256"] = hmac.new(
        _signing_key(Path(settings["cache_dir"]), create=True),
        _canonical(entry),
        hashlib.sha256,
    ).hexdigest()
    raw = _canonical(entry)
    if len(raw) > MAX_VISUAL_CACHE_JSON:
        raise ReviewUnavailable("evidence_byte_limit")
    temporary = cache_dir / "result.tmp"
    temporary.write_bytes(raw)
    temporary.replace(cache_dir / "result.json")
    return visual


def _load_cached_visual(
    detail: dict, products: list[dict], settings: dict
) -> dict | None:
    cache_dir = _visual_cache_dir(settings, str(detail["aweme_id"]))
    try:
        raw = _read_bounded(cache_dir / "result.json", MAX_VISUAL_CACHE_JSON)
    except (ReviewUnavailable, OSError):
        return None
    try:
        entry = strict_json(raw)
        signature = entry.pop("hmac_sha256", "")
        expected_signature = hmac.new(
            _signing_key(Path(settings["cache_dir"])),
            _canonical(entry),
            hashlib.sha256,
        ).hexdigest()
        if not isinstance(signature, str) or not hmac.compare_digest(
            signature, expected_signature
        ):
            return None
    except (ReviewUnavailable, OSError):
        return None
    if (
        entry.get("version") != REVIEW_VERSION
        or entry.get("model") != settings["ark_model"]
        or entry.get("video_id") != str(detail["aweme_id"])
        or entry.get("create_time") != detail["create_time"]
        or entry.get("anchors") != detail.get("anchors")
        or entry.get("products") != products
    ):
        return None
    try:
        visual = entry["visual"]
        frames = visual["frames"]
        interval = visual["interval_seconds"]
        if interval not in (FRAME_INTERVAL_SECONDS, None):
            return None
        if not isinstance(frames, list) or not 1 <= len(frames) <= 8:
            return None
        seen = set()
        for position, frame in enumerate(frames, start=1):
            if set(frame) != {"path", "sha256", "index", "timestamp_seconds"}:
                return None
            path = _safe_path(cache_dir, frame["path"])
            if path in seen or path.suffix != ".jpg":
                return None
            seen.add(path)
            if frame["index"] != position or (
                hashlib.sha256(_read_bounded(path, MAX_FRAME_BYTES)).hexdigest()
                != frame["sha256"]
            ):
                return None
            expected_timestamp = (
                (position - 1) * interval if interval is not None else 0.0
            )
            if abs(frame["timestamp_seconds"] - expected_timestamp) > 0.01:
                return None
        validated = validate_visual(visual["result"], products, len(frames))
        return {
            "interval_seconds": interval,
            "frames": frames,
            "result": validated,
        }
    except (ReviewUnavailable, OSError):
        return None


def _review_video(
    detail: dict,
    products: list[dict],
    client: TikHubClient,
    handle: str,
    sec_uid: str,
    settings: dict,
    deadline: float,
) -> dict:
    video_id = str(detail["aweme_id"])
    cached = _load_cached_visual(detail, products, settings)
    if cached is not None:
        return cached
    if not settings.get("ark_api_key"):
        raise ReviewUnavailable("missing_ark_key")
    if not settings.get("ffmpeg_path") and not shutil.which("ffmpeg"):
        raise ReviewUnavailable("ffmpeg_unavailable")
    fresh_detail = client.detail(video_id, handle, sec_uid)
    if fresh_detail.get("create_time") != detail["create_time"] or shopping_products(
        fresh_detail.get("anchors")
    ) != shopping_products(detail.get("anchors")):
        raise ReviewUnavailable("video_metadata_changed")
    video = fresh_detail.get("video") or {}
    urls = []
    for field in ("play_addr_h264", "play_addr", "download_addr"):
        address = video.get(field)
        if isinstance(address, dict) and isinstance(address.get("url_list"), list):
            urls.extend(
                value for value in address["url_list"] if isinstance(value, str)
            )
    if not urls:
        raise ReviewUnavailable("media_url_missing")
    media_bytes = bounded_request(
        urls[0],
        deadline=min(deadline, time.monotonic() + 45),
        max_bytes=settings["max_media_bytes"],
        redirects=3,
    )
    root = Path(settings["cache_dir"])
    work_root = root / REVIEW_VERSION / handle
    work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix="review-media-", dir=work_root) as temp:
        temp_dir = Path(temp)
        media = temp_dir / "source.mp4"
        media.write_bytes(media_bytes)
        frames, interval = _extract_frames(media, temp_dir, settings, deadline)
        result = _analyse_frames(frames, products, settings, deadline)
        return _save_visual_cache(detail, products, settings, frames, interval, result)


def _metadata_proof(detail: dict) -> dict:
    author = detail.get("author") or {}
    return {
        "aweme_id": detail.get("aweme_id"),
        "create_time": detail.get("create_time"),
        "author": {key: author.get(key) for key in ("unique_id", "sec_uid")},
        "anchors": detail.get("anchors"),
    }


def _proof_verdict(proof: dict, root: Path) -> tuple[str, str, int]:
    """Independent verdict over stored metadata; raises only on corruption."""
    handle = proof["handle"]
    start = datetime.fromisoformat(proof["window_start"])
    end = datetime.fromisoformat(proof["window_end"])
    videos = proof["videos"]
    if not isinstance(videos, list) or len(videos) > 200 or not proof.get("sec_uid"):
        raise ReviewUnavailable("invalid_proof_videos")
    unknown = False
    related_count = 0
    positive = False
    seen_videos = set()
    visual_count = 0
    for video in videos:
        detail = video["metadata"]
        verify_author(detail, handle, proof["sec_uid"])
        video_id = str(detail.get("aweme_id", ""))
        timestamp = detail.get("create_time")
        if (
            not re.fullmatch(r"\d{15,25}", video_id)
            or video_id in seen_videos
            or type(timestamp) is not int
            or not start.timestamp() <= timestamp <= end.timestamp()
        ):
            raise ReviewUnavailable("invalid_proof_video")
        seen_videos.add(video_id)
        try:
            products = related_products(shopping_products(detail.get("anchors")))
        except ReviewUnavailable:
            # Unknown/unparsed anchors can never prove or disprove a pass.
            unknown = True
            continue
        if not products:
            if video.get("visual"):
                raise ReviewUnavailable("unrelated_visual_evidence")
            continue
        related_count += 1
        visual = video.get("visual")
        if visual:
            visual_count += 1
            if visual_count > 5:
                raise ReviewUnavailable("visual_limit")
            frames = visual["frames"]
            interval = visual["interval_seconds"]
            if (
                interval not in (FRAME_INTERVAL_SECONDS, None)
                or not isinstance(frames, list)
                or not 1 <= len(frames) <= 8
            ):
                raise ReviewUnavailable("invalid_proof_frames")
            seen_frames = set()
            for position, frame in enumerate(frames, start=1):
                if set(frame) != {"path", "sha256", "index", "timestamp_seconds"}:
                    raise ReviewUnavailable("invalid_proof_frames")
                path = _safe_path(root, frame["path"])
                if path in seen_frames or path.suffix != ".jpg":
                    raise ReviewUnavailable("invalid_proof_frames")
                seen_frames.add(path)
                if frame["index"] != position or (
                    hashlib.sha256(_read_bounded(path, MAX_FRAME_BYTES)).hexdigest()
                    != frame["sha256"]
                ):
                    raise ReviewUnavailable("frame_integrity_error")
                expected_timestamp = (
                    (position - 1) * interval if interval is not None else 0.0
                )
                if abs(frame["timestamp_seconds"] - expected_timestamp) > 0.01:
                    raise ReviewUnavailable("invalid_proof_frames")
            result = validate_visual(visual["result"], products, len(frames))
            positive = positive or has_positive_visual(result)
    if unknown:
        return "needs_review", "unknown_shopping_anchor_evidence", related_count
    if related_count >= 4 and positive:
        return (
            "passed",
            "four_related_videos_and_visible_product_demonstration",
            related_count,
        )
    if related_count < 4:
        if proof.get("complete") is True:
            return "failed", "fewer_than_four_related_shopping_videos", related_count
        return "needs_review", "collection_incomplete", related_count
    return "needs_review", "sparse_visual_evidence_inconclusive", related_count


def _write_proof(proof: dict, root: Path, directory: Path) -> Path:
    signature = hmac.new(
        _signing_key(root, create=True), _canonical(proof), hashlib.sha256
    ).hexdigest()
    envelope = _canonical({"proof": proof, "hmac_sha256": signature})
    if len(envelope) > MAX_PROOF_BYTES:
        raise ReviewUnavailable("evidence_byte_limit")
    path = directory / (hashlib.sha256(envelope).hexdigest() + ".json")
    temporary = directory / "proof.tmp"
    temporary.write_bytes(envelope)
    temporary.replace(path)
    return path


EXPECTED_PROOF_KEYS = {
    "version",
    "model",
    "handle",
    "sec_uid",
    "complete",
    "stop_reason",
    "checked_at",
    "window_start",
    "window_end",
    "videos",
}


def _load_valid_proof(row: dict, settings: dict, now: datetime) -> tuple[dict, int]:
    root = Path(settings["cache_dir"])
    path = _safe_path(root, str(row.get("content_review_evidence_path") or ""))
    if not path.is_relative_to(root / REVIEW_VERSION) or path.suffix != ".json":
        raise ReviewUnavailable("evidence_version_path_invalid")
    raw = _read_bounded(path, MAX_PROOF_BYTES)
    if path.stem != hashlib.sha256(raw).hexdigest():
        raise ReviewUnavailable("proof_hash_mismatch")
    envelope = strict_json(raw)
    if set(envelope) != {"proof", "hmac_sha256"}:
        raise ReviewUnavailable("invalid_proof_envelope")
    proof = envelope["proof"]
    signature = hmac.new(
        _signing_key(root), _canonical(proof), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, envelope["hmac_sha256"]):
        raise ReviewUnavailable("proof_signature_mismatch")
    if set(proof) != EXPECTED_PROOF_KEYS:
        raise ReviewUnavailable("invalid_proof_envelope")
    if proof["version"] != REVIEW_VERSION or proof["model"] != settings["ark_model"]:
        raise ReviewUnavailable("proof_version_or_model_mismatch")
    handle = normalize_handle(row.get("creator_name"))
    if (
        not handle
        or proof["handle"] != handle
        or normalize_handle(row.get("content_review_handle")) != handle
    ):
        raise ReviewUnavailable("proof_creator_mismatch")
    checked = datetime.fromisoformat(proof["checked_at"])
    end = datetime.fromisoformat(proof["window_end"])
    start = datetime.fromisoformat(proof["window_start"])
    if any(value.tzinfo is None for value in (checked, end, start)) or not timedelta(
        0
    ) <= now - checked <= timedelta(hours=24):
        raise ReviewUnavailable("proof_expired_or_future")
    if checked != end or end - start != timedelta(days=7):
        raise ReviewUnavailable("proof_window_invalid")
    status, _reason, count = _proof_verdict(proof, root)
    if status != "passed":
        raise ReviewUnavailable("proof_does_not_pass")
    # A 24-hour cache lifetime must not extend a counted video's seven-day age.
    current_window_start = (now - timedelta(days=7)).timestamp()
    for video in proof["videos"]:
        metadata = video["metadata"]
        if metadata["create_time"] < current_window_start and related_products(
            shopping_products(metadata.get("anchors"))
        ):
            raise ReviewUnavailable("proof_videos_outside_current_window")
    if (
        row.get("content_review_status") != "passed"
        or row.get("content_review_version") != REVIEW_VERSION
        or row.get("content_review_model") != proof["model"]
        or not _is_true(row.get("content_review_complete"))
    ):
        raise ReviewUnavailable("export_proof_mismatch")
    if (
        str(row.get("content_review_related_count")) != str(count)
        or row.get("content_review_window_start") != proof["window_start"]
        or row.get("content_review_window_end") != proof["window_end"]
    ):
        raise ReviewUnavailable("export_proof_mismatch")
    expected_ids = [str(video["metadata"]["aweme_id"]) for video in proof["videos"]]
    supplied_ids = row.get("content_review_video_ids")
    if isinstance(supplied_ids, str):
        supplied_ids = json.loads(supplied_ids)
    if supplied_ids != expected_ids:
        raise ReviewUnavailable("export_video_ids_mismatch")
    return proof, count


def validate_content_review(
    row: dict, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Approval gate: require sales eligibility and authenticated, fresh local proof."""
    try:
        if not _is_true(row.get("sales_eligible")):
            return False, "sales_not_eligible"
        _load_valid_proof(row, load_content_review_settings(), _utc_now(now))
        return True, "content_review_proof_valid"
    except ReviewUnavailable as error:
        return False, str(error)
    except Exception:  # noqa: BLE001 - approval boundary must fail closed on malformed proof
        # Provider exceptions may embed signed URLs or credentials: never export them.
        return False, "content_review_proof_invalid"


def _collect_and_review(
    handle: str,
    seed_video_id: str,
    start: datetime,
    end: datetime,
    client: TikHubClient,
    root: Path,
    settings: dict,
    deadline: float,
    budget: list[int],
) -> tuple[list[dict], dict]:
    """Page the creator and interleave sparse vision; no loop-scope closures here.

    Returns (items, collected) where items are the proof video entries. The
    visual budget is a single-element list mutated in place so the cap is
    shared across every creator in the run (maximum five serial visuals).
    """
    items: list[dict] = []
    related: list[tuple[dict, dict, list[dict]]] = []
    unknown = False
    positive = False

    def on_video(detail: dict, sec_uid: str) -> bool:
        nonlocal unknown, positive
        item = {"metadata": _metadata_proof(detail), "visual": None}
        items.append(item)
        try:
            products = related_products(shopping_products(detail.get("anchors")))
        except ReviewUnavailable:
            unknown = True
            return False
        if not products:
            return False
        related.append((detail, item, products))
        if len(related) < 4:
            return False
        if budget[0] <= 0:
            return True
        budget[0] -= 1
        try:
            item["visual"] = _review_video(
                detail,
                products,
                client,
                handle,
                sec_uid,
                settings,
                deadline,
            )
        except Exception:  # noqa: BLE001 - one bad video must not abort the creator
            return False
        if has_positive_visual(item["visual"]["result"]):
            positive = True
            return True
        return False

    collected = client.collect(handle, start, end, seed_video_id, on_video=on_video)
    return items, collected


def review_creator_rows(rows: list[dict], *, now: datetime | None = None) -> list[dict]:
    """Return copied final rows; reuse creators in-batch, maximum five serial visuals.

    Optional row input content_review_seed_video_id must identify a video whose
    provider author matches creator_name. Creator IDs/sec_uids from exports are
    never accepted as identity proof. No network calls when disabled/sales fail.
    Collection and visuals are interleaved: pagination stops as soon as four
    related videos plus a positive visual are proven, or the visual budget runs
    out, or the page/video caps are hit - a partial result is preserved in the
    proof. A single failing video never aborts the whole creator.
    """
    checked = _utc_now(now).astimezone(timezone(timedelta(hours=8)))
    start = checked - timedelta(days=7)
    settings_error = False
    try:
        settings = load_content_review_settings()
    except (OSError, ValueError, TypeError, RuntimeError):
        settings_error = True
        settings = {"enabled": False, "ark_model": "", "run_timeout_seconds": 1}
    deadline = time.monotonic() + settings["run_timeout_seconds"]
    reused: dict[str, dict] = {}
    budget = [settings.get("max_visual_videos", 0)]
    output_rows = []
    for source_row in rows:
        row = dict(source_row)
        sales = _is_true(row.get("sales_eligible", row.get("eligible")))
        row["sales_eligible"] = sales
        row["sales_reason"] = row.get("sales_reason", row.get("reason", ""))
        handle = normalize_handle(row.get("creator_name"))
        content = {
            "content_review_status": "not_run",
            "content_review_reason": "sales_not_eligible",
            "content_review_handle": handle,
            "content_review_window_start": start.isoformat(),
            "content_review_window_end": checked.isoformat(),
            "content_review_related_count": 0,
            "content_review_complete": False,
            "content_review_evidence_path": "",
            "content_review_version": REVIEW_VERSION,
            "content_review_model": settings["ark_model"],
            "content_review_video_ids": [],
        }
        if sales:
            if settings_error:
                content.update(
                    content_review_status="needs_review",
                    content_review_reason="content_review_configuration_error",
                )
            elif not settings["enabled"]:
                content["content_review_reason"] = "content_review_disabled"
            elif handle in reused:
                content = dict(reused[handle])
            else:
                proof_dir = None
                try:
                    if not handle:
                        raise ReviewUnavailable("invalid_creator_handle")
                    if time.monotonic() >= deadline:
                        raise ReviewUnavailable("run_deadline")
                    # Reuse only a fully validated proof explicitly carried by this row.
                    try:
                        _load_valid_proof(row, settings, checked)
                    except Exception:  # noqa: BLE001, S110 - invalid cache is a miss; never log secrets
                        pass
                    else:
                        content = {key: row[key] for key in content}
                        reused[handle] = dict(content)
                        row.update(content, eligible=True)
                        output_rows.append(row)
                        continue
                    client = TikHubClient(settings, deadline)
                    root = Path(settings["cache_dir"])
                    parent = root / REVIEW_VERSION / handle
                    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if not parent.resolve().is_relative_to(root.resolve()):
                        raise ReviewUnavailable("evidence_path_outside_cache")
                    proof_dir = Path(tempfile.mkdtemp(prefix="review-", dir=parent))
                    items, collected = _collect_and_review(
                        handle,
                        str(row.get("content_review_seed_video_id") or ""),
                        start,
                        checked,
                        client,
                        root,
                        settings,
                        deadline,
                        budget,
                    )
                    proof = {
                        "version": REVIEW_VERSION,
                        "model": settings["ark_model"],
                        "handle": handle,
                        "sec_uid": collected["sec_uid"],
                        "complete": collected["complete"],
                        "stop_reason": collected["stop_reason"],
                        "checked_at": checked.isoformat(),
                        "window_start": start.isoformat(),
                        "window_end": checked.isoformat(),
                        "videos": items,
                    }
                    status, reason, related_count = _proof_verdict(proof, root)
                    evidence_path = _write_proof(proof, root, proof_dir)
                    content.update(
                        content_review_status=status,
                        content_review_reason=reason,
                        content_review_related_count=related_count,
                        content_review_complete=True,
                        content_review_evidence_path=str(evidence_path),
                        content_review_video_ids=[
                            str(item["metadata"]["aweme_id"]) for item in items
                        ],
                    )
                except Exception as error:  # noqa: BLE001 - isolate and redact provider failures
                    content.update(
                        content_review_status="needs_review",
                        content_review_reason=str(error)
                        if isinstance(error, ReviewUnavailable)
                        else "content_review_unavailable",
                    )
                    if proof_dir is not None:
                        shutil.rmtree(proof_dir, ignore_errors=True)
                reused[handle] = dict(content)
        row.update(content)
        row["eligible"] = sales and content["content_review_status"] == "passed"
        output_rows.append(row)
    return output_rows
