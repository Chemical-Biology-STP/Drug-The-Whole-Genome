"""SLURM log viewing route handler.

Provides the logs blueprint with a route for fetching SLURM log file
contents as JSON, supporting AJAX-based log refresh from the job detail page.

Requirements: 14.1, 14.2, 14.3
"""

from __future__ import annotations

import os
import uuid

from flask import Blueprint, abort, jsonify, session

from webapp.services.job_store import JobStore

logs_bp = Blueprint("logs", __name__, url_prefix="/jobs")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_session_id() -> str:
    """Return the current session ID, creating one if it does not exist."""
    if "id" not in session:
        session["id"] = str(uuid.uuid4())
    return session["id"]


def _get_job_store() -> JobStore:
    """Instantiate a JobStore pointed at the canonical data path."""
    store_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json"
    )
    return JobStore(store_path)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@logs_bp.route("/<job_id>/log", methods=["GET"])
def view(job_id: str):
    """Return SLURM log file contents as JSON for AJAX refresh.

    Verifies session ownership, reads the log file from disk, and returns
    the content wrapped in a JSON object. If the log file does not exist
    or cannot be read, returns a placeholder message.

    Returns
    -------
    Response
        JSON response with shape ``{"log": "<content>"}``
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    # Attempt to read the SLURM log file
    log_content = "Log file not available."

    if record.log_path:
        try:
            with open(record.log_path, "r") as f:
                log_content = f.read()
        except (OSError, IOError):
            log_content = "Log file not available."

    return jsonify({"log": log_content})
