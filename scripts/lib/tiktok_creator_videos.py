"""Bounded, read-only TikHub metadata and public HTTPS media transport.

Two transports:
  * trusted_request - normal bounded HTTPS via httpx, only for the hardcoded
    trusted TikHub/ARK endpoints, never follows redirects. A local DNS
    resolver/proxy on this machine makes IP pinning unusable for these hosts.
  * bounded_request - pinned, validated public IP only, for arbitrary media
    URLs supplied by upstream metadata (redirects re-validated per hop).

OpenAPI inspected 2026-09-06: app/v3/fetch_user_post_videos accepts
unique_id, sec_user_id, max_cursor (NOT cursor), count, sort_type.
Every returned author's handle and sec_uid are checked; caller IDs are not proof.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import ssl
import threading
import time
from datetime import datetime
from urllib.parse import urlencode, urljoin, urlsplit

import httpx

TRUSTED_HOSTS = frozenset({"api.tikhub.io", "ark.cn-beijing.volces.com"})
TRUSTED_TIKHUB_PREFIX = "https://api.tikhub.io/api/v1/tiktok/app/v3/"
TRUSTED_ARK_URL = "https://ark.cn-beijing.volces.com/api/v3/responses"


class ReviewUnavailable(RuntimeError):
    """A safe, non-secret reason that requires human review."""


def normalize_handle(value: object) -> str:
    handle = str(value or "").strip().removeprefix("@").lower()
    return handle if re.fullmatch(r"[a-z0-9_.]{1,24}", handle) else ""


def _parse_trusted_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in TRUSTED_HOSTS
        or parsed.port not in (None, 443)
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ReviewUnavailable("unsafe_trusted_url")
    return parsed.hostname, (parsed.path or "/") + (
        f"?{parsed.query}" if parsed.query else ""
    )


def trusted_request(
    url: str,
    *,
    deadline: float,
    max_bytes: int,
    headers: dict | None = None,
    body: bytes | None = None,
) -> bytes:
    """Normal bounded HTTPS to a hardcoded trusted host; no redirects, no pinning."""
    _parse_trusted_url(url)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ReviewUnavailable("request_deadline")
    request_headers = {
        "User-Agent": "zn-sample-content-review/1.0",
        "Accept-Encoding": "identity",
    }
    request_headers.update(headers or {})
    try:
        with (
            httpx.Client(follow_redirects=False, timeout=min(60, remaining)) as (
                session
            ),
            session.stream(
                "POST" if body is not None else "GET",
                url,
                headers=request_headers,
                content=body,
            ) as response,
        ):
            if response.status_code in (301, 302, 303, 307, 308):
                raise ReviewUnavailable("redirect_rejected")
            if response.status_code != 200:
                raise ReviewUnavailable("provider_http_error")
            length = response.headers.get("content-length")
            if length and int(length) > max_bytes:
                raise ReviewUnavailable("response_byte_limit")
            result = bytearray()
            for chunk in response.iter_bytes():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReviewUnavailable("request_deadline")
                result.extend(chunk)
                if len(result) > max_bytes:
                    raise ReviewUnavailable("response_byte_limit")
            return bytes(result)
    except ReviewUnavailable:
        raise
    except (httpx.RequestError, httpx.InvalidURL, ValueError, OSError):
        # httpx exceptions may embed URLs or credentials: never export them.
        raise ReviewUnavailable("provider_unavailable") from None


def public_addresses(
    url: str, *, deadline: float | None = None
) -> tuple[str, list[str]]:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ReviewUnavailable("unsafe_media_url")
    hostname = parsed.hostname
    resolved = []
    errors = []

    def resolve() -> None:
        try:
            resolved.extend(socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError) as error:
            errors.append(error)

    # OS getaddrinfo has no portable timeout. The daemon is bounded per request
    # and cannot keep the process alive if the system resolver stalls.
    resolver = threading.Thread(target=resolve, daemon=True)
    resolver.start()
    resolver.join(max(0, min(5, (deadline or time.monotonic() + 5) - time.monotonic())))
    if resolver.is_alive() or errors:
        raise ReviewUnavailable("dns_resolution_unavailable")
    addresses = list(dict.fromkeys(item[4][0] for item in resolved))
    if not addresses or any(
        not ipaddress.ip_address(address).is_global for address in addresses
    ):
        raise ReviewUnavailable("non_public_media_address")
    return hostname, addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, timeout: float):
        super().__init__(
            hostname, timeout=timeout, context=ssl.create_default_context()
        )
        self.address = address

    def connect(self) -> None:
        connection = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except Exception:
            connection.close()
            raise


def bounded_request(
    url: str,
    *,
    deadline: float,
    max_bytes: int,
    headers: dict | None = None,
    body: bytes | None = None,
    redirects: int = 0,
) -> bytes:
    """Pin a checked public IP, retain TLS hostname verification, never use proxies.

    Authenticated API calls never follow redirects. Media redirects are individually
    validated and receive no provider credentials. No URLs enter error messages.
    """
    for redirect_index in range(redirects + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReviewUnavailable("request_deadline")
        hostname, addresses = public_addresses(url, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReviewUnavailable("request_deadline")
        connection = _PinnedHTTPSConnection(hostname, addresses[0], min(60, remaining))
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        request_headers = {
            "User-Agent": "zn-sample-content-review/1.0",
            "Accept-Encoding": "identity",
        }
        request_headers.update(headers or {})
        try:
            connection.request(
                "POST" if body is not None else "GET",
                target,
                body=body,
                headers=request_headers,
            )
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                if headers or body is not None or redirect_index == redirects:
                    raise ReviewUnavailable("redirect_rejected")
                location = response.getheader("Location")
                if not location:
                    raise ReviewUnavailable("invalid_redirect")
                url = urljoin(url, location)
                continue
            if response.status != 200:
                raise ReviewUnavailable("provider_http_error")
            length = response.getheader("Content-Length")
            if length and int(length) > max_bytes:
                raise ReviewUnavailable("response_byte_limit")
            result = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ReviewUnavailable("request_deadline")
                if connection.sock:
                    connection.sock.settimeout(min(15, remaining))
                chunk = response.read1(min(65536, max_bytes + 1 - len(result)))
                if not chunk:
                    break
                result.extend(chunk)
                if len(result) > max_bytes:
                    raise ReviewUnavailable("response_byte_limit")
            return bytes(result)
        finally:
            connection.close()
    raise ReviewUnavailable("redirect_limit")


def strict_json_value(raw: str | bytes) -> object:
    """Parse JSON with duplicate-key and non-finite-number rejection."""

    def unique_object(pairs: list) -> dict:
        value = {}
        for key, item in pairs:
            if key in value:
                raise ReviewUnavailable("duplicate_json_key")
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ReviewUnavailable("invalid_json_number")

    try:
        return json.loads(
            raw, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except json.JSONDecodeError as error:
        raise ReviewUnavailable("invalid_json") from error


def strict_json(raw: str | bytes) -> dict:
    result = strict_json_value(raw)
    if not isinstance(result, dict):
        raise ReviewUnavailable("invalid_json_object")
    return result


def verify_author(detail: dict, handle: str, sec_uid: str = "") -> str:
    author = detail.get("author")
    if (
        not isinstance(author, dict)
        or normalize_handle(author.get("unique_id")) != handle
    ):
        raise ReviewUnavailable("creator_identity_mismatch")
    actual_sec_uid = author.get("sec_uid")
    if (
        not isinstance(actual_sec_uid, str)
        or not actual_sec_uid
        or len(actual_sec_uid) > 256
    ):
        raise ReviewUnavailable("creator_identity_missing")
    if sec_uid and actual_sec_uid != sec_uid:
        raise ReviewUnavailable("creator_identity_mismatch")
    return actual_sec_uid


class TikHubClient:
    def __init__(self, settings: dict, deadline: float):
        self.settings = settings
        self.deadline = deadline

    def fetch(self, endpoint: str, parameters: dict) -> dict:
        if not self.settings["tikhub_api_key"]:
            raise ReviewUnavailable("missing_tikhub_key")
        url = TRUSTED_TIKHUB_PREFIX + endpoint + "?" + urlencode(parameters)
        payload = strict_json(
            trusted_request(
                url,
                deadline=min(self.deadline, time.monotonic() + 30),
                max_bytes=4 * 1024 * 1024,
                headers={"Authorization": "Bearer " + self.settings["tikhub_api_key"]},
            )
        )
        data = payload.get("data")
        if (
            payload.get("code") != 200
            or not isinstance(data, dict)
            or data.get("status_code", 0) != 0
        ):
            raise ReviewUnavailable("metadata_provider_error")
        return data

    def detail(self, video_id: str, handle: str, sec_uid: str = "") -> dict:
        if not re.fullmatch(r"\d{15,25}", str(video_id)):
            raise ReviewUnavailable("invalid_seed_video_id")
        detail = self.fetch("fetch_one_video_v2", {"aweme_id": video_id}).get(
            "aweme_detail"
        )
        if not isinstance(detail, dict) or str(detail.get("aweme_id")) != video_id:
            raise ReviewUnavailable("video_detail_missing")
        verify_author(detail, handle, sec_uid)
        return detail

    def collect(
        self,
        handle: str,
        start: datetime,
        end: datetime,
        seed_video_id: str = "",
        on_video=None,
    ) -> dict:
        """Page the creator's videos; never fails closed on page/video caps.

        on_video(detail, sec_uid) -> bool is called for each in-window video.
        Returning True stops further pagination. Caps and stalls return a
        partial result with complete=False instead of raising so that an
        already-sufficient lower-bound proof is preserved. Hard validation
        failures (identity mismatch, conflicting/corrupt metadata) still raise.
        """
        if not handle:
            raise ReviewUnavailable("invalid_creator_handle")
        sec_uid = ""
        if seed_video_id:
            sec_uid = verify_author(self.detail(seed_video_id, handle), handle)
        cursor = 0
        seen_cursors = {0}
        seen_videos: dict[str, dict] = {}
        recent: list[dict] = []
        stop_reason = "complete"
        for _page in range(self.settings["max_pages"]):
            data = self.fetch(
                "fetch_user_post_videos",
                {
                    "sec_user_id": sec_uid,
                    "unique_id": handle if not sec_uid else "",
                    "max_cursor": cursor,
                    "count": 20,
                    "sort_type": 0,
                },
            )
            videos = data.get("aweme_list")
            if not isinstance(videos, list) or len(videos) > 100:
                raise ReviewUnavailable("invalid_video_list")
            for detail in videos:
                if not isinstance(detail, dict):
                    raise ReviewUnavailable("invalid_video_metadata")
                sec_uid = verify_author(detail, handle, sec_uid)
                video_id = str(detail.get("aweme_id", ""))
                timestamp = detail.get("create_time")
                if (
                    not re.fullmatch(r"\d{15,25}", video_id)
                    or type(timestamp) is not int
                    or timestamp <= 0
                    or timestamp > end.timestamp() + 300
                ):
                    raise ReviewUnavailable("invalid_video_metadata")
                if video_id in seen_videos:
                    if seen_videos[video_id] != detail:
                        raise ReviewUnavailable("conflicting_video_metadata")
                    continue
                seen_videos[video_id] = detail
                if len(seen_videos) > self.settings["max_videos"]:
                    stop_reason = "video_limit"
                    break
                if start.timestamp() <= timestamp <= end.timestamp():
                    recent.append(detail)
                    if on_video is not None and on_video(detail, sec_uid):
                        stop_reason = "stopped"
                        break
            if stop_reason != "complete":
                break
            has_more = data.get("has_more")
            if type(has_more) not in (bool, int) or has_more not in (0, 1):
                raise ReviewUnavailable("invalid_pagination")
            if not has_more:
                if not sec_uid:
                    raise ReviewUnavailable("creator_identity_unresolved")
                stop_reason = "complete"
                break
            next_cursor = data.get("max_cursor")
            if (
                type(next_cursor) is not int
                or next_cursor < 0
                or next_cursor in seen_cursors
                or not videos
            ):
                stop_reason = "pagination_stalled"
                break
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        else:
            stop_reason = "pagination_limit"
        return {
            "handle": handle,
            "sec_uid": sec_uid,
            "videos": recent,
            "complete": stop_reason == "complete",
            "stop_reason": stop_reason,
        }
