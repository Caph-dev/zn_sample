from __future__ import annotations

import sys
import tempfile
import unittest
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

import assistant.bootstrap as bootstrap
from assistant.database.engine import create_database_engine
from assistant.database.models import Base


ALEMBIC_CONFIGURATION_PATH = (
    PROJECT_ROOT / "assistant" / "database" / "alembic.ini"
).resolve()


class MigrationIntegrationTests(unittest.TestCase):
    def test_upgrade_from_outside_repository_matches_schema_and_downgrades(self) -> None:
        with (
            tempfile.TemporaryDirectory() as database_directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            sqlite_path = Path(database_directory) / "assistant.sqlite3"
            with (
                patch.object(
                    bootstrap,
                    "ensure_user_dirs",
                    side_effect=AssertionError("temporary migration touched user dirs"),
                ),
                patch.object(
                    bootstrap,
                    "database_path",
                    side_effect=AssertionError("temporary migration used user database"),
                ),
                chdir(outside_directory),
            ):
                bootstrap.upgrade_database(
                    configuration_path=ALEMBIC_CONFIGURATION_PATH,
                    sqlite_path=sqlite_path,
                )

            engine = create_database_engine(sqlite_path)
            try:
                inspector = inspect(engine)
                expected_model_tables = set(Base.metadata.tables)
                self.assertEqual(len(expected_model_tables), 13)
                self.assertEqual(
                    set(inspector.get_table_names()),
                    expected_model_tables | {"alembic_version"},
                )

                with engine.connect() as connection:
                    self.assertEqual(
                        connection.scalar(text("SELECT version_num FROM alembic_version")),
                        "0006_followup_previewed_at",
                    )

                for table_name, model_table in Base.metadata.tables.items():
                    actual_columns = sorted(
                        (
                            column["name"],
                            str(column["type"]),
                            column["nullable"],
                        )
                        for column in inspector.get_columns(table_name)
                    )
                    expected_columns = sorted(
                        (
                            column.name,
                            str(column.type),
                            column.nullable,
                        )
                        for column in model_table.columns
                    )
                    self.assertEqual(actual_columns, expected_columns, table_name)

                expected_foreign_keys = {
                    "sample_cases": {(('store_id',), 'stores', ('id',))},
                    "shipments": {(('sample_case_id',), 'sample_cases', ('id',))},
                    "shipment_snapshots": {
                        (("shipment_id",), "shipments", ("id",))
                    },
                    "followup_tasks": {
                        (("sample_case_id",), "sample_cases", ("id",))
                    },
                    "content_evidences": {
                        (("sample_case_id",), "sample_cases", ("id",))
                    },
                    "job_events": {(('job_id',), 'jobs', ('id',))},
                }
                for table_name, expected_keys in expected_foreign_keys.items():
                    actual_keys = {
                        (
                            tuple(foreign_key["constrained_columns"]),
                            foreign_key["referred_table"],
                            tuple(foreign_key["referred_columns"]),
                        )
                        for foreign_key in inspector.get_foreign_keys(table_name)
                    }
                    self.assertEqual(actual_keys, expected_keys, table_name)

                expected_unique_constraints = {
                    "stores": {("ziniao_store_id",)},
                    "sample_cases": {
                        ("store_id", "creator_id", "product_id", "apply_id")
                    },
                    "shipments": {("sample_case_id",)},
                    "followup_tasks": {
                        ("sample_case_id", "stage", "scheduled_for")
                    },
                    "job_events": {("job_id", "sequence")},
                }
                for table_name, expected_constraints in expected_unique_constraints.items():
                    actual_constraints = {
                        tuple(constraint["column_names"])
                        for constraint in inspector.get_unique_constraints(table_name)
                    }
                    self.assertEqual(actual_constraints, expected_constraints, table_name)

                downgrade_configuration = Config(str(ALEMBIC_CONFIGURATION_PATH))
                bootstrap.configure_alembic_paths(downgrade_configuration)
                with engine.begin() as connection:
                    downgrade_configuration.attributes["connection"] = connection
                    command.downgrade(downgrade_configuration, "base")

                self.assertEqual(inspect(engine).get_table_names(), ["alembic_version"])
                with engine.connect() as connection:
                    self.assertIsNone(
                        connection.scalar(text("SELECT version_num FROM alembic_version"))
                    )
            finally:
                engine.dispose()


if __name__ == "__main__":
    unittest.main()
