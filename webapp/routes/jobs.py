"""Job submission, detail, and cancellation route handlers."""

from __future__ import annotations

import os

from flask import (
    Blueprint, abort, flash, redirect, render_template,
    request, session, url_for,
)

from webapp.config import ADMIN_EMAIL, PROJECT_ROOT
from webapp.services.file_upload import FileUploadHandler, ValidationError
from webapp.services.job_store import JobStore
from webapp.services.job_submission import AuthorizationError, JobSubmissionService
from webapp.services.models import JobParams
from webapp.services.slurm_client import SlurmClient, SlurmError
from webapp.services.validation import (
    derive_target_name, validate_file_extension, validate_params,
)

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


def _get_email() -> str:
    return session.get("email", "")


def _get_job_store() -> JobStore:
    store_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(store_path)


def _get_submission_service() -> JobSubmissionService:
    return JobSubmissionService(SlurmClient(), _get_job_store(), PROJECT_ROOT)


def _owns_record(record, email: str) -> bool:
    """Return True if email owns the record or is admin."""
    return record.email == email or email.lower() == ADMIN_EMAIL.lower()


@jobs_bp.route("/submit", methods=["POST"])
def submit():
    email = _get_email()
    if not email:
        flash("Please log in to submit a job.", "danger")
        return redirect(url_for("login"))

    upload_handler = FileUploadHandler()
    errors: list[str] = []

    # PDB file
    pdb_file = request.files.get("pdb_file")
    pdb_path: str | None = None
    if not pdb_file or not pdb_file.filename:
        errors.append("Receptor PDB file is required.")
    else:
        if not validate_file_extension(pdb_file.filename, "pdb"):
            errors.append("Receptor PDB file must be .pdb format.")
        else:
            try:
                pdb_path = upload_handler.validate_and_save(pdb_file, email, "pdb")
            except ValidationError as e:
                errors.append(str(e))

    # Library file — upload (regular or chunked), or HPC path
    library_hpc_path = request.form.get("library_hpc_path", "").strip()
    library_upload_path = request.form.get("library_upload_path", "").strip()
    library_path: str | None = None
    library_is_remote = False

    if library_hpc_path:
        # Path to a file already on the HPC
        if not validate_file_extension(library_hpc_path, "library"):
            errors.append("HPC library path must end in .sdf, .smi, .smiles, or .txt.")
        else:
            library_path = library_hpc_path
            library_is_remote = True
    elif library_upload_path:
        # Completed chunked upload — path is local on the web server
        if not os.path.isfile(library_upload_path):
            errors.append("Chunked upload not found. Please re-upload the library.")
        elif not validate_file_extension(library_upload_path, "library"):
            errors.append("Uploaded library must be .sdf, .smi, .smiles, or .txt.")
        else:
            library_path = library_upload_path
    else:
        library_file = request.files.get("library_file")
        if not library_file or not library_file.filename:
            errors.append("Compound library is required — either upload a file or enter an HPC path.")
        else:
            if not validate_file_extension(library_file.filename, "library"):
                errors.append("Library must be .sdf, .smi, .smiles, or .txt.")
            else:
                try:
                    library_path = upload_handler.validate_and_save(library_file, email, "library")
                except ValidationError as e:
                    errors.append(str(e))

    # Binding site
    binding_site_method = request.form.get("binding_site_method", "")
    ligand_path: str | None = None
    residue_name: str | None = None
    center_x = center_y = center_z = None
    binding_residues: str | None = None
    chain_id: str | None = None

    if not binding_site_method:
        errors.append("A binding site definition is required.")
    else:
        if binding_site_method == "ligand":
            ligand_file = request.files.get("ligand_file")
            if not ligand_file or not ligand_file.filename:
                errors.append("Ligand file is required for this binding site method.")
            elif not validate_file_extension(ligand_file.filename, "ligand"):
                errors.append("Ligand file must be .pdb or .sdf.")
            else:
                try:
                    ligand_path = upload_handler.validate_and_save(ligand_file, email, "ligand")
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
                errors.append("All three coordinates (X, Y, Z) are required and must be numbers.")
        elif binding_site_method == "binding_residues":
            binding_residues = request.form.get("binding_residues", "").strip()
            if not binding_residues:
                errors.append("At least one residue number is required.")
            chain_id = request.form.get("chain_id", "").strip() or None
        else:
            errors.append("Unknown binding site method.")

    # Numeric params
    screening_mode = request.form.get("screening_mode", "standard")
    try:
        cutoff = float(request.form.get("cutoff", "10.0"))
    except (ValueError, TypeError):
        cutoff = -1.0
    try:
        top_fraction = float(request.form.get("top_fraction", "0.02"))
    except (ValueError, TypeError):
        top_fraction = -1.0
    try:
        chunk_size = int(request.form.get("chunk_size", "1000000"))
    except (ValueError, TypeError):
        chunk_size = 0

    for _, msg in validate_params(cutoff, top_fraction, chunk_size).items():
        errors.append(msg)

    if errors:
        for err in errors:
            flash(err, "danger")
        return redirect(url_for("dashboard.index"))

    target_name = request.form.get("target_name", "").strip()
    if not target_name and pdb_path:
        target_name = derive_target_name(pdb_path)

    params = JobParams(
        session_id=email,  # use email as session_id for compatibility
        pdb_path=pdb_path,
        library_path=library_path,
        library_is_remote=library_is_remote,
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
    )

    service = _get_submission_service()
    try:
        if screening_mode == "large_scale":
            record = service.submit_large_scale(params, email)
        else:
            record = service.submit_standard(params, email)
    except SlurmError as e:
        flash(f"Job submission failed: {e.stderr}", "danger")
        return redirect(url_for("dashboard.index"))

    flash(f"Job submitted! SLURM Job ID: {record.job_id}", "success")
    return redirect(url_for("jobs.detail", job_id=record.job_id))


@jobs_bp.route("/<job_id>", methods=["GET"])
def detail(job_id: str):
    email = _get_email()
    if not email:
        flash("Please log in.", "danger")
        return redirect(url_for("login"))
    record = _get_job_store().get_job(job_id)
    if record is None:
        abort(404)
    if not _owns_record(record, email):
        abort(403)
    return render_template("job_detail.html", job=record, current_user=email)


@jobs_bp.route("/<job_id>/cancel", methods=["POST"])
def cancel(job_id: str):
    email = _get_email()
    if not email:
        abort(403)
    service = _get_submission_service()
    try:
        service.cancel_job(job_id, email)
    except AuthorizationError:
        abort(403)
    except SlurmError as e:
        flash(f"Could not cancel job: {e.stderr}", "danger")
        return redirect(url_for("jobs.detail", job_id=job_id))
    flash("Job cancelled.", "success")
    return redirect(url_for("jobs.detail", job_id=job_id))
