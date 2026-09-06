"""Dry-run SOP 2 content-thanks: Feishu 待发布 → 已完成 tab → preview DM.

Development/test always keep execute=False and write_feishu=False.
Uncertain matches, thread copy, or content type are held, never sent.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from assistant.database.models import FollowupTask, SampleCase
from assistant.domain.content_thanks import (
    CONTENT_THANKS_STAGE,
    DECISION_ALREADY_SENT,
    DECISION_HOLD,
    DECISION_PREVIEW,
    DECISION_SKIP,
    classify_completed_content,
    content_thanks_since,
    decide_content_thanks_action,
    first_content_url,
    match_completed_rows,
    render_content_thanks_preview,
    resolve_content_thanks_language,
)
from assistant.domain.followup_stage import followup_task_completed
from assistant.domain.timeutil import beijing_now
from assistant.jobs.registry import HandlerFailure
from assistant.services.store_service import StoreService
from scripts.lib.feishu_bitable import (
    _field_plain,
    record_outreach_ms,
)


class ContentThanksService:
    def __init__(
        self,
        session_factory,
        *,
        warning=None,
        cancel_check=None,
        progress=None,
        execute: bool = False,
        write_feishu: bool = False,
        list_completed=None,
        inspect_content=None,
        fetch_performance=None,
        send_message=None,
        search_feishu=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)
        self.cancel_check = cancel_check or (lambda: None)
        self.progress = progress or (lambda current, total, message: None)
        if execute or write_feishu:
            raise HandlerFailure(
                "content-thanks-writes-disabled",
                "开发和测试阶段禁止实际发送私信或写飞书。",
            )
        self.execute = False
        self.write_feishu = False
        self.list_completed = list_completed
        self.inspect_content = inspect_content
        self.fetch_performance = fetch_performance
        self.send_message = send_message
        self.search_feishu = search_feishu
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def preview(self) -> dict[str, Any]:
        self.cancel_check()
        store_result = StoreService(self.session_factory).resolve_unique_running_store()
        if not store_result.get("ok"):
            raise HandlerFailure("running-not-unique", "请只开一家店后再预演已完成感谢私信。")
        store = store_result["store"]
        store_id = str(store.get("storeId") or "")
        shop_id = str(store.get("shopId") or "")
        now = beijing_now(self.clock())
        records = self._load_feishu_candidates(now)
        total = max(1, len(records))
        completed_by_handle: dict[str, dict[str, Any]] = {}
        distinct_handles: list[str] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            creator_handle = _field_plain((record.get("fields") or {}).get("红人ID")).strip()
            if not creator_handle or creator_handle in completed_by_handle:
                continue
            completed_by_handle[creator_handle] = None
            distinct_handles.append(creator_handle)
        # 页面只保证一次；逐个搜索时不再重复导航检查。
        ensure_page = len(distinct_handles) > 0
        for index, creator_handle in enumerate(distinct_handles, start=1):
            self.cancel_check()
            self.progress(
                index - 1,
                total,
                f"已完成tab搜索 {index}/{len(distinct_handles)}",
            )
            sample_case = self._local_case(creator_handle)
            completed_match = self._search_completed(
                store_id,
                creator_handle,
                sample_case,
                ensure_page=ensure_page,
            )
            ensure_page = False
            completed_by_handle[creator_handle] = completed_match
        inspected_by_handle = self._inspect_wanted_creators(store_id, completed_by_handle)
        summary = {
            "candidates": len(records),
            "preview": 0,
            "already_sent": 0,
            "hold": 0,
            "skip": 0,
            "execute": False,
            "write_feishu": False,
            "rows": [],
        }
        for index, record in enumerate(records, start=1):
            self.cancel_check()
            self.progress(index - 1, total, f"预演已完成感谢 {index}/{len(records)}")
            creator_handle = _field_plain(((record.get("fields") or {}) if isinstance(record, dict) else {}).get("红人ID")).strip()
            row = self._preview_record(
                store_id=store_id,
                shop_id=shop_id,
                record=record,
                now=now,
                completed_match=completed_by_handle.get(creator_handle),
                inspected_by_handle=inspected_by_handle,
            )
            summary["rows"].append(row)
            decision = str(row.get("decision") or DECISION_HOLD)
            if decision == DECISION_PREVIEW:
                summary["preview"] += 1
            elif decision == DECISION_ALREADY_SENT:
                summary["already_sent"] += 1
            elif decision == DECISION_SKIP:
                summary["skip"] += 1
            else:
                summary["hold"] += 1
        self.progress(total, total, "已完成感谢预演结束")
        return summary

    def _load_feishu_candidates(self, now: datetime) -> list[dict[str, Any]]:
        if self.search_feishu is not None:
            return list(self.search_feishu(since=content_thanks_since(now)) or [])
        from lib.app_config import load_bitable_settings
        from lib.feishu_bitable import (
            DEFAULT_BITABLE_APP_ID,
            FeishuBitableError,
            get_bitable_access_token,
            search_pending_post_records,
        )

        settings = load_bitable_settings(default_app_id=DEFAULT_BITABLE_APP_ID)
        if not (settings.get("app_id") and settings.get("app_secret")):
            raise HandlerFailure("feishu-credentials-missing", "未配置飞书密钥，无法读取待发布达人。")
        try:
            token = get_bitable_access_token()
            return search_pending_post_records(token, since=content_thanks_since(now))
        except FeishuBitableError as error:
            raise HandlerFailure("feishu-search-failed", str(error)) from error

    def _preview_record(
        self,
        *,
        store_id: str,
        shop_id: str,
        record: dict[str, Any],
        now: datetime,
        completed_match: dict[str, Any] | None = None,
        inspected_by_handle: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        creator_handle = _field_plain(fields.get("红人ID")).strip()
        sample_product = _field_plain(fields.get("寄样产品")).strip()
        feishu_status = _field_plain(fields.get("合作状态")).strip()
        feishu_lang = _field_plain(fields.get("使用语言")).strip()
        record_id = str(record.get("record_id") or "").strip()
        outreach_ms = record_outreach_ms(record)
        outreach_age_days = None
        if outreach_ms is not None:
            outreach_at = datetime.fromtimestamp(outreach_ms / 1000, tz=timezone.utc)
            outreach_age_days = (now.date() - beijing_now(outreach_at).date()).days
        sample_case = self._local_case(creator_handle)
        content = {"status": "hold", "content_type": "", "reason": "not-inspected"}
        thread_text = ""
        thread_checked = False
        content_url = ""
        creator_id = ""
        if completed_match is None and creator_handle:
            completed_match = self._search_completed(store_id, creator_handle, sample_case)
        if completed_match is None:
            completed_match = {"status": "none", "rows": [], "reason": "not-searched"}
        if completed_match.get("status") == "unique":
            inspected = self._inspect_content(
                store_id,
                creator_handle,
                inspected_by_handle=inspected_by_handle or {},
            )
            content = classify_completed_content(
                panel_text=str(inspected.get("panel_text") or ""),
                links=list(inspected.get("links") or []),
                video_count=inspected.get("video_count"),
                live_count=inspected.get("live_count"),
            )
            content_url = first_content_url(list(inspected.get("links") or []))
            if inspected.get("ok") and content.get("content_type") in {"video", "live"}:
                creator_id = str(sample_case.creator_id or "") if sample_case else ""
                if not creator_id:
                    creator_id = self._completed_creator_id(completed_match)
                thread_probe = self._preview_thread(
                    store_id=store_id,
                    sample_case=sample_case,
                    creator_handle=creator_handle,
                    creator_id=creator_id,
                )
                thread_checked = bool(thread_probe.get("ok"))
                thread_text = str(thread_probe.get("thread_text") or "")
        decision = decide_content_thanks_action(
            feishu_status=feishu_status,
            outreach_age_days=outreach_age_days,
            completed_match=str(completed_match.get("status") or "none"),
            content_type=str(content.get("content_type") or ""),
            content_reason=str(content.get("reason") or ""),
            thread_text=thread_text,
            thread_checked=thread_checked,
            today=now.date(),
            local_sent_at=self._latest_local_thanks_sent_at(sample_case),
            feishu_record_count=1,
        )
        language = resolve_content_thanks_language(
            feishu_lang=feishu_lang,
            manual_lang=str(sample_case.language or "") if sample_case else "",
            bio=str(sample_case.bio or "") if sample_case else "",
        )
        preview = {"ok": False, "message": "", "template_key": "", "language": language}
        if decision["decision"] in {DECISION_PREVIEW, DECISION_ALREADY_SENT} and decision["content_type"]:
            preview = render_content_thanks_preview(
                content_type=str(decision["content_type"]),
                lang=language,
                creator_name=creator_handle,
                content_url=content_url,
            )
            if decision["decision"] == DECISION_PREVIEW and preview.get("ok") and preview.get("message"):
                self._preview_send(
                    store_id=store_id,
                    shop_id=shop_id,
                    sample_case=sample_case,
                    creator_handle=creator_handle,
                    creator_id=creator_id,
                    body=str(preview["message"]),
                )
        row = {
            "creator_handle": creator_handle,
            "sample_product": sample_product,
            "record_id": record_id,
            "decision": decision["decision"],
            "reason": decision["reason"],
            "content_type": decision["content_type"],
            "language": language,
            "template_key": preview.get("template_key") or "",
            "message_preview": preview.get("message") or "",
            "planned_feishu_status": "已完成",
            "send": False,
            "write_feishu": False,
            "execute": False,
        }
        if not creator_handle:
            row["decision"] = DECISION_HOLD
            row["reason"] = "missing-creator-handle"
        return row

    def _local_case(self, creator_handle: str) -> SampleCase | None:
        handle = str(creator_handle or "").strip()
        if not handle:
            return None
        with self.session_factory() as session:
            matches = session.scalars(
                select(SampleCase).where(SampleCase.creator_name == handle)
            ).all()
            if len(matches) == 1:
                return matches[0]
            return None

    def _latest_local_thanks_sent_at(self, sample_case: SampleCase | None):
        if sample_case is None:
            return None
        with self.session_factory() as session:
            tasks = session.scalars(
                select(FollowupTask).where(
                    FollowupTask.sample_case_id == sample_case.id,
                    FollowupTask.stage == CONTENT_THANKS_STAGE,
                )
            ).all()
        sent_times = [
            task.sent_at
            for task in tasks
            if followup_task_completed(
                {"sent_at": task.sent_at, "send_result": task.send_result}
            )
            and task.sent_at is not None
        ]
        if not sent_times:
            return None
        return max(sent_times)

    def _search_completed(
        self,
        store_id: str,
        creator_handle: str,
        sample_case: SampleCase | None,
        *,
        ensure_page: bool = True,
    ) -> dict[str, Any]:
        if self.list_completed is not None:
            rows = list(
                self.list_completed(
                    store_id,
                    creator_handle=creator_handle,
                )
                or []
            )
        else:
            from lib.sample_api import scrape_completed_list_api
            from lib.shipped_dom import ensure_sample_page_loaded
            from lib.completed_content_dom import ensure_completed_tab

            if ensure_page:
                ensure_sample_page_loaded(store_id)
                ensure_completed_tab(store_id)
            rows = scrape_completed_list_api(
                store_id,
                creator_handle=creator_handle,
                max_pages=1,
            )
        return match_completed_rows(
            rows,
            creator_handle=creator_handle,
            creator_id=str(sample_case.creator_id or "") if sample_case else "",
        )

    def _inspect_wanted_creators(
        self,
        store_id: str,
        completed_by_handle: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch video/live counts per unique completed match via performance API."""
        if self.inspect_content is not None:
            return {}
        inspected: dict[str, dict[str, Any]] = {}
        unique = [
            (handle, match)
            for handle, match in completed_by_handle.items()
            if match is not None and match.get("status") == "unique"
        ]
        total = max(1, len(unique))
        for index, (handle, match) in enumerate(unique, start=1):
            self.cancel_check()
            self.progress(
                index - 1,
                total,
                f"读取内容数量 {index}/{len(unique)}",
            )
            rows = list(match.get("rows") or [])
            apply_ids = [
                str(row.get("apply_id") or "").strip()
                for row in rows
                if str(row.get("apply_id") or "").strip()
            ]
            if not apply_ids:
                inspected[handle.lower()] = {
                    "ok": False,
                    "reason": "no-apply-id",
                    "panel_text": "",
                    "links": [],
                }
                continue
            inspected[handle.lower()] = self._fetch_performance_for_apply(
                store_id,
                apply_ids,
            )
        return inspected

    def _fetch_performance_for_apply(
        self,
        store_id: str,
        apply_ids: list[str],
    ) -> dict[str, Any]:
        if self.fetch_performance is not None:
            return dict(self.fetch_performance(store_id, apply_ids) or {})
        from lib.page_api import (
            SAMPLE_PERFORMANCE_ENDPOINT,
            PageApiError,
            get_affiliate_page_context,
            get_affiliate_read_json,
        )

        try:
            context = get_affiliate_page_context(store_id)
        except PageApiError as error:
            return {
                "ok": False,
                "reason": "context-unavailable",
                "panel_text": "",
                "links": [],
                "detail": str(error)[:200],
            }
        video_total = 0
        live_total = 0
        any_success = False
        last_error = ""
        for apply_id in apply_ids:
            for content_type in (1, 2):
                try:
                    payload = get_affiliate_read_json(
                        store_id,
                        SAMPLE_PERFORMANCE_ENDPOINT,
                        query={
                            "apply_id": apply_id,
                            "content_type": content_type,
                            "size": 20,
                            "offset": 0,
                        },
                        context=context,
                        request_timeout_seconds=30,
                    )
                except PageApiError as error:
                    last_error = str(error)[:200]
                    continue
                any_success = True
                data = payload.get("data") or {}
                try:
                    video_total = max(video_total, int(data.get("total_video_count") or 0))
                    live_total = max(live_total, int(data.get("total_live_count") or 0))
                except (TypeError, ValueError):
                    last_error = "bad-count"
        if not any_success:
            return {
                "ok": False,
                "reason": "performance-unavailable",
                "panel_text": "",
                "links": [],
                "detail": last_error,
            }
        return {
            "ok": True,
            "reason": "",
            "panel_text": "",
            "links": [],
            "video_count": video_total,
            "live_count": live_total,
        }

    def _inspect_content(
        self,
        store_id: str,
        creator_handle: str,
        *,
        inspected_by_handle: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if self.inspect_content is not None:
            return dict(self.inspect_content(store_id, creator_handle) or {})
        handle = str(creator_handle or "").strip().lower()
        cached = (inspected_by_handle or {}).get(handle)
        if cached is not None:
            return dict(cached)
        return {
            "ok": False,
            "reason": "row-not-found",
            "panel_text": "",
            "links": [],
        }

    def _preview_thread(
        self,
        *,
        store_id: str,
        sample_case: SampleCase | None,
        creator_handle: str,
        creator_id: str = "",
    ) -> dict[str, Any]:
        from lib.im_dom import (
            im_thread_text,
            inspect_current_thread,
            open_conversation_via_new_message,
        )

        opened = open_conversation_via_new_message(
            store_id,
            creator_id=creator_id or (str(sample_case.creator_id or "") if sample_case else ""),
            creator_name=creator_handle,
            wait=3.5,
        )
        if not opened.get("ok"):
            return {"ok": False, "thread_text": ""}
        probe = inspect_current_thread(store_id, creator_handle)
        return {"ok": True, "thread_text": im_thread_text(probe)}

    @staticmethod
    def _completed_creator_id(completed_match: dict[str, Any] | None) -> str:
        """Reuse the completed-list ID when local sample data is unavailable."""
        if not isinstance(completed_match, dict):
            return ""
        creator_ids = {
            str(row.get("creator_id") or "").strip()
            for row in completed_match.get("rows") or []
            if isinstance(row, dict) and str(row.get("creator_id") or "").strip()
        }
        return next(iter(creator_ids)) if len(creator_ids) == 1 else ""

    def _preview_send(
        self,
        *,
        store_id: str,
        shop_id: str,
        sample_case: SampleCase | None,
        creator_handle: str,
        body: str,
        creator_id: str = "",
    ) -> dict[str, Any]:
        sender = self.send_message
        if sender is None:
            from lib.im_api import send_direct_message as sender
        return sender(
            store_id,
            body,
            creator_name=creator_handle,
            creator_id=creator_id or (str(sample_case.creator_id or "") if sample_case else ""),
            shop_id=shop_id,
            execute=False,
        )
