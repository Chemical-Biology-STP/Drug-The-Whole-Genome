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
    # List SDF/SMI files — use separate ls calls to avoid glob-no-match failures
    lib_dir = REMOTE_LIBRARIES_DIR
    files = []
    for ext in ("sdf", "smi", "smiles", "txt"):
        out, _ = server.run_command(f"ls {lib_dir}/*.{ext} 2>/dev/null")
        if out:
            files.extend(p.strip() for p in out.splitlines() if p.strip())

    import logging
    logging.getLogger(__name__).info("API libraries files: %s", files)

    libraries = []
    for filepath in sorted(files):
        filename = filepath.split("/")[-1]
        size_out, _ = server.run_command(f"wc -c < {filepath} 2>/dev/null")
        try:
            size_bytes = int((size_out or "0").strip())
        except ValueError:
            size_bytes = 0
        libraries.append({
            "name": filename,
            "path": filepath,
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
