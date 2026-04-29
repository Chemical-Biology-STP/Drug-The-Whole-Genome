"""Results viewing and download route handlers.

Provides the results blueprint with routes for viewing paginated screening
results and downloading the raw results file.

Requirements: 10.1, 10.2, 10.3, 10.4, 11.4
"""

from __future__ import annotations

import os
import uuid

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from webapp.config import RESULTS_PER_PAGE
from webapp.services.job_store import JobStore
from webapp.services.results_parser import paginate, parse_results

results_bp = Blueprint("results", __name__, url_prefix="/jobs")


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


@results_bp.route("/<job_id>/results", methods=["GET"])
def view(job_id: str):
    """Display paginated screening results for a completed job.

    Verifies session ownership, checks that the job is COMPLETED with a
    results file, parses the results, paginates them, and renders the
    results template.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    # Ensure job is completed and has results
    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    # Parse results file
    results = parse_results(record.results_path)

    # Get page number from query parameter
    try:
        page = int(request.args.get("page", 1))
    except (ValueError, TypeError):
        page = 1

    # Paginate results
    pagination = paginate(results, page, RESULTS_PER_PAGE)

    return render_template(
        "results.html",
        job=record,
        pagination=pagination,
    )


@results_bp.route("/<job_id>/results/download", methods=["GET"])
def download(job_id: str):
    """Serve the results file as a downloadable CSV.

    Verifies session ownership and serves the raw results file with
    appropriate Content-Type and Content-Disposition headers.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    # Ensure job is completed and has results
    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    return send_file(
        record.results_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="results.txt",
    )
