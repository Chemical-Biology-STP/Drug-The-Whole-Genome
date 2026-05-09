"""
Application configuration for the DrugCLIP web application (SSH/HPC edition).
"""

import os

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_WEBAPP_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(_WEBAPP_DIR)

# Local directories (on the web server)
UPLOAD_FOLDER = os.path.join(_WEBAPP_DIR, "uploads")
SESSION_FILE_DIR = os.path.join(_WEBAPP_DIR, "flask_session")

# SQLite user database
DB_FILE = os.path.join(_WEBAPP_DIR, "data", "users.db")

# ---------------------------------------------------------------------------
# Remote HPC connection
# ---------------------------------------------------------------------------

REMOTE_HOST = "login.nemo.thecrick.org"
REMOTE_USER = "yipy"

# Base directory on the HPC where DrugCLIP is installed
REMOTE_PROJECT_ROOT = "/nemo/stp/chemicalbiology/home/shared/software/drugclip"

# Directory on the HPC where per-job directories live
REMOTE_JOBS_DIR = f"{REMOTE_PROJECT_ROOT}/jobs"

# ---------------------------------------------------------------------------
# SLURM settings (standard screening job)
# ---------------------------------------------------------------------------

SLURM_PARTITION = "ga100"
SLURM_GPUS = 1
SLURM_CPUS = 8
SLURM_MEM = "64G"
SLURM_TIME = "04:00:00"

# ---------------------------------------------------------------------------
# File upload limits
# ---------------------------------------------------------------------------

MAX_FILE_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_CONTENT_LENGTH = MAX_FILE_SIZE

ALLOWED_EXTENSIONS = {
    "pdb":     {".pdb"},
    "library": {".sdf", ".smi", ".smiles", ".txt"},
    "ligand":  {".pdb", ".sdf"},
}

# ---------------------------------------------------------------------------
# Session configuration (flask-session, filesystem backend)
# ---------------------------------------------------------------------------

SESSION_TYPE = "filesystem"

# ---------------------------------------------------------------------------
# Flask / auth
# ---------------------------------------------------------------------------

SECRET_KEY = os.environ.get("SECRET_KEY", "drugclip-change-me-in-production")
ADMIN_EMAIL = "yewmun.yip@crick.ac.uk"

# ---------------------------------------------------------------------------
# Flask app URLs
# ---------------------------------------------------------------------------

APP_BASE_URL = "http://10.0.208.23:8017"
PORTAL_URL = "http://10.0.208.23:8000"

# ---------------------------------------------------------------------------
# Job monitoring
# ---------------------------------------------------------------------------

POLL_INTERVAL = 120  # seconds between background polls

# ---------------------------------------------------------------------------
# Results viewer
# ---------------------------------------------------------------------------

RESULTS_PER_PAGE = 50

# ---------------------------------------------------------------------------
# Screening parameter defaults
# ---------------------------------------------------------------------------

DEFAULT_CUTOFF = 10.0
DEFAULT_TOP_FRACTION = 0.02
DEFAULT_CHUNK_SIZE = 1_000_000
DEFAULT_PARTITION = "ga100"
DEFAULT_MAX_PARALLEL = 50
