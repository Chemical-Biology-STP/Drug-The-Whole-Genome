"""Job submission service for the DrugCLIP web application (SSH/HPC edition).

Uploads input files to the HPC via SCP, then submits SLURM jobs via SSH.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import List, Optional

from webapp.config import REMOTE_HOST, REMOTE_JOBS_DIR, REMOTE_LIBRARIES_DIR, REMOTE_PROJECT_ROOT, REMOTE_USER
from webapp.modules.remote_server import RemoteServer
from webapp.services.job_store import JobStore
from webapp.services.models import JobParams, JobRecord
from webapp.services.slurm_client import SlurmClient, SlurmError


class AuthorizationError(Exception):
    """Raised when a user attempts to access a job they do not own."""

    def __init__(self, email: str, job_id: str):
        self.email = email
        self.job_id = job_id
        super().__init__(f"User {email!r} is not authorized to access job {job_id!r}")


class JobSubmissionService:
    """Uploads files to HPC, submits SLURM jobs, and manages them."""

    def __init__(self, slurm_client: SlurmClient, job_store: JobStore,
                 project_root: str) -> None:
        self._slurm_client = slurm_client
        self._job_store = job_store
        self._project_root = project_root

    def _server(self) -> RemoteServer:
        return RemoteServer(REMOTE_HOST, REMOTE_USER)

    def _upload_inputs(self, params: JobParams, remote_job_dir: str) -> dict:
        """Upload PDB, library, and optional ligand to the HPC.

        Returns a dict mapping param names to their remote paths.
        """
        server = self._server()
        ok, err = server.create_remote_directory(remote_job_dir)
        if not ok:
            raise SlurmError("mkdir", None, f"Could not create remote job dir: {err}")

        remote_paths = {}

        # PDB
        remote_pdb = f"{remote_job_dir}/{os.path.basename(params.pdb_path)}"
        ok, err = server.upload_file(params.pdb_path, remote_pdb)
        if not ok:
            raise SlurmError("scp pdb", None, f"PDB upload failed: {err}")
        remote_paths["pdb_path"] = remote_pdb

        # Library — skip upload if it's already on the HPC
        if params.library_is_remote:
            remote_paths["library_path"] = params.library_path  # already a remote path
        else:
            # Upload to the job-specific input dir
            remote_lib = f"{remote_job_dir}/{os.path.basename(params.library_path)}"
            ok, err = server.upload_file(params.library_path, remote_lib, timeout=600)
            if not ok:
                raise SlurmError("scp library", None, f"Library upload failed: {err}")
            remote_paths["library_path"] = remote_lib

            # Also copy to the shared library store for future reuse (best-effort)
            lib_filename = os.path.basename(params.library_path)
            remote_shared_lib = f"{REMOTE_LIBRARIES_DIR}/{lib_filename}"
            server.run_command(f"mkdir -p {REMOTE_LIBRARIES_DIR}")
            # Only copy if not already there (avoid re-uploading large files)
            exists_out, _ = server.run_command(
                f"test -f {remote_shared_lib} && echo exists"
            )
            if (exists_out or "").strip() != "exists":
                server.run_command(f"cp {remote_lib} {remote_shared_lib}")

        # Ligand (optional)
        if params.ligand_path:
            remote_lig = f"{remote_job_dir}/{os.path.basename(params.ligand_path)}"
            ok, err = server.upload_file(params.ligand_path, remote_lig)
            if not ok:
                raise SlurmError("scp ligand", None, f"Ligand upload failed: {err}")
            remote_paths["ligand_path"] = remote_lig

        return remote_paths

    def build_script_args(self, params: JobParams, remote_paths: dict) -> List[str]:
        """Build the argument list for submit_screening.sh / submit_large_screening.sh."""
        args: List[str] = [
            remote_paths["pdb_path"],
            remote_paths["library_path"],
        ]

        if params.binding_site_method == "ligand":
            args.extend(["--ligand", remote_paths["ligand_path"]])
        elif params.binding_site_method == "residue":
            args.extend(["--residue", params.residue_name])
        elif params.binding_site_method == "center":
            args.extend(["--center", str(params.center_x),
                         str(params.center_y), str(params.center_z)])
        elif params.binding_site_method == "binding_residues":
            args.extend(["--binding-residues"] + params.binding_residues.split())
            if params.chain_id:
                args.extend(["--chain", params.chain_id])

        if params.cutoff is not None:
            args.extend(["--cutoff", str(params.cutoff)])
        if params.target_name:
            args.extend(["--name", params.target_name])
        if params.top_fraction is not None:
            args.extend(["--top-fraction", str(params.top_fraction)])

        if params.screening_mode == "large_scale":
            if params.chunk_size:
                args.extend(["--chunk-size", str(params.chunk_size)])
            if params.partition:
                args.extend(["--partition", params.partition])
            if params.max_parallel:
                args.extend(["--max-parallel", str(params.max_parallel)])

        return args

    def _derive_library_name(self, library_path: str) -> str:
        return os.path.splitext(os.path.basename(library_path))[0]

    def _derive_target_name(self, params: JobParams) -> str:
        if params.target_name:
            return params.target_name
        return os.path.splitext(os.path.basename(params.pdb_path))[0]

    def submit_standard(self, params: JobParams, email: str) -> JobRecord:
        """Upload inputs to HPC and submit a standard screening job via sbatch."""
        target_name = self._derive_target_name(params)
        library_name = self._derive_library_name(params.library_path)
        job_dir_name = f"{target_name}_vs_{library_name}"
        remote_job_dir = f"{REMOTE_JOBS_DIR}/{job_dir_name}/input"

        remote_paths = self._upload_inputs(params, remote_job_dir)

        remote_script = f"{REMOTE_PROJECT_ROOT}/submit_screening.sh"
        script_args = self.build_script_args(params, remote_paths)
        # Pass --jobs-dir so the script writes output under REMOTE_JOBS_DIR
        script_args.extend(["--jobs-dir", REMOTE_JOBS_DIR])

        job_id = self._slurm_client.sbatch(remote_script, script_args)

        now = datetime.now(timezone.utc).isoformat()
        record = JobRecord(
            job_id=job_id,
            session_id=params.session_id,
            email=email,
            target_name=target_name,
            library_name=library_name,
            screening_mode="standard",
            status="PENDING",
            submitted_at=now,
            updated_at=now,
            params=params.to_dict(),
            job_dir=f"{REMOTE_JOBS_DIR}/{job_dir_name}",
            log_path=f"{REMOTE_JOBS_DIR}/logs/slurm_{job_id}.log",
            results_path=None,
            error_message=None,
            child_job_ids=None,
        )
        self._job_store.add_job(record)
        return record

    def submit_large_scale(self, params: JobParams, email: str) -> JobRecord:
        """Upload inputs to HPC and submit a large-scale screening pipeline via bash."""
        target_name = self._derive_target_name(params)
        library_name = self._derive_library_name(params.library_path)
        job_dir_name = f"{target_name}_vs_{library_name}"
        remote_job_dir = f"{REMOTE_JOBS_DIR}/{job_dir_name}/input"

        remote_paths = self._upload_inputs(params, remote_job_dir)

        remote_script = f"{REMOTE_PROJECT_ROOT}/submit_large_screening.sh"
        script_args = self.build_script_args(params, remote_paths)
        script_args.extend(["--jobs-dir", REMOTE_JOBS_DIR])

        # Large-scale script is run via bash (not sbatch) — use SSH
        from webapp.modules.remote_server import RemoteServer
        server = RemoteServer(REMOTE_HOST, REMOTE_USER)
        cmd = f"cd {REMOTE_PROJECT_ROOT} && bash {remote_script} {' '.join(script_args)}"
        out, err = server.run_command(cmd, timeout=300)
        if out is None:
            raise SlurmError(command=cmd, return_code=None, stderr=err or "")

        all_job_ids = re.findall(r"\b(\d{5,})\b", out)
        seen: set[str] = set()
        child_job_ids: list[str] = []
        for jid in all_job_ids:
            if jid not in seen:
                seen.add(jid)
                child_job_ids.append(jid)

        if not child_job_ids:
            raise SlurmError(command=cmd, return_code=0,
                             stderr=f"Could not parse job IDs from output: {out!r}")

        primary_job_id = child_job_ids[0]
        now = datetime.now(timezone.utc).isoformat()
        record = JobRecord(
            job_id=primary_job_id,
            session_id=params.session_id,
            email=email,
            target_name=target_name,
            library_name=library_name,
            screening_mode="large_scale",
            status="PENDING",
            submitted_at=now,
            updated_at=now,
            params=params.to_dict(),
            job_dir=f"{REMOTE_JOBS_DIR}/{job_dir_name}",
            log_path=f"{REMOTE_JOBS_DIR}/logs/slurm_{primary_job_id}.log",
            results_path=None,
            error_message=None,
            child_job_ids=child_job_ids,
        )
        self._job_store.add_job(record)
        return record

    def cancel_job(self, job_id: str, email: str) -> None:
        """Cancel a SLURM job after verifying ownership."""
        from webapp.config import ADMIN_EMAIL
        record = self._job_store.get_job(job_id)
        if record is None or (record.email != email and email.lower() != ADMIN_EMAIL.lower()):
            raise AuthorizationError(email=email, job_id=job_id)
        self._slurm_client.scancel(job_id)
        if record.child_job_ids:
            for child_id in record.child_job_ids:
                if child_id != job_id:
                    try:
                        self._slurm_client.scancel(child_id)
                    except Exception:
                        pass
