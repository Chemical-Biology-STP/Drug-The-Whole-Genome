"""SLURM log viewing route handler — fetches log from HPC via SSH."""

from __future__ import annotations

import os

from flask import Blueprint, abort, jsonify, session

from webapp.config import ADMIN_EMAIL, REMOTE_HOST, REMOTE_USER
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
    """Return SLURM log file contents as JSON, fetched from the HPC via SSH."""
    email = _get_email()
    if not email:
        abort(403)

    record = _get_job_store().get_job(job_id)
    if record is None:
        abort(404)
    if not _owns_record(record, email):
        abort(403)

    log_content = "Log file not available."
    if record.log_path:
        server = RemoteServer(REMOTE_HOST, REMOTE_USER)
        out, _ = server.run_command(f"tail -n 200 {record.log_path} 2>/dev/null")
        if out:
            log_content = out

    return jsonify({"log": log_content})
