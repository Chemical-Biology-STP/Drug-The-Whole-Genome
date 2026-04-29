"""Low-level wrapper around SLURM CLI commands.

All subprocess calls to sbatch, squeue, sacct, and scancel are centralized
here. Raises SlurmError on failures.
"""

from __future__ import annotations

import re
import subprocess
from typing import Dict, List, Optional


class SlurmError(Exception):
    """Raised when a SLURM command fails.

    Attributes:
        command: The command that was executed.
        return_code: The process return code (None if timed out).
        stderr: The stderr output from the command.
    """

    def __init__(self, command: str, return_code: Optional[int], stderr: str):
        self.command = command
        self.return_code = return_code
        self.stderr = stderr
        super().__init__(
            f"SLURM command failed: {command!r} "
            f"(return_code={return_code}, stderr={stderr!r})"
        )


class SlurmClient:
    """Low-level wrapper around SLURM CLI commands."""

    def _run(self, cmd: List[str], timeout: int = 30) -> subprocess.CompletedProcess:
        """Execute a command via subprocess with capture, timeout, and error handling.

        Args:
            cmd: Command and arguments as a list of strings.
            timeout: Maximum seconds to wait for the command to complete.

        Returns:
            The CompletedProcess result on success.

        Raises:
            SlurmError: If the command returns non-zero or times out.
        """
        cmd_str = " ".join(cmd)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise SlurmError(
                command=cmd_str,
                return_code=None,
                stderr=f"Command timed out after {timeout} seconds",
            )

        if result.returncode != 0:
            raise SlurmError(
                command=cmd_str,
                return_code=result.returncode,
                stderr=result.stderr.strip(),
            )

        return result

    def sbatch(self, script_path: str, script_args: Optional[List[str]] = None) -> str:
        """Submit a job via sbatch.

        Args:
            script_path: Path to the SLURM batch script.
            script_args: Optional arguments to pass to the script.

        Returns:
            The SLURM job ID string parsed from sbatch stdout.

        Raises:
            SlurmError: If sbatch fails or the output cannot be parsed.
        """
        cmd = ["sbatch", script_path]
        if script_args:
            cmd.extend(script_args)

        result = self._run(cmd)

        # sbatch outputs: "Submitted batch job 12345"
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        if not match:
            raise SlurmError(
                command=" ".join(cmd),
                return_code=0,
                stderr=f"Could not parse job ID from sbatch output: {result.stdout!r}",
            )

        return match.group(1)

    def squeue(
        self,
        job_ids: Optional[List[str]] = None,
        user: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """Query job status via squeue.

        Args:
            job_ids: Optional list of job IDs to query.
            user: Optional username to filter by.

        Returns:
            List of dicts with keys: job_id, state, name, time, partition, nodelist.
        """
        cmd = [
            "squeue",
            "--noheader",
            "--format=%i|%T|%j|%M|%P|%N",
        ]
        if job_ids:
            cmd.extend(["--jobs", ",".join(job_ids)])
        if user:
            cmd.extend(["--user", user])

        result = self._run(cmd)

        jobs = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id": parts[0].strip(),
                    "state": parts[1].strip(),
                    "name": parts[2].strip(),
                    "time": parts[3].strip(),
                    "partition": parts[4].strip(),
                    "nodelist": parts[5].strip(),
                })

        return jobs

    def sacct(self, job_ids: List[str]) -> List[Dict[str, str]]:
        """Query completed job info via sacct.

        Args:
            job_ids: List of job IDs to query.

        Returns:
            List of dicts with keys: job_id, state, exit_code, elapsed, start, end.
        """
        cmd = [
            "sacct",
            "--noheader",
            "--parsable2",
            "--format=JobID,State,ExitCode,Elapsed,Start,End",
            "--jobs=" + ",".join(job_ids),
        ]

        result = self._run(cmd)

        jobs = []
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("|")
            if len(parts) >= 6:
                jobs.append({
                    "job_id": parts[0].strip(),
                    "state": parts[1].strip(),
                    "exit_code": parts[2].strip(),
                    "elapsed": parts[3].strip(),
                    "start": parts[4].strip(),
                    "end": parts[5].strip(),
                })

        return jobs

    def scancel(self, job_id: str) -> None:
        """Cancel a SLURM job.

        Args:
            job_id: The SLURM job ID to cancel.

        Raises:
            SlurmError: If scancel fails.
        """
        self._run(["scancel", job_id])

    def is_available(self) -> bool:
        """Check if SLURM commands are accessible.

        Returns:
            True if squeue can be executed, False otherwise.
        """
        try:
            subprocess.run(
                ["squeue", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            return False
