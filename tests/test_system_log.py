"""System log stores job outcomes and archives for search."""
import os
import tempfile
import unittest
from unittest.mock import patch

from app.config import job_hard_timeout_seconds, llm_request_timeout


class TimeoutConfigTests(unittest.TestCase):
    def test_remote_keeps_request_timeout(self):
        self.assertEqual(llm_request_timeout("omnirouter"), llm_request_timeout("openrouter"))
        self.assertLess(llm_request_timeout("openrouter"), llm_request_timeout("ollama"))
        self.assertGreater(job_hard_timeout_seconds("ollama"), job_hard_timeout_seconds("openai"))


class SystemLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "app.db")
        self.patcher = patch("app.db.DB_PATH", self.db_path)
        self.patcher.start()
        from app.db import init_db

        init_db()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_job_and_archive_are_searchable(self):
        from app.db import create_user, insert_deletion_log, insert_system_log, list_system_log, system_log_count

        user = create_user("log@test.local", "hashed-password", "supervisor", "Log User")
        insert_system_log(
            kind="job",
            status="Done",
            filename="ok.pdf",
            message="Done. Pat. openrouter/flash: Score 80%.",
            provider="openrouter",
            model="flash",
        )
        insert_system_log(
            kind="job",
            status="Failed",
            filename="bad.pdf",
            message="Failed. provider omnirouter/auto timed out after 600s",
            provider="omnirouter",
            model="auto",
        )
        insert_system_log(
            kind="job",
            status="Timed out",
            filename="essay.pdf",
            message="Timed out. provider omnirouter/auto timed out after 600s",
            provider="omnirouter",
            model="auto",
        )
        insert_deletion_log(
            kind="submission",
            record_id=1,
            filename="essay.pdf",
            reason="duplicate upload",
            deleted_by=user["id"],
        )
        timeout_hits = list_system_log(limit=20, search="timed_out")
        self.assertTrue(any(row["status"] == "Timed out" for row in timeout_hits))
        done_hits = list_system_log(limit=20, search="Done")
        self.assertTrue(any(row["status"] == "Done" for row in done_hits))
        failed_hits = list_system_log(limit=20, search="Failed")
        self.assertTrue(any(row["status"] == "Failed" for row in failed_hits))
        archive_hits = list_system_log(limit=20, search="duplicate")
        self.assertTrue(any(row["kind"] == "submission" for row in archive_hits))
        self.assertGreaterEqual(system_log_count(), 2)


if __name__ == "__main__":
    unittest.main()
