import tempfile
import unittest
from pathlib import Path

from core.database import DatabaseRepository
from services.collector_service import process_new_dom_ids


class CollectorServiceTest(unittest.TestCase):
    def make_repository(self) -> DatabaseRepository:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = DatabaseRepository(
            Path(temp_dir.name) / "creators.db"
        )
        repository.init_database()

        return repository

    def test_process_new_dom_ids_skips_existing_and_inserts_new(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("100")
        seen_session_ids: set[str] = set()

        result = process_new_dom_ids(
            repository,
            ["100", "101", "102", "101"],
            seen_session_ids,
            target=500,
        )

        self.assertEqual(
            result["old_db_ids"],
            ["100"],
        )
        self.assertEqual(
            result["inserted_ids"],
            ["101", "102"],
        )
        self.assertEqual(
            repository.count_pending_creators(),
            3,
        )

    def test_process_new_dom_ids_respects_pending_target(self):
        repository = self.make_repository()
        repository.add_creator_to_queue("100")
        repository.add_creator_to_queue("101")
        seen_session_ids: set[str] = set()

        result = process_new_dom_ids(
            repository,
            ["102", "103", "104"],
            seen_session_ids,
            target=3,
        )

        self.assertEqual(
            result["inserted_ids"],
            ["102"],
        )
        self.assertEqual(
            repository.count_pending_creators(),
            3,
        )

    def test_process_new_dom_ids_does_not_reprocess_seen_session_ids(self):
        repository = self.make_repository()
        seen_session_ids: set[str] = {"100"}

        result = process_new_dom_ids(
            repository,
            ["100", "101"],
            seen_session_ids,
            target=500,
        )

        self.assertEqual(
            result["new_session_ids"],
            ["101"],
        )
        self.assertEqual(
            result["inserted_ids"],
            ["101"],
        )


if __name__ == "__main__":
    unittest.main()

