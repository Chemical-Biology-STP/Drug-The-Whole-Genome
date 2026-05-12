#!/bin/bash
#SBATCH --job-name=drugclip_dock
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=7-00:00:00
#SBATCH --output=/dev/null

# ============================================================================
# AutoDock-GPU Docking Pipeline
#
# Usage:
#   sbatch submit_docking.sh <job_dir> <receptor.pdb> <ligands.pdbqt|ligands.smi>
#                            <center_x> <center_y> <center_z>
#                            [--nrun N] [--box-size Å]
#
# Arguments:
#   $1  Job directory (absolute path, already created)
#   $2  Receptor PDB file
#   $3  Ligand PDBQT file (multi-molecule) or SMILES file (.smi)
#   $4  Binding site centre X (Å)
#   $5  Binding site centre Y (Å)
#   $6  Binding site centre Z (Å)
#
# Optional:
#   --nrun N        LGA runs per ligand (default: 20)
#   --box-size Å    Grid box edge length (default: 22.5)
#
# Output (in <job_dir>/):
#   receptor.pdbqt          — prepared receptor
#   docking_results/        — per-compound best-pose PDBQT files
#   summary.csv             — drugclip_rank, smiles, docking_score_kcal_mol, result_stem
#   slurm_<id>.log          — SLURM log
# ============================================================================

set -euo pipefail

# Always run from the project root
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

while [ $# -gt 0 ]; do
    case "$1" in
        --nrun)     NRUN="$2";     shift 2 ;;
        --box-size) BOX_SIZE="$2"; shift 2 ;;
        *)          echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Redirect SLURM log to job directory
exec > "${JOB_DIR}/slurm_${SLURM_JOB_ID}.log" 2>&1

echo "============================================"
echo "AutoDock-GPU Docking Pipeline"
echo "============================================"
echo "Job dir:    $JOB_DIR"
echo "Receptor:   $RECEPTOR_PDB"
echo "Ligands:    $LIGANDS_FILE"
echo "Centre:     ($CENTER_X, $CENTER_Y, $CENTER_Z)"
echo "Box size:   ${BOX_SIZE} Å"
echo "LGA runs:   $NRUN"
echo "============================================"

# Load required modules
if [ -f /etc/profile.d/lmod.sh ]; then
    source /etc/profile.d/lmod.sh
elif [ -f /usr/share/lmod/lmod/init/bash ]; then
    source /usr/share/lmod/lmod/init/bash
fi
export LMOD_IGNORE_CACHE=1

# Add system EasyBuild modules (GCC, CUDA etc.)
if [ -d /flask/apps/eb/modules/all ]; then
    module use /flask/apps/eb/modules/all
else
    module use /flask/apps/eb/modules 2>/dev/null || true
fi

# Add ChemBio STP custom modules
module use /nemo/stp/chemicalbiology/home/shared/easybuild/modules/all

module load GCC/13.2.0
module load CUDA/12.1.1
module load AutoDock-GPU/1.5.3-CUDA

RESULTS_DIR="${JOB_DIR}/docking_results"
mkdir -p "$RESULTS_DIR"

# ---------------------------------------------------------------------------
# Step 1: Convert SMILES to PDBQT if needed
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
            r = subprocess.run(['pixi', 'run', 'python', '/nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_ligand4.py',
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
    -r "$RECEPTOR_PDB" \
    -o "$RECEPTOR_PDBQT" \
    -A hydrogens \
    -U nphs_lps_waters_nonstdres

echo "  Receptor PDBQT: $RECEPTOR_PDBQT"

# ---------------------------------------------------------------------------
# Step 3: Split multi-ligand PDBQT into individual files
# ---------------------------------------------------------------------------
echo ""
echo "[Step 3/5] Splitting ligands into individual PDBQT files..."

LIGANDS_DIR="${JOB_DIR}/ligands"
mkdir -p "$LIGANDS_DIR"

# Read the ligands.smi to get rank→smiles mapping (most reliable source of rank info)
SMI_FILE="${JOB_DIR}/ligands.smi"

pixi run python - "$LIGANDS_PDBQT" "$SMI_FILE" "$LIGANDS_DIR" << 'PYEOF'
import sys, os, re

pdbqt_file = sys.argv[1]
smi_file   = sys.argv[2]
out_dir    = sys.argv[3]

# Build smiles→rank mapping from the .smi file (if it exists)
smiles_to_rank = {}
if os.path.exists(smi_file):
    with open(smi_file) as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) >= 2:
                smi = parts[0]
                name = parts[1]  # e.g. "rank_1"
                m = re.match(r'rank_(\d+)', name)
                if m:
                    smiles_to_rank[smi] = int(m.group(1))

# Split the multi-ligand PDBQT into individual files.
# Each ligand block starts with REMARK SMILES= and ends with TORSDOF.
blocks = []
current_lines = []
current_smiles = None

with open(pdbqt_file) as f:
    for line in f:
        if line.startswith('REMARK SMILES='):
            # Start of a new ligand block
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

# Write each block to its own file
for i, (smiles, lines) in enumerate(blocks):
    rank = smiles_to_rank.get(smiles, i + 1)
    out_path = os.path.join(out_dir, f'rank_{rank}.pdbqt')
    with open(out_path, 'w') as f:
        f.writelines(lines)
    print(f'  rank_{rank}: {smiles[:60]}')

print(f'Split {len(blocks)} ligands into {out_dir}')
PYEOF

# ---------------------------------------------------------------------------
# Step 4: Generate AutoDock-GPU grid maps (once, shared by all ligands)
# ---------------------------------------------------------------------------
echo ""
echo "[Step 4/5] Generating grid maps..."

GRID_DIR="${JOB_DIR}/grid"
mkdir -p "$GRID_DIR"

NPTS=$(python3 -c "import math; n=int(math.ceil($BOX_SIZE/0.375)); print(n if n%2==0 else n+1)")

# Use the first ligand file for GPF generation
FIRST_LIGAND=$(ls "${LIGANDS_DIR}"/rank_*.pdbqt 2>/dev/null | sort -V | head -1)
if [ -z "$FIRST_LIGAND" ]; then
    echo "ERROR: No ligand PDBQT files found in $LIGANDS_DIR"
    exit 1
fi

GPF="${GRID_DIR}/receptor.gpf"

# Run prepare_gpf4 from the job directory so MolKit can find files by relative path
cd "$JOB_DIR"
pixi run python /nemo/stp/chemicalbiology/home/shared/software/AutoDockTools/Utilities24/prepare_gpf4.py \
    -r "$RECEPTOR_PDBQT" \
    -l "$FIRST_LIGAND" \
    -o "$GPF" \
    -p npts="${NPTS},${NPTS},${NPTS}" \
    -p gridcenter="${CENTER_X},${CENTER_Y},${CENTER_Z}"
cd "$DRUGCLIP_ROOT"

# Run autogrid4 from the job directory so it finds receptor.pdbqt
cd "$JOB_DIR"
autogrid4 -p "$GPF" -l "${GRID_DIR}/receptor.glg"
cd "$DRUGCLIP_ROOT"

echo "  Grid maps generated in $GRID_DIR"

# ---------------------------------------------------------------------------
# Step 5: Run AutoDock-GPU in batch mode (all ligands in one GPU call)
# ---------------------------------------------------------------------------
echo ""
echo "[Step 5/5] Running AutoDock-GPU (batch mode) and parsing results..."

# Build the batch filelist:
#   Line 1:  receptor maps fld (used for all ligands)
#   Lines 2+: <ligand.pdbqt> <result_stem>
BATCH_FILE="${JOB_DIR}/batch.txt"
echo "${JOB_DIR}/receptor.maps.fld" > "$BATCH_FILE"
for LIGAND_PDBQT in $(ls "${LIGANDS_DIR}"/rank_*.pdbqt 2>/dev/null | sort -V); do
    STEM=$(basename "$LIGAND_PDBQT" .pdbqt)
    echo "${LIGAND_PDBQT} ${RESULTS_DIR}/${STEM}" >> "$BATCH_FILE"
done

N_LIGANDS=$(( $(wc -l < "$BATCH_FILE") - 1 ))
echo "  Batch file: $BATCH_FILE ($N_LIGANDS ligands)"

autodock_gpu_128wi \
    --filelist "$BATCH_FILE" \
    --nrun "$NRUN" \
    --xmloutput 0

echo "  AutoDock-GPU batch complete."

# ---------------------------------------------------------------------------
# Parse all DLG files → per-ligand best-pose PDBQT + summary.csv
# ---------------------------------------------------------------------------
SUMMARY_FILE="${JOB_DIR}/summary.csv"

pixi run python - "$LIGANDS_DIR" "$RESULTS_DIR" "$SUMMARY_FILE" << 'PYEOF'
import sys, os, re, csv, glob

ligands_dir  = sys.argv[1]
results_dir  = sys.argv[2]
summary_path = sys.argv[3]

rows = []

for dlg_file in sorted(glob.glob(os.path.join(results_dir, 'rank_*.dlg'))):
    stem = os.path.basename(dlg_file)[:-4]          # e.g. "rank_1"
    rank = int(stem.split('_')[1])
    ligand_pdbqt = os.path.join(ligands_dir, f'{stem}.pdbqt')

    # Get SMILES from the individual ligand PDBQT
    smiles = ''
    if os.path.exists(ligand_pdbqt):
        with open(ligand_pdbqt) as f:
            for line in f:
                if line.startswith('REMARK SMILES='):
                    smiles = line.strip().split('=', 1)[1]
                    break

    # Parse DLG: find the MODEL with the lowest binding energy
    best_energy = None
    best_pose_lines = []
    current_energy = None
    current_lines = []
    in_model = False

    with open(dlg_file) as f:
        for line in f:
            if 'DOCKED: MODEL' in line:
                in_model = True
                current_energy = None
                current_lines = []
            elif in_model and 'Estimated Free Energy of Binding' in line:
                m = re.search(r'=\s*([-+]?\d+\.?\d*)', line)
                if m:
                    current_energy = float(m.group(1))
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

    # Write best-pose PDBQT
    best_pdbqt = os.path.join(results_dir, f'{stem}-best.pdbqt')
    with open(best_pdbqt, 'w') as f:
        f.write(f'REMARK SMILES={smiles}\n')
        f.writelines(best_pose_lines)

    rows.append({
        'drugclip_rank': rank,
        'smiles': smiles,
        'docking_score_kcal_mol': best_energy,
        'result_stem': stem,
    })
    print(f'  {stem}: {best_energy:.2f} kcal/mol')

rows.sort(key=lambda r: r['docking_score_kcal_mol'])

with open(summary_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['drugclip_rank', 'smiles', 'docking_score_kcal_mol', 'result_stem'])
    writer.writeheader()
    writer.writerows(rows)

print(f'\nWrote {len(rows)} results to {summary_path}')
PYEOF

echo ""
echo "============================================"
echo "Docking complete!"
echo "Results: ${JOB_DIR}/summary.csv"
echo "Poses:   ${RESULTS_DIR}/"
echo "============================================"