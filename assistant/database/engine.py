"""SQLite engine creation, migration-safe backups, and pragmas."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import Engine, create_engine, event

from assistant.paths import database_path

# 长写入事务（生成待办、物流同步）会连续占住 SQLite 写锁，短超时会让并发的
# 心跳与页面写入直接报 "database is locked"，因此显式放大等待窗口。
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0
SQLITE_BUSY_TIMEOUT_MS = int(SQLITE_BUSY_TIMEOUT_SECONDS * 1000)


def create_database_engine(path: Path | None = None) -> Engine:
    resolved_path = path or database_path()
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{resolved_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
        },
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _connection_record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
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
