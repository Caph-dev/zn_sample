#!/usr/bin/env python3
"""SOP 第 7–9 步：已发货 → TikTok 物流单号 → 回写飞书 → 可选发私信。

默认只读预演。
  --write-feishu     写订单号 / 快递单号 / 是否已寄样 / 合作状态=待发布
  --send-tracking --execute --yes  发送物流私信（发的是物流单号，不是订单 ID）
北京时间 16:00 前拒绝跑，测试加 --force。
"""
from __future__ import annotations

import logging

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.app_config import load_bitable_settings, load_dotenv  # noqa: E402
from lib.creator_detail import (  # noqa: E402
    extract_creator_detail,
    go_back_to_list,
    open_creator_detail_by_url,
)
from lib.detect_lang import detect_creator_lang  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402
from lib.console import set_verbose  # noqa: E402
from lib.export_util import write_generic_reports  # noqa: E402
from lib.run_summary import format_job_summary  # noqa: E402
from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    DEFAULT_VIEW_ID,
    FeishuBitableError,
    _field_plain,
    build_product_id_to_sku_map,
    build_shipping_fields,
    describe_cooperation_transition,
    feishu_shipping_already_current,
    get_bitable_access_token,
    index_pending_ship_records,
    list_sample_product_options,
    pending_ship_lookup_key,
    resolve_sample_product_for_row,
    search_pending_ship_records,
    update_record_fields,
)
from lib.feishu_hero import FeishuHeroError, load_hero_from_feishu  # noqa: E402
from lib.im_api import send_direct_message  # noqa: E402
from lib.job_cancel import EXIT_CODE_CANCELLED, cancellation_requested  # noqa: E402
from lib.message_templates import looks_like_tracking, tracking_message  # noqa: E402
from lib.order_dom import fetch_tiktok_tracking  # noqa: E402
from lib.order_api import fetch_tiktok_tracking_api  # noqa: E402
from lib.sample_api import scrape_shipped_list_api  # noqa: E402
from lib.shipped_dom import (  # noqa: E402
    ensure_on_sample_page,
    ensure_sample_page_loaded,
    scrape_shipped_list,
)
from lib.zclaw import resolve_store_id  # noqa: E402

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_EXECUTE_LIMIT = 1
PENDING_SHIP_LOOKBACK = timedelta(hours=7 * 24)
try:
    from zoneinfo import ZoneInfo

    BEIJING = ZoneInfo("Asia/Shanghai")
except Exception:
    BEIJING = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

EXPORT_FIELDS = [
    "creator_name",
    "creator_id",
    "apply_id",
    "product_id",
    "resolved_sku",
    "sample_product_option",
    "main_order_id",
    "tracking_source",
    "tracking_raw",
    "tracking_no",
    "lang",
    "feishu_lang",
    "feishu_record_id",
    "feishu_status",
    "cooperation_status_update",
    "send_source",
    "send_status",
    "send_postcheck",
    "message",
    "error",
]


def _before_four_pm_beijing(now: datetime | None = None) -> bool:
    current = now or datetime.now(BEIJING)
    if current.tzinfo is None:
        current = current.replace(tzinfo=BEIJING)
    return current.astimezone(BEIJING).hour < 16


def _detect_lang(
    store_id: str,
    row: dict[str, Any],
    *,
    wait: float,
) -> dict[str, Any]:
    cid = str(row.get("creator_id") or "").strip()
    name = str(row.get("creator_name") or "").strip()
    if not cid:
        return detect_creator_lang("")
    opened = open_creator_detail_by_url(store_id, cid, creator_name=name, wait=wait)
    if not opened.get("ok"):
        return detect_creator_lang("")
    detail = extract_creator_detail(store_id)
    detected = detect_creator_lang(str(detail.get("bio") or ""))
    go_back_to_list(store_id, wait=wait)
    return detected


def _send_tracking_dm(
    store_id: str,
    row: dict[str, Any],
    *,
    lang: str,
    tracking_no: str,
    execute: bool,
    wait: float,
    write_source: str,
) -> dict[str, Any]:
    body = tracking_message(lang, tracking_no)
    return send_direct_message(
        store_id,
        body,
        creator_name=str(row.get("creator_name") or ""),
        creator_id=str(row.get("creator_id") or ""),
        shop_id=str(row.get("_shop_id") or ""),
        execute=execute,
        wait=wait,
        write_source=write_source,
        already_sent_predicate=lambda thread: looks_like_tracking(thread, tracking_no),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SOP 第 7–9 步：同步已发货物流（默认不写不发）")
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--no-default-store", action="store_true")
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--creator-name", default=None, help="只处理该达人 handle")
    parser.add_argument("--creator-id", default=None, help="只处理该达人 creator_id")
    parser.add_argument("--page-wait", type=float, default=2.5)
    parser.add_argument(
        "--data-source",
        choices=("api", "auto", "dom"),
        default="api",
        help="已发货列表读取方式：api=页面同源接口（默认）；auto=失败回退 DOM；dom=旧路径",
    )
    parser.add_argument(
        "--tracking-source",
        choices=("api", "auto", "dom"),
        default="api",
        help="订单物流读取方式：api=logistic_detail/list（默认）；auto=失败回退 DOM；dom=旧路径",
    )
    parser.add_argument("--config", default=None)
    parser.add_argument("--force", action="store_true", help="忽略北京时间 16:00 前门闩")
    parser.add_argument("--overwrite", action="store_true", help="覆盖飞书已有快递单号")
    parser.add_argument("--write-feishu", action="store_true")
    parser.add_argument("--send-tracking", action="store_true", help="回写成功后再发物流私信")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument(
        "--execute-limit",
        type=int,
        default=DEFAULT_EXECUTE_LIMIT,
        help=f"最多发送物流私信 N 条（默认 {DEFAULT_EXECUTE_LIMIT}；0=不限制）",
    )
    parser.add_argument("--execute-delay", type=float, default=1.5)
    parser.add_argument(
        "--write-source",
        choices=("api", "dom"),
        default="api",
        help="物流私信发送方式：api=页面内 IM SDK（默认）；dom=点击发送按钮备用路径",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true", help="终端打印页面 API 明细")
    args = parser.parse_args()
    load_dotenv()
    configure_logging(verbose=bool(args.verbose))
    set_verbose(bool(args.verbose))

    if args.send_tracking and args.execute and not args.yes:
        logger.error("将真实发送物流私信。确认请加 --yes。")
        return 2
    if args.send_tracking and args.execute and not args.write_feishu:
        logger.error("发物流私信须同时 --write-feishu（先有飞书单号再发）。")
        return 2
    if _before_four_pm_beijing() and not args.force:
        now = datetime.now(BEIJING).strftime("%H:%M")
        logger.error(
            f"现在北京时间 {now}，早于 16:00。SOP 第 7 步须四点后跑；测试请加 --force。"
        )
        return 2

    default_sid = None if args.no_default_store else DEFAULT_TEST_STORE_ID
    store_id = resolve_store_id(
        store_id=args.store_id,
        store_name=args.store_name,
        default_store_id=default_sid,
    )

    logger.info("=" * 60)
    logger.info(
        f"第7-9步物流同步 | 列表数据源={args.data_source} | "
        f"物流数据源={args.tracking_source} | "
        f"写飞书={'开' if args.write_feishu else '关'} | "
        f"发私信={'开' if args.send_tracking and args.execute else '关'} | "
        f"force={'是' if args.force else '否'}")
    logger.info("=" * 60)

    hero_data: dict[str, Any] = {}
    try:
        hero_data = load_hero_from_feishu(config_path=args.config)
        logger.info(f"[主推] 已加载，货号映射用")
    except FeishuHeroError as error:
        logger.error(f"[主推] 读取失败，寄样产品只能按货号模糊匹配: {error}")

    # 物流只解析已有寄样产品选项；含历史误批的非主推行。批准/筛查仍只认主推。
    product_id_to_sku = build_product_id_to_sku_map(hero_data, hero_only=False)
    bitable_token = None
    sample_options: list[str] = []
    app_token = DEFAULT_APP_TOKEN
    table_id = DEFAULT_TABLE_ID
    try:
        settings = load_bitable_settings(
            config_path=args.config,
            default_app_token=DEFAULT_APP_TOKEN,
            default_table_id=DEFAULT_TABLE_ID,
            default_view_id=DEFAULT_VIEW_ID,
        )
        app_token = settings.get("app_token") or DEFAULT_APP_TOKEN
        table_id = settings.get("table_id") or DEFAULT_TABLE_ID
        bitable_token = get_bitable_access_token(config_path=args.config)
        sample_options = list_sample_product_options(
            bitable_token, app_token=app_token, table_id=table_id
        )
        logger.info(f"[飞书] 可读 table={table_id} 寄样选项={len(sample_options)}")
    except Exception as error:
        logger.error(f"[飞书] 不可用，无法按待发货筛选: {error}")
        return 2

    now = datetime.now(BEIJING)
    since = now - PENDING_SHIP_LOOKBACK
    try:
        pending_records = search_pending_ship_records(
            bitable_token,
            since=since,
            app_token=app_token,
            table_id=table_id,
        )
    except FeishuBitableError as error:
        logger.error(f"[飞书] 读取待发货失败: {error}")
        return 2
    pending_index = index_pending_ship_records(pending_records)
    logger.info(
        f"[飞书] 近 {int(PENDING_SHIP_LOOKBACK.total_seconds() // 3600)} 小时待发货 "
        f"{len(pending_records)} 行，主键 {len(pending_index)} 个"
    )
    if not pending_index:
        logger.info("飞书近 7 天没有「待发货」记录，不读已发货")
        return 0

    # 指定达人时先扫完全表再过滤，避免 max_rows 把目标截在页外。
    scrape_max_rows = 0 if (args.creator_name or args.creator_id) else args.max_rows
    if args.data_source == "dom":
        rows = scrape_shipped_list(
            store_id,
            max_pages=args.max_pages,
            max_rows=scrape_max_rows,
            page_wait=args.page_wait,
        )
    else:
        try:
            # API 读取只需样品申请页上下文，不依赖 DOM 切到「已发货」tab。
            ensure_sample_page_loaded(store_id, page_wait=args.page_wait)
            rows = scrape_shipped_list_api(
                store_id,
                max_pages=args.max_pages,
                max_rows=scrape_max_rows,
            )
            logger.info(f"[已发货] API 读取完成 rows={len(rows)}")
        except Exception as error:
            if args.data_source == "api":
                logger.error(f"[已发货] API 读取失败，未自动执行 DOM 回退: {error}")
                return 2
            logger.error(f"[已发货] API 读取失败，回退 DOM: {error}")
            rows = scrape_shipped_list(
                store_id,
                max_pages=args.max_pages,
                max_rows=scrape_max_rows,
                page_wait=args.page_wait,
            )
    wanted_name = str(args.creator_name or "").strip().lower()
    wanted_id = str(args.creator_id or "").strip()
    if wanted_name or wanted_id:
        rows = [
            row
            for row in rows
            if (
                (wanted_name and str(row.get("creator_name") or "").strip().lower() == wanted_name)
                or (wanted_id and str(row.get("creator_id") or "").strip() == wanted_id)
            )
        ]
        logger.info(
            f"[过滤] creator_name={args.creator_name or '-'} "
            f"creator_id={args.creator_id or '-'} → {len(rows)} 行")

    matched_rows: list[dict[str, Any]] = []
    for row in rows:
        resolved = resolve_sample_product_for_row(
            row,
            product_id_to_sku=product_id_to_sku,
            sample_product_options=sample_options or list(product_id_to_sku.values()),
        )
        option = str(resolved.get("option") or "") if resolved.get("ok") else ""
        key = pending_ship_lookup_key(str(row.get("creator_name") or ""), option)
        record = pending_index.get(key)
        if not record:
            continue
        row = dict(row)
        row["resolved_sku"] = resolved.get("sku") if resolved.get("ok") else ""
        row["sample_product_option"] = option
        row["_feishu_record"] = record
        matched_rows.append(row)
    logger.info(
        f"[对齐] 已发货 {len(rows)} 行中，命中飞书待发货主键 {len(matched_rows)} 行"
    )
    rows = matched_rows
    if args.max_rows and len(rows) > args.max_rows:
        rows = rows[: args.max_rows]
        logger.info(f"[限量] max_rows={args.max_rows} → {len(rows)} 行")
    if not rows:
        logger.info("已发货里没有与近 7 天待发货主键匹配的行")
        return 0

    unlimited_send = args.execute_limit <= 0
    limit = 0 if unlimited_send else args.execute_limit
    sent = 0
    written = 0
    cancelled = False
    results: list[dict[str, Any]] = []
    total_rows = len(rows)

    for index, row in enumerate(rows, 1):
        # 安全检查点：网页取消只在这里生效——当前行必须整行处理完，
        # 已写入飞书、已发出的私信都保留，绝不从一次写入的中间停下。
        if cancellation_requested():
            cancelled = True
            logger.info(
                f"  [{index}/{total_rows}] 已请求取消：这一行起不再处理，"
                f"已完成 {len(results)} 行；已写入的飞书行和已发私信保留。"
            )
            break
        name = str(row.get("creator_name") or "")
        order_id = str(row.get("main_order_id") or "").strip()
        seq = f"[{index}/{total_rows}]"
        out: dict[str, Any] = {
            "creator_name": name,
            "creator_id": row.get("creator_id") or "",
            "apply_id": row.get("apply_id") or "",
            "product_id": row.get("product_id") or "",
            "main_order_id": order_id,
        }
        if not order_id:
            out["error"] = "列表无 main_order_id"
            results.append(out)
            logger.info(f"  {seq} [跳过] {name}: 无订单号")
            continue

        if args.tracking_source == "dom":
            tracking = fetch_tiktok_tracking(
                store_id,
                order_id,
                shop_id=str(row.get("_shop_id") or ""),
                wait=max(3.5, args.page_wait + 1.0),
            )
        else:
            try:
                tracking = fetch_tiktok_tracking_api(
                    store_id,
                    order_id,
                    shop_id=str(row.get("_shop_id") or ""),
                    fulfill_unit_ids=row.get("fulfill_unit_ids") or [],
                    wait=max(3.5, args.page_wait + 1.0),
                )
            except Exception as error:
                if args.tracking_source == "api":
                    tracking = {
                        "ok": False,
                        "error": f"物流 API 读取失败: {error}",
                    }
                else:
                    logger.error(f"    物流 API 失败，回退 DOM: {error}")
                    tracking = fetch_tiktok_tracking(
                        store_id,
                        order_id,
                        shop_id=str(row.get("_shop_id") or ""),
                        wait=max(3.5, args.page_wait + 1.0),
                    )
        out["tracking_raw"] = tracking.get("tracking_raw") or ""
        out["tracking_no"] = tracking.get("tracking_no") or ""
        out["tracking_source"] = tracking.get("via") or args.tracking_source
        if not tracking.get("ok"):
            out["error"] = tracking.get("error") or "无 TikTok 物流单号"
            logger.info(f"  {seq} [无运单] {name} order={order_id}: {out['error']}")
            results.append(out)
            continue
        logger.info(
            f"  {seq} [物流] {name} order={order_id} "
            f"track={out['tracking_raw'] or out['tracking_no']}")

        option = str(row.get("sample_product_option") or "")
        sku = str(row.get("resolved_sku") or "")
        out["resolved_sku"] = sku
        out["sample_product_option"] = option

        record = row.get("_feishu_record") if isinstance(row.get("_feishu_record"), dict) else None
        if record:
            out["feishu_record_id"] = record.get("record_id")
            existing_lang = _field_plain((record.get("fields") or {}).get("使用语言"))
        else:
            existing_lang = ""
            if bitable_token:
                out["feishu_status"] = "no-record"
                logger.info(f"    {seq} 飞书无匹配行，不新建")

        if existing_lang in {"英语", "西班牙语"}:
            out["feishu_lang"] = existing_lang
            out["lang"] = "es" if existing_lang == "西班牙语" else "en"
        else:
            detected = _detect_lang(store_id, row, wait=args.page_wait)
            out["lang"] = detected.get("lang")
            out["feishu_lang"] = detected.get("feishu_lang")
            out["lang_reason"] = detected.get("reason")

        if args.write_feishu and bitable_token and record:
            plan = build_shipping_fields(
                order_no=order_id,
                tracking_raw=str(out["tracking_raw"]),
                language=out.get("feishu_lang"),
                overwrite=args.overwrite,
                current=record,
            )
            out["cooperation_status_update"] = plan.get("status_transition")
            if plan.get("skip_tracking"):
                out["feishu_status"] = "skip-existing-track"
                logger.info(
                    f"    {seq} 飞书已有不同运单 {plan.get('current_track')}，"
                    "未覆盖且未推进合作状态")
            elif feishu_shipping_already_current(plan, str(out["tracking_raw"])):
                out["feishu_status"] = "unchanged"
                logger.info(
                    f"    {seq} 飞书无需改写："
                    f"{describe_cooperation_transition(str(plan.get('status_transition') or ''))}")
            else:
                try:
                    update_record_fields(
                        bitable_token,
                        str(record.get("record_id")),
                        plan["fields"],
                        app_token=app_token,
                        table_id=table_id,
                    )
                    out["feishu_status"] = "updated"
                    written += 1
                    transition = str(plan.get("status_transition") or "")
                    logger.info(
                        f"    {seq} 飞书已更新：快递单号；"
                        f"{describe_cooperation_transition(transition)}")
                except FeishuBitableError as error:
                    out["feishu_status"] = "update-error"
                    out["error"] = str(error)
                    logger.info(f"    {seq} 飞书更新失败: {error}")
                    results.append(out)
                    continue
        elif not args.write_feishu:
            if not out.get("feishu_status"):
                out["feishu_status"] = "dry-run"

        want_send = bool(args.send_tracking)
        if want_send and out.get("feishu_status") not in {
            "updated",
            "unchanged",
            "skip-existing-track",
        } and args.execute:
            out["send_status"] = "skipped-no-write"
        elif want_send:
            if args.execute and not unlimited_send and sent >= limit:
                out["send_status"] = "skipped-limit"
            else:
                ensure_sample_page_loaded(store_id, page_wait=args.page_wait)
                dm = _send_tracking_dm(
                    store_id,
                    row,
                    lang=str(out.get("lang") or "en"),
                    tracking_no=str(out["tracking_raw"] or out["tracking_no"]),
                    execute=bool(args.execute),
                    wait=args.page_wait,
                    write_source=args.write_source,
                )
                out["message"] = dm.get("message") or tracking_message(
                    str(out.get("lang") or "en"),
                    str(out["tracking_raw"] or out["tracking_no"]),
                )
                out["send_source"] = dm.get("send_source") or args.write_source
                out["send_postcheck"] = dm.get("send_postcheck") or ""
                if dm.get("ok"):
                    out["send_status"] = dm.get("status")
                    if dm.get("status") == "sent":
                        sent += 1
                else:
                    out["send_status"] = dm.get("status") or "send-failed"
                    out["error"] = dm.get("error")
        if args.execute_delay:
            time.sleep(args.execute_delay)
        results.append(out)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = args.out or (ROOT / "exports" / f"sample_shipped_{ts}")
    paths = write_generic_reports(results, prefix, fieldnames=EXPORT_FIELDS)
    logger.info("--- 导出 ---")
    for key, path in paths.items():
        logger.info(f"  {key}: {path}")
    logger.info(f"完成 rows={len(results)} 飞书写入={written} 私信发送={sent}")
    if cancelled:
        logger.info(
            f"已请求取消：在安全检查点停下，已处理 {len(results)}/{total_rows} 行；"
            "已写入的飞书行和已发私信保留，剩余行没有处理。"
        )
    sending = bool(args.send_tracking and args.execute)
    logger.info(
        "\n"
        + format_job_summary(
            title=(
                "「获取物流信息写飞书发单号」已在安全检查点取消"
                if cancelled
                else "「获取物流信息写飞书发单号」完成"
            ),
            stats=[
                f"飞书写入 : {written}",
                f"私信发送 : {sent}",
                f"已查看   : {len(results)}",
                f"发私信   : {'开' if sending else '关'}",
            ],
            csv_path=paths.get("csv"),
            json_path=paths.get("json"),
            xlsx_path=paths.get("xlsx"),
            root=ROOT,
            hint=(
                "已取消：已写入的飞书行和已发私信保留，下次再跑会接着处理剩余行。"
                if cancelled
                else "请看报表里的飞书状态和私信发送结果。"
            ),
        ))
    return EXIT_CODE_CANCELLED if cancelled else 0


if __name__ == "__main__":
    raise SystemExit(main())
