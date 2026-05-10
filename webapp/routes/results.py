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

    # Auto-detect docking centre from screening params
    from types import SimpleNamespace
    params = record.params
    method = params.get("binding_site_method", "")
    cx = cy = cz = None
    if method == "center":
        cx = params.get("center_x")
        cy = params.get("center_y")
        cz = params.get("center_z")
    docking_centre = SimpleNamespace(x=cx, y=cy, z=cz)

    return render_template("results.html", job=record, pagination=pagination,
                           current_user=email, docking_centre=docking_centre)


@results_bp.route("/<job_id>/results/centre", methods=["GET"])
def calculate_centre(job_id: str):
    """Calculate binding site centre from PDB residues (AJAX endpoint for docking modal)."""
    email = _get_email()
    if not email:
        from flask import jsonify
        return jsonify({"error": "Not authenticated"}), 401

    record = _get_job_store().get_job(job_id)
    if record is None or not _owns_record(record, email):
        from flask import jsonify
        return jsonify({"error": "Not found"}), 404

    method = request.args.get("method", "residue")
    pdb_path = record.params.get("pdb_path", "")

    if not pdb_path or not os.path.exists(pdb_path):
        from flask import jsonify
        return jsonify({"error": "PDB file not available locally"}), 404

    from flask import jsonify
    import re

    try:
        with open(pdb_path) as f:
            lines = f.readlines()

        coords = []
        if method == "residue":
            residue_name = request.args.get("residue_name", "").strip().upper()
            for line in lines:
                if line[:6].strip() in ("ATOM", "HETATM") and line[17:20].strip().upper() == residue_name:
                    coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))
        elif method == "binding_residues":
            residues_raw = request.args.get("binding_residues", "").strip()
            chain_id = request.args.get("chain_id", "").strip()
            residue_nums = set(residues_raw.split())
            for line in lines:
                if line[:6].strip() == "ATOM":
                    resnum = line[22:26].strip()
                    chain = line[21].strip()
                    if resnum in residue_nums and (not chain_id or chain == chain_id):
                        coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))

        if not coords:
            return jsonify({"error": f"No atoms found for the specified {method}"}), 400

        cx = round(sum(c[0] for c in coords) / len(coords), 3)
        cy = round(sum(c[1] for c in coords) / len(coords), 3)
        cz = round(sum(c[2] for c in coords) / len(coords), 3)
        return jsonify({"x": cx, "y": cy, "z": cz})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@results_bp.route("/<job_id>/results/download/sdf", methods=["GET"])
def download_sdf(job_id: str):
    """Generate and download 3D SDF for all hits."""
    import io as _io
    from flask import Response
    email = _get_email()
    if not email:
        abort(403)
    record = _get_job_store().get_job(job_id)
    if record is None or not _owns_record(record, email):
        abort(403)
    if record.status != "COMPLETED" or not record.results_path:
        abort(400)

    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError:
        abort(500)

    results = parse_results(record.results_path)
    buf = _io.StringIO()
    writer = Chem.SDWriter(buf)
    for rank, smiles, score in results:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            continue
        mol = Chem.AddHs(mol)
        p = AllChem.ETKDGv3(); p.randomSeed = 42
        if AllChem.EmbedMolecule(mol, p) == -1:
            continue
        AllChem.MMFFOptimizeMolecule(mol)
        mol = Chem.RemoveHs(mol)
        mol.SetProp("_Name", f"rank_{rank}")
        mol.SetProp("DrugCLIP_Score", str(score))
        writer.write(mol)
    writer.close()

    response = Response(buf.getvalue(), mimetype="chemical/x-mdl-sdfile")
    response.headers["Content-Disposition"] = f'attachment; filename="{record.target_name}_hits.sdf"'
    response.set_cookie("sdf_ready", "1", max_age=60)
    return response


@results_bp.route("/<job_id>/results/download/pdbqt", methods=["GET"])
def download_pdbqt(job_id: str):
    """Generate and download PDBQT for all hits (AutoDock format)."""
    import io as _io, subprocess, sys, tempfile
    from flask import Response
    email = _get_email()
    if not email:
        abort(403)
    record = _get_job_store().get_job(job_id)
    if record is None or not _owns_record(record, email):
        abort(403)
    if record.status != "COMPLETED" or not record.results_path:
        abort(400)

    results = parse_results(record.results_path)
    from webapp.services.docking_submission import _smiles_to_pdbqt_local
    try:
        pdbqt_bytes, n_ok, _ = _smiles_to_pdbqt_local(results)
    except Exception:
        abort(500)

    response = Response(pdbqt_bytes, mimetype="chemical/x-pdbqt")
    response.headers["Content-Disposition"] = f'attachment; filename="{record.target_name}_hits.pdbqt"'
    response.set_cookie("pdbqt_ready", "1", max_age=60)
    return response


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
