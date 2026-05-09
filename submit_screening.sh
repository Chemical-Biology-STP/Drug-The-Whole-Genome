#!/bin/bash
#SBATCH --job-name=drugclip_screen
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=jobs/logs/slurm_%j.log

# ============================================================================
# DrugCLIP Virtual Screening Pipeline
#
# Usage:
#   sbatch submit_screening.sh <receptor.pdb> <library.sdf|smi> [options]
#
# Required arguments:
#   $1  Receptor PDB file
#   $2  Compound library (SDF, SMI, or SMILES file)
#
# Binding site definition (one required):
#   --ligand <file>        Ligand file (PDB/SDF) to define binding site center
#   --residue <name>       HETATM residue name in the PDB (e.g., JHN)
#   --center <x> <y> <z>  Explicit binding site coordinates
#   --binding-residues <n> [n...]  Protein residue numbers (e.g., 45 67 89)
#
# Optional:
#   --cutoff <float>       Pocket extraction radius in Å (default: 10.0)
#   --chain <id>           Chain ID for --binding-residues (default: all chains)
#   --name <string>        Target name (default: derived from PDB filename)
#   --jobs-dir <path>      Top-level jobs directory (default: jobs/)
#
# Examples:
#   sbatch submit_screening.sh receptor.pdb library.sdf --ligand ligand.sdf
#   sbatch submit_screening.sh receptor.pdb library.smi --residue JHN
#   sbatch submit_screening.sh receptor.pdb library.sdf --center 25.6 7.8 19.0
#   sbatch submit_screening.sh receptor.pdb library.sdf --binding-residues 45 67 89 102
#   sbatch submit_screening.sh receptor.pdb library.sdf --binding-residues 45 67 89 --chain A
#
# Output structure:
#   jobs/
#     logs/                          SLURM log files
#       slurm_<jobid>.log
#     <target>_vs_<library>/         Per-job directory
#       results.txt                  Screening hits (SMILES, score)
#       slurm_<jobid>.log            Symlink to SLURM log
# ============================================================================

set -euo pipefail

# Always run from the project root regardless of where sbatch was called from
cd "$(dirname "$0")"

mkdir -p jobs/logs

export PATH="/camp/home/yipy/.pixi/bin:$PATH"

pixi run bash screen_pipeline.sh "$@"

# Symlink the SLURM log into the job directory for convenience.
# The pipeline writes the job dir path to a temp file for us to pick up.
if [ -n "${SLURM_JOB_ID:-}" ]; then
    MARKER="jobs/logs/.job_dir_${SLURM_JOB_ID}"
    if [ -f "$MARKER" ]; then
        JOB_DIR=$(cat "$MARKER")
        SLURM_LOG="jobs/logs/slurm_${SLURM_JOB_ID}.log"
        if [ -d "$JOB_DIR" ]; then
            ln -sf "$(realpath "$SLURM_LOG")" "${JOB_DIR}/slurm_${SLURM_JOB_ID}.log" 2>/dev/null || true
        fi
        rm -f "$MARKER"
    fi
fi
