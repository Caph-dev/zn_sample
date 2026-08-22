"""Read-only running-store diagnostics."""
from __future__ import annotations

from fastapi import APIRouter


router = APIRouter()


def running_store_summary() -> dict:
    from lib.zclaw import list_running_stores

    try:
        stores = list_running_stores()
    except Exception as error:
        return {"ok": False, "error": "running-query-failed", "detail": str(error)}
    safe_stores = [
        {
            "storeId": str(store.get("storeId") or ""),
            "storeName": str(store.get("storeName") or ""),
        }
        for store in stores
    ]
    if len(safe_stores) != 1:
        return {"ok": False, "error": "running-not-unique", "stores": safe_stores}
    return {"ok": True, "store": safe_stores[0], "stores": safe_stores}


@router.get("/api/stores")
def stores() -> dict:
    return running_store_summary()
