#!/bin/bash
# ============================================================================
# DrugCLIP Large-Scale Virtual Screening Pipeline
#
# Designed for billion-scale compound libraries. Splits the work into
# parallelizable stages submitted as SLURM job arrays.
#
# Usage:
#   bash submit_large_screening.sh <receptor.pdb> <library.sdf|smi> [options]
#
# Required arguments:
#   $1  Receptor PDB file
#   $2  Compound library (SDF, SMI, or SMILES file)
#
# Binding site definition (one required):
#   --ligand <file>        Ligand file (PDB/SDF) to define binding site center
#   --residue <name>       HETATM residue name in the PDB (e.g., JHN)
#   --center <x> <y> <z>  Explicit binding site coordinates
#   --binding-residues <n> [n...]  Protein residue numbers
#
# Optional:
#   --cutoff <float>       Pocket extraction radius in Å (default: 10.0)
#   --chain <id>           Chain ID for --binding-residues
#   --name <string>        Target name (default: from PDB filename)
#   --chunk-size <int>     Molecules per chunk (default: 1,000,000)
#   --partition <name>     SLURM partition (default: ga100)
#   --max-parallel <int>   Max parallel SLURM jobs (default: 50)
#   --jobs-dir <path>      Top-level jobs directory (default: jobs/)
#
# Pipeline stages:
#   1. Split input file into chunks (runs locally, fast)
#   2. Pocket extraction (runs locally, fast)
#   3. Convert chunks to LMDB (SLURM array, CPU-only)
#   4. Encode molecule embeddings (SLURM array, GPU)
#   5. Stream-score against pocket (single GPU job)
#
# Example:
#   bash submit_large_screening.sh receptor.pdb enamine_real_15B.smi \
#       --residue JHN --chunk-size 2000000 --max-parallel 100
# ============================================================================

set -euo pipefail

# Always run from the project root regardless of where the script was called from
cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
DRUGCLIP_ROOT="$PWD"

export PATH="/camp/home/yipy/.pixi/bin:$PATH"

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
    echo "  --binding-residues <n> [n...]  Residue numbers"
    echo ""
    echo "Optional:"
    echo "  --cutoff <float>       Pocket radius in Å (default: 10.0)"
    echo "  --chain <id>           Chain ID for --binding-residues"
    echo "  --name <string>        Target name (default: from PDB filename)"
    echo "  --chunk-size <int>     Molecules per chunk (default: 1,000,000)"
    echo "  --partition <name>     SLURM partition (default: ga100)"
    echo "  --max-parallel <int>   Max parallel jobs (default: 50)"
    echo "  --jobs-dir <path>      Jobs directory (default: jobs/)"
    echo "  --top-fraction <float> Fraction of library to return (default: 0.02)"
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
CHUNK_SIZE=1000000
PARTITION="ga100"
MAX_PARALLEL=50
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
        --chunk-size)
            CHUNK_SIZE="$2"; shift 2 ;;
        --partition)
            PARTITION="$2"; shift 2 ;;
        --max-parallel)
            MAX_PARALLEL="$2"; shift 2 ;;
        --jobs-dir)
            JOBS_DIR="$2"; shift 2 ;;
        --top-fraction)
            TOP_FRACTION="$2"; shift 2 ;;
        *)
            echo "Unknown option: $1"; usage ;;
    esac
done

# Validate
if [ ! -f "$PDB_FILE" ]; then echo "Error: PDB not found: $PDB_FILE"; exit 1; fi
if [ ! -f "$LIBRARY_FILE" ]; then echo "Error: Library not found: $LIBRARY_FILE"; exit 1; fi
if [ -z "$LIGAND" ] && [ -z "$RESIDUE" ] && [ -z "$CENTER" ] && [ -z "$BINDING_RESIDUES" ]; then
    echo "Error: Must specify binding site"; usage
fi

if [ -z "$TARGET_NAME" ]; then
    TARGET_NAME=$(basename "$PDB_FILE" .pdb)
fi
LIB_BASENAME=$(basename "$LIBRARY_FILE")
LIB_NAME="${LIB_BASENAME%.*}"
JOB_NAME="${TARGET_NAME}_vs_${LIB_NAME}"
JOB_DIR="${JOBS_DIR}/${JOB_NAME}"
CHUNK_DIR="${JOB_DIR}/chunks"
LMDB_DIR="data/libraries/${LIB_NAME}_chunks"
EMB_DIR="data/encoded_mol_embs/${LIB_NAME}"

mkdir -p "$JOB_DIR" "${JOBS_DIR}/logs"

echo "============================================"
echo "DrugCLIP Large-Scale Screening Pipeline"
echo "============================================"
echo "Target:      $TARGET_NAME"
echo "Library:     $LIB_NAME"
echo "Chunk size:  ${CHUNK_SIZE}"
echo "Partition:   $PARTITION"
echo "Max parallel: $MAX_PARALLEL"
echo "Job dir:     $JOB_DIR/"
echo "============================================"

# ===========================================================================
# Stage 1: Split input file into chunks (local, fast)
# ===========================================================================
echo ""
echo "[Stage 1/5] Splitting input file into chunks..."

if [ -f "${CHUNK_DIR}/manifest.txt" ]; then
    N_CHUNKS=$(wc -l < "${CHUNK_DIR}/manifest.txt")
    echo "  Chunks already exist: ${N_CHUNKS} chunks in ${CHUNK_DIR}/"
else
    pixi run python utils/split_input.py \
        --input "$LIBRARY_FILE" \
        --output-dir "$CHUNK_DIR" \
        --chunk-size "$CHUNK_SIZE"
    N_CHUNKS=$(wc -l < "${CHUNK_DIR}/manifest.txt")
fi
echo "  Total chunks: $N_CHUNKS"
LAST_CHUNK_IDX=$((N_CHUNKS - 1))

# ===========================================================================
# Stage 2: Extract pocket (local, fast)
# ===========================================================================
POCKET_DIR="data/targets/${TARGET_NAME}"
POCKET_LMDB="${POCKET_DIR}/pocket.lmdb"

if [ -f "$POCKET_LMDB" ]; then
    echo ""
    echo "[Stage 2/5] Pocket already exists: $POCKET_LMDB (skipping)"
else
    echo ""
    echo "[Stage 2/5] Extracting binding pocket..."

    POCKET_ARGS="--pdb $PDB_FILE --output $POCKET_LMDB --name $TARGET_NAME --cutoff $CUTOFF"

    if [ -n "$LIGAND" ]; then
        POCKET_ARGS="$POCKET_ARGS --ligand $LIGAND"
    elif [ -n "$RESIDUE" ]; then
        echo "  Computing centroid of residue $RESIDUE..."
        CTR=$(grep "HETATM.*$RESIDUE" "$PDB_FILE" \
            | awk '{x+=$7; y+=$8; z+=$9; n++} END {printf "%.3f %.3f %.3f", x/n, y/n, z/n}')
        if [ -z "$CTR" ]; then echo "Error: Residue $RESIDUE not found"; exit 1; fi
        POCKET_ARGS="$POCKET_ARGS --center $CTR"
    elif [ -n "$BINDING_RESIDUES" ]; then
        POCKET_ARGS="$POCKET_ARGS --binding-residues $BINDING_RESIDUES"
        if [ -n "$CHAIN" ]; then POCKET_ARGS="$POCKET_ARGS --chain $CHAIN"; fi
    elif [ -n "$CENTER" ]; then
        POCKET_ARGS="$POCKET_ARGS --center $CENTER"
    fi

    pixi run python utils/pdb_to_pocket_lmdb.py $POCKET_ARGS
fi

# ===========================================================================
# Stage 3: Convert chunks to LMDB (SLURM array, CPU)
# ===========================================================================
echo ""
echo "[Stage 3/5] Submitting LMDB conversion jobs..."

mkdir -p "$LMDB_DIR"

CONVERT_SCRIPT="${JOB_DIR}/convert_chunk.sh"
cat > "$CONVERT_SCRIPT" << 'CONVERT_EOF'
#!/bin/bash
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=7-00:00:00

set -euo pipefail
export PATH="/camp/home/yipy/.pixi/bin:$PATH"

# $1=DRUGCLIP_ROOT $2=CHUNK_DIR $3=LMDB_DIR
cd "$1"
CHUNK_DIR="$2"
LMDB_DIR="$3"

CHUNK_FILE=$(sed -n "$((SLURM_ARRAY_TASK_ID + 1))p" "${CHUNK_DIR}/manifest.txt")
CHUNK_BASE=$(basename "$CHUNK_FILE")
CHUNK_NAME="${CHUNK_BASE%.*}"
OUTPUT="${LMDB_DIR}/${CHUNK_NAME}.lmdb"

if [ -f "$OUTPUT" ]; then
    # Verify the LMDB is non-empty before skipping
    ENTRIES=$(pixi run python -c "
import lmdb
env = lmdb.open('$OUTPUT', readonly=True, lock=False, subdir=False)
with env.begin() as txn:
    print(txn.stat()['entries'])
" 2>/dev/null || echo 0)
    if [ "$ENTRIES" -gt 0 ] 2>/dev/null; then
        echo "LMDB already exists with $ENTRIES entries: $OUTPUT (skipping)"
        exit 0
    else
        echo "LMDB exists but is empty, re-converting: $OUTPUT"
        rm -f "$OUTPUT"
    fi
fi

pixi run python utils/sdf_to_mol_lmdb.py \
    --input "$CHUNK_FILE" \
    --output "$OUTPUT" \
    --force
CONVERT_EOF

CONVERT_JOB=$(sbatch \
    --parsable \
    --job-name="${JOB_NAME}_convert" \
    --partition="$PARTITION" \
    --array="0-${LAST_CHUNK_IDX}%${MAX_PARALLEL}" \
    --output="${JOBS_DIR}/logs/convert_%A_%a.log" \
    "$CONVERT_SCRIPT" "$DRUGCLIP_ROOT" "$CHUNK_DIR" "$LMDB_DIR")

echo "  Submitted conversion array: job $CONVERT_JOB (${N_CHUNKS} tasks)"

# ===========================================================================
# Stage 4: Encode molecule embeddings (SLURM array, GPU)
# ===========================================================================
echo ""
echo "[Stage 4/5] Submitting encoding jobs (depends on Stage 3)..."

mkdir -p "$EMB_DIR"

ENCODE_SCRIPT="${JOB_DIR}/encode_chunk.sh"
cat > "$ENCODE_SCRIPT" << 'ENCODE_EOF'
#!/bin/bash
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=7-00:00:00

set -euo pipefail
export PATH="/camp/home/yipy/.pixi/bin:$PATH"

# $1=DRUGCLIP_ROOT $2=LMDB_DIR $3=EMB_DIR $4=CHUNK_SIZE
cd "$1"
LMDB_DIR="$2"
EMB_DIR="$3"
CHUNK_SIZE="$4"

# Find the LMDB for this array task
LMDB_FILES=($(ls -1 "${LMDB_DIR}"/chunk_*.lmdb 2>/dev/null | sort))
LMDB_FILE="${LMDB_FILES[$SLURM_ARRAY_TASK_ID]}"
CHUNK_BASE=$(basename "$LMDB_FILE" .lmdb)
CHUNK_EMB_DIR="${EMB_DIR}/${CHUNK_BASE}"

if [ -f "${CHUNK_EMB_DIR}/done" ]; then
    echo "Embeddings already exist: ${CHUNK_EMB_DIR}/ (skipping)"
    exit 0
fi

mkdir -p "$CHUNK_EMB_DIR"

pixi run python ./unimol/encode_mols.py \
    --user-dir ./unimol ./dict \
    --valid-subset test \
    --num-workers 4 \
    --ddp-backend=c10d \
    --batch-size 256 \
    --task drugclip \
    --loss in_batch_softmax \
    --arch drugclip \
    --max-pocket-atoms 256 \
    --seed 1 \
    --log-interval 100 \
    --log-format simple \
    --mol-path "$LMDB_FILE" \
    --save-dir "$CHUNK_EMB_DIR" \
    --write-h5

touch "${CHUNK_EMB_DIR}/done"
echo "Encoding complete: ${CHUNK_EMB_DIR}/"
ENCODE_EOF

ENCODE_JOB=$(sbatch \
    --parsable \
    --job-name="${JOB_NAME}_encode" \
    --partition="$PARTITION" \
    --array="0-${LAST_CHUNK_IDX}%${MAX_PARALLEL}" \
    --dependency="aftercorr:${CONVERT_JOB}" \
    --output="${JOBS_DIR}/logs/encode_%A_%a.log" \
    "$ENCODE_SCRIPT" "$DRUGCLIP_ROOT" "$LMDB_DIR" "$EMB_DIR" "$CHUNK_SIZE")

echo "  Submitted encoding array: job $ENCODE_JOB (${N_CHUNKS} tasks, depends on $CONVERT_JOB)"

# ===========================================================================
# Stage 5: Stream-score against pocket (single GPU, after all encoding done)
# ===========================================================================
echo ""
echo "[Stage 5/5] Submitting screening job (depends on Stage 4)..."

SAVE_PATH="${JOB_DIR}/results.txt"

SCREEN_SCRIPT="${JOB_DIR}/screen.sh"
cat > "$SCREEN_SCRIPT" << SCREEN_EOF
#!/bin/bash
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=128G
#SBATCH --time=7-00:00:00

set -euo pipefail
export PATH="/camp/home/yipy/.pixi/bin:\$PATH"

# Run from the project root so relative paths resolve correctly
cd ${DRUGCLIP_ROOT}

echo "Starting streaming screening..."
echo "  Embeddings: ${EMB_DIR}/"
echo "  Pocket: ${POCKET_LMDB}"
echo "  Output: ${SAVE_PATH}"

pixi run python utils/screen_streaming.py \\
    --emb-dir "${EMB_DIR}" \\
    --pocket-lmdb "${POCKET_LMDB}" \\
    --output "${SAVE_PATH}" \\
    --fold-version 6_folds \\
    --top-fraction ${TOP_FRACTION}

echo ""
echo "============================================"
echo "Screening complete!"
echo "Results: ${SAVE_PATH}"
echo "Top 5 hits:"
head -5 "${SAVE_PATH}"
echo "============================================"
SCREEN_EOF

SCREEN_JOB=$(sbatch \
    --parsable \
    --job-name="${JOB_NAME}_screen" \
    --partition="$PARTITION" \
    --dependency="afterok:${ENCODE_JOB}" \
    --output="${JOBS_DIR}/logs/screen_${JOB_NAME}_%j.log" \
    "$SCREEN_SCRIPT")

echo "  Submitted screening job: $SCREEN_JOB (depends on $ENCODE_JOB)"

# ===========================================================================
# Summary
# ===========================================================================
echo ""
echo "============================================"
echo "All jobs submitted!"
echo "============================================"
echo "  Stage 3 (convert): $CONVERT_JOB  (${N_CHUNKS} array tasks)"
echo "  Stage 4 (encode):  $ENCODE_JOB  (${N_CHUNKS} array tasks, after convert)"
echo "  Stage 5 (screen):  $SCREEN_JOB  (after encode)"
echo ""
echo "Monitor with:"
echo "  squeue -u \$USER"
echo "  sacct -j ${CONVERT_JOB},${ENCODE_JOB},${SCREEN_JOB}"
echo ""
echo "Results will be at: ${SAVE_PATH}"
echo "Logs in: ${JOBS_DIR}/logs/"
echo "============================================"
