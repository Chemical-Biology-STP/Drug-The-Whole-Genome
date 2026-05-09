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
    we concatenate the most recent logs from the child jobs instead.
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

    # Try the primary log path first
    if record.log_path:
        out, _ = server.run_command(f"tail -n 200 {record.log_path} 2>/dev/null")
        if out and out.strip():
            log_content = out

    # For large-scale jobs (or when primary log is missing), collect child logs
    if not log_content and record.child_job_ids:
        parts = []
        for child_id in record.child_job_ids:
            # Try the per-array log pattern: logs/<prefix>_<array_id>_<task>.log
            # and the simple pattern: logs/slurm_<id>.log
            cmd = (
                f"ls {REMOTE_JOBS_DIR}/logs/ 2>/dev/null"
                f" | grep -E '^(convert|encode|screen).*{child_id}|slurm_{child_id}'"
                f" | sort | tail -3"
                f" | xargs -I{{}} tail -n 50 {REMOTE_JOBS_DIR}/logs/{{}} 2>/dev/null"
            )
            out, _ = server.run_command(cmd)
            if out and out.strip():
                parts.append(f"=== Child job {child_id} ===\n{out}")

        if parts:
            log_content = "\n\n".join(parts)

    return jsonify({"log": log_content or "Log file not available yet — job may still be starting."})
