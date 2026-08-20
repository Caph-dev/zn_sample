#!/usr/bin/env python3
"""导出 csv + xlsx（openpyxl 可选；无则写简易 xlsx）。"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXPORT_DIR = REPO_ROOT / "exports"


def latest_screen_export(exports_dir: Path | None = None) -> Path:
    """exports/ 里最新一份正式筛查 json，排除批准前备份。"""
    directory = Path(exports_dir or DEFAULT_EXPORT_DIR)
    candidates = [
        path
        for path in directory.glob("sample_screen_*.json")
        if path.is_file() and "_pre_execute" not in path.name
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
    "feishu_status",
    "feishu_record_id",
    "feishu_error",
    "sample_product_option",
    "resolved_sku",
    "order_no",
    "feishu_order_status",
    "feishu_order_error",
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
    "飞书状态",
    "飞书record_id",
    "飞书错误",
    "寄样产品选项",
    "解析货号",
    "待发货订单号",
    "订单号回填状态",
    "订单号回填错误",
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


def prepare_export_row(row: dict) -> dict:
    """写表前补齐「视频达人」「直播达人」列为「是」或空。"""
    out = dict(row)
    video_flag, live_flag = derive_creator_type_flags(out)
    out["is_video_creator"] = video_flag
    out["is_live_creator"] = live_flag
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
    return [_cell(prepared.get(field_name)) for field_name in EXPORT_FIELDS]


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
