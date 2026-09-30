"""Unauthenticated liveness endpoint without operational details."""
from __future__ import annotations

from fastapi import APIRouter, Request

from assistant.settings import APP_NAME


router = APIRouter()


@router.get("/api/health")
def health(request: Request) -> dict:
    from assistant.app import application_version

    startup_version = getattr(request.app.state, "release_version", None)
    payload = {"ok": True, "app": APP_NAME, "version": startup_version or application_version()}
    instance_id = getattr(request.app.state, "release_instance_id", None)
    if instance_id is not None:
        import os
        payload.update(instance_id=instance_id, pid=os.getpid(),
                       target=request.app.state.release_target)
    return payload
