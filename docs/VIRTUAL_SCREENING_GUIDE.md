# DrugCLIP Virtual Screening Guide

End-to-end instructions for running virtual screening with a custom receptor PDB
and compound library SDF on an HPC cluster with SLURM.

## Prerequisites

- HPC access with GPU nodes (SLURM scheduler)
- Pixi module available (`module load pixi/0.56.0`)
- Receptor structure as a PDB file
- Compound library as an SDF file (with 3D coordinates preferred)
- This repository cloned to your HPC workspace

## Overview

The pipeline has four stages:

1. **Environment setup** — install all dependencies with pixi
2. **Prepare pocket** — extract binding site from receptor PDB → LMDB
3. **Prepare compound library** — convert SDF → LMDB, then encode molecule embeddings
4. **Run virtual screening** — score all compounds against the pocket

---

## Step 1: Environment Setup

```bash
# Load pixi
module load pixi/0.56.0

# Install all dependencies (run from the repo root)
pixi install
```

This installs PyTorch, CUDA, RDKit, Uni-Core, and all other dependencies defined
in `pixi.toml`. Only needs to be done once.

---

## Step 2: Prepare the Receptor Pocket

DrugCLIP operates on binding pockets, not full receptors. You need to extract the
binding site region and convert it to LMDB format.

### Option A: You already have an extracted pocket PDB

```bash
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb pocket.pdb \
    --output data/targets/MY_TARGET/pocket.lmdb \
    --name MY_TARGET
```

### Option B: Extract pocket from full receptor using known binding site coordinates

```bash
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb receptor.pdb \
    --output data/targets/MY_TARGET/pocket.lmdb \
    --name MY_TARGET \
    --center 12.5 -3.2 8.0 \
    --cutoff 10.0
```

`--center` is the XYZ coordinate of the binding site center. `--cutoff` is the
radius in Ångströms (default 10.0) — all residues with any atom within this
distance are included.

### Option C: Extract pocket using a co-crystallized ligand

```bash
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb receptor.pdb \
    --output data/targets/MY_TARGET/pocket.lmdb \
    --name MY_TARGET \
    --ligand ligand.sdf \
    --cutoff 10.0
```

The ligand centroid is computed automatically and used as the binding site center.
Accepts PDB or SDF format for the ligand.

### Notes

- Hydrogens are stripped automatically downstream — no need to remove them.
- Pockets are cropped to 511 atoms max by the model — large pockets are fine.
- For pocket detection without a known binding site, see:
  https://github.com/THU-ATOM/Pocket-Detection-of-DTWG

---

## Step 3: Prepare the Compound Library

### 3a. Convert SDF to LMDB

```bash
pixi run python utils/sdf_to_mol_lmdb.py \
    --sdf compounds.sdf \
    --output data/my_library.lmdb
```

If your SDF lacks 3D coordinates, add `--gen-3d` to generate conformers with
RDKit ETKDG:

```bash
pixi run python utils/sdf_to_mol_lmdb.py \
    --sdf compounds.sdf \
    --output data/my_library.lmdb \
    --gen-3d
```

For very large libraries (>1M compounds), you may need to increase the LMDB map
size: `--map-size-gb 100`.

### 3b. Encode molecule embeddings

This step pre-computes molecular embeddings across all 6 model folds. It is
GPU-intensive but only needs to be done once per library.

```bash
pixi run bash encode_mols.sh 0 data/my_library.lmdb data/my_library_embs/
```

Arguments: `<gpu_id> <mol_lmdb_path> <output_dir>`

The script writes embeddings as HDF5/NPY files to the output directory.

For large libraries, you can encode in chunks by editing `encode_mols.sh` and
setting `--start` and `--end` indices.

> **Note:** If your library is small enough to fit in GPU memory during screening
> (< ~500K molecules), you can skip this step and screen directly from the LMDB
> (see Step 4, Option B).

---

## Step 4: Run Virtual Screening

### Option A: Small to medium libraries (< 1M compounds)

Use the single-job pipeline:

```bash
sbatch submit_screening.sh receptor.pdb library.sdf --residue JHN
```

This handles pocket extraction, LMDB conversion, and screening in one job.

### Option B: Large libraries (> 1M compounds, up to billions)

Use the large-scale pipeline which parallelizes across multiple SLURM jobs:

```bash
bash submit_large_screening.sh receptor.pdb library.smi \
    --residue JHN \
    --chunk-size 2000000 \
    --max-parallel 100
```

This submits a chain of SLURM jobs:
1. Splits the library into chunks (local, instant)
2. Extracts the pocket (local, instant)
3. Converts each chunk to LMDB (SLURM array, CPU)
4. Encodes each chunk's embeddings (SLURM array, GPU)
5. Streams all chunks against the pocket (single GPU)

Each stage waits for the previous one to complete. Monitor with `squeue -u $USER`.

### Option C: Screen with pre-encoded embeddings (recommended for large libraries)

After encoding (Step 3b), use the chunked screening pipeline:

```bash
pixi run python utils/screening_chunk.py \
    --gpu_num 1 \
    --mol_embs data/my_library_embs/mol_reps*.npy \
    --pocket_reps data/targets/MY_TARGET/pocket_reps.pkl \
    --batch_size 4 \
    --output_dir output/MY_TARGET/ \
    --rm_intermediate
```

Then retrieve SMILES and scores:

```bash
pixi run python utils/retrieve_chunk.py \
    --input_files "output/MY_TARGET/merge*.pkl" \
    --mol_lmdb data/my_library.lmdb \
    --output_dir results/MY_TARGET/ \
    --num_threads 8
```

Results are saved as `results/MY_TARGET/{pocket_name}.csv`.

### Option B: Screen directly from LMDB (simpler, for smaller libraries)

Edit `retrieval.sh`:

```bash
MOL_PATH="data/my_library.lmdb"
POCKET_PATH="./data/targets/MY_TARGET/pocket.lmdb"
FOLD_VERSION=6_folds
use_cache=False          # <-- important: set to False for custom libraries
save_path="results_MY_TARGET.txt"
```

Then run:

```bash
pixi run screen
```

Or submit via SLURM (see Step 5).

Output is a text file with lines: `smiles,score` sorted by descending score
(top 2% of the library).

---

## Step 5: SLURM Submission

A ready-to-use SLURM script is provided at `submit_screening.sh`:

```bash
sbatch submit_screening.sh
```

Contents:

```bash
#!/bin/bash
#SBATCH --job-name=drugclip_screen
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=drugclip_%j.log

module load pixi/0.56.0

pixi run screen
```

Adjust `--time` based on library size. The pre-encoded ChemDIV library (1.6M
compounds) takes roughly 1-2 hours on a single A100. Encoding a new library
from scratch takes longer — budget 4-8 hours for ~1M compounds.

For multi-GPU screening with the chunked pipeline, increase `--gres=gpu:N` and
set `--gpu_num N` in the screening command.

---

## Quick Reference

| Task | Command |
|------|---------|
| Install environment | `module load pixi/0.56.0 && pixi install` |
| PDB → pocket LMDB | `pixi run python utils/pdb_to_pocket_lmdb.py --pdb receptor.pdb --output pocket.lmdb --ligand ligand.sdf` |
| SDF → molecule LMDB | `pixi run python utils/sdf_to_mol_lmdb.py --sdf compounds.sdf --output mols.lmdb` |
| Encode molecules | `pixi run bash encode_mols.sh 0 mols.lmdb embs/` |
| Run screening | `pixi run screen` (after editing `retrieval.sh`) |
| Submit to SLURM | `sbatch submit_screening.sh` |

---

## Troubleshooting

- **Uni-Core fails to build**: Ensure CUDA is available and `TORCH_CUDA_ARCH_LIST`
  matches your GPU. For A100: `"8.0"`. Check with `nvidia-smi`.
- **Out of memory during screening**: Reduce `--batch-size` in `retrieval.sh`,
  or use the chunked screening pipeline (Option A in Step 4).
- **No 3D coordinates in SDF**: Use `--gen-3d` flag in `sdf_to_mol_lmdb.py`.
  This uses RDKit ETKDG and can be slow for large libraries.
- **LMDB map size error**: Increase `--map-size-gb` when creating the molecule LMDB.
