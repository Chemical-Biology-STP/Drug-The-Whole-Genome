"""Docking job routes for AutoDock-GPU integration (SSH/HPC edition)."""

from __future__ import annotations

import csv
import io
import math
import os
import tempfile
import zipfile

from flask import (
    Blueprint, abort, flash, jsonify, redirect,
    render_template, request, send_file, session, url_for,
)

from webapp.config import ADMIN_EMAIL, REMOTE_HOST, REMOTE_JOBS_DIR, REMOTE_USER, PROJECT_ROOT
from webapp.modules.remote_server import RemoteServer
from webapp.services.docking_store import DockingStore
from webapp.services.docking_submission import DockingSubmissionService, _derive_centre
from webapp.services.job_store import JobStore
from webapp.services.results_parser import parse_results
from webapp.services.slurm_client import SlurmClient, SlurmError

docking_bp = Blueprint("docking", __name__)

DOCKING_RESULTS_PER_PAGE = 50


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _email() -> str:
    return session.get("email", "")


def _get_job_store() -> JobStore:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "jobs.json")
    return JobStore(path)


def _get_docking_store() -> DockingStore:
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "docking_jobs.json")
    return DockingStore(path)


def _owns(record, email: str) -> bool:
    return record.email == email or email.lower() == ADMIN_EMAIL.lower()


def _server() -> RemoteServer:
    return RemoteServer(REMOTE_HOST, REMOTE_USER)


def _fetch_summary(record) -> list[dict]:
    """Download summary.csv from HPC if not cached locally, return rows."""
    if record.local_summary_path and os.path.exists(record.local_summary_path):
        path = record.local_summary_path
    elif record.summary_path:
        local_dir = os.path.join(tempfile.gettempdir(), "drugclip_docking", record.docking_id)
        os.makedirs(local_dir, exist_ok=True)
        local_path = os.path.join(local_dir, "summary.csv")
        ok, _ = _server().download_file(record.summary_path, local_path)
        if not ok:
            return []
        # Cache the path
        _get_docking_store().update(record.docking_id, {"local_summary_path": local_path})
        path = local_path
    else:
        return []

    rows = []
    try:
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "drugclip_rank": int(row.get("drugclip_rank", 0)),
                    "smiles": row.get("smiles", ""),
                    "docking_score": float(row.get("docking_score_kcal_mol", 0)),
                    "result_stem": row.get("result_stem", ""),
                    "docking_id": record.docking_id,
                })
    except Exception:
        pass
    return rows


# ---------------------------------------------------------------------------
# Submit docking
# ---------------------------------------------------------------------------

@docking_bp.route("/jobs/<job_id>/dock", methods=["POST"])
def submit(job_id: str):
    """Submit selected compounds for AutoDock-GPU docking."""
    email = _email()
    if not email:
        abort(403)

    job_store = _get_job_store()
    record = job_store.get_job(job_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.status != "COMPLETED" or not record.results_path:
        flash("Screening results are not available for this job.", "warning")
        return redirect(url_for("jobs.detail", job_id=job_id))

    # Parse selected ranks — either explicit checkboxes or top_n
    top_n_raw = request.form.get("top_n", "").strip()
    selected_ranks_raw = request.form.getlist("selected_compounds")

    if top_n_raw:
        try:
            top_n = int(top_n_raw)
            if top_n < 1:
                raise ValueError
        except ValueError:
            flash("Invalid top-N value.", "danger")
            return redirect(url_for("results.view", job_id=job_id))
        # Load all results and take the top N by score
        all_results = parse_results(record.results_path)
        selected = list(all_results[:top_n])
        if not selected:
            flash("No results available.", "danger")
            return redirect(url_for("results.view", job_id=job_id))
    elif selected_ranks_raw:
        try:
            selected_ranks = set(int(r) for r in selected_ranks_raw)
        except ValueError:
            flash("Invalid compound selection.", "danger")
            return redirect(url_for("results.view", job_id=job_id))
        all_results = parse_results(record.results_path)
        selected = [(rank, smi, score) for rank, smi, score in all_results if rank in selected_ranks]
        if not selected:
            flash("None of the selected compounds were found in the results.", "danger")
            return redirect(url_for("results.view", job_id=job_id))
    else:
        flash("No compounds selected.", "warning")
        return redirect(url_for("results.view", job_id=job_id))


    # Binding site centre
    try:
        center_x = float(request.form.get("center_x", 0))
        center_y = float(request.form.get("center_y", 0))
        center_z = float(request.form.get("center_z", 0))
    except (ValueError, TypeError):
        flash("Invalid binding site coordinates.", "danger")
        return redirect(url_for("results.view", job_id=job_id))

    try:
        nrun = max(1, min(100, int(request.form.get("nrun", 20))))
        box_size = max(10.0, min(60.0, float(request.form.get("box_size", 22.5))))
    except (ValueError, TypeError):
        nrun, box_size = 20, 22.5

    # Submit
    docking_store = _get_docking_store()
    service = DockingSubmissionService(SlurmClient(), docking_store, PROJECT_ROOT)
    try:
        docking_record = service.submit(
            email=email,
            screening_record=record,
            selected_compounds=selected,
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
            nrun=nrun,
            box_size=box_size,
        )
    except RuntimeError as e:
        flash(f"Docking preparation failed: {e}", "danger")
        return redirect(url_for("results.view", job_id=job_id))
    except SlurmError as e:
        flash(f"Docking job submission failed: {e.stderr}", "danger")
        return redirect(url_for("results.view", job_id=job_id))

    flash(f"Docking job submitted! {docking_record.n_compounds} compounds → SLURM job {docking_record.slurm_job_id}", "success")
    return redirect(url_for("docking.detail", docking_id=docking_record.docking_id))


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------

@docking_bp.route("/docking/<docking_id>", methods=["GET"])
def detail(docking_id: str):
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    return render_template("docking_detail.html", job=record, current_user=email)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@docking_bp.route("/docking/<docking_id>/results", methods=["GET"])
def results(docking_id: str):
    email = _email()
    if not email:
        abort(403)
    docking_store = _get_docking_store()
    record = docking_store.get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.status != "COMPLETED":
        flash("Docking results are not yet available.", "warning")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    rows_by_rank: dict = {}

    def _merge(d_record):
        for row in _fetch_summary(d_record):
            rank = row["drugclip_rank"]
            if rank not in rows_by_rank or row["docking_score"] < rows_by_rank[rank]["docking_score"]:
                rows_by_rank[rank] = row

    _merge(record)
    # Merge from other completed docking jobs for the same screening run
    for d in docking_store.get_for_user(email):
        if d.docking_id != docking_id and d.screening_job_id == record.screening_job_id and d.status == "COMPLETED":
            dx = d.center_x - record.center_x
            dy = d.center_y - record.center_y
            dz = d.center_z - record.center_z
            if math.sqrt(dx*dx + dy*dy + dz*dz) <= 0.5:
                _merge(d)

    rows = sorted(rows_by_rank.values(), key=lambda r: r["docking_score"])

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    total = len(rows)
    total_pages = math.ceil(total / DOCKING_RESULTS_PER_PAGE) if total > 0 else 1
    page = min(page, total_pages)
    start = (page - 1) * DOCKING_RESULTS_PER_PAGE
    pagination = {
        "items": rows[start:start + DOCKING_RESULTS_PER_PAGE],
        "total_items": total,
        "total_pages": total_pages,
        "current_page": page,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }
    return render_template("docking_results.html", job=record, pagination=pagination,
                           current_user=email)


# ---------------------------------------------------------------------------
# Download summary CSV
# ---------------------------------------------------------------------------

@docking_bp.route("/docking/<docking_id>/results/download", methods=["GET"])
def download_results(docking_id: str):
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.status != "COMPLETED":
        flash("Results not available.", "warning")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    rows = _fetch_summary(record)
    if not rows:
        flash("Could not retrieve results.", "warning")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=["drugclip_rank", "smiles", "docking_score", "result_stem"])
    writer.writeheader()
    writer.writerows(rows)
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"{record.target_name}_docking_{docking_id}_summary.csv",
    )


# ---------------------------------------------------------------------------
# Pose viewer — serve pose files from HPC via SSH
# ---------------------------------------------------------------------------

def _pdbqt_to_pdb(pdbqt_text: str) -> str:
    """Strip AutoDock-specific records and return PDB-format text."""
    lines = []
    for line in pdbqt_text.splitlines():
        rec = line[:6].strip()
        if rec in ("ATOM", "HETATM"):
            pdb_line = line[:66].ljust(66)
            if pdb_line[21] == " ":
                pdb_line = pdb_line[:21] + "A" + pdb_line[22:]
            lines.append(pdb_line)
        elif rec in ("ROOT", "ENDROOT", "BRANCH", "ENDBRANCH", "TORSDOF"):
            continue
        elif rec in ("REMARK", "MODEL", "ENDMDL", "END", "TER"):
            lines.append(line.rstrip()[:80])
    return "\n".join(lines)


@docking_bp.route("/docking/<docking_id>/pose/<result_stem>", methods=["GET"])
def pose_pdbqt(docking_id: str, result_stem: str):
    """Serve a best-pose as PDB for in-browser NGL viewing."""
    import re
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if not re.match(r"^[\w\-]+$", result_stem):
        abort(400)

    remote_pose = f"{record.job_dir}/docking_results/{result_stem}-best.pdbqt"
    local_dir = os.path.join(tempfile.gettempdir(), "drugclip_poses", docking_id)
    os.makedirs(local_dir, exist_ok=True)
    local_pose = os.path.join(local_dir, f"{result_stem}-best.pdbqt")

    if not os.path.exists(local_pose):
        ok, err = _server().download_file(remote_pose, local_pose)
        if not ok:
            abort(404)

    if request.args.get("download") == "1":
        return send_file(local_pose, mimetype="chemical/x-pdbqt",
                         as_attachment=True, download_name=f"{result_stem}-best.pdbqt")

    with open(local_pose) as f:
        pdbqt_text = f.read()
    return _pdbqt_to_pdb(pdbqt_text), 200, {"Content-Type": "text/plain"}


@docking_bp.route("/docking/<docking_id>/pose/<result_stem>/sdf", methods=["GET"])
def pose_sdf(docking_id: str, result_stem: str):
    """Serve a best-pose as SDF with correct bond orders for NGL."""
    import re
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if not re.match(r"^[\w\-]+$", result_stem):
        abort(400)

    remote_pose = f"{record.job_dir}/docking_results/{result_stem}-best.pdbqt"
    local_dir = os.path.join(tempfile.gettempdir(), "drugclip_poses", docking_id)
    os.makedirs(local_dir, exist_ok=True)
    local_pose = os.path.join(local_dir, f"{result_stem}-best.pdbqt")

    if not os.path.exists(local_pose):
        ok, err = _server().download_file(remote_pose, local_pose)
        if not ok:
            abort(404)

    polar_h_only = request.args.get("polar_h_only") == "1"
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        smiles = None
        with open(local_pose) as f:
            for line in f:
                if line.startswith("REMARK SMILES="):
                    smiles = line.strip().split("=", 1)[1]
                    break
        if smiles:
            with open(local_pose) as f:
                pdbqt_text = f.read()
            pdb_text = _pdbqt_to_pdb(pdbqt_text)
            pdb_mol = Chem.MolFromPDBBlock(pdb_text, removeHs=True, sanitize=True)
            template = Chem.MolFromSmiles(smiles)
            if pdb_mol and template and template.GetNumAtoms() == pdb_mol.GetNumAtoms():
                result = AllChem.AssignBondOrdersFromTemplate(template, pdb_mol)
                Chem.SanitizeMol(result)
                result = Chem.AddHs(result, addCoords=True)
                if polar_h_only:
                    atoms_to_remove = [a.GetIdx() for a in result.GetAtoms()
                                       if a.GetAtomicNum() == 1 and
                                       all(n.GetAtomicNum() == 6 for n in a.GetNeighbors())]
                    if atoms_to_remove:
                        edit = Chem.RWMol(result)
                        for idx in sorted(atoms_to_remove, reverse=True):
                            edit.RemoveAtom(idx)
                        result = edit.GetMol()
                sdf = Chem.MolToMolBlock(result)
                return sdf, 200, {"Content-Type": "text/plain"}
    except Exception:
        pass

    # Fallback to PDB
    with open(local_pose) as f:
        pdbqt_text = f.read()
    return _pdbqt_to_pdb(pdbqt_text), 200, {"Content-Type": "text/plain"}


@docking_bp.route("/docking/<docking_id>/receptor", methods=["GET"])
def receptor_pdbqt(docking_id: str):
    """Serve the receptor PDB for NGL viewing."""
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)

    # Try to get the original PDB from the screening job
    job_store = _get_job_store()
    screening_record = job_store.get_job(record.screening_job_id)
    pdb_path = None
    if screening_record:
        pdb_path = screening_record.params.get("pdb_path", "")
        if pdb_path and not os.path.exists(pdb_path):
            pdb_path = None

    if not pdb_path:
        # Download from HPC
        remote_pdb = f"{record.job_dir}/receptor.pdb"
        local_dir = os.path.join(tempfile.gettempdir(), "drugclip_poses", docking_id)
        os.makedirs(local_dir, exist_ok=True)
        local_pdb = os.path.join(local_dir, "receptor.pdb")
        if not os.path.exists(local_pdb):
            ok, _ = _server().download_file(remote_pdb, local_pdb)
            if not ok:
                abort(404)
        pdb_path = local_pdb

    polar_h_only = request.args.get("polar_h_only") == "1"
    try:
        from rdkit import Chem
        mol = Chem.MolFromPDBFile(pdb_path, removeHs=False, sanitize=False)
        if mol is not None:
            if polar_h_only:
                atoms_to_remove = [a.GetIdx() for a in mol.GetAtoms()
                                   if a.GetAtomicNum() == 1 and
                                   all(n.GetAtomicNum() == 6 for n in a.GetNeighbors())]
                if atoms_to_remove:
                    edit = Chem.RWMol(mol)
                    for idx in sorted(atoms_to_remove, reverse=True):
                        edit.RemoveAtom(idx)
                    mol = edit.GetMol()
            return Chem.MolToPDBBlock(mol), 200, {"Content-Type": "text/plain"}
    except Exception:
        pass

    with open(pdb_path) as f:
        return f.read(), 200, {"Content-Type": "text/plain"}


@docking_bp.route("/docking/<docking_id>/poses/download", methods=["GET"])
def download_poses(docking_id: str):
    """Download all best-pose PDBQT files as a zip archive."""
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.status != "COMPLETED":
        flash("Docking is not yet complete.", "warning")
        return redirect(url_for("docking.results", docking_id=docking_id))

    server = _server()
    # List pose files on HPC
    out, _ = server.run_command(
        f"ls {record.job_dir}/docking_results/*-best.pdbqt 2>/dev/null"
    )
    if not out:
        flash("No pose files found.", "warning")
        return redirect(url_for("docking.results", docking_id=docking_id))

    remote_files = out.splitlines()
    local_dir = os.path.join(tempfile.gettempdir(), "drugclip_poses", docking_id)
    os.makedirs(local_dir, exist_ok=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for remote_path in remote_files:
            fname = os.path.basename(remote_path.strip())
            local_path = os.path.join(local_dir, fname)
            if not os.path.exists(local_path):
                server.download_file(remote_path.strip(), local_path)
            if os.path.exists(local_path):
                zf.write(local_path, fname)
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=f"{record.target_name}_docking_{docking_id}_poses.zip")


@docking_bp.route("/docking/<docking_id>/contact-residues/<result_stem>", methods=["GET"])
def contact_residues(docking_id: str, result_stem: str):
    """Return residue numbers within a given radius of the docked ligand."""
    import re, math as _math
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if not re.match(r"^[\w\-]+$", result_stem):
        abort(400)

    try:
        radius = float(request.args.get("radius", 4.5))
    except (ValueError, TypeError):
        radius = 4.5

    local_dir = os.path.join(tempfile.gettempdir(), "drugclip_poses", docking_id)
    os.makedirs(local_dir, exist_ok=True)
    server = _server()

    local_pose = os.path.join(local_dir, f"{result_stem}-best.pdbqt")
    if not os.path.exists(local_pose):
        ok, _ = server.download_file(f"{record.job_dir}/docking_results/{result_stem}-best.pdbqt", local_pose)
        if not ok:
            abort(404)

    local_receptor = os.path.join(local_dir, "receptor.pdbqt")
    if not os.path.exists(local_receptor):
        ok, _ = server.download_file(f"{record.job_dir}/receptor.pdbqt", local_receptor)
        if not ok:
            abort(404)

    lig_coords = []
    with open(local_pose) as f:
        for line in f:
            rec = line[:6].strip()
            if rec in ("ATOM", "HETATM") and not line[12:16].strip().startswith("H"):
                lig_coords.append((float(line[30:38]), float(line[38:46]), float(line[46:54])))

    contact_set = set()
    with open(local_receptor) as f:
        for line in f:
            if line[:6].strip() != "ATOM":
                continue
            rx, ry, rz = float(line[30:38]), float(line[38:46]), float(line[46:54])
            resnum = line[22:26].strip()
            for lx, ly, lz in lig_coords:
                if _math.sqrt((rx-lx)**2 + (ry-ly)**2 + (rz-lz)**2) <= radius:
                    contact_set.add(resnum)
                    break

    residues = sorted(contact_set, key=lambda x: int(x))
    return jsonify({"residues": residues, "selection": " or ".join(residues) or "none",
                    "count": len(residues)})


# ---------------------------------------------------------------------------
# Cancel / Delete
# ---------------------------------------------------------------------------

@docking_bp.route("/docking/<docking_id>/restart", methods=["POST"])
def restart(docking_id: str):
    """Re-submit a failed/cancelled docking job with the same parameters."""
    email = _email()
    if not email:
        abort(403)
    docking_store = _get_docking_store()
    original = docking_store.get(docking_id)
    if original is None:
        abort(404)
    if not _owns(original, email):
        abort(403)

    # Get the parent screening record
    job_store = _get_job_store()
    screening_record = job_store.get_job(original.screening_job_id)
    if screening_record is None:
        flash("Parent screening job not found.", "danger")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    # Get compounds from the screening results (most reliable source)
    if not screening_record.results_path or not os.path.exists(screening_record.results_path):
        flash("Screening results file not available. Cannot restart docking.", "danger")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    from webapp.services.results_parser import parse_results
    all_results = parse_results(screening_record.results_path)

    # We need to know which ranks were originally selected.
    # Try to read from the SMI file on the HPC (most reliable — has rank_N names)
    server = _server()
    remote_smi = f"{original.job_dir}/ligands.smi"
    local_dir = os.path.join(tempfile.gettempdir(), "drugclip_docking_restart",
                             f"{docking_id}_{os.urandom(4).hex()}")
    os.makedirs(local_dir, exist_ok=True)
    local_smi = os.path.join(local_dir, "ligands.smi")

    selected_ranks: set[int] = set()
    ok, _ = server.download_file(remote_smi, local_smi)
    if ok and os.path.exists(local_smi):
        with open(local_smi) as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    name = parts[1]  # e.g. "rank_1"
                    if name.startswith("rank_"):
                        try:
                            selected_ranks.add(int(name[5:]))
                        except ValueError:
                            pass

    if selected_ranks:
        selected_compounds = [(r, s, sc) for r, s, sc in all_results if r in selected_ranks]
    else:
        # Fall back: use all results up to original n_compounds
        selected_compounds = list(all_results[:original.n_compounds])

    if not selected_compounds:
        flash("Could not determine which compounds were originally selected. "
              "Please re-submit from the screening results page.", "danger")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    service = DockingSubmissionService(SlurmClient(), docking_store, PROJECT_ROOT)
    try:
        new_record = service.submit(
            email=email,
            screening_record=screening_record,
            selected_compounds=selected_compounds,
            center_x=original.center_x,
            center_y=original.center_y,
            center_z=original.center_z,
        )
    except RuntimeError as e:
        flash(f"Restart failed: {e}", "danger")
        return redirect(url_for("docking.detail", docking_id=docking_id))
    except SlurmError as e:
        flash(f"Restart failed: {e.stderr}", "danger")
        return redirect(url_for("docking.detail", docking_id=docking_id))

    flash(f"Docking job restarted! New SLURM job {new_record.slurm_job_id}", "success")
    return redirect(url_for("docking.detail", docking_id=new_record.docking_id))


@docking_bp.route("/docking/<docking_id>/force-refresh", methods=["POST"])
def force_refresh(docking_id: str):
    """Force an immediate status check for a docking job."""
    email = _email()
    if not email:
        abort(403)
    docking_store = _get_docking_store()
    record = docking_store.get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)

    if record.slurm_job_id and record.status in ("PENDING", "RUNNING"):
        from webapp.services.slurm_client import SlurmClient
        from datetime import datetime, timezone
        slurm = SlurmClient()
        now = datetime.now(timezone.utc).isoformat()

        # Check squeue first
        squeue = slurm.squeue(job_ids=[record.slurm_job_id])
        squeue_map = {e["job_id"]: e["state"] for e in squeue}

        new_status = None
        if record.slurm_job_id in squeue_map:
            from webapp.services.job_monitor import JobMonitor
            new_status = JobMonitor._normalize_status(squeue_map[record.slurm_job_id])
        else:
            sacct = slurm.sacct([record.slurm_job_id])
            for e in sacct:
                if "." not in e["job_id"]:
                    from webapp.services.job_monitor import JobMonitor
                    new_status = JobMonitor._normalize_status(e["state"])
                    break

        if new_status and new_status != record.status:
            updates = {"status": new_status, "updated_at": now}
            if new_status == "COMPLETED":
                # Download summary.csv
                import tempfile
                local_dir = os.path.join(tempfile.gettempdir(), "drugclip_docking", docking_id)
                os.makedirs(local_dir, exist_ok=True)
                local_summary = os.path.join(local_dir, "summary.csv")
                ok, _ = _server().download_file(record.summary_path, local_summary)
                if ok:
                    updates["local_summary_path"] = local_summary
                else:
                    new_status = "FAILED"
                    updates["status"] = "FAILED"
                    updates["error_message"] = "Docking completed but summary.csv was not found."
            docking_store.update(docking_id, updates)
            flash(f"Status updated: {new_status}", "success")
        else:
            flash(f"Status unchanged: {record.status}", "info")

    return redirect(url_for("docking.detail", docking_id=docking_id))


@docking_bp.route("/docking/<docking_id>/cancel", methods=["POST"])
def cancel(docking_id: str):
    email = _email()
    if not email:
        abort(403)
    record = _get_docking_store().get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.slurm_job_id:
        try:
            SlurmClient().scancel(record.slurm_job_id)
        except SlurmError as e:
            flash(f"Could not cancel: {e.stderr}", "danger")
            return redirect(url_for("docking.detail", docking_id=docking_id))
    from datetime import datetime, timezone
    _get_docking_store().update(docking_id, {
        "status": "CANCELLED",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    flash("Docking job cancelled.", "success")
    return redirect(url_for("docking.detail", docking_id=docking_id))


@docking_bp.route("/docking/<docking_id>/delete", methods=["POST"])
def delete(docking_id: str):
    email = _email()
    if not email:
        abort(403)
    docking_store = _get_docking_store()
    record = docking_store.get(docking_id)
    if record is None:
        abort(404)
    if not _owns(record, email):
        abort(403)
    if record.status in ("PENDING", "RUNNING"):
        flash("Cancel the job before deleting.", "warning")
        return redirect(url_for("docking.detail", docking_id=docking_id))
    docking_store.delete(docking_id)
    flash("Docking job deleted.", "success")
    return redirect(url_for("jobs.detail", job_id=record.screening_job_id))
