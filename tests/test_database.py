import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.database import (
    DatabaseRepository,
    migrate_legacy_database,
    sqlite_integrity_check,
)
from core.paths import AppPaths


class DatabaseTest(unittest.TestCase):
    def make_paths(self, root: Path) -> AppPaths:
        return AppPaths(
            project_root=root / "project",
            app_support_dir=root / "support",
            documents_dir=root / "documents",
        )

    def create_legacy_database(self, path: Path) -> None:
        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        repository = DatabaseRepository(path)
        repository.init_database()
        repository.add_creator_to_queue("123")

    def test_migration_copies_legacy_database_and_creates_backup(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = self.make_paths(root)
            self.create_legacy_database(paths.legacy_database_path)

            result = migrate_legacy_database(paths)

            self.assertTrue(result.migrated)
            self.assertTrue(paths.database_path.exists())
            self.assertTrue(result.backup_path.exists())
            self.assertTrue(sqlite_integrity_check(paths.database_path))
            self.assertTrue(sqlite_integrity_check(result.backup_path))

            migrated_repo = DatabaseRepository(paths.database_path)
            self.assertEqual(
                migrated_repo.count_creators(),
                1,
            )

    def test_reset_processing_to_pending(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "creators.db"
            repository = DatabaseRepository(db_path)
            repository.init_database()
            repository.add_creator_to_queue("123")
            repository.set_creator_status(
                "123",
                "PROCESSING",
            )

            changed = repository.reset_processing_to_pending()

            self.assertEqual(changed, 1)
            self.assertEqual(
                repository.get_creator("123")["status"],
                "PENDING",
            )

    def test_should_skip_completed_creator(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "creators.db"
            repository = DatabaseRepository(db_path)
            repository.init_database()
            repository.add_creator_to_queue("123")

            self.assertFalse(
                repository.should_skip_creator("123")
            )

            repository.set_creator_status(
                "123",
                "FOUND_PHONE",
            )

            self.assertTrue(
                repository.should_skip_creator("123")
            )

    def test_init_database_adds_missing_retry_columns(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "creators.db"
            connection = sqlite3.connect(db_path)
            connection.execute(
                """
                CREATE TABLE creators (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    creator_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL
                )
                """
            )
            connection.commit()
            connection.close()

            repository = DatabaseRepository(db_path)
            repository.init_database()

            connection = sqlite3.connect(db_path)
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(creators)"
                ).fetchall()
            }
            connection.close()

            self.assertIn(
                "error_message",
                columns,
            )
            self.assertIn(
                "retry_count",
                columns,
            )


if __name__ == "__main__":
    unittest.main()

