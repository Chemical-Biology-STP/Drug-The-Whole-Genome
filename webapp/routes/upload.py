"""Chunked file upload endpoint for large compound libraries.

Implements a simple resumable upload protocol:

  POST   /upload/library
         Headers: X-Upload-Id, X-Chunk-Index, X-Total-Chunks, X-Filename
         Body:    raw chunk bytes
         Returns: {"upload_id": "...", "received": N, "total": N, "done": bool,
                   "path": "..." (only when done=true)}

  GET    /upload/library/<upload_id>
         Returns: {"upload_id": "...", "received_chunks": [...], "filename": "..."}
         Used by the client to resume after a dropped connection.

  DELETE /upload/library/<upload_id>
         Cancels and cleans up an in-progress upload.

Chunks are written to a temp directory keyed by upload_id. On the final
chunk the pieces are assembled into a single file and the temp chunks are
removed.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid

from flask import Blueprint, jsonify, request, session

from webapp.config import ALLOWED_EXTENSIONS, UPLOAD_FOLDER
from webapp.services.validation import validate_file_extension

upload_bp = Blueprint("upload", __name__, url_prefix="/upload")

# Max individual chunk size: 10 MB
MAX_CHUNK_SIZE = 10 * 1024 * 1024

# Where in-progress uploads live: webapp/uploads/<email>/chunks/<upload_id>/
def _chunk_dir(email: str, upload_id: str) -> str:
    return os.path.join(UPLOAD_FOLDER, email, "chunks", upload_id)

def _final_path(email: str, upload_id: str, filename: str) -> str:
    return os.path.join(UPLOAD_FOLDER, email, filename)

def _meta_path(chunk_dir: str) -> str:
    return os.path.join(chunk_dir, "_meta.json")

def _load_meta(chunk_dir: str) -> dict:
    with open(_meta_path(chunk_dir)) as f:
        return json.load(f)

def _save_meta(chunk_dir: str, meta: dict) -> None:
    with open(_meta_path(chunk_dir), "w") as f:
        json.dump(meta, f)


@upload_bp.route("/library", methods=["POST"])
def receive_chunk():
    """Receive one chunk of a library file upload."""
    email = session.get("email", "")
    if not email:
        return jsonify({"error": "Not authenticated"}), 401

    # Read headers
    upload_id   = request.headers.get("X-Upload-Id", "").strip()
    chunk_index = request.headers.get("X-Chunk-Index", "").strip()
    total_chunks = request.headers.get("X-Total-Chunks", "").strip()
    filename    = request.headers.get("X-Filename", "").strip()

    # Validate
    if not filename or not validate_file_extension(filename, "library"):
        return jsonify({"error": "Filename must end in .sdf, .smi, .smiles, or .txt"}), 400

    try:
        chunk_index  = int(chunk_index)
        total_chunks = int(total_chunks)
    except (ValueError, TypeError):
        return jsonify({"error": "Invalid chunk index or total"}), 400

    if not upload_id:
        upload_id = str(uuid.uuid4())

    chunk_data = request.get_data()
    if len(chunk_data) > MAX_CHUNK_SIZE:
        return jsonify({"error": "Chunk exceeds 50 MB limit"}), 413

    # Create chunk directory and meta on first chunk
    cdir = _chunk_dir(email, upload_id)
    os.makedirs(cdir, exist_ok=True)

    meta_file = _meta_path(cdir)
    if not os.path.exists(meta_file):
        _save_meta(cdir, {
            "upload_id": upload_id,
            "filename": filename,
            "total_chunks": total_chunks,
            "received_chunks": [],
        })

    meta = _load_meta(cdir)

    # Write this chunk
    chunk_path = os.path.join(cdir, f"chunk_{chunk_index:06d}")
    with open(chunk_path, "wb") as f:
        f.write(chunk_data)

    if chunk_index not in meta["received_chunks"]:
        meta["received_chunks"].append(chunk_index)
    _save_meta(cdir, meta)

    received = len(meta["received_chunks"])
    done = received >= total_chunks

    if done:
        # Assemble all chunks into the final file using cat for speed
        upload_dir = os.path.join(UPLOAD_FOLDER, email)
        os.makedirs(upload_dir, exist_ok=True)
        final = _final_path(email, upload_id, filename)

        # Build sorted list of chunk paths
        chunk_paths = [
            os.path.join(cdir, f"chunk_{i:06d}")
            for i in range(total_chunks)
        ]

        # Use shell cat for fast concatenation (avoids Python read/write overhead)
        import subprocess
        with open(final, "wb") as out_f:
            proc = subprocess.run(
                ["cat"] + chunk_paths,
                stdout=out_f,
                stderr=subprocess.PIPE,
            )
        if proc.returncode != 0:
            return jsonify({"error": "Assembly failed: " + proc.stderr.decode()}), 500

        # Clean up chunks
        shutil.rmtree(cdir, ignore_errors=True)

        return jsonify({
            "upload_id": upload_id,
            "received": received,
            "total": total_chunks,
            "done": True,
            "path": final,
            "filename": filename,
        })

    return jsonify({
        "upload_id": upload_id,
        "received": received,
        "total": total_chunks,
        "done": False,
    })


@upload_bp.route("/library/<upload_id>", methods=["GET"])
def upload_status(upload_id: str):
    """Return which chunks have been received (for resume)."""
    email = session.get("email", "")
    if not email:
        return jsonify({"error": "Not authenticated"}), 401

    cdir = _chunk_dir(email, upload_id)
    if not os.path.isdir(cdir):
        return jsonify({"error": "Upload not found"}), 404

    meta = _load_meta(cdir)
    return jsonify({
        "upload_id": upload_id,
        "received_chunks": meta.get("received_chunks", []),
        "total_chunks": meta.get("total_chunks"),
        "filename": meta.get("filename"),
    })


@upload_bp.route("/library/<upload_id>", methods=["DELETE"])
def cancel_upload(upload_id: str):
    """Cancel and clean up an in-progress upload."""
    email = session.get("email", "")
    if not email:
        return jsonify({"error": "Not authenticated"}), 401

    cdir = _chunk_dir(email, upload_id)
    if os.path.isdir(cdir):
        shutil.rmtree(cdir, ignore_errors=True)

    return jsonify({"ok": True})
