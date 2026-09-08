import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.lib import app_config
from scripts.lib import creator_video_contract as contract
from scripts.lib import creator_video_review as review

NOW = datetime(2026, 9, 6, tzinfo=UTC)
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tiktok_shopping_anchors.json"
FIXTURE_ANCHORS = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["anchors"]

# Parsed contract of the live fixture root level-1 name "Womenswear Underwear".
FIXTURE_PRODUCT = {
    "product_id": "1729840619138750658",
    "title": "Cider Sequin V-Neck Halter Ruffled Hem Oversized Mini Dress With Scarf",
    "category": "Womenswear & Underwear",
}

PRODUCT = {
    "product_id": "product-1",
    "title": "Bra",
    "category": "Womenswear & Underwear",
}


def observation(frame=1, **values):
    return {
        "frame": frame,
        "product_id": "product-1",
        "body_worn": False,
        "face_visible": False,
        "holding": False,
        "description": "An identifiable anchored item",
        **values,
    }


def shopping_anchor(product: dict, root="Womenswear Underwear") -> list[dict]:
    """Verified TikTok Shop type-35/33 anchor shape (see fixture)."""
    child_extra = {
        "product_id": int(product["product_id"])
        if str(product["product_id"]).isdigit()
        else product["product_id"],
        "source": "TikTok Shop",
        "title": product["title"],
        "categories": [
            {"category_id": 601152, "category_name": root, "level": 1, "parent_id": 0}
        ],
    }
    child = {
        "type": 33,
        "component_key": "anchor_shop",
        "id": str(child_extra["product_id"]),
        "extra": json.dumps(child_extra),
    }
    return [
        {
            "type": 35,
            "component_key": "anchor_complex_shop",
            "extra": json.dumps([child]),
        }
    ]


def detail(number=1, *, age=1, handle="alice", anchors=None):
    return {
        "aweme_id": str(7000000000000000000 + number),
        "create_time": int((NOW - timedelta(days=age)).timestamp()),
        "author": {"unique_id": handle, "sec_uid": "verified-sec"},
        "anchors": [] if anchors is None else anchors,
    }


def visual_with(frame_path, **observation_values):
    return {
        "result": {
            "observations": [observation(**observation_values)],
            "uncertainties": [],
        },
        "frames": [
            {
                "path": str(frame_path),
                "sha256": hashlib.sha256(frame_path.read_bytes()).hexdigest(),
                "index": 1,
                "timestamp_seconds": 0.0,
            }
        ],
        "interval_seconds": None,
    }


@pytest.fixture
def settings(monkeypatch, tmp_path):
    values = {
        "enabled": True,
        "cache_dir": str(tmp_path / "cache"),
        "ark_model": "test-model",
        "ark_api_key": "test-key",
        "tikhub_api_key": "test-key",
        "max_visual_videos": 5,
        "max_frames": 8,
        "max_media_bytes": 1024,
        "max_pages": 10,
        "max_videos": 200,
        "run_timeout_seconds": 60,
        "ffmpeg_path": "test-ffmpeg",
    }
    monkeypatch.setattr(review, "load_content_review_settings", lambda: dict(values))
    return values


def patch_provider(
    monkeypatch,
    count=4,
    *,
    positive=True,
    complete=True,
    unknown=None,
    unknown_first=False,
    visual=None,
):
    """Synthetic orchestration adapter with real TikHub client surface."""
    calls = {"collect": 0, "visual": 0}
    details = [
        detail(number, anchors=shopping_anchor(PRODUCT)) for number in range(count)
    ]
    if unknown is not None:
        if unknown_first:
            details.insert(0, unknown)
        else:
            details.append(unknown)

    def collect(self, handle, start, end, seed, on_video=None):
        calls["collect"] += 1
        seen = []
        for item in details:
            seen.append(item)
            if on_video is not None and on_video(item, "verified-sec"):
                break
        return {
            "handle": handle,
            "sec_uid": "verified-sec",
            "videos": seen,
            "complete": complete,
            "stop_reason": "complete" if complete else "stopped",
        }

    def default_visual(detail, products, client, handle, sec_uid, settings, deadline):
        calls["visual"] += 1
        directory = Path(settings["cache_dir"]) / "tmp" / str(detail["aweme_id"])
        directory.mkdir(parents=True, exist_ok=True)
        frame = directory / "frame_01.jpg"
        frame.write_bytes(b"synthetic frame bytes")
        return visual_with(frame, body_worn=positive)

    monkeypatch.setattr(review.TikHubClient, "collect", collect)
    monkeypatch.setattr(review, "_review_video", visual or default_visual)
    return calls


def test_shopping_products_from_live_fixture():
    assert contract.shopping_products(FIXTURE_ANCHORS) == [FIXTURE_PRODUCT]
    assert contract.related_products(contract.shopping_products(FIXTURE_ANCHORS)) == [
        FIXTURE_PRODUCT
    ]


def test_off_whitelist_root_is_known_but_not_related():
    products = contract.shopping_products(
        shopping_anchor(PRODUCT, root="Muslim Fashion")
    )
    assert products == [{**PRODUCT, "category": "Muslim Fashion"}]
    assert contract.related_products(products) == []


@pytest.mark.parametrize(
    "anchors",
    [
        {},
        [{"type": 35, "keyword": "Bra", "id": "123"}],
        [{"title": "#TikTokShop"}],
        [{"type": 35, "component_key": "anchor_complex_shop", "extra": "not-json"}],
        [
            {
                "type": 35,
                "component_key": "anchor_complex_shop",
                "extra": json.dumps(
                    [{"type": 33, "component_key": "anchor_shop", "id": "9"}]
                ),
            }
        ],
    ],
)
def test_unknown_anchor_shapes_raise(anchors):
    with pytest.raises(review.ReviewUnavailable, match="contract_unverified"):
        contract.shopping_products(anchors)


def test_empty_complex_shop_children_is_non_shopping():
    anchors = [{"type": 35, "component_key": "anchor_complex_shop", "extra": "[]"}]
    assert contract.shopping_products(anchors) == []


def test_missing_product_category_is_unknown():
    child_extra = {
        "product_id": 123,
        "source": "TikTok Shop",
        "title": "Mystery item",
        "categories": [],
    }
    anchors = [
        {
            "type": 35,
            "component_key": "anchor_complex_shop",
            "extra": json.dumps(
                [
                    {
                        "type": 33,
                        "component_key": "anchor_shop",
                        "id": "123",
                        "extra": json.dumps(child_extra),
                    }
                ]
            ),
        }
    ]
    with pytest.raises(review.ReviewUnavailable, match="product_category_unknown"):
        contract.shopping_products(anchors)
    child_extra["categories"] = [{"category_name": "Womenswear Underwear", "level": 3}]
    anchors[0]["extra"] = json.dumps(
        [
            {
                "type": 33,
                "component_key": "anchor_shop",
                "id": "123",
                "extra": json.dumps(child_extra),
            }
        ]
    )
    with pytest.raises(review.ReviewUnavailable, match="product_category_unknown"):
        contract.shopping_products(anchors)


def test_same_frame_rule_and_strict_booleans():
    result = {
        "observations": [
            observation(1, face_visible=True),
            observation(2, holding=True),
        ],
        "uncertainties": [],
    }
    contract.validate_visual(result, [PRODUCT], 2)
    assert not contract.has_positive_visual(result)
    result["observations"] = [observation(face_visible=True, holding=True)]
    assert contract.has_positive_visual(contract.validate_visual(result, [PRODUCT], 1))
    result["observations"][0]["holding"] = "false"
    with pytest.raises(review.ReviewUnavailable, match="boolean"):
        contract.validate_visual(result, [PRODUCT], 1)


@pytest.mark.parametrize(
    "change",
    [
        {"frame": 0},
        {"frame": 9},
        {"product_id": "unrelated"},
        {"description": ""},
        {"extra": True},
    ],
)
def test_invalid_visual_contract(change):
    with pytest.raises(review.ReviewUnavailable):
        contract.validate_visual(
            {"observations": [observation(**change)], "uncertainties": []}, [PRODUCT], 8
        )


def test_positive_with_uncertainty_is_not_pass():
    assert not contract.has_positive_visual(
        {
            "observations": [observation(body_worn=True)],
            "uncertainties": ["ambiguous match"],
        }
    )


def test_disabled_or_sales_failure_is_closed_and_no_network(settings, monkeypatch):
    monkeypatch.setattr(
        review.TikHubClient, "collect", lambda *args: pytest.fail("unexpected network")
    )
    source = {"creator_name": "alice", "eligible": False, "reason": "sales failure"}
    output = review.review_creator_rows([source], now=NOW)[0]
    assert output["content_review_status"] == "not_run"
    assert output["sales_reason"] == "sales failure"
    assert source == {
        "creator_name": "alice",
        "eligible": False,
        "reason": "sales failure",
    }
    settings["enabled"] = False
    output = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert output["eligible"] is False
    assert output["content_review_reason"] == "content_review_disabled"


def test_settings_failure_is_needs_review(settings, monkeypatch):
    def broken():
        raise RuntimeError("configured incorrectly")

    monkeypatch.setattr(review, "load_content_review_settings", broken)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["content_review_status"] == "needs_review"
    assert row["content_review_reason"] == "content_review_configuration_error"


def test_pass_proof_roundtrip_and_creator_reuse(settings, monkeypatch):
    calls = patch_provider(monkeypatch)
    rows = review.review_creator_rows(
        [
            {"creator_name": "@Alice", "eligible": True, "reason": "sales ok"},
            {"creator_name": "alice", "eligible": True},
        ],
        now=NOW,
    )
    assert calls == {"collect": 1, "visual": 1}
    assert rows[0]["eligible"] is True
    assert rows[0]["sales_eligible"] is True
    assert rows[0]["sales_reason"] == "sales ok"
    assert rows[0]["content_review_related_count"] == 4
    assert review.validate_content_review(rows[0], now=NOW) == (
        True,
        "content_review_proof_valid",
    )
    csv_row = dict(
        rows[0],
        sales_eligible="True",
        content_review_complete="True",
        content_review_related_count="4",
        content_review_video_ids=json.dumps(rows[0]["content_review_video_ids"]),
    )
    assert review.validate_content_review(csv_row, now=NOW)[0]
    assert review.review_creator_rows([rows[0]], now=NOW)[0]["eligible"]
    assert calls == {"collect": 1, "visual": 1}


@pytest.mark.parametrize("count", [0, 3, 4])
def test_count_and_sparse_negative(settings, monkeypatch, count):
    calls = patch_provider(monkeypatch, count=count, positive=False)
    output = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert not output["eligible"]
    assert output["content_review_status"] == (
        "failed" if count < 4 else "needs_review"
    )
    assert calls["visual"] == (0 if count < 4 else 1)


def test_partial_collection_passes_on_lower_bound(settings, monkeypatch):
    patch_provider(monkeypatch, count=5, positive=True, complete=False)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["eligible"] is True
    assert row["content_review_status"] == "passed"
    proof = json.loads(Path(row["content_review_evidence_path"]).read_text())["proof"]
    assert proof["complete"] is False
    assert proof["stop_reason"] == "stopped"
    assert review.validate_content_review(row, now=NOW)[0]


def test_incomplete_collection_below_four_is_not_failed(settings, monkeypatch):
    patch_provider(monkeypatch, count=3, positive=False, complete=False)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["content_review_status"] == "needs_review"
    assert row["content_review_reason"] == "collection_incomplete"


def test_unknown_anchor_shape_needs_review_even_when_complete(settings, monkeypatch):
    unknown = detail(99, age=3, anchors=[{"type": 35, "keyword": "unknown"}])
    patch_provider(
        monkeypatch,
        count=3,
        positive=False,
        complete=True,
        unknown=unknown,
        unknown_first=True,
    )
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["content_review_status"] == "needs_review"
    assert row["content_review_reason"] == "unknown_shopping_anchor_evidence"
    assert row["content_review_related_count"] == 3

    patch_provider(
        monkeypatch,
        count=4,
        positive=True,
        complete=True,
        unknown=unknown,
        unknown_first=True,
    )
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["content_review_status"] == "needs_review"
    assert not row["eligible"]


def test_single_video_failure_does_not_abort_creator(settings, monkeypatch):
    attempts = {"count": 0}

    def flaky_visual(detail, products, client, handle, sec_uid, settings, deadline):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("one video failed transiently")
        directory = Path(settings["cache_dir"]) / "tmp" / str(detail["aweme_id"])
        directory.mkdir(parents=True, exist_ok=True)
        frame = directory / "frame_01.jpg"
        frame.write_bytes(b"synthetic frame bytes")
        return visual_with(frame, body_worn=True)

    patch_provider(monkeypatch, count=5, visual=flaky_visual)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert attempts["count"] == 2
    assert row["eligible"] is True
    assert row["content_review_status"] == "passed"


def test_global_visual_budget(settings, monkeypatch):
    calls = patch_provider(monkeypatch, count=10, positive=False)
    rows = review.review_creator_rows(
        [
            {"creator_name": "alice", "eligible": True},
            {"creator_name": "bob", "eligible": True},
        ],
        now=NOW,
    )
    assert calls["visual"] == 5
    assert all(row["content_review_status"] == "needs_review" for row in rows)


def test_visual_cache_reused_across_windows(settings, monkeypatch):
    counters = {"frames": 0, "analyse": 0}

    def synthetic_collect(self, handle, start, end, seed, on_video=None):
        items = [
            detail(number, anchors=shopping_anchor(PRODUCT)) for number in range(4)
        ]
        for item in items:
            if on_video is not None and on_video(item, "verified-sec"):
                break
        return {
            "handle": handle,
            "sec_uid": "verified-sec",
            "videos": items,
            "complete": True,
            "stop_reason": "complete",
        }

    def synthetic_detail(self, video_id, handle, sec_uid=""):
        fresh = {
            "aweme_id": video_id,
            "create_time": int((NOW - timedelta(days=1)).timestamp()),
            "author": {"unique_id": handle, "sec_uid": "verified-sec"},
            "anchors": shopping_anchor(PRODUCT),
            "video": {
                "play_addr_h264": {"url_list": ["https://cdn.example/video.mp4"]}
            },
        }
        return fresh

    def synthetic_frames(media, directory, settings, deadline):
        counters["frames"] += 1
        frame = directory / "frame_01.jpg"
        frame.write_bytes(b"frame bytes")
        return [frame], 10

    def synthetic_analyse(frames, products, settings, deadline):
        counters["analyse"] += 1
        return {"observations": [observation(body_worn=True)], "uncertainties": []}

    monkeypatch.setattr(review.TikHubClient, "collect", synthetic_collect)
    monkeypatch.setattr(review.TikHubClient, "detail", synthetic_detail)
    monkeypatch.setattr(review, "bounded_request", lambda *args, **kwargs: b"mp4")
    monkeypatch.setattr(review, "_extract_frames", synthetic_frames)
    monkeypatch.setattr(review, "_analyse_frames", synthetic_analyse)

    first = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert counters == {"frames": 1, "analyse": 1}
    second = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW + timedelta(hours=1)
    )[0]
    assert counters == {"frames": 1, "analyse": 1}
    assert first["eligible"] is True and second["eligible"] is True


def test_frame_entries_timestamps_and_interval(settings, tmp_path):
    first = tmp_path / "frame_01.jpg"
    second = tmp_path / "frame_02.jpg"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    entries = review._frame_entries([first, second], 10)
    assert [(frame["index"], frame["timestamp_seconds"]) for frame in entries] == [
        (1, 0.0),
        (2, 10.0),
    ]
    entries = review._frame_entries([first], None)
    assert entries[0]["timestamp_seconds"] == 0.0


def test_short_clip_falls_back_to_first_frame(settings, tmp_path, monkeypatch):
    calls = {"count": 0}

    def run(command, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return type("Result", (), {"returncode": 0})()
        (tmp_path / "frame_01.jpg").write_bytes(b"frame")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(review.subprocess, "run", run)
    frames, interval = review._extract_frames(
        tmp_path / "source.mp4", tmp_path, settings, time.monotonic() + 60
    )
    assert interval is None
    assert [frame.name for frame in frames] == ["frame_01.jpg"]


def test_ffmpeg_local_only_bounded_and_no_path_changes(settings, tmp_path, monkeypatch):
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        (tmp_path / "frame_01.jpg").write_bytes(b"frame")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(review.subprocess, "run", run)
    before = dict(os.environ)
    frames, interval = review._extract_frames(
        tmp_path / "source.mp4", tmp_path, settings, time.monotonic() + 60
    )
    command, options = commands[0]
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command[command.index("-format_whitelist") + 1] == "mov"
    assert "-an" in command and "-frames:v" in command and "-max_alloc" in command
    assert options["timeout"] <= 45
    assert interval == 10
    assert len(frames) == 1
    assert os.environ == before


@pytest.mark.parametrize(
    "mutation",
    [
        {"creator_name": "bob"},
        {"content_review_handle": "bob"},
        {"sales_eligible": False},
        {"content_review_version": "old"},
        {"content_review_model": "other"},
        {"content_review_complete": "false"},
        {"content_review_related_count": 99},
        {"content_review_video_ids": []},
        {"content_review_window_start": "2020-01-01"},
    ],
)
def test_export_tampering_rejected(settings, monkeypatch, mutation):
    patch_provider(monkeypatch)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    row.update(mutation)
    assert not review.validate_content_review(row, now=NOW)[0]


def test_proof_age_model_and_frame_integrity(settings, monkeypatch):
    patch_provider(monkeypatch)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert not review.validate_content_review(
        row, now=NOW + timedelta(hours=24, seconds=1)
    )[0]
    assert not review.validate_content_review(row, now=NOW - timedelta(seconds=1))[0]
    settings["ark_model"] = "changed"
    assert not review.validate_content_review(row, now=NOW)[0]
    settings["ark_model"] = "test-model"
    envelope = json.loads(Path(row["content_review_evidence_path"]).read_text())
    with_visual = next(
        video for video in envelope["proof"]["videos"] if video["visual"]
    )
    frame = Path(with_visual["visual"]["frames"][0]["path"])
    frame.write_bytes(b"tampered")
    assert review.validate_content_review(row, now=NOW) == (
        False,
        "frame_integrity_error",
    )


def test_forged_proof_hash_is_not_enough(settings, monkeypatch):
    patch_provider(monkeypatch)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    path = Path(row["content_review_evidence_path"])
    envelope = json.loads(path.read_text())
    envelope["proof"]["complete"] = False
    raw = review._canonical(envelope)
    forged = path.with_name(hashlib.sha256(raw).hexdigest() + ".json")
    forged.write_bytes(raw)
    row["content_review_evidence_path"] = str(forged)
    assert review.validate_content_review(row, now=NOW) == (
        False,
        "proof_signature_mismatch",
    )


def test_external_proof_and_symlink_rejected(settings, tmp_path):
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    root = Path(settings["cache_dir"])
    root.mkdir()
    link = root / "link.json"
    link.symlink_to(outside)
    for path in (outside, link):
        valid, _reason = review.validate_content_review(
            {"sales_eligible": True, "content_review_evidence_path": str(path)}, now=NOW
        )
        assert not valid


def test_provider_error_redacted(settings, monkeypatch):
    def fail(*args):
        raise RuntimeError(
            "Authorization Bearer SECRET https://signed.example?token=SECRET"
        )

    monkeypatch.setattr(review.TikHubClient, "collect", fail)
    row = review.review_creator_rows(
        [{"creator_name": "alice", "eligible": True}], now=NOW
    )[0]
    assert row["content_review_status"] == "needs_review"
    assert "SECRET" not in json.dumps(row)


def test_config_project_first_explicit_external_whitelist_no_pollution(
    tmp_path, monkeypatch
):
    project = tmp_path / ".env"
    external = tmp_path / "external.env"
    project.write_text("ARK_API_KEY=project-key\nCONTENT_REVIEW_ENABLED=true\n")
    external.write_text(
        "ARK_API_KEY=external-key\nTIKHUB_API_KEY=external-tikhub\nPATH=unsafe\nCONTENT_REVIEW_MAX_VISUAL_VIDEOS=99\n"
    )
    monkeypatch.setattr(app_config, "DEFAULT_ENV_PATH", project)
    monkeypatch.setattr(
        app_config,
        "load_raw_config",
        lambda: {"content_review": {"external_env_path": str(external)}},
    )
    monkeypatch.delenv("TIKHUB_API_KEY", raising=False)
    before = dict(os.environ)
    result = app_config.load_content_review_settings()
    assert result["ark_api_key"] == "project-key"
    assert result["tikhub_api_key"] == "external-tikhub"
    assert result["max_visual_videos"] == 5
    assert result["enabled"] is True
    assert os.environ == before
    monkeypatch.setattr(
        app_config,
        "load_raw_config",
        lambda: {"content_review": {"max_visual_videos": 6}},
    )
    with pytest.raises(app_config.AppConfigError):
        app_config.load_content_review_settings()


def test_config_defaults_enabled(monkeypatch, tmp_path):
    monkeypatch.setattr(app_config, "DEFAULT_ENV_PATH", tmp_path / "missing.env")
    monkeypatch.setattr(app_config, "load_raw_config", dict)
    for key in (
        "TIKHUB_API_KEY",
        "ARK_API_KEY",
        "ARK_MODEL",
        "FFMPEG_PATH",
        "CONTENT_REVIEW_ENABLED",
        "CONTENT_REVIEW_CACHE_DIR",
        "CONTENT_REVIEW_MAX_VISUAL_VIDEOS",
        "CONTENT_REVIEW_EXTERNAL_ENV_PATH",
    ):
        monkeypatch.delenv(key, raising=False)
    resolved = app_config.load_content_review_settings()
    assert resolved["enabled"] is True
    assert resolved["max_visual_videos"] == 5
    assert resolved["max_pages"] == 10
