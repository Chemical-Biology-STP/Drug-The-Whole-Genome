"""Results viewing and download route handlers.

Provides the results blueprint with routes for viewing paginated screening
results and downloading the raw results file.

Requirements: 10.1, 10.2, 10.3, 10.4, 11.4
"""

from __future__ import annotations

import io
import logging
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

from webapp.config import PROJECT_ROOT, RESULTS_PER_PAGE
from webapp.services.job_store import JobStore
from webapp.services.results_parser import paginate, parse_results

logger = logging.getLogger(__name__)

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


def _resolve_path(path: str) -> str:
    """Resolve a potentially relative path against the project root."""
    if os.path.isabs(path):
        return path
    return os.path.join(PROJECT_ROOT, path)


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
    results = parse_results(_resolve_path(record.results_path))

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
        _resolve_path(record.results_path),
        mimetype="text/csv",
        as_attachment=True,
        download_name="results.txt",
    )


@results_bp.route("/<job_id>/results/download_sdf", methods=["GET"])
def download_sdf(job_id: str):
    """Generate and serve a 3D multi-molecule SDF for docking.

    Reads the results file, converts each SMILES to a 3D conformer using
    RDKit ETKDGv3, and streams the SDF back as a download. Molecules that
    fail conformer generation are skipped with a warning in the log.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem, Descriptors
    except ImportError:
        flash("RDKit is not available — cannot generate SDF.", "danger")
        return redirect(url_for("results.view", job_id=job_id))

    results = parse_results(_resolve_path(record.results_path))

    sdf_buffer = io.StringIO()
    writer = Chem.SDWriter(sdf_buffer)

    n_ok = 0
    n_fail = 0
    for rank, smiles, score in results:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            n_fail += 1
            continue

        mol = Chem.AddHs(mol)
        params = AllChem.ETKDGv3()
        params.randomSeed = 42
        result = AllChem.EmbedMolecule(mol, params)

        if result == -1:
            # ETKDGv3 failed — try distance geometry fallback
            result = AllChem.EmbedMolecule(mol, AllChem.ETKDG())

        if result == -1:
            logger.warning("Conformer generation failed for rank %d: %s", rank, smiles)
            n_fail += 1
            continue

        AllChem.MMFFOptimizeMolecule(mol)
        mol = Chem.RemoveHs(mol)

        mol.SetProp("_Name", f"rank_{rank}")
        mol.SetProp("SMILES", smiles)
        mol.SetProp("DrugCLIP_Score", f"{score:.6f}")
        mol.SetProp("Rank", str(rank))

        writer.write(mol)
        n_ok += 1

    writer.close()

    if n_ok == 0:
        flash("No valid 3D structures could be generated from the results.", "danger")
        return redirect(url_for("results.view", job_id=job_id))

    if n_fail > 0:
        logger.warning("SDF download: %d molecules failed conformer generation", n_fail)

    sdf_bytes = sdf_buffer.getvalue().encode("utf-8")
    download_name = f"{record.target_name}_vs_{record.library_name}_hits.sdf"

    response = send_file(
        io.BytesIO(sdf_bytes),
        mimetype="chemical/x-mdl-sdfile",
        as_attachment=True,
        download_name=download_name,
    )
    # Cookie lets the client-side modal know the file is ready
    response.set_cookie("sdf_ready", "1", max_age=10, samesite="Lax")
    return response


@results_bp.route("/<job_id>/results/download_pdbqt", methods=["GET"])
def download_pdbqt(job_id: str):
    """Generate and serve a multi-molecule PDBQT for AutoDock/Vina docking.

    Converts each SMILES hit to a 3D conformer via RDKit, then runs
    AutoDockTools prepare_ligand4 on each molecule. All PDBQT blocks are
    concatenated into a single file.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)
    if record.session_id != session_id:
        abort(403)

    if record.status != "COMPLETED" or not record.results_path:
        flash("Results are not yet available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    from webapp.services.pdbqt_converter import smiles_list_to_pdbqt

    results = parse_results(_resolve_path(record.results_path))

    try:
        pdbqt_bytes, n_ok, n_fail = smiles_list_to_pdbqt(results)
    except RuntimeError as e:
        flash(str(e), "danger")
        return redirect(url_for("results.view", job_id=job_id))

    if n_ok == 0:
        flash("No valid PDBQT structures could be generated.", "danger")
        return redirect(url_for("results.view", job_id=job_id))

    if n_fail > 0:
        logger.warning("PDBQT download: %d molecules failed conversion", n_fail)

    download_name = f"{record.target_name}_vs_{record.library_name}_hits.pdbqt"
    response = send_file(
        io.BytesIO(pdbqt_bytes),
        mimetype="chemical/x-pdbqt",
        as_attachment=True,
        download_name=download_name,
    )
    response.set_cookie("pdbqt_ready", "1", max_age=10, samesite="Lax")
    return response
