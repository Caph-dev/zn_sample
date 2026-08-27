"""Production bootstrap: logging, directories, backup, migration, lifecycle."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIRECTORY = REPOSITORY_ROOT / "scripts"
MIGRATIONS_DIRECTORY = (REPOSITORY_ROOT / "assistant" / "database" / "migrations").resolve()
DEFAULT_CONFIGURATION_PATH = (
    REPOSITORY_ROOT / "assistant" / "database" / "alembic.ini"
).resolve()
for import_path in (str(REPOSITORY_ROOT), str(SCRIPTS_DIRECTORY)):
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from alembic import command
from alembic.config import Config

from assistant.database.engine import backup_sqlite, create_database_engine
from assistant.paths import database_path, ensure_user_dirs
from lib.app_config import load_dotenv
from lib.app_log import configure_logging


def configure_alembic_paths(configuration: Config) -> None:
    """Make migration and import paths independent from the process cwd."""
    configuration.set_main_option("script_location", str(MIGRATIONS_DIRECTORY))
    configuration.set_main_option(
        "prepend_sys_path",
        os.pathsep.join(
            str(import_path.resolve())
            for import_path in (REPOSITORY_ROOT, SCRIPTS_DIRECTORY)
        ),
    )


def upgrade_database(
    *,
    configuration_path: Path | None = None,
    sqlite_path: Path | None = None,
) -> None:
    """Upgrade the assistant database using an application-managed connection."""
    if sqlite_path is None:
        application_directory = ensure_user_dirs()
        resolved_sqlite_path = database_path().resolve()
        backup_directory = application_directory / "backups"
    else:
        resolved_sqlite_path = Path(sqlite_path).resolve()
        backup_directory = resolved_sqlite_path.parent / "backups"

    engine = create_database_engine(resolved_sqlite_path)
    try:
        if resolved_sqlite_path.exists() and resolved_sqlite_path.stat().st_size:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup_sqlite(
                engine,
                backup_directory / f"pre-upgrade-{timestamp}.sqlite3",
            )

        resolved_configuration_path = (
            Path(configuration_path).resolve()
            if configuration_path is not None
            else DEFAULT_CONFIGURATION_PATH
        )
        configuration = Config(str(resolved_configuration_path))
        configure_alembic_paths(configuration)
        with engine.begin() as connection:
            configuration.attributes["connection"] = connection
            command.upgrade(configuration, "head")
    finally:
        engine.dispose()


def main() -> int:
    load_dotenv()
    configure_logging()
    upgrade_database()
    from assistant.lifecycle import run_assistant

    return run_assistant()
