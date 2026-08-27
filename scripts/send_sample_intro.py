#!/usr/bin/env python3
"""SOP 第 6 步：打开达人私信，发送介绍话术。

默认只读预演（抽简介、判语言、生成话术，不发送）。
真正发送须 --execute --yes。默认 limit=1。
语言只认英语 / 西班牙语，来源：达人详情页简介。
真实发送默认调用页面内 IM SDK API；需要时可显式使用 DOM 备用路径。
"""
from __future__ import annotations

import logging

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.creator_detail import (  # noqa: E402
    extract_creator_detail,
    open_creator_detail_by_url,
)
from lib.app_config import load_dotenv  # noqa: E402
from lib.detect_lang import detect_creator_lang  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402
from lib.console import set_verbose  # noqa: E402
from lib.export_util import resolve_from_export_arg, write_generic_reports  # noqa: E402
from lib.run_summary import format_job_summary  # noqa: E402
from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    FeishuBitableError,
    get_bitable_access_token,
    pick_shipping_target,
    search_relation_records,
    update_record_fields,
)
from lib.im_dom import (  # noqa: E402
    fill_or_send_message,
    inspect_current_thread,
    open_target_conversation,
    thread_has_named_intro,
)
from lib.im_api import send_message_via_sdk  # noqa: E402
from lib.message_templates import intro_message  # noqa: E402
from lib.zclaw import resolve_store_id  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_EXECUTE_LIMIT = 1

EXPORT_FIELDS = [
    "creator_name",
    "creator_id",
    "apply_id",
    "product_id",
    "sample_product_option",
    "resolved_sku",
    "bio",
    "lang",
    "feishu_lang",
    "lang_reason",
    "message",
    "send_source",
    "im_status",
    "send_status",
    "send_postcheck",
    "feishu_record_id",
    "feishu_status",
    "error",
]


def _target_product_key(row: dict[str, Any]) -> str:
    """Return the most stable product identity available in an export row."""
    for field_name in ("sample_product_option", "resolved_sku", "product_id"):
        value = str(row.get(field_name) or "").strip()
        if value:
            return value
    return ""


def _intro_target_key(row: dict[str, Any]) -> tuple[str, str]:
    """Use creator ID plus product as the introduction-message dedupe key."""
    return (
        str(row.get("creator_id") or "").strip(),
        _target_product_key(row),
    )


def _load_sent_intro_audit() -> tuple[set[tuple[str, str]], set[str]]:
    """Load successful intro sends from prior local intro exports.

    Older intro exports did not include product fields, so their apply IDs are
    also retained as an exact same-application fallback.
    """
    sent_target_keys: set[tuple[str, str]] = set()
    sent_apply_ids: set[str] = set()
    for export_path in (ROOT / "exports").glob("sample_intro_*.json"):
        try:
            data = json.loads(export_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows = data if isinstance(data, list) else data.get("rows") or data.get("items") or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict) or row.get("send_status") != "sent":
                continue
            apply_id = str(row.get("apply_id") or "").strip()
            if apply_id:
                sent_apply_ids.add(apply_id)
            target_key = _intro_target_key(row)
            if target_key[0] and target_key[1]:
                sent_target_keys.add(target_key)
    return sent_target_keys, sent_apply_ids


def _load_export_targets(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("rows") or data.get("items") or []
    if not isinstance(data, list):
        raise RuntimeError(f"导出不是数组: {path}")
    picked: list[dict[str, Any]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        if row.get("approve_status") == "approved" or row.get("feishu_status") in {
            "created",
            "approved+feishu",
        }:
            picked.append(row)
            continue
        if row.get("eligible") and row.get("creator_id"):
            picked.append(row)
    return picked


def _filter_targets(
    rows: list[dict[str, Any]],
    *,
    creator_id: str = "",
    creator_name: str = "",
) -> list[dict[str, Any]]:
    """从导出候选里挑指定人。不要用只有名字的空壳覆盖整行（会丢掉 creator_id）。"""
    wanted_id = str(creator_id or "").strip()
    wanted_name = str(creator_name or "").strip().lower()
    if not wanted_id and not wanted_name:
        return list(rows)
    picked: list[dict[str, Any]] = []
    for row in rows:
        if wanted_id and str(row.get("creator_id") or "").strip() == wanted_id:
            picked.append(row)
            continue
        if wanted_name and str(row.get("creator_name") or "").strip().lower() == wanted_name:
            picked.append(row)
    return picked


def _detect_from_detail(store_id: str, row: dict[str, Any], *, wait: float) -> dict[str, Any]:
    cid = str(row.get("creator_id") or "").strip()
    name = str(row.get("creator_name") or "").strip()
    if not cid:
        return {"ok": False, "error": "无 creator_id，无法打开详情页判语言"}
    opened = open_creator_detail_by_url(store_id, cid, creator_name=name, wait=wait)
    if not opened.get("ok"):
        return {"ok": False, "error": "打开详情失败", "opened": opened}
    detail = extract_creator_detail(store_id)
    bio = str(detail.get("bio") or "")
    detected = detect_creator_lang(bio)
    detected["ok"] = True
    detected["detail_href"] = detail.get("href") or ""
    return detected


def _write_feishu_lang(
    out: dict[str, Any],
    *,
    bitable_token: str | None,
    feishu_lang: str,
    creator_name: str,
) -> None:
    if not bitable_token or not feishu_lang:
        return
    try:
        record_id = str(out.get("feishu_record_id") or "").strip()
        if not record_id:
            found = search_relation_records(bitable_token, creator_handle=creator_name)
            picked = pick_shipping_target(found)
            record_id = str((picked or {}).get("record_id") or "")
        if record_id:
            update_record_fields(
                bitable_token,
                record_id,
                {"使用语言": feishu_lang},
            )
            out["feishu_record_id"] = record_id
            out["feishu_status"] = "lang-updated"
        else:
            out["feishu_status"] = "no-record"
    except FeishuBitableError as error:
        out["feishu_status"] = "error"
        out["error"] = str(error)


def _open_conversation(store_id: str, row: dict[str, Any], *, wait: float) -> dict[str, Any]:
    return open_target_conversation(
        store_id,
        str(row.get("creator_name") or ""),
        creator_id=str(row.get("creator_id") or ""),
        wait=wait,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="SOP 第 6 步：发送样品介绍私信（默认不发送）")
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--no-default-store", action="store_true")
    parser.add_argument(
        "--from-export",
        nargs="?",
        const="latest",
        default=None,
        help="筛查/批准导出 json；不写路径则用 exports/ 最新一份",
    )
    parser.add_argument("--creator-id", default=None, help="只处理该达人；配合 --from-export 时从导出里筛，不覆盖行")
    parser.add_argument("--creator-name", default=None, help="只处理该达人 handle；配合 --from-export 时从导出里筛")
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--page-wait", type=float, default=3.0)
    parser.add_argument("--config", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--execute-limit", type=int, default=DEFAULT_EXECUTE_LIMIT)
    parser.add_argument("--execute-delay", type=float, default=1.5)
    parser.add_argument(
        "--write-source",
        choices=("api", "dom"),
        default="api",
        help="私信发送方式：api=页面内 IM SDK（默认）；dom=点击发送按钮备用路径",
    )
    parser.add_argument(
        "--write-feishu",
        action="store_true",
        help="把识别到的「使用语言」写回达人关系管理(新)",
    )
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--verbose", action="store_true", help="终端打印页面 API 明细")
    args = parser.parse_args()
    load_dotenv()
    configure_logging(verbose=bool(args.verbose))
    set_verbose(bool(args.verbose))
    try:
        args.from_export = resolve_from_export_arg(args.from_export)
    except FileNotFoundError as error:
        logger.error(str(error))
        return 2

    if args.execute and not args.yes:
        logger.error("将真实发送私信。确认请加 --yes，或去掉 --execute 做只读预演。")
        return 2

    targets: list[dict[str, Any]] = []
    if args.from_export:
        targets = _load_export_targets(args.from_export)
        logger.info(f"[输入] 导出 {args.from_export} → {len(targets)} 条候选")
        if args.creator_id or args.creator_name:
            targets = _filter_targets(
                targets,
                creator_id=args.creator_id or "",
                creator_name=args.creator_name or "",
            )
            logger.info(
                f"[过滤] creator_name={args.creator_name or '-'} "
                f"creator_id={args.creator_id or '-'} → {len(targets)} 条")
            if not targets:
                logger.error(
                    "导出里没有这个达人。核对 --creator-name / --creator-id，"
                    "或改用带 creator_id 的筛查/批准 json。"
                )
                return 2
    elif args.creator_id or args.creator_name:
        targets = [
            {
                "creator_id": args.creator_id or "",
                "creator_name": args.creator_name or "",
            }
        ]
    if not targets:
        logger.error("请提供 --from-export 或 --creator-id/--creator-name")
        return 2
    if args.max_rows and len(targets) > args.max_rows:
        targets = targets[: args.max_rows]

    default_sid = None if args.no_default_store else DEFAULT_TEST_STORE_ID
    store_id = resolve_store_id(
        store_id=args.store_id,
        store_name=args.store_name,
        default_store_id=default_sid,
    )

    bitable_token = None
    if args.write_feishu:
        try:
            bitable_token = get_bitable_access_token(config_path=args.config)
        except FeishuBitableError as error:
            logger.error(f"[飞书] 初始化失败，将不写表: {error}")
            bitable_token = None

    limit = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    sent = 0
    results: list[dict[str, Any]] = []
    sent_intro_keys, sent_intro_apply_ids = _load_sent_intro_audit()
    current_run_intro_keys: set[tuple[str, str]] = set()
    current_run_apply_ids: set[str] = set()
    logger.info("=" * 60)
    logger.info(
        f"第6步介绍私信 | 模式={'EXECUTE' if args.execute else 'DRY-RUN'} "
        f"| 目标 {len(targets)} | limit={limit}")
    logger.info("=" * 60)

    for row in targets:
        name = str(row.get("creator_name") or "").strip()
        out: dict[str, Any] = {
            "creator_name": name,
            "creator_id": row.get("creator_id") or "",
            "apply_id": row.get("apply_id") or "",
            "product_id": row.get("product_id") or "",
            "sample_product_option": row.get("sample_product_option") or "",
            "resolved_sku": row.get("resolved_sku") or "",
            "feishu_record_id": row.get("feishu_record_id") or "",
        }
        if args.execute and sent >= limit:
            out["send_status"] = "skipped-limit"
            results.append(out)
            continue

        detected = _detect_from_detail(store_id, row, wait=args.page_wait)
        if not detected.get("ok"):
            out["error"] = detected.get("error")
            out["send_status"] = "skipped"
            logger.info(f"  [跳过] {name}: {out['error']}")
            results.append(out)
            continue
        out["bio"] = detected.get("bio") or ""
        out["lang"] = detected.get("lang")
        out["feishu_lang"] = detected.get("feishu_lang")
        out["lang_reason"] = detected.get("reason")
        body = intro_message(str(detected.get("lang")), name)
        out["message"] = body
        logger.info(
            f"  [语言] {name} {detected.get('feishu_lang')} "
            f"({detected.get('reason')}) bio={(detected.get('bio') or '')[:60]!r}")

        intro_key = _intro_target_key(row)
        apply_id = str(row.get("apply_id") or "").strip()
        already_sent = (
            (intro_key[0] and intro_key[1] and intro_key in sent_intro_keys)
            or (apply_id and apply_id in sent_intro_apply_ids)
            or (intro_key[0] and intro_key[1] and intro_key in current_run_intro_keys)
            or (apply_id and apply_id in current_run_apply_ids)
        )
        if args.execute and already_sent:
            out["send_status"] = "already-sent"
            logger.info(f"    已发送同一红人+产品介绍，跳过")
            _write_feishu_lang(
                out,
                bitable_token=bitable_token,
                feishu_lang=str(detected.get("feishu_lang") or ""),
                creator_name=name,
            )
            results.append(out)
            continue

        opened = _open_conversation(store_id, row, wait=args.page_wait)
        if not opened.get("ok"):
            out["im_status"] = "open-failed"
            out["error"] = opened.get("error")
            out["send_status"] = "skipped"
            logger.info(f"    私信打开失败: {out['error']}")
            results.append(out)
            continue
        out["im_status"] = opened.get("via")
        opened_click = opened.get("click")
        if isinstance(opened_click, dict) and isinstance(opened_click.get("click"), dict):
            click_detail = opened_click.get("click") or {}
        else:
            click_detail = opened_click if isinstance(opened_click, dict) else {}

        if not args.execute:
            out["send_status"] = "dry-run"
            logger.info("    DRY-RUN 不发送")
        else:
            out["send_source"] = args.write_source
            if args.write_source == "api":
                sent_ret = send_message_via_sdk(
                    store_id,
                    body,
                    expected_creator_name=name,
                    expected_creator_id=str(row.get("creator_id") or ""),
                    conversation_id=str(click_detail.get("conversation_id") or ""),
                )
                if sent_ret.get("ok"):
                    time.sleep(max(1.5, args.page_wait))
                    post_probe = inspect_current_thread(
                        store_id, name, wait=args.page_wait
                    )
                    out["send_postcheck"] = (
                        "confirmed"
                        if thread_has_named_intro(post_probe, name)
                        else "unknown"
                    )
                    # API 已启动即视为发送成功；postcheck 只用于诊断，避免
                    # 因页面未及时刷新而误报未发送并诱发重复私信。
                    out["send_status"] = "sent"
                    sent += 1
                    if intro_key[0] and intro_key[1]:
                        current_run_intro_keys.add(intro_key)
                    if apply_id:
                        current_run_apply_ids.add(apply_id)
                    if out["send_postcheck"] == "confirmed":
                        logger.info("    已通过 IM SDK API 发送并确认")
                    else:
                        out["error"] = "API 已启动但发送后未确认，禁止自动重试"
                        logger.info(f"    已通过 IM SDK API 发送（{out['error']}）")
                else:
                    out["send_status"] = "send-failed"
                    out["error"] = sent_ret.get("reason") or str(sent_ret)
                    logger.info(f"    API 发送失败: {out['error']}")
            else:
                sent_ret = fill_or_send_message(store_id, body, execute=True)
                if sent_ret.get("ok") and sent_ret.get("sent"):
                    time.sleep(1.0)
                    post_probe = inspect_current_thread(
                        store_id, name, wait=args.page_wait
                    )
                    out["send_postcheck"] = (
                        "confirmed"
                        if thread_has_named_intro(post_probe, name)
                        else "unknown"
                    )
                    # 点击发送并获得成功响应后即计入发送；postcheck 只作诊断。
                    out["send_status"] = "sent"
                    sent += 1
                    if intro_key[0] and intro_key[1]:
                        current_run_intro_keys.add(intro_key)
                    if apply_id:
                        current_run_apply_ids.add(apply_id)
                    if out["send_postcheck"] == "confirmed":
                        logger.info("    已通过 DOM 发送并确认")
                    else:
                        out["error"] = "DOM 点击后未确认消息，禁止自动重试"
                        logger.info(f"    已通过 DOM 发送（{out['error']}）")
                else:
                    out["send_status"] = "send-failed"
                    out["error"] = sent_ret.get("error") or str(sent_ret)
                    logger.info(f"    发送失败: {out['error']}")

        _write_feishu_lang(
            out,
            bitable_token=bitable_token,
            feishu_lang=str(detected.get("feishu_lang") or ""),
            creator_name=name,
        )

        if args.execute_delay:
            time.sleep(args.execute_delay)
        results.append(out)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = args.out or (ROOT / "exports" / f"sample_intro_{ts}")
    paths = write_generic_reports(results, prefix, fieldnames=EXPORT_FIELDS)
    logger.info("--- 导出 ---")
    for key, path in paths.items():
        logger.info(f"  {key}: {path}")
    logger.info(f"完成 sent={sent} / {len(results)}")
    logger.info(
        "\n"
        + format_job_summary(
            title="「发介绍私信」完成" if args.execute else "「介绍私信预演」完成",
            stats=[
                f"已发送 : {sent}",
                f"已查看 : {len(results)}",
                f"模式   : {'发送' if args.execute else '预演（不会发送）'}",
            ],
            csv_path=paths.get("csv"),
            json_path=paths.get("json"),
            xlsx_path=paths.get("xlsx"),
            root=ROOT,
            hint="物流请北京时间 16:00 后再双击「3-获取物流信息写飞书发单号」。",
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
