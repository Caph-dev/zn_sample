"""Attachment resolution: only B005 ships an image (讲解图); other products get text only."""
from __future__ import annotations

from scripts.lib.filters import ACTIVE_HERO_PRODUCT_ID


FOLLOWUP_IMAGE_PRODUCT_ID = ACTIVE_HERO_PRODUCT_ID
FOLLOWUP_IMAGE_PATH = "样品申请筛查sop/图片和附件/2-查看到货+达人跟进-b05.png"


def resolve_followup_attachment(
    *,
    product_id: str | None,
    resolved_sku: str | None = None,
) -> dict:
    """Resolve the approved image by exact product ID, never by SKU text."""
    del resolved_sku
    if str(product_id).strip() == FOLLOWUP_IMAGE_PRODUCT_ID:
        return {
            "matched": True,
            "sku_key": "b005",
            "path": FOLLOWUP_IMAGE_PATH,
        }
    return {"matched": False, "sku_key": "", "path": ""}
