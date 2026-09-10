from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from assistant.database.engine import (
    SQLITE_BUSY_TIMEOUT_MS,
    create_database_engine,
)


class DatabaseEnginePragmaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.engine = create_database_engine(
            Path(self.temporary_directory.name) / "pragma.sqlite3"
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temporary_directory.cleanup()

    def test_connections_use_wal_and_wait_for_the_write_lock(self) -> None:
        with self.engine.connect() as connection:
            busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
            journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()

        self.assertEqual(int(busy_timeout), SQLITE_BUSY_TIMEOUT_MS)
        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(int(foreign_keys), 1)

    def test_competing_writer_waits_for_the_open_transaction(self) -> None:
        """写锁被占时，第二个写连接必须等锁释放；busy_timeout=0 时会立刻失败。"""
        lock_release_delay = 0.4

        def hold_write_lock() -> None:
            with self.engine.connect() as holder_connection:
                holder_connection.exec_driver_sql("BEGIN IMMEDIATE")
                holder_connection.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS lock_holder (id INTEGER)"
                )
                time.sleep(lock_release_delay)

        holder_thread = threading.Thread(target=hold_write_lock)
        holder_thread.start()
        try:
            time.sleep(0.1)
            with self.engine.begin() as competing_connection:
                competing_connection.exec_driver_sql(
                    "CREATE TABLE IF NOT EXISTS lock_waiter (id INTEGER)"
                )
        finally:
            holder_thread.join(timeout=5.0)

        self.assertFalse(holder_thread.is_alive())


if __name__ == "__main__":
    unittest.main()
