"""Results viewing and download route handlers."""

from __future__ import annotations

import os

from flask import (
    Blueprint, abort, flash, redirect, render_template,
    request, send_file, session, url_for,
)

from webapp.config import ADMIN_EMAIL, RESULTS_PER_PAGE
from webapp.services.job_store import JobStore
from webapp.services.results_parser import paginate, parse_results

results_bp = Blueprint("results", __name__, url_prefix="/jobs")


def _get_email() -> str:
    return session.get("email", "")


def _get_job_store() -> JobStore:
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


def _owns_record(record, email: str) -> bool:
    return record.email == email or email.lower() == ADMIN_EMAIL.lower()


@results_bp.route("/<job_id>/results", methods=["GET"])
def view(job_id: str):
    email = _get_email()
    if not email:
        flash("Please log in.", "danger")
        return redirect(url_for("login"))

    record = _get_job_store().get_job(job_id)
    if record is None:
        abort(404)
    if not _owns_record(record, email):
        abort(403)

    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    results = parse_results(record.results_path)
    try:
        page = int(request.args.get("page", 1))
    except (ValueError, TypeError):
        page = 1
    pagination = paginate(results, page, RESULTS_PER_PAGE)

    return render_template("results.html", job=record, pagination=pagination,
                           current_user=email)


@results_bp.route("/<job_id>/results/download", methods=["GET"])
def download(job_id: str):
    email = _get_email()
    if not email:
        abort(403)

    record = _get_job_store().get_job(job_id)
    if record is None:
        abort(404)
    if not _owns_record(record, email):
        abort(403)

    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    return send_file(
        record.results_path,
        mimetype="text/csv",
        as_attachment=True,
        download_name="results.txt",
    )
