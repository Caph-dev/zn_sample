#!/usr/bin/env python3
"""Repair only legacy local batch metadata; never approve or write Feishu."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from sqlalchemy.orm import sessionmaker

from assistant.database.engine import create_database_engine
from assistant.paths import database_path, user_data_dir
from assistant.services.auto_approval_recovery import (
    plan_legacy_execution_recovery, recover_legacy_execution,
)
from lib.app_log import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execution-id", required=True)
    parser.add_argument("--apply", action="store_true", help="备份数据库后修复本地状态；不执行任何平台/飞书操作")
    args = parser.parse_args()
    configure_logging()
    logger = logging.getLogger(__name__)
    if not database_path().is_file():
        logger.error("本地数据库不存在，停止恢复")
        return 2
    engine = create_database_engine()
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        if args.apply:
            backup_directory = user_data_dir() / "backups" / (
                "auto_approval_recovery_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            )
            result = recover_legacy_execution(sessions, args.execution_id, backup_directory=backup_directory)
        else:
            with sessions() as session:
                result = plan_legacy_execution_recovery(session, args.execution_id)
        logger.info(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        logger.error("历史批次恢复停止：%s", error)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
