#!/bin/bash
# ============================================================================
# AutoDock-GPU Docking Pipeline — Multi-GPU Orchestrator
#
# Runs on the LOGIN NODE (not via sbatch). Does minimal prep, then submits
# a 4-stage SLURM pipeline:
#
#   Stage 1 (login node):      receptor prep + grid maps + split SMI chunks
#   Stage 2 (CPU array):       SMILES→PDBQT conversion (one task per chunk)
#   Stage 3 (GPU array):       AutoDock-GPU docking (depends on Stage 2)
#   Stage 4 (CPU, single):     merge results (depends on Stage 3)
#
# Usage:
#   bash submit_docking.sh <job_dir> <receptor.pdb> <ligands.pdbqt|ligands.smi>
#                          <center_x> <center_y> <center_z>
#                          [--nrun N] [--box-size Å] [--chunk-size N]
#                          [--partition NAME] [--max-parallel N]
#
# Outputs to stdout (last line): MERGE_JOB_ID=<id>
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
CHUNK_SIZE=500
PARTITION="ga100"
CPU_PARTITION="ncpu"
MAX_PARALLEL=50
CPU_WORKERS=16   # workers per conversion task (cpus-per-task)

while [ $# -gt 0 ]; do
    case "$1" in
        --nrun)         NRUN="$2";         shift 2 ;;
        --box-size)     BOX_SIZE="$2";     shift 2 ;;
        --chunk-size)   CHUNK_SIZE="$2";   shift 2 ;;
        --partition)    PARTITION="$2";    shift 2 ;;
        --cpu-partition) CPU_PARTITION="$2"; shift 2 ;;
        --max-parallel) MAX_PARALLEL="$2"; shift 2 ;;
        --cpu-workers)  CPU_WORKERS="$2";  shift 2 ;;
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
SMI_CHUNKS_DIR="${JOB_DIR}/smi_chunks"
mkdir -p "$RESULTS_DIR" "$LIGANDS_DIR" "$CHUNKS_DIR" "$SMI_CHUNKS_DIR" "${JOB_DIR}/logs"

# Load modules for login-node prep (receptor + grid only)
if [ -f /etc/profile.d/lmod.sh ]; then
    source /etc/profile.d/lmod.sh
elif [ -f /usr/share/lmod/lmod/init/bash ]; then
    source /usr/share/lmod/lmod/init/bash
fi
export LMOD_IGNORE_CACHE=1
[ -d /flask/apps/eb/modules/all ] && module use /flask/apps/eb/modules/all
module use /nemo/stp/chemicalbiology/home/shared/easybuild/modules/all
module load GCC/13.2.0
module load CUDA/12.1.1
module load AutoDock-GPU/1.5.3-CUDA

# ---------------------------------------------------------------------------
# Stage 1a (login node): Prepare receptor PDBQT
# ---------------------------------------------------------------------------
RECEPTOR_PDBQT="${JOB_DIR}/receptor.pdbqt"
echo ""
echo "[Stage 1/4] Preparing receptor PDBQT..."
pixi run python /nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_receptor4.py \
    -r "$RECEPTOR_PDB" -o "$RECEPTOR_PDBQT" -A hydrogens -U nphs_lps_waters_nonstdres
echo "  Receptor PDBQT: $RECEPTOR_PDBQT"

# ---------------------------------------------------------------------------
# Stage 1b (login node): Split input into SMI chunks for parallel conversion
# If input is already PDBQT, split it directly into ligand files instead.
# ---------------------------------------------------------------------------
if [[ "$LIGANDS_FILE" == *.smi ]]; then
    echo ""
    echo "[Stage 1/4] Splitting SMILES file into chunks for parallel conversion..."
    pixi run python - "$LIGANDS_FILE" "$SMI_CHUNKS_DIR" "$CHUNK_SIZE" << 'PYEOF'
import sys, os, math

smi_file   = sys.argv[1]
out_dir    = sys.argv[2]
chunk_size = int(sys.argv[3])

lines = []
with open(smi_file) as f:
    for line in f:
        line = line.strip()
        if line:
            lines.append(line)

n_chunks = math.ceil(len(lines) / chunk_size)
manifest = os.path.join(out_dir, 'manifest.txt')
with open(manifest, 'w') as mf:
    for ci in range(n_chunks):
        chunk_path = os.path.join(out_dir, f'chunk_{ci:04d}.smi')
        with open(chunk_path, 'w') as f:
            for line in lines[ci * chunk_size:(ci + 1) * chunk_size]:
                f.write(line + '\n')
        mf.write(f'chunk_{ci:04d}.smi\n')

print(f'Split {len(lines)} SMILES into {n_chunks} chunks of up to {chunk_size}')
PYEOF

    N_CHUNKS=$(wc -l < "${SMI_CHUNKS_DIR}/manifest.txt")
    LAST_CHUNK_IDX=$(( N_CHUNKS - 1 ))
    echo "  $N_CHUNKS conversion chunks created"
    NEED_CONVERSION=1
else
    echo ""
    echo "[Stage 1/4] Ligand PDBQT provided — splitting into individual files..."
    LIGANDS_PDBQT="$LIGANDS_FILE"
    pixi run python - "$LIGANDS_PDBQT" "${JOB_DIR}/ligands.smi" "$LIGANDS_DIR" "$CHUNKS_DIR" "$CHUNK_SIZE" << 'PYEOF'
import sys, os, re, math

pdbqt_file = sys.argv[1]
smi_file   = sys.argv[2]
lig_dir    = sys.argv[3]
chunk_dir  = sys.argv[4]
chunk_size = int(sys.argv[5])

smiles_to_rank = {}
if os.path.exists(smi_file):
    with open(smi_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                m = re.match(r'rank_(\d+)', parts[1])
                if m:
                    smiles_to_rank[parts[0]] = int(m.group(1))

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
n_chunks = math.ceil(len(lig_paths) / chunk_size)
for ci in range(n_chunks):
    chunk_ligs = lig_paths[ci * chunk_size:(ci + 1) * chunk_size]
    with open(os.path.join(chunk_dir, f'chunk_{ci:04d}.txt'), 'w') as f:
        for rank, path in chunk_ligs:
            f.write(f'{path} rank_{rank}\n')

with open(os.path.join(chunk_dir, 'manifest.txt'), 'w') as f:
    for ci in range(n_chunks):
        f.write(f'chunk_{ci:04d}.txt\n')

print(f'Split {len(lig_paths)} ligands into {n_chunks} GPU chunks')
PYEOF

    N_CHUNKS=$(wc -l < "${CHUNKS_DIR}/manifest.txt")
    LAST_CHUNK_IDX=$(( N_CHUNKS - 1 ))
    echo "  $N_CHUNKS GPU chunks created"
    NEED_CONVERSION=0
fi

# ---------------------------------------------------------------------------
# Stage 1c (login node): Generate grid maps
# ---------------------------------------------------------------------------
echo ""
echo "[Stage 1/4] Generating grid maps..."
GRID_DIR="${JOB_DIR}/grid"
mkdir -p "$GRID_DIR"
NPTS=$(python3 -c "import math; n=int(math.ceil($BOX_SIZE/0.375)); print(n if n%2==0 else n+1)")
GPF="${GRID_DIR}/receptor.gpf"

# For grid map generation we need one representative ligand PDBQT.
# If converting from SMILES, use the first line of the first SMI chunk
# to generate a temporary single-ligand PDBQT on the login node.
if [ "$NEED_CONVERSION" -eq 1 ]; then
    FIRST_SMI_LINE=$(head -1 "${SMI_CHUNKS_DIR}/chunk_0000.smi")
    FIRST_SMILES=$(echo "$FIRST_SMI_LINE" | awk '{print $1}')
    FIRST_NAME=$(echo "$FIRST_SMI_LINE" | awk '{print $2}')
    GRID_LIGAND="${JOB_DIR}/grid_ligand.pdbqt"
    pixi run python - "$FIRST_SMILES" "$FIRST_NAME" "$GRID_LIGAND" << 'PYEOF'
import sys, os, subprocess, tempfile
from rdkit import Chem
from rdkit.Chem import AllChem

smiles, name, out = sys.argv[1], sys.argv[2], sys.argv[3]
PREPARE_LIGAND = '/nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_ligand4.py'
mol = Chem.MolFromSmiles(smiles)
mol = Chem.AddHs(mol)
p = AllChem.ETKDGv3(); p.randomSeed = 42
AllChem.EmbedMolecule(mol, p)
AllChem.MMFFOptimizeMolecule(mol)
mol = Chem.RemoveHs(mol)
mol.SetProp('_Name', name)
with tempfile.TemporaryDirectory() as td:
    pdb = os.path.join(td, 'lig.pdb')
    pdbqt = os.path.join(td, 'lig.pdbqt')
    Chem.MolToPDBFile(mol, pdb)
    subprocess.run(['pixi', 'run', 'python', PREPARE_LIGAND,
                    '-l', pdb, '-o', pdbqt, '-A', 'hydrogens'],
                   check=True, capture_output=True)
    import shutil; shutil.copy(pdbqt, out)
print(f'Grid ligand: {out}')
PYEOF
else
    GRID_LIGAND=$(ls "${LIGANDS_DIR}"/rank_*.pdbqt 2>/dev/null | sort -V | head -1)
fi

cd "$JOB_DIR"
pixi run python /nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_gpf4.py \
    -r "$RECEPTOR_PDBQT" -l "$GRID_LIGAND" -o "$GPF" \
    -p npts="${NPTS},${NPTS},${NPTS}" \
    -p gridcenter="${CENTER_X},${CENTER_Y},${CENTER_Z}"
autogrid4 -p "$GPF" -l "${GRID_DIR}/receptor.glg"
cd "$DRUGCLIP_ROOT"
echo "  Grid maps generated in $GRID_DIR"

# ---------------------------------------------------------------------------
# Stage 2: SMILES→PDBQT conversion array (CPU partition, one task per chunk)
# Only submitted when input is .smi; skipped for pre-converted PDBQT.
# ---------------------------------------------------------------------------
CONVERT_SCRIPT="${JOB_DIR}/convert_chunk.sh"
# Max parallel conversion tasks = floor(1600 / CPU_WORKERS) — 80% of 2000 CPU limit
MAX_CONV_PARALLEL=$(python3 -c "print(min(${MAX_PARALLEL}, 1600 // ${CPU_WORKERS}))")
cat > "$CONVERT_SCRIPT" << CONV_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_convert
#SBATCH --partition=${CPU_PARTITION}
#SBATCH --cpus-per-task=${CPU_WORKERS}
#SBATCH --mem=$(( CPU_WORKERS * 2 ))G
#SBATCH --time=4:00:00
#SBATCH --output=${JOB_DIR}/logs/convert_%a.log

set -euo pipefail
cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
export PATH="/camp/home/yipy/.pixi/bin:\$PATH"

CHUNK_FILE=\$(sed -n "\$(( SLURM_ARRAY_TASK_ID + 1 ))p" "${SMI_CHUNKS_DIR}/manifest.txt")
CHUNK_PATH="${SMI_CHUNKS_DIR}/\${CHUNK_FILE}"
CHUNK_IDX=\$(printf '%04d' \$SLURM_ARRAY_TASK_ID)
OUT_PDBQT="${LIGANDS_DIR}/chunk_\${CHUNK_IDX}.pdbqt"

echo "Converting chunk \$CHUNK_IDX: \$(wc -l < \$CHUNK_PATH) SMILES"

pixi run python - "\$CHUNK_PATH" "\$OUT_PDBQT" "${JOB_DIR}/ligands.smi" "\$CHUNK_IDX" << 'PYEOF'
import sys, os, subprocess, tempfile, re, math
from rdkit import Chem
from rdkit.Chem import AllChem
from multiprocessing import Pool, cpu_count

chunk_smi  = sys.argv[1]
out_pdbqt  = sys.argv[2]
global_smi = sys.argv[3]   # master ligands.smi for rank lookup
chunk_idx  = int(sys.argv[4])

PREPARE_LIGAND = '/nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_ligand4.py'

# Build smiles→rank from master smi file
smiles_to_rank = {}
if os.path.exists(global_smi):
    with open(global_smi) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                m = re.match(r'rank_(\d+)', parts[1])
                if m:
                    smiles_to_rank[parts[0]] = int(m.group(1))

entries = []
with open(chunk_smi) as f:
    for line in f:
        parts = line.strip().split()
        if parts:
            entries.append((parts[0], parts[1] if len(parts) > 1 else 'lig'))

def convert_one(args):
    smiles, name = args
    rank = smiles_to_rank.get(smiles)
    if rank is None:
        m = re.match(r'rank_(\d+)', name)
        rank = int(m.group(1)) if m else None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None: return None
        mol = Chem.AddHs(mol)
        p = AllChem.ETKDGv3(); p.randomSeed = 42
        if AllChem.EmbedMolecule(mol, p) == -1:
            if AllChem.EmbedMolecule(mol, AllChem.ETKDG()) == -1:
                return None
        AllChem.MMFFOptimizeMolecule(mol)
        mol = Chem.RemoveHs(mol)
        mol.SetProp('_Name', name)
        with tempfile.TemporaryDirectory() as td:
            pdb   = os.path.join(td, 'lig.pdb')
            pdbqt = os.path.join(td, 'lig.pdbqt')
            Chem.MolToPDBFile(mol, pdb)
            r = subprocess.run(
                ['pixi', 'run', 'python', PREPARE_LIGAND,
                 '-l', pdb, '-o', pdbqt, '-A', 'hydrogens'],
                capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not os.path.exists(pdbqt):
                return None
            with open(pdbqt) as f2:
                block = f2.read().strip()
        return (rank, smiles, f'REMARK SMILES={smiles}\n{block}')
    except Exception:
        return None

n_workers = min(cpu_count(), 16)
print(f'Converting {len(entries)} SMILES using {n_workers} workers...')

with Pool(n_workers) as pool:
    results = pool.map(convert_one, entries)

# Write individual PDBQT files per ligand (for docking) and combined chunk PDBQT
lig_dir = os.path.dirname(out_pdbqt)
blocks = []
n_ok = n_fail = 0
for res in results:
    if res is None:
        n_fail += 1
        continue
    rank, smiles, block = res
    if rank is not None:
        lig_path = os.path.join(lig_dir, f'rank_{rank}.pdbqt')
        with open(lig_path, 'w') as f:
            f.write(block)
    blocks.append(block)
    n_ok += 1

with open(out_pdbqt, 'w') as f:
    f.write('\n'.join(blocks))

print(f'Converted {n_ok} ligands ({n_fail} failed) -> {out_pdbqt}')
PYEOF

echo "Conversion complete for chunk \$CHUNK_IDX"
CONV_EOF

# ---------------------------------------------------------------------------
# Stage 3: After conversion, build GPU chunk manifests + submit docking array
# This runs as a single CPU job that depends on all conversion tasks.
# ---------------------------------------------------------------------------
SPLIT_SCRIPT="${JOB_DIR}/split_and_dock.sh"
cat > "$SPLIT_SCRIPT" << SPLIT_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_split_dock
#SBATCH --partition=${CPU_PARTITION}
#SBATCH --cpus-per-task=4
#SBATCH --mem=8G
#SBATCH --time=1:00:00
#SBATCH --output=${JOB_DIR}/logs/split_dock.log

set -euo pipefail
cd /nemo/stp/chemicalbiology/home/shared/software/drugclip
export PATH="/camp/home/yipy/.pixi/bin:\$PATH"

echo "Building GPU chunk manifests from converted ligands..."

pixi run python - "${LIGANDS_DIR}" "${CHUNKS_DIR}" "${CHUNK_SIZE}" << 'PYEOF'
import sys, os, math, glob

lig_dir    = sys.argv[1]
chunk_dir  = sys.argv[2]
chunk_size = int(sys.argv[3])

lig_files = sorted(glob.glob(os.path.join(lig_dir, 'rank_*.pdbqt')),
                   key=lambda p: int(os.path.basename(p)[5:-6]))
n_chunks = math.ceil(len(lig_files) / chunk_size)

for ci in range(n_chunks):
    chunk_ligs = lig_files[ci * chunk_size:(ci + 1) * chunk_size]
    with open(os.path.join(chunk_dir, f'chunk_{ci:04d}.txt'), 'w') as f:
        for path in chunk_ligs:
            stem = os.path.basename(path)[:-6]  # rank_N
            f.write(f'{path} {stem}\n')

with open(os.path.join(chunk_dir, 'manifest.txt'), 'w') as f:
    for ci in range(n_chunks):
        f.write(f'chunk_{ci:04d}.txt\n')

print(f'Created {n_chunks} GPU chunks from {len(lig_files)} ligands')
PYEOF

N_CHUNKS=\$(wc -l < "${CHUNKS_DIR}/manifest.txt")
LAST_IDX=\$(( N_CHUNKS - 1 ))
echo "Submitting docking array: \$N_CHUNKS GPU tasks..."

DOCK_JOB=\$(sbatch --parsable \
    --array="0-\${LAST_IDX}%${MAX_PARALLEL}" \
    "${JOB_DIR}/dock_chunk.sh")
echo "Docking array: \$DOCK_JOB"

MERGE_JOB=\$(sbatch --parsable \
    --dependency="afterok:\${DOCK_JOB}" \
    "${JOB_DIR}/merge_results.sh")
echo "Merge job: \$MERGE_JOB"

echo "\${DOCK_JOB}" > "${JOB_DIR}/dock_array_job_id.txt"
echo "\${MERGE_JOB}" > "${JOB_DIR}/merge_job_id.txt"
SPLIT_EOF

# ---------------------------------------------------------------------------
# Stage 3 (GPU array): Dock one chunk per GPU
# ---------------------------------------------------------------------------
DOCK_CHUNK_SCRIPT="${JOB_DIR}/dock_chunk.sh"
cat > "$DOCK_CHUNK_SCRIPT" << CHUNK_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_dock_chunk
#SBATCH --partition=${PARTITION}
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=7-00:00:00
#SBATCH --output=${JOB_DIR}/logs/dock_%a.log

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

echo "Docking chunk \$CHUNK_IDX: \$(wc -l < \$CHUNK_PATH) ligands"

BATCH_FILE="${JOB_DIR}/chunks/batch_\${CHUNK_IDX}.txt"
echo "${JOB_DIR}/receptor.maps.fld" > "\$BATCH_FILE"
while IFS=' ' read -r lig_path stem; do
    echo "\${lig_path} ${RESULTS_DIR}/\${stem}" >> "\$BATCH_FILE"
done < "\$CHUNK_PATH"

autodock_gpu_128wi \
    --filelist "\$BATCH_FILE" \
    --nrun ${NRUN} \
    --xmloutput 0

echo "Chunk \$CHUNK_IDX docking complete."
CHUNK_EOF

# ---------------------------------------------------------------------------
# Stage 4: Merge results
# ---------------------------------------------------------------------------
MERGE_SCRIPT="${JOB_DIR}/merge_results.sh"
cat > "$MERGE_SCRIPT" << MERGE_EOF
#!/bin/bash
#SBATCH --job-name=drugclip_dock_merge
#SBATCH --partition=${CPU_PARTITION}
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

# ---------------------------------------------------------------------------
# Submit the pipeline
# ---------------------------------------------------------------------------
echo ""
echo "Submitting pipeline..."

if [ "$NEED_CONVERSION" -eq 1 ]; then
    # Submit conversion array → split+dock submitter → (dock array → merge)
    # The split_and_dock job submits the dock array and merge job itself
    CONV_JOB=$(sbatch \
        --parsable \
        --array="0-${LAST_CHUNK_IDX}%${MAX_CONV_PARALLEL}" \
        "$CONVERT_SCRIPT")
    echo "  Stage 2 (convert array): $CONV_JOB ($N_CHUNKS tasks, max ${MAX_CONV_PARALLEL} parallel = $(( MAX_CONV_PARALLEL * CPU_WORKERS )) CPUs)"

    SPLIT_JOB=$(sbatch \
        --parsable \
        --dependency="afterok:${CONV_JOB}" \
        "$SPLIT_SCRIPT")
    echo "  Stage 3 (split+submit dock): $SPLIT_JOB (depends on $CONV_JOB)"

    # The merge job ID isn't known yet (split_and_dock submits it dynamically).
    # We track the split job as the primary ID; the monitor will follow the chain.
    PRIMARY_JOB="$SPLIT_JOB"
    echo "${CONV_JOB}"  > "${JOB_DIR}/convert_job_id.txt"
    echo "${SPLIT_JOB}" > "${JOB_DIR}/split_job_id.txt"
else
    # PDBQT already provided — skip conversion, go straight to dock + merge
    DOCK_JOB=$(sbatch \
        --parsable \
        --array="0-${LAST_CHUNK_IDX}%${MAX_PARALLEL}" \
        "$DOCK_CHUNK_SCRIPT")
    echo "  Stage 3 (dock array): $DOCK_JOB ($N_CHUNKS tasks)"

    MERGE_JOB=$(sbatch \
        --parsable \
        --dependency="afterok:${DOCK_JOB}" \
        "$MERGE_SCRIPT")
    echo "  Stage 4 (merge): $MERGE_JOB (depends on $DOCK_JOB)"

    PRIMARY_JOB="$MERGE_JOB"
    echo "${DOCK_JOB}"  > "${JOB_DIR}/array_job_id.txt"
    echo "${MERGE_JOB}" > "${JOB_DIR}/merge_job_id.txt"
fi

echo ""
echo "============================================"
echo "Pipeline submitted! Primary job: $PRIMARY_JOB"
echo "============================================"

echo "MERGE_JOB_ID=${PRIMARY_JOB}"
