#!/usr/bin/env python3
"""Consume a server-owned cleanup batch; never accept CSV, store or shell input."""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from lib.app_log import configure_logging
from lib.target_plan_cleanup import (
    TargetCleanupError,
    create_snapshot,
    execute_snapshot,
    persist_preview,
)

logger = logging.getLogger(__name__)


def parse_identifier(value: str) -> str:
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError("noncanonical identifier")
    except (ValueError, TypeError, AttributeError) as error:
        raise argparse.ArgumentTypeError("A server-generated UUID is required.") from error
    return value


def run_batch(session_factory, *, batch_id: str, job_id: str, mode: str,
              execute: bool = False, yes: bool = False) -> int:
    from assistant.services import target_cleanup_service as service

    if mode not in {"preview", "execute"}:
        raise TargetCleanupError("invalid-mode")
    if mode == "execute" and not (execute is True and yes is True):
        raise TargetCleanupError("execute-confirmation-required")
    request = service.begin_script(session_factory, batch_id, job_id, mode)
    try:
        return _run_claimed_batch(session_factory, request, execute=execute, yes=yes)
    except Exception as error:
        # A rejected claim must not close another process's active execution.
        service.fail_batch(session_factory, batch_id, mode=mode, error=str(error))
        raise


def _run_claimed_batch(session_factory, request: dict, *, execute: bool, yes: bool) -> int:
    from assistant.services import target_cleanup_service as service
    from lib.target_invitation_dom import scan_older_invitations
    from lib.target_invitation_navigation import navigate_from_seller_home_to_ongoing

    batch_id = request["batch_id"]
    mode = request["mode"]
    frozen = request["frozen"]
    directory = service.cleanup_directory()
    navigation = navigate_from_seller_home_to_ongoing(frozen["store_id"])
    destination = navigation.get("destination") or {}
    if destination.get("shop_id") != frozen["shop_id"]:
        raise TargetCleanupError("shop-changed")
    if mode == "preview":
        scan = scan_older_invitations(frozen["store_id"], cutoff=date.fromisoformat(frozen["cutoff"]))
        snapshot = create_snapshot(frozen, scan)
        persist_preview(snapshot, directory)
        service.finish_preview(session_factory, batch_id, snapshot)
        logger.info("Preview ended: %s candidates; complete=%s; reason=%s",
                    len(snapshot["candidate_ids"]), snapshot["scan_complete"], snapshot["stop_reason"])
        return 0 if snapshot["scan_complete"] else 1
    snapshot = request["snapshot"]
    report = execute_snapshot(
        snapshot, directory=directory, snapshot_sha256=request["snapshot_sha256"], execute=execute, yes=yes,
        before_attempt=lambda invitation_id: service.mark_item_attempting(session_factory, batch_id, invitation_id),
        on_result=lambda item: service.save_item_result(session_factory, batch_id, item),
        validate_identity=lambda: service.validate_script_identity(session_factory, batch_id, request["job_id"]),
    )
    service.finish_execution(session_factory, batch_id, status=report["status"])
    logger.info("Execution ended: %s; %s. Submitted is not platform-final confirmation.",
                report["status"], report["counts"])
    return 0 if report["status"] == "completed" else 1


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Server-owned 2/4-month target-plan cleanup")
    parser.add_argument("--mode", choices=("preview", "execute"), required=True)
    parser.add_argument("--batch-id", type=parse_identifier, required=True)
    parser.add_argument("--job-id", type=parse_identifier, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    options = parser.parse_args(arguments)
    if options.mode == "execute" and not (options.execute and options.yes):
        parser.error("execute mode requires both --execute and --yes")
    if options.mode == "preview" and (options.execute or options.yes):
        parser.error("preview mode does not accept execution flags")

    # The gate above must run before opening the user database or touching a page.
    from sqlalchemy.orm import sessionmaker

    from assistant.database.engine import create_database_engine
    from assistant.paths import database_path, ensure_user_dirs

    configure_logging(log_path=ensure_user_dirs() / "logs" / f"target_cleanup_{options.batch_id}.log")
    database = database_path()
    if not database.is_file():
        logger.error("The server-owned batch database is missing; no platform action was performed.")
        return 2
    engine = create_database_engine(database)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        return run_batch(session_factory, batch_id=options.batch_id, job_id=options.job_id,
                         mode=options.mode, execute=options.execute, yes=options.yes)
    except Exception:
        logger.exception("Cleanup stopped; no automatic write retry.")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
