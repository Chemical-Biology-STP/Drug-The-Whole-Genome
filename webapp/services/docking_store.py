"""JSON-based docking job persistence with file locking.

Mirrors job_store.py but for DockingRecord objects stored in
webapp/data/docking_jobs.json.
"""

from __future__ import annotations

import fcntl
import json
import os
from typing import Dict, List, Optional

from webapp.services.models import DockingRecord


class DockingStore:
    """Thread-safe persistent store for docking job records."""

    def __init__(self, store_path: str) -> None:
        self._store_path = store_path
        os.makedirs(os.path.dirname(self._store_path), exist_ok=True)
        if not os.path.exists(self._store_path):
            self._write({"jobs": []})

    def _read(self) -> Dict:
        with open(self._store_path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                content = f.read()
                return json.loads(content) if content.strip() else {"jobs": []}
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _write(self, data: Dict) -> None:
        with open(self._store_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def add(self, record: DockingRecord) -> None:
        with open(self._store_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                data = json.loads(content) if content.strip() else {"jobs": []}
                data["jobs"].append(record.to_dict())
                f.seek(0); f.truncate()
                json.dump(data, f, indent=2)
                f.flush(); os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def update(self, docking_id: str, updates: Dict) -> None:
        with open(self._store_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                data = json.loads(content) if content.strip() else {"jobs": []}
                for job in data["jobs"]:
                    if job["docking_id"] == docking_id:
                        job.update(updates)
                        break
                f.seek(0); f.truncate()
                json.dump(data, f, indent=2)
                f.flush(); os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def get(self, docking_id: str) -> Optional[DockingRecord]:
        data = self._read()
        for job in data["jobs"]:
            if job["docking_id"] == docking_id:
                return DockingRecord.from_dict(job)
        return None

    def get_for_session(self, session_id: str) -> List[DockingRecord]:
        data = self._read()
        jobs = [
            DockingRecord.from_dict(j)
            for j in data["jobs"]
            if j["session_id"] == session_id
        ]
        jobs.sort(key=lambda r: r.submitted_at, reverse=True)
        return jobs

    def get_for_user(self, email: str) -> List[DockingRecord]:
        data = self._read()
        jobs = [
            DockingRecord.from_dict(j)
            for j in data["jobs"]
            if j.get("email") == email
        ]
        jobs.sort(key=lambda r: r.submitted_at, reverse=True)
        return jobs

    def get_all(self) -> List[DockingRecord]:
        data = self._read()
        jobs = [DockingRecord.from_dict(j) for j in data["jobs"]]
        jobs.sort(key=lambda r: r.submitted_at, reverse=True)
        return jobs

    def get_active(self) -> List[DockingRecord]:
        data = self._read()
        return [
            DockingRecord.from_dict(j)
            for j in data["jobs"]
            if j["status"] in ("PENDING", "RUNNING")
        ]

    def delete(self, docking_id: str) -> bool:
        with open(self._store_path, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                content = f.read()
                data = json.loads(content) if content.strip() else {"jobs": []}
                before = len(data["jobs"])
                data["jobs"] = [j for j in data["jobs"] if j["docking_id"] != docking_id]
                if len(data["jobs"]) == before:
                    return False
                f.seek(0); f.truncate()
                json.dump(data, f, indent=2)
                f.flush(); os.fsync(f.fileno())
                return True
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
