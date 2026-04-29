"""Background job monitor that polls SLURM for status updates.

Runs a daemon thread that periodically queries squeue/sacct for active jobs
and updates the job store accordingly. Designed to never crash — all errors
in poll_once() are caught and logged.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from webapp.services.job_store import JobStore
from webapp.services.slurm_client import SlurmClient

logger = logging.getLogger(__name__)


class JobMonitor:
    """Background SLURM job status poller.

    Periodically queries SLURM for the status of all active (PENDING/RUNNING)
    jobs and updates the job store with the latest state. On completion, sets
    the results_path; on failure/timeout, captures the last 50 lines of the
    log file as an error message.

    Parameters
    ----------
    slurm_client:
        The SlurmClient instance used to query squeue/sacct.
    job_store:
        The JobStore instance used to read/write job records.
    poll_interval:
        How often (in seconds) to poll SLURM. Default is 30.
    """

    def __init__(
        self,
        slurm_client: SlurmClient,
        job_store: JobStore,
        poll_interval: int = 30,
    ) -> None:
        self._slurm_client = slurm_client
        self._job_store = job_store
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the background polling thread.

        Launches a daemon thread that runs _poll_loop(). If the monitor is
        already running, this is a no-op.
        """
        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop,
            name="job-monitor",
            daemon=True,
        )
        self._thread.start()
        logger.info("JobMonitor started (poll_interval=%ds)", self._poll_interval)

    def stop(self) -> None:
        """Stop the background polling thread.

        Sets the stop event and waits for the thread to finish. Safe to call
        even if the monitor is not running.
        """
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 5)
            self._thread = None
        logger.info("JobMonitor stopped")

    def _poll_loop(self) -> None:
        """Main loop for the background thread.

        Calls poll_once() at the configured interval, breaking when the stop
        event is set.
        """
        while not self._stop_event.is_set():
            self.poll_once()
            # Wait for the poll interval, but break early if stop is signaled
            self._stop_event.wait(timeout=self._poll_interval)

    def poll_once(self) -> None:
        """Poll SLURM for all active jobs and update the job store.

        Steps:
        1. Fetch active jobs (PENDING or RUNNING) from the store.
        2. Query squeue for those job IDs to get current state.
        3. For jobs not found in squeue, query sacct for final status.
        4. Update job statuses in the store.
        5. Set results_path on COMPLETED jobs.
        6. Set error_message on FAILED/TIMEOUT jobs (last 50 lines of log).

        All exceptions are caught and logged so the thread never crashes.
        """
        try:
            active_jobs = self._job_store.get_active_jobs()
            if not active_jobs:
                return

            job_ids = [job.job_id for job in active_jobs]

            # Query squeue for currently queued/running jobs
            squeue_results = self._slurm_client.squeue(job_ids=job_ids)
            squeue_map = {entry["job_id"]: entry["state"] for entry in squeue_results}

            # Identify jobs not found in squeue (likely finished)
            missing_ids = [jid for jid in job_ids if jid not in squeue_map]

            # Query sacct for finished jobs
            sacct_map: dict[str, str] = {}
            if missing_ids:
                sacct_results = self._slurm_client.sacct(missing_ids)
                for entry in sacct_results:
                    # sacct may return sub-job entries (e.g., "12345.batch")
                    # We only care about the main job entry
                    raw_id = entry["job_id"]
                    if "." not in raw_id:
                        sacct_map[raw_id] = entry["state"]

            # Build a lookup from job_id to JobRecord for convenience
            job_record_map = {job.job_id: job for job in active_jobs}

            now = datetime.now(timezone.utc).isoformat()

            # Update each job based on squeue or sacct results
            for job_id in job_ids:
                new_status: Optional[str] = None

                if job_id in squeue_map:
                    new_status = squeue_map[job_id]
                elif job_id in sacct_map:
                    new_status = sacct_map[job_id]
                else:
                    # Job not found in squeue or sacct — mark as FAILED
                    new_status = "FAILED"

                if new_status is None:
                    continue

                record = job_record_map[job_id]

                # Normalize SLURM state names
                new_status = self._normalize_status(new_status)

                # Skip if status hasn't changed
                if new_status == record.status:
                    continue

                updates: dict = {
                    "status": new_status,
                    "updated_at": now,
                }

                if new_status == "COMPLETED":
                    # Set results_path to <job_dir>/results.txt
                    updates["results_path"] = os.path.join(
                        record.job_dir, "results.txt"
                    )

                elif new_status in ("FAILED", "TIMEOUT"):
                    # Try to read last 50 lines of the log file
                    error_msg = self._read_log_tail(record.log_path)
                    if error_msg:
                        updates["error_message"] = error_msg

                self._job_store.update_job(job_id, updates)
                logger.info(
                    "Job %s status updated: %s -> %s",
                    job_id,
                    record.status,
                    new_status,
                )

        except Exception:
            # Never let the polling thread crash
            logger.exception("Error in JobMonitor.poll_once()")

    def get_job_status(self, job_id: str) -> Optional[str]:
        """Get the current status of a specific job from the store.

        Parameters
        ----------
        job_id:
            The SLURM job ID to look up.

        Returns
        -------
        str or None
            The current status string, or None if the job is not found.
        """
        record = self._job_store.get_job(job_id)
        if record is None:
            return None
        return record.status

    @staticmethod
    def _normalize_status(slurm_state: str) -> str:
        """Normalize a SLURM state string to one of our recognized statuses.

        SLURM can report states like COMPLETING, CANCELLED+, etc. We map
        them to our canonical set: PENDING, RUNNING, COMPLETED, FAILED,
        CANCELLED, TIMEOUT.
        """
        state = slurm_state.upper().rstrip("+")

        if state in ("PENDING", "CONFIGURING", "REQUEUED"):
            return "PENDING"
        elif state in ("RUNNING", "COMPLETING"):
            return "RUNNING"
        elif state == "COMPLETED":
            return "COMPLETED"
        elif state in ("FAILED", "NODE_FAIL", "PREEMPTED", "OUT_OF_MEMORY"):
            return "FAILED"
        elif state in ("CANCELLED",):
            return "CANCELLED"
        elif state in ("TIMEOUT", "DEADLINE"):
            return "TIMEOUT"
        else:
            # Unknown state — treat as FAILED to be safe
            return "FAILED"

    @staticmethod
    def _read_log_tail(log_path: Optional[str], lines: int = 50) -> Optional[str]:
        """Read the last N lines of a log file.

        Parameters
        ----------
        log_path:
            Path to the SLURM log file. If None or the file doesn't exist,
            returns None.
        lines:
            Number of lines to read from the end. Default is 50.

        Returns
        -------
        str or None
            The last N lines of the log file, or None if unavailable.
        """
        if not log_path or not os.path.isfile(log_path):
            return None

        try:
            with open(log_path, "r") as f:
                all_lines = f.readlines()
                tail = all_lines[-lines:]
                return "".join(tail)
        except (OSError, IOError):
            return None
