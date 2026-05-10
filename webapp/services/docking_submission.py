"""Docking job submission service for AutoDock-GPU (SSH/HPC edition).

Workflow:
1. Generate ligand PDBQT locally using RDKit + AutoDockTools (if available),
   or write SMILES to a file and let the HPC script handle conversion.
2. Upload receptor PDB and ligand PDBQT to the HPC job directory via SCP.
3. Submit submit_docking.sh via SSH/sbatch.
4. Store a DockingRecord.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from typing import List, Tuple

from webapp.config import ADMIN_EMAIL, REMOTE_HOST, REMOTE_JOBS_DIR, REMOTE_PROJECT_ROOT, REMOTE_USER
from webapp.modules.remote_server import RemoteServer
from webapp.services.docking_store import DockingStore
from webapp.services.models import DockingRecord, JobRecord
from webapp.services.slurm_client import SlurmClient, SlurmError

logger = logging.getLogger(__name__)


def _derive_centre(record: JobRecord) -> Tuple[float, float, float]:
    """Extract binding site centre from a screening JobRecord."""
    params = record.params
    method = params.get("binding_site_method", "")
    if method == "center":
        return (
            float(params.get("center_x", 0)),
            float(params.get("center_y", 0)),
            float(params.get("center_z", 0)),
        )
    return (0.0, 0.0, 0.0)


def _smiles_to_pdbqt_local(entries: list) -> Tuple[bytes, int, int]:
    """Convert (rank, smiles, score) list to multi-molecule PDBQT using RDKit + AutoDockTools."""
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
        import subprocess, sys, tempfile
    except ImportError as e:
        raise RuntimeError("RDKit is required for ligand PDBQT generation.") from e

    _PREPARE_LIGAND = "AutoDockTools.Utilities24.prepare_ligand4"
    pdbqt_blocks: list[str] = []
    n_ok = n_fail = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for rank, smiles, score in entries:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                n_fail += 1
                continue
            mol = Chem.AddHs(mol)
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            if AllChem.EmbedMolecule(mol, params) == -1:
                if AllChem.EmbedMolecule(mol, AllChem.ETKDG()) == -1:
                    n_fail += 1
                    continue
            AllChem.MMFFOptimizeMolecule(mol)
            mol = Chem.RemoveHs(mol)
            mol.SetProp("_Name", f"rank_{rank}")

            pdb_path = os.path.join(tmpdir, f"lig_{rank}.pdb")
            pdbqt_path = os.path.join(tmpdir, f"lig_{rank}.pdbqt")
            Chem.MolToPDBFile(mol, pdb_path)

            result = subprocess.run(
                [sys.executable, "-m", _PREPARE_LIGAND, "-l", pdb_path, "-o", pdbqt_path, "-A", "hydrogens"],
                capture_output=True, text=True, timeout=60,
            )
            if result.returncode != 0 or not os.path.exists(pdbqt_path):
                n_fail += 1
                continue

            with open(pdbqt_path) as f:
                block = f.read().strip()
            header = f"REMARK DrugCLIP rank={rank} score={score:.6f}\nREMARK SMILES={smiles}\n"
            pdbqt_blocks.append(header + block)
            n_ok += 1

    return "\n".join(pdbqt_blocks).encode("utf-8"), n_ok, n_fail


class DockingSubmissionService:
    """Prepares files and submits AutoDock-GPU jobs via SSH/SLURM."""

    def __init__(self, slurm_client: SlurmClient, docking_store: DockingStore,
                 project_root: str) -> None:
        self._slurm_client = slurm_client
        self._docking_store = docking_store
        self._project_root = project_root

    def _server(self) -> RemoteServer:
        return RemoteServer(REMOTE_HOST, REMOTE_USER)

    def submit(
        self,
        email: str,
        screening_record: JobRecord,
        selected_compounds: List[Tuple[int, str, float]],
        center_x: float,
        center_y: float,
        center_z: float,
        nrun: int = 20,
        box_size: float = 22.5,
    ) -> DockingRecord:
        """Prepare files, upload to HPC, and submit AutoDock-GPU job."""
        docking_id = str(uuid.uuid4())[:8]
        target_name = screening_record.target_name
        library_name = screening_record.library_name

        remote_job_dir = f"{REMOTE_JOBS_DIR}/{target_name}_docking_{docking_id}"
        server = self._server()
        server.create_remote_directory(remote_job_dir)

        # --- Ligand PDBQT ---
        # Try local conversion first; fall back to writing SMILES for HPC conversion
        import tempfile, os
        local_tmp = tempfile.mkdtemp(prefix="drugclip_dock_")
        ligands_pdbqt_local = os.path.join(local_tmp, "ligands_input.pdbqt")
        remote_ligands = f"{remote_job_dir}/ligands_input.pdbqt"  # default; overridden on fallback

        try:
            pdbqt_bytes, n_ok, n_fail = _smiles_to_pdbqt_local(selected_compounds)
            if n_ok == 0:
                raise RuntimeError("No valid 3D structures could be generated.")
            if n_fail > 0:
                logger.warning("Docking prep: %d/%d molecules failed conformer generation", n_fail, len(selected_compounds))
            with open(ligands_pdbqt_local, "wb") as f:
                f.write(pdbqt_bytes)
            n_compounds = n_ok
        except Exception as e:
            # Fall back: write SMILES file and let HPC script convert
            logger.warning("Local PDBQT conversion failed (%s), falling back to SMILES file", e)
            smiles_path = os.path.join(local_tmp, "ligands.smi")
            with open(smiles_path, "w") as f:
                for rank, smiles, score in selected_compounds:
                    f.write(f"{smiles} rank_{rank}\n")
            # Upload SMILES instead; submit_docking.sh will handle conversion on HPC
            remote_smiles = f"{remote_job_dir}/ligands.smi"
            ok, err = server.upload_file(smiles_path, remote_smiles)
            if not ok:
                raise RuntimeError(f"Failed to upload SMILES file: {err}")
            ligands_pdbqt_local = None
            n_compounds = len(selected_compounds)
            remote_ligands = remote_smiles  # pass the .smi file to the script

        # Upload ligand PDBQT (if generated locally)
        if ligands_pdbqt_local is not None:
            remote_ligands = f"{remote_job_dir}/ligands_input.pdbqt"
            ok, err = server.upload_file(ligands_pdbqt_local, remote_ligands)
            if not ok:
                raise RuntimeError(f"Failed to upload ligand PDBQT: {err}")
        # (if ligands_pdbqt_local is None, remote_ligands was already set to remote_smiles above)

        # --- Receptor PDB ---
        pdb_path = screening_record.params.get("pdb_path", "")
        remote_receptor_pdb = f"{remote_job_dir}/receptor.pdb"
        if pdb_path and os.path.exists(pdb_path):
            ok, err = server.upload_file(pdb_path, remote_receptor_pdb)
            if not ok:
                raise RuntimeError(f"Failed to upload receptor PDB: {err}")
        else:
            # Try to find it on the HPC from the screening job input dir
            screening_job_dir = screening_record.job_dir
            remote_pdb_search = f"{screening_job_dir}/input/*.pdb"
            out, _ = server.run_command(f"ls {remote_pdb_search} 2>/dev/null | head -1")
            if out and out.strip():
                server.run_command(f"cp {out.strip()} {remote_receptor_pdb}")
            else:
                raise RuntimeError("Could not find receptor PDB file.")

        # --- Submit SLURM job ---
        remote_script = f"{REMOTE_PROJECT_ROOT}/submit_docking.sh"
        script_args = [
            remote_job_dir,
            remote_receptor_pdb,
            remote_ligands,
            str(center_x), str(center_y), str(center_z),
            "--nrun", str(nrun),
            "--box-size", str(box_size),
        ]

        slurm_job_id = self._slurm_client.sbatch(remote_script, script_args)

        now = datetime.now(timezone.utc).isoformat()
        record = DockingRecord(
            docking_id=docking_id,
            screening_job_id=screening_record.job_id,
            slurm_job_id=slurm_job_id,
            session_id=email,
            email=email,
            target_name=target_name,
            library_name=library_name,
            n_compounds=n_compounds,
            center_x=center_x,
            center_y=center_y,
            center_z=center_z,
            status="PENDING",
            submitted_at=now,
            updated_at=now,
            job_dir=remote_job_dir,
            log_path=f"{REMOTE_JOBS_DIR}/logs/slurm_{slurm_job_id}.log",
            summary_path=f"{remote_job_dir}/summary.csv",
            local_summary_path=None,
            error_message=None,
        )
        self._docking_store.add(record)

        # Clean up local temp files
        import shutil
        shutil.rmtree(local_tmp, ignore_errors=True)

        return record
