#!/usr/bin/env python3
"""SOP 第 6 步：打开达人私信，发送介绍话术。

默认只读预演（抽简介、判语言、生成话术，不发送）。
真正发送须 --execute --yes。默认 limit=1。
语言只认英语 / 西班牙语，来源：达人详情页简介。
"""
from __future__ import annotations

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
from lib.detect_lang import detect_creator_lang  # noqa: E402
from lib.export_util import write_generic_reports  # noqa: E402
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
    inspect_im,
    open_im_from_detail,
    open_im_inbox,
    search_and_open_conversation,
)
from lib.message_templates import intro_message, looks_like_intro  # noqa: E402
from lib.zclaw import resolve_store_id  # noqa: E402

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_EXECUTE_LIMIT = 1

EXPORT_FIELDS = [
    "creator_name",
    "creator_id",
    "apply_id",
    "bio",
    "lang",
    "feishu_lang",
    "lang_reason",
    "message",
    "im_status",
    "send_status",
    "feishu_record_id",
    "feishu_status",
    "error",
]


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


def _open_conversation(store_id: str, row: dict[str, Any], *, wait: float) -> dict[str, Any]:
    from_detail = open_im_from_detail(store_id, wait=wait)
    if from_detail.get("ok"):
        clicked = search_and_open_conversation(
            store_id, str(row.get("creator_name") or ""), wait=wait
        )
        if clicked.get("ok"):
            return {"ok": True, "via": "detail+search", "detail": from_detail, "click": clicked}
        # 详情气泡有时已直接打开该会话
        probe = inspect_im(store_id)
        name = str(row.get("creator_name") or "")
        if name and name.lower() in str(probe.get("text") or "").lower():
            return {"ok": True, "via": "detail-direct", "detail": from_detail}
        return {"ok": True, "via": "detail-im", "detail": from_detail, "click": clicked}
    inbox = open_im_inbox(store_id)
    if not inbox.get("ok"):
        return {"ok": False, "error": "无法打开私信页", "detail": from_detail, "inbox": inbox}
    clicked = search_and_open_conversation(
        store_id, str(row.get("creator_name") or ""), wait=wait
    )
    if not clicked.get("ok"):
        return {"ok": False, "error": "私信中找不到会话", "inbox": inbox, "click": clicked}
    return {"ok": True, "via": "inbox-search", "inbox": inbox, "click": clicked}


def main() -> int:
    parser = argparse.ArgumentParser(description="SOP 第 6 步：发送样品介绍私信（默认不发送）")
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--no-default-store", action="store_true")
    parser.add_argument("--from-export", type=Path, default=None, help="筛查/批准导出 json")
    parser.add_argument("--creator-id", default=None)
    parser.add_argument("--creator-name", default=None)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--page-wait", type=float, default=3.0)
    parser.add_argument("--config", default=None)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--execute-limit", type=int, default=DEFAULT_EXECUTE_LIMIT)
    parser.add_argument("--execute-delay", type=float, default=1.5)
    parser.add_argument(
        "--write-feishu",
        action="store_true",
        help="把识别到的「使用语言」写回达人关系管理(新)",
    )
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    if args.execute and not args.yes:
        print("将真实发送私信。确认请加 --yes，或去掉 --execute 做只读预演。", file=sys.stderr)
        return 2

    targets: list[dict[str, Any]] = []
    if args.from_export:
        targets = _load_export_targets(args.from_export)
        print(f"[输入] 导出 {args.from_export} → {len(targets)} 条候选")
    if args.creator_id or args.creator_name:
        targets = [
            {
                "creator_id": args.creator_id or "",
                "creator_name": args.creator_name or "",
            }
        ]
    if not targets:
        print("请提供 --from-export 或 --creator-id/--creator-name", file=sys.stderr)
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
            print(f"[飞书] 初始化失败，将不写表: {error}", file=sys.stderr)
            bitable_token = None

    limit = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    sent = 0
    results: list[dict[str, Any]] = []
    print("=" * 60)
    print(
        f"第6步介绍私信 | 模式={'EXECUTE' if args.execute else 'DRY-RUN'} "
        f"| 目标 {len(targets)} | limit={limit}"
    )
    print("=" * 60)

    for row in targets:
        name = str(row.get("creator_name") or "").strip()
        out: dict[str, Any] = {
            "creator_name": name,
            "creator_id": row.get("creator_id") or "",
            "apply_id": row.get("apply_id") or "",
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
            print(f"  [跳过] {name}: {out['error']}")
            results.append(out)
            continue
        out["bio"] = detected.get("bio") or ""
        out["lang"] = detected.get("lang")
        out["feishu_lang"] = detected.get("feishu_lang")
        out["lang_reason"] = detected.get("reason")
        body = intro_message(str(detected.get("lang")), name)
        out["message"] = body
        print(
            f"  [语言] {name} {detected.get('feishu_lang')} "
            f"({detected.get('reason')}) bio={(detected.get('bio') or '')[:60]!r}"
        )

        opened = _open_conversation(store_id, row, wait=args.page_wait)
        if not opened.get("ok"):
            out["im_status"] = "open-failed"
            out["error"] = opened.get("error")
            out["send_status"] = "skipped"
            print(f"    私信打开失败: {out['error']}")
            results.append(out)
            continue
        out["im_status"] = opened.get("via")
        probe = inspect_im(store_id)
        if looks_like_intro(str(probe.get("text") or "")):
            out["send_status"] = "already-sent"
            print(f"    已有介绍话术，跳过")
            results.append(out)
            continue

        if not args.execute:
            out["send_status"] = "dry-run"
            print("    DRY-RUN 不发送")
        else:
            sent_ret = fill_or_send_message(store_id, body, execute=True)
            if sent_ret.get("ok") and sent_ret.get("sent"):
                out["send_status"] = "sent"
                sent += 1
                print("    已发送")
            else:
                out["send_status"] = "send-failed"
                out["error"] = sent_ret.get("error") or str(sent_ret)
                print(f"    发送失败: {out['error']}")

        if bitable_token and detected.get("feishu_lang"):
            try:
                record_id = str(out.get("feishu_record_id") or "").strip()
                if not record_id:
                    found = search_relation_records(bitable_token, creator_handle=name)
                    picked = pick_shipping_target(found)
                    record_id = str((picked or {}).get("record_id") or "")
                if record_id:
                    update_record_fields(
                        bitable_token,
                        record_id,
                        {"使用语言": detected["feishu_lang"]},
                    )
                    out["feishu_record_id"] = record_id
                    out["feishu_status"] = "lang-updated"
                else:
                    out["feishu_status"] = "no-record"
            except FeishuBitableError as error:
                out["feishu_status"] = "error"
                out["error"] = str(error)

        if args.execute_delay:
            time.sleep(args.execute_delay)
        results.append(out)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = args.out or (ROOT / "exports" / f"sample_intro_{ts}")
    paths = write_generic_reports(results, prefix, fieldnames=EXPORT_FIELDS)
    print("--- 导出 ---")
    for key, path in paths.items():
        print(f"  {key}: {path}")
    print(f"完成 sent={sent} / {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
