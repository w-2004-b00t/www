from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import persistence
from app.services import mistake_repository as repository


class MistakeRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite"
        self.path_patch = patch.object(persistence, "DB_PATH", self.db_path)
        self.path_patch.start()
        persistence.init_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.temp_dir.cleanup()

    def _record(self, *, updated_at: str = "2026-06-25 10:00", answer: str = "") -> dict:
        return {
            "id": "mistake_repo",
            "userId": "student_repo",
            "knowledge": "栈",
            "stem": "说明栈的特点。",
            "answer": answer,
            "wrongReason": "概念不清",
            "status": "待订正",
            "createdAt": "2026-06-25 09:00",
            "updatedAt": updated_at,
        }

    def test_migration_is_idempotent_and_newer_record_wins(self) -> None:
        repository.migrate_legacy_mistakes([self._record(answer="先进后出")])
        repository.migrate_legacy_mistakes([self._record(answer="先进后出")])
        self.assertEqual(len(repository.list_mistakes("student_repo")), 1)

        repository.migrate_legacy_mistakes([
            self._record(updated_at="2026-06-25 11:00", answer="后进先出"),
        ])
        loaded = repository.get_mistake("mistake_repo", "student_repo")
        self.assertEqual(loaded["answer"], "后进先出")

    def test_version_conflict_does_not_overwrite_current_record(self) -> None:
        created = repository.create_mistake(self._record())
        created["status"] = "订正中"
        updated = repository.update_mistake(created, expected_version=1)
        self.assertEqual(updated["version"], 2)

        stale = dict(created)
        stale["status"] = "已掌握"
        with self.assertRaises(repository.MistakeVersionConflict):
            repository.update_mistake(stale, expected_version=1)
        self.assertEqual(repository.get_mistake("mistake_repo", "student_repo")["status"], "订正中")

    def test_missing_answer_stays_empty_and_analytics_use_real_attempts(self) -> None:
        first = repository.create_mistake(self._record(answer=""))
        second = repository.create_mistake({
            **self._record(answer="后进先出"),
            "id": "mistake_mastered",
            "status": "已掌握",
            "correctionAttempts": [{"score": 80}],
            "latestCorrection": {"score": 80},
            "verificationAttempts": [{"passed": True}],
        })
        records = repository.list_mistakes("student_repo")
        analytics = repository.mistake_analytics(records)

        self.assertEqual(first["answer"], "")
        self.assertEqual(second["version"], 1)
        self.assertEqual(analytics["total"], 2)
        self.assertEqual(analytics["masteryRate"], 50)
        self.assertEqual(analytics["verificationPassRate"], 100)


if __name__ == "__main__":
    unittest.main()
