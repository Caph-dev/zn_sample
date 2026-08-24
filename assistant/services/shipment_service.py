"""Read-only shipment synchronization for free-sample tabs 30 and 40."""
from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from assistant.database.models import SampleCase, Shipment, ShipmentSnapshot, Store
from assistant.jobs.registry import HandlerFailure
from assistant.services.store_service import StoreService

logger = logging.getLogger(__name__)


VALID_ORDER_ID = re.compile(r"^\d{15,20}$")

# 增量刷新周期：使用代码常量而不是散落数字。
ACTIVE_SHIPMENT_REFRESH_INTERVAL = timedelta(hours=6)
EXCEPTION_SHIPMENT_REFRESH_INTERVAL = timedelta(hours=1)
DELIVERED_SHIPMENT_REFRESH_INTERVAL = None  # 完整 Delivered 默认永久跳过
EXCEPTION_SHIPMENT_STATUSES = frozenset({"exception", "returned", "lost"})


@dataclass
class ShipmentRowCandidate:
    row: dict[str, Any]
    source_status: int
    sample_case_id: int | None = None


@dataclass
class OrderQueryPlan:
    main_order_id: str
    fulfill_unit_ids: tuple[str, ...]
    candidates: list[ShipmentRowCandidate]
    candidates_by_case: dict[tuple[str, str, str], ShipmentRowCandidate] = field(
        default_factory=dict
    )


class ShipmentService:
    def __init__(
        self,
        session_factory,
        *,
        warning=None,
        cancel_check=None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)
        self.cancel_check = cancel_check or (lambda: None)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _check_cancelled(self) -> None:
        self.cancel_check()

    @staticmethod
    def _normalize_order_id(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_fulfill_unit_ids(values: Any) -> tuple[str, ...]:
        """字符串化、去空白、丢弃空值、按首次出现顺序去重。"""
        seen: set[str] = set()
        normalized: list[str] = []
        for value in values or ():
            text = "" if value is None else str(value).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)

    @staticmethod
    def _merge_fulfill_unit_ids(
        existing: tuple[str, ...],
        incoming: tuple[str, ...],
    ) -> tuple[str, ...]:
        merged = list(existing)
        for unit_id in incoming:
            if unit_id not in merged:
                merged.append(unit_id)
        return tuple(merged)

    @staticmethod
    def _request_fingerprint(
        main_order_id: str,
        fulfill_unit_ids: tuple[str, ...],
    ) -> str:
        """订单号 + 规范化 fulfill IDs 的稳定 SHA-256 指纹。"""
        payload = main_order_id + "\x00" + "\x00".join(fulfill_unit_ids)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _shipment_business_fingerprint(shipment: Shipment) -> str:
        """规范化业务状态指纹；不含 checked_at / 同步元数据。"""
        payload = "\x00".join(
            (
                str(shipment.tracking_number or ""),
                str(shipment.carrier or ""),
                str(shipment.status_code or ""),
                str(shipment.status_category or ""),
                str(shipment.status_label or ""),
                str(shipment.estimated_delivery_at or ""),
                str(shipment.delivered_at or ""),
                str(shipment.last_event_at or ""),
                str(shipment.last_event_text or ""),
                str(shipment.package_count or 0),
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_shipments(self, sample_case_ids: list[int]) -> dict[int, Shipment]:
        if not sample_case_ids:
            return {}
        with self.session_factory() as session:
            shipments = session.scalars(
                select(Shipment).where(Shipment.sample_case_id.in_(sample_case_ids))
            ).all()
            for shipment in shipments:
                session.expunge(shipment)
            return {shipment.sample_case_id: shipment for shipment in shipments}

    def _should_refresh_shipment(
        self,
        shipment: Shipment | None,
        *,
        now: datetime,
        fingerprint: str,
    ) -> bool:
        """单个关联 Shipment 是否需要本轮远端查询。"""
        if shipment is None:
            return True
        if not shipment.tracking_number:
            return True
        if (shipment.request_fingerprint or "") != fingerprint:
            return True
        last_success = shipment.last_successful_sync_at
        had_recent_error = (
            shipment.last_error_at is not None
            and (last_success is None or shipment.last_error_at > last_success)
        )
        if had_recent_error:
            return True
        category = str(shipment.status_category or "unknown")
        if category == "unknown":
            return True
        if category == "delivered":
            # 只有时间、单号完整且无需人工确认才能直接跳过。
            return bool(
                shipment.delivered_at is None
                or shipment.needs_delivery_time_confirmation
                or not shipment.tracking_number
            )
        if category in EXCEPTION_SHIPMENT_STATUSES:
            if last_success is None:
                return True
            return now - last_success >= EXCEPTION_SHIPMENT_REFRESH_INTERVAL
        if last_success is None:
            return True
        return now - last_success >= ACTIVE_SHIPMENT_REFRESH_INTERVAL

    def _apply_details(self, shipment: Shipment, details: dict) -> None:
        """安全合并远端观察：空值不回退已有事实，unknown 不覆盖 Delivered。"""
        incoming_tracking = str(details.get("tracking_no") or "").strip()
        incoming_carrier = str(details.get("carrier") or "").strip()
        incoming_category = str(details.get("status_category") or "unknown").strip()
        incoming_label = str(details.get("status_label") or "").strip()
        incoming_delivered_at = details.get("delivered_at")
        incoming_estimated = details.get("estimated_delivery_at")
        incoming_last_event_at = details.get("last_event_at")
        incoming_last_event_text = str(details.get("last_event_text") or "").strip()
        incoming_package_count = int(details.get("package_count") or 0)
        incoming_via = str(details.get("via") or "").strip()

        confirmed_delivered = shipment.delivered_at is not None

        if incoming_tracking:
            shipment.tracking_number = incoming_tracking
            shipment.tracking_display = str(
                details.get("tracking_raw") or incoming_tracking
            )
        if incoming_carrier:
            shipment.carrier = incoming_carrier

        if (
            confirmed_delivered
            and incoming_category != "delivered"
            and incoming_delivered_at is None
        ):
            # stale 的 in-transit/unknown 响应不得回退已确认的 Delivered。
            pass
        else:
            shipment.status_category = incoming_category
            shipment.status_label = incoming_label

        if incoming_delivered_at is not None:
            shipment.delivered_at = incoming_delivered_at
        if incoming_estimated is not None:
            shipment.estimated_delivery_at = incoming_estimated
        if incoming_last_event_at is not None:
            shipment.last_event_at = incoming_last_event_at
        if incoming_last_event_text:
            shipment.last_event_text = incoming_last_event_text
        if incoming_package_count:
            shipment.package_count = incoming_package_count
        if incoming_via:
            shipment.source = incoming_via
        shipment.needs_delivery_time_confirmation = bool(
            details.get("needs_delivery_time_confirmation")
        )

    def _persist_shipment(
        self,
        sample_case_id: int | None,
        details: dict,
        *,
        fingerprint: str,
        synced: bool,
        now: datetime,
    ) -> bool:
        """按安全合并规则持久化一次远端观察，并按需新增 Snapshot。"""
        if sample_case_id is None:
            return False
        with self.session_factory() as session:
            shipment = session.scalar(
                select(Shipment).where(Shipment.sample_case_id == sample_case_id)
            )
            is_new = shipment is None
            if shipment is None:
                shipment = Shipment(sample_case_id=sample_case_id)
                session.add(shipment)
                session.flush()
            previous_fingerprint = self._shipment_business_fingerprint(shipment)
            previous_was_error = bool(shipment.last_error) and (
                shipment.last_successful_sync_at is None
                or (shipment.last_error_at or now) >= shipment.last_successful_sync_at
            )
            self._apply_details(shipment, details)
            shipment.last_attempted_at = now
            shipment.last_checked_at = now
            if synced:
                shipment.last_successful_sync_at = now
                shipment.request_fingerprint = fingerprint
                shipment.last_error = ""
                shipment.last_error_at = None
            session.flush()
            new_fingerprint = self._shipment_business_fingerprint(shipment)
            snapshot_needed = (
                is_new
                or new_fingerprint != previous_fingerprint
                or previous_was_error
            )
            if snapshot_needed:
                session.add(
                    ShipmentSnapshot(
                        shipment_id=shipment.id,
                        status_code=shipment.status_code,
                        status_label=shipment.status_label,
                        status_category=shipment.status_category,
                        estimated_delivery_at=shipment.estimated_delivery_at,
                        delivered_at=shipment.delivered_at,
                        last_event_at=shipment.last_event_at,
                        last_event_text=shipment.last_event_text,
                        raw_payload_hash=str(details.get("raw_payload_hash") or ""),
                        source=shipment.source,
                    )
                )
            session.commit()
            return new_fingerprint != previous_fingerprint

    def _record_order_error(
        self,
        sample_case_ids: list[int | None],
        *,
        error: Exception,
        now: datetime,
    ) -> bool:
        """单订单查询失败：更新错误元数据，不清空任何物流事实。"""
        sanitized_error = f"{type(error).__name__}: {str(error)[:200]}"
        changed = False
        with self.session_factory() as session:
            for sample_case_id in sample_case_ids:
                if sample_case_id is None:
                    continue
                shipment = session.scalar(
                    select(Shipment).where(Shipment.sample_case_id == sample_case_id)
                )
                if shipment is None:
                    continue
                previous_was_success = shipment.last_successful_sync_at is not None
                previous_error = str(shipment.last_error or "")
                shipment.last_attempted_at = now
                shipment.last_error = sanitized_error
                shipment.last_error_at = now
                session.flush()
                snapshot_needed = previous_was_success or (
                    bool(previous_error) and previous_error != sanitized_error
                )
                if snapshot_needed:
                    session.add(
                        ShipmentSnapshot(
                            shipment_id=shipment.id,
                            error=sanitized_error,
                            source="error",
                        )
                    )
                    changed = True
            session.commit()
            return changed

    @staticmethod
    def _mask_order_id(order_id: str) -> str:
        """订单号只保留脱敏后缀，避免日志携带完整敏感标识。"""
        if len(order_id) <= 4:
            return "*" * len(order_id) or "-"
        return f"****{order_id[-4:]}"

    @staticmethod
    def _order_error_stage(error: Exception) -> str:
        from lib.sync_errors import (
            LogisticsRequestTimeout,
            SellerNavigationTimeout,
            SellerPageReadinessTimeout,
        )

        if isinstance(error, (SellerNavigationTimeout, SellerPageReadinessTimeout)):
            return "seller_context"
        if isinstance(error, LogisticsRequestTimeout):
            return "logistics_get"
        if type(error).__name__ == "TimeoutExpired":
            return "logistics_get"
        if type(error).__name__ == "PageApiSchemaError":
            return "parse"
        return "logistics_get"

    @staticmethod
    def _seller_context_lost(store_id: str) -> bool:
        """探测当前页面是否已离开商家订单页 host。探测失败也视为失效。"""
        from lib.sample_navigation import current_page_href, is_seller_order_href

        try:
            href = current_page_href(store_id)
        except Exception:
            return True
        return not is_seller_order_href(href)

    @staticmethod
    def _unknown_details(order_id: str) -> dict:
        return {
            "tracking_no": "", "tracking_raw": "", "carrier": "",
            "status_label": "", "status_category": "unknown",
            "estimated_delivery_at": None, "delivered_at": None,
            "last_event_at": None, "last_event_text": "", "package_count": 0,
            "needs_delivery_time_confirmation": False, "raw_payload_hash": "",
            "via": "missing-order-id" if not order_id else "invalid-order-id",
        }

    def synchronize_shipments(self, store: dict, *, job_progress) -> dict:
        from lib.order_api import (
            enter_seller_order_page,
            fetch_logistics_details_in_seller_context,
        )
        from lib.page_api import PageApiSchemaError
        from lib.sample_api import scrape_processing_list_api, scrape_shipped_list_api
        from lib.sample_navigation import navigate_to_sample_request
        from lib.shipped_dom import ensure_sample_page_loaded, scrape_shipped_list
        from lib.debug_log import debug_log

        store_model = StoreService(self.session_factory).upsert_store(store)
        ziniao_store_id = store_model.ziniao_store_id
        page_state = ensure_sample_page_loaded(ziniao_store_id)
        # region agent log
        debug_log(
            "shipment-page-loaded",
            location="assistant/services/shipment_service.py:synchronize_shipments",
            hypothesisId="H3",
            href=str((page_state or {}).get("href") or "")[:180],
            already=bool((page_state or {}).get("already")),
        )
        # endregion

        destination = (page_state or {}).get("destination") or {}
        if not store_model.shop_id:
            # running 列表只有 storeId/storeName。样品申请页 URL / 导航结果带 shop_id，
            # 物流详情查询依赖它跳商家订单页，因此回填一次并持久化。
            from lib.sample_navigation import current_page_href, is_sample_request_href
            from urllib.parse import parse_qs, urlsplit

            resolved_shop_id = str(destination.get("shop_id") or "").strip()
            if not resolved_shop_id:
                page_href = str((page_state or {}).get("href") or "")
                try:
                    if not page_href:
                        page_href = current_page_href(ziniao_store_id)
                except Exception:
                    page_href = page_href or ""
                if is_sample_request_href(page_href):
                    shop_id_values = parse_qs(urlsplit(page_href).query).get("shop_id")
                    resolved_shop_id = str(
                        shop_id_values[0] if shop_id_values else ""
                    ).strip()
            if resolved_shop_id:
                store_model.shop_id = resolved_shop_id
                with self.session_factory() as session:
                    stored = session.get(Store, store_model.id)
                    if stored is not None:
                        stored.shop_id = resolved_shop_id
                        session.commit()
        if not store_model.shop_region:
            resolved_region = str(destination.get("shop_region") or "").strip().upper()
            if resolved_region:
                store_model.shop_region = resolved_region

        try:
            shipped_rows = scrape_shipped_list_api(ziniao_store_id)
            # region agent log
            debug_log(
                "shipped-list-api-ok",
                location="assistant/services/shipment_service.py:synchronize_shipments",
                hypothesisId="H3",
                row_count=len(shipped_rows),
            )
            # endregion
        except PageApiSchemaError as api_error:
            # region agent log
            debug_log(
                "shipped-list-api-fallback",
                location="assistant/services/shipment_service.py:synchronize_shipments",
                hypothesisId="H3",
                error_type=type(api_error).__name__,
                error=str(api_error)[:240],
            )
            # endregion
            shipped_rows = scrape_shipped_list(ziniao_store_id)
        except Exception as api_error:
            # region agent log
            debug_log(
                "shipped-list-api-fallback",
                location="assistant/services/shipment_service.py:synchronize_shipments",
                hypothesisId="H3",
                error_type=type(api_error).__name__,
                error=str(api_error)[:240],
            )
            # endregion
            shipped_rows = scrape_shipped_list(ziniao_store_id)
        try:
            processing_rows = scrape_processing_list_api(ziniao_store_id)
        except PageApiSchemaError as error:
            self.warning(f"处理中列表 API 失败，未使用已发货 DOM 回退：{error}")
            processing_rows = []

        combined_rows = [(row, 30) for row in shipped_rows]
        combined_rows.extend((row, 40) for row in processing_rows)
        total = len(combined_rows)

        changed = 0
        synchronized = 0
        duplicate_rows = 0
        seen_valid_orders: set[str] = set()
        # 无合法订单号的行先直接落 unknown；有合法订单号的行按唯一订单聚合后批量查询。
        pending_plans: dict[str, OrderQueryPlan] = {}
        for index, (row, source_status) in enumerate(combined_rows, 1):
            self._check_cancelled()
            job_progress(index - 1, total, f"同步 {index}/{total}")
            sample_case = self._upsert_sample_case(store_model.id, row, source_status)
            if sample_case is None:
                continue
            order_id = self._normalize_order_id(row.get("main_order_id"))
            if not VALID_ORDER_ID.fullmatch(order_id):
                changed += int(
                    self._persist_shipment(
                        sample_case.id,
                        self._unknown_details(order_id),
                        fingerprint="",
                        synced=False,
                        now=self.clock(),
                    )
                )
                synchronized += 1
                continue
            if order_id in seen_valid_orders:
                duplicate_rows += 1
            else:
                seen_valid_orders.add(order_id)
            plan = pending_plans.setdefault(
                order_id,
                OrderQueryPlan(
                    main_order_id=order_id,
                    fulfill_unit_ids=(),
                    candidates=[],
                ),
            )
            plan.fulfill_unit_ids = self._merge_fulfill_unit_ids(
                plan.fulfill_unit_ids,
                self._normalize_fulfill_unit_ids(row.get("fulfill_unit_ids")),
            )
            case_key = (
                str(sample_case.apply_id or "").strip(),
                str(sample_case.creator_id or "").strip(),
                str(sample_case.product_id or "").strip(),
            )
            existing_candidate = plan.candidates_by_case.get(case_key)
            if existing_candidate is None:
                candidate = ShipmentRowCandidate(
                    row=row,
                    source_status=source_status,
                    sample_case_id=sample_case.id,
                )
                plan.candidates.append(candidate)
                plan.candidates_by_case[case_key] = candidate
            else:
                # 同一申请同时出现在 tab 30/40：状态只升级不回退，本轮只持久化一次。
                existing_candidate.source_status = max(
                    existing_candidate.source_status,
                    source_status,
                )
                existing_candidate.row = row

        fetched_orders = 0
        skipped_orders = 0
        failed_orders = 0
        refresh_plans: list[tuple[OrderQueryPlan, str]] = []
        if pending_plans:
            now = self.clock()
            for plan in pending_plans.values():
                fingerprint = self._request_fingerprint(
                    plan.main_order_id,
                    plan.fulfill_unit_ids,
                )
                sample_case_ids = [
                    candidate.sample_case_id
                    for candidate in plan.candidates
                    if candidate.sample_case_id is not None
                ]
                shipments = self._load_shipments(sample_case_ids)
                needs_refresh = any(
                    self._should_refresh_shipment(
                        shipments.get(sample_case_id),
                        now=now,
                        fingerprint=fingerprint,
                    )
                    for sample_case_id in sample_case_ids
                )
                if needs_refresh:
                    refresh_plans.append((plan, fingerprint))
                else:
                    skipped_orders += 1

        entered_seller_order_page = False
        try:
            if refresh_plans:
                from lib.order_api import (
                    PER_ORDER_LOGISTICS_TIMEOUT,
                    RETURN_TO_SAMPLE_TIMEOUT,
                    SELLER_NAVIGATION_TIMEOUT,
                    SELLER_PAGE_READY_TIMEOUT,
                )
                from lib.sync_errors import BatchDeadlineExceeded

                batch_deadline = time.monotonic() + (
                    SELLER_NAVIGATION_TIMEOUT
                    + SELLER_PAGE_READY_TIMEOUT
                    + PER_ORDER_LOGISTICS_TIMEOUT * len(refresh_plans)
                    + RETURN_TO_SAMPLE_TIMEOUT
                )

                def report_page_progress(message: str) -> None:
                    job_progress(0, total, message)

                context = enter_seller_order_page(
                    ziniao_store_id,
                    shop_id=store_model.shop_id,
                    shop_region=store_model.shop_region,
                    progress=report_page_progress,
                    deadline=batch_deadline,
                )
                entered_seller_order_page = True
                for order_index, (plan, fingerprint) in enumerate(refresh_plans, 1):
                    self._check_cancelled()
                    if time.monotonic() >= batch_deadline:
                        raise BatchDeadlineExceeded(
                            f"物流同步批次 deadline 已到，剩余订单 {len(refresh_plans) - order_index + 1} 个未查询"
                        )
                    job_progress(
                        0,
                        total,
                        f"开始查询 {order_index}/{len(refresh_plans)}",
                    )
                    now = self.clock()
                    sample_case_ids = [
                        candidate.sample_case_id
                        for candidate in plan.candidates
                        if candidate.sample_case_id is not None
                    ]
                    order_deadline = min(
                        batch_deadline,
                        time.monotonic() + PER_ORDER_LOGISTICS_TIMEOUT,
                    )
                    details: dict | None = None
                    order_started_at = time.monotonic()
                    try:
                        details = fetch_logistics_details_in_seller_context(
                            ziniao_store_id,
                            plan.main_order_id,
                            context=context,
                            fulfill_unit_ids=plan.fulfill_unit_ids,
                            deadline=order_deadline,
                        )
                    except Exception as error:
                        elapsed_ms = int((time.monotonic() - order_started_at) * 1000)
                        stage = self._order_error_stage(error)
                        order_id_suffix = self._mask_order_id(plan.main_order_id)
                        logger.warning(
                            "event=shipment_order_failed stage=%s order_id_suffix=%s "
                            "attempt=%d error_type=%s elapsed_ms=%d",
                            stage,
                            order_id_suffix,
                            1,
                            type(error).__name__,
                            elapsed_ms,
                        )
                        if self._seller_context_lost(ziniao_store_id):
                            # 页面离开订单 host / context 失效：停止新查询，进入批次恢复。
                            # region agent log
                            debug_log(
                                "seller-context-lost",
                                location="assistant/services/shipment_service.py:synchronize_shipments",
                                hypothesisId="H-detail",
                                order_id=plan.main_order_id,
                                error_type=type(error).__name__,
                            )
                            # endregion
                            raise RuntimeError(
                                "订单页上下文失效，已停止本批查询："
                                f"error_type={type(error).__name__}"
                            ) from error
                        # Isolate one page/API failure without turning it into
                        # unknown data or stopping later orders.
                        failed_orders += 1
                        changed += int(
                            self._record_order_error(
                                sample_case_ids,
                                error=error,
                                now=now,
                            )
                        )
                        # region agent log
                        debug_log(
                            "logistics-detail-error",
                            location="assistant/services/shipment_service.py:synchronize_shipments",
                            hypothesisId="H-detail",
                            order_id=plan.main_order_id,
                            error_type=type(error).__name__,
                            error=str(error)[:400],
                            shop_id=store_model.shop_id,
                            shop_region=store_model.shop_region,
                        )
                        # endregion
                        self.warning(
                            f"订单 {order_id_suffix} 物流详情读取失败，已跳过："
                            f"阶段={stage} 错误类型={type(error).__name__} "
                            f"耗时={elapsed_ms / 1000:.1f} 秒"
                        )
                        continue
                    if not isinstance(details, dict) or details.get("ok") is False:
                        failed_orders += 1
                        self.warning(
                            "物流详情响应结构异常，已跳过该行："
                            f"order_id={plan.main_order_id}"
                        )
                        continue
                    fetched_orders += 1
                    for candidate in plan.candidates:
                        changed += int(
                            self._persist_shipment(
                                candidate.sample_case_id,
                                details,
                                fingerprint=fingerprint,
                                synced=True,
                                now=now,
                            )
                        )
                        synchronized += 1
            job_progress(total, total, "物流同步完成")
            logger.info(
                "event=shipment_sync_batch source_rows=%d valid_rows=%d "
                "unique_orders=%d duplicate_orders=%d fetched_orders=%d "
                "skipped_orders=%d failed_orders=%d changed_shipments=%d",
                total,
                duplicate_rows + len(seen_valid_orders),
                len(seen_valid_orders),
                duplicate_rows,
                fetched_orders,
                skipped_orders,
                failed_orders,
                changed,
            )
            return {
                "rows": total,
                "unique_orders": len(seen_valid_orders),
                "duplicate_rows": duplicate_rows,
                "fetched": fetched_orders,
                "skipped": skipped_orders,
                "failed": failed_orders,
                "changed": changed,
                "synchronized_cases": synchronized,
                "synchronized": synchronized,
                "processing": len(processing_rows),
            }
        finally:
            if entered_seller_order_page:
                from lib.order_api import RETURN_TO_SAMPLE_TIMEOUT

                try:
                    navigation_result = navigate_to_sample_request(
                        ziniao_store_id,
                        shop_id=store_model.shop_id,
                        shop_region=store_model.shop_region,
                        timeout=RETURN_TO_SAMPLE_TIMEOUT,
                    )
                    if (
                        not isinstance(navigation_result, dict)
                        or not navigation_result.get("ok")
                    ):
                        raise RuntimeError(
                            "样品申请页导航未确认成功: "
                            f"{navigation_result!r}"[:300]
                        )
                except Exception as error:
                    # region agent log
                    debug_log(
                        "return-sample-error",
                        location="assistant/services/shipment_service.py:synchronize_shipments",
                        hypothesisId="H-return",
                        error_type=type(error).__name__,
                        error=str(error)[:400],
                        shop_id=store_model.shop_id,
                        shop_region=store_model.shop_region,
                    )
                    # endregion
                    self.warning(
                        "回样品申请页失败，已停止本批："
                        f"error_type={type(error).__name__}"
                    )
                    raise HandlerFailure(
                        "sample-navigation-failed",
                        "回样品申请页失败，已停止后续物流同步。",
                    ) from error

    def _upsert_sample_case(self, store_id: int, row: dict, source_status: int) -> SampleCase | None:
        apply_id = str(row.get("apply_id") or "").strip()
        creator_id = str(row.get("creator_id") or "").strip()
        product_id = str(row.get("product_id") or "").strip()
        if not all((apply_id, creator_id, product_id)):
            self.warning("样品行缺 apply_id/creator_id/product_id，已跳过")
            return None
        with self.session_factory() as session:
            model = session.scalar(
                select(SampleCase).where(
                    SampleCase.store_id == store_id,
                    SampleCase.creator_id == creator_id,
                    SampleCase.product_id == product_id,
                    SampleCase.apply_id == apply_id,
                )
            )
            now = self.clock()
            if model is None:
                model = SampleCase(
                    store_id=store_id,
                    creator_id=creator_id,
                    creator_name=str(row.get("creator_name") or ""),
                    apply_id=apply_id,
                    product_id=product_id,
                    first_seen_at=now,
                )
                session.add(model)
            model.creator_name = str(row.get("creator_name") or model.creator_name)
            model.creator_nickname = str(row.get("nick_name") or model.creator_nickname or "")
            model.sku_id = str(row.get("sku_id") or model.sku_id or "")
            model.main_order_id = str(row.get("main_order_id") or model.main_order_id or "")
            model.curr_status = max(int(model.curr_status or 0), source_status)
            model.last_seen_at = now
            session.commit()
            session.refresh(model)
            session.expunge(model)
            return model
