"""Unauthenticated liveness endpoint without operational details."""
from __future__ import annotations

from fastapi import APIRouter

from assistant.settings import APP_NAME


router = APIRouter()


@router.get("/api/health")
def health() -> dict:
    from assistant.app import application_version

    return {"ok": True, "app": APP_NAME, "version": application_version()}
