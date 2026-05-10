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
#   slurm_<id>.log          — SLURM log (symlinked from jobs/logs/)
# ============================================================================

set -euo pipefail

# Always run from the project root
cd "$(dirname "$0")"
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
module use /nemo/stp/chemicalbiology/home/shared/easybuild/modules/all
module load AutoDockTools/0.1.0
module load AutoDock-GPU/1.5.3-CUDA

RESULTS_DIR="${JOB_DIR}/docking_results"
mkdir -p "$RESULTS_DIR"

# ---------------------------------------------------------------------------
# Step 1: Convert SMILES to PDBQT if needed
# ---------------------------------------------------------------------------
if [[ "$LIGANDS_FILE" == *.smi ]]; then
    echo ""
    echo "[Step 1/4] Converting SMILES to PDBQT..."
    LIGANDS_PDBQT="${JOB_DIR}/ligands_input.pdbqt"
    pixi run python - << 'PYEOF'
import sys, os, subprocess, tempfile
from rdkit import Chem
from rdkit.Chem import AllChem

smi_file = os.environ.get('LIGANDS_SMI')
out_file  = os.environ.get('LIGANDS_PDBQT')
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
            r = subprocess.run([sys.executable, '-m', 'AutoDockTools.Utilities24.prepare_ligand4',
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
    export LIGANDS_SMI="$LIGANDS_FILE"
    export LIGANDS_PDBQT
else
    echo ""
    echo "[Step 1/4] Ligand PDBQT already provided — skipping conversion"
    LIGANDS_PDBQT="$LIGANDS_FILE"
fi

# ---------------------------------------------------------------------------
# Step 2: Prepare receptor PDBQT
# ---------------------------------------------------------------------------
RECEPTOR_PDBQT="${JOB_DIR}/receptor.pdbqt"
echo ""
echo "[Step 2/4] Preparing receptor PDBQT..."

python -m AutoDockTools.Utilities24.prepare_receptor4 \
    -r "$RECEPTOR_PDB" \
    -o "$RECEPTOR_PDBQT" \
    -A hydrogens \
    -U nphs_lps_waters_nonstdres

echo "  Receptor PDBQT: $RECEPTOR_PDBQT"

# ---------------------------------------------------------------------------
# Step 3: Generate AutoDock-GPU grid maps
# ---------------------------------------------------------------------------
echo ""
echo "[Step 3/4] Generating grid maps..."

GRID_DIR="${JOB_DIR}/grid"
mkdir -p "$GRID_DIR"

# Write GPF (grid parameter file)
GPF="${GRID_DIR}/receptor.gpf"
NPTS=$(python3 -c "import math; n=int(math.ceil($BOX_SIZE/0.375)); print(n if n%2==0 else n+1)")

python -m AutoDockTools.Utilities24.prepare_gpf4 \
    -r "$RECEPTOR_PDBQT" \
    -l "$LIGANDS_PDBQT" \
    -o "$GPF" \
    -p npts="${NPTS},${NPTS},${NPTS}" \
    -p gridcenter="${CENTER_X},${CENTER_Y},${CENTER_Z}"

# Run autogrid4
cd "$GRID_DIR"
autogrid4 -p "$GPF" -l "${GRID_DIR}/receptor.glg"
cd "$DRUGCLIP_ROOT"

echo "  Grid maps generated in $GRID_DIR"

# ---------------------------------------------------------------------------
# Step 4: Run AutoDock-GPU
# ---------------------------------------------------------------------------
echo ""
echo "[Step 4/4] Running AutoDock-GPU..."

DPF="${JOB_DIR}/docking.dpf"
python -m AutoDockTools.Utilities24.prepare_dpf42 \
    -r "$RECEPTOR_PDBQT" \
    -l "$LIGANDS_PDBQT" \
    -o "$DPF" \
    -p ga_num_evals=2500000 \
    -p ga_run="$NRUN"

autodock_gpu_64wi \
    --ffile "${GRID_DIR}/receptor.maps.fld" \
    --lfile "$LIGANDS_PDBQT" \
    --nrun "$NRUN" \
    --resnam "${RESULTS_DIR}/" \
    --xmloutput 0

# ---------------------------------------------------------------------------
# Step 5: Parse results → summary.csv
# ---------------------------------------------------------------------------
echo ""
echo "[Step 5/5] Parsing docking results..."

python3 - << PYEOF
import os, csv, re, glob

results_dir = "${RESULTS_DIR}"
summary_path = "${JOB_DIR}/summary.csv"

rows = []
for dlg in sorted(glob.glob(os.path.join(results_dir, "*.dlg"))):
    stem = os.path.splitext(os.path.basename(dlg))[0]
    # Parse rank and SMILES from REMARK in the corresponding PDBQT
    pdbqt_src = "${LIGANDS_PDBQT}"
    rank = None
    smiles = None
    with open(pdbqt_src) as f:
        for line in f:
            if line.startswith("REMARK DrugCLIP"):
                m = re.search(r"rank=(\d+)", line)
                if m: rank = int(m.group(1))
            elif line.startswith("REMARK SMILES="):
                smiles = line.strip().split("=", 1)[1]
            elif line.startswith("ROOT") and rank is not None:
                break

    # Parse best binding energy from DLG
    best_energy = None
    with open(dlg) as f:
        for line in f:
            if "DOCKED: USER    Estimated Free Energy of Binding" in line:
                m = re.search(r"=\s*([-\d.]+)", line)
                if m:
                    best_energy = float(m.group(1))
                    break

    if best_energy is not None:
        # Write best pose PDBQT
        best_pdbqt = os.path.join(results_dir, f"{stem}-best.pdbqt")
        in_best = False
        lines = []
        with open(dlg) as f:
            for line in f:
                if "DOCKED: MODEL        1" in line:
                    in_best = True
                if in_best:
                    if line.startswith("DOCKED: "):
                        pdb_line = line[8:]
                        if pdb_line.strip() == "ENDMDL":
                            break
                        lines.append(pdb_line)
        if lines:
            header = ""
            if smiles:
                header = f"REMARK SMILES={smiles}\n"
            if rank:
                header = f"REMARK DrugCLIP rank={rank} score=0\n" + header
            with open(best_pdbqt, "w") as f:
                f.write(header + "".join(lines))

        rows.append({
            "drugclip_rank": rank or stem,
            "smiles": smiles or "",
            "docking_score_kcal_mol": best_energy,
            "result_stem": stem,
        })

rows.sort(key=lambda r: r["docking_score_kcal_mol"])

with open(summary_path, "w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=["drugclip_rank", "smiles", "docking_score_kcal_mol", "result_stem"])
    writer.writeheader()
    writer.writerows(rows)

print(f"Wrote {len(rows)} docking results to {summary_path}")
PYEOF

echo ""
echo "============================================"
echo "Docking complete!"
echo "Results: ${JOB_DIR}/summary.csv"
echo "Poses:   ${RESULTS_DIR}/"
echo "============================================"
