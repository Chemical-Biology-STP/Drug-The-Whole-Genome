"""Low-level wrapper around SLURM CLI commands — SSH edition.

All sbatch/squeue/sacct/scancel calls are routed through SSH to the HPC
login node via RemoteServer, matching the app_ProtPrep pattern.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from webapp.config import REMOTE_HOST, REMOTE_USER
from webapp.modules.remote_server import RemoteServer


class SlurmError(Exception):
    """Raised when a SLURM command fails."""

    def __init__(self, command: str, return_code: Optional[int], stderr: str):
        self.command = command
        self.return_code = return_code
        self.stderr = stderr
        super().__init__(
            f"SLURM command failed: {command!r} "
            f"(return_code={return_code}, stderr={stderr!r})"
        )


class SlurmClient:
    """Routes SLURM commands to the HPC via SSH."""

    def _server(self) -> RemoteServer:
        return RemoteServer(REMOTE_HOST, REMOTE_USER)

    def _run(self, cmd_str: str, timeout: int = 60) -> str:
        """Run *cmd_str* on the HPC via SSH. Returns stdout or raises SlurmError."""
        out, err = self._server().run_command(cmd_str, timeout=timeout)
        if out is None:
            raise SlurmError(command=cmd_str, return_code=None, stderr=err or "")
        return out

    def sbatch(self, script_path: str, script_args: Optional[List[str]] = None) -> str:
        """Submit a job via sbatch on the HPC. Returns the SLURM job ID."""
        args_str = " ".join(script_args) if script_args else ""
        cmd = f"sbatch {script_path} {args_str}".strip()
        out = self._run(cmd)
        match = re.search(r"Submitted batch job (\d+)", out)
        if not match:
            raise SlurmError(command=cmd, return_code=0,
                             stderr=f"Could not parse job ID from sbatch output: {out!r}")
        return match.group(1)

    def squeue(self, job_ids: Optional[List[str]] = None,
               user: Optional[str] = None) -> List[Dict[str, str]]:
        """Query job status via squeue on the HPC."""
        cmd = "squeue --noheader --format='%i|%T|%j|%M|%P|%N'"
        if job_ids:
            cmd += f" --jobs={','.join(job_ids)}"
        if user:
            cmd += f" --user={user}"
        try:
            out = self._run(cmd)
        except SlurmError:
            return []
        jobs = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id":    parts[0].strip(),
                    "state":     parts[1].strip(),
                    "name":      parts[2].strip(),
                    "time":      parts[3].strip(),
                    "partition": parts[4].strip(),
                    "nodelist":  parts[5].strip(),
                })
        return jobs

    def sacct(self, job_ids: List[str]) -> List[Dict[str, str]]:
        """Query completed job info via sacct on the HPC."""
        ids_str = ",".join(job_ids)
        cmd = (
            f"sacct --noheader --parsable2 "
            f"--format=JobID,State,ExitCode,Elapsed,Start,End "
            f"--jobs={ids_str}"
        )
        try:
            out = self._run(cmd)
        except SlurmError:
            return []
        jobs = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id":    parts[0].strip(),
                    "state":     parts[1].strip(),
                    "exit_code": parts[2].strip(),
                    "elapsed":   parts[3].strip(),
                    "start":     parts[4].strip(),
                    "end":       parts[5].strip(),
                })
        return jobs

    def scancel(self, job_id: str) -> None:
        """Cancel a SLURM job on the HPC."""
        self._run(f"scancel {job_id}")

    def is_available(self) -> bool:
        """Check if SLURM is reachable on the HPC."""
        try:
            self._run("squeue --version", timeout=10)
            return True
        except Exception:
            return False
