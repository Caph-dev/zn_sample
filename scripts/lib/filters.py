#!/usr/bin/env python3
"""样品申请筛查规则（对齐 样品申请筛查sop.md）。

列表层可判定的条件先做；详情页专属指标（视频/直播 GPM 等）标为 unknown，
默认 **不** 因 unknown 直接否决（可用 require_detail 收紧）。

硬性：本模块只返回 eligible 判定，不触发任何「同意」动作。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .parse_metrics import (
    category_names,
    female_ratio,
    parse_count,
    parse_money,
    parse_percent,
)

# SOP 条件 6：类目（英文名，与页面 creator.categories[].name 对齐）
ALLOWED_CATEGORIES = {
    "Beauty & Personal Care",
    "Womenswear & Underwear",
    "Household Appliances",
    "Fashion Accessories",
    "Shoes",
    "Sports & Outdoor",
    "Home Textiles",
}

# 中文别名（列表文案偶发中文）
CATEGORY_ALIASES = {
    "美妆个护": "Beauty & Personal Care",
    "女装与女士内衣": "Womenswear & Underwear",
    "家用电器": "Household Appliances",
    "时尚配件": "Fashion Accessories",
    "鞋靴": "Shoes",
    "运动与户外": "Sports & Outdoor",
    "运动户外": "Sports & Outdoor",
    "家纺": "Home Textiles",
    "家居纺织": "Home Textiles",
}


@dataclass
class Criteria:
    min_followers: float = 2000
    min_gmv: float = 1500  # 1k5
    min_units: float = 80
    min_gpm_like: float = 10  # 千次曝光成交金额 / 视频GPM 同门槛先用列表近似
    min_aov: float = 10
    max_aov: float = 25
    min_fulfillment: float = 80  # 预计发布率/履约
    min_female_pct: float = 60
    # 视频/直播（详情页；列表暂无）
    min_video_gpm: float = 10
    min_live_gpm: float = 12
    min_avg_video_views: float = 300
    min_avg_live_views: float = 1000
    min_video_engagement: float = 2  # %
    allowed_categories: set[str] = field(default_factory=lambda: set(ALLOWED_CATEGORIES))
    # 列表无 GPM 时：用 GMV / (views/1000) 近似「千次曝光成交」
    use_list_gpm_proxy: bool = True
    require_category: bool = True
    require_hero_sku: bool = True


# 当前只批准 / 跟进这一款 B005。空集合 = 全部主推（--all-hero-products）。
ACTIVE_HERO_PRODUCT_ID = "1732414717062320994"
DEFAULT_ACTIVE_HERO_PRODUCT_IDS = frozenset({ACTIVE_HERO_PRODUCT_ID})


def reject_if_not_active_hero_product(
    product_id: str | None,
    allowed_product_ids: set[str] | frozenset[str] | None,
) -> str | None:
    """允许集合为空则不额外限制（恢复凡主推均可）。"""
    if not allowed_product_ids:
        return None
    pid = str(product_id or "").strip()
    if pid in allowed_product_ids:
        return None
    return f"非当前跟进款({pid or '无product_id'})"


def normalize_category(name: str) -> str:
    s = (name or "").strip()
    return CATEGORY_ALIASES.get(s, s)


def load_hero_keys(path: str | None) -> set[str]:
    """主推款 key 集合：product_id / sku_id / 货号 字符串（小写去空格）。

    支持：
      - .txt 每行一个
      - .csv 含 product_id 或 sku_id 或 sku/货号 列
      - .json 数组 of str 或 {product_id/sku_id/...}
    """
    if not path:
        return set()
    from pathlib import Path
    import csv
    import json

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"主推款文件不存在: {p}")
    keys: set[str] = set()

    def add(x: Any) -> None:
        if x is None:
            return
        s = str(x).strip().lower().replace(" ", "")
        if s:
            keys.add(s)

    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    add(item)
                elif isinstance(item, dict):
                    for k in ("product_id", "sku_id", "sku", "货号", "hero_sku", "id"):
                        if k in item:
                            add(item[k])
        elif isinstance(data, dict):
            for v in data.values():
                if isinstance(v, list):
                    for item in v:
                        add(item if not isinstance(item, dict) else item.get("product_id") or item.get("sku_id"))
    elif p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                for row in reader:
                    for k in ("product_id", "sku_id", "sku", "货号", "hero_sku", "id"):
                        if k in row:
                            add(row.get(k))
            else:
                f.seek(0)
                for line in f:
                    add(line.strip())
    else:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                add(line)
    return keys


def enrich_row(raw: dict) -> dict:
    """从列表 raw 抽出数值字段。"""
    followers = parse_count(raw.get("follower_num"))
    gmv = parse_money(raw.get("gmv") or raw.get("cell_gmv"))
    units = parse_count(raw.get("item_sold") or raw.get("cell_units"))
    views = parse_count(raw.get("content_video_views") or raw.get("cell_video_views"))
    fulfill = parse_percent(raw.get("fulfillment_rate") or raw.get("cell_fulfill"))
    female = female_ratio(raw.get("top_follower_gender"))
    cats = [normalize_category(x) for x in category_names(raw.get("categories"))]
    aov = (gmv / units) if (gmv is not None and units and units > 0) else None
    # 列表近似：千次曝光成交 ≈ GMV / (播放量/1000)
    gpm_proxy = None
    if gmv is not None and views and views > 0:
        gpm_proxy = gmv / (views / 1000.0)

    return {
        **raw,
        "followers_n": followers,
        "gmv_n": gmv,
        "units_n": units,
        "views_n": views,
        "fulfillment_n": fulfill,
        "female_pct": female,
        "categories_n": cats,
        "aov_n": aov,
        "gpm_proxy_n": gpm_proxy,
        "video_gpm_n": parse_money(raw.get("video_gpm")),  # 详情页将来填
        "live_gpm_n": parse_money(raw.get("live_gpm")),
        "avg_video_views_n": parse_count(raw.get("avg_video_views")),
        "avg_live_views_n": parse_count(raw.get("avg_live_views")),
        "video_engagement_n": parse_percent(raw.get("video_engagement")),
        "creator_type": raw.get("creator_type") or "",  # 视频达人/直播达人
    }


def evaluate_row(
    raw: dict,
    *,
    criteria: Criteria | None = None,
    hero_keys: set[str] | None = None,
    skip_hero_check: bool = False,
    require_detail: bool = False,
    allowed_product_ids: set[str] | frozenset[str] | None = None,
) -> dict:
    """返回带 eligible / reason / metrics 的行。"""
    from .feishu_hero import match_hero  # 避免循环

    c = criteria or Criteria()
    hero_keys = hero_keys or set()
    row = enrich_row(raw)
    # 详情页字段优先覆盖
    if raw.get("video_gpm_n") is not None:
        row["video_gpm_n"] = raw["video_gpm_n"]
    if raw.get("live_gpm_n") is not None:
        row["live_gpm_n"] = raw["live_gpm_n"]
    if raw.get("avg_video_views_n") is not None:
        row["avg_video_views_n"] = raw["avg_video_views_n"]
    if raw.get("avg_live_views_n") is not None:
        row["avg_live_views_n"] = raw["avg_live_views_n"]
    if raw.get("video_engagement_n") is not None:
        row["video_engagement_n"] = raw["video_engagement_n"]
    if raw.get("overall_gpm_n") is not None:
        row["gpm_proxy_n"] = raw["overall_gpm_n"]  # 官方 GPM 优先
    if raw.get("aov_detail_n") is not None:
        row["aov_n"] = raw["aov_detail_n"]
    if raw.get("est_post_rate_n") is not None:
        row["fulfillment_n"] = raw["est_post_rate_n"]
    if raw.get("creator_type"):
        row["creator_type"] = raw["creator_type"]

    fails: list[str] = []
    unknowns: list[str] = []

    def need(cond: bool, msg: str) -> None:
        if not cond:
            fails.append(msg)

    # 1 粉丝
    if row["followers_n"] is None:
        unknowns.append("粉丝数缺失")
    else:
        need(row["followers_n"] > c.min_followers, f"粉丝数 {row['followers_n']} ≤ {c.min_followers}")

    # 2 GMV
    if row["gmv_n"] is None:
        unknowns.append("GMV缺失")
    else:
        need(row["gmv_n"] > c.min_gmv, f"GMV {row['gmv_n']} ≤ {c.min_gmv}")

    # 3 成交件数
    if row["units_n"] is None:
        unknowns.append("成交件数缺失")
    else:
        need(row["units_n"] > c.min_units, f"成交件数 {row['units_n']} ≤ {c.min_units}")

    # 4 千次曝光：优先详情 overall GPM，否则列表代理
    gpm_val = row.get("gpm_proxy_n")
    if gpm_val is None and c.use_list_gpm_proxy:
        unknowns.append("千次曝光/GPM缺失")
    elif gpm_val is not None:
        need(gpm_val > c.min_gpm_like, f"GPM/千次曝光 {gpm_val:.2f} ≤ {c.min_gpm_like}")

    # 5 客单价
    if row["aov_n"] is None:
        unknowns.append("客单价缺失")
    else:
        need(
            c.min_aov <= row["aov_n"] <= c.max_aov,
            f"客单价 {row['aov_n']:.2f} 不在 [{c.min_aov},{c.max_aov}]",
        )

    # 6 类目
    if c.require_category:
        if not row["categories_n"]:
            unknowns.append("类目缺失")
        else:
            hit = any(cat in c.allowed_categories for cat in row["categories_n"])
            need(hit, f"类目不匹配: {row['categories_n']}")

    # 7 履约/预计发布率
    if row["fulfillment_n"] is None:
        unknowns.append("履约率缺失")
    else:
        need(
            row["fulfillment_n"] > c.min_fulfillment,
            f"履约率 {row['fulfillment_n']} ≤ {c.min_fulfillment}",
        )

    # 8 女性粉丝
    if row["female_pct"] is None:
        unknowns.append("女性占比缺失")
    else:
        need(
            row["female_pct"] > c.min_female_pct,
            f"女性占比 {row['female_pct']:.1f}% ≤ {c.min_female_pct}%",
        )

    # 9–11 视频/直播（详情页）二选一
    detail_ok = False
    # 已访问详情：上游打标，或任一详情字段已写入（含 overall/客单价）
    detail_checked = bool(
        raw.get("detail_checked")
        or row.get("video_gpm_n") is not None
        or row.get("live_gpm_n") is not None
        or raw.get("overall_gpm")
        or raw.get("overall_gpm_n") is not None
        or raw.get("video_gpm")
        or raw.get("live_gpm")
        or raw.get("revenue_per_buyer")
        or raw.get("aov_detail_n") is not None
    )
    has_detail_metrics = (
        row.get("video_gpm_n") is not None or row.get("live_gpm_n") is not None
    )
    if has_detail_metrics:
        detail_checked = True
        video_ok = (
            row.get("video_gpm_n") is not None
            and row["video_gpm_n"] > c.min_video_gpm
            and (
                row.get("avg_video_views_n") is None
                or row["avg_video_views_n"] > c.min_avg_video_views
            )
            and (
                row.get("video_engagement_n") is None
                or row["video_engagement_n"] > c.min_video_engagement
            )
        )
        live_ok = (
            row.get("live_gpm_n") is not None
            and row["live_gpm_n"] > c.min_live_gpm
            and (
                row.get("avg_live_views_n") is None
                or row["avg_live_views_n"] > c.min_avg_live_views
            )
        )
        detail_ok = bool(video_ok or live_ok)
        if not detail_ok:
            fails.append("视频/直播数据均不达标")
        # 导出用：视频达人 / 直播达人 两列（是 / 空）
        if video_ok:
            row["is_video_creator"] = "是"
        if live_ok:
            row["is_live_creator"] = "是"
        if not row.get("creator_type"):
            if video_ok and live_ok:
                row["creator_type"] = "视频+直播"
            elif video_ok:
                row["creator_type"] = "视频达人"
            elif live_ok:
                row["creator_type"] = "直播达人"
        # 详情有 GPM 但未达标：仍按有数据侧标记类型（便于名单区分）
        if not video_ok and not live_ok:
            vg = row.get("video_gpm_n")
            lg = row.get("live_gpm_n")
            if vg is not None and float(vg) > 0:
                row["is_video_creator"] = row.get("is_video_creator") or "是"
            if lg is not None and float(lg) > 0:
                row["is_live_creator"] = row.get("is_live_creator") or "是"
    else:
        if detail_checked:
            unknowns.append("视频/直播GPM抽取为空")
            if require_detail:
                fails.append("缺少视频/直播GPM(详情已打开但未读到)")
        else:
            unknowns.append("视频/直播GPM未拉详情")
            if require_detail:
                fails.append("缺少达人详情(视频/直播指标)")

    # 主推款（飞书货号）
    hero = None
    hero_match_reason = ""
    if skip_hero_check or not c.require_hero_sku:
        hero = True
        hero_match_reason = "skip"
    elif not hero_keys:
        hero = False
        hero_match_reason = "no-hero-table"
        need(False, "非主推款(未加载飞书主推表或是否主推为空)")
    else:
        hero, hero_match_reason = match_hero(row, hero_keys)
        if hero is False:
            need(False, f"非主推款({hero_match_reason})")
        elif hero is None:
            unknowns.append(hero_match_reason)
            need(False, f"非主推款({hero_match_reason})")

    active_product_reason = reject_if_not_active_hero_product(
        row.get("product_id") or raw.get("product_id"),
        allowed_product_ids,
    )
    if active_product_reason:
        need(False, active_product_reason)

    eligible = len(fails) == 0
    reason = "通过" if eligible else "; ".join(fails)
    if unknowns:
        reason = reason + (" | " if reason else "") + "unknown: " + ", ".join(unknowns)

    return {
        **row,
        "is_hero": hero,
        "hero_match": hero_match_reason,
        "eligible": eligible,
        "reason": reason,
        "fail_reasons": fails,
        "unknowns": unknowns,
        "detail_checked": detail_checked,
        "detail_ok": detail_ok,
        "action": "export-only",  # 禁止同意
        "approve_forbidden": True,
    }
