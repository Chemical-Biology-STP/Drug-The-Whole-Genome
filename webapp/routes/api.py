"""JSON API endpoints for the DrugCLIP web application."""

from __future__ import annotations

from flask import Blueprint, jsonify, session

from webapp.config import REMOTE_HOST, REMOTE_LIBRARIES_DIR, REMOTE_USER
from webapp.modules.remote_server import RemoteServer

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/libraries", methods=["GET"])
def list_libraries():
    """Return a JSON list of compound libraries saved on the HPC.

    Lists files in REMOTE_LIBRARIES_DIR, returning name, size, and full path
    for each. Used by the dashboard to populate the 'Saved libraries' dropdown.
    """
    if "email" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    server = RemoteServer(REMOTE_HOST, REMOTE_USER)

    # Create the directory if it doesn't exist yet
    server.run_command(f"mkdir -p {REMOTE_LIBRARIES_DIR}")

    # List files with sizes: "bytes filename"
    out, err = server.run_command(
        f"find {REMOTE_LIBRARIES_DIR} -maxdepth 1 -type f "
        f"\\( -name '*.sdf' -o -name '*.smi' -o -name '*.smiles' -o -name '*.txt' \\) "
        f"-printf '%s\\t%f\\n' 2>/dev/null | sort -k2"
    )

    libraries = []
    if out:
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                size_bytes = int(parts[0])
                filename = parts[1]
                libraries.append({
                    "name": filename,
                    "path": f"{REMOTE_LIBRARIES_DIR}/{filename}",
                    "size": _format_size(size_bytes),
                    "size_bytes": size_bytes,
                })

    return jsonify({"libraries": libraries})


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 ** 3:
        return f"{size_bytes / 1024**2:.1f} MB"
    return f"{size_bytes / 1024**3:.2f} GB"
