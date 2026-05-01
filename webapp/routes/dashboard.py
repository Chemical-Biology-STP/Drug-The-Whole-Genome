"""Dashboard route handler for the DrugCLIP web application.

Serves the main dashboard page with the job submission form and the
user's job list.

Requirements: 2.1
"""

from __future__ import annotations

import math
import os
import uuid

from flask import Blueprint, render_template, request, session

from webapp.config import JOBS_PER_PAGE, list_available_libraries
from webapp.services.job_store import JobStore

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="")


def _get_job_store() -> JobStore:
    """Get the JobStore instance."""
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


@dashboard_bp.route("/", methods=["GET"])
def index():
    """Render the dashboard with submission form and paginated job list."""
    if "id" not in session:
        session["id"] = str(uuid.uuid4())

    session_id = session.get("id")
    job_store = _get_job_store()
    all_jobs = job_store.get_jobs_for_session(session_id)

    # Paginate the job list
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    total_jobs = len(all_jobs)
    total_pages = math.ceil(total_jobs / JOBS_PER_PAGE) if total_jobs > 0 else 1
    page = min(page, total_pages)

    start = (page - 1) * JOBS_PER_PAGE
    jobs = all_jobs[start:start + JOBS_PER_PAGE]

    pagination = {
        "page": page,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "total_jobs": total_jobs,
    }

    available_libraries = list_available_libraries()

    return render_template(
        "dashboard.html",
        jobs=jobs,
        pagination=pagination,
        available_libraries=available_libraries,
    )
