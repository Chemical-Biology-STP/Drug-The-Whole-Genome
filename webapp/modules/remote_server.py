"""SSH and SCP helpers for DrugCLIP — mirrors app_ProtPrep/modules/remote_server.py.

Assumes passwordless SSH key authentication between the web server and the HPC.
"""

from __future__ import annotations

import subprocess


class RemoteServer:
    """Thin wrapper around SSH/SCP subprocess calls."""

    def __init__(self, host: str, user: str):
        self.host = host
        self.user = user

    def run_command(self, command: str, timeout: int = 2400) -> tuple[str | None, str | None]:
        """Run *command* on the remote host via SSH."""
        try:
            from webapp.modules.safety_monitor import get_safety_monitor
            get_safety_monitor().record_ssh_connection()
        except Exception:
            pass

        ssh_cmd = f"ssh {self.user}@{self.host} '{command}'"
        try:
            result = subprocess.run(
                ssh_cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                return result.stdout.strip(), None
            return None, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return None, "SSH error: command timed out"
        except Exception as exc:
            return None, f"SSH error: {exc}"

    def upload_file(self, local_path: str, remote_path: str, timeout: int = 300) -> tuple[bool, str | None]:
        """Upload a file to the HPC via SCP."""
        scp_cmd = f"scp {local_path} {self.user}@{self.host}:{remote_path}"
        try:
            result = subprocess.run(
                scp_cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                return True, None
            return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "SCP upload timed out"
        except Exception as exc:
            return False, f"SCP error: {exc}"

    def download_file(self, remote_path: str, local_path: str, timeout: int = 600) -> tuple[bool, str | None]:
        """Download a file from the HPC via SCP."""
        scp_cmd = f"scp {self.user}@{self.host}:{remote_path} {local_path}"
        try:
            result = subprocess.run(
                scp_cmd, shell=True, capture_output=True, text=True, timeout=timeout,
            )
            if result.returncode == 0:
                return True, None
            return False, result.stderr.strip()
        except subprocess.TimeoutExpired:
            return False, "SCP download timed out"
        except Exception as exc:
            return False, f"SCP error: {exc}"

    def create_remote_directory(self, remote_path: str) -> tuple[bool, str | None]:
        """Create *remote_path* (and parents) on the HPC."""
        out, err = self.run_command(f"mkdir -p {remote_path}")
        return err is None, err

    def check_file_exists(self, remote_path: str) -> bool:
        """Return True if *remote_path* exists on the HPC."""
        out, _ = self.run_command(f"test -f {remote_path} && echo exists")
        return (out or "").strip() == "exists"
