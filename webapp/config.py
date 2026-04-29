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
