#!/usr/bin/env python3
"""SOP 2 达人跟进：按本地到期任务打开会话并可选发送跟进私信。

默认只预演（打开会话、核对身份、打印话术），不发送。
真实发送须 --execute --yes；--execute-limit 默认 1。
B005 刚到货任务在文本确认发送后，走页面内 IM SDK 追加配图；
SDK Provider 不可用时拒绝配图，不回退 DOM 上传。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lib.app_config import load_dotenv  # noqa: E402
from lib.app_log import configure_logging  # noqa: E402
from lib.console import set_verbose  # noqa: E402
from lib.im_api import (  # noqa: E402
    send_direct_message,
    thread_contains_message_predicate,
)
from lib.zclaw import resolve_store_id  # noqa: E402

logger = logging.getLogger(__name__)

DEFAULT_TEST_STORE_ID = "27437742526069"
DEFAULT_EXECUTE_LIMIT = 1
SENDABLE_STAGES = ("arrival", "day_3", "day_7", "content_found")
MESSAGE_STAGES = ("arrival", "day_3", "day_7")
DEFAULT_CLAIM_STALE_MINUTES = 30


def select_sendable_tasks(
    session,
    *,
    today,
    stage: str = "",
    creator_id: str = "",
    creator_name: str = "",
    task_id: int | None = None,
    limit: int = DEFAULT_EXECUTE_LIMIT,
) -> list[dict[str, Any]]:
    """Select due follow-up tasks that may be sent now (read-only query)."""
    from sqlalchemy import and_, func, or_, select

    from assistant.database.models import FollowupTask, SampleCase, Store
    from assistant.domain.followup_labels import COMPLETED_RESULTS, SENDING_RESULT
    from assistant.domain.followup_stage import (
        ACTION_KIND_ACKNOWLEDGE_CONTENT,
        ACTION_KIND_SEND_MESSAGE,
    )
    from assistant.domain.platform_status import PLATFORM_STATUS_PROCESSING
    from assistant.domain.policies import message_send_idempotency_key

    query = (
        select(FollowupTask, SampleCase, Store)
        .join(SampleCase, FollowupTask.sample_case_id == SampleCase.id)
        .join(Store, SampleCase.store_id == Store.id)
        .where(
            FollowupTask.status == "pending",
            FollowupTask.sent_at.is_(None),
            ~FollowupTask.send_result.in_(COMPLETED_RESULTS),
            FollowupTask.send_result != SENDING_RESULT,
            FollowupTask.requires_manual_confirmation.is_(False),
            FollowupTask.scheduled_for.is_not(None),
            FollowupTask.scheduled_for <= today,
            FollowupTask.template_key != "",
            FollowupTask.message_preview != "",
            FollowupTask.action_kind.in_(
                (ACTION_KIND_SEND_MESSAGE, ACTION_KIND_ACKNOWLEDGE_CONTENT)
            ),
        )
    )
    if stage:
        query = query.where(FollowupTask.stage == stage)
    else:
        query = query.where(FollowupTask.stage.in_(SENDABLE_STAGES))
    query = query.where(
        or_(
            and_(
                FollowupTask.stage.in_(MESSAGE_STAGES),
                SampleCase.platform_status == PLATFORM_STATUS_PROCESSING,
                SampleCase.platform_status_stale.is_(False),
            ),
            FollowupTask.stage == "content_found",
        )
    )
    if creator_id:
        query = query.where(SampleCase.creator_id == str(creator_id))
    if creator_name:
        query = query.where(
            func.lower(SampleCase.creator_name) == str(creator_name).lower()
        )
    if task_id is not None:
        query = query.where(FollowupTask.id == int(task_id))
    query = query.order_by(FollowupTask.scheduled_for, FollowupTask.id).limit(
        max(1, int(limit))
    )

    rows: list[dict[str, Any]] = []
    for task, sample_case, store in session.execute(query).all():
        rows.append(
            {
                "task_id": task.id,
                "stage": task.stage,
                "action_kind": task.action_kind,
                "scheduled_for": (
                    task.scheduled_for.isoformat() if task.scheduled_for else ""
                ),
                "creator_id": str(sample_case.creator_id or ""),
                "creator_name": str(sample_case.creator_name or ""),
                "product_id": str(sample_case.product_id or ""),
                "store_ziniao_id": str(store.ziniao_store_id or ""),
                "feishu_record_id": str(sample_case.feishu_record_id or ""),
                "message": str(task.message_preview or ""),
                "attachment_key": str(task.attachment_key or ""),
                "language": str(task.language or ""),
                "creator_type": str(task.creator_type or ""),
                "idempotency_key": message_send_idempotency_key(
                    str(store.ziniao_store_id or ""),
                    str(sample_case.creator_id or ""),
                    str(sample_case.product_id or ""),
                    task.stage,
                    task.scheduled_for,
                ),
            }
        )
    return rows


def write_pre_execute_backup(
    rows: list[dict[str, Any]],
    *,
    exports_directory: Path | None = None,
) -> Path:
    """Write the pre-send snapshot required by the follow-up send discipline."""
    from assistant.paths import user_data_dir

    directory = exports_directory or (user_data_dir() / "exports")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = directory / f"followup_send_{stamp}_pre_execute.json"
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def resolve_attachment_path(row: dict[str, Any]) -> Path | None:
    key = str(row.get("attachment_key") or "").strip()
    if not key:
        return None
    path = Path(key)
    if not path.is_absolute():
        path = ROOT / path
    return path if path.is_file() else None


def find_running_jobs(
    session_factory,
    *,
    ignore_job_id: str = "",
) -> list[dict[str, Any]]:
    """Return pending/running jobs that occupy the store page (mutex guard)."""
    from sqlalchemy import select

    from assistant.database.models import Job
    from assistant.jobs.registry import ZINIAO_JOB_TYPES

    blocking_types = tuple(ZINIAO_JOB_TYPES | {"followup_generate"})
    with session_factory() as session:
        jobs = session.scalars(
            select(Job)
            .where(
                Job.status.in_(("pending", "running")),
                Job.job_type.in_(blocking_types),
                Job.id != str(ignore_job_id or ""),
            )
            .order_by(Job.created_at.desc())
        ).all()
        return [
            {"id": job.id, "job_type": job.job_type, "status": job.status}
            for job in jobs
        ]


def claim_task(session_factory, task_id: int) -> bool:
    """Atomically claim one task for sending.

    A single conditional UPDATE flips ``send_result`` to ``sending``; only one
    runner can win, and any later runner skips the task instead of double
    sending. The claim is replaced by the final result after the send.
    """
    from sqlalchemy import update

    from assistant.database.models import FollowupTask
    from assistant.domain.followup_labels import COMPLETED_RESULTS, SENDING_RESULT

    with session_factory() as session:
        result = session.execute(
            update(FollowupTask)
            .where(
                FollowupTask.id == int(task_id),
                FollowupTask.status == "pending",
                FollowupTask.sent_at.is_(None),
                ~FollowupTask.send_result.in_(COMPLETED_RESULTS),
                FollowupTask.send_result != SENDING_RESULT,
            )
            .values(send_result=SENDING_RESULT)
        )
        session.commit()
        return int(result.rowcount or 0) == 1


def find_stale_claims(session_factory, *, stale_minutes: int) -> list[dict[str, Any]]:
    """Return ``sending`` claims older than the window (crashed runs)."""
    from datetime import timedelta, timezone

    from sqlalchemy import select

    from assistant.database.models import FollowupTask
    from assistant.domain.followup_labels import SENDING_RESULT

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        minutes=max(1, int(stale_minutes))
    )
    stale: list[dict[str, Any]] = []
    with session_factory() as session:
        tasks = session.scalars(
            select(FollowupTask).where(FollowupTask.send_result == SENDING_RESULT)
        ).all()
        for task in tasks:
            updated_at = task.updated_at
            if updated_at is None:
                continue
            if updated_at.tzinfo is not None:
                updated_at = updated_at.replace(tzinfo=None)
            if updated_at < cutoff:
                stale.append(
                    {
                        "task_id": task.id,
                        "stage": task.stage,
                        "updated_at": updated_at.isoformat(sep=" ", timespec="seconds"),
                    }
                )
    return stale


def release_stale_claims(
    session_factory,
    *,
    stale_minutes: int,
    apply: bool,
) -> list[dict[str, Any]]:
    """Move stale claims to review so they are never auto-resent."""
    from sqlalchemy import update

    from assistant.database.models import FollowupTask
    from assistant.domain.followup_labels import SEND_UNKNOWN_RESULT, SENDING_RESULT

    stale = find_stale_claims(session_factory, stale_minutes=stale_minutes)
    if not apply or not stale:
        return stale
    with session_factory() as session:
        for item in stale:
            session.execute(
                update(FollowupTask)
                .where(
                    FollowupTask.id == int(item["task_id"]),
                    FollowupTask.send_result == SENDING_RESULT,
                )
                .values(
                    send_result=SEND_UNKNOWN_RESULT,
                    status="needs_review",
                    review_reason="send_interrupted",
                    requires_manual_confirmation=True,
                    last_error="上次发送未完成（进程中断）；请先人工核对会话",
                )
            )
        session.commit()
    return stale


def record_send_result(session_factory, row: dict[str, Any], result: dict[str, Any]) -> None:
    """Persist the send outcome on the task; never marks an unknown result as sent."""
    from assistant.database.models import FollowupTask
    from assistant.domain.followup_labels import SENDING_RESULT
    from assistant.domain.timeutil import beijing_now

    with session_factory() as session:
        task = session.get(FollowupTask, int(row["task_id"]))
        if task is None:
            return
        status = str(result.get("status") or "")
        if result.get("ok") and status in {"sent", "already-sent"}:
            task.sent_at = beijing_now()
            task.send_result = "platform-sent"
            task.send_confirmation = (
                "already-sent" if status == "already-sent" else "api-confirmed"
            )
            task.last_error = ""
            image = result.get("image")
            if isinstance(image, dict) and not image.get("ok"):
                task.status = "needs_review"
                task.review_reason = "image_send_failed"
                task.requires_manual_confirmation = True
                task.last_error = (
                    "配图发送失败："
                    f"{image.get('reason') or image.get('error') or 'unknown'}"
                )
            session.commit()
            return
        if status == "send-unknown":
            task.send_result = "send-unknown"
            task.status = "needs_review"
            task.review_reason = "send_unknown_needs_review"
            task.requires_manual_confirmation = True
            task.last_error = str(result.get("error") or "发送结果未确认")
            session.commit()
            return
        task.last_error = str(
            result.get("error") or result.get("reason") or "send-failed"
        )
        if task.send_result == SENDING_RESULT:
            task.send_result = ""
        session.commit()


def write_feishu_completed(row: dict[str, Any]) -> dict[str, Any]:
    record_id = str(row.get("feishu_record_id") or "").strip()
    if not record_id:
        return {"status": "no-record", "error": "缺少飞书 record_id"}
    try:
        from lib.app_config import load_bitable_settings
        from lib.feishu_bitable import (
            COOPERATION_STATUS_COMPLETED,
            DEFAULT_BITABLE_APP_ID,
            FeishuBitableError,
            get_bitable_access_token,
            update_record_cooperation_status,
        )

        settings = load_bitable_settings(default_app_id=DEFAULT_BITABLE_APP_ID)
        if not (settings.get("app_id") and settings.get("app_secret")):
            return {"status": "skipped-no-credentials", "error": "未配置飞书密钥"}
        result = update_record_cooperation_status(
            get_bitable_access_token(),
            record_id,
            COOPERATION_STATUS_COMPLETED,
        )
        result["record_id"] = record_id
        return result
    except FeishuBitableError as error:
        return {"status": "error", "error": str(error)}
    except Exception as error:
        return {"status": "error", "error": str(error)}


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    configure_logging(verbose=bool(args.verbose))
    set_verbose(bool(args.verbose))

    if args.execute and not args.yes:
        logger.error(
            "将真实发送跟进私信。确认请加 --yes，或去掉 --execute 做只读预演。"
        )
        return 2

    from sqlalchemy.orm import sessionmaker

    from assistant.database.engine import create_database_engine
    from assistant.domain.timeutil import beijing_now
    from assistant.paths import database_path

    limit = args.execute_limit if args.execute_limit > 0 else DEFAULT_EXECUTE_LIMIT
    session_factory = sessionmaker(
        bind=create_database_engine(database_path()),
        expire_on_commit=False,
    )

    running_jobs = find_running_jobs(
        session_factory,
        ignore_job_id=str(args.ignore_job_id or ""),
    )
    if running_jobs:
        detail = "、".join(
            f"{item['job_type']}({item['status']})" for item in running_jobs[:3]
        )
        logger.error(
            f"[互斥] 有任务正在使用店铺页面：{detail}。请等任务结束后再跑。"
        )
        print(
            json.dumps(
                {"blocked": "running-jobs", "jobs": running_jobs},
                ensure_ascii=False,
            )
        )
        return 3

    stale_claims = release_stale_claims(
        session_factory,
        stale_minutes=int(args.claim_stale_minutes),
        apply=bool(args.execute),
    )
    for item in stale_claims:
        logger.warning(
            f"[认领] task={item['task_id']} 上次发送未完成（{item['updated_at']}）；"
            + (
                "已转人工确认，不会自动重发。"
                if args.execute
                else "预演不修改，执行时会转人工确认。"
            )
        )

    task_id = int(args.task_id) if args.task_id else None
    today = beijing_now().date()
    with session_factory() as session:
        rows = select_sendable_tasks(
            session,
            today=today,
            stage=args.stage or "",
            creator_id=args.creator_id or "",
            creator_name=args.creator_name or "",
            task_id=task_id,
            limit=1 if task_id else limit,
        )
    if not rows:
        if task_id:
            logger.error(
                f"[选人] task={task_id} 当前不可发送（已发/待确认/未到期/平台状态不符）。"
            )
            print(
                json.dumps(
                    {"selected": 0, "sent": 0, "skipped": 0, "task_id": task_id},
                    ensure_ascii=False,
                )
            )
            return 4
        logger.info("[选人] 没有到期的跟进私信任务。")
        print(
            json.dumps(
                {"selected": 0, "sent": 0, "skipped": 0, "rows": []},
                ensure_ascii=False,
            )
        )
        return 0

    try:
        store_id = resolve_store_id(
            store_id=args.store_id,
            store_name=args.store_name,
            default_store_id=None if args.no_default_store else DEFAULT_TEST_STORE_ID,
        )
    except RuntimeError as error:
        logger.error(f"[店铺] {error}")
        print(json.dumps({"error": "store-resolution-failed", "detail": str(error)}, ensure_ascii=False))
        return 2

    summary: dict[str, Any] = {
        "selected": len(rows),
        "sent": 0,
        "previewed": 0,
        "skipped": 0,
        "rows": [],
        "execute": bool(args.execute),
        "write_feishu": bool(args.write_feishu),
        "store_id": store_id,
    }
    if stale_claims:
        summary["stale_claims"] = [item["task_id"] for item in stale_claims]
    backup_path: Path | None = None
    if args.execute:
        backup_path = write_pre_execute_backup(rows)
        summary["backup_path"] = str(backup_path)
        logger.info(f"[备份] 发送前快照已写入 {backup_path}")

    failed = 0
    for row in rows:
        row_store = str(row.get("store_ziniao_id") or "")
        if row_store and row_store != store_id:
            summary["skipped"] += 1
            summary["rows"].append(
                {
                    "task_id": row["task_id"],
                    "creator_name": row["creator_name"],
                    "result": "store-mismatch",
                    "expected_store": row_store,
                    "resolved_store": store_id,
                }
            )
            logger.warning(
                f"[跳过] {row['creator_name']} 任务属于店 {row_store}，"
                f"与当前解析的 {store_id} 不一致。"
            )
            if task_id:
                failed += 1
                break
            continue

        if args.execute and not claim_task(session_factory, row["task_id"]):
            summary["skipped"] += 1
            summary["rows"].append(
                {
                    "task_id": row["task_id"],
                    "creator_name": row["creator_name"],
                    "result": "already-claimed",
                }
            )
            logger.warning(
                f"[跳过] task={row['task_id']} 已被其他发送占用（sending），不重复发送。"
            )
            continue

        attachment = resolve_attachment_path(row)
        logger.info(
            f"[{'发送' if args.execute else '预演'}] {row['creator_name']} "
            f"{row['stage']} scheduled={row['scheduled_for']} "
            f"image={'yes' if attachment else 'no'}"
        )
        result = send_direct_message(
            store_id,
            row["message"],
            creator_name=row["creator_name"],
            creator_id=row["creator_id"],
            execute=bool(args.execute),
            wait=float(args.send_wait),
            already_sent_predicate=thread_contains_message_predicate(row["message"]),
            image_path=attachment,
        )
        row_summary = {
            "task_id": row["task_id"],
            "creator_name": row["creator_name"],
            "stage": row["stage"],
            "status": result.get("status") or "",
            "ok": bool(result.get("ok")),
            "error": result.get("error") or result.get("reason") or "",
            "image": result.get("image"),
        }
        if not args.execute:
            row_summary["message"] = row["message"]
        if args.execute:
            record_send_result(session_factory, row, result)
            if args.write_feishu and row["stage"] == "content_found" and result.get("ok"):
                feishu_result = write_feishu_completed(row)
                row_summary["feishu"] = feishu_result
                logger.info(f"[飞书] {row['creator_name']} → {feishu_result.get('status')}")
        summary["rows"].append(row_summary)
        if result.get("ok"):
            if args.execute:
                summary["sent"] += 1
            else:
                summary["previewed"] += 1
        else:
            failed += 1
            logger.error(
                f"[失败] {row['creator_name']}: "
                f"{result.get('error') or result.get('reason') or result}"
            )
            break

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="SOP 2 达人跟进：发送到期跟进私信（默认只预演，不发送）"
    )
    parser.add_argument("--store-id", default=None)
    parser.add_argument("--store-name", default=None)
    parser.add_argument("--no-default-store", action="store_true")
    parser.add_argument(
        "--stage",
        choices=SENDABLE_STAGES,
        default=None,
        help="只处理该阶段；不传按到期任务批次",
    )
    parser.add_argument("--creator-id", default=None, help="只处理该达人")
    parser.add_argument("--creator-name", default=None, help="只处理该达人名")
    parser.add_argument(
        "--task-id",
        type=int,
        default=0,
        help="只处理该跟进任务（网页「发送」入口使用）；不可发送时退出码 4",
    )
    parser.add_argument(
        "--ignore-job-id",
        default="",
        help="互斥检查时忽略该任务（网页入口传入自己的任务 ID）",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--execute-limit", type=int, default=DEFAULT_EXECUTE_LIMIT)
    parser.add_argument(
        "--claim-stale-minutes",
        type=int,
        default=DEFAULT_CLAIM_STALE_MINUTES,
        help="超过该分钟数的「发送中」认领会转人工确认，不自动重发",
    )
    parser.add_argument(
        "--write-feishu",
        action="store_true",
        help="content_found 发送成功后把飞书合作状态改为「已完成」",
    )
    parser.add_argument("--send-wait", type=float, default=2.5)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
