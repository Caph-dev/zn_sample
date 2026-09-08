#!/usr/bin/env python3
"""导出 csv + xlsx（openpyxl 可选；无则写简易 xlsx）。"""
from __future__ import annotations

import csv
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPORT_DIR = REPO_ROOT / "exports"
RECONCILIATION_MANIFEST_SUFFIX = "_reconcile.json"
RECONCILIATION_STAGE_SCREEN = "screen"
RECONCILIATION_STAGE_APPROVE = "approve"
RECONCILIATION_STAGE_CONFIRM = "confirm"


class ReconciliationManifestError(RuntimeError):
    """A reconciliation export cannot be safely resumed."""


def latest_screen_export(exports_dir: Path | None = None) -> Path:
    """exports/ 里最新一份正式筛查 json，排除批准前备份。"""
    directory = Path(exports_dir or DEFAULT_EXPORT_DIR)
    candidates = [
        path
        for path in directory.glob("sample_screen_*.json")
        if (
            path.is_file()
            and "_pre_execute" not in path.name
            and not path.name.endswith(RECONCILIATION_MANIFEST_SUFFIX)
        )
    ]
    if not candidates:
        raise FileNotFoundError(f"{directory} 下没有 sample_screen_*.json")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def resolve_from_export_arg(value: str | Path | None) -> Path | None:
    """--from-export 不写路径或写 latest 时，用最新筛查导出。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "latest":
        return latest_screen_export()
    return Path(text)


def reconciliation_manifest_path(export_json_path: Path) -> Path:
    """Return the sidecar manifest path for a sample-screen JSON export."""
    path = Path(export_json_path)
    return path.with_name(path.stem + RECONCILIATION_MANIFEST_SUFFIX)


def sha256_file(path: Path) -> str:
    """Return a content hash without loading potentially large reports at once."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_reconciliation_manifest(manifest_path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReconciliationManifestError(
            f"无法读取 reconcile manifest: {manifest_path}"
        ) from error
    if not isinstance(payload, dict):
        raise ReconciliationManifestError(
            f"reconcile manifest 不是对象: {manifest_path}"
        )
    return payload


def _manifest_output_json_path(
    manifest: dict[str, Any],
    *,
    manifest_path: Path,
) -> Path:
    output = manifest.get("output")
    if not isinstance(output, dict):
        raise ReconciliationManifestError(
            f"reconcile manifest 缺少 output: {manifest_path}"
        )
    raw_path = str(output.get("json_path") or "").strip()
    if not raw_path:
        raise ReconciliationManifestError(
            f"reconcile manifest 缺少 output.json_path: {manifest_path}"
        )
    path = Path(raw_path)
    return path if path.is_absolute() else manifest_path.parent / path


def load_reconciliation_manifest_for_export(
    export_json_path: Path,
) -> dict[str, Any] | None:
    """Load and integrity-check a sidecar manifest when one is present.

    Historical JSON exports have no manifest and remain explicitly usable.  A
    present but invalid manifest is never ignored, because it could otherwise
    make a recovery run use a modified or mismatched report.
    """
    export_path = Path(export_json_path)
    manifest_path = reconciliation_manifest_path(export_path)
    if not manifest_path.is_file():
        return None
    manifest = _read_reconciliation_manifest(manifest_path)
    output_path = _manifest_output_json_path(manifest, manifest_path=manifest_path)
    if output_path.resolve() != export_path.resolve():
        raise ReconciliationManifestError(
            "reconcile manifest 的 output.json_path 与输入导出不一致: "
            f"{manifest_path}"
        )
    if not export_path.is_file():
        raise ReconciliationManifestError(f"导出文件不存在: {export_path}")
    output = manifest.get("output") or {}
    expected_hash = str(output.get("sha256") or "").strip()
    if not expected_hash:
        raise ReconciliationManifestError(
            f"reconcile manifest 缺少 output.sha256: {manifest_path}"
        )
    if sha256_file(export_path) != expected_hash:
        raise ReconciliationManifestError(
            "导出文件已变更，与 reconcile manifest 不一致；"
            "请使用未修改的输出，或重新从明确的历史导出开始。"
        )
    return manifest


def validate_reconciliation_export_input(
    export_json_path: Path,
    *,
    allowed_stages: set[str] | frozenset[str],
    store_id: str,
) -> dict[str, Any] | None:
    """Validate stage and store binding before a resume-capable operation."""
    manifest = load_reconciliation_manifest_for_export(export_json_path)
    if manifest is None:
        return None
    stage = str(manifest.get("stage") or "").strip()
    if stage not in allowed_stages:
        expected = "/".join(sorted(allowed_stages))
        raise ReconciliationManifestError(
            f"该导出阶段是 {stage or '未知'}，当前操作只接受 {expected}。"
        )
    manifest_store_id = str(manifest.get("store_id") or "").strip()
    if manifest_store_id and manifest_store_id != str(store_id or "").strip():
        raise ReconciliationManifestError(
            "导出绑定的店铺与当前运行店铺不一致；禁止跨店恢复。"
        )
    return manifest


def latest_reconciliation_export(
    *,
    allowed_stages: set[str] | frozenset[str],
    exports_dir: Path | None = None,
) -> Path:
    """Find the newest verified manifest-backed export for specific stages."""
    directory = Path(exports_dir or DEFAULT_EXPORT_DIR)
    candidates: list[tuple[str, float, Path]] = []
    invalid_candidates: list[tuple[str, float, ReconciliationManifestError]] = []
    for manifest_path in directory.glob(f"*{RECONCILIATION_MANIFEST_SUFFIX}"):
        created_at = ""
        try:
            manifest = _read_reconciliation_manifest(manifest_path)
            created_at = str(manifest.get("created_at") or "")
            stage = str(manifest.get("stage") or "").strip()
            if stage not in allowed_stages:
                continue
            output_path = _manifest_output_json_path(manifest, manifest_path=manifest_path)
            # Reuse the public validator so a broken manifest never quietly
            # falls back to an older result.
            load_reconciliation_manifest_for_export(output_path)
        except ReconciliationManifestError as error:
            invalid_candidates.append(
                (created_at, manifest_path.stat().st_mtime, error)
            )
            continue
        candidates.append((created_at, manifest_path.stat().st_mtime, output_path))
    if candidates:
        newest_valid = max(candidates, key=lambda candidate: (candidate[0], candidate[1]))
        if invalid_candidates:
            newest_invalid = max(
                invalid_candidates,
                key=lambda candidate: (candidate[0], candidate[1]),
            )
            if newest_invalid[:2] >= newest_valid[:2]:
                raise newest_invalid[2]
        return newest_valid[2]
    if invalid_candidates:
        raise max(
            invalid_candidates,
            key=lambda candidate: (candidate[0], candidate[1]),
        )[2]
    expected = "/".join(sorted(allowed_stages))
    raise FileNotFoundError(
        f"{directory} 下没有阶段为 {expected} 的已验证 reconcile 导出"
    )


def resolve_reconciliation_export_arg(
    value: str | Path | None,
    *,
    allowed_stages: set[str] | frozenset[str],
    exports_dir: Path | None = None,
) -> Path | None:
    """Resolve ``latest`` by verified stage; explicit legacy paths still work."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() == "latest":
        return latest_reconciliation_export(
            allowed_stages=allowed_stages,
            exports_dir=exports_dir,
        )
    return Path(text)


EXPORT_FIELDS = [
    "creator_name",
    "nick_name",
    "product_id",
    "sku_id",
    "sku_desc",
    "product_title",
    "fulfillment_n",
    "units_n",
    "gmv_n",
    "aov_n",
    "gpm_proxy_n",
    "video_gpm_n",
    "live_gpm_n",
    "is_video_creator",
    "is_live_creator",
    "followers_n",
    "female_pct",
    "categories_n",
    "views_n",
    "apply_id",
    "is_hero",
    "eligible",
    "screening_stage",
    "initial_screen_failure_reason",
    "reason",
    "action",
    "approve_status",
    "approve_error",
    "approved_at",
    "approve_confirmation",
    "platform_confirmation_status",
    "feishu_status",
    "feishu_relation_status",
    "feishu_record_id",
    "feishu_error",
    "feishu_duplicate_record_ids",
    "sample_product_option",
    "resolved_sku",
    "order_no",
    "feishu_order_status",
    "order_backfill_status",
    "feishu_order_error",
    "reconcile_next_action",
    "sales_eligible",
    "sales_reason",
    "content_review_status",
    "content_review_reason",
    "content_review_handle",
    "content_review_window_start",
    "content_review_window_end",
    "content_review_related_count",
    "content_review_complete",
    "content_review_evidence_path",
    "content_review_version",
    "content_review_model",
    "content_review_video_ids",
    "content_review_reviewed_at",
]

# SOP 名单列（中文表头）
CN_HEADERS = [
    "达人名",
    "昵称",
    "产品货号/product_id",
    "sku_id",
    "sku描述",
    "商品标题",
    "履约率/预计发布率",
    "成交件数",
    "GMV",
    "客单价",
    "千次曝光代理",
    "视频GPM",
    "直播GPM",
    "视频达人",
    "直播达人",
    "粉丝数",
    "女性粉丝%",
    "类目",
    "视频播放量",
    "apply_id",
    "是否主推",
    "是否通过筛查",
    "筛选阶段",
    "初筛淘汰原因",
    "原因",
    "动作",
    "批准状态",
    "批准错误",
    "批准时间",
    "平台确认（旧）",
    "平台确认状态",
    "飞书状态",
    "飞书关系状态",
    "飞书record_id",
    "飞书错误",
    "飞书重复record_id",
    "寄样产品选项",
    "解析货号",
    "待发货订单号",
    "订单号回填状态",
    "订单号规范状态",
    "订单号回填错误",
    "对账下一步",
    "销售筛查通过",
    "销售筛查原因",
    "内容审核状态",
    "内容审核原因",
    "内容审核达人ID",
    "内容审核窗口起点",
    "内容审核窗口终点",
    "内容审核相关视频数",
    "内容审核采集完整",
    "内容审核证据路径",
    "内容审核版本",
    "内容审核模型",
    "内容审核视频ID",
    "内容审核时间",
]


def _yes_flag(v: Any) -> str:
    """导出「是」标记：仅是则为「是」，否则空。"""
    if v is True:
        return "是"
    if isinstance(v, str):
        s = v.strip()
        if s in {"是", "Y", "y", "yes", "Yes", "TRUE", "true", "1"}:
            return "是"
    return ""


def derive_creator_type_flags(row: dict) -> tuple[str, str]:
    """从 is_* / creator_type / GPM 推导「视频达人」「直播达人」是否=是。"""
    video_flag = _yes_flag(row.get("is_video_creator"))
    live_flag = _yes_flag(row.get("is_live_creator"))
    if video_flag or live_flag:
        return video_flag, live_flag

    creator_type = str(row.get("creator_type") or "").strip()
    if creator_type in {"视频+直播", "视频达人+直播达人"}:
        return "是", "是"
    if "视频" in creator_type and "直播" in creator_type:
        return "是", "是"
    if creator_type == "视频达人" or (creator_type.startswith("视频") and "直播" not in creator_type):
        return "是", ""
    if creator_type == "直播达人" or (creator_type.startswith("直播") and "视频" not in creator_type):
        return "", "是"

    # 无 type 文案时：有实质视频/直播 GPM 则对应标「是」
    video_gpm = row.get("video_gpm_n")
    live_gpm = row.get("live_gpm_n")
    try:
        video_n = float(video_gpm) if video_gpm is not None and video_gpm != "" else None
    except (TypeError, ValueError):
        video_n = None
    try:
        live_n = float(live_gpm) if live_gpm is not None and live_gpm != "" else None
    except (TypeError, ValueError):
        live_n = None
    if video_n is not None and video_n > 0:
        video_flag = "是"
    if live_n is not None and live_n > 0:
        live_flag = "是"
    return video_flag, live_flag


def _reconciliation_value(
    row: dict,
    *,
    checkpoint_field: str,
    legacy_field: str,
) -> str:
    return str(row.get(checkpoint_field) or row.get(legacy_field) or "").strip()


def derive_reconciliation_next_action(row: dict) -> str:
    """Turn reconciliation checkpoints into a safe, operator-readable next step."""
    platform_status = _reconciliation_value(
        row,
        checkpoint_field="platform_confirmation_status",
        legacy_field="approve_confirmation",
    )
    relation_status = _reconciliation_value(
        row,
        checkpoint_field="feishu_relation_status",
        legacy_field="feishu_status",
    )
    order_status = _reconciliation_value(
        row,
        checkpoint_field="order_backfill_status",
        legacy_field="feishu_order_status",
    )
    record_id = str(row.get("feishu_record_id") or "").strip()

    if not platform_status and not relation_status and not order_status and not record_id:
        return ""

    if platform_status in {"deferred", "waiting-platform-lag"}:
        return "等待平台待发货刷新后再核对"
    if platform_status == "still-pending":
        return "人工核对仍在待审核；不要自动重批"
    if platform_status in {"identity-mismatch", "unknown", "error"}:
        return "人工核对平台状态；不要自动重批"
    if platform_status == "skipped-limit":
        return "本轮核对额度已用尽；下轮只读核对"
    if platform_status != "confirmed":
        return "等待或人工核对平台确认"

    if relation_status == "ambiguous-match":
        return "人工核对飞书重复关系行；不要自动写入"
    if relation_status in {"error", "invalid-match", "write-uncertain"}:
        return "修复飞书记录问题后重跑补写"
    if relation_status in {"blocked-not-hero", "blocked-product-unresolved"}:
        return "产品映射被拦截；人工核对后再处理"
    if relation_status == "skipped-limit":
        return "本轮飞书补写额度已用尽；下轮补写"
    if not record_id:
        return "平台已确认；可用 --confirm-export --write-feishu 补建飞书行"

    if order_status in {"skipped-existing-different", "write-uncertain"}:
        return "人工核对飞书订单号；默认不覆盖"
    if order_status in {
        "error",
        "skipped-identity-mismatch",
        "skipped-not-found-in-pending",
        "skipped-not-ready-to-ship",
    }:
        return "人工核对待发货与飞书后再回填"
    if order_status == "skipped-invalid-order-no":
        return "待发货暂无有效订单号；留待物流步骤"
    if order_status == "skipped-limit":
        return "本轮订单回填额度已用尽；下轮回填"
    if order_status in {"written", "unchanged"}:
        return "平台、飞书和订单号已完成核对"
    return "平台已确认；待回填飞书订单号"


def prepare_export_row(row: dict) -> dict:
    """补齐达人标记与对账状态，供 CSV/XLSX/JSON 使用。"""
    out = dict(row)
    video_flag, live_flag = derive_creator_type_flags(out)
    out["is_video_creator"] = video_flag
    out["is_live_creator"] = live_flag
    out["platform_confirmation_status"] = _reconciliation_value(
        out,
        checkpoint_field="platform_confirmation_status",
        legacy_field="approve_confirmation",
    )
    out["feishu_relation_status"] = _reconciliation_value(
        out,
        checkpoint_field="feishu_relation_status",
        legacy_field="feishu_status",
    )
    out["order_backfill_status"] = _reconciliation_value(
        out,
        checkpoint_field="order_backfill_status",
        legacy_field="feishu_order_status",
    )
    out["reconcile_next_action"] = derive_reconciliation_next_action(out)
    return out


def _sanitize_text(value: str) -> str:
    """修复页面数据中的 UTF-16 代理项，确保文本可编码为 UTF-8。"""
    if not any("\ud800" <= character <= "\udfff" for character in value):
        return value

    # 浏览器数据偶尔会留下 UTF-16 代理项。合法代理对在往返过程中恢复为
    # 对应 Unicode 字符，孤立代理项则替换为 U+FFFD，避免整份报告导出失败。
    return value.encode("utf-16", errors="surrogatepass").decode(
        "utf-16", errors="replace"
    )


def _sanitize_export_value(value: Any) -> Any:
    """递归清洗 JSON 全量数据以及表格中可能出现的嵌套值。"""
    if isinstance(value, str):
        return _sanitize_text(value)
    if isinstance(value, dict):
        return {
            _sanitize_export_value(key): _sanitize_export_value(nested_value)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_export_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_export_value(item) for item in value)
    return value


def _cell(v: Any) -> Any:
    if isinstance(v, list):
        return _sanitize_text(", ".join(str(x) for x in v))
    if isinstance(v, bool):
        return "Y" if v else "N"
    if v is None:
        return ""
    if isinstance(v, str):
        return _sanitize_text(v)
    return v


def _export_row_values(row: dict) -> list[Any]:
    prepared = prepare_export_row(row)
    return [
        json.dumps(prepared.get(field_name) or [], ensure_ascii=False)
        if field_name == "content_review_video_ids"
        else _cell(prepared.get(field_name))
        for field_name in EXPORT_FIELDS
    ]


def write_csv(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(CN_HEADERS)
        for r in rows:
            w.writerow(_export_row_values(r))
    return path


def write_json(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    prepared = [_sanitize_export_value(prepare_export_row(r)) for r in rows]
    path.write_text(json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def write_xlsx(rows: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "sample_screen"
        ws.append(CN_HEADERS)
        for r in rows:
            ws.append(_export_row_values(r))
        wb.save(path)
        return path
    except ImportError:
        return _write_xlsx_minimal(rows, path)


def _write_xlsx_minimal(rows: list[dict], path: Path) -> Path:
    """无 openpyxl 时写最小 xlsx（sheet1）。"""
    import zipfile
    from xml.sax.saxutils import escape

    def col_name(n: int) -> str:
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def cell_xml(ref: str, val: Any) -> str:
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            return f'<c r="{ref}"><v>{val}</v></c>'
        t = escape(str(val if val is not None else ""))
        return f'<c r="{ref}" t="inlineStr"><is><t>{t}</t></is></c>'

    sheet_rows = [CN_HEADERS] + [_export_row_values(r) for r in rows]
    row_xml = []
    for i, row in enumerate(sheet_rows, 1):
        cells = []
        for j, val in enumerate(row, 1):
            cells.append(cell_xml(f"{col_name(j)}{i}", val))
        row_xml.append(f'<row r="{i}">{"".join(cells)}</row>')
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData></worksheet>'
    )
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    wb = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="sample_screen" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    wb_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", wb_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return path


def write_reports(rows: Iterable[dict], out_prefix: Path) -> dict[str, Path]:
    rows = list(rows)
    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    return {
        "csv": write_csv(rows, out_prefix.with_suffix(".csv")),
        "json": write_json(rows, out_prefix.with_suffix(".json")),
    }


def _reconciliation_row_summary(row: dict[str, Any], *, store_id: str) -> dict[str, str]:
    """Keep only stable, operator-relevant facts in the recovery sidecar."""
    apply_id = str(row.get("apply_id") or "").strip()
    creator_id = str(row.get("creator_id") or "").strip()
    product_id = str(row.get("product_id") or "").strip()
    return {
        "row_key": "|".join((str(store_id or "").strip(), apply_id, creator_id, product_id)),
        "apply_id": apply_id,
        "creator_id": creator_id,
        "creator_name": str(row.get("creator_name") or "").strip(),
        "product_id": product_id,
        "resolved_sku": str(row.get("resolved_sku") or "").strip(),
        "sample_product_option": str(row.get("sample_product_option") or "").strip(),
        "approve_status": str(row.get("approve_status") or "").strip(),
        "platform_confirmation_status": str(
            row.get("platform_confirmation_status")
            or row.get("approve_confirmation")
            or ""
        ).strip(),
        "feishu_relation_status": str(
            row.get("feishu_relation_status") or row.get("feishu_status") or ""
        ).strip(),
        "feishu_record_id": str(row.get("feishu_record_id") or "").strip(),
        "order_backfill_status": str(
            row.get("order_backfill_status") or row.get("feishu_order_status") or ""
        ).strip(),
        "order_no": str(row.get("order_no") or "").strip(),
    }


def write_reconciliation_manifest(
    rows: Iterable[dict[str, Any]],
    *,
    out_prefix: Path,
    stage: str,
    store_id: str,
    source_export: Path | None = None,
) -> Path:
    """Write a tamper-evident, append-only recovery sidecar for one stage."""
    output_prefix = Path(out_prefix)
    output_json_path = output_prefix.with_suffix(".json")
    if not output_json_path.is_file():
        raise ReconciliationManifestError(
            f"写 reconcile manifest 前找不到 JSON 导出: {output_json_path}"
        )

    source: dict[str, str] | None = None
    if source_export is not None:
        source_path = Path(source_export)
        if not source_path.is_file():
            raise ReconciliationManifestError(
                f"reconcile 来源导出不存在: {source_path}"
            )
        source = {
            "json_path": str(source_path.resolve()),
            "sha256": sha256_file(source_path),
        }
        source_manifest_path = reconciliation_manifest_path(source_path)
        if source_manifest_path.is_file():
            source["manifest_path"] = str(source_manifest_path.resolve())

    rows_list = [row for row in rows if isinstance(row, dict)]
    manifest = {
        "schema_version": 1,
        "run_id": f"{stage}-{uuid.uuid4().hex}",
        "stage": str(stage or "").strip(),
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "store_id": str(store_id or "").strip(),
        "source": source,
        "output": {
            "json_path": str(output_json_path.resolve()),
            "sha256": sha256_file(output_json_path),
        },
        "rows": [
            _reconciliation_row_summary(row, store_id=store_id)
            for row in rows_list
        ],
    }
    manifest_path = reconciliation_manifest_path(output_json_path)
    manifest_path.write_text(
        json.dumps(_sanitize_export_value(manifest), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest_path


def write_generic_reports(
    rows: list[dict],
    out_prefix: Path,
    *,
    fieldnames: list[str] | None = None,
) -> dict[str, Path]:
    """任意列 json + csv（6–9 步导出用，不走筛查中文表头）。"""
    out_prefix = Path(out_prefix)
    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    names = list(fieldnames or [])
    if not names:
        seen: set[str] = set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    names.append(key)
    json_path = out_prefix.with_suffix(".json")
    json_path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    csv_path = out_prefix.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=names, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in names})
    return {"json": json_path, "csv": csv_path}
