"""Production bootstrap: logging, directories, backup, migration, lifecycle."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
for import_path in (str(REPOSITORY_ROOT), str(SCRIPTS_DIRECTORY)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from alembic import command
from alembic.config import Config

from assistant.database.engine import backup_sqlite, create_database_engine
from assistant.paths import database_path, ensure_user_dirs
from lib.app_log import configure_logging


def upgrade_database() -> None:
    application_directory = ensure_user_dirs()
    sqlite_path = database_path()
    engine = create_database_engine(sqlite_path)
    if sqlite_path.exists() and sqlite_path.stat().st_size:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_sqlite(engine, application_directory / "backups" / f"pre-upgrade-{timestamp}.sqlite3")
    configuration = Config(str(REPOSITORY_ROOT / "assistant" / "database" / "alembic.ini"))
    with engine.begin() as connection:
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, "head")
    engine.dispose()


def main() -> int:
    configure_logging()
    upgrade_database()
    from assistant.lifecycle import run_assistant

    return run_assistant()
