#!/usr/bin/env python3
"""自动审批自定义规则：严格 schema、三态评估、证据信封与中文摘要。

与正式 SOP（lib.filters.Criteria）的关系：
- 正式 SOP 阈值与缺失值语义完全不变；本模块不改 filters、不改 Criteria。
- 自定义规则只绑定「本次任务」的快照，绝不在服务端改写正式默认值。

三态语义（4.4 节）：
- 启用指标缺失 → needs_review（不得默认通过；比正式 SOP 的宽松语义更严）
- 已知违反任一必需条件 → failed
- 完整满足 → passed
- 未启用的检查 → not_checked（不是「通过」，也不是「跳过即通过」）

本模块只做判定与证据校验，不触发任何平台/飞书写操作。
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .filters import ALLOWED_CATEGORIES

SCHEMA_VERSION = 1
MODE_CUSTOM = "custom"

# 第一版固定的后端边界（P0 决策；改这里必须同步 AGENTS.md 与页面 options）。
EXECUTE_LIMIT_MAX = 10
EXECUTE_LIMIT_DEFAULT = 1
PREVIEW_FRESHNESS_SECONDS = 24 * 60 * 60  # 证据新鲜度：执行时预览须在 24 小时内完成
PRODUCT_IDS_MAX = 20

# 内容审核：沿用本地审查库，第一版只支持与库一致的 7 天窗口与至少 4 条。
CONTENT_ALLOWED_DAYS = (7,)
CONTENT_ALLOWED_MIN_RELATED = (4,)

# 内容审查关闭时的统一标记（执行边界据此区分「合法关闭」与「证据缺失」）。
CONTENT_DISABLED_REASON = "custom_content_check_disabled"

# 基础条件数值上限（服务端拒绝超限；前端 options 同步同值）。
BASIC_LIMITS = {
    "followers": {"min": 0, "max": 1_000_000_000, "integer": True, "default": 2000},
    "gmv": {"min": 0, "max": 1_000_000_000, "integer": False, "default": 1500},
    "units": {"min": 0, "max": 1_000_000_000, "integer": True, "default": 80},
    "gpm": {"min": 0, "max": 1_000_000, "integer": False, "default": 10},
    "aov": {"min": 0, "max": 1_000_000, "integer": False, "default_min": 10, "default_max": 25},
    "fulfillment": {"min": 0, "max": 100, "integer": False, "default": 80},
    "female_pct": {"min": 0, "max": 100, "integer": False, "default": 60},
}
VIDEO_LIVE_LIMITS = {
    "video_gpm": {"min": 0, "max": 1_000_000, "default": 10},
    "video_avg_views": {"min": 0, "max": 1_000_000_000, "default": 300},
    "video_engagement": {"min": 0, "max": 100, "default": 2},
    "live_gpm": {"min": 0, "max": 1_000_000, "default": 12},
    "live_avg_views": {"min": 0, "max": 1_000_000_000, "default": 1000},
    "live_engagement": {"min": 0, "max": 100, "default": 2},
}

BASIC_KEYS = ("followers", "gmv", "units", "gpm", "aov", "fulfillment", "female_pct", "categories")


class AutoApprovalRuleError(RuntimeError):
    """规则校验失败；code 为稳定机器可读错误码，summary 给人看。"""

    def __init__(self, code: str, summary: str) -> None:
        super().__init__(summary)
        self.code = code
        self.summary = summary


@dataclass(frozen=True)
class BasicCondition:
    enabled: bool = False
    min_value: float | None = None
    max_value: float | None = None
    categories: tuple[str, ...] = ()


@dataclass(frozen=True)
class SideCondition:
    enabled: bool = False
    gpm: float | None = None
    avg_views: float | None = None
    engagement: float | None = None


@dataclass(frozen=True)
class VideoLiveGroup:
    enabled: bool = False
    logic: str = "either"  # either | both
    video: SideCondition = field(default_factory=SideCondition)
    live: SideCondition = field(default_factory=SideCondition)


@dataclass(frozen=True)
class ContentGroup:
    enabled: bool = False
    days: int = 7
    min_related: int = 4
    require_display: bool = True


@dataclass(frozen=True)
class CustomRule:
    schema_version: int = SCHEMA_VERSION
    mode: str = MODE_CUSTOM
    product_ids: tuple[str, ...] = ()
    basic: dict[str, BasicCondition] = field(default_factory=dict)
    video_live: VideoLiveGroup = field(default_factory=VideoLiveGroup)
    content: ContentGroup = field(default_factory=ContentGroup)

    def to_dict(self) -> dict[str, Any]:
        def basic_dict(condition: BasicCondition) -> dict[str, Any]:
            payload: dict[str, Any] = {"enabled": condition.enabled}
            if condition.min_value is not None:
                payload["min"] = condition.min_value
            if condition.max_value is not None:
                payload["max"] = condition.max_value
            if condition.enabled:
                payload["values"] = sorted(condition.categories)
            return payload

        def side_dict(side: SideCondition) -> dict[str, Any]:
            payload: dict[str, Any] = {"enabled": side.enabled}
            if side.gpm is not None:
                payload["gpm"] = side.gpm
            if side.avg_views is not None:
                payload["avg_views"] = side.avg_views
            if side.engagement is not None:
                payload["engagement"] = side.engagement
            return payload

        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "product_ids": list(self.product_ids),
            "basic": {key: basic_dict(self.basic.get(key, BasicCondition())) for key in BASIC_KEYS},
            "video_live": {
                "enabled": self.video_live.enabled,
                "logic": self.video_live.logic,
                "video": side_dict(self.video_live.video),
                "live": side_dict(self.video_live.live),
            },
            "content": {
                "enabled": self.content.enabled,
                "days": self.content.days,
                "min_related": self.content.min_related,
                "require_display": self.content.require_display,
            },
        }

    def any_check_enabled(self) -> bool:
        if self.video_live.enabled or self.content.enabled:
            return True
        return any(condition.enabled for condition in self.basic.values())


def _reject(code: str, summary: str) -> None:
    raise AutoApprovalRuleError(code, summary)


def _finite_number(value: Any, field_name: str, code: str = "invalid-number") -> float:
    """JSON 数字（int/float），拒绝 bool、字符串、NaN/Infinity。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject(code, f"{field_name} 必须是有限数字")
    number = float(value)
    if not math.isfinite(number):
        _reject(code, f"{field_name} 必须是有限数字")
    return number


def _read_basic_condition(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
) -> BasicCondition:
    limits = BASIC_LIMITS.get(key)
    if key not in payload:
        return BasicCondition()
    raw = payload[key]
    if not isinstance(raw, dict):
        _reject("invalid-basic-condition", f"{context} 的 {key} 必须是对象")
    allowed = {"enabled", "min", "max", "values"}
    unknown = set(raw) - allowed
    if unknown:
        _reject("unknown-field", f"{context}.{key} 含未知字段: {sorted(unknown)}")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        _reject("invalid-boolean", f"{context}.{key}.enabled 必须是布尔值")
    min_value: float | None = None
    max_value: float | None = None
    if "min" in raw and raw["min"] is not None:
        min_value = _finite_number(raw["min"], f"{context}.{key}.min")
        if not limits["min"] <= min_value <= limits["max"]:
            _reject(
                "value-out-of-range",
                f"{context}.{key}.min 超出范围 [{limits['min']}, {limits['max']}]",
            )
        if limits.get("integer") and min_value != int(min_value):
            _reject("invalid-integer", f"{context}.{key}.min 必须是整数")
    if "max" in raw and raw["max"] is not None:
        max_value = _finite_number(raw["max"], f"{context}.{key}.max")
        if not limits["min"] <= max_value <= limits["max"]:
            _reject(
                "value-out-of-range",
                f"{context}.{key}.max 超出范围 [{limits['min']}, {limits['max']}]",
            )
    if key == "categories":
        categories: tuple[str, ...] = ()
        if enabled:
            raw_values = raw.get("values")
            if not isinstance(raw_values, list) or not raw_values:
                _reject("missing-value", f"{context}.categories 启用时必须选择至少一个类目")
            for value in raw_values:
                if not isinstance(value, str) or value not in ALLOWED_CATEGORIES:
                    _reject(
                        "invalid-category",
                        f"{context}.categories 含不允许的类目: {value}",
                    )
            categories = tuple(raw_values)
        return BasicCondition(
            enabled=enabled,
            min_value=min_value,
            max_value=max_value,
            categories=categories,
        )
    if enabled:
        if key == "aov":
            if min_value is None or max_value is None:
                _reject("missing-value", f"{context}.aov 启用时必须提供 min 与 max")
            if min_value > max_value:
                _reject("invalid-range", f"{context}.aov min 必须 ≤ max")
        else:
            if min_value is None:
                _reject("missing-value", f"{context}.{key} 启用时必须提供 min")
            if max_value is not None and max_value < min_value:
                _reject("invalid-range", f"{context}.{key} min 必须 ≤ max")
    return BasicCondition(enabled=enabled, min_value=min_value, max_value=max_value)


def _read_side_condition(
    payload: dict[str, Any],
    key: str,
    *,
    context: str,
) -> SideCondition:
    if key not in payload:
        return SideCondition()
    raw = payload[key]
    if not isinstance(raw, dict):
        _reject("invalid-side-condition", f"{context}.{key} 必须是对象")
    allowed = {"enabled", "gpm", "avg_views", "engagement"}
    unknown = set(raw) - allowed
    if unknown:
        _reject("unknown-field", f"{context}.{key} 含未知字段: {sorted(unknown)}")
    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        _reject("invalid-boolean", f"{context}.{key}.enabled 必须是布尔值")

    def read_metric(metric_key: str, suffix: str, required: bool) -> float | None:
        value: float | None = None
        # 显式 null 视为「未提供」：禁用侧允许为 null，启用侧按 missing-value 报错。
        if metric_key in raw and raw[metric_key] is not None:
            value = _finite_number(raw[metric_key], f"{context}.{key}.{metric_key}")
        limits = VIDEO_LIVE_LIMITS[f"{suffix}_{metric_key}"]
        if value is not None and not limits["min"] <= value <= limits["max"]:
            _reject(
                "value-out-of-range",
                f"{context}.{key}.{metric_key} 超出范围 [{limits['min']}, {limits['max']}]",
            )
        if enabled and required and value is None:
            _reject("missing-value", f"{context}.{key}.{metric_key} 启用时必须提供")
        return value

    if key == "video":
        return SideCondition(
            enabled=enabled,
            gpm=read_metric("gpm", "video", required=True),
            avg_views=read_metric("avg_views", "video", required=True),
            engagement=read_metric("engagement", "video", required=True),
        )
    return SideCondition(
        enabled=enabled,
        gpm=read_metric("gpm", "live", required=True),
        avg_views=read_metric("avg_views", "live", required=True),
        engagement=read_metric("engagement", "live", required=False),
    )


def validate_custom_rule(
    payload: dict[str, Any],
    *,
    allowed_product_ids: set[str],
) -> CustomRule:
    """严格校验客户端规则；未知字段、非法数值、空组合、非允许商品都拒绝。"""
    if not isinstance(payload, dict):
        _reject("invalid-rule", "规则必须是 JSON 对象")
    if set(payload) - {"schema_version", "mode", "product_ids", "basic", "video_live", "content"}:
        _reject("unknown-field", f"规则含未知字段: {sorted(set(payload) - {'schema_version', 'mode', 'product_ids', 'basic', 'video_live', 'content'})}")
    if payload.get("schema_version") != SCHEMA_VERSION:
        _reject("unsupported-schema", f"仅支持规则 schema 版本 {SCHEMA_VERSION}")
    if payload.get("mode") != MODE_CUSTOM:
        _reject("invalid-mode", "自动审批页面只接受 custom 模式；正式 SOP 请走既有入口")

    raw_products = payload.get("product_ids")
    if not isinstance(raw_products, list) or not raw_products:
        _reject("missing-value", "必须至少选择一款主推商品")
    if len(raw_products) > PRODUCT_IDS_MAX:
        _reject("value-out-of-range", f"商品数量不得超过 {PRODUCT_IDS_MAX}")
    product_ids: list[str] = []
    for value in raw_products:
        if not isinstance(value, str) or not value.strip():
            _reject("invalid-product", "商品 ID 必须是非空字符串")
        if value in product_ids:
            _reject("invalid-product", f"商品 ID 重复: {value}")
        product_ids.append(value)
    disallowed = [pid for pid in product_ids if pid not in allowed_product_ids]
    if disallowed:
        _reject(
            "product-not-allowed",
            f"商品不在当前主推款集合中: {', '.join(sorted(disallowed))}",
        )

    basic_raw = payload.get("basic", {})
    if not isinstance(basic_raw, dict):
        _reject("invalid-basic-condition", "basic 必须是对象")
    unknown_basic = set(basic_raw) - set(BASIC_KEYS)
    if unknown_basic:
        _reject("unknown-field", f"basic 含未知条件: {sorted(unknown_basic)}")
    basic = {
        key: _read_basic_condition(basic_raw, key, context="basic")
        for key in BASIC_KEYS
    }

    video_live_raw = payload.get("video_live", {})
    if not isinstance(video_live_raw, dict):
        _reject("invalid-video-live", "video_live 必须是对象")
    unknown_group = set(video_live_raw) - {"enabled", "logic", "video", "live"}
    if unknown_group:
        _reject("unknown-field", f"video_live 含未知字段: {sorted(unknown_group)}")
    group_enabled = video_live_raw.get("enabled", False)
    if not isinstance(group_enabled, bool):
        _reject("invalid-boolean", "video_live.enabled 必须是布尔值")
    logic = video_live_raw.get("logic", "either")
    if logic not in {"either", "both"}:
        _reject("invalid-logic", "video_live.logic 只允许 either 或 both")
    video_side = _read_side_condition(video_live_raw, "video", context="video_live")
    live_side = _read_side_condition(video_live_raw, "live", context="video_live")
    if group_enabled:
        enabled_sides = sum(1 for side in (video_side, live_side) if side.enabled)
        if enabled_sides == 0:
            _reject("missing-value", "video_live 启用时至少启用视频或直播一侧")
        if logic == "both" and enabled_sides < 2:
            _reject("invalid-logic", "两侧均满足（both）必须同时启用视频与直播")
    elif video_side.enabled or live_side.enabled:
        _reject("invalid-logic", "组未启用时不允许单独启用某一侧")
    video_live = VideoLiveGroup(enabled=group_enabled, logic=logic, video=video_side, live=live_side)

    content_raw = payload.get("content", {})
    if not isinstance(content_raw, dict):
        _reject("invalid-content", "content 必须是对象")
    unknown_content = set(content_raw) - {"enabled", "days", "min_related", "require_display"}
    if unknown_content:
        _reject("unknown-field", f"content 含未知字段: {sorted(unknown_content)}")
    content_enabled = content_raw.get("enabled", False)
    if not isinstance(content_enabled, bool):
        _reject("invalid-boolean", "content.enabled 必须是布尔值")
    days = content_raw.get("days", 7)
    min_related = content_raw.get("min_related", 4)
    require_display = content_raw.get("require_display", True)
    if not isinstance(days, bool) and isinstance(days, int):
        pass
    else:
        _reject("invalid-number", "content.days 必须是整数")
    if days not in CONTENT_ALLOWED_DAYS:
        _reject(
            "value-out-of-range",
            f"content.days 当前版本仅支持 {CONTENT_ALLOWED_DAYS}（与内容审核库一致）",
        )
    if not isinstance(min_related, int) or isinstance(min_related, bool):
        _reject("invalid-number", "content.min_related 必须是整数")
    if min_related not in CONTENT_ALLOWED_MIN_RELATED:
        _reject(
            "value-out-of-range",
            f"content.min_related 当前版本仅支持 {CONTENT_ALLOWED_MIN_RELATED}（与内容审核库一致）",
        )
    if not isinstance(require_display, bool):
        _reject("invalid-boolean", "content.require_display 必须是布尔值")
    content = ContentGroup(
        enabled=content_enabled,
        days=days,
        min_related=min_related,
        require_display=require_display,
    )

    rule = CustomRule(
        schema_version=SCHEMA_VERSION,
        mode=MODE_CUSTOM,
        product_ids=tuple(product_ids),
        basic=basic,
        video_live=video_live,
        content=content,
    )
    if not rule.any_check_enabled():
        _reject(
            "empty-check-set",
            "不允许只选商品而关闭全部审核项；请至少启用一项检查",
        )
    return rule


def canonical_rule_json(rule: CustomRule) -> str:
    return json.dumps(rule.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def rule_hash(rule: CustomRule) -> str:
    return hashlib.sha256(canonical_rule_json(rule).encode("utf-8")).hexdigest()


def rule_summary_lines(
    rule: CustomRule,
    *,
    product_display: dict[str, str] | None = None,
) -> list[str]:
    """服务端生成的中文摘要；页面实时摘要与此一致。"""
    product_display = product_display or {}
    lines: list[str] = []
    product_labels = [
        f"{product_display.get(pid, '')}({pid})" if product_display.get(pid) else pid
        for pid in rule.product_ids
    ]
    lines.append(f"模式：自定义（本次临时标准，不代表完整 SOP 通过）")
    lines.append(f"商品：{'、'.join(product_labels)}")

    enabled_parts: list[str] = []
    disabled_parts: list[str] = []
    basic_labels = {
        "followers": ("粉丝数", "min", "> {v}"),
        "gmv": ("GMV", "min", "> {v} USD"),
        "units": ("成交件数", "min", "> {v}"),
        "gpm": ("千次曝光成交/GPM", "min", "> {v}"),
        "aov": ("客单价", "range", "{min}–{max} USD"),
        "fulfillment": ("履约率/预计发布率", "min", "> {v}%"),
        "female_pct": ("女性粉丝占比", "min", "> {v}%"),
        "categories": ("类目", "categories", "命中：{values}"),
    }
    for key, (label, shape, template) in basic_labels.items():
        condition = rule.basic[key]
        if not condition.enabled:
            disabled_parts.append(label)
            continue
        if shape == "min":
            enabled_parts.append(label + template.format(v=f"{condition.min_value:g}"))
        elif shape == "range":
            enabled_parts.append(
                label + template.format(min=f"{condition.min_value:g}", max=f"{condition.max_value:g}")
            )
        else:
            enabled_parts.append(label + template.format(values="、".join(condition.categories)))
    lines.append(f"基础条件（全部 AND）：{'；'.join(enabled_parts) or '无'}")
    if disabled_parts:
        lines.append(f"未检查（not_checked）：{'、'.join(disabled_parts)}")

    group = rule.video_live
    if group.enabled:
        def side_text(name: str, side: SideCondition) -> str:
            if not side.enabled:
                return f"{name}未启用"
            parts = [f"GPM>{side.gpm}"]
            if side.avg_views is not None:
                parts.append(f"均播>{side.avg_views:g}")
            if side.engagement is not None:
                parts.append(f"互动>{side.engagement:g}%")
            return f"{name}（{' 且 '.join(parts)}）"
        relation = "任一侧达标" if group.logic == "either" else "两侧均满足"
        lines.append(
            f"视频/直播：启用，{relation}；{side_text('视频', group.video)}；{side_text('直播', group.live)}"
        )
    else:
        lines.append("视频/直播：未检查（not_checked）")

    content = rule.content
    if content.enabled:
        display_note = "，至少 1 条明确展示" if content.require_display else "（不要求展示证据）"
        lines.append(
            f"内容审核：启用，最近 {content.days} 天 ≥ {content.min_related} 条相关带货视频{display_note}"
        )
    else:
        lines.append("内容审核：未执行（风险：不核对近期带货内容）")
    lines.append("指标口径：履约（预计发布率）、GPM、客单价均详情值优先；缺失时回退列表履约率、列表近似 GPM、GMV÷件数")
    return lines


def evaluate_custom_row(
    row: dict[str, Any],
    rule: CustomRule,
    *,
    hero_keys: set[str],
    allowed_product_ids: set[str] | None,
) -> dict[str, Any]:
    """按自定义规则对单行做三态评估；返回逐项结果与总体结论。

    依赖上游已做数值抽取（lib.filters.evaluate_row 的 *_n 字段或等价字段）。
    这里不做写操作，也不做内容审核（内容审核单独跑 review_creator_rows）。
    """
    from .feishu_hero import match_hero  # 避免循环

    checks: list[dict[str, Any]] = []

    def triage(
        key: str,
        label: str,
        value: float | None,
        *,
        test_passed: bool | None = None,
        source: str = "",
        detail: str = "",
    ) -> str:
        status: str
        if value is None:
            status = "needs_review"
            detail = detail or "指标缺失"
        elif test_passed:
            status = "passed"
        else:
            status = "failed"
        checks.append(
            {
                "key": key,
                "label": label,
                "status": status,
                "value": value,
                "source": source,
                "detail": detail,
            }
        )
        return status

    has_failure = False
    has_pending = False

    def absorb(status: str) -> None:
        nonlocal has_failure, has_pending
        if status == "failed":
            has_failure = True
        elif status == "needs_review":
            has_pending = True

    basic = rule.basic
    if basic["followers"].enabled:
        absorb(
            triage(
                "followers",
                "粉丝数",
                row.get("followers_n"),
                test_passed=row.get("followers_n") is not None
                and float(row["followers_n"]) > float(basic["followers"].min_value),
                source="list",
                detail=f"阈值 > {basic['followers'].min_value:g}",
            )
        )
    if basic["gmv"].enabled:
        absorb(
            triage(
                "gmv",
                "GMV",
                row.get("gmv_n"),
                test_passed=row.get("gmv_n") is not None
                and float(row["gmv_n"]) > float(basic["gmv"].min_value),
                source="list",
                detail=f"阈值 > {basic['gmv'].min_value:g} USD",
            )
        )
    if basic["units"].enabled:
        absorb(
            triage(
                "units",
                "成交件数",
                row.get("units_n"),
                test_passed=row.get("units_n") is not None
                and float(row["units_n"]) > float(basic["units"].min_value),
                source="list",
                detail=f"阈值 > {basic['units'].min_value:g}",
            )
        )
    if basic["gpm"].enabled:
        official = row.get("overall_gpm_n")
        gpm_value = official if official is not None else row.get("gpm_proxy_n")
        source = "detail-official" if official is not None else "list-proxy"
        absorb(
            triage(
                "gpm",
                "千次曝光成交/GPM",
                gpm_value,
                test_passed=gpm_value is not None
                and float(gpm_value) > float(basic["gpm"].min_value),
                source=source,
                detail=(
                    f"阈值 > {basic['gpm'].min_value:g}"
                    + ("；列表近似值" if source == "list-proxy" else "")
                ),
            )
        )
    if basic["aov"].enabled:
        detail_aov = row.get("aov_detail_n")
        aov_value = detail_aov if detail_aov is not None else row.get("aov_n")
        absorb(
            triage(
                "aov",
                "客单价",
                aov_value,
                test_passed=aov_value is not None
                and float(basic["aov"].min_value)
                <= float(aov_value)
                <= float(basic["aov"].max_value),
                source="detail" if detail_aov is not None else "derived",
                detail=f"范围 [{basic['aov'].min_value:g}, {basic['aov'].max_value:g}] USD",
            )
        )
    if basic["fulfillment"].enabled:
        est_post = row.get("est_post_rate_n")
        fulfill_value = est_post if est_post is not None else row.get("fulfillment_n")
        source = "detail-est-post-rate" if est_post is not None else "list-fulfillment"
        absorb(
            triage(
                "fulfillment",
                "履约率/预计发布率",
                fulfill_value,
                test_passed=fulfill_value is not None
                and float(fulfill_value) > float(basic["fulfillment"].min_value),
                source=source,
                detail=f"阈值 > {basic['fulfillment'].min_value:g}%",
            )
        )
    if basic["female_pct"].enabled:
        absorb(
            triage(
                "female_pct",
                "女性粉丝占比",
                row.get("female_pct"),
                test_passed=row.get("female_pct") is not None
                and float(row["female_pct"]) > float(basic["female_pct"].min_value),
                source="list",
                detail=f"阈值 > {basic['female_pct'].min_value:g}%",
            )
        )
    if basic["categories"].enabled:
        categories = row.get("categories_n") or []
        matched = [cat for cat in categories if cat in basic["categories"].categories]
        if not categories:
            status = "needs_review"
            detail = "类目缺失"
        elif matched:
            status = "passed"
            detail = f"命中：{'、'.join(matched)}"
        else:
            status = "failed"
            detail = f"类目不匹配：{'、'.join(categories)}"
        checks.append(
            {
                "key": "categories",
                "label": "类目",
                "status": status,
                "value": None,
                "source": "list",
                "detail": detail,
            }
        )
        absorb(status)

    for key, condition in rule.basic.items():
        if condition.enabled:
            continue
        checks.append(
            {
                "key": key,
                "label": {
                    "followers": "粉丝数",
                    "gmv": "GMV",
                    "units": "成交件数",
                    "gpm": "千次曝光成交/GPM",
                    "aov": "客单价",
                    "fulfillment": "履约率/预计发布率",
                    "female_pct": "女性粉丝占比",
                    "categories": "类目",
                }[key],
                "status": "not_checked",
                "value": None,
                "source": "",
                "detail": "本项未启用",
            }
        )

    group = rule.video_live
    video_status = "not_checked"
    live_status = "not_checked"
    video_parts: list[dict[str, Any]] = []
    live_parts: list[dict[str, Any]] = []
    if group.enabled:
        video_side = group.video
        if video_side.enabled:
            sub_statuses: list[str] = []
            for metric_key, label, value, threshold in (
                ("video_gpm", "视频 GPM", row.get("video_gpm_n"), video_side.gpm),
                ("video_avg_views", "视频均播", row.get("avg_video_views_n"), video_side.avg_views),
                ("video_engagement", "视频互动率", row.get("video_engagement_n"), video_side.engagement),
            ):
                sub_statuses.append(
                    triage(
                        metric_key,
                        label,
                        value,
                        test_passed=value is not None and float(value) > float(threshold),
                        source="detail",
                        detail=f"阈值 > {threshold:g}",
                    )
                )
                video_parts.append(checks[-1])
            video_status = (
                "failed" if "failed" in sub_statuses else "needs_review" if "needs_review" in sub_statuses else "passed"
            )
        live_side = group.live
        if live_side.enabled:
            sub_statuses = []
            for metric_key, label, value, threshold in (
                ("live_gpm", "直播 GPM", row.get("live_gpm_n"), live_side.gpm),
                ("live_avg_views", "直播均播", row.get("avg_live_views_n"), live_side.avg_views),
            ):
                sub_statuses.append(
                    triage(
                        metric_key,
                        label,
                        value,
                        test_passed=value is not None and float(value) > float(threshold),
                        source="detail",
                        detail=f"阈值 > {threshold:g}",
                    )
                )
                live_parts.append(checks[-1])
            live_status = (
                "failed" if "failed" in sub_statuses else "needs_review" if "needs_review" in sub_statuses else "passed"
            )
        if group.logic == "either":
            if video_status == "passed" or live_status == "passed":
                group_status = "passed"
            elif video_status == "failed" and live_status == "failed":
                group_status = "failed"
            else:
                group_status = "needs_review"
        else:
            if video_status == "failed" or live_status == "failed":
                group_status = "failed"
            elif video_status == "passed" and live_status == "passed":
                group_status = "passed"
            else:
                group_status = "needs_review"
        checks.append(
            {
                "key": "video_live",
                "label": "视频/直播",
                "status": group_status,
                "value": None,
                "source": "detail",
                "detail": f"组逻辑={group.logic}；视频={video_status}；直播={live_status}",
            }
        )
        absorb(group_status)
    else:
        checks.append(
            {
                "key": "video_live",
                "label": "视频/直播",
                "status": "not_checked",
                "value": None,
                "source": "",
                "detail": "本组未启用",
            }
        )

    # 主推款与当前跟进款（安全拦截；不属于自定义审核项）
    safety_blocks: list[dict[str, str]] = []
    hero_ok: bool | None = True
    hero_reason = ""
    if not hero_keys:
        hero_ok = False
        hero_reason = "no-hero-table"
    else:
        hero_ok, hero_reason = match_hero(row, hero_keys)
    if hero_ok is False:
        safety_blocks.append({"code": "not-hero", "detail": f"非主推款({hero_reason})"})
    active_reason = None
    if allowed_product_ids:
        from .filters import reject_if_not_active_hero_product
        active_reason = reject_if_not_active_hero_product(
            row.get("product_id"), allowed_product_ids
        )
    if active_reason:
        safety_blocks.append({"code": "not-active-product", "detail": active_reason})
    if str(row.get("apply_id") or "").strip() == "":
        safety_blocks.append({"code": "missing-apply-id", "detail": "缺少申请 ID"})
    if str(row.get("creator_id") or "").strip() == "":
        safety_blocks.append({"code": "missing-creator-id", "detail": "缺少达人 ID"})
    if str(row.get("product_id") or "").strip() == "":
        safety_blocks.append({"code": "missing-product-id", "detail": "缺少商品 ID"})
    if row.get("can_be_approved") is False:
        safety_blocks.append({"code": "cannot-approve", "detail": "can_be_approved=false"})

    if has_failure:
        overall = "failed"
    elif has_pending:
        overall = "needs_review"
    else:
        overall = "passed"
    blocked = bool(safety_blocks)
    eligible = overall == "passed" and not blocked

    return {
        "checks": checks,
        "video_parts": video_parts,
        "live_parts": live_parts,
        "overall": overall,
        "safety_blocks": safety_blocks,
        "blocked": blocked,
        "custom_eligible": eligible,
    }


def verify_preview_envelope(
    preview: dict[str, Any],
    *,
    expected_store_id: str,
    expected_rule_hash: str,
) -> CustomRule:
    """执行/核对时验证预览信封：schema、店铺、规则 hash 与新鲜度。"""
    if not isinstance(preview, dict):
        _reject("invalid-preview", "预览文件不是有效 JSON 对象")
    if preview.get("schema_version") != SCHEMA_VERSION:
        _reject("unsupported-schema", f"仅支持规则 schema 版本 {SCHEMA_VERSION}")
    if preview.get("mode") != MODE_CUSTOM:
        _reject("invalid-mode", "预览不是自定义模式产物")
    if str(preview.get("store_id") or "") != str(expected_store_id):
        _reject("store-mismatch", "预览店铺与执行店铺不一致")
    if preview.get("rule_hash") != expected_rule_hash:
        _reject("rule-hash-mismatch", "规则快照 hash 不一致，禁止执行")
    if not preview.get("integrity_complete"):
        _reject("preview-incomplete", "预览采集不完整，禁止执行；请重新筛查")
    finished_at = _parse_iso(preview.get("finished_at"))
    if finished_at is None:
        _reject("preview-stale", "预览缺少完成时间，禁止执行；请重新筛查")
    elapsed = (datetime.now(timezone.utc) - finished_at).total_seconds()
    if elapsed > PREVIEW_FRESHNESS_SECONDS:
        _reject(
            "preview-stale",
            f"预览已超过 {PREVIEW_FRESHNESS_SECONDS // 3600} 小时，禁止执行；请重新筛查",
        )
    rule_payload = preview.get("rule")
    if not isinstance(rule_payload, dict):
        _reject("invalid-preview", "预览缺少规则快照")
    rule = validate_custom_rule(
        rule_payload,
        allowed_product_ids=set(preview.get("allowed_product_ids") or []),
    )
    if rule_hash(rule) != expected_rule_hash:
        _reject("rule-hash-mismatch", "规则快照 hash 与内容不一致，禁止执行")
    return rule


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def verify_row_evidence(
    row: dict[str, Any],
    rule: CustomRule,
    *,
    hero_keys: set[str],
    allowed_product_ids: set[str] | None,
) -> tuple[bool, str]:
    """批准边界：逐项验证自定义证据，不做任何写操作。

    内容审查启用时必须带合法内容证据（与正式链路同一 validate_content_review 口径）；
    内容审查关闭时只接受规则快照声明关闭 + 统一 not_checked 标记，禁止前端布尔量绕门禁。
    """
    if not isinstance(row, dict):
        return False, "row-not-object"
    if row.get("custom_eligible") is not True:
        return False, "row-not-custom-eligible"
    evaluation = evaluate_custom_row(
        row,
        rule,
        hero_keys=hero_keys,
        allowed_product_ids=allowed_product_ids,
    )
    if not evaluation["custom_eligible"]:
        return False, "row-evaluation-changed"
    if evaluation["overall"] != str(row.get("custom_overall") or ""):
        return False, "row-conclusion-mismatch"
    stored_checks = row.get("custom_checks")
    if not isinstance(stored_checks, list) or len(stored_checks) != len(evaluation["checks"]):
        return False, "row-checks-mismatch"

    content = rule.content
    if content.enabled:
        from .creator_video_review import validate_content_review

        if row.get("sales_eligible") is not True:
            return False, "custom-row-sales-eligible-missing"
        valid, reason = validate_content_review(row)
        if not valid:
            return False, f"content-proof-invalid:{reason}"
        return True, "custom-evidence-valid"
    reason = str(row.get("content_review_reason") or "").strip()
    if row.get("content_review_status") not in (None, "", "not_run") or reason not in (
        "",
        CONTENT_DISABLED_REASON,
    ):
        return False, "content-disabled-state-invalid"
    return True, "custom-evidence-valid-content-not-checked"
