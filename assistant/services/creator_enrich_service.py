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

from sqlalchemy import select

from assistant.database.models import SampleCase, Store


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
    if not filled:
        warning(
            f"达人类型补齐失败（{sample_case.creator_name}）："
            f"视频 GPM={video_gpm!r} 直播 GPM={live_gpm!r}，两侧都没有可用数据"
        )
    return filled


def fill_creator_type_from_profile_api(
    sample_case: SampleCase,
    *,
    store_id: str | None,
    warning,
) -> bool:
    """页面同源 profile API 拉类型；需要页面停在样品申请页。"""
    if not store_id:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：没有唯一运行中的店铺")
        return False
    creator_id = str(sample_case.creator_id or "").strip()
    if not creator_id:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：缺少 creator_id")
        return False
    try:
        from lib.creator_api import fetch_creator_detail_api

        result = fetch_creator_detail_api(
            store_id,
            {"creator_id": creator_id},
        )
    except Exception as error:
        warning(f"达人类型补齐失败（{sample_case.creator_name}）：{error}")
        return False
    if not result.get("ok"):
        warning(
            f"达人类型补齐失败（{sample_case.creator_name}）："
            f"{result.get('error') or 'profile-api-failed'}"
        )
        return False
    detail = result.get("detail") or {}
    return _mark_type_from_metrics(
        sample_case,
        video_gpm=detail.get("video_gpm_n"),
        live_gpm=detail.get("live_gpm_n"),
        warning=warning,
    )


def fill_language_from_feishu(
    sample_case: SampleCase,
    *,
    warning,
) -> bool:
    """飞书「使用语言」只读查询；无值返回 False。"""
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
        language = _field_plain((record or {}).get("fields", {}).get("使用语言"))
        if language in {"英语", "西班牙语"}:
            sample_case.feishu_lang = language
            return True
        return False
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
) -> bool:
    """异步导航到达人详情页抽简介（顺便补类型），再异步回样品申请页。"""
    if not store_id:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：没有唯一运行中的店铺")
        return False
    creator_id = str(sample_case.creator_id or "").strip()
    if not creator_id:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：缺少 creator_id")
        return False
    try:
        from lib.creator_detail import extract_creator_detail
        from lib.sample_navigation import (
            is_sample_request_href,
            navigate_to_url,
            sample_request_url,
        )
        from urllib.parse import quote

        detail_url = (
            "https://affiliate.tiktokshopglobalselling.com/connection/creator/detail"
            f"?cid={quote(creator_id, safe='')}"
            "&enter_from=sample_request&pair_source=sample_request_click"
            f"&cname={quote(sample_case.creator_name or '', safe='')}"
            f"&shop_region={quote(str(shop_region or 'US'), safe='')}"
        )
        navigate_to_url(
            store_id,
            detail_url,
            href_matches=lambda href: "/creator/detail" in str(href or ""),
            timeout=90.0,
            poll_interval=0.5,
        )
        detail = extract_creator_detail(store_id)
        filled = False
        bio = str(detail.get("bio") or "").strip()
        if bio:
            sample_case.bio = bio
            filled = True
        if is_missing_creator_type(sample_case):
            if _mark_type_from_metrics(
                sample_case,
                video_gpm=detail.get("video_gpm_n"),
                live_gpm=detail.get("live_gpm_n"),
                warning=warning,
            ):
                filled = True
        return filled
    except Exception as error:
        warning(f"达人详情读取失败（{sample_case.creator_name}）：{error}")
        return False
    finally:
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
                timeout=90.0,
                poll_interval=0.5,
            )
        except Exception as error:
            warning(f"回样品申请页失败：{error}")


def fill_language_from_feishu_then_bio(
    sample_case: SampleCase,
    *,
    store_id: str | None,
    warning,
    shop_id: str = "",
    shop_region: str = "US",
) -> bool:
    """语言优先级：飞书「使用语言」→ 达人详情简介（缓存 bio 列）。"""
    if fill_language_from_feishu(sample_case, warning=warning):
        return True
    return fill_from_creator_detail_page(
        sample_case,
        store_id=store_id,
        warning=warning,
        shop_id=shop_id,
        shop_region=shop_region,
    )


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

    def enrich(self) -> dict:
        stats = {
            "missing": 0,
            "type_filled": 0,
            "language_filled": 0,
            "failed": 0,
        }
        with self.session_factory() as session:
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
            total = len(need_type) + len(need_language)
            if stats["missing"] and not self.store_id:
                self.warning("没有唯一运行中的店铺，达人类型和详情简介无法自动补齐")

            # 类型 API 需要页面停在样品申请页（上一轮任务可能把窗口留在详情页）。
            # 先只读导航回样品申请页；失败则跳过类型阶段，语言阶段仍可继续。
            if self.store_id and need_type:
                try:
                    from lib.sample_navigation import ensure_sample_request_context

                    ensure_sample_request_context(
                        self.store_id,
                        force_reload=False,
                        navigation_timeout=90.0,
                    )
                except Exception as error:
                    self.warning(f"导航回样品申请页失败，类型补齐将跳过：{error}")

            # 阶段 1a：类型批量（页面不动）。
            for index, sample_case in enumerate(need_type, start=1):
                self.cancel_check()
                self.progress(
                    index,
                    max(1, total),
                    f"补齐类型 {index}/{len(need_type)}（{sample_case.creator_name}）",
                )
                if fill_creator_type_from_profile_api(
                    sample_case,
                    store_id=self.store_id,
                    warning=self.warning,
                ):
                    stats["type_filled"] += 1
                else:
                    stats["failed"] += 1

            # 阶段 1b：语言先查飞书。
            offset = len(need_type)
            for index, sample_case in enumerate(need_language, start=1):
                self.cancel_check()
                self.progress(
                    offset + index,
                    max(1, total),
                    f"查询飞书语言 {index}/{len(need_language)}（{sample_case.creator_name}）",
                )
                if fill_language_from_feishu(sample_case, warning=self.warning):
                    stats["language_filled"] += 1

            # 阶段 2：仍缺语言的跳详情页抽简介（顺便补类型）。
            still_missing_language = [
                case for case in need_language if is_missing_language(case)
            ]
            for index, sample_case in enumerate(still_missing_language, start=1):
                self.cancel_check()
                self.progress(
                    offset + len(need_language) + index,
                    max(1, total),
                    f"读取简介 {index}/{len(still_missing_language)}（{sample_case.creator_name}）",
                )
                if fill_from_creator_detail_page(
                    sample_case,
                    store_id=self.store_id,
                    warning=self.warning,
                    shop_id=shop_id,
                    shop_region=shop_region,
                ):
                    stats["language_filled"] += 1
                else:
                    stats["failed"] += 1

            session.commit()
        return stats
