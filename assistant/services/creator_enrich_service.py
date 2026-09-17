"""补齐处理中达人的类型与语言资料（只读）。

两阶段设计，避免「详情页 ↔ 列表」逐人交替跳转导致的页面反复重载：

  阶段 1（页面不动）：类型走页面同源 profile API 批量补齐；
                     语言先查飞书「使用语言」。
  阶段 2（跳页循环）：对飞书无值仍缺语言的样本，逐个异步导航到达人
                     详情页抽简介（顺便补类型），再异步导航回样品申请页。

模块级函数供 CreatorEnrichService 与 FollowupService 兜底路径共用。
所有操作只读：不批准、不发私信、不写飞书。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlsplit

from sqlalchemy import select

from assistant.database.models import SampleCase, Store
from assistant.jobs.registry import JobCancelled
from lib.operation_cancel import OperationCancelled, raise_if_cancelled
from lib.sample_data_source import (
    SYSTEMIC_DETAIL_FAILURE_LIMIT,
    is_systemic_detail_error,
)


@dataclass(frozen=True)
class CreatorReadOutcome:
    """Result of one creator read, distinguishing empty data from read failure."""

    read_ok: bool
    type_filled: bool = False
    language_filled: bool = False
    error_type: str = ""
    error_message: str = ""
    source: str = ""

    def __bool__(self) -> bool:
        """Preserve the former helper contract: truthy means a field was filled."""
        return self.type_filled or self.language_filled


def is_missing_creator_type(sample_case: SampleCase) -> bool:
    """视频/直播标记与手动类型全空才算缺失。"""
    return not (
        str(sample_case.is_video_creator or "").strip()
        or str(sample_case.is_live_creator or "").strip()
        or str(sample_case.creator_type or "").strip()
    )


def is_missing_language(sample_case: SampleCase) -> bool:
    """手动语言、飞书语言、缓存简介全空才算缺失。"""
    return not (
        str(sample_case.language or "").strip()
        or str(sample_case.feishu_lang or "").strip()
        or str(sample_case.bio or "").strip()
    )


def _mark_type_from_metrics(
    sample_case: SampleCase,
    *,
    video_gpm,
    live_gpm,
    warning,
) -> bool:
    """「哪侧有数据」即标该侧。视频+直播同时有数据则双标记，跟进话术按视频达人。"""
    filled = False
    try:
        if video_gpm is not None and float(video_gpm) > 0:
            sample_case.is_video_creator = "是"
            filled = True
        if live_gpm is not None and float(live_gpm) > 0:
            sample_case.is_live_creator = "是"
            filled = True
    except (TypeError, ValueError) as error:
        warning(f"达人类型指标解析失败（{sample_case.creator_name}）：{error}")
        return False
    return filled


def fill_creator_type_from_profile_api(
    sample_case: SampleCase,
    *,
    store_id: str | None,
    warning,
    cancel_check=None,
) -> CreatorReadOutcome:
    """页面同源 profile API 拉类型；需要页面停在样品申请页。"""
    if not store_id:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：没有唯一运行中的店铺")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="missing-store",
            error_message="没有唯一运行中的店铺",
        )
    creator_id = str(sample_case.creator_id or "").strip()
    if not creator_id:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：缺少 creator_id")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="missing-creator-id",
            error_message="缺少 creator_id",
        )
    try:
        from lib.creator_api import fetch_creator_detail_api

        fetch_kwargs = {}
        if cancel_check is not None:
            fetch_kwargs["cancel_check"] = cancel_check
        result = fetch_creator_detail_api(
            store_id,
            {"creator_id": creator_id},
            **fetch_kwargs,
        )
        raise_if_cancelled(cancel_check)
    except (JobCancelled, OperationCancelled):
        raise
    except Exception as error:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：{error}")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="profile-read-error",
            error_message=str(error),
        )
    if not isinstance(result, dict):
        error_message = f"profile API 返回未知类型：{type(result).__name__}"
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：{error_message}")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="profile-read-error",
            error_message=error_message,
        )
    if not result.get("ok"):
        error_message = str(result.get("error") or "profile-api-failed")
        warning(
            f"达人类型补齐失败（{sample_case.creator_name}）："
            f"{error_message}；未将空指标当作无数据"
        )
        return CreatorReadOutcome(
            read_ok=False,
            error_type=str(result.get("error_type") or "profile-read-error"),
            error_message=error_message,
        )
    detail = result.get("detail") or {}
    type_filled = _mark_type_from_metrics(
        sample_case,
        video_gpm=detail.get("video_gpm_n"),
        live_gpm=detail.get("live_gpm_n"),
        warning=warning,
    )
    return CreatorReadOutcome(read_ok=True, type_filled=type_filled, source="profile-api")


def fill_language_from_feishu(
    sample_case: SampleCase,
    *,
    warning,
    cancel_check=None,
) -> bool:
    """飞书「使用语言」只读查询；无值返回 False。"""
    raise_if_cancelled(cancel_check)
    try:
        from lib.app_config import load_bitable_settings
        from lib.feishu_bitable import (
            DEFAULT_BITABLE_APP_ID,
            _field_plain,
            find_duplicate_record,
            get_bitable_access_token,
        )

        settings = load_bitable_settings(default_app_id=DEFAULT_BITABLE_APP_ID)
        if not (settings.get("app_id") and settings.get("app_secret")):
            return False
        token = get_bitable_access_token()
        sample_product = str(sample_case.sample_product_option or "").strip()
        if not sample_product:
            return False
        record = find_duplicate_record(
            token,
            creator_handle=sample_case.creator_name,
            sample_product=sample_product,
        )
        raise_if_cancelled(cancel_check)
        language = _field_plain((record or {}).get("fields", {}).get("使用语言"))
        if language in {"英语", "西班牙语"}:
            sample_case.feishu_lang = language
            return True
        return False
    except (JobCancelled, OperationCancelled):
        raise
    except Exception as error:
        warning(f"飞书语言只读查询失败（{sample_case.creator_name}）：{error}")
        return False


def fill_from_creator_detail_page(
    sample_case: SampleCase,
    *,
    store_id: str | None,
    warning,
    shop_id: str = "",
    shop_region: str = "US",
    cancel_check=None,
) -> CreatorReadOutcome:
    """异步导航到达人详情页抽简介（顺便补类型），再异步回样品申请页。"""
    if not store_id:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：没有唯一运行中的店铺")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="missing-store",
            error_message="没有唯一运行中的店铺",
        )
    creator_id = str(sample_case.creator_id or "").strip()
    if not creator_id:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：缺少 creator_id")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="missing-creator-id",
            error_message="缺少 creator_id",
        )
    try:
        from lib.creator_detail import extract_creator_detail
        from lib.sample_navigation import navigate_to_url

        detail_url = (
            "https://affiliate.tiktokshopglobalselling.com/connection/creator/detail"
            f"?cid={quote(creator_id, safe='')}"
            "&enter_from=sample_request&pair_source=sample_request_click"
            f"&cname={quote(sample_case.creator_name or '', safe='')}"
            f"&shop_region={quote(str(shop_region or 'US'), safe='')}"
        )

        def is_target_detail_href(href: str) -> bool:
            parsed_href = urlsplit(str(href or ""))
            query = parse_qs(parsed_href.query)
            return (
                "creator/detail" in parsed_href.path
                and str((query.get("cid") or [""])[0]).strip() == creator_id
            )

        navigate_to_url(
            store_id,
            detail_url,
            href_matches=is_target_detail_href,
            timeout=90.0,
            poll_interval=0.5,
            cancel_check=cancel_check,
        )
        extract_kwargs = {}
        if cancel_check is not None:
            extract_kwargs["cancel_check"] = cancel_check
        detail = extract_creator_detail(store_id, **extract_kwargs)
        raise_if_cancelled(cancel_check)
        language_filled = False
        if detail.get("detail_read_status") == "page-data-unavailable":
            error_message = str(
                detail.get("detail_error_message") or "详情页资料加载失败"
            )
            warning(
                f"达人详情读取失败（{sample_case.creator_name}）：{error_message}；"
                "未将空 GPM 判定为无数据"
            )
            return CreatorReadOutcome(
                read_ok=False,
                language_filled=language_filled,
                error_type=str(
                    detail.get("detail_error_type") or "page-data-unavailable"
                ),
                error_message=error_message,
            )

        type_filled = False
        bio = str(detail.get("bio") or "").strip()
        if bio and not str(sample_case.bio or "").strip():
            sample_case.bio = bio
            language_filled = True

        if is_missing_creator_type(sample_case):
            type_filled = _mark_type_from_metrics(
                sample_case,
                video_gpm=detail.get("video_gpm_n"),
                live_gpm=detail.get("live_gpm_n"),
                warning=warning,
            )
        return CreatorReadOutcome(
            read_ok=True,
            type_filled=type_filled,
            language_filled=language_filled,
            source="detail-page",
        )
    except (JobCancelled, OperationCancelled):
        raise
    except Exception as error:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：{error}")
        return CreatorReadOutcome(
            read_ok=False,
            error_type="detail-read-error",
            error_message=str(error),
        )
    finally:
        if store_id:
            try:
                from lib.sample_navigation import (
                    is_sample_request_href,
                    navigate_to_url,
                    sample_request_url,
                )

                navigate_to_url(
                    store_id,
                    sample_request_url(shop_id=shop_id, shop_region=shop_region),
                    href_matches=is_sample_request_href,
                    timeout=20.0,
                    poll_interval=0.5,
                    cancel_check=cancel_check,
                )
            except (JobCancelled, OperationCancelled):
                raise
            except Exception as error:
                warning(f"回样品申请页失败：{error}")


def fill_language_from_feishu_then_bio(
    sample_case: SampleCase,
    *,
    store_id: str | None,
    warning,
    shop_id: str = "",
    shop_region: str = "US",
    cancel_check=None,
) -> bool:
    """语言优先级：飞书「使用语言」→ 达人详情简介（缓存 bio 列）。"""
    if fill_language_from_feishu(
        sample_case,
        warning=warning,
        cancel_check=cancel_check,
    ):
        return True
    return fill_from_creator_detail_page(
        sample_case,
        store_id=store_id,
        warning=warning,
        shop_id=shop_id,
        shop_region=shop_region,
        cancel_check=cancel_check,
    ).language_filled


class CreatorEnrichService:
    """补齐跟进池（处理中）样本缺失的类型/语言资料。"""

    def __init__(
        self,
        session_factory,
        *,
        warning=None,
        cancel_check=None,
        store_id=None,
        progress=None,
    ) -> None:
        self.session_factory = session_factory
        self.warning = warning or (lambda message: None)
        self.cancel_check = cancel_check or (lambda: None)
        self.store_id = store_id
        self.progress = progress or (lambda current, total, message: None)

    def _is_low_level_cancellation_requested(self) -> bool:
        """Adapt the job's raising cancellation callback to browser helpers."""
        try:
            self._check_cancellation()
        except JobCancelled:
            return True
        return False

    def _check_cancellation(self) -> None:
        """Accept both raising and boolean cancellation callbacks."""
        if self.cancel_check():
            raise JobCancelled("达人资料补齐已在安全检查点取消")

    def _save_checkpoint(self, session) -> None:
        """Commit one creator's changes without holding a transaction over I/O."""
        session.commit()

    @staticmethod
    def _normalize_read_outcome(outcome) -> CreatorReadOutcome:
        """Keep helper monkey-patches and older callers compatible with bool results."""
        if isinstance(outcome, CreatorReadOutcome):
            return outcome
        if isinstance(outcome, bool):
            return CreatorReadOutcome(read_ok=outcome, type_filled=outcome)
        raise TypeError(f"未知达人读取结果类型：{type(outcome).__name__}")

    @staticmethod
    def _cancelled_result_summary(stats: dict) -> str:
        summary = dict(stats)
        summary["cancelled"] = True
        return json.dumps(summary, ensure_ascii=False, sort_keys=True)

    def enrich(self) -> dict:
        stats = {
            "missing": 0,
            "type_filled": 0,
            "language_filled": 0,
            "failed": 0,
            "type_failures": 0,
            "detail_failures": 0,
            "unprocessed": 0,
        }
        with self.session_factory() as session:
            try:
                cases = session.scalars(
                    select(SampleCase).where(
                        SampleCase.platform_status == "processing",
                        SampleCase.platform_status_stale.is_(False),
                    )
                ).all()
                store = session.scalar(select(Store))
                shop_id = str(store.shop_id or "") if store else ""
                shop_region = str(store.shop_region or "US") if store else "US"

                need_type = [case for case in cases if is_missing_creator_type(case)]
                need_language = [case for case in cases if is_missing_language(case)]
                stats["missing"] = len({case.id for case in need_type + need_language})
                completed_units = 0
                total = max(1, len(need_type) + len(need_language))
                if stats["missing"] and not self.store_id:
                    self.warning("没有唯一运行中的店铺，达人类型和详情简介无法自动补齐")

                # End the read transaction before any browser or network I/O.
                session.commit()

                page_ready = True
                systemic_failure = False
                consecutive_systemic_failures = 0
                if self.store_id and need_type:
                    try:
                        from lib.sample_navigation import ensure_sample_request_context

                        ensure_sample_request_context(
                            self.store_id,
                            force_reload=False,
                            navigation_timeout=90.0,
                            cancel_check=self._is_low_level_cancellation_requested,
                        )
                    except (JobCancelled, OperationCancelled):
                        raise
                    except Exception as error:
                        page_ready = False
                        self.warning(f"导航回样品申请页失败，类型补齐将跳过：{error}")

                if not page_ready:
                    stats["unprocessed"] += len(need_type)
                else:
                    # 阶段 1a：类型 API（页面不动）。
                    for index, sample_case in enumerate(need_type, start=1):
                        self._check_cancellation()
                        self.progress(
                            completed_units + 1,
                            total,
                            f"补齐类型 {index}/{len(need_type)}（{sample_case.creator_name}）",
                        )
                        outcome = self._normalize_read_outcome(
                            fill_creator_type_from_profile_api(
                                sample_case,
                                store_id=self.store_id,
                                warning=self.warning,
                                cancel_check=self._is_low_level_cancellation_requested,
                            )
                        )
                        self._save_checkpoint(session)
                        completed_units += 1
                        stats["type_filled"] += int(outcome.type_filled)
                        stats["failed"] += int(not outcome.read_ok)
                        stats["type_failures"] += int(not outcome.read_ok)
                        if not outcome.read_ok and is_systemic_detail_error(outcome.error_message):
                            consecutive_systemic_failures += 1
                        else:
                            consecutive_systemic_failures = 0
                        if consecutive_systemic_failures >= SYSTEMIC_DETAIL_FAILURE_LIMIT:
                            systemic_failure = True
                            stats["unprocessed"] += len(need_type) - index
                            self.warning(
                                f"达人详情服务连续 {SYSTEMIC_DETAIL_FAILURE_LIMIT} 次系统级错误，"
                                "已停止本批类型 API 和详情页读取；继续查询飞书语言，"
                                "缺失类型保留人工确认。请检查插件/浏览器环境或等待平台恢复。"
                            )
                            break

                # 阶段 1b：语言先查飞书。
                for index, sample_case in enumerate(need_language, start=1):
                    self._check_cancellation()
                    self.progress(
                        completed_units + 1,
                        total,
                        f"查询飞书语言 {index}/{len(need_language)}（{sample_case.creator_name}）",
                    )
                    if fill_language_from_feishu(
                        sample_case,
                        warning=self.warning,
                        cancel_check=self._is_low_level_cancellation_requested,
                    ):
                        stats["language_filled"] += 1
                    self._save_checkpoint(session)
                    completed_units += 1

                # 阶段 2：仍缺语言的跳详情页抽简介（顺便补类型）。
                still_missing_language = [
                    case for case in need_language if is_missing_language(case)
                ]
                if not page_ready or systemic_failure:
                    stats["unprocessed"] += len(still_missing_language)
                else:
                    total = max(1, completed_units + len(still_missing_language))
                    for index, sample_case in enumerate(still_missing_language, start=1):
                        self._check_cancellation()
                        self.progress(
                            completed_units + 1,
                            total,
                            f"读取简介 {index}/{len(still_missing_language)}（{sample_case.creator_name}）",
                        )
                        outcome = self._normalize_read_outcome(
                            fill_from_creator_detail_page(
                                sample_case,
                                store_id=self.store_id,
                                warning=self.warning,
                                shop_id=shop_id,
                                shop_region=shop_region,
                                cancel_check=self._is_low_level_cancellation_requested,
                            )
                        )
                        self._save_checkpoint(session)
                        completed_units += 1
                        stats["type_filled"] += int(outcome.type_filled)
                        stats["language_filled"] += int(outcome.language_filled)
                        stats["failed"] += int(not outcome.read_ok)
                        stats["detail_failures"] += int(not outcome.read_ok)

                self.progress(
                    max(1, completed_units),
                    max(1, total),
                    "资料补齐阶段完成",
                )
            except (JobCancelled, OperationCancelled) as error:
                session.rollback()
                raise JobCancelled(
                    str(error) or "达人资料补齐已在安全检查点取消",
                    result_summary=self._cancelled_result_summary(stats),
                ) from error
        return stats
