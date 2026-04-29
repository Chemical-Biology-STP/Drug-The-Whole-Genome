"""JSON-based job metadata persistence with file locking.

Provides thread-safe read/write access to the job store at
``webapp/data/jobs.json`` using POSIX advisory file locks (``fcntl.flock``).
"""

from __future__ import annotations

import fcntl
import json
import os
from typing import Dict, List, Optional

from webapp.services.models import JobRecord


class JobStore:
    """Persistent store for job records backed by a JSON file.

    All public methods are thread-safe: concurrent access from the Flask
    request threads and the background JobMonitor thread is serialized via
    an exclusive file lock acquired during each read-modify-write cycle.
    """

    def __init__(self, store_path: str) -> None:
        """Initialize the job store.

        Parameters
        ----------
        store_path:
            Absolute or relative path to the JSON file used for persistence.
            The file (and parent directories) will be created if they do not
            exist.
        """
        self._store_path = store_path
        # Ensure the parent directory exists
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        # Create the file with an empty job list if it doesn't exist
        if not os.path.exists(self._store_path):
            self._write({"jobs": []})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _read(self) -> Dict:
        """Read the entire job store from disk with a shared lock.

        Returns
        -------
        dict
            The parsed JSON content (expected shape: ``{"jobs": [...]}``)
        """
        with open(self._store_path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                content = f.read()
                if not content.strip():
                    return {"jobs": []}
                return json.loads(content)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _write(self, data: Dict) -> None:
        """Write the entire job store to disk with an exclusive lock.

        Parameters
        ----------
        data:
            The full store content to persist (expected shape: ``{"jobs": [...]}``).
        """
        with open(self._store_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_job(self, record: JobRecord) -> None:
        """Append a new job record to the store.

        Parameters
        ----------
        record:
            The :class:`JobRecord` to persist.
        """
        with open(self._store_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                if not content.strip():
                    data = {"jobs": []}
                else:
                    data = json.loads(content)
                data["jobs"].append(record.to_dict())
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def update_job(self, job_id: str, updates: Dict) -> None:
        """Update fields on an existing job record.

        Parameters
        ----------
        job_id:
            The SLURM job ID identifying the record to update.
        updates:
            A dictionary of field names to new values. Only keys present in
            the dict are updated; other fields are left unchanged.
        """
        with open(self._store_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                if not content.strip():
                    data = {"jobs": []}
                else:
                    data = json.loads(content)
                for job in data["jobs"]:
                    if job["job_id"] == job_id:
                        job.update(updates)
                        break
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def get_job(self, job_id: str) -> Optional[JobRecord]:
        """Retrieve a single job record by SLURM job ID.

        Parameters
        ----------
        job_id:
            The SLURM job ID to look up.

        Returns
        -------
        JobRecord or None
            The matching record, or ``None`` if no job with that ID exists.
        """
        data = self._read()
        for job in data["jobs"]:
            if job["job_id"] == job_id:
                return JobRecord.from_dict(job)
        return None

    def get_jobs_for_session(self, session_id: str) -> List[JobRecord]:
        """Retrieve all jobs belonging to a session, newest first.

        Parameters
        ----------
        session_id:
            The session identifier to filter by.

        Returns
        -------
        list[JobRecord]
            Job records matching the session, sorted by ``submitted_at``
            descending (newest first).
        """
        data = self._read()
        jobs = [
            JobRecord.from_dict(job)
            for job in data["jobs"]
            if job["session_id"] == session_id
        ]
        # Sort by submitted_at descending (newest first)
        jobs.sort(key=lambda r: r.submitted_at, reverse=True)
        return jobs

    def get_active_jobs(self) -> List[JobRecord]:
        """Retrieve all jobs in PENDING or RUNNING state.

        Returns
        -------
        list[JobRecord]
            All active job records across all sessions.
        """
        data = self._read()
        return [
            JobRecord.from_dict(job)
            for job in data["jobs"]
            if job["status"] in ("PENDING", "RUNNING")
        ]
