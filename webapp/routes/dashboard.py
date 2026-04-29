"""Dashboard route handler for the DrugCLIP web application.

Serves the main dashboard page with the job submission form and the
user's job list.

Requirements: 2.1
"""

from __future__ import annotations

import os
import uuid

from flask import Blueprint, render_template, session

from webapp.services.job_store import JobStore

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="")


def _get_job_store() -> JobStore:
    """Get the JobStore instance.

    Returns a JobStore pointed at the canonical data/jobs.json path.
    """
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


@dashboard_bp.route("/", methods=["GET"])
def index():
    """Render the dashboard with submission form and job list.

    Creates a session ID (UUID4) if one does not already exist, then
    fetches the user's jobs from the job store and renders the dashboard
    template.
    """
    # Ensure the session has a unique ID
    if "id" not in session:
        session["id"] = str(uuid.uuid4())

    session_id = session.get("id")
    job_store = _get_job_store()
    jobs = job_store.get_jobs_for_session(session_id)

    return render_template("dashboard.html", jobs=jobs)
