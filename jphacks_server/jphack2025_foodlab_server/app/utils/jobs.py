from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Dict, Optional
from uuid import uuid4


@dataclass
class Job:
    id: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: datetime = datetime.now(timezone.utc)
    updated_at: datetime = datetime.now(timezone.utc)


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = Lock()

    def create(self) -> Job:
        job_id = uuid4().hex
        job = Job(id=job_id, status="pending")
        with self._lock:
            self._jobs[job_id] = job
        return job

    def complete(self, job_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "done"
                job.result = result
                job.error = None
                job.updated_at = datetime.now(timezone.utc)

    def fail(self, job_id: str, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = "error"
                job.error = message
                job.updated_at = datetime.now(timezone.utc)

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return Job(
                id=job.id,
                status=job.status,
                result=job.result,
                error=job.error,
                created_at=job.created_at,
                updated_at=job.updated_at,
            )


job_store = InMemoryJobStore()
