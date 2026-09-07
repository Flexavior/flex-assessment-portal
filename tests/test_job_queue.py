"""Job worker timeout and sequential unblocking."""
import time
import unittest
from unittest.mock import patch

from app.job_queue import GradingJobQueue


class JobQueueTimeoutTests(unittest.TestCase):
    def test_hung_provider_fails_and_unblocks_queue(self):
        q = GradingJobQueue()
        ran = []

        def hang():
            time.sleep(8)
            return {"status": "Success"}

        def ok():
            ran.append("ok")
            return {"student_name": "Pat", "total_score_percent": 80}

        with patch("app.job_queue.job_hard_timeout_seconds", return_value=0.25), patch(
            "app.job_queue.GradingJobQueue._persist"
        ):
            first = q.enqueue("stuck.txt", "omnirouter", "auto", hang)
            second = q.enqueue("next.txt", "omnirouter", "auto", ok)
            deadline = time.time() + 4
            while time.time() < deadline:
                if first.status == "failed" and second.status == "done":
                    break
                time.sleep(0.05)

        self.assertEqual(first.status, "failed")
        self.assertIn("timed out", (first.error or "").lower())
        self.assertEqual(second.status, "done")
        self.assertEqual(ran, ["ok"])

    def test_expire_stale_marks_overdue_running_job(self):
        from app.job_queue import GradeJob

        q = GradingJobQueue()
        job = GradeJob(
            id="oldjob01",
            filename="old.txt",
            provider="openrouter",
            model="x",
            status="running",
            started_at="2000-01-01T00:00:00",
        )
        job._finalized = False
        q._jobs[job.id] = job
        with patch("app.job_queue.job_hard_timeout_seconds", return_value=1), patch(
            "app.job_queue.GradingJobQueue._persist"
        ):
            expired = q.expire_stale()
        self.assertEqual(expired, 1)
        self.assertEqual(job.status, "failed")


class JobQueueActorTests(unittest.TestCase):
    def test_persist_records_requested_by(self):
        from unittest.mock import patch

        from app.job_queue import GradeJob, GradingJobQueue

        q = GradingJobQueue()
        job = GradeJob(
            id="act00001",
            filename="essay.pdf",
            provider="omnirouter",
            model="auto",
            status="done",
            student_name="Pat",
            total_score_percent=79.0,
            report="pat_report.pdf",
            requested_by="Jane Doe (educator)",
        )
        with patch("app.db.insert_system_log") as mock_log:
            q._persist(job)
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args.kwargs["actor"], "Jane Doe (educator)")

    def test_persist_is_idempotent_for_same_job(self):
        from app.job_queue import GradeJob, GradingJobQueue

        q = GradingJobQueue()
        job = GradeJob(
            id="act00002",
            filename="essay.pdf",
            provider="omnirouter",
            model="auto",
            status="failed",
            error="timed out",
        )
        with patch("app.db.insert_system_log") as mock_log:
            q._persist(job)
            q._persist(job)
        mock_log.assert_called_once()

    def test_has_active_job_for_counts_timed_out_lingering_thread(self):
        from app.job_queue import GradeJob, GradingJobQueue

        class _FakeThread:
            def __init__(self, alive: bool):
                self._alive = alive

            def is_alive(self):
                return self._alive

        q = GradingJobQueue()
        job = GradeJob(
            id="act00003",
            filename="same.pdf",
            provider="omnirouter",
            model="auto",
            status="failed",
            error="timed out",
        )
        job._timed_out_thread = _FakeThread(True)
        q._jobs[job.id] = job
        self.assertTrue(q.has_active_job_for("same.pdf"))
        self.assertEqual(q.active_count(), 1)


if __name__ == "__main__":
    unittest.main()
