"""Store resolution and persistence for the read-only assistant."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from assistant.database.models import Store


class StoreService:
    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory

    def resolve_unique_running_store(self) -> dict:
        from lib.zclaw import list_running_stores

        try:
            stores = list_running_stores()
        except Exception as error:
            return {"ok": False, "error": "running-query-failed", "detail": str(error)}
        if len(stores) != 1:
            return {"ok": False, "error": "running-not-unique", "stores": stores}
        return {"ok": True, "store": stores[0]}

    def upsert_store(self, store: dict) -> Store:
        ziniao_store_id = str(store.get("storeId") or "").strip()
        if not ziniao_store_id:
            raise ValueError("missing-ziniao-store-id")
        with self.session_factory() as session:
            model = session.scalar(
                select(Store).where(Store.ziniao_store_id == ziniao_store_id)
            )
            if model is None:
                model = Store(
                    ziniao_store_id=ziniao_store_id,
                    store_name=str(store.get("storeName") or ""),
                )
                session.add(model)
            model.store_name = str(store.get("storeName") or model.store_name)
            model.shop_id = str(store.get("shopId") or model.shop_id or "")
            model.shop_region = str(store.get("shopRegion") or model.shop_region or "US")
            model.last_seen_at = datetime.now(timezone.utc)
            session.commit()
            session.refresh(model)
            session.expunge(model)
            return model
