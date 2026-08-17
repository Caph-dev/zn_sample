#!/usr/bin/env python3
"""从飞书「tk产品图+货号」电子表格拉取主推货号（只读）。

默认资源（知识库节点 → 电子表格）:
  https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc

凭证（勿写入 git；优先级 CLI > 环境变量 > config.toml）:
  FEISHU_APP_ID / [feishu].app_id           默认 cli_a96b1c39ac789bb4
  FEISHU_APP_SECRET / [feishu].app_secret   必填
  FEISHU_HERO_URL / [feishu].hero_url       可选
  配置文件：仓库根 config.toml（见 config.toml.example）

表结构（实测 API）:
  表头含「产品货号」「是否主推」；图片列忽略。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .app_config import AppConfigError, load_feishu_settings

OPEN_API_BASE = "https://open.feishu.cn/open-apis"

DEFAULT_APP_ID = "cli_a96b1c39ac789bb4"
DEFAULT_HERO_URL = (
    "https://rsed6zggjt.feishu.cn/wiki/Bw0cwepLyiivGjkJH5IcVknJnQc"
)
DEFAULT_SHEET_TITLE = "Sheet1"
DEFAULT_READ_COLS = "A:H"
TRUE_VALUES = {"是", "y", "yes", "true", "1", "主推", "Y", "YES", "True"}

_CELL_CHUNK_ROWS = 500


class FeishuHeroError(RuntimeError):
    """飞书主推表读取失败。"""


def _normalize_sku_key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _http_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    for header_name, header_value in (headers or {}).items():
        request.add_header(header_name, header_value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raw_body = error.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as decode_error:
            raise FeishuHeroError(
                f"HTTP {error.code} 非 JSON: {raw_body[:500]}"
            ) from decode_error
        raise FeishuHeroError(
            f"HTTP {error.code} code={payload.get('code')} msg={payload.get('msg')}"
        ) from error
    except urllib.error.URLError as error:
        raise FeishuHeroError(f"网络错误: {error}") from error

    if not isinstance(payload, dict):
        raise FeishuHeroError(f"响应非对象: {type(payload)}")
    # auth 成功带 tenant_access_token；业务接口 code==0
    if payload.get("tenant_access_token"):
        return payload
    if payload.get("code") not in (0, None):
        raise FeishuHeroError(
            f"飞书 API 失败 code={payload.get('code')} msg={payload.get('msg')}"
        )
    return payload


def get_tenant_access_token(
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    config_path: str | Path | None = None,
) -> str:
    try:
        settings = load_feishu_settings(
            config_path=config_path,
            app_id=app_id,
            app_secret=app_secret,
            default_app_id=DEFAULT_APP_ID,
        )
    except AppConfigError as error:
        raise FeishuHeroError(str(error)) from error

    resolved_app_id = settings.get("app_id")
    resolved_secret = settings.get("app_secret")
    if not resolved_app_id:
        raise FeishuHeroError("缺少 FEISHU_APP_ID / [feishu].app_id")
    if not resolved_secret:
        raise FeishuHeroError(
            "缺少 App Secret：请在 config.toml 的 [feishu].app_secret 填写，"
            "或 export FEISHU_APP_SECRET='...'，或传 --feishu-app-secret"
        )

    payload = _http_json(
        "POST",
        f"{OPEN_API_BASE}/auth/v3/tenant_access_token/internal",
        body={"app_id": resolved_app_id, "app_secret": resolved_secret},
    )
    if payload.get("code") not in (0, None):
        raise FeishuHeroError(
            f"获取 token 失败 code={payload.get('code')} msg={payload.get('msg')}"
        )
    token = payload.get("tenant_access_token")
    if not token:
        raise FeishuHeroError(f"token 响应无 tenant_access_token: {payload}")
    return str(token)


def extract_resource_token(url_or_token: str) -> tuple[str, str]:
    """返回 (kind, token)。kind: wiki | sheets | raw。"""
    text = (url_or_token or "").strip()
    if not text:
        raise FeishuHeroError("空的飞书链接/token")
    if "://" not in text and "/" not in text:
        return "raw", text

    wiki_match = re.search(r"/wiki/([A-Za-z0-9]+)", text)
    if wiki_match:
        return "wiki", wiki_match.group(1)

    sheets_match = re.search(r"/sheets/([A-Za-z0-9]+)", text)
    if sheets_match:
        return "sheets", sheets_match.group(1)

    raise FeishuHeroError(f"无法从链接解析 wiki/sheets token: {text}")


def resolve_spreadsheet_token(
    access_token: str,
    url_or_token: str,
) -> dict[str, Any]:
    kind, token = extract_resource_token(url_or_token)
    if kind in {"sheets", "raw"}:
        return {
            "kind": kind,
            "spreadsheet_token": token,
            "node_token": None,
            "title": None,
        }

    payload = _http_json(
        "GET",
        f"{OPEN_API_BASE}/wiki/v2/spaces/get_node?token={urllib.parse.quote(token)}",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if payload.get("code") != 0:
        raise FeishuHeroError(
            f"wiki get_node 失败 code={payload.get('code')} msg={payload.get('msg')}"
        )
    node = (payload.get("data") or {}).get("node") or {}
    obj_type = node.get("obj_type")
    obj_token = node.get("obj_token")
    if obj_type != "sheet" or not obj_token:
        raise FeishuHeroError(
            f"知识库节点不是电子表格: obj_type={obj_type} obj_token={obj_token}"
        )
    return {
        "kind": "wiki",
        "spreadsheet_token": str(obj_token),
        "node_token": token,
        "title": node.get("title"),
        "space_id": node.get("space_id"),
    }


def list_sheet_tabs(access_token: str, spreadsheet_token: str) -> list[dict[str, Any]]:
    payload = _http_json(
        "GET",
        f"{OPEN_API_BASE}/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if payload.get("code") != 0:
        raise FeishuHeroError(
            f"sheets/query 失败 code={payload.get('code')} msg={payload.get('msg')}"
        )
    sheets = (payload.get("data") or {}).get("sheets") or []
    if not sheets:
        raise FeishuHeroError("电子表格无子表")
    return sheets


def _sheet_row_count(sheet: dict[str, Any]) -> int:
    grid = sheet.get("grid_properties") or sheet.get("gridProperties") or {}
    try:
        count = int(grid.get("row_count") or grid.get("rowCount") or 0)
    except (TypeError, ValueError):
        count = 0
    return max(count, 200)


def read_sheet_values(
    access_token: str,
    spreadsheet_token: str,
    sheet_id: str,
    *,
    columns: str = DEFAULT_READ_COLS,
    max_rows: int | None = None,
) -> list[list[Any]]:
    """按块读取工作表，返回二维 values。"""
    end_row = max_rows if max_rows and max_rows > 0 else None
    # 未知总行数时多读几块；有 row_count 则按上限切块
    if end_row is None:
        end_row = 5000

    all_values: list[list[Any]] = []
    start_row = 1
    while start_row <= end_row:
        chunk_end = min(start_row + _CELL_CHUNK_ROWS - 1, end_row)
        # columns like A:H → A{start}:H{end}
        if ":" in columns:
            start_col, end_col = columns.split(":", 1)
        else:
            start_col, end_col = columns, columns
        range_a1 = f"{sheet_id}!{start_col}{start_row}:{end_col}{chunk_end}"
        encoded_range = urllib.parse.quote(range_a1, safe="")
        query = urllib.parse.urlencode(
            {
                "valueRenderOption": "ToString",
                "dateTimeRenderOption": "FormattedString",
            }
        )
        payload = _http_json(
            "GET",
            f"{OPEN_API_BASE}/sheets/v2/spreadsheets/{spreadsheet_token}/values/"
            f"{encoded_range}?{query}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if payload.get("code") != 0:
            raise FeishuHeroError(
                f"values 读取失败 range={range_a1} "
                f"code={payload.get('code')} msg={payload.get('msg')}"
            )
        chunk = (
            (payload.get("data") or {})
            .get("valueRange", {})
            .get("values")
        ) or []
        if not chunk:
            break
        all_values.extend(chunk)
        # 不足一块说明已到表尾
        if len(chunk) < (chunk_end - start_row + 1):
            break
        start_row = chunk_end + 1

    return all_values


def _cell_text(row: list[Any], index: int) -> str:
    if index < 0 or index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    return str(value).strip()


def _find_header_row(values: list[list[Any]], scan_limit: int = 15) -> tuple[int, list[str]]:
    limit = min(len(values), scan_limit)
    for row_index in range(limit):
        header = [_cell_text(values[row_index], col) for col in range(len(values[row_index]))]
        joined = " ".join(header)
        if "货号" in joined and "主推" in joined:
            return row_index, header
    raise FeishuHeroError(
        "未找到含「产品货号/是否主推」的表头行；"
        f"前几行={values[:3]!r}"
    )


def _find_column_index(header: list[str], *needles: str, default: int | None = None) -> int:
    for index, title in enumerate(header):
        for needle in needles:
            if needle and needle in title:
                return index
    if default is not None:
        return default
    raise FeishuHeroError(f"表头中找不到列 {needles}；header={header}")


def parse_hero_grid(values: list[list[Any]]) -> dict[str, Any]:
    """把二维表格解析为与旧 parse_hero_xlsx 兼容的结构。"""
    if not values:
        return {
            "all_skus": [],
            "hero_skus": [],
            "hero_keys": set(),
            "all_keys": set(),
            "rows": [],
            "sku_col": -1,
            "hero_col": -1,
        }

    header_row_index, header = _find_header_row(values)
    sku_col = _find_column_index(header, "产品货号", "货号", default=2)
    hero_col = _find_column_index(header, "是否主推", "主推", default=6)
    sell_col = _find_column_index(header, "卖点", default=3)
    comm_col = _find_column_index(header, "佣金", default=4)
    product_id_col = _find_column_index(header, "商品ID", "商品 Id", "商品id", default=-1)

    rows: list[dict[str, Any]] = []
    hero_skus: list[str] = []
    hero_product_ids: list[str] = []
    all_skus: list[str] = []
    all_product_ids: list[str] = []
    pending_sku: str | None = None

    def _usable_product_id(value: str) -> str:
        """平台商品 ID；排除「下架」等非 ID 文案。"""
        text = (value or "").strip()
        if not text:
            return ""
        if any(marker in text for marker in ("下架", "无", "—", "-", "N/A", "n/a")):
            # 纯数字 ID 不会进这里；带中文说明的跳过
            if not text.isdigit():
                return ""
        # 飞书偶发科学计数；ToString 后一般是纯数字字符串
        if text.isdigit() or re.fullmatch(r"\d+\.0+", text):
            return text.split(".", 1)[0]
        # 允许带空格的长数字
        digits_only = re.sub(r"\s+", "", text)
        if digits_only.isdigit() and len(digits_only) >= 6:
            return digits_only
        return ""

    def _register_hero_product_id(value: str) -> None:
        pid = _usable_product_id(value)
        if pid and pid not in hero_product_ids:
            hero_product_ids.append(pid)

    def _register_all_product_id(value: str) -> None:
        pid = _usable_product_id(value)
        if pid and pid not in all_product_ids:
            all_product_ids.append(pid)

    for raw_row in values[header_row_index + 1 :]:
        row = list(raw_row or [])
        sku = _cell_text(row, sku_col)
        hero_raw = _cell_text(row, hero_col)
        sell = _cell_text(row, sell_col)
        commission = _cell_text(row, comm_col)
        product_id = _cell_text(row, product_id_col) if product_id_col >= 0 else ""

        if sku:
            pending_sku = sku
            all_skus.append(sku)
            _register_all_product_id(product_id)
            is_hero = hero_raw in TRUE_VALUES
            if is_hero:
                hero_skus.append(sku)
                _register_hero_product_id(product_id)
            rows.append(
                {
                    "sku": sku,
                    "is_hero": is_hero,
                    "hero_raw": hero_raw,
                    "commission": commission,
                    "selling_points": sell,
                    "product_id": product_id,
                }
            )
            continue

        # 合并单元格：主推标记落在货号下一空行
        if pending_sku and hero_raw and rows and rows[-1]["sku"] == pending_sku:
            is_hero = hero_raw in TRUE_VALUES
            if not rows[-1].get("hero_raw"):
                rows[-1]["is_hero"] = is_hero
                rows[-1]["hero_raw"] = hero_raw
                if is_hero and pending_sku not in hero_skus:
                    hero_skus.append(pending_sku)
                if is_hero:
                    _register_hero_product_id(rows[-1].get("product_id") or product_id)
            if sell and not rows[-1].get("selling_points"):
                rows[-1]["selling_points"] = sell
            if commission and not rows[-1].get("commission"):
                rows[-1]["commission"] = commission
            if product_id and not rows[-1].get("product_id"):
                rows[-1]["product_id"] = product_id
                _register_all_product_id(product_id)
                if rows[-1].get("is_hero"):
                    _register_hero_product_id(product_id)

    # 匹配键 = 主推货号 ∪ 主推行的平台商品ID
    # 待审核列表只有 product_id（数字），没有商家货号「2024」时必须靠商品ID命中
    hero_keys = {_normalize_sku_key(sku) for sku in hero_skus if sku}
    hero_keys |= {_normalize_sku_key(pid) for pid in hero_product_ids if pid}
    all_keys = {_normalize_sku_key(sku) for sku in all_skus if sku}
    all_keys |= {_normalize_sku_key(pid) for pid in all_product_ids if pid}

    return {
        "header_row_index": header_row_index,
        "header": header,
        "sku_col": sku_col,
        "hero_col": hero_col,
        "product_id_col": product_id_col,
        "all_skus": all_skus,
        "hero_skus": hero_skus,
        "hero_product_ids": hero_product_ids,
        "all_product_ids": all_product_ids,
        "hero_keys": hero_keys,
        "all_keys": all_keys,
        "rows": rows,
    }


# 只精确比对商家货号 / 平台商品 ID。标题、sku 描述、TikTok sku_id 禁止参与。
_HERO_EXACT_FIELDS = (
    "product_id",
    "sku",
    "seller_sku",
    "hero_sku",
    "resolved_sku",
)


def match_hero(product_fields: dict, hero_keys: set[str]) -> tuple[bool | None, str]:
    """精确匹配主推货号或商品 ID。

    ``hero_keys`` 只应包含「是否主推=是」的货号和商品 ID。
    不匹配标题/描述，也不做子串包含（避免货号 328 命中无关 product_id）。
    """
    if not hero_keys:
        return None, "无主推名单"

    for field_name in _HERO_EXACT_FIELDS:
        candidate = product_fields.get(field_name)
        normalized = _normalize_sku_key(candidate or "")
        if normalized and normalized in hero_keys:
            return True, f"精确匹配:{candidate}"
    return False, "未精确匹配货号/商品ID"


def load_hero_from_feishu(
    *,
    url: str | None = None,
    sheet_title: str | None = None,
    app_id: str | None = None,
    app_secret: str | None = None,
    access_token: str | None = None,
    config_path: str | Path | None = None,
) -> dict[str, Any]:
    """拉取飞书主推表并解析。

    返回字段兼容旧 parse_hero_xlsx，并附加 source/spreadsheet_token 等元数据。
    """
    try:
        settings = load_feishu_settings(
            config_path=config_path,
            app_id=app_id,
            app_secret=app_secret,
            hero_url=url,
            sheet_title=sheet_title,
            default_app_id=DEFAULT_APP_ID,
            default_hero_url=DEFAULT_HERO_URL,
            default_sheet_title=DEFAULT_SHEET_TITLE,
        )
    except AppConfigError as error:
        raise FeishuHeroError(str(error)) from error

    resolved_url = settings.get("hero_url") or DEFAULT_HERO_URL
    resolved_sheet_title = settings.get("sheet_title") or DEFAULT_SHEET_TITLE

    token = access_token or get_tenant_access_token(
        app_id=settings.get("app_id"),
        app_secret=settings.get("app_secret"),
        config_path=config_path,
    )
    resolved = resolve_spreadsheet_token(token, resolved_url)
    spreadsheet_token = resolved["spreadsheet_token"]
    sheets = list_sheet_tabs(token, spreadsheet_token)

    target_sheet = next(
        (item for item in sheets if str(item.get("title") or "") == resolved_sheet_title),
        None,
    )
    if target_sheet is None and len(sheets) == 1:
        target_sheet = sheets[0]
    if target_sheet is None:
        titles = [item.get("title") for item in sheets]
        raise FeishuHeroError(
            f"找不到子表 {resolved_sheet_title!r}，现有: {titles}"
        )

    sheet_id = target_sheet.get("sheet_id") or target_sheet.get("sheetId")
    if not sheet_id:
        raise FeishuHeroError(f"子表无 sheet_id: {target_sheet}")

    row_count = _sheet_row_count(target_sheet)
    values = read_sheet_values(
        token,
        spreadsheet_token,
        str(sheet_id),
        max_rows=row_count,
    )
    parsed = parse_hero_grid(values)
    parsed.update(
        {
            "source": "feishu",
            "url": resolved_url,
            "kind": resolved.get("kind"),
            "node_token": resolved.get("node_token"),
            "spreadsheet_token": spreadsheet_token,
            "sheet_title": target_sheet.get("title"),
            "sheet_id": sheet_id,
            "doc_title": resolved.get("title"),
            "raw_row_count": len(values),
            "config_path": settings.get("config_path"),
        }
    )
    return parsed
