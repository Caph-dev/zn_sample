"""Read-only shipment synchronization for free-sample tabs 30 and 40."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import select

from assistant.database.models import SampleCase, Shipment, ShipmentSnapshot, Store
from assistant.jobs.registry import HandlerFailure
from assistant.services.store_service import StoreService


VALID_ORDER_ID = re.compile(r"^\d{15,20}$")


class ShipmentService:
    def __init__(
        self,
        session_factory,
        *,
        warning=None,
        cancel_check=None,
    ) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)
        self.cancel_check = cancel_check or (lambda: None)

    def _check_cancelled(self) -> None:
        self.cancel_check()

    def synchronize_shipments(self, store: dict, *, job_progress) -> dict:
        from lib.order_api import fetch_tiktok_logistics_details_api
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
        changed = 0
        synchronized = 0
        total = len(combined_rows)
        for index, (row, source_status) in enumerate(combined_rows, 1):
            self._check_cancelled()
            job_progress(index - 1, total, f"同步 {index}/{total}")
            sample_case = self._upsert_sample_case(store_model.id, row, source_status)
            if sample_case is None:
                continue
            details = None
            order_id = str(row.get("main_order_id") or "").strip()
            if VALID_ORDER_ID.fullmatch(order_id):
                detail_error: Exception | None = None
                navigation_error: Exception | None = None
                try:
                    try:
                        details = fetch_tiktok_logistics_details_api(
                            ziniao_store_id,
                            order_id,
                            shop_id=store_model.shop_id,
                            shop_region=store_model.shop_region,
                            fulfill_unit_ids=row.get("fulfill_unit_ids") or (),
                        )
                    except Exception as error:
                        # Isolate one page/API failure without turning it into
                        # unknown data or stopping later orders.
                        detail_error = error
                        # region agent log
                        debug_log(
                            "logistics-detail-error",
                            location="assistant/services/shipment_service.py:synchronize_shipments",
                            hypothesisId="H-detail",
                            order_id=order_id,
                            error_type=type(error).__name__,
                            error=str(error)[:400],
                            shop_id=store_model.shop_id,
                            shop_region=store_model.shop_region,
                        )
                        # endregion
                finally:
                    try:
                        navigation_result = navigate_to_sample_request(
                            ziniao_store_id,
                            shop_id=store_model.shop_id,
                            shop_region=store_model.shop_region,
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
                        navigation_error = error
                        # region agent log
                        debug_log(
                            "return-sample-error",
                            location="assistant/services/shipment_service.py:synchronize_shipments",
                            hypothesisId="H-return",
                            order_id=order_id,
                            error_type=type(error).__name__,
                            error=str(error)[:400],
                            shop_id=store_model.shop_id,
                            shop_region=store_model.shop_region,
                        )
                        # endregion
                if detail_error is not None:
                    self.warning(
                        "物流详情读取失败，已跳过该行："
                        f"order_id={order_id} "
                        f"error_type={type(detail_error).__name__}"
                    )
                if navigation_error is not None:
                    self.warning(
                        "回样品申请页失败，已停止本批："
                        f"order_id={order_id} "
                        f"error_type={type(navigation_error).__name__}"
                    )
                    raise HandlerFailure(
                        "sample-navigation-failed",
                        "回样品申请页失败，已停止后续物流同步。",
                    ) from navigation_error
                if detail_error is not None:
                    continue
                if not isinstance(details, dict) or details.get("ok") is False:
                    self.warning(
                        "物流详情响应结构异常，已跳过该行："
                        f"order_id={order_id}"
                    )
                    continue
            if details is None:
                details = self._unknown_details(order_id)
            changed += int(self._persist_shipment(sample_case.id, details))
            synchronized += 1
        job_progress(total, total, "物流同步完成")
        return {"synchronized": synchronized, "changed": changed, "processing": len(processing_rows)}

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
            now = datetime.now(timezone.utc)
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

    def _persist_shipment(self, sample_case_id: int, details: dict) -> bool:
        with self.session_factory() as session:
            shipment = session.scalar(select(Shipment).where(Shipment.sample_case_id == sample_case_id))
            if shipment is None:
                shipment = Shipment(sample_case_id=sample_case_id)
                session.add(shipment)
                previous = ("", "", None)
            else:
                previous = (shipment.status_category, shipment.tracking_number, shipment.delivered_at)
            shipment.tracking_number = str(details.get("tracking_no") or "")
            shipment.tracking_display = str(details.get("tracking_raw") or "")
            shipment.carrier = str(details.get("carrier") or "")
            shipment.status_label = str(details.get("status_label") or "")
            shipment.status_category = str(details.get("status_category") or "unknown")
            shipment.estimated_delivery_at = details.get("estimated_delivery_at")
            shipment.delivered_at = details.get("delivered_at")
            shipment.last_event_at = details.get("last_event_at")
            shipment.last_event_text = str(details.get("last_event_text") or "")
            shipment.package_count = int(details.get("package_count") or 0)
            shipment.source = str(details.get("via") or "api")
            shipment.last_checked_at = datetime.now(timezone.utc)
            shipment.needs_delivery_time_confirmation = bool(details.get("needs_delivery_time_confirmation"))
            session.flush()
            session.add(ShipmentSnapshot(
                shipment_id=shipment.id,
                status_label=shipment.status_label,
                status_category=shipment.status_category,
                estimated_delivery_at=shipment.estimated_delivery_at,
                delivered_at=shipment.delivered_at,
                last_event_at=shipment.last_event_at,
                last_event_text=shipment.last_event_text,
                raw_payload_hash=str(details.get("raw_payload_hash") or ""),
                source=shipment.source,
            ))
            current = (shipment.status_category, shipment.tracking_number, shipment.delivered_at)
            session.commit()
            return current != previous

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
