"""Fixed product SKU policy for the custom auto-approval chain only."""
from __future__ import annotations

from typing import Any

B005_PRODUCT_ID = "1732414717062320994"
B005_SKU_CHECK_KEY = "b005_sku"
B005_SKU_RULE_DESCRIPTION = (
    "B005 SKU 不得包含 6PCS（不区分大小写）；SKU 缺失或无法读取时待复核，"
    "不予批准。其他商品不受此限制。"
)


def evaluate_product_sku(row: dict[str, Any]) -> dict[str, Any] | None:
    """Use only this application's sku_desc, never its title or a sibling SKU."""
    if str(row.get("product_id") or "") != B005_PRODUCT_ID:
        return None
    raw_description = row.get("sku_desc")
    description = raw_description.strip() if isinstance(raw_description, str) else ""
    if not description or description in {"-", "—", "--"}:
        status = "needs_review"
        detail = "未取得本条 B005 申请的 SKU 文本，禁止批准；请重新采集。"
    elif "6PCS" in description.upper():
        status = "failed"
        detail = "本条 B005 申请的 SKU 包含 6PCS，禁止批准。"
    else:
        status = "passed"
        detail = "本条 B005 申请的 SKU 不含 6PCS；仍须满足其余审核条件。"
    return {
        "key": B005_SKU_CHECK_KEY,
        "label": "B005 SKU 限制",
        "status": status,
        "value": description or None,
        "source": "application.sku_desc",
        "detail": detail,
    }


def has_product_sku_evidence(row: dict[str, Any], checks: Any) -> bool:
    """Reject old or inconsistent B005 evidence even if stored eligible is true."""
    expected = evaluate_product_sku(row)
    if expected is None:
        return True
    if expected["status"] != "passed" or not isinstance(checks, list):
        return False
    matching = [check for check in checks if isinstance(check, dict) and check.get("key") == B005_SKU_CHECK_KEY]
    return len(matching) == 1 and all(
        matching[0].get(key) == expected[key]
        for key in ("status", "value", "source")
    )
