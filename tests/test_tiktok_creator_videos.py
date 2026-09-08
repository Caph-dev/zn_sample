import socket
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from scripts.lib import tiktok_creator_videos as metadata

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def video(number=1, *, age=1, handle="alice", anchors=None):
    return {
        "aweme_id": str(7000000000000000000 + number),
        "create_time": int((NOW - timedelta(days=age)).timestamp()),
        "author": {"unique_id": handle, "sec_uid": "verified-sec"},
        "anchors": [] if anchors is None else anchors,
    }


def client(monkeypatch, pages, **overrides):
    settings = {
        "tikhub_api_key": "test",
        "max_pages": 10,
        "max_videos": 200,
        **overrides,
    }
    instance = metadata.TikHubClient(settings, time.monotonic() + 60)
    requests = []
    responses = iter(pages)

    def fetch(endpoint, parameters):
        requests.append((endpoint, parameters))
        return next(responses)

    monkeypatch.setattr(instance, "fetch", fetch)
    return instance, requests


def test_username_contract_identity_pagination_and_pinned_old_video(monkeypatch):
    instance, requests = client(
        monkeypatch,
        [
            {
                "aweme_list": [video(1, age=30), video(2)],
                "has_more": 1,
                "max_cursor": 100,
            },
            {"aweme_list": [video(3), video(4, age=7)], "has_more": 0},
        ],
    )
    result = instance.collect("alice", NOW - timedelta(days=7), NOW)
    assert len(result["videos"]) == 3
    assert result["complete"] is True
    assert result["stop_reason"] == "complete"
    assert requests[0][1]["unique_id"] == "alice"
    assert requests[1][1]["sec_user_id"] == "verified-sec"
    assert requests[1][1]["max_cursor"] == 100
    assert "cursor" not in requests[1][1]


@pytest.mark.parametrize(
    "page,reason",
    [
        ({"aweme_list": [video(handle="wrong")], "has_more": 0}, "identity_mismatch"),
        ({"aweme_list": [], "has_more": 0}, "identity_unresolved"),
        ({"aweme_list": [video()], "has_more": "0"}, "invalid_pagination"),
        ({"aweme_list": [video(age=-2)], "has_more": 0}, "invalid_video_metadata"),
        ({"aweme_list": None, "has_more": 0}, "invalid_video_list"),
    ],
)
def test_metadata_errors_never_complete(monkeypatch, page, reason):
    instance, _requests = client(monkeypatch, [page])
    with pytest.raises(metadata.ReviewUnavailable, match=reason):
        instance.collect("alice", NOW - timedelta(days=7), NOW)


def test_pagination_and_video_caps_are_partial_not_failures(monkeypatch):
    instance, _requests = client(
        monkeypatch,
        [{"aweme_list": [video()], "has_more": 1, "max_cursor": 1}],
        max_pages=1,
    )
    result = instance.collect("alice", NOW - timedelta(days=7), NOW)
    assert result["complete"] is False
    assert result["stop_reason"] == "pagination_limit"
    assert len(result["videos"]) == 1

    instance, _requests = client(
        monkeypatch, [{"aweme_list": [video(), video(2)], "has_more": 0}], max_videos=1
    )
    result = instance.collect("alice", NOW - timedelta(days=7), NOW)
    assert result["complete"] is False
    assert result["stop_reason"] == "video_limit"
    assert len(result["videos"]) == 1

    instance, _requests = client(
        monkeypatch, [{"aweme_list": [video()], "has_more": 1, "max_cursor": 0}]
    )
    result = instance.collect("alice", NOW - timedelta(days=7), NOW)
    assert result["complete"] is False
    assert result["stop_reason"] == "pagination_stalled"
    assert len(result["videos"]) == 1


def test_on_video_callback_stops_collection(monkeypatch):
    instance, _requests = client(
        monkeypatch, [{"aweme_list": [video(), video(2)], "has_more": 0}]
    )
    called = []

    def handler(detail, sec_uid):
        called.append(str(detail["aweme_id"]))
        return True

    result = instance.collect("alice", NOW - timedelta(days=7), NOW, on_video=handler)
    assert called == [video()["aweme_id"]]
    assert result["stop_reason"] == "stopped"
    assert result["complete"] is False
    assert len(result["videos"]) == 1


def test_duplicate_video_dedup_and_conflict(monkeypatch):
    instance, _requests = client(
        monkeypatch, [{"aweme_list": [video(), video()], "has_more": 0}]
    )
    assert len(instance.collect("alice", NOW - timedelta(days=7), NOW)["videos"]) == 1
    instance, _requests = client(
        monkeypatch, [{"aweme_list": [video(), video(age=2)], "has_more": 0}]
    )
    with pytest.raises(metadata.ReviewUnavailable, match="conflicting"):
        instance.collect("alice", NOW - timedelta(days=7), NOW)


def test_seed_identity_is_verified(monkeypatch):
    instance, _requests = client(
        monkeypatch, [{"aweme_detail": video(handle="impostor")}]
    )
    with pytest.raises(metadata.ReviewUnavailable, match="identity_mismatch"):
        instance.collect("alice", NOW - timedelta(days=7), NOW, video()["aweme_id"])


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/video",
        "file:///tmp/movie",
        "https://user:secret@example.com/a",
        "https://example.com:8443/a",
        "https://example.com/a#fragment",
    ],
)
def test_unsafe_url_forms_rejected(url):
    with pytest.raises(metadata.ReviewUnavailable, match="unsafe"):
        metadata.public_addresses(url)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "::1",
        "::ffff:127.0.0.1",
        "100.64.0.1",
    ],
)
def test_private_dns_answers_rejected(monkeypatch, address):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))
        ],
    )
    with pytest.raises(metadata.ReviewUnavailable, match="non_public"):
        metadata.public_addresses("https://cdn.example/media")


def test_json_duplicate_and_nonfinite_rejected():
    for value in ('{"code":200,"code":500}', '{"value":NaN}', "[]"):
        with pytest.raises(metadata.ReviewUnavailable):
            metadata.strict_json(value)


def test_strict_json_value_allows_arrays():
    assert metadata.strict_json_value('[{"a":1}]') == [{"a": 1}]
    with pytest.raises(metadata.ReviewUnavailable, match="invalid_json"):
        metadata.strict_json_value("{broken")


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = list(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_bytes(self):
        yield from self._chunks


class _FakeClient:
    def __init__(self, *, status=200, headers=None, chunks=(), error=None, **kwargs):
        self.status = status
        self.headers = headers or {}
        self.chunks = list(chunks)
        self.error = error
        self.kwargs = kwargs
        self.method = None
        self.url = None
        self.request_headers = None
        self.request_body = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def stream(self, method, url, headers=None, content=None):
        self.method = method
        self.url = url
        self.request_headers = headers
        self.request_body = content
        if self.error is not None:
            raise self.error
        return _FakeResponse(self.status, self.headers, self.chunks)


def test_trusted_request_bounded_get(monkeypatch):
    fake = _FakeClient(chunks=[b'{"ok":1}'])
    captured = {}

    def make_client(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr(metadata.httpx, "Client", make_client)
    raw = metadata.trusted_request(
        "https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_one_video_v2?aweme_id=1",
        deadline=time.monotonic() + 20,
        max_bytes=1024,
        headers={"Authorization": "Bearer test"},
    )
    assert raw == b'{"ok":1}'
    assert captured["follow_redirects"] is False
    assert fake.method == "GET"
    assert fake.request_headers["Authorization"] == "Bearer test"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/api",
        "https://api.tikhub.io.evil.example/",
        "http://api.tikhub.io/",
        "https://api.tikhub.io:8443/",
    ],
)
def test_trusted_host_allowlist(monkeypatch, url):
    def never_called(**kwargs):
        raise AssertionError("client must not be created for untrusted host")

    monkeypatch.setattr(metadata.httpx, "Client", never_called)
    with pytest.raises(metadata.ReviewUnavailable, match="unsafe_trusted_url"):
        metadata.trusted_request(url, deadline=time.monotonic() + 20, max_bytes=1024)


def test_trusted_request_redirect_rejected(monkeypatch):
    fake = _FakeClient(status=302, headers={"location": "https://evil.example/x"})
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: fake)
    with pytest.raises(metadata.ReviewUnavailable, match="redirect_rejected"):
        metadata.trusted_request(
            "https://ark.cn-beijing.volces.com/api/v3/responses",
            deadline=time.monotonic() + 20,
            max_bytes=1024,
        )


def test_trusted_request_byte_limits_via_header_and_stream(monkeypatch):
    fake = _FakeClient(headers={"content-length": "99999"}, chunks=[b"12345"])
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: fake)
    with pytest.raises(metadata.ReviewUnavailable, match="response_byte_limit"):
        metadata.trusted_request(
            "https://ark.cn-beijing.volces.com/api/v3/responses",
            deadline=time.monotonic() + 20,
            max_bytes=10,
        )
    fake = _FakeClient(chunks=[b"12345", b"67890", b"11"])
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: fake)
    with pytest.raises(metadata.ReviewUnavailable, match="response_byte_limit"):
        metadata.trusted_request(
            "https://ark.cn-beijing.volces.com/api/v3/responses",
            deadline=time.monotonic() + 20,
            max_bytes=10,
        )


def test_trusted_request_maps_httpx_errors_without_uris(monkeypatch):
    fake = _FakeClient(error=httpx.ReadTimeout("boom"))
    monkeypatch.setattr(metadata.httpx, "Client", lambda **kwargs: fake)
    with pytest.raises(metadata.ReviewUnavailable, match="provider_unavailable"):
        metadata.trusted_request(
            "https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_one_video_v2",
            deadline=time.monotonic() + 20,
            max_bytes=1024,
        )


def test_trusted_request_deadline_before_network(monkeypatch):
    def never_called(**kwargs):
        raise AssertionError("client must not be created past deadline")

    monkeypatch.setattr(metadata.httpx, "Client", never_called)
    with pytest.raises(metadata.ReviewUnavailable, match="request_deadline"):
        metadata.trusted_request(
            "https://api.tikhub.io/api/v1/tiktok/app/v3/fetch_one_video_v2",
            deadline=time.monotonic() - 1,
            max_bytes=1024,
        )


def test_redirect_is_revalidated_and_no_provider_authorization(monkeypatch):
    checked_urls = []

    def addresses(url, **kwargs):
        checked_urls.append(url)
        if "127.0.0.1" in url:
            raise metadata.ReviewUnavailable("non_public_media_address")
        return "cdn.example", ["8.8.8.8"]

    class RedirectConnection:
        def __init__(self, *args):
            pass

        def request(self, method, target, body, headers):
            assert "Authorization" not in headers

        def getresponse(self):
            return self

        status = 302

        def getheader(self, name):
            return "https://127.0.0.1/secret"

        def close(self):
            pass

    monkeypatch.setattr(metadata, "public_addresses", addresses)
    monkeypatch.setattr(metadata, "_PinnedHTTPSConnection", RedirectConnection)
    with pytest.raises(metadata.ReviewUnavailable, match="non_public"):
        metadata.bounded_request(
            "https://cdn.example/media",
            deadline=time.monotonic() + 20,
            max_bytes=100,
            redirects=3,
        )
    assert len(checked_urls) == 2
