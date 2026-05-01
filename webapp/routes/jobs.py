"""Job submission, detail, and cancellation route handlers.

Provides the jobs blueprint with routes for submitting screening jobs,
viewing job details, and cancelling active jobs.

Requirements: 3.4, 3.5, 4.6, 7.4, 7.5, 7.6, 8.1-8.4, 9.4, 11.4, 12.4, 15.1-15.4
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

from webapp.config import PROJECT_ROOT, list_available_libraries
from webapp.services.file_upload import FileUploadHandler, ValidationError
from webapp.services.job_store import JobStore
from webapp.services.job_submission import AuthorizationError, JobSubmissionService
from webapp.services.models import JobParams
from webapp.services.slurm_client import SlurmClient, SlurmError
from webapp.services.validation import (
    derive_target_name,
    validate_binding_site,
    validate_file_extension,
    validate_params,
)

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


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


def _get_submission_service() -> JobSubmissionService:
    """Instantiate a JobSubmissionService with its dependencies."""
    slurm_client = SlurmClient()
    job_store = _get_job_store()
    return JobSubmissionService(slurm_client, job_store, PROJECT_ROOT)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@jobs_bp.route("/submit", methods=["POST"])
def submit():
    """Handle job submission form.

    Validates form inputs, uploads files, builds JobParams, and submits
    the job via the appropriate service method. On success, flashes a
    confirmation message and redirects to the job detail page. On failure,
    flashes error messages and redirects back to the dashboard.
    """
    session_id = _get_session_id()
    upload_handler = FileUploadHandler()
    errors: list[str] = []

    # ------------------------------------------------------------------
    # 1. Validate and upload required files
    # ------------------------------------------------------------------

    # PDB file (required)
    pdb_file = request.files.get("pdb_file")
    pdb_path: str | None = None
    if not pdb_file or not pdb_file.filename:
        errors.append("Receptor PDB file is required. Accepted format: .pdb")
    else:
        if not validate_file_extension(pdb_file.filename, "pdb"):
            errors.append("Receptor PDB file is required. Accepted format: .pdb")
        else:
            try:
                pdb_path = upload_handler.validate_and_save(
                    pdb_file, session_id, "pdb"
                )
            except ValidationError as e:
                errors.append(str(e))

    # Library source: either upload a file or select a pre-encoded library
    library_source = request.form.get("library_source", "upload")
    library_path: str | None = None
    use_preencoded_library = False
    preencoded_library_name: str | None = None
    cache_dir: str | None = None

    if library_source == "preencoded":
        # User selected a pre-built library from the server
        selected_lib = request.form.get("preencoded_library", "").strip()
        if not selected_lib:
            errors.append("Please select a pre-encoded library.")
        else:
            # Look up the library in the available list
            available = {lib["name"]: lib for lib in list_available_libraries()}
            if selected_lib not in available:
                errors.append(f"Unknown library: {selected_lib!r}. Please select from the list.")
            else:
                lib_info = available[selected_lib]
                library_path = lib_info["lmdb_path"]
                use_preencoded_library = lib_info["has_cache"]
                preencoded_library_name = selected_lib
                # Prefer 6_folds cache; fall back to 8_folds
                cache_dir = lib_info["cache_dirs"].get("6_folds") or \
                            lib_info["cache_dirs"].get("8_folds")
    else:
        # Upload mode
        library_file = request.files.get("library_file")
        if not library_file or not library_file.filename:
            errors.append(
                "Compound library is required. Accepted formats: .sdf, .smi, .smiles, .txt"
            )
        else:
            if not validate_file_extension(library_file.filename, "library"):
                errors.append(
                    "Compound library is required. Accepted formats: .sdf, .smi, .smiles, .txt"
                )
            else:
                try:
                    library_path = upload_handler.validate_and_save(
                        library_file, session_id, "library"
                    )
                except ValidationError as e:
                    errors.append(str(e))

    # ------------------------------------------------------------------
    # 2. Validate binding site method and fields
    # ------------------------------------------------------------------

    binding_site_method = request.form.get("binding_site_method", "")
    ligand_path: str | None = None
    residue_name: str | None = None
    center_x: float | None = None
    center_y: float | None = None
    center_z: float | None = None
    binding_residues: str | None = None
    chain_id: str | None = None

    if not binding_site_method:
        errors.append(
            "A binding site definition is required. Choose one of the four methods."
        )
    else:
        if binding_site_method == "ligand":
            ligand_file = request.files.get("ligand_file")
            if not ligand_file or not ligand_file.filename:
                errors.append(
                    "Ligand file is required for this binding site method. "
                    "Accepted formats: .pdb, .sdf"
                )
            else:
                if not validate_file_extension(ligand_file.filename, "ligand"):
                    errors.append(
                        "Ligand file is required for this binding site method. "
                        "Accepted formats: .pdb, .sdf"
                    )
                else:
                    try:
                        ligand_path = upload_handler.validate_and_save(
                            ligand_file, session_id, "ligand"
                        )
                    except ValidationError as e:
                        errors.append(str(e))

        elif binding_site_method == "residue":
            residue_name = request.form.get("residue_name", "").strip()
            if not residue_name:
                errors.append("Residue name is required (e.g., JHN).")

        elif binding_site_method == "center":
            try:
                center_x = float(request.form.get("center_x", ""))
                center_y = float(request.form.get("center_y", ""))
                center_z = float(request.form.get("center_z", ""))
            except (ValueError, TypeError):
                errors.append(
                    "All three coordinates (X, Y, Z) are required and must be numbers."
                )

        elif binding_site_method == "binding_residues":
            binding_residues = request.form.get("binding_residues", "").strip()
            if not binding_residues:
                errors.append("At least one residue number is required.")
            chain_id = request.form.get("chain_id", "").strip() or None

        else:
            errors.append(
                "A binding site definition is required. Choose one of the four methods."
            )

    # ------------------------------------------------------------------
    # 3. Validate numeric parameters
    # ------------------------------------------------------------------

    screening_mode = request.form.get("screening_mode", "standard")

    try:
        cutoff = float(request.form.get("cutoff", "10.0"))
    except (ValueError, TypeError):
        cutoff = -1.0  # Will fail validation

    try:
        top_fraction = float(request.form.get("top_fraction", "0.02"))
    except (ValueError, TypeError):
        top_fraction = -1.0  # Will fail validation

    try:
        chunk_size = int(request.form.get("chunk_size", "1000000"))
    except (ValueError, TypeError):
        chunk_size = 0  # Will fail validation

    param_errors = validate_params(cutoff, top_fraction, chunk_size)
    for field_name, msg in param_errors.items():
        errors.append(msg)

    # ------------------------------------------------------------------
    # 4. If there are validation errors, flash them and redirect back
    # ------------------------------------------------------------------

    if errors:
        for err in errors:
            flash(err, "danger")
        return redirect(url_for("dashboard.index"))

    # ------------------------------------------------------------------
    # 5. Build JobParams and submit
    # ------------------------------------------------------------------

    target_name = request.form.get("target_name", "").strip()
    if not target_name and pdb_path:
        target_name = derive_target_name(pdb_path)

    params = JobParams(
        session_id=session_id,
        pdb_path=pdb_path,  # type: ignore[arg-type]
        library_path=library_path,  # type: ignore[arg-type]
        binding_site_method=binding_site_method,
        ligand_path=ligand_path,
        residue_name=residue_name,
        center_x=center_x,
        center_y=center_y,
        center_z=center_z,
        binding_residues=binding_residues,
        chain_id=chain_id,
        cutoff=cutoff,
        target_name=target_name or None,
        top_fraction=top_fraction,
        screening_mode=screening_mode,
        chunk_size=chunk_size,
        partition=request.form.get("partition", "ga100"),
        max_parallel=int(request.form.get("max_parallel", "50")),
        use_preencoded_library=use_preencoded_library,
        preencoded_library_name=preencoded_library_name,
        cache_dir=cache_dir,
    )

    submission_service = _get_submission_service()

    try:
        if screening_mode == "large_scale":
            record = submission_service.submit_large_scale(params)
        else:
            record = submission_service.submit_standard(params)
    except SlurmError as e:
        flash(f"Job submission failed: {e.stderr}", "danger")
        return redirect(url_for("dashboard.index"))

    flash(f"Job submitted successfully! SLURM Job ID: {record.job_id}", "success")
    return redirect(url_for("jobs.detail", job_id=record.job_id))


@jobs_bp.route("/<job_id>", methods=["GET"])
def detail(job_id: str):
    """Display the job detail page.

    Verifies session ownership and renders the job detail template with
    all submission parameters, current status, and action links.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    return render_template("job_detail.html", job=record)


@jobs_bp.route("/<job_id>/cancel", methods=["POST"])
def cancel(job_id: str):
    """Cancel a PENDING or RUNNING job.

    Verifies session ownership, calls scancel via the submission service,
    and redirects back to the job detail page with a status message.
    """
    session_id = _get_session_id()
    submission_service = _get_submission_service()

    try:
        submission_service.cancel_job(job_id, session_id)
    except AuthorizationError:
        abort(403)
    except SlurmError as e:
        flash(f"Could not cancel job: {e.stderr}", "danger")
        return redirect(url_for("jobs.detail", job_id=job_id))

    flash("Job cancelled.", "success")
    return redirect(url_for("jobs.detail", job_id=job_id))


@jobs_bp.route("/<job_id>/delete", methods=["POST"])
def delete(job_id: str):
    """Delete a job record from the store.

    Only completed, failed, cancelled, or timed-out jobs may be deleted.
    Active jobs must be cancelled first. Verifies session ownership before
    removing the record.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)

    if record.session_id != session_id:
        abort(403)

    if record.status in ("PENDING", "RUNNING"):
        flash("Cancel the job before deleting it.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    job_store.delete_job(job_id)
    flash(f"Job {job_id} deleted.", "success")
    return redirect(url_for("dashboard.index"))


@jobs_bp.route("/<job_id>/receptor_pdbqt", methods=["GET"])
def receptor_pdbqt(job_id: str):
    """Convert the receptor PDB to PDBQT and serve as a download.

    Uses AutoDockTools prepare_receptor4 to add hydrogens, remove waters
    and non-standard residues, and assign Gasteiger charges.
    """
    session_id = _get_session_id()
    job_store = _get_job_store()
    record = job_store.get_job(job_id)

    if record is None:
        abort(404)
    if record.session_id != session_id:
        abort(403)

    pdb_path = record.params.get("pdb_path")
    if not pdb_path or not os.path.exists(pdb_path):
        flash("Receptor PDB file is no longer available.", "danger")
        return redirect(url_for("jobs.detail", job_id=job_id))

    from webapp.services.pdbqt_converter import receptor_pdb_to_pdbqt
    import io as _io

    try:
        pdbqt_bytes = receptor_pdb_to_pdbqt(pdb_path)
    except Exception as e:
        flash(f"PDBQT conversion failed: {e}", "danger")
        return redirect(url_for("jobs.detail", job_id=job_id))

    download_name = f"{record.target_name}_receptor.pdbqt"
    return send_file(
        _io.BytesIO(pdbqt_bytes),
        mimetype="chemical/x-pdbqt",
        as_attachment=True,
        download_name=download_name,
    )
