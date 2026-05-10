#!/bin/bash
# ============================================================================
# DrugCLIP screening pipeline — called by submit_screening.sh
# Handles pocket extraction, library conversion, and screening in one go.
#
# Each job gets its own directory under jobs/:
#   jobs/<target>_vs_<library>/
#     results.txt        — screening hits
#     slurm_<id>.log     — symlinked SLURM log
# ============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
usage() {
    echo "Usage: $0 <receptor.pdb> <library.sdf|smi> [options]"
    echo ""
    echo "Binding site (one required):"
    echo "  --ligand <file>        Ligand file to define binding site"
    echo "  --residue <name>       HETATM residue name in the PDB"
    echo "  --center <x> <y> <z>  Explicit binding site coordinates"
    echo "  --binding-residues <n> [n...]  Residue numbers (e.g., 45 67 89)"
    echo ""
    echo "Optional:"
    echo "  --cutoff <float>       Pocket radius in Å (default: 10.0)"
    echo "  --chain <id>           Chain ID for --binding-residues (default: all)"
    echo "  --name <string>        Target name (default: from PDB filename)"
    echo "  --jobs-dir <path>      Top-level jobs directory (default: jobs/)"
    echo "  --top-fraction <float> Fraction of library to return (default: 0.02 = top 2%)"
    exit 1
}

if [ $# -lt 2 ]; then
    usage
fi

PDB_FILE="$1"
LIBRARY_FILE="$2"
shift 2

LIGAND=""
RESIDUE=""
CENTER=""
BINDING_RESIDUES=""
CHAIN=""
CUTOFF="10.0"
TARGET_NAME=""
JOBS_DIR="jobs"
TOP_FRACTION="0.02"

while [ $# -gt 0 ]; do
    case "$1" in
        --ligand)
            LIGAND="$2"; shift 2 ;;
        --residue)
            RESIDUE="$2"; shift 2 ;;
        --center)
            CENTER="$2 $3 $4"; shift 4 ;;
        --binding-residues)
            shift
            BINDING_RESIDUES=""
            while [ $# -gt 0 ] && [[ ! "$1" == --* ]]; do
                BINDING_RESIDUES="$BINDING_RESIDUES $1"
                shift
            done
            BINDING_RESIDUES="${BINDING_RESIDUES# }"
            ;;
        --chain)
            CHAIN="$2"; shift 2 ;;
        --cutoff)
            CUTOFF="$2"; shift 2 ;;
        --name)
            TARGET_NAME="$2"; shift 2 ;;
        --jobs-dir)
            JOBS_DIR="$2"; shift 2 ;;
        --top-fraction)
            TOP_FRACTION="$2"; shift 2 ;;
        *)
            echo "Unknown option: $1"; usage ;;
    esac
done

# Validate inputs
if [ ! -f "$PDB_FILE" ]; then
    echo "Error: PDB file not found: $PDB_FILE"
    exit 1
fi
if [ ! -f "$LIBRARY_FILE" ]; then
    echo "Error: Library file not found: $LIBRARY_FILE"
    exit 1
fi
if [ -z "$LIGAND" ] && [ -z "$RESIDUE" ] && [ -z "$CENTER" ] && [ -z "$BINDING_RESIDUES" ]; then
    echo "Error: Must specify one of --ligand, --residue, --center, or --binding-residues"
    usage
fi

# Derive target name from PDB filename if not provided
if [ -z "$TARGET_NAME" ]; then
    TARGET_NAME=$(basename "$PDB_FILE" .pdb)
fi

# Derive library name from filename (without extension)
LIB_BASENAME=$(basename "$LIBRARY_FILE")
LIB_NAME="${LIB_BASENAME%.*}"

# ---------------------------------------------------------------------------
# Create job directory
# ---------------------------------------------------------------------------
JOB_NAME="${TARGET_NAME}_vs_${LIB_NAME}"
JOB_DIR="${JOBS_DIR}/${JOB_NAME}"
mkdir -p "$JOB_DIR"

# Write job dir path for submit_screening.sh to pick up (for SLURM log symlink)
if [ -n "${SLURM_JOB_ID:-}" ]; then
    echo "$JOB_DIR" > "jobs/logs/.job_dir_${SLURM_JOB_ID}"
fi

echo "============================================"
echo "DrugCLIP Virtual Screening Pipeline"
echo "============================================"
echo "Target:    $TARGET_NAME ($PDB_FILE)"
echo "Library:   $LIB_NAME ($LIBRARY_FILE)"
echo "Job dir:   $JOB_DIR/"
echo "============================================"

# ---------------------------------------------------------------------------
# Step 1: Extract pocket
# ---------------------------------------------------------------------------
POCKET_DIR="data/targets/${TARGET_NAME}"
POCKET_LMDB="${POCKET_DIR}/pocket.lmdb"

if [ -f "$POCKET_LMDB" ]; then
    echo ""
    echo "[Step 1/3] Pocket LMDB already exists: $POCKET_LMDB (skipping)"
else
    echo ""
    echo "[Step 1/3] Extracting binding pocket..."

    POCKET_ARGS="--pdb $PDB_FILE --output $POCKET_LMDB --name $TARGET_NAME --cutoff $CUTOFF"

    if [ -n "$LIGAND" ]; then
        POCKET_ARGS="$POCKET_ARGS --ligand $LIGAND"
    elif [ -n "$RESIDUE" ]; then
        echo "  Computing centroid of residue $RESIDUE..."
        CENTER=$(grep "HETATM.*$RESIDUE" "$PDB_FILE" \
            | awk '{x+=$7; y+=$8; z+=$9; n++} END {printf "%.3f %.3f %.3f", x/n, y/n, z/n}')
        if [ -z "$CENTER" ]; then
            echo "Error: Residue $RESIDUE not found in $PDB_FILE"
            exit 1
        fi
        echo "  Residue centroid: $CENTER"
        POCKET_ARGS="$POCKET_ARGS --center $CENTER"
    elif [ -n "$BINDING_RESIDUES" ]; then
        POCKET_ARGS="$POCKET_ARGS --binding-residues $BINDING_RESIDUES"
        if [ -n "$CHAIN" ]; then
            POCKET_ARGS="$POCKET_ARGS --chain $CHAIN"
        fi
    elif [ -n "$CENTER" ]; then
        POCKET_ARGS="$POCKET_ARGS --center $CENTER"
    fi

    python utils/pdb_to_pocket_lmdb.py $POCKET_ARGS
fi

# ---------------------------------------------------------------------------
# Step 2: Convert compound library to LMDB (with deduplication)
# ---------------------------------------------------------------------------
MOL_LMDB="data/libraries/${LIB_NAME}.lmdb"

if [ -f "$MOL_LMDB" ]; then
    echo ""
    echo "[Step 2/3] Molecule LMDB already exists: $MOL_LMDB (skipping)"
else
    echo ""
    echo "[Step 2/3] Converting compound library to LMDB..."
    python utils/sdf_to_mol_lmdb.py --input "$LIBRARY_FILE" --output "$MOL_LMDB"fi

# ---------------------------------------------------------------------------
# Step 3: Run screening
# ---------------------------------------------------------------------------
SAVE_PATH="${JOB_DIR}/results.txt"

echo ""
echo "[Step 3/3] Running DrugCLIP screening..."
echo "  Pocket: $POCKET_LMDB"
echo "  Library: $MOL_LMDB"
echo "  Output: $SAVE_PATH"

python ./unimol/retrieval.py --user-dir ./unimol "./dict" --valid-subset test \
       --num-workers 8 --ddp-backend=c10d --batch-size 4 \
       --task drugclip --loss in_batch_softmax --arch drugclip \
       --max-pocket-atoms 511 \
       --fp16 --fp16-init-scale 4 --fp16-scale-window 256 --seed 1 \
       --log-interval 100 --log-format simple \
       --mol-path "$MOL_LMDB" \
       --pocket-path "$POCKET_LMDB" \
       --fold-version 6_folds \
       --save-path "$SAVE_PATH" \
       --top-fraction "$TOP_FRACTION"

echo ""
echo "============================================"
echo "Screening complete!"
echo "Results: $SAVE_PATH"
echo "Top 5 hits:"
head -5 "$SAVE_PATH"
echo "============================================"
