"""Job submission service for the DrugCLIP web application.

Constructs CLI commands from JobParams, submits them via SLURM (sbatch for
standard mode, bash for large-scale mode), and records job metadata.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from typing import List, Optional

from webapp.services.job_store import JobStore
from webapp.services.models import JobParams, JobRecord
from webapp.services.slurm_client import SlurmClient


class AuthorizationError(Exception):
    """Raised when a session attempts to access a job it does not own."""

    def __init__(self, session_id: str, job_id: str):
        self.session_id = session_id
        self.job_id = job_id
        super().__init__(
            f"Session {session_id!r} is not authorized to access job {job_id!r}"
        )


class JobSubmissionService:
    """Service for building commands, submitting SLURM jobs, and managing them.

    Parameters
    ----------
    slurm_client:
        Low-level SLURM CLI wrapper for sbatch/scancel calls.
    job_store:
        Persistent job metadata store.
    project_root:
        Absolute path to the project root directory (where shell scripts live).
    """

    def __init__(
        self,
        slurm_client: SlurmClient,
        job_store: JobStore,
        project_root: str,
    ) -> None:
        self._slurm_client = slurm_client
        self._job_store = job_store
        self._project_root = project_root

    def build_command_args(self, params: JobParams) -> List[str]:
        """Convert JobParams into a CLI argument list for the shell scripts.

        The returned list starts with the script path, followed by positional
        arguments (PDB path, library path), exactly one binding site flag, and
        any optional parameters.

        Parameters
        ----------
        params:
            The job parameters collected from the submission form.

        Returns
        -------
        list[str]
            The full command argument list ready for execution.
        """
        # Determine script based on screening mode
        if params.screening_mode == "large_scale":
            script = os.path.join(self._project_root, "submit_large_screening.sh")
        else:
            script = os.path.join(self._project_root, "submit_screening.sh")

        args: List[str] = [script, params.pdb_path, params.library_path]

        # Binding site flag — exactly one
        if params.binding_site_method == "ligand":
            args.extend(["--ligand", params.ligand_path])
        elif params.binding_site_method == "residue":
            args.extend(["--residue", params.residue_name])
        elif params.binding_site_method == "center":
            args.extend([
                "--center",
                str(params.center_x),
                str(params.center_y),
                str(params.center_z),
            ])
        elif params.binding_site_method == "binding_residues":
            args.extend(["--binding-residues", params.binding_residues])
            if params.chain_id:
                args.extend(["--chain", params.chain_id])

        # Optional parameters
        if params.cutoff is not None:
            args.extend(["--cutoff", str(params.cutoff)])

        if params.target_name is not None:
            args.extend(["--name", str(params.target_name)])

        if params.top_fraction is not None:
            args.extend(["--top-fraction", str(params.top_fraction)])

        # Large-scale only parameters
        if params.screening_mode == "large_scale":
            if params.chunk_size is not None:
                args.extend(["--chunk-size", str(params.chunk_size)])
            if params.partition is not None:
                args.extend(["--partition", str(params.partition)])
            if params.max_parallel is not None:
                args.extend(["--max-parallel", str(params.max_parallel)])

        # Pre-encoded library: skip molecule encoding step
        if params.use_preencoded_library and params.cache_dir:
            args.extend(["--use-cache", "True"])
            args.extend(["--cache-dir", params.cache_dir])

        return args

    def _derive_library_name(self, library_path: str) -> str:
        """Derive the library name from the library file path.

        Returns the basename without extension.
        """
        return os.path.splitext(os.path.basename(library_path))[0]

    def _derive_target_name(self, params: JobParams) -> str:
        """Derive the target name from params or PDB filename."""
        if params.target_name:
            return params.target_name
        return os.path.splitext(os.path.basename(params.pdb_path))[0]

    def _build_job_dir(self, target_name: str, library_name: str) -> str:
        """Construct the job directory path."""
        return f"jobs/{target_name}_vs_{library_name}"

    def submit_standard(self, params: JobParams) -> JobRecord:
        """Submit a standard screening job via sbatch.

        Builds the command arguments, submits via slurm_client.sbatch,
        creates a JobRecord, stores it, and returns it.

        Parameters
        ----------
        params:
            The job parameters collected from the submission form.

        Returns
        -------
        JobRecord
            The newly created job record with the SLURM job ID.
        """
        cmd_args = self.build_command_args(params)

        # sbatch expects script_path and script_args separately
        script_path = cmd_args[0]
        script_args = cmd_args[1:]

        job_id = self._slurm_client.sbatch(script_path, script_args)

        target_name = self._derive_target_name(params)
        library_name = self._derive_library_name(params.library_path)
        job_dir = self._build_job_dir(target_name, library_name)
        now = datetime.now(timezone.utc).isoformat()

        record = JobRecord(
            job_id=job_id,
            session_id=params.session_id,
            target_name=target_name,
            library_name=library_name,
            screening_mode=params.screening_mode,
            status="PENDING",
            submitted_at=now,
            updated_at=now,
            params=params.to_dict(),
            job_dir=job_dir,
            log_path=f"jobs/logs/slurm_{job_id}.log",
            results_path=None,
            error_message=None,
            child_job_ids=None,
        )

        self._job_store.add_job(record)
        return record

    def submit_large_scale(self, params: JobParams) -> JobRecord:
        """Submit a large-scale screening job via bash (not sbatch).

        The large-scale script is run directly with bash and outputs multiple
        SLURM job IDs (one per line or comma-separated). The first job ID is
        used as the primary job_id, and all are stored as child_job_ids.

        Parameters
        ----------
        params:
            The job parameters collected from the submission form.

        Returns
        -------
        JobRecord
            The newly created job record with primary and child job IDs.
        """
        cmd_args = self.build_command_args(params)

        # Execute via bash (not sbatch)
        cmd = ["bash"] + cmd_args
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            from webapp.services.slurm_client import SlurmError
            raise SlurmError(
                command=" ".join(cmd),
                return_code=result.returncode,
                stderr=result.stderr.strip(),
            )

        # Parse job IDs from output — look for numeric IDs
        all_job_ids = re.findall(r"\b(\d{5,})\b", result.stdout)

        # Deduplicate while preserving order
        seen = set()
        child_job_ids = []
        for jid in all_job_ids:
            if jid not in seen:
                seen.add(jid)
                child_job_ids.append(jid)

        if not child_job_ids:
            from webapp.services.slurm_client import SlurmError
            raise SlurmError(
                command=" ".join(cmd),
                return_code=0,
                stderr=f"Could not parse job IDs from output: {result.stdout!r}",
            )

        # First job ID is the primary one
        primary_job_id = child_job_ids[0]

        target_name = self._derive_target_name(params)
        library_name = self._derive_library_name(params.library_path)
        job_dir = self._build_job_dir(target_name, library_name)
        now = datetime.now(timezone.utc).isoformat()

        record = JobRecord(
            job_id=primary_job_id,
            session_id=params.session_id,
            target_name=target_name,
            library_name=library_name,
            screening_mode=params.screening_mode,
            status="PENDING",
            submitted_at=now,
            updated_at=now,
            params=params.to_dict(),
            job_dir=job_dir,
            log_path=f"jobs/logs/slurm_{primary_job_id}.log",
            results_path=None,
            error_message=None,
            child_job_ids=child_job_ids,
        )

        self._job_store.add_job(record)
        return record

    def cancel_job(self, job_id: str, session_id: str) -> None:
        """Cancel a SLURM job after verifying session ownership.

        Parameters
        ----------
        job_id:
            The SLURM job ID to cancel.
        session_id:
            The session ID of the requesting user.

        Raises
        ------
        AuthorizationError
            If the session does not own the job.
        """
        record = self._job_store.get_job(job_id)

        if record is None or record.session_id != session_id:
            raise AuthorizationError(session_id=session_id, job_id=job_id)

        self._slurm_client.scancel(job_id)

        # Also cancel child jobs if present
        if record.child_job_ids:
            for child_id in record.child_job_ids:
                if child_id != job_id:
                    try:
                        self._slurm_client.scancel(child_id)
                    except Exception:
                        pass  # Best effort for child jobs
