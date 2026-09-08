"""An unexpired audit must not keep counting videos outside the rolling window."""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.lib import creator_video_review as review
from scripts.lib.creator_video_contract import REVIEW_VERSION


def test_counted_video_aging_out_invalidates_proof(tmp_path, monkeypatch):
    checked = datetime(2026, 9, 6, tzinfo=timezone.utc)
    root = tmp_path / "cache"
    directory = root / REVIEW_VERSION / "alice" / "review-test"
    directory.mkdir(parents=True)
    frame = directory / "frame_01.jpg"
    frame.write_bytes(b"offline test frame")
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "tiktok_shopping_anchors.json").read_text()
    )
    videos = [
        {
            "metadata": {
                "aweme_id": str(7000000000000000000 + index),
                "create_time": int((checked - timedelta(days=6, hours=23)).timestamp()),
                "author": {"unique_id": "alice", "sec_uid": "verified-sec"},
                "anchors": fixture["anchors"],
            },
            "visual": None,
        }
        for index in range(4)
    ]
    videos[0]["visual"] = {
        "interval_seconds": 10,
        "frames": [{
            "path": str(frame), "index": 1, "timestamp_seconds": 0.0,
            "sha256": hashlib.sha256(frame.read_bytes()).hexdigest(),
        }],
        "result": {
            "observations": [{
                "frame": 1, "product_id": "1729840619138750658",
                "body_worn": True, "face_visible": False, "holding": False,
                "description": "The anchored dress is worn on the presenter.",
            }],
            "uncertainties": [],
        },
    }
    proof = {
        "version": REVIEW_VERSION, "model": "offline-model", "handle": "alice",
        "sec_uid": "verified-sec", "complete": True, "stop_reason": "exhausted",
        "checked_at": checked.isoformat(), "window_end": checked.isoformat(),
        "window_start": (checked - timedelta(days=7)).isoformat(), "videos": videos,
    }
    evidence = review._write_proof(proof, root, directory)
    row = {
        "creator_name": "alice", "sales_eligible": True,
        "content_review_status": "passed", "content_review_handle": "alice",
        "content_review_version": REVIEW_VERSION, "content_review_model": "offline-model",
        "content_review_complete": True, "content_review_related_count": 4,
        "content_review_evidence_path": str(evidence),
        "content_review_window_start": proof["window_start"],
        "content_review_window_end": proof["window_end"],
        "content_review_video_ids": [video["metadata"]["aweme_id"] for video in videos],
    }
    monkeypatch.setattr(review, "load_content_review_settings", lambda: {
        "cache_dir": str(root), "ark_model": "offline-model",
    })
    assert review.validate_content_review(row, now=checked)[0]
    valid, reason = review.validate_content_review(row, now=checked + timedelta(hours=2))
    assert not valid
    assert reason == "proof_videos_outside_current_window"


def test_edited_video_cache_is_not_signed_into_new_pass(tmp_path):
    settings = {"cache_dir": str(tmp_path / "cache"), "ark_model": "offline-model"}
    frame = tmp_path / "frame_01.jpg"
    frame.write_bytes(b"offline frame")
    detail = {"aweme_id": "7000000000000000001", "create_time": 100, "anchors": []}
    products = [{"product_id": "123", "title": "Dress", "category": "Womenswear & Underwear"}]
    result = {
        "observations": [{
            "frame": 1, "product_id": "123", "body_worn": False,
            "face_visible": False, "holding": False, "description": "No visible demonstration.",
        }],
        "uncertainties": [],
    }
    review._save_visual_cache(detail, products, settings, [frame], 10, result)
    assert review._load_cached_visual(detail, products, settings) is not None
    cache_file = review._visual_cache_dir(settings, detail["aweme_id"]) / "result.json"
    edited = json.loads(cache_file.read_text())
    edited["visual"]["result"]["observations"][0]["body_worn"] = True
    cache_file.write_text(json.dumps(edited))
    assert review._load_cached_visual(detail, products, settings) is None
