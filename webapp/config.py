"""
Application configuration for the DrugCLIP web application.

Override SECRET_KEY in production via environment variable or a local config file.
"""

import os

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

# The directory containing this file (webapp/)
_WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))

# The project root is the parent of the webapp/ directory
PROJECT_ROOT = os.path.dirname(_WEBAPP_DIR)

# Directory where user-uploaded files are stored (per-session subdirectories)
UPLOAD_FOLDER = os.path.join(_WEBAPP_DIR, "uploads")

# Directory used by flask-session to store server-side session files
SESSION_FILE_DIR = os.path.join(_WEBAPP_DIR, "flask_session")

# ---------------------------------------------------------------------------
# Pre-encoded library paths
# ---------------------------------------------------------------------------

# Directory containing pre-built molecule LMDB files
LIBRARIES_DIR = os.path.join(PROJECT_ROOT, "data", "libraries")

# Directory containing pre-encoded molecule embedding caches
ENCODED_MOL_EMBS_DIR = os.path.join(PROJECT_ROOT, "data", "encoded_mol_embs")


def list_available_libraries():
    """Return a list of pre-built libraries available for screening.

    Scans LIBRARIES_DIR for .lmdb files and checks whether a matching
    pre-encoded embedding cache exists in ENCODED_MOL_EMBS_DIR.

    Returns
    -------
    list[dict]
        Each dict has keys:
        - ``name``: library stem (e.g. "enamine_dds10")
        - ``lmdb_path``: absolute path to the LMDB file
        - ``cache_dirs``: dict mapping fold_version -> cache_dir path (only
          entries where all fold pkl files exist are included)
        - ``has_cache``: True if at least one complete fold cache exists
        - ``compound_count``: number of entries in the LMDB (or None on error)
    """
    import lmdb as _lmdb

    libraries = []

    if not os.path.isdir(LIBRARIES_DIR):
        return libraries

    for fname in sorted(os.listdir(LIBRARIES_DIR)):
        if not fname.endswith(".lmdb"):
            continue
        name = fname[:-5]  # strip .lmdb
        lmdb_path = os.path.join(LIBRARIES_DIR, fname)

        # Count compounds in the LMDB
        compound_count = None
        try:
            env = _lmdb.open(lmdb_path, readonly=True, lock=False, subdir=False)
            with env.begin() as txn:
                compound_count = txn.stat()["entries"]
            env.close()
        except Exception:
            pass

        # Check for pre-encoded caches — both flat layout (legacy) and
        # hash-subdirectory layout (new).
        cache_dirs = {}

        for fold_version, n_folds in [("6_folds", 6), ("8_folds", 8)]:
            # Flat layout: data/encoded_mol_embs/<fold_version>/
            flat_dir = os.path.join(ENCODED_MOL_EMBS_DIR, fold_version)
            if all(
                os.path.exists(os.path.join(flat_dir, f"fold{i}.pkl"))
                for i in range(n_folds)
            ):
                cache_dirs[fold_version] = flat_dir
                continue

            # Named layout: data/encoded_mol_embs/<name>/<fold_version>/
            named_dir = os.path.join(ENCODED_MOL_EMBS_DIR, name, fold_version)
            if all(
                os.path.exists(os.path.join(named_dir, f"fold{i}.pkl"))
                for i in range(n_folds)
            ):
                cache_dirs[fold_version] = named_dir

        libraries.append({
            "name": name,
            "lmdb_path": lmdb_path,
            "cache_dirs": cache_dirs,
            "has_cache": bool(cache_dirs),
            "compound_count": compound_count,
        })

    return libraries

# ---------------------------------------------------------------------------
# File upload limits
# ---------------------------------------------------------------------------

# Maximum allowed upload size: 500 MB
MAX_FILE_SIZE = 500 * 1024 * 1024  # bytes

# Flask uses MAX_CONTENT_LENGTH to enforce upload size limits at the WSGI layer
MAX_CONTENT_LENGTH = MAX_FILE_SIZE

# Allowed file extensions per upload type
ALLOWED_EXTENSIONS = {
    "pdb": {".pdb"},
    "library": {".sdf", ".smi", ".smiles", ".txt"},
    "ligand": {".pdb", ".sdf"},
}

# ---------------------------------------------------------------------------
# Session configuration
# ---------------------------------------------------------------------------

# Use the filesystem backend for server-side sessions (flask-session)
SESSION_TYPE = "filesystem"

# Secret key used to sign session cookies.
# IMPORTANT: Override this with a strong random value in production.
SECRET_KEY = "change-me-in-production"  # noqa: S105

# ---------------------------------------------------------------------------
# Job monitoring
# ---------------------------------------------------------------------------

# How often (in seconds) the background thread polls SLURM for job status
POLL_INTERVAL = 30  # seconds

# ---------------------------------------------------------------------------
# Results viewer
# ---------------------------------------------------------------------------

# Number of result rows displayed per page in the results table
RESULTS_PER_PAGE = 50

# Number of jobs displayed per page in the dashboard jobs table
JOBS_PER_PAGE = 10

# ---------------------------------------------------------------------------
# Screening parameter defaults
# ---------------------------------------------------------------------------

# Default pocket extraction radius in Ångströms
DEFAULT_CUTOFF = 10.0

# Default fraction of the library to return as hits (top 2%)
DEFAULT_TOP_FRACTION = 0.02

# Default number of compounds per chunk for large-scale screening
DEFAULT_CHUNK_SIZE = 1_000_000

# Default SLURM partition for large-scale screening jobs
DEFAULT_PARTITION = "ga100"

# Default maximum number of parallel SLURM jobs for large-scale screening
DEFAULT_MAX_PARALLEL = 50
