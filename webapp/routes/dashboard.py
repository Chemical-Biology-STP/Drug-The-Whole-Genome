"""Dashboard route handler for the DrugCLIP web application."""

from __future__ import annotations

import os

from flask import Blueprint, render_template, session

from webapp.services.job_store import JobStore

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="")


def _get_job_store() -> JobStore:
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


@dashboard_bp.route("/", methods=["GET"])
def index():
    """Render the dashboard with submission form and job list."""
    email = session.get("email", "")
    job_store = _get_job_store()

    from webapp.config import ADMIN_EMAIL
    if email.lower() == ADMIN_EMAIL.lower():
        jobs = job_store.get_all_jobs()
    else:
        jobs = job_store.get_jobs_for_user(email)

    # Also load docking jobs
    import os
    from webapp.services.docking_store import DockingStore
    docking_store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "docking_jobs.json")
    docking_store = DockingStore(docking_store_path)
    if email.lower() == ADMIN_EMAIL.lower():
        docking_jobs = docking_store.get_all()
    else:
        docking_jobs = docking_store.get_for_user(email)

    return render_template("dashboard.html", jobs=jobs, docking_jobs=docking_jobs,
                           current_user=email)
