#!/bin/bash
# ============================================================================
# AutoDock-GPU Docking Pipeline — Multi-GPU Orchestrator
#
# Runs on the LOGIN NODE (not via sbatch). Prepares inputs, then submits:
#   - A SLURM array job (one GPU per ligand chunk) for docking
#   - A merge job (depends on array) to collate results
#
# Usage:
#   bash submit_docking.sh <job_dir> <receptor.pdb> <ligands.pdbqt|ligands.smi>
#                          <center_x> <center_y> <center_z>
#                          [--nrun N] [--box-size Å] [--chunk-size N]
#                          [--partition NAME] [--max-parallel N]
#
# Outputs to stdout (last line): the merge SLURM job ID
# ============================================================================

set -euo pipefail

cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
DRUGCLIP_ROOT="$PWD"
export PATH="/camp/home/yipy/.pixi/bin:$PATH"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
JOB_DIR="$1"
RECEPTOR_PDB="$2"
LIGANDS_FILE="$3"
CENTER_X="$4"
CENTER_Y="$5"
CENTER_Z="$6"
shift 6

NRUN=20
BOX_SIZE=22.5
CHUNK_SIZE=500        # ligands per GPU chunk
PARTITION="ga100"
MAX_PARALLEL=50

while [ $# -gt 0 ]; do
    case "$1" in
        --nrun)         NRUN="$2";         shift 2 ;;
        --box-size)     BOX_SIZE="$2";     shift 2 ;;
        --chunk-size)   CHUNK_SIZE="$2";   shift 2 ;;
        --partition)    PARTITION="$2";    shift 2 ;;
        --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
        *)              echo "Unknown option: $1"; exit 1 ;;
    esac
done

LOG="${JOB_DIR}/orchestrator.log"
exec > "$LOG" 2>&1

echo "============================================"
echo "AutoDock-GPU Multi-GPU Docking Pipeline"
echo "============================================"
echo "Job dir:    $JOB_DIR"
echo "Receptor:   $RECEPTOR_PDB"
echo "Ligands:    $LIGANDS_FILE"
echo "Centre:     ($CENTER_X, $CENTER_Y, $CENTER_Z)"
echo "Box size:   ${BOX_SIZE} Å"
echo "LGA runs:   $NRUN"
echo "Chunk size: $CHUNK_SIZE ligands/GPU"
echo "Partition:  $PARTITION"
echo "============================================"

RESULTS_DIR="${JOB_DIR}/docking_results"
LIGANDS_DIR="${JOB_DIR}/ligands"
CHUNKS_DIR="${JOB_DIR}/chunks"
mkdir -p "$RESULTS_DIR" "$LIGANDS_DIR" "$CHUNKS_DIR"

# Load modules needed for prep steps
if [ -f /etc/profile.d/lmod.sh ]; then
    source /etc/profile.d/lmod.sh
elif [ -f /usr/share/lmod/lmod/init/bash ]; then
    source /usr/share/lmod/lmod/init/bash
fi
export LMOD_IGNORE_CACHE=1
if [ -d /flask/apps/eb/modules/all ]; then
    module use /flask/apps/eb/modules/all
fi
module use /nemo/stp/chemicalbiology/home/shared/easybuild/modules/all
module load GCC/13.2.0
module load CUDA/12.1.1
module load AutoDock-GPU/1.5.3-CUDA

# ---------------------------------------------------------------------------
# Step 1: Convert SMILES → PDBQT if needed
# ---------------------------------------------------------------------------
if [[ "$LIGANDS_FILE" == *.smi ]]; then
    echo ""
    echo "[Step 1/5] Converting SMILES to PDBQT..."
    LIGANDS_PDBQT="${JOB_DIR}/ligands_input.pdbqt"
    pixi run python - "$LIGANDS_FILE" "$LIGANDS_PDBQT" << 'PYEOF'
import sys, os, subprocess, tempfile
from rdkit import Chem
from rdkit.Chem import AllChem

smi_file = sys.argv[1]
out_file  = sys.argv[2]
blocks = []
n_ok = n_fail = 0

with open(smi_file) as f:
    for line in f:
        parts = line.strip().split()
        if not parts: continue
        smiles = parts[0]
        name   = parts[1] if len(parts) > 1 else 'lig'
        mol = Chem.MolFromSmiles(smiles)
        if mol is None: n_fail += 1; continue
        mol = Chem.AddHs(mol)
        p = AllChem.ETKDGv3(); p.randomSeed = 42
        if AllChem.EmbedMolecule(mol, p) == -1:
            n_fail += 1; continue
        AllChem.MMFFOptimizeMolecule(mol)
        mol = Chem.RemoveHs(mol)
        mol.SetProp('_Name', name)
        with tempfile.TemporaryDirectory() as td:
            pdb = os.path.join(td, 'lig.pdb')
            pdbqt = os.path.join(td, 'lig.pdbqt')
            Chem.MolToPDBFile(mol, pdb)
            r = subprocess.run(
                ['pixi', 'run', 'python',
                 '/nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_ligand4.py',
                 '-l', pdb, '-o', pdbqt, '-A', 'hydrogens'],
                capture_output=True, text=True, timeout=60)
            if r.returncode != 0 or not os.path.exists(pdbqt):
                n_fail += 1; continue
            with open(pdbqt) as f2:
                blocks.append(f'REMARK SMILES={smiles}\n' + f2.read().strip())
            n_ok += 1

with open(out_file, 'w') as f:
    f.write('\n'.join(blocks))
print(f'Converted {n_ok} ligands ({n_fail} failed)')
PYEOF
else
    echo ""
    echo "[Step 1/5] Ligand PDBQT already provided — skipping conversion"
    LIGANDS_PDBQT="$LIGANDS_FILE"
fi

# ---------------------------------------------------------------------------
# Step 2: Prepare receptor PDBQT
# ---------------------------------------------------------------------------
RECEPTOR_PDBQT="${JOB_DIR}/receptor.pdbqt"
echo ""
echo "[Step 2/5] Preparing receptor PDBQT..."
pixi run python /nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_receptor4.py \
    -r "$RECEPTOR_PDB" -o "$RECEPTOR_PDBQT" -A hydrogens -U nphs_lps_waters_nonstdres
echo "  Receptor PDBQT: $RECEPTOR_PDBQT"

# ---------------------------------------------------------------------------
# Step 3: Split ligands into individual PDBQT files, then into GPU chunks
# ---------------------------------------------------------------------------
echo ""
echo "[Step 3/5] Splitting ligands into individual files and GPU chunks..."

SMI_FILE="${JOB_DIR}/ligands.smi"

pixi run python - "$LIGANDS_PDBQT" "$SMI_FILE" "$LIGANDS_DIR" "$CHUNKS_DIR" "$CHUNK_SIZE" << 'PYEOF'
import sys, os, re, math

pdbqt_file = sys.argv[1]
smi_file   = sys.argv[2]
lig_dir    = sys.argv[3]
chunk_dir  = sys.argv[4]
chunk_size = int(sys.argv[5])

# Build smiles→rank mapping
smiles_to_rank = {}
if os.path.exists(smi_file):
    with open(smi_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                m = re.match(r'rank_(\d+)', parts[1])
                if m:
                    smiles_to_rank[parts[0]] = int(m.group(1))

# Split multi-ligand PDBQT into individual files
blocks = []
current_lines = []
current_smiles = None
with open(pdbqt_file) as f:
    for line in f:
        if line.startswith('REMARK SMILES='):
            if current_lines and current_smiles is not None:
                blocks.append((current_smiles, current_lines))
            current_smiles = line.strip().split('=', 1)[1]
            current_lines = [line]
        elif current_smiles is not None:
            current_lines.append(line)
            if line.startswith('TORSDOF'):
                blocks.append((current_smiles, current_lines))
                current_smiles = None
                current_lines = []

lig_paths = []
for i, (smiles, lines) in enumerate(blocks):
    rank = smiles_to_rank.get(smiles, i + 1)
    out_path = os.path.join(lig_dir, f'rank_{rank}.pdbqt')
    with open(out_path, 'w') as f:
        f.writelines(lines)
    lig_paths.append((rank, out_path))

lig_paths.sort(key=lambda x: x[0])
print(f'Split {len(lig_paths)} ligands into {lig_dir}')

# Group into chunks and write per-chunk batch files
n_chunks = math.ceil(len(lig_paths) / chunk_size)
for ci in range(n_chunks):
    chunk_ligs = lig_paths[ci * chunk_size:(ci + 1) * chunk_size]
    chunk_file = os.path.join(chunk_dir, f'chunk_{ci:04d}.txt')
    with open(chunk_file, 'w') as f:
        for rank, path in chunk_ligs:
            f.write(f'{path} rank_{rank}\n')

# Write manifest
manifest = os.path.join(chunk_dir, 'manifest.txt')
with open(manifest, 'w') as f:
    for ci in range(n_chunks):
        f.write(f'chunk_{ci:04d}.txt\n')

print(f'Created {n_chunks} chunks of up to {chunk_size} ligands each')
print(f'Manifest: {manifest}')
PYEOF

N_CHUNKS=$(wc -l < "${CHUNKS_DIR}/manifest.txt")
LAST_CHUNK_IDX=$(( N_CHUNKS - 1 ))
echo "  $N_CHUNKS GPU chunks created"

# ---------------------------------------------------------------------------
# Step 4: Generate grid maps (once, on login node)
# ---------------------------------------------------------------------------
echo ""
echo "[Step 4/5] Generating grid maps..."

GRID_DIR="${JOB_DIR}/grid"
mkdir -p "$GRID_DIR"
NPTS=$(python3 -c "import math; n=int(math.ceil($BOX_SIZE/0.375)); print(n if n%2==0 else n+1)")
FIRST_LIGAND=$(ls "${LIGANDS_DIR}"/rank_*.pdbqt 2>/dev/null | sort -V | head -1)
GPF="${GRID_DIR}/receptor.gpf"

cd "$JOB_DIR"
pixi run python /nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_gpf4.py \
    -r "$RECEPTOR_PDBQT" -l "$FIRST_LIGAND" -o "$GPF" \
    -p npts="${NPTS},${NPTS},${NPTS}" \
    -p gridcenter="${CENTER_X},${CENTER_Y},${CENTER_Z}"
autogrid4 -p "$GPF" -l "${GRID_DIR}/receptor.glg"
cd "$DRUGCLIP_ROOT"
echo "  Grid maps generated in $GRID_DIR"

# ---------------------------------------------------------------------------
# Step 5: Submit SLURM array (one GPU per chunk) + merge job
# ---------------------------------------------------------------------------
echo ""
echo "[Step 5/5] Submitting SLURM array job ($N_CHUNKS tasks)..."

# Write the per-chunk docking script
DOCK_CHUNK_SCRIPT="${JOB_DIR}/dock_chunk.sh"
cat > "$DOCK_CHUNK_SCRIPT" << CHUNK_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_dock_chunk
#SBATCH --partition=${PARTITION}
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=7-00:00:00
#SBATCH --output=${JOB_DIR}/logs/chunk_%a.log

set -euo pipefail
cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
export PATH="/camp/home/yipy/.pixi/bin:\$PATH"

if [ -f /etc/profile.d/lmod.sh ]; then source /etc/profile.d/lmod.sh
elif [ -f /usr/share/lmod/lmod/init/bash ]; then source /usr/share/lmod/lmod/init/bash; fi
export LMOD_IGNORE_CACHE=1
[ -d /flask/apps/eb/modules/all ] && module use /flask/apps/eb/modules/all
module use /nemo/stp/chemicalbiology/home/shared/easybuild/modules/all
module load GCC/13.2.0
module load CUDA/12.1.1
module load AutoDock-GPU/1.5.3-CUDA

CHUNK_FILE=\$(sed -n "\$(( SLURM_ARRAY_TASK_ID + 1 ))p" "${CHUNKS_DIR}/manifest.txt")
CHUNK_PATH="${CHUNKS_DIR}/\${CHUNK_FILE}"
CHUNK_IDX=\$(printf '%04d' \$SLURM_ARRAY_TASK_ID)

echo "Chunk \$CHUNK_IDX: \$(wc -l < \$CHUNK_PATH) ligands"

# Build batch filelist for this chunk:
#   Line 1: receptor maps fld
#   Lines 2+: <ligand.pdbqt> <result_stem>
BATCH_FILE="${JOB_DIR}/chunks/batch_\${CHUNK_IDX}.txt"
echo "${JOB_DIR}/receptor.maps.fld" > "\$BATCH_FILE"
while IFS=' ' read -r lig_path stem; do
    echo "\${lig_path} ${RESULTS_DIR}/\${stem}" >> "\$BATCH_FILE"
done < "\$CHUNK_PATH"

autodock_gpu_128wi \\
    --filelist "\$BATCH_FILE" \\
    --nrun ${NRUN} \\
    --xmloutput 0

echo "Chunk \$CHUNK_IDX complete."
CHUNK_EOF

# Write the merge script
MERGE_SCRIPT="${JOB_DIR}/merge_results.sh"
cat > "$MERGE_SCRIPT" << MERGE_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_dock_merge
#SBATCH --partition=cpu
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=2:00:00
#SBATCH --output=${JOB_DIR}/slurm_merge_%j.log

set -euo pipefail
cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
export PATH="/camp/home/yipy/.pixi/bin:\$PATH"

echo "Merging docking results..."

pixi run python - "${LIGANDS_DIR}" "${RESULTS_DIR}" "${JOB_DIR}/summary.csv" << 'PYEOF'
import sys, os, re, csv, glob

ligands_dir  = sys.argv[1]
results_dir  = sys.argv[2]
summary_path = sys.argv[3]

rows = []
for dlg_file in sorted(glob.glob(os.path.join(results_dir, 'rank_*.dlg'))):
    stem = os.path.basename(dlg_file)[:-4]
    rank = int(stem.split('_')[1])
    ligand_pdbqt = os.path.join(ligands_dir, f'{stem}.pdbqt')

    smiles = ''
    if os.path.exists(ligand_pdbqt):
        with open(ligand_pdbqt) as f:
            for line in f:
                if line.startswith('REMARK SMILES='):
                    smiles = line.strip().split('=', 1)[1]
                    break

    best_energy = None
    best_pose_lines = []
    current_energy = None
    current_lines = []
    in_model = False

    with open(dlg_file) as f:
        for line in f:
            if 'DOCKED: MODEL' in line:
                in_model = True; current_energy = None; current_lines = []
            elif in_model and 'Estimated Free Energy of Binding' in line:
                m = re.search(r'=\s*([-+]?\d+\.?\d*)', line)
                if m: current_energy = float(m.group(1))
            elif in_model and line.startswith('DOCKED: '):
                pdb_line = line[8:]
                if pdb_line.strip() == 'ENDMDL':
                    if current_energy is not None:
                        if best_energy is None or current_energy < best_energy:
                            best_energy = current_energy
                            best_pose_lines = list(current_lines)
                    in_model = False
                else:
                    current_lines.append(pdb_line)

    if best_energy is None:
        print(f'  WARNING: No valid poses in {dlg_file}')
        continue

    best_pdbqt = os.path.join(results_dir, f'{stem}-best.pdbqt')
    with open(best_pdbqt, 'w') as f:
        f.write(f'REMARK SMILES={smiles}\n')
        f.writelines(best_pose_lines)

    rows.append({'drugclip_rank': rank, 'smiles': smiles,
                 'docking_score_kcal_mol': best_energy, 'result_stem': stem})

rows.sort(key=lambda r: r['docking_score_kcal_mol'])
with open(summary_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['drugclip_rank','smiles','docking_score_kcal_mol','result_stem'])
    writer.writeheader()
    writer.writerows(rows)

print(f'Wrote {len(rows)} results to {summary_path}')
PYEOF

echo "Merge complete: ${JOB_DIR}/summary.csv"
MERGE_EOF

mkdir -p "${JOB_DIR}/logs"

# Submit array job
ARRAY_JOB=$(sbatch \
    --parsable \
    --array="0-${LAST_CHUNK_IDX}%${MAX_PARALLEL}" \
    "$DOCK_CHUNK_SCRIPT")
echo "  Submitted docking array: $ARRAY_JOB ($N_CHUNKS tasks)"

# Submit merge job (depends on all array tasks completing successfully)
MERGE_JOB=$(sbatch \
    --parsable \
    --dependency="afterok:${ARRAY_JOB}" \
    "$MERGE_SCRIPT")
echo "  Submitted merge job: $MERGE_JOB (depends on $ARRAY_JOB)"

echo ""
echo "============================================"
echo "All jobs submitted!"
echo "  Array: $ARRAY_JOB  ($N_CHUNKS GPU tasks)"
echo "  Merge: $MERGE_JOB  (after array)"
echo "============================================"

# Write job IDs to a file for the webapp to read
echo "${ARRAY_JOB}" > "${JOB_DIR}/array_job_id.txt"
echo "${MERGE_JOB}"  > "${JOB_DIR}/merge_job_id.txt"

# Print merge job ID as the last line (webapp reads this as the primary job ID)
echo "MERGE_JOB_ID=${MERGE_JOB}"
