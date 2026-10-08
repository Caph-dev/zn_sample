#!/usr/bin/env python3
"""Preview local D+15 tasks, or explicitly write Feishu cooperation status.

No browser, store discovery, migrations, worker, messages or platform writes.
Remote writes require all three switches and a positive per-run limit.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sqlalchemy import inspect, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from assistant.database.engine import create_database_engine  # noqa: E402
from assistant.database.models import (  # noqa: E402
    ContentEvidence, FollowupTask, Job, SampleCase, Shipment, Store,
)
from assistant.domain.timeutil import beijing_now  # noqa: E402
from assistant.jobs.progress import update_progress  # noqa: E402
from assistant.paths import database_path, user_exports_dir  # noqa: E402
from assistant.services.export_service import serialize_csv_cell_value  # noqa: E402
from assistant.services.followup_service import FollowupService  # noqa: E402
from lib.app_config import load_dotenv  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402

logger = logging.getLogger(__name__)
JOB_TYPE = "followup_unfulfilled_write"
REPORT_FIELDS = (
    "task_id", "store_id", "store_name", "creator_name", "sample_product",
    "record_id", "delivered_on", "days_since_delivery", "scheduled_for",
    "result", "reason",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store-id", default="", help="Explicit local Zinao store ID")
    parser.add_argument("--task-id", type=int, default=None)
    parser.add_argument("--job-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--write-feishu", action="store_true")
    parser.add_argument("--execute-limit", type=int, default=1)
    parser.add_argument("--verbose", action="store_true")
    return parser


def validate_arguments(arguments: argparse.Namespace) -> None:
    switches = (arguments.execute, arguments.yes, arguments.write_feishu)
    if any(switches) and not all(switches):
        raise ValueError("Remote writes require --execute --yes --write-feishu together")
    if arguments.execute_limit <= 0:
        raise ValueError("--execute-limit must be a positive integer; 0 is not unlimited")
    if arguments.task_id is not None and arguments.task_id <= 0:
        raise ValueError("--task-id must be a positive integer")
    if arguments.job_id:
        if not all(switches) or arguments.store_id or arguments.task_id is not None:
            raise ValueError("Internal jobs require write gates and server-owned scope")
    elif not str(arguments.store_id).strip():
        raise ValueError("--store-id is required; no live store or default fallback")


def open_existing_database():
    """Refuse an absent/old database without bootstrapping user state."""
    path = database_path()
    if not path.is_file():
        raise ValueError("Local database missing: prepare the console database first")
    engine = create_database_engine(path)
    try:
        schema = inspect(engine)
        for model in (Store, SampleCase, Shipment, FollowupTask, ContentEvidence, Job):
            if not schema.has_table(model.__tablename__):
                raise ValueError("Local database needs preparation/migration in the console")
            existing_columns = {
                column["name"] for column in schema.get_columns(model.__tablename__)
            }
            if not set(model.__table__.columns.keys()) <= existing_columns:
                raise ValueError("Local database needs preparation/migration in the console")
    except Exception:
        engine.dispose()
        raise
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def load_job_request(session_factory, job_id: str, *, execute_limit: int) -> dict:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.job_type != JOB_TYPE or job.status != "running":
            raise ValueError("Internal job is not a running D+15 write job")
        try:
            payload = json.loads(job.result_summary)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("Invalid server job request") from error
        if not isinstance(payload, dict):
            raise ValueError("Invalid server job request")
        task_ids = payload.get("task_ids")
        store_id = payload.get("store_id")
        server_limit = payload.get("execute_limit")
        if (
            not isinstance(task_ids, list) or not task_ids
            or any(type(task_id) is not int or task_id <= 0 for task_id in task_ids)
            or len(set(task_ids)) != len(task_ids)
            or not isinstance(store_id, str) or not store_id.strip()
            or type(server_limit) is not int or server_limit <= 0
            or server_limit != execute_limit or len(task_ids) > server_limit
            or job.store_id != store_id
            # A checkpoint is not a fresh execution request. Never replay it.
            or payload.get("json_path") or payload.get("backup_path")
        ):
            raise ValueError("Invalid or already-started server job scope")
        stores = session.execute(
            select(FollowupTask.id, Store.ziniao_store_id)
            .join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
            .join(Store, SampleCase.store_id == Store.id)
            .where(FollowupTask.id.in_(task_ids))
        ).all()
        if len(stores) != len(task_ids) or any(row_store != store_id for _, row_store in stores):
            raise ValueError("Server job scope contains missing or cross-store tasks")
        return {"task_ids": task_ids, "store_id": store_id, "execute_limit": server_limit}


def report_paths(run_id: str) -> dict[str, Path]:
    if not run_id or any(
        not character.isascii() or not (character.isalnum() or character in "_-")
        for character in run_id
    ):
        raise ValueError("Invalid report run ID")
    directory = user_exports_dir() / "followup_unfulfilled"
    return {
        "json_path": directory / f"{run_id}_result.json",
        "csv_path": directory / f"{run_id}_result.csv",
        "backup_path": directory / f"{run_id}_pre_execute.json",
    }


def write_atomic_json(destination: Path, payload: dict) -> None:
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, delete=False,
        ) as output:
            temporary_path = Path(output.name)
            json.dump(payload, output, ensure_ascii=False, indent=2)
            output.write("\n")
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def write_pre_execute_backup(destination: Path, payload: dict) -> None:
    # Exclusive creation also prevents silently re-running the same internal job.
    with destination.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")


def persist_job_summary(summary: dict, session_factory, *, job_id: str) -> None:
    if not job_id:
        return
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.status != "running" or job.job_type != JOB_TYPE:
            raise ValueError("Job ownership lost while saving D+15 results")
        job.result_summary = json.dumps(summary, ensure_ascii=False, sort_keys=True)
        session.commit()


def save_report(summary: dict, session_factory, *, job_id: str = "") -> None:
    summary["counts"] = dict(Counter(row["result"] for row in summary["rows"]))
    write_atomic_json(Path(summary["json_path"]), summary)
    destination = Path(summary["csv_path"])
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8-sig", newline="",
            dir=destination.parent, delete=False,
        ) as output:
            temporary_path = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(
                {field: serialize_csv_cell_value(row.get(field, "")) for field in REPORT_FIELDS}
                for row in summary["rows"]
            )
        temporary_path.replace(destination)
    except Exception:
        # A previous CSV cannot describe the latest JSON checkpoint.
        summary["csv_path"] = ""
        write_atomic_json(Path(summary["json_path"]), summary)
        raise
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    persist_job_summary(summary, session_factory, job_id=job_id)


def record_artifact_failure(summary: dict, session_factory, *, job_id: str, reason: str) -> None:
    """Keep known outcomes in the job even when the filesystem stops accepting writes."""
    summary["stopped_reason"] = reason
    summary["counts"] = dict(Counter(row["result"] for row in summary["rows"]))
    # Do not advertise stale files as the current result. Existing checkpoints remain on disk.
    summary["json_path"] = ""
    summary["csv_path"] = ""
    try:
        persist_job_summary(summary, session_factory, job_id=job_id)
    except Exception as error:
        logger.error("Could not preserve latest job results; manual local-state review required: %s", error)


def run_batch(
    session_factory, *, store_id: str, execute: bool, execute_limit: int = 1,
    task_id: int | None = None, task_ids: list[int] | None = None, job_id: str = "",
) -> tuple[int, dict]:
    """Freeze selection once; every write still rechecks the current local day."""
    if type(execute_limit) is not int or execute_limit <= 0:
        raise ValueError("Execution limit must be positive")
    service = FollowupService(session_factory)
    selected_at = beijing_now()
    candidates = service.list_unfulfilled_candidates(
        store_id, today=selected_at.date(), task_id=task_id,
    )
    if task_ids is not None:
        candidates_by_id = {candidate["task_id"]: candidate for candidate in candidates}
        rows = [
            candidates_by_id.get(selected_id, {
                "task_id": selected_id, "store_id": store_id, "ineligible": True,
            }) for selected_id in task_ids
        ]
    else:
        rows = candidates[:execute_limit] if execute else candidates
    if execute and len(rows) > execute_limit:
        raise ValueError("Frozen scope exceeds execution limit")
    run_id = job_id or str(uuid.uuid4())
    paths = report_paths(run_id)
    paths["json_path"].parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "mode": JOB_TYPE, "run_id": run_id, "store_id": store_id,
        "task_ids": [row["task_id"] for row in rows], "execute_limit": execute_limit,
        "selected_at": selected_at.isoformat(), "execute": execute,
        "json_path": str(paths["json_path"]), "csv_path": str(paths["csv_path"]),
        "backup_path": str(paths["backup_path"]) if execute else "",
        "rows": [
            {**row, "result": "not-processed" if execute else "planned", "reason": ""}
            for row in rows
        ],
        "counts": {}, "stopped_reason": "",
    }
    logger.info(
        "Before writing, synchronize logistics and generate today's tasks. "
        "Local status is not a fresh platform verification."
    )
    try:
        if execute:
            write_pre_execute_backup(paths["backup_path"], summary)
        save_report(summary, session_factory, job_id=job_id)
    except Exception as error:
        record_artifact_failure(
            summary, session_factory, job_id=job_id, reason="artifact-failed-before-write",
        )
        logger.error("Backup/checkpoint failed; no remote calls: %s", error)
        return 1, summary
    if not execute:
        logger.info(
            "Local preview: %d candidates. JSON: %s CSV: %s",
            len(rows), summary["json_path"], summary["csv_path"],
        )
        return 0, summary

    failed = False
    for row_index, row in enumerate(summary["rows"]):
        if row.get("ineligible"):
            outcome = {"result": "skipped", "reason": "no-longer-current-candidate"}
        else:
            try:
                outcome = service.mark_unfulfilled(
                    row["task_id"], write_feishu=True, expected_store_id=store_id,
                )
            except Exception as error:
                # No inference about whether a remote write happened on an exception.
                outcome = {"result": "write-unknown", "reason": str(error)}
        row["result"] = outcome.get("result") or "needs-review"
        row["reason"] = str(outcome.get("reason") or "")
        feishu_result = outcome.get("feishu") or {}
        row["record_id"] = (
            outcome.get("record_id") or feishu_result.get("record_id") or row.get("record_id", "")
        )
        if row["result"] in {"needs-review", "write-unknown"}:
            failed = True
        if row["result"] == "write-unknown":
            summary["stopped_reason"] = "write-unknown"
            for remaining_row in summary["rows"][row_index + 1:]:
                remaining_row["reason"] = "stopped-after-unknown-write"
        try:
            save_report(summary, session_factory, job_id=job_id)
            if job_id:
                update_progress(
                    session_factory, job_id, current=row_index + 1, total=len(rows),
                    message=f"D+15 task {row['task_id']}: {row['result']}",
                )
        except Exception as error:
            record_artifact_failure(
                summary, session_factory, job_id=job_id, reason="artifact-failed-after-row",
            )
            logger.error("Result persistence failed; stopping subsequent writes: %s", error)
            return 1, summary
        logger.info("D+15 task %s: %s %s", row["task_id"], row["result"], row["reason"])
        if summary["stopped_reason"]:
            break
    logger.info("Batch results: JSON %s CSV %s", summary["json_path"], summary["csv_path"])
    return (1 if failed else 0), summary


def run(arguments: argparse.Namespace, *, session_factory=None) -> int:
    engine = None
    try:
        validate_arguments(arguments)
        load_dotenv()
        configure_logging(verbose=bool(arguments.verbose))
        if session_factory is None:
            engine, session_factory = open_existing_database()
        request = (
            load_job_request(session_factory, arguments.job_id, execute_limit=arguments.execute_limit)
            if arguments.job_id else None
        )
        exit_code, _summary = run_batch(
            session_factory, store_id=request["store_id"] if request else str(arguments.store_id).strip(),
            execute=arguments.execute, execute_limit=arguments.execute_limit,
            task_id=arguments.task_id, task_ids=request["task_ids"] if request else None,
            job_id=arguments.job_id,
        )
        return exit_code
    except ValueError as error:
        logger.error("D+15 input/preparation error: %s", error)
        return 2
    except Exception as error:
        logger.error("D+15 batch stopped; do not automatically retry uncertain writes: %s", error)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


def main() -> int:
    return run(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
