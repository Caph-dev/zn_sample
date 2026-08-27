"""Generate local follow-up tasks; Feishu writes stay behind an explicit switch."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime

from sqlalchemy import select

from assistant.database.models import ContentEvidence, FollowupTask, SampleCase, Shipment, Store
from assistant.domain.followup_stage import (
    ACTION_KIND_ACKNOWLEDGE_CONTENT,
    ACTION_KIND_CONFIRM_DELIVERY_DATE,
    ACTION_KIND_LIST_ONLY,
    ACTION_KIND_MARK_UNFULFILLED,
    ACTION_KIND_SEND_MESSAGE,
    ACTIVE_TASK_STATUSES,
    CONFIRM_DELIVERY_TIME_STAGE,
    CONTENT_FOUND_STAGE,
    action_kind_for_stage,
    days_since_delivery,
    followup_task_completed,
    plan_followup_mutation,
)
from assistant.domain.followup_labels import (
    LISTED_RESULT,
    MARKED_SENT_RESULT,
    followup_review_label,
)
from assistant.domain.message_templates import TEMPLATE_VERSION, choose_template_key, render_followup_message
from assistant.domain.platform_status import (
    PLATFORM_STATUS_COMPLETED,
    PLATFORM_STATUS_LABELS,
    is_processing_followup_case,
)
from assistant.domain.policies import choose_followup_creator_type, choose_followup_language
from assistant.domain.sku_images import resolve_followup_attachment
from assistant.domain.timeutil import beijing_now
from scripts.lib.feishu_bitable import COOPERATION_STATUS_COMPLETED, COOPERATION_STATUS_UNPUBLISHED


class FollowupService:
    def __init__(self, session_factory, *, warning=None, cancel_check=None, store_id=None) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)
        self.cancel_check = cancel_check or (lambda: None)
        self.store_id = store_id

    def generate(self, now: datetime | None = None) -> dict:
        self._hydrate_from_latest_export()
        self.cancel_check()
        effective_now = beijing_now(now)
        created = 0
        refreshed = 0
        with self.session_factory() as session:
            cases = session.scalars(select(SampleCase)).all()
            for sample_case in cases:
                self.cancel_check()
                shipment = session.scalar(
                    select(Shipment).where(Shipment.sample_case_id == sample_case.id)
                )
                if shipment is None:
                    continue
                if shipment.needs_delivery_time_confirmation or (
                    shipment.status_category == "delivered" and shipment.delivered_at is None
                ):
                    created += self._upsert_confirmation(session, sample_case)
                    continue
                self._suppress_confirmation(session, sample_case.id)
                if shipment.delivered_at is None:
                    continue
                confirmed_content = self._latest_confirmed_content(session, sample_case.id)
                created_keys: set[tuple] = set()
                if is_processing_followup_case(
                    platform_status=sample_case.platform_status or "",
                    stale=bool(sample_case.platform_status_stale),
                ):
                    days = days_since_delivery(shipment.delivered_at, today=effective_now.date())
                    existing_tasks = session.scalars(
                        select(FollowupTask).where(
                            FollowupTask.sample_case_id == sample_case.id,
                            FollowupTask.stage != CONFIRM_DELIVERY_TIME_STAGE,
                        )
                    ).all()
                    mutation = plan_followup_mutation(
                        existing_unpublished=[
                            {
                                "stage": task.stage,
                                "scheduled_for": task.scheduled_for,
                                "status": task.status,
                                "sent_at": task.sent_at,
                                "send_result": task.send_result,
                            }
                            for task in existing_tasks
                        ],
                        days=days,
                        has_confirmed_content=confirmed_content is not None,
                        today=effective_now.date(),
                    )
                    created_keys = {
                        (task_creation["stage"], task_creation["scheduled_for"])
                        for task_creation in mutation["create"]
                    }
                    for task_creation in mutation["create"]:
                        created += self._upsert_stage(
                            session,
                            sample_case,
                            task_creation["stage"],
                            task_creation["scheduled_for"],
                        )
                    for suppression in mutation["suppress"]:
                        self._suppress_matching_task(
                            session,
                            sample_case.id,
                            stage=suppression["stage"],
                            scheduled_for=suppression["scheduled_for"],
                            reason=suppression["reason"],
                        )
                    if confirmed_content is not None:
                        created += self._upsert_content_found(
                            session,
                            sample_case,
                            confirmed_content,
                        )
                refreshed += self._refresh_active_unpublished_tasks(
                    session,
                    sample_case,
                    skip_keys=created_keys,
                )
            session.commit()
        return {"created": created, "refreshed": refreshed}

    def _refresh_active_unpublished_tasks(
        self,
        session,
        sample_case: SampleCase,
        *,
        skip_keys: set[tuple],
    ) -> int:
        refreshed = 0
        active_tasks = session.scalars(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case.id,
                FollowupTask.status.in_(ACTIVE_TASK_STATUSES),
                FollowupTask.stage != CONFIRM_DELIVERY_TIME_STAGE,
            )
        ).all()
        for task in active_tasks:
            if (task.stage, task.scheduled_for) in skip_keys:
                continue
            if followup_task_completed(
                {"sent_at": task.sent_at, "send_result": task.send_result}
            ):
                continue
            before = (
                task.status,
                task.review_reason,
                task.template_key,
                task.creator_type,
                task.language,
                task.action_kind,
            )
            self._populate_language(sample_case)
            self._fill_task_preview(task, sample_case)
            after = (
                task.status,
                task.review_reason,
                task.template_key,
                task.creator_type,
                task.language,
                task.action_kind,
            )
            if after != before:
                refreshed += 1
        return refreshed

    def refresh_task(self, task_id: int) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            if task.status not in ACTIVE_TASK_STATUSES:
                return
            if followup_task_completed(
                {"sent_at": task.sent_at, "send_result": task.send_result}
            ):
                return
            sample_case = session.get(SampleCase, task.sample_case_id)
            self._fill_task_preview(task, sample_case)
            session.commit()

    def confirm_content(
        self,
        task_id: int,
        *,
        content_type: str,
        content_url: str = "",
        write_feishu: bool = False,
        confirmed_by: str = "operator",
    ) -> dict:
        if content_type not in {"video", "live"}:
            raise ValueError("invalid-content-type")
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            sample_case = session.get(SampleCase, task.sample_case_id)
            now = beijing_now()
            evidence = ContentEvidence(
                sample_case_id=sample_case.id,
                content_type=content_type,
                content_status="confirmed",
                content_url=str(content_url or "").strip(),
                source="manual",
                confirmed_by=confirmed_by,
                confirmed_at=now,
                observed_at=now,
            )
            session.add(evidence)
            session.flush()
            sample_case.platform_status = PLATFORM_STATUS_COMPLETED
            sample_case.platform_status_label = PLATFORM_STATUS_LABELS[PLATFORM_STATUS_COMPLETED]
            sample_case.platform_status_source = "manual_content_confirmation"
            sample_case.platform_status_stale = False
            sample_case.platform_status_observed_at = now
            for existing in session.scalars(
                select(FollowupTask).where(
                    FollowupTask.sample_case_id == sample_case.id,
                    FollowupTask.status.in_(ACTIVE_TASK_STATUSES),
                )
            ).all():
                if existing.stage == CONTENT_FOUND_STAGE:
                    continue
                if followup_task_completed(
                    {"sent_at": existing.sent_at, "send_result": existing.send_result}
                ):
                    continue
                existing.status = "suppressed"
                existing.suppressed_reason = "content_confirmed"
                existing.requires_manual_confirmation = False
            self._upsert_content_found(session, sample_case, evidence)
            feishu_result = {"status": "not-requested"}
            if write_feishu:
                feishu_result = self._write_cooperation_status(
                    sample_case,
                    COOPERATION_STATUS_COMPLETED,
                    evidence_id=str(evidence.id),
                )
                evidence.feishu_write_status = str(feishu_result.get("status") or "")
                evidence.feishu_write_error = str(feishu_result.get("error") or "")
                if feishu_result.get("status") in {"written", "unchanged"}:
                    sample_case.feishu_cooperation_status = COOPERATION_STATUS_COMPLETED
            session.commit()
            return {
                "ok": True,
                "content_type": content_type,
                "feishu": feishu_result,
            }

    def mark_unfulfilled(self, task_id: int, *, write_feishu: bool = False) -> dict:
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            if task.stage != "unfulfilled":
                raise ValueError("not-unfulfilled-task")
            sample_case = session.get(SampleCase, task.sample_case_id)
            if self._latest_confirmed_content(session, sample_case.id) is not None:
                raise ValueError("content-already-confirmed")
            feishu_result = {"status": "not-requested"}
            if write_feishu:
                feishu_result = self._write_cooperation_status(
                    sample_case,
                    COOPERATION_STATUS_UNPUBLISHED,
                    evidence_id="unfulfilled",
                )
                if feishu_result.get("status") in {"written", "unchanged"}:
                    sample_case.feishu_cooperation_status = COOPERATION_STATUS_UNPUBLISHED
            session.commit()
            return {"ok": True, "feishu": feishu_result}

    def preview_followup_message(self, task_id: int) -> dict:
        """Open the IM thread and fill/inspect SOP copy. Never sends."""
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            action_kind = task.action_kind or action_kind_for_stage(task.stage)
            if action_kind != ACTION_KIND_SEND_MESSAGE:
                raise ValueError("not-message-task")
            if task.status == "needs_review":
                raise ValueError("task-needs-review")
            if not str(task.message_preview or "").strip():
                raise ValueError("missing-message")
            sample_case = session.get(SampleCase, task.sample_case_id)
            store = session.get(Store, sample_case.store_id) if sample_case else None
            store_id = str((store.ziniao_store_id if store else "") or self.store_id or "").strip()
            creator_name = str(sample_case.creator_name if sample_case else "")
            creator_id = str(sample_case.creator_id if sample_case else "")
            shop_id = str(store.shop_id if store else "")
            body = str(task.message_preview)
            attachment_key = str(task.attachment_key or "")
        if not store_id:
            raise ValueError("missing-store")
        from lib.im_api import send_direct_message

        result = send_direct_message(
            store_id,
            body,
            creator_name=creator_name,
            creator_id=creator_id,
            shop_id=shop_id,
            execute=False,
            write_source="api",
        )
        result["attachment_key"] = attachment_key
        result["execute"] = False
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is not None:
                if result.get("ok"):
                    task.send_confirmation = str(result.get("status") or "dry-run")
                    task.last_error = ""
                else:
                    task.last_error = str(result.get("error") or "preview-failed")
                session.commit()
        return result

    def mark_message_sent(self, task_id: int) -> dict:
        return self._mark_local_completion(
            task_id,
            expected_action=ACTION_KIND_SEND_MESSAGE,
            send_result=MARKED_SENT_RESULT,
            wrong_action_error="not-message-task",
        )

    def mark_list_handed_over(self, task_id: int) -> dict:
        return self._mark_local_completion(
            task_id,
            expected_action=ACTION_KIND_LIST_ONLY,
            send_result=LISTED_RESULT,
            wrong_action_error="not-list-task",
        )

    def _mark_local_completion(
        self,
        task_id: int,
        *,
        expected_action: str,
        send_result: str,
        wrong_action_error: str,
    ) -> dict:
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            action_kind = task.action_kind or action_kind_for_stage(task.stage)
            if action_kind != expected_action:
                raise ValueError(wrong_action_error)
            if followup_task_completed(
                {"sent_at": task.sent_at, "send_result": task.send_result}
            ):
                session.commit()
                return {"ok": True, "already_completed": True, "send_result": task.send_result}
            if task.status != "pending":
                raise ValueError("task-not-active")
            now = beijing_now()
            task.action_kind = action_kind
            task.sent_at = now
            task.send_result = send_result
            task.send_confirmation = "local-operator"
            task.status = "pending"
            task.requires_manual_confirmation = False
            task.review_reason = ""
            session.commit()
            return {"ok": True, "already_completed": False, "send_result": send_result}

    def _write_cooperation_status(
        self,
        sample_case: SampleCase,
        target_status: str,
        *,
        evidence_id: str,
    ) -> dict:
        record_id = str(sample_case.feishu_record_id or "").strip()
        if not record_id:
            return {"status": "no-record", "error": "缺少飞书 record_id"}
        try:
            from lib.app_config import load_bitable_settings
            from lib.feishu_bitable import (
                DEFAULT_BITABLE_APP_ID,
                FeishuBitableError,
                get_bitable_access_token,
                update_record_cooperation_status,
            )

            settings = load_bitable_settings(default_app_id=DEFAULT_BITABLE_APP_ID)
            if not (settings.get("app_id") and settings.get("app_secret")):
                return {"status": "skipped-no-credentials", "error": "未配置飞书密钥"}
            token = get_bitable_access_token()
            result = update_record_cooperation_status(
                token,
                record_id,
                target_status,
            )
            result["evidence_id"] = evidence_id
            return result
        except FeishuBitableError as error:
            return {"status": "error", "error": str(error)}
        except Exception as error:
            return {"status": "error", "error": str(error)}

    def _latest_confirmed_content(self, session, sample_case_id: int) -> ContentEvidence | None:
        return session.scalar(
            select(ContentEvidence)
            .where(
                ContentEvidence.sample_case_id == sample_case_id,
                ContentEvidence.content_status == "confirmed",
            )
            .order_by(ContentEvidence.confirmed_at.desc(), ContentEvidence.id.desc())
        )

    def _upsert_confirmation(self, session, sample_case: SampleCase) -> int:
        task = session.scalar(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case.id,
                FollowupTask.stage == CONFIRM_DELIVERY_TIME_STAGE,
            )
        )
        created = int(task is None)
        if task is None:
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage=CONFIRM_DELIVERY_TIME_STAGE,
                scheduled_for=None,
            )
            session.add(task)
        task.action_kind = ACTION_KIND_CONFIRM_DELIVERY_DATE
        task.status = "needs_review"
        task.requires_manual_confirmation = True
        task.review_reason = "missing_delivery_time"
        task.message_preview = followup_review_label("missing_delivery_time")
        task.suppressed_reason = ""
        return created

    def _suppress_confirmation(self, session, sample_case_id: int) -> None:
        task = session.scalar(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case_id,
                FollowupTask.stage == CONFIRM_DELIVERY_TIME_STAGE,
            )
        )
        if task is not None and task.status in ACTIVE_TASK_STATUSES:
            task.status = "suppressed"
            task.suppressed_reason = "delivery_time_known"
            task.requires_manual_confirmation = False
            task.review_reason = ""

    def _suppress_matching_task(
        self,
        session,
        sample_case_id: int,
        *,
        stage: str,
        scheduled_for,
        reason: str,
    ) -> None:
        query = select(FollowupTask).where(
            FollowupTask.sample_case_id == sample_case_id,
            FollowupTask.stage == stage,
        )
        if scheduled_for is None:
            query = query.where(FollowupTask.scheduled_for.is_(None))
        else:
            query = query.where(FollowupTask.scheduled_for == scheduled_for)
        task = session.scalar(query)
        if task is not None and task.status in ACTIVE_TASK_STATUSES:
            task.status = "suppressed"
            task.suppressed_reason = reason
            task.requires_manual_confirmation = False

    def _upsert_stage(self, session, sample_case: SampleCase, stage: str, scheduled_for: date) -> int:
        task = session.scalar(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case.id,
                FollowupTask.stage == stage,
                FollowupTask.scheduled_for == scheduled_for,
            )
        )
        created = int(task is None)
        if task is None:
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage=stage,
                scheduled_for=scheduled_for,
            )
            session.add(task)
        if followup_task_completed(
            {"sent_at": task.sent_at, "send_result": task.send_result}
        ):
            return created
        if task.status not in ACTIVE_TASK_STATUSES and not created:
            return created
        self._populate_language(sample_case)
        self._fill_task_preview(task, sample_case)
        return created

    def _upsert_content_found(
        self,
        session,
        sample_case: SampleCase,
        evidence: ContentEvidence,
    ) -> int:
        scheduled_for = beijing_now().date()
        existing = session.scalars(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case.id,
                FollowupTask.stage == CONTENT_FOUND_STAGE,
            )
        ).all()
        task = next((item for item in existing if item.status in ACTIVE_TASK_STATUSES), None)
        created = int(task is None)
        if task is None:
            task = FollowupTask(
                sample_case_id=sample_case.id,
                stage=CONTENT_FOUND_STAGE,
                scheduled_for=scheduled_for,
            )
            session.add(task)
        sample_case.creator_type = evidence.content_type
        self._fill_task_preview(task, sample_case, content_url=evidence.content_url)
        return created

    def _fill_task_preview(
        self,
        task: FollowupTask,
        sample_case: SampleCase,
        *,
        content_url: str = "",
    ) -> None:
        if task.status in {"skipped", "suppressed"}:
            return
        if followup_task_completed(
            {"sent_at": task.sent_at, "send_result": task.send_result}
        ):
            return
        language = choose_followup_language(
            bio=sample_case.bio or None,
            feishu_lang=sample_case.feishu_lang or None,
            manual_lang=sample_case.language or None,
        )
        creator_type = choose_followup_creator_type(
            manual_type=sample_case.creator_type or None,
            is_video_creator=sample_case.is_video_creator,
            is_live_creator=sample_case.is_live_creator,
        )
        attachment = resolve_followup_attachment(
            product_id=sample_case.product_id,
            resolved_sku=sample_case.resolved_sku,
        )
        action_kind = task.action_kind or action_kind_for_stage(task.stage)
        template_key = choose_template_key(
            stage=task.stage,
            creator_type=creator_type["creator_type"],
            lang=language["lang"],
            is_hero_sku=attachment["matched"],
        )
        review_reason = ""
        if creator_type["needs_review"]:
            review_reason = str(creator_type.get("review_reason") or "missing_creator_type")
        elif action_kind == ACTION_KIND_SEND_MESSAGE and not template_key:
            review_reason = "missing_template"
        elif action_kind == ACTION_KIND_ACKNOWLEDGE_CONTENT and not template_key:
            review_reason = "missing_template"

        needs_review = bool(review_reason)
        task.action_kind = action_kind
        task.language = language["lang"]
        task.creator_type = creator_type["creator_type"] or ""
        task.template_key = template_key or ""
        task.template_version = TEMPLATE_VERSION
        if template_key:
            task.message_preview = render_followup_message(
                template_key,
                creator_name=sample_case.creator_name,
                content_url=content_url,
            )
        elif action_kind == ACTION_KIND_LIST_ONLY:
            task.message_preview = "到货后第 10 天仍未发布。导出名单交给业务，不自动发私信。"
        elif action_kind == ACTION_KIND_MARK_UNFULFILLED:
            task.message_preview = "到货 15 天仍未发布。确认后可将飞书合作状态改为「未发布」。"
        elif action_kind == ACTION_KIND_CONFIRM_DELIVERY_DATE:
            task.message_preview = followup_review_label("missing_delivery_time")
        else:
            task.message_preview = followup_review_label(review_reason) if review_reason else ""
        task.attachment_key = (
            attachment["path"]
            if attachment["matched"] and action_kind == ACTION_KIND_SEND_MESSAGE
            else ""
        )
        task.requires_manual_confirmation = needs_review
        task.review_reason = review_reason
        if task.status not in {"skipped", "suppressed"}:
            task.status = "needs_review" if needs_review else "pending"
        if task.status != "suppressed":
            task.suppressed_reason = ""

    def _populate_language(self, sample_case: SampleCase) -> None:
        if sample_case.language or sample_case.feishu_lang or sample_case.bio:
            return
        from assistant.services.creator_enrich_service import (
            fill_language_from_feishu_then_bio,
        )

        fill_language_from_feishu_then_bio(
            sample_case,
            store_id=self.store_id,
            warning=self.warning,
        )

    def _hydrate_from_latest_export(self) -> None:
        try:
            from lib.export_util import latest_screen_export
            export_path = latest_screen_export()
        except FileNotFoundError:
            return
        try:
            if export_path.suffix.lower() == ".json":
                data = json.loads(export_path.read_text(encoding="utf-8"))
                rows = data.get("rows") or data.get("items") or [] if isinstance(data, dict) else data
            else:
                with export_path.open(encoding="utf-8-sig", newline="") as handle:
                    rows = list(csv.DictReader(handle))
        except Exception as error:
            self.warning(f"筛查导出读取失败：{error}")
            return
        if not isinstance(rows, list):
            return
        with self.session_factory() as session:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                apply_id = str(row.get("apply_id") or "")
                product_id = str(row.get("product_id") or row.get("产品货号/product_id") or "")
                sample_case = session.scalar(
                    select(SampleCase).where(
                        SampleCase.apply_id == apply_id,
                        SampleCase.product_id == product_id,
                    )
                )
                if sample_case is None:
                    continue
                mappings = {
                    "is_video_creator": row.get("is_video_creator") or row.get("视频达人"),
                    "is_live_creator": row.get("is_live_creator") or row.get("直播达人"),
                    "resolved_sku": row.get("resolved_sku") or row.get("解析货号"),
                    "sample_product_option": row.get("sample_product_option") or row.get("寄样产品选项"),
                    "feishu_record_id": row.get("feishu_record_id") or row.get("飞书record_id"),
                }
                for field_name, value in mappings.items():
                    if not getattr(sample_case, field_name) and value:
                        setattr(sample_case, field_name, str(value))
            session.commit()
