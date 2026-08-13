#!/usr/bin/env python3
"""样品申请筛查 — 对齐 样品申请筛查sop。

默认：只读导出，**不**点同意。

危险路径（须显式）：
  --execute --yes [--execute-limit N]
  顺序：筛查通过 → 点「同意」成功 →（可选）写飞书「达人关系管理(新)」
  测试环境默认 **不写飞书**；须再加 --write-feishu

硬性纪律：
  - 默认禁止同意；仅 --execute --yes 可批
  - 用户须 **已手动** 打开：样品申请 → 待审核
  - 测试环境默认 **1 号店** storeId=27437742526069
  - 主推表仅飞书 wiki；达人关系表 bitable 见 [feishu.bitable]

示例：
  # 只读筛查
  python3 scripts/screen_sample_requests.py --with-detail --require-detail

  # 试批 1 条（默认 limit=1；不写飞书）
  python3 scripts/screen_sample_requests.py \\
    --with-detail --require-detail --execute --yes

  # 批 + 写飞书（生产/明确要求时）
  python3 scripts/screen_sample_requests.py \\
    --with-detail --require-detail --execute --yes --write-feishu --execute-limit 1
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

from lib.approve_dom import click_approve_for_apply_id  # noqa: E402
from lib.creator_detail import fetch_detail_for_row  # noqa: E402
from lib.export_util import write_reports  # noqa: E402
from lib.feishu_bitable import (  # noqa: E402
    DEFAULT_APP_TOKEN,
    DEFAULT_TABLE_ID,
    DEFAULT_VIEW_ID,
    FeishuBitableError,
    build_product_id_to_sku_map,
    create_creator_relation_record,
    find_duplicate_record,
    format_followers_raw,
    format_fulfillment_raw,
    get_bitable_access_token,
    list_sample_product_options,
    resolve_sample_product_for_row,
)
from lib.feishu_hero import (  # noqa: E402
    DEFAULT_APP_ID,
    DEFAULT_HERO_URL,
    DEFAULT_SHEET_TITLE,
    FeishuHeroError,
    load_hero_from_feishu,
)
from lib.filters import Criteria, evaluate_row  # noqa: E402
from lib.sample_dom import scrape_pending_list  # noqa: E402
from lib.zclaw import ensure_store_exec_ready, resolve_store_id  # noqa: E402

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_TEST_STORE_NAME = "跨境1号店（Lingerie Outlet）"
DEFAULT_EXECUTE_LIMIT = 1


def _write_backup(rows: list[dict[str, Any]], prefix: Path) -> dict[str, Path]:
    """批准前备份：json 快照 + 标准导出三件套。"""
    prefix = Path(prefix)
    prefix.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path = prefix.with_name(prefix.name + "_pre_execute.json")
    snapshot_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    paths = write_reports(rows, prefix.with_name(prefix.name + "_pre_execute"))
    paths["snapshot"] = snapshot_path
    return paths


def _run_execute_pipeline(
    *,
    store_id: str,
    candidates: list[dict[str, Any]],
    hero_data: dict[str, Any] | None,
    write_feishu: bool,
    execute_limit: int,
    execute_delay: float,
    config_path: str | None,
    page_wait: float,
) -> list[dict[str, Any]]:
    """对筛查通过行执行：解析货号 →（可选查重）→ 同意 →（可选写飞书）。"""
    product_id_to_sku = build_product_id_to_sku_map(hero_data or {})
    sample_options: list[str] = []
    bitable_token: str | None = None
    app_token = DEFAULT_APP_TOKEN
    table_id = DEFAULT_TABLE_ID

    if write_feishu:
        try:
            from lib.app_config import load_bitable_settings

            bitable_settings = load_bitable_settings(
                config_path=config_path,
                default_app_token=DEFAULT_APP_TOKEN,
                default_table_id=DEFAULT_TABLE_ID,
                default_view_id=DEFAULT_VIEW_ID,
            )
            app_token = bitable_settings.get("app_token") or DEFAULT_APP_TOKEN
            table_id = bitable_settings.get("table_id") or DEFAULT_TABLE_ID
            bitable_token = get_bitable_access_token(config_path=config_path)
            sample_options = list_sample_product_options(
                bitable_token,
                app_token=app_token,
                table_id=table_id,
            )
            print(
                f"[飞书] 写入开启 table={table_id} 寄样产品选项={len(sample_options)} 个"
            )
        except (FeishuBitableError, Exception) as error:
            print(f"[飞书] 初始化失败，将只批准不写表: {error}", file=sys.stderr)
            write_feishu = False
    else:
        # 不写飞书时仍用主推表解析货号，便于跳过「无货号/无选项」
        sample_options = []
        print("[飞书] 未开启 --write-feishu：批准后不写多维表格")

    # 无 write 时也尽量解析 sku（用 hero 映射）；选项匹配在无 options 时跳过「选项缺失」卡点
    if not sample_options and product_id_to_sku:
        # 仅用于 dry 字段；真正写入前仍须 options
        pass

    limit = execute_limit if execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    processed = 0
    feishu_created_ids: list[str] = []

    for row in candidates:
        if processed >= limit:
            row["action"] = "skipped-limit"
            row["approve_status"] = "skipped"
            row["approve_error"] = f"超过 --execute-limit {limit}"
            continue

        apply_id = str(row.get("apply_id") or "").strip()
        creator_name = str(row.get("creator_name") or "").strip()
        row["action"] = "execute-pending"
        row["approve_forbidden"] = False

        if not apply_id:
            row["approve_status"] = "skipped"
            row["approve_error"] = "无 apply_id"
            row["action"] = "skipped-no-apply-id"
            continue
        if not creator_name:
            row["approve_status"] = "skipped"
            row["approve_error"] = "无达人名(creator_name)"
            row["action"] = "skipped-no-creator"
            continue
        if row.get("can_be_approved") is False:
            row["approve_status"] = "skipped"
            row["approve_error"] = "can_be_approved=false"
            row["action"] = "skipped-cannot-approve"
            continue

        product_resolve = resolve_sample_product_for_row(
            row,
            product_id_to_sku=product_id_to_sku,
            sample_product_options=sample_options
            or list(product_id_to_sku.values()),  # 无选项表时先用货号本身做「是否有货号」检查
        )
        # 若有真实 options，再严格匹配一次
        if sample_options:
            product_resolve = resolve_sample_product_for_row(
                row,
                product_id_to_sku=product_id_to_sku,
                sample_product_options=sample_options,
            )
        elif product_resolve.get("ok"):
            # 无 options 且不写飞书：允许只批准；标记 sku
            product_resolve = {
                "ok": True,
                "sku": product_resolve.get("sku"),
                "option": product_resolve.get("sku"),
                "reason": "no-options-skip-option-check",
            }

        row["resolved_sku"] = product_resolve.get("sku")
        row["sample_product_option"] = product_resolve.get("option")

        if not product_resolve.get("ok"):
            # 整单跳过：不批不写
            row["approve_status"] = "skipped"
            row["feishu_status"] = "skipped"
            row["approve_error"] = product_resolve.get("reason")
            row["feishu_error"] = product_resolve.get("reason")
            row["action"] = "skipped-product-unresolved"
            print(
                f"  [跳过] {creator_name} apply={apply_id} "
                f"原因={product_resolve.get('reason')}"
            )
            continue

        # 去重（仅写飞书时强制；不写时也可选查重——默认不查以免无 token）
        if write_feishu and bitable_token:
            try:
                dup = find_duplicate_record(
                    bitable_token,
                    creator_handle=creator_name,
                    sample_product=str(product_resolve.get("option")),
                    app_token=app_token,
                    table_id=table_id,
                )
            except FeishuBitableError as error:
                row["approve_status"] = "skipped"
                row["feishu_status"] = "error"
                row["feishu_error"] = f"查重失败: {error}"
                row["action"] = "skipped-dedupe-error"
                print(f"  [跳过] 查重失败 {creator_name}: {error}")
                continue
            if dup:
                row["approve_status"] = "skipped"
                row["feishu_status"] = "duplicate"
                row["feishu_record_id"] = dup.get("record_id")
                row["approve_error"] = "飞书已存在 红人ID+寄样产品"
                row["action"] = "skipped-duplicate"
                print(
                    f"  [跳过-去重] {creator_name} × {product_resolve.get('option')} "
                    f"record={dup.get('record_id')}"
                )
                continue

        print(
            f"  [批准] ({processed + 1}/{limit}) {creator_name} "
            f"apply={apply_id} sku={product_resolve.get('sku')} "
            f"option={product_resolve.get('option')}"
        )
        try:
            # doctor 绿 ≠ execute_script 可用；批准前先探活（含 network 重试）
            ensure_store_exec_ready(store_id, label="批准前")
            approve_result = click_approve_for_apply_id(
                store_id,
                apply_id,
                page_wait=max(1.0, page_wait),
            )
        except Exception as error:
            row["approve_status"] = "error"
            row["approve_error"] = str(error)
            row["action"] = "approve-error"
            print(f"    批准异常: {error}")
            continue

        if not approve_result.get("ok"):
            row["approve_status"] = "failed"
            row["approve_error"] = approve_result.get("error") or str(approve_result)
            row["action"] = "approve-failed"
            row["approve_detail"] = approve_result
            print(f"    批准失败: {row['approve_error']}")
            continue

        row["approve_status"] = "approved"
        row["action"] = "approved"
        row["approve_detail"] = {
            "note": approve_result.get("note"),
            "steps_summary": [
                list(step.keys())[0] for step in (approve_result.get("steps") or [])
            ],
        }
        print(f"    批准成功 note={approve_result.get('note')}")
        processed += 1

        if not write_feishu or not bitable_token:
            row["feishu_status"] = "skipped-no-write"
            if execute_delay:
                time.sleep(execute_delay)
            continue

        try:
            created = create_creator_relation_record(
                bitable_token,
                creator_handle=creator_name,
                followers_raw=format_followers_raw(row),
                fulfillment_raw=format_fulfillment_raw(row),
                sample_product=str(product_resolve.get("option")),
                app_token=app_token,
                table_id=table_id,
            )
            record_id = created.get("record_id")
            row["feishu_status"] = "created"
            row["feishu_record_id"] = record_id
            row["action"] = "approved+feishu"
            if record_id:
                feishu_created_ids.append(str(record_id))
            print(f"    飞书写入成功 record_id={record_id}")
        except FeishuBitableError as error:
            row["feishu_status"] = "error"
            row["feishu_error"] = str(error)
            row["action"] = "approved+feishu-failed"
            print(
                f"    飞书写入失败（批准已成功，需人工补录）: {error}",
                file=sys.stderr,
            )

        if execute_delay:
            time.sleep(execute_delay)

    if feishu_created_ids:
        print(
            f"[回退提示] 本轮新建飞书 record_id: {', '.join(feishu_created_ids)}；"
            f"如需撤销可调用 delete_record / 在表中删除"
        )
    return candidates


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "样品申请筛查（默认只读导出；"
            "--execute --yes 可批准；--write-feishu 才写达人关系管理(新)）"
        ),
    )
    ap.add_argument("--store-id", default=None, help=f"默认测试 1 号店 {DEFAULT_TEST_STORE_ID}")
    ap.add_argument("--store-name", default=None)
    ap.add_argument("--no-default-store", action="store_true")
    ap.add_argument("--max-pages", type=int, default=50)
    ap.add_argument("--max-rows", type=int, default=0, help="最多处理 N 条（0=不限；测试建议小）")
    ap.add_argument("--page-wait", type=float, default=2.0)
    ap.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认仓库根 config.toml；也可用 ZN_SAMPLE_CONFIG）",
    )
    ap.add_argument(
        "--hero-feishu-url",
        default=None,
        help=f"飞书主推表 wiki/sheets 链接（默认 {DEFAULT_HERO_URL}；也可用 FEISHU_HERO_URL / config.toml）",
    )
    ap.add_argument(
        "--hero-sheet",
        default=None,
        help=f"飞书电子表格子表名（默认 {DEFAULT_SHEET_TITLE}；也可用 FEISHU_HERO_SHEET / config.toml）",
    )
    ap.add_argument(
        "--feishu-app-id",
        default=None,
        help=f"飞书应用 App ID（默认 {DEFAULT_APP_ID}；也可用 FEISHU_APP_ID / config.toml）",
    )
    ap.add_argument(
        "--feishu-app-secret",
        default=None,
        help="飞书 App Secret（优先 CLI；否则环境变量 / config.toml；勿提交 git）",
    )
    ap.add_argument(
        "--skip-hero-check",
        action="store_true",
        help="跳过主推条件（放松 SOP；正式筛查禁止）",
    )
    ap.add_argument(
        "--with-detail",
        action="store_true",
        help="对筛查通过(列表层)的达人再进详情页拉视频/直播 GPM（只读）",
    )
    ap.add_argument(
        "--detail-all",
        action="store_true",
        help="对所有扫到的行拉详情（更慢；仍只读）",
    )
    ap.add_argument("--detail-delay", type=float, default=1.0, help="详情间间隔秒")
    ap.add_argument(
        "--require-detail",
        action="store_true",
        help="无详情指标则判定不通过",
    )
    ap.add_argument("--eligible-only", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="危险：对筛查通过行点击「同意」（须同时 --yes；默认仍只导出）",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="与 --execute 联用，确认真实批准（缺一不可）",
    )
    ap.add_argument(
        "--execute-limit",
        type=int,
        default=DEFAULT_EXECUTE_LIMIT,
        help=f"最多批准 N 条（默认 {DEFAULT_EXECUTE_LIMIT}；0 视为 {DEFAULT_EXECUTE_LIMIT}）",
    )
    ap.add_argument(
        "--execute-delay",
        type=float,
        default=1.5,
        help="每条批准间隔秒（默认 1.5）",
    )
    ap.add_argument(
        "--write-feishu",
        action="store_true",
        help="批准成功后写入飞书「达人关系管理(新)」（测试环境请勿默认开启）",
    )
    ap.add_argument(
        "--no-pre-backup",
        action="store_true",
        help="execute 时跳过批准前本地备份（不推荐）",
    )
    args = ap.parse_args()

    if args.execute and not args.yes:
        print(
            "将真实点击「同意」。确认请加 --yes，或去掉 --execute 做只读筛查。",
            file=sys.stderr,
        )
        return 2
    if args.write_feishu and not args.execute:
        print("--write-feishu 仅在 --execute --yes 时有效", file=sys.stderr)
        return 2

    mode = "EXECUTE" if args.execute else "DRY-RUN/只读导出"
    print("=" * 60)
    print(f"样品申请筛查 | 模式={mode}")
    if args.execute:
        limit_show = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
        print(
            f"  批准: 开 | limit={limit_show} | "
            f"写飞书={'开' if args.write_feishu else '关（测试默认）'}"
        )
        print("  顺序: 同意成功 → 再写飞书；去重命中/货号无法映射 → 整单跳过")
    else:
        print("  默认只读导出 | **未**启用同意")
    print("主推表来源: 飞书云文档（不再使用本地 xlsx）")
    print("前提：你已手动打开「样品申请 → 待审核」并停在该页")
    print("=" * 60)

    default_sid = None if args.no_default_store else DEFAULT_TEST_STORE_ID
    try:
        store_id = resolve_store_id(
            store_id=args.store_id,
            store_name=args.store_name,
            default_store_id=default_sid,
        )
    except Exception as e:
        print(f"解析店铺失败: {e}", file=sys.stderr)
        return 2

    if store_id == DEFAULT_TEST_STORE_ID:
        print(f"[测试环境] 1 号店 {DEFAULT_TEST_STORE_NAME} ({store_id})")

    # 主推表：仅飞书
    hero_keys: set[str] = set()
    hero_data: dict[str, Any] | None = None
    if args.skip_hero_check:
        print("[主推] --skip-hero-check：跳过飞书主推条件（非正式）")
    else:
        try:
            hero_data = load_hero_from_feishu(
                url=args.hero_feishu_url,
                sheet_title=args.hero_sheet,
                app_id=args.feishu_app_id,
                app_secret=args.feishu_app_secret,
                config_path=args.config,
            )
        except FeishuHeroError as error:
            print(f"飞书主推表读取失败: {error}", file=sys.stderr)
            print(
                "请确认：1) config.toml [feishu].app_secret 或 FEISHU_APP_SECRET  "
                "2) 应用仍有表格可阅读权限  "
                "3) 链接/子表名正确",
                file=sys.stderr,
            )
            return 2
        except Exception as error:
            print(f"飞书主推表读取异常: {error}", file=sys.stderr)
            return 2

        hero_keys = set(hero_data.get("hero_keys") or set())
        hero_skus = hero_data.get("hero_skus") or []
        hero_product_ids = hero_data.get("hero_product_ids") or []
        preview = ", ".join(str(sku) for sku in hero_skus[:20])
        if len(hero_skus) > 20:
            preview = f"{preview}, …"
        cfg_note = hero_data.get("config_path") or "（无 config.toml，仅用环境变量/默认）"
        print(f"[配置] {cfg_note}")
        print(
            f"[主推飞书] title={hero_data.get('doc_title') or hero_data.get('sheet_title')} "
            f"sheet={hero_data.get('sheet_title')} "
            f"token={hero_data.get('spreadsheet_token')} "
            f"→ 货号 {len(hero_data.get('all_skus') or [])} 个，"
            f"主推货号 {len(hero_skus)} 个，主推商品ID {len(hero_product_ids)} 个，"
            f"匹配键 {len(hero_keys)} 个"
            + (f"; 主推货号: {preview}" if preview else "")
        )
        if not hero_keys:
            print(
                "警告: 飞书表已读到，但「是否主推=是」为空；条件2 将全部判非主推",
                file=sys.stderr,
            )

    criteria = Criteria(require_hero_sku=not args.skip_hero_check)

    t0 = time.time()
    try:
        raw_rows = scrape_pending_list(
            store_id,
            max_pages=args.max_pages,
            max_rows=args.max_rows,
            page_wait=args.page_wait,
            ensure_tab=True,
        )
    except Exception as e:
        print(f"扫表失败: {e}", file=sys.stderr)
        return 1

    if not raw_rows:
        print("未读到待审核行", file=sys.stderr)
        return 1

    list_href = (raw_rows[0].get("_list_href") or raw_rows[0].get("href") or "") if raw_rows else ""

    # 先做列表层初筛，并保留阶段结果供后续导出。初筛淘汰者不会因为未拉详情，
    # 被追加“缺少达人详情”等与其实际淘汰原因无关的终筛失败。
    pre = [
        evaluate_row(
            r,
            criteria=criteria,
            hero_keys=hero_keys,
            skip_hero_check=args.skip_hero_check,
            require_detail=False,
        )
        for r in raw_rows
    ]
    for row in pre:
        initial_failure_reason = "; ".join(row.get("fail_reasons") or [])
        row["initial_screen_eligible"] = bool(row.get("eligible"))
        row["initial_screen_failure_reason"] = initial_failure_reason
        if row["initial_screen_eligible"]:
            row["screening_stage"] = "初筛通过，待详情复筛"
        else:
            row["screening_stage"] = "初筛淘汰"
            row["reason"] = f"初筛淘汰：{initial_failure_reason}"

    # 详情：只读
    need_detail = args.with_detail or args.detail_all or args.require_detail
    detail_targets: list[dict] = []
    if need_detail:
        if args.detail_all:
            detail_targets = list(pre)
        else:
            # 列表层已过的再拉详情（禁止 --detail-limit 截断；要限量用 --max-rows）
            detail_targets = [x for x in pre if x.get("eligible")]
        print(f"--- 拉详情(只读) 目标 {len(detail_targets)} 人 ---")

        by_key = {
            (x.get("apply_id") or x.get("creator_name")): x for x in pre
        }
        for i, target in enumerate(detail_targets, 1):
            key = target.get("apply_id") or target.get("creator_name")
            print(
                f"  [{i}/{len(detail_targets)}] 详情 {target.get('creator_name')} "
                f"cid={target.get('creator_id')} …"
            )
            # 合并 raw 字段给 fetch
            src = next(
                (
                    r
                    for r in raw_rows
                    if str(r.get("apply_id")) == str(target.get("apply_id"))
                    or r.get("creator_name") == target.get("creator_name")
                ),
                target,
            )
            try:
                res = fetch_detail_for_row(
                    store_id,
                    src,
                    list_href=list_href,
                    prefer_url=True,
                    wait=max(3.5, args.page_wait + 1.5),
                )
            except Exception as e:
                print(f"    失败: {e}")
                target["detail_error"] = str(e)
                continue
            if not res.get("ok"):
                print(f"    失败: {res.get('error')}")
                target["detail_error"] = res.get("error")
            else:
                d = res["detail"]
                print(
                    f"    VideoGPM={d.get('video_gpm')} LiveGPM={d.get('live_gpm')} "
                    f"avgViews={d.get('avg_video_views')} eng={d.get('video_engagement')} "
                    f"type={d.get('creator_type')}"
                )
                # 写回 pre 行
                row = by_key.get(key) or target
                for k in (
                    "video_gpm",
                    "live_gpm",
                    "avg_video_views",
                    "video_engagement",
                    "avg_live_views",
                    "live_engagement",
                    "est_post_rate",
                    "overall_gpm",
                    "revenue_per_buyer",
                    "creator_type",
                    "video_gpm_n",
                    "live_gpm_n",
                    "avg_video_views_n",
                    "avg_live_views_n",
                    "video_engagement_n",
                    "live_engagement_n",
                    "overall_gpm_n",
                    "aov_detail_n",
                    "est_post_rate_n",
                    "has_cn_video_card",
                    "has_en_video_card",
                    "extract_via",
                    "text_head",
                ):
                    if k in d:
                        row[k] = d[k]
                row["detail_checked"] = True
                if not d.get("video_gpm") and not d.get("live_gpm"):
                    print(
                        f"    警告: 视频/直播GPM仍为空 "
                        f"cn_card={d.get('has_cn_video_card')} "
                        f"en_card={d.get('has_en_video_card')} "
                        f"overall={d.get('overall_gpm')} "
                        f"via={d.get('extract_via')}"
                    )
            if args.detail_delay:
                time.sleep(args.detail_delay)

        # 带详情重判。只有初筛通过者才要求详情指标；初筛淘汰者保留原始原因，
        # 避免导出出现并未获取详情所产生的额外 failure。
        reevaluated_rows: list[dict[str, Any]] = []
        for row in pre:
            if not row.get("initial_screen_eligible"):
                reevaluated_rows.append(row)
                continue

            reevaluated_row = evaluate_row(
                row,
                criteria=criteria,
                hero_keys=hero_keys,
                skip_hero_check=args.skip_hero_check,
                require_detail=args.require_detail,
            )
            reevaluated_row["initial_screen_eligible"] = True
            reevaluated_row["initial_screen_failure_reason"] = ""
            if reevaluated_row.get("eligible"):
                reevaluated_row["screening_stage"] = "终筛通过"
            else:
                reevaluated_row["screening_stage"] = "详情复筛淘汰"
                final_failure_reason = "; ".join(
                    reevaluated_row.get("fail_reasons") or []
                )
                reevaluated_row["reason"] = f"详情复筛淘汰：{final_failure_reason}"
            reevaluated_rows.append(reevaluated_row)
        pre = reevaluated_rows

    passed = [x for x in pre if x.get("eligible")]
    print(
        f"--- 判定: 合计={len(pre)} 通过={len(passed)} "
        f"用时 {time.time() - t0:.1f}s ---"
    )
    for x in passed[:30]:
        print(
            f"  [通过] {x.get('creator_name')}\t"
            f"粉={x.get('followers_n')}\tGMV={x.get('gmv_n')}\t"
            f"件={x.get('units_n')}\t履约={x.get('fulfillment_n')}\t"
            f"VGPM={x.get('video_gpm_n')}\tLGPM={x.get('live_gpm_n')}\t"
            f"type={x.get('creator_type')}\thero={x.get('is_hero')}"
        )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_prefix = args.out or (ROOT / "exports" / f"sample_screen_{ts}")

    if args.execute:
        # 批准前备份（可回看筛查结果；平台侧「同意」不可自动撤销）
        if not args.no_pre_backup:
            backup_paths = _write_backup(pre, out_prefix)
            print("--- 批准前备份 ---")
            for key, path in backup_paths.items():
                print(f"  {key}: {path}")

        # 合并 can_be_approved 等列表字段（evaluate 可能未保留）
        raw_by_apply = {
            str(item.get("apply_id") or ""): item for item in raw_rows if item.get("apply_id")
        }
        for row in pre:
            apply_key = str(row.get("apply_id") or "")
            source = raw_by_apply.get(apply_key) or {}
            if "can_be_approved" not in row and "can_be_approved" in source:
                row["can_be_approved"] = source.get("can_be_approved")
            if not row.get("follower_num") and source.get("follower_num") is not None:
                row["follower_num"] = source.get("follower_num")
            if not row.get("fulfillment_rate") and source.get("fulfillment_rate") is not None:
                row["fulfillment_rate"] = source.get("fulfillment_rate")

        candidates = [row for row in pre if row.get("eligible")]
        print(f"--- EXECUTE 目标通过行 {len(candidates)}（limit={args.execute_limit or DEFAULT_EXECUTE_LIMIT}）---")
        _run_execute_pipeline(
            store_id=store_id,
            candidates=candidates,
            hero_data=hero_data,
            write_feishu=bool(args.write_feishu),
            execute_limit=args.execute_limit,
            execute_delay=args.execute_delay,
            config_path=args.config,
            page_wait=args.page_wait,
        )
        # 非通过行保持 export-only
        for row in pre:
            if not row.get("eligible") and not row.get("action"):
                row["action"] = "export-only"
                row["approve_forbidden"] = True
            elif row.get("eligible") and not row.get("action"):
                row["action"] = "export-only"
                row["approve_forbidden"] = True
    else:
        for row in pre:
            row["action"] = "export-only"
            row["approve_forbidden"] = True

    export_rows = (
        [row for row in pre if row.get("eligible")] if args.eligible_only else pre
    )
    paths = write_reports(export_rows, out_prefix)
    print("--- 导出 ---")
    for key, path in paths.items():
        print(f"  {key}: {path}")
    if args.execute:
        approved_count = sum(1 for row in pre if row.get("approve_status") == "approved")
        print(
            f"完成。模式=EXECUTE 批准成功={approved_count} "
            f"写飞书={'开' if args.write_feishu else '关'}。"
        )
        print(
            "回退：平台「同意」无法脚本撤销；飞书新建行见导出列 feishu_record_id / 控制台提示。"
        )
    else:
        print("完成。未执行任何同意/批准/拒绝操作。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
