"""Sequential grading queue — one assessment at a time, non-blocking HTTP."""
from __future__ import annotations

import os
import threading
import uuid
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from datetime import datetime
from queue import Empty, Queue
from typing import Callable, Optional

from app.config import job_hard_timeout_seconds

RECENT_FINISHED = 40


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class GradeJob:
    id: str
    filename: str
    provider: str = ""
    model: str = ""
    status: str = "queued"  # queued | running | done | failed
    created_at: str = field(default_factory=_now)
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    report: Optional[str] = None
    student_name: Optional[str] = None
    total_score_percent: Optional[float] = None
    error: Optional[str] = None
    extraction_word_count: Optional[int] = None
    requested_by: Optional[str] = None

    def to_dict(self):
        return {
            "id": self.id,
            "filename": self.filename,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
            "student_name": self.student_name,
            "total_score_percent": self.total_score_percent,
            "error": self.error,
            "extraction_word_count": self.extraction_word_count,
        }


def _log_status(job: GradeJob) -> str:
    if "timed out" in (job.error or "").lower():
        return "Timed out"
    if job.status == "done":
        return "Done"
    if job.status == "failed":
        return "Failed"
    return (job.status or "Unknown").replace("_", " ").title()


class TaskTimedOut(FuturesTimeout):
    def __init__(self, thread: threading.Thread):
        super().__init__("grading task timed out")
        self.thread = thread


class GradingJobQueue:
    """Processes grading jobs one-by-one in a background worker thread."""

    def __init__(self):
        self._jobs: dict[str, GradeJob] = {}
        self._queue: Queue[str] = Queue()
        self._lock = threading.Lock()
        self._worker_started = False

    def _ensure_worker(self):
        with self._lock:
            if self._worker_started:
                return
            self._worker_started = True
            thread = threading.Thread(target=self._worker_loop, name="grading-queue", daemon=True)
            thread.start()

    def _timed_out_thread_alive(self, job: GradeJob) -> bool:
        thread = getattr(job, "_timed_out_thread", None)
        return bool(thread and thread.is_alive())

    def _is_inflight_job(self, job: GradeJob) -> bool:
        return job.status in ("queued", "running") or self._timed_out_thread_alive(job)

    def _persist(self, job: GradeJob):
        with self._lock:
            if getattr(job, "_logged", False) or getattr(job, "_logging", False):
                return
            job._logging = True
        success = False
        try:
            from app.db import insert_system_log

            status = _log_status(job)
            student = (job.student_name or "").strip()
            if job.status == "done":
                score = job.total_score_percent
                score_bit = f"Score {score}%." if score is not None else "Completed."
                report_bit = f" Report {job.report}." if job.report else ""
                who = f"{student}. " if student else ""
                message = f"{status}. {who}{job.provider}/{job.model}: {score_bit}{report_bit}".strip()
            else:
                who = f"{student}. " if student else ""
                detail = job.error or "Grading failed"
                message = f"{status}. {who}{detail}"
            insert_system_log(
                kind="job",
                status=status,
                filename=job.filename,
                message=message,
                provider=job.provider,
                model=job.model,
                actor=job.requested_by or "system",
            )
            success = True
        except Exception as exc:
            print(f"--- system log write failed: {exc} ---")
        finally:
            with self._lock:
                if success:
                    job._logged = True
                job._logging = False

    def flush_finished_to_log(self):
        """Write any in-memory Done/Failed jobs that never reached System Log."""
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status not in ("done", "failed"):
                continue
            if getattr(job, "_logged", False):
                continue
            self._persist(job)

    def _finalize(self, job: GradeJob, status: str, *, error: Optional[str] = None, result: Optional[dict] = None):
        with self._lock:
            if getattr(job, "_finalized", False):
                return False
            job._finalized = True
            job.status = status
            job.finished_at = _now()
            if error:
                job.error = error
            if result:
                job.student_name = result.get("student_name")
                job.report = result.get("report")
                job.total_score_percent = result.get("total_score_percent")
                job.extraction_word_count = result.get("extraction_word_count")
                if not error:
                    job.error = result.get("error")
            self._prune_unlocked()
        self._persist(job)
        return True

    def _prune_unlocked(self):
        finished = [j for j in self._jobs.values() if j.status in ("done", "failed") and not self._timed_out_thread_alive(j)]
        finished.sort(key=lambda j: j.finished_at or j.created_at, reverse=True)
        keep = {j.id for j in self._jobs.values() if self._is_inflight_job(j)}
        keep.update(j.id for j in finished[:RECENT_FINISHED])
        self._jobs = {i: j for i, j in self._jobs.items() if i in keep}

    def _timeout_message(self, job: GradeJob, seconds: float) -> str:
        kind = "local Ollama" if (job.provider or "").lower() == "ollama" else "provider"
        return (
            f"{kind} {job.provider}/{job.model} timed out after {int(seconds)}s. "
            "Job marked failed so the queue can continue. See System log."
        )

    def _run_task(self, job: GradeJob, task: Callable[[], dict], timeout_sec: float) -> dict:
        result_box: list = []
        error_box: list[BaseException] = []
        done = threading.Event()

        def runner():
            try:
                result_box.append(task())
            except BaseException as exc:
                error_box.append(exc)
            finally:
                done.set()

        thread = threading.Thread(target=runner, name="grade-job", daemon=True)
        job._task_thread = thread
        thread.start()
        if not done.wait(timeout=timeout_sec):
            raise TaskTimedOut(thread)
        if error_box:
            raise error_box[0]
        return result_box[0]

    def expire_stale(self) -> int:
        """Fail running jobs that exceeded the provider hard cap (unblocks the queue)."""
        expired = 0
        now = datetime.now()
        with self._lock:
            snapshot = list(self._jobs.values())
        for job in snapshot:
            if job.status != "running" or getattr(job, "_finalized", False):
                continue
            started = _parse_ts(job.started_at) or _parse_ts(job.created_at)
            if not started:
                continue
            limit = job_hard_timeout_seconds(job.provider)
            if (now - started).total_seconds() < limit:
                continue
            if self._finalize(job, "failed", error=self._timeout_message(job, limit)):
                expired += 1
        return expired

    def _worker_loop(self):
        while True:
            try:
                job_id = self._queue.get(timeout=0.5)
            except Empty:
                self.expire_stale()
                continue
            job = self._jobs.get(job_id)
            if not job:
                self._queue.task_done()
                continue
            task = job._task  # type: ignore[attr-defined]
            timeout_sec = job_hard_timeout_seconds(job.provider)
            job.status = "running"
            job.started_at = _now()
            try:
                result = self._run_task(job, task, timeout_sec)
                if result.get("error") or result.get("status") == "Failed":
                    self._finalize(
                        job,
                        "failed",
                        error=result.get("error") or "Grading failed",
                        result=result,
                    )
                else:
                    self._finalize(job, "done", result=result)
            except TaskTimedOut as timeout_exc:
                with self._lock:
                    job._timed_out_thread = timeout_exc.thread
                self._finalize(job, "failed", error=self._timeout_message(job, timeout_sec))
            except Exception as exc:
                self._finalize(job, "failed", error=str(exc))
            finally:
                if hasattr(job, "_task"):
                    del job._task  # type: ignore[attr-defined]
                if hasattr(job, "_task_thread") and not job._task_thread.is_alive():  # type: ignore[attr-defined]
                    del job._task_thread  # type: ignore[attr-defined]
                self._queue.task_done()

    def enqueue(
        self,
        filename: str,
        provider: str,
        model: str,
        task: Callable[[], dict],
        requested_by: Optional[str] = None,
    ) -> GradeJob:
        job_id = uuid.uuid4().hex[:8]
        job = GradeJob(
            id=job_id,
            filename=filename,
            provider=provider,
            model=model,
            requested_by=requested_by,
        )
        job._task = task  # type: ignore[attr-defined]
        job._finalized = False
        with self._lock:
            self._jobs[job_id] = job
        self._queue.put(job_id)
        self._ensure_worker()
        return job

    def get(self, job_id: str) -> Optional[GradeJob]:
        self.expire_stale()
        return self._jobs.get(job_id)

    def list_jobs(self, limit: int = 20, offset: int = 0, status: Optional[str] = None):
        self.expire_stale()
        with self._lock:
            jobs = list(self._jobs.values())
        if status == "active":
            jobs = [j for j in jobs if self._is_inflight_job(j)]
        elif status:
            jobs = [j for j in jobs if j.status == status]
        jobs = sorted(jobs, key=lambda j: j.created_at, reverse=True)
        total = len(jobs)
        return jobs[offset : offset + limit], total

    def status_counts(self) -> dict:
        self.expire_stale()
        counts = {"queued": 0, "running": 0, "done": 0, "failed": 0}
        with self._lock:
            jobs = list(self._jobs.values())
        for job in jobs:
            if job.status in counts:
                counts[job.status] += 1
        counts["active"] = sum(1 for j in jobs if self._is_inflight_job(j))
        counts["total"] = len(jobs)
        return counts

    def active_count(self) -> int:
        self.expire_stale()
        with self._lock:
            return sum(1 for j in self._jobs.values() if self._is_inflight_job(j))

    def has_active_job_for(self, filename: str) -> bool:
        self.expire_stale()
        base = os.path.basename(filename)
        with self._lock:
            return any(
                os.path.basename(j.filename) == base and self._is_inflight_job(j)
                for j in self._jobs.values()
            )


grading_queue = GradingJobQueue()
