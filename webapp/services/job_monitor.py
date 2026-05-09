"""Background job status poller for DrugCLIP (SSH/HPC edition).

Polls SLURM via SSH every POLL_INTERVAL seconds, updates the job store,
downloads results on COMPLETED, and emails the job owner.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone
from typing import Optional

from webapp.config import APP_BASE_URL, REMOTE_HOST, REMOTE_JOBS_DIR, REMOTE_USER
from webapp.modules.remote_server import RemoteServer
from webapp.services.job_store import JobStore
from webapp.services.slurm_client import SlurmClient

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({
    "COMPLETED", "FAILED", "CANCELLED", "TIMEOUT",
    "OUT_OF_MEMORY", "PREEMPTED", "NODE_FAIL",
})


class JobMonitor:
    """Background SLURM job status poller."""

    def __init__(self, slurm_client: SlurmClient, job_store: JobStore,
                 poll_interval: int = 120) -> None:
        self._slurm_client = slurm_client
        self._job_store = job_store
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._poll_loop, name="drugclip-job-monitor", daemon=True,
        )
        self._thread.start()
        logger.info("DrugCLIP JobMonitor started (poll_interval=%ds)", self._poll_interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._poll_interval + 5)
            self._thread = None

    def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            self.poll_once()
            self._stop_event.wait(timeout=self._poll_interval)

    def poll_once(self) -> None:
        """Poll SLURM for all active jobs and update the store."""
        try:
            active_jobs = self._job_store.get_active_jobs()
            if not active_jobs:
                return

            # Collect all SLURM IDs to query — for large-scale jobs use child IDs
            all_slurm_ids: set[str] = set()
            for job in active_jobs:
                if job.child_job_ids:
                    all_slurm_ids.update(job.child_job_ids)
                else:
                    all_slurm_ids.add(job.job_id)

            # squeue for currently queued/running
            squeue_results = self._slurm_client.squeue(job_ids=list(all_slurm_ids))
            squeue_map = {e["job_id"]: e["state"] for e in squeue_results}

            # sacct for finished
            missing_ids = [jid for jid in all_slurm_ids if jid not in squeue_map]
            sacct_map: dict[str, str] = {}
            if missing_ids:
                sacct_results = self._slurm_client.sacct(missing_ids)
                for e in sacct_results:
                    if "." not in e["job_id"]:
                        sacct_map[e["job_id"]] = e["state"]

            now = datetime.now(timezone.utc).isoformat()

            for record in active_jobs:
                if record.child_job_ids:
                    new_status = self._aggregate_child_status(
                        record.child_job_ids, squeue_map, sacct_map
                    )
                else:
                    jid = record.job_id
                    if jid in squeue_map:
                        new_status = self._normalize_status(squeue_map[jid])
                    elif jid in sacct_map:
                        new_status = self._normalize_status(sacct_map[jid])
                    else:
                        new_status = "FAILED"

                if new_status == record.status:
                    continue

                updates: dict = {"status": new_status, "updated_at": now}

                if new_status == "COMPLETED":
                    remote_results = f"{record.job_dir}/results.txt"
                    local_results = self._download_results(record.job_id, remote_results)
                    if local_results:
                        updates["results_path"] = local_results
                    else:
                        new_status = "FAILED"
                        updates["status"] = "FAILED"
                        updates["error_message"] = "Job completed but results.txt was not found on the HPC."

                elif new_status in ("FAILED", "TIMEOUT"):
                    error_msg = self._fetch_log_tail(record.log_path)
                    if error_msg:
                        updates["error_message"] = error_msg

                self._job_store.update_job(record.job_id, updates)
                logger.info("Job %s: %s -> %s (owner: %s)",
                            record.job_id, record.status, new_status, record.email)

                if new_status in TERMINAL_STATES and record.email:
                    self._send_notification(record, new_status)

        except Exception:
            logger.exception("Error in JobMonitor.poll_once()")

    def _aggregate_child_status(
        self,
        child_ids: list,
        squeue_map: dict,
        sacct_map: dict,
    ) -> str:
        """Derive overall job status from a set of child SLURM job IDs.

        Only considers IDs that actually appear in squeue or sacct.
        IDs not found in either are ignored (may be non-SLURM output noise).

        Rules (in priority order):
        - No known children found → RUNNING (still being submitted)
        - Any child RUNNING/PENDING → RUNNING
        - Any child FAILED/TIMEOUT/etc → FAILED
        - All known children COMPLETED → COMPLETED
        """
        statuses = []
        for jid in child_ids:
            if jid in squeue_map:
                statuses.append(self._normalize_status(squeue_map[jid]))
            elif jid in sacct_map:
                statuses.append(self._normalize_status(sacct_map[jid]))
            # IDs not found in squeue or sacct are ignored

        if not statuses:
            return "RUNNING"  # no children visible in SLURM yet

        if any(s in ("PENDING", "RUNNING") for s in statuses):
            return "RUNNING"
        if any(s in ("FAILED", "TIMEOUT", "CANCELLED") for s in statuses):
            return "FAILED"
        if all(s == "COMPLETED" for s in statuses):
            return "COMPLETED"
        return "RUNNING"

    def _download_results(self, job_id: str, remote_results: str) -> Optional[str]:
        """Download results.txt from the HPC to a local cache path."""
        import tempfile
        local_dir = os.path.join(tempfile.gettempdir(), "drugclip_results", job_id)
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, "results.txt")
        server = RemoteServer(REMOTE_HOST, REMOTE_USER)
        ok, err = server.download_file(remote_results, local_path)
        if ok:
            return local_path
        logger.warning("Could not download results for job %s: %s", job_id, err)
        return None

    def _fetch_log_tail(self, log_path: Optional[str], lines: int = 50) -> Optional[str]:
        """Fetch the last N lines of the SLURM log from the HPC."""
        if not log_path:
            return None
        server = RemoteServer(REMOTE_HOST, REMOTE_USER)
        out, _ = server.run_command(f"tail -n {lines} {log_path} 2>/dev/null")
        return out or None

    def _send_notification(self, record, status: str) -> None:
        """Send a completion/failure email to the job owner."""
        from webapp.modules.auth import send_email_via_hpc
        target = record.target_name
        library = record.library_name
        label = f"{target} vs {library}"

        if status == "COMPLETED":
            subject = f"[DrugCLIP] Screening complete: {label}"
            body = (
                f"Your DrugCLIP virtual screening job has completed.\n\n"
                f"Target:  {target}\n"
                f"Library: {library}\n"
                f"Job ID:  {record.job_id}\n\n"
                f"View results at:\n"
                f"{APP_BASE_URL}/jobs/{record.job_id}/results\n\n"
                f"— DrugCLIP Virtual Screening"
            )
        elif status == "CANCELLED":
            subject = f"[DrugCLIP] Job cancelled: {label}"
            body = (
                f"Your DrugCLIP job was cancelled.\n\n"
                f"Target:  {target}\n"
                f"Library: {library}\n"
                f"Job ID:  {record.job_id}\n\n"
                f"— DrugCLIP Virtual Screening"
            )
        else:
            subject = f"[DrugCLIP] Job failed: {label}"
            body = (
                f"Your DrugCLIP virtual screening job has failed.\n\n"
                f"Target:  {target}\n"
                f"Library: {library}\n"
                f"Job ID:  {record.job_id}\n"
                f"Status:  {status}\n\n"
                f"View details at:\n"
                f"{APP_BASE_URL}/jobs/{record.job_id}\n\n"
                f"If the problem persists, contact yewmun.yip@crick.ac.uk\n\n"
                f"— DrugCLIP Virtual Screening"
            )
        try:
            send_email_via_hpc(record.email, subject, body)
            logger.info("Sent %s email to %s for job %s", status, record.email, record.job_id)
        except Exception as exc:
            logger.warning("Failed to send email to %s: %s", record.email, exc)

    @staticmethod
    def _normalize_status(slurm_state: str) -> str:
        state = slurm_state.upper().rstrip("+")
        if state in ("PENDING", "CONFIGURING", "REQUEUED"):
            return "PENDING"
        elif state in ("RUNNING", "COMPLETING"):
            return "RUNNING"
        elif state == "COMPLETED":
            return "COMPLETED"
        elif state in ("FAILED", "NODE_FAIL", "PREEMPTED", "OUT_OF_MEMORY"):
            return "FAILED"
        elif state == "CANCELLED":
            return "CANCELLED"
        elif state in ("TIMEOUT", "DEADLINE"):
            return "TIMEOUT"
        return "FAILED"

    def get_job_status(self, job_id: str) -> Optional[str]:
        record = self._job_store.get_job(job_id)
        return record.status if record else None
