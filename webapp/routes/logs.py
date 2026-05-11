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
        # Build one shell command that does ls + tail in a single SSH connection
        child_greps = " ".join(
            f"_{cid}_\\|_{cid}\\.log" for cid in record.child_job_ids
        )
        cmd = (
            f"cd {REMOTE_JOBS_DIR}/logs && "
            f"for f in $(ls 2>/dev/null | grep -E "
            f"'(convert|encode|screen)' | grep -E "
            f"'({'|'.join(record.child_job_ids)})' | sort); do "
            f"echo \"=== $f ===\"; tail -n 15 \"$f\"; echo; done"
        )
        out, _ = server.run_command(cmd)
        if out and out.strip():
            log_content = out

    # Fallback: try the primary log path (standard / screen job)
    if not log_content and record.log_path:
        out, _ = server.run_command(f"tail -n 200 {record.log_path} 2>/dev/null")
        if out and out.strip():
            log_content = out

    return jsonify({"log": log_content or "Log file not available yet — job may still be starting."})
