"""SLURM log viewing route handler — fetches log from HPC via SSH."""

from __future__ import annotations

import os

from flask import Blueprint, abort, jsonify, session

from webapp.config import ADMIN_EMAIL, REMOTE_HOST, REMOTE_JOBS_DIR, REMOTE_USER
from webapp.modules.remote_server import RemoteServer
from webapp.services.job_store import JobStore

logs_bp = Blueprint("logs", __name__, url_prefix="/jobs")


def _get_email() -> str:
    return session.get("email", "")


def _get_job_store() -> JobStore:
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


def _owns_record(record, email: str) -> bool:
    return record.email == email or email.lower() == ADMIN_EMAIL.lower()


@logs_bp.route("/<job_id>/log", methods=["GET"])
def view(job_id: str):
    """Return SLURM log file contents as JSON, fetched from the HPC via SSH.

    For large-scale jobs the primary job_id has no SLURM log. In that case
    we collect logs from all child array tasks and show a progress summary.
    """
    email = _get_email()
    if not email:
        abort(403)

    record = _get_job_store().get_job(job_id)
    if record is None:
        abort(404)
    if not _owns_record(record, email):
        abort(403)

    server = RemoteServer(REMOTE_HOST, REMOTE_USER)
    log_content = None

    # For large-scale jobs, build a structured progress view from all child logs
    if record.child_job_ids:
        parts = []

        # List all relevant log files once
        ls_out, _ = server.run_command(f"ls {REMOTE_JOBS_DIR}/logs/ 2>/dev/null")
        all_logs = [l.strip() for l in (ls_out or "").splitlines() if l.strip()]

        for child_id in record.child_job_ids:
            # Match convert_<id>_<task>.log, encode_<id>_<task>.log, screen_..._<id>.log
            matching = sorted([
                l for l in all_logs
                if (f"_{child_id}_" in l or l.endswith(f"_{child_id}.log"))
                and l.endswith(".log")
            ])
            if not matching:
                continue

            # Determine stage label
            if matching[0].startswith("convert_"):
                stage = "Stage 3 — Convert to LMDB"
            elif matching[0].startswith("encode_"):
                stage = "Stage 4 — Encode embeddings"
            elif matching[0].startswith("screen_"):
                stage = "Stage 5 — Screening"
            else:
                stage = f"Job {child_id}"

            # Show tail of each task log (last 15 lines each, up to 6 tasks)
            task_parts = []
            for logfile in matching[:6]:
                tail_out, _ = server.run_command(
                    f"tail -n 15 {REMOTE_JOBS_DIR}/logs/{logfile} 2>/dev/null"
                )
                if tail_out and tail_out.strip():
                    task_parts.append(f"  [{logfile}]\n{tail_out.rstrip()}")

            if task_parts:
                parts.append(f"{'='*60}\n{stage} (SLURM {child_id})\n{'='*60}\n" +
                              "\n\n".join(task_parts))

        if parts:
            log_content = "\n\n".join(parts)

    # Fallback: try the primary log path (standard / screen job)
    if not log_content and record.log_path:
        out, _ = server.run_command(f"tail -n 200 {record.log_path} 2>/dev/null")
        if out and out.strip():
            log_content = out

    return jsonify({"log": log_content or "Log file not available yet — job may still be starting."})
