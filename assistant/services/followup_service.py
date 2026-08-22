"""Generate local follow-up tasks without sending or writing Feishu."""
from __future__ import annotations

import csv
import json
from datetime import date, datetime

from sqlalchemy import select

from assistant.database.models import FollowupTask, SampleCase, Shipment
from assistant.domain.followup_stage import (
    CONFIRM_DELIVERY_TIME_STAGE,
    CONFIRM_DELIVERY_TIME_SENTINEL,
    days_since_delivery,
    plan_followup_mutation,
)
from assistant.domain.message_templates import TEMPLATE_VERSION, choose_template_key, render_followup_message
from assistant.domain.policies import choose_followup_creator_type, choose_followup_language
from assistant.domain.sku_images import resolve_followup_attachment
from assistant.domain.timeutil import beijing_now
from scripts.lib.filters import ACTIVE_HERO_PRODUCT_ID


class FollowupService:
    def __init__(self, session_factory, *, warning=None) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)

    def generate(self, now: datetime | None = None) -> dict:
        self._hydrate_from_latest_export()
        effective_now = beijing_now(now)
        created = 0
        with self.session_factory() as session:
            cases = session.scalars(select(SampleCase)).all()
            for sample_case in cases:
                if sample_case.product_id != ACTIVE_HERO_PRODUCT_ID:
                    continue
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
                if int(sample_case.curr_status or 0) != 40 or shipment.delivered_at is None:
                    continue
                days = days_since_delivery(shipment.delivered_at, today=effective_now.date())
                existing_tasks = session.scalars(
                    select(FollowupTask).where(
                        FollowupTask.sample_case_id == sample_case.id,
                        FollowupTask.stage != CONFIRM_DELIVERY_TIME_STAGE,
                    )
                ).all()
                mutation = plan_followup_mutation(
                    existing_unpublished=[
                        {"stage": task.stage, "scheduled_for": task.scheduled_for, "status": task.status}
                        for task in existing_tasks
                    ],
                    days=days,
                    has_confirmed_content=False,
                    today=effective_now.date(),
                )
                for task_creation in mutation["create"]:
                    created += self._upsert_stage(
                        session,
                        sample_case,
                        task_creation["stage"],
                        task_creation["scheduled_for"],
                    )
                for suppression in mutation["suppress"]:
                    task = session.scalar(
                        select(FollowupTask).where(
                            FollowupTask.sample_case_id == sample_case.id,
                            FollowupTask.stage == suppression["stage"],
                            FollowupTask.scheduled_for == suppression["scheduled_for"],
                        )
                    )
                    if task is not None:
                        task.status = "suppressed"
                        task.suppressed_reason = suppression["reason"]
            session.commit()
        return {"created": created}

    def refresh_task(self, task_id: int) -> None:
        with self.session_factory() as session:
            task = session.get(FollowupTask, task_id)
            if task is None:
                raise ValueError("followup-not-found")
            sample_case = session.get(SampleCase, task.sample_case_id)
            self._fill_task_preview(task, sample_case)
            session.commit()

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
                scheduled_for=CONFIRM_DELIVERY_TIME_SENTINEL,
            )
            session.add(task)
        task.status = "needs_review"
        task.requires_manual_confirmation = True
        task.message_preview = "请人工确认实际送达日期；预计送达时间不能作为 D0。"
        task.suppressed_reason = ""
        return created

    def _suppress_confirmation(self, session, sample_case_id: int) -> None:
        task = session.scalar(
            select(FollowupTask).where(
                FollowupTask.sample_case_id == sample_case_id,
                FollowupTask.stage == CONFIRM_DELIVERY_TIME_STAGE,
            )
        )
        if task is not None and task.status in {"pending", "ready", "needs_review"}:
            task.status = "suppressed"
            task.suppressed_reason = "delivery_time_known"

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
            task = FollowupTask(sample_case_id=sample_case.id, stage=stage, scheduled_for=scheduled_for)
            session.add(task)
        self._populate_readonly_language(sample_case)
        self._fill_task_preview(task, sample_case)
        return created

    def _fill_task_preview(self, task: FollowupTask, sample_case: SampleCase) -> None:
        language = choose_followup_language(
            bio=None,
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
        template_key = choose_template_key(
            stage=task.stage,
            creator_type=creator_type["creator_type"],
            lang=language["lang"],
            is_hero_sku=attachment["matched"],
        )
        task.language = language["lang"]
        task.creator_type = creator_type["creator_type"] or ""
        task.template_key = template_key or ""
        task.template_version = TEMPLATE_VERSION
        task.message_preview = (
            render_followup_message(template_key, creator_name=sample_case.creator_name)
            if template_key else ""
        )
        task.attachment_key = attachment["path"] if attachment["matched"] else ""
        task.requires_manual_confirmation = bool(
            language["needs_review"] or creator_type["needs_review"] or not template_key
        )
        task.status = "needs_review" if task.requires_manual_confirmation else "pending"

    def _populate_readonly_language(self, sample_case: SampleCase) -> None:
        if sample_case.language or sample_case.feishu_lang:
            return
        try:
            from lib.app_config import load_bitable_settings
            from lib.feishu_bitable import (
                DEFAULT_BITABLE_APP_ID,
                _field_plain,
                find_duplicate_record,
                get_bitable_access_token,
                list_sample_product_options,
                match_sample_product_option,
            )
            settings = load_bitable_settings(default_app_id=DEFAULT_BITABLE_APP_ID)
            if not settings.get("app_id") or not settings.get("app_secret"):
                return
            token = get_bitable_access_token()
            sample_product = sample_case.sample_product_option
            if not sample_product:
                sample_product = match_sample_product_option(
                    "B005", list_sample_product_options(token)
                ) or ""
                if sample_product:
                    sample_case.sample_product_option = sample_product
                    sample_case.resolved_sku = "B005"
            if not sample_product:
                return
            record = find_duplicate_record(
                token,
                creator_handle=sample_case.creator_name,
                sample_product=sample_product,
            )
            language = _field_plain((record or {}).get("fields", {}).get("使用语言"))
            if language in {"英语", "西班牙语"}:
                sample_case.feishu_lang = language
        except Exception as error:
            self.warning(f"飞书语言只读查询失败：{error}")

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
