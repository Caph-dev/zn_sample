"""SQLite engine creation, migration-safe backups, and pragmas."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event

from assistant.paths import database_path


def create_database_engine(path: Path | None = None) -> Engine:
    resolved_path = path or database_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{resolved_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def backup_sqlite(engine: Engine, destination: Path) -> None:
    """Create a consistent SQLite backup, including committed WAL contents."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = engine.raw_connection()
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.driver_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
