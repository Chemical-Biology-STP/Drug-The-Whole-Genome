# DrugCLIP for Drug-The-Whole-Genome

Virtual screening of compound libraries against protein targets using DrugCLIP.

## Quick start

There are two ways to use DrugCLIP: as an HPC module or from a local clone.

### Option A: HPC module (recommended)

```bash
module load DrugCLIP/1.0

# First time only: download model weights
drugclip-download-weights

# Screen a compound library against a protein target
sbatch --partition=ga100 --gres=gpu:1 --cpus-per-task=8 --mem=64G --time=04:00:00 \
    --wrap="drugclip-screen receptor.pdb library.sdf --residue LIG"
```

### Option B: Local clone with pixi

```bash
# 1. Install the environment (one-time)
pixi install
pixi run install-unicore

# 2. Screen a compound library against a protein target
sbatch submit_screening.sh receptor.pdb library.sdf --residue LIG
```

## Setup

### Prerequisites

- Linux with SLURM scheduler and GPU nodes
- [Pixi](https://pixi.sh) package manager

### Install

```bash
# Install all dependencies
pixi install

# Install Uni-Core (requires torch, built separately)
pixi run install-unicore
```

### Download model weights

Download `model_weights.zip`, `encoded_mol_embs.zip`, and `targets.zip` from
[HuggingFace](https://huggingface.co/datasets/bgao95/DrugCLIP_data), unzip
them, and place them inside `./data/`.

## Usage

There are two scripts for running virtual screening. Pick based on your library size.

### Small to medium libraries (up to ~1M compounds)

Use `submit_screening.sh`. It runs pocket extraction, library conversion, and
screening in a single SLURM job.

```bash
sbatch submit_screening.sh <receptor.pdb> <library.sdf|smi> [binding site options]
```

#### Binding site options (one required)

| Option | Description |
|--------|-------------|
| `--ligand <file>` | Co-crystallized ligand (PDB or SDF) to define the binding site |
| `--residue <name>` | HETATM residue name in the PDB (e.g., a drug molecule) |
| `--center <x> <y> <z>` | Explicit binding site coordinates |
| `--binding-residues <n> [n...]` | Protein residue numbers that form the binding site |

#### Additional options

| Option | Default | Description |
|--------|---------|-------------|
| `--cutoff <float>` | 10.0 | Pocket extraction radius in Å |
| `--chain <id>` | all | Chain ID filter (used with `--binding-residues`) |
| `--name <string>` | from PDB filename | Target name for output files |
| `--jobs-dir <path>` | `jobs/` | Directory for job output |

#### Examples

```bash
# Using a co-crystallized ligand to define the binding site
sbatch submit_screening.sh examples/6QTP.pdb compounds.sdf --ligand ligand.sdf

# Using a HETATM residue name (e.g., the drug JHN in the PDB)
sbatch submit_screening.sh examples/6QTP.pdb compounds.sdf --residue JHN

# Using explicit coordinates
sbatch submit_screening.sh receptor.pdb compounds.smi --center 25.6 7.8 19.0

# Using protein residue numbers that line the binding pocket
sbatch submit_screening.sh receptor.pdb compounds.sdf --binding-residues 45 67 89 102

# Same, but restricted to chain A
sbatch submit_screening.sh receptor.pdb compounds.sdf --binding-residues 45 67 89 --chain A
```

### Large libraries (over ~1M compounds)

Use `submit_large_screening.sh`. It splits the work into parallel SLURM jobs:

1. Splits the input file into chunks (local, instant)
2. Extracts the binding pocket (local, instant)
3. Converts each chunk to LMDB format (SLURM array, CPU)
4. Encodes molecule embeddings per chunk (SLURM array, GPU)
5. Scores all chunks against the pocket (single GPU)

Each stage waits for the previous one to finish automatically.

```bash
bash submit_large_screening.sh <receptor.pdb> <library.sdf|smi> [binding site options]
```

Note: this script is run with `bash`, not `sbatch` — it submits SLURM jobs internally.

#### Additional options for large-scale screening

| Option | Default | Description |
|--------|---------|-------------|
| `--chunk-size <int>` | 1,000,000 | Molecules per chunk |
| `--partition <name>` | ga100 | SLURM partition |
| `--max-parallel <int>` | 50 | Max simultaneous SLURM array tasks |

#### Examples

```bash
# Screen 15 billion compounds, 2M per chunk, up to 100 jobs at a time
bash submit_large_screening.sh receptor.pdb enamine_real_15B.smi \
    --residue JHN \
    --chunk-size 2000000 \
    --max-parallel 100

# Screen a 50M compound SDF with default settings
bash submit_large_screening.sh receptor.pdb chembl_50M.sdf \
    --ligand cocrystal.sdf
```

## Using the HPC module

If DrugCLIP is installed as an HPC module, you don't need pixi or a local clone.
Everything is available via `module load`.

### Setup (one-time)

```bash
module load DrugCLIP/1.0
drugclip-download-weights
```

### Available commands

After `module load DrugCLIP/1.0`, the following commands are on your PATH:

| Command | Description |
|---------|-------------|
| `drugclip-screen` | Screen a library against a target (single job) |
| `drugclip-screen-large` | Screen a large library (parallel jobs) |
| `drugclip-prepare-pocket` | Convert a PDB to pocket LMDB |
| `drugclip-prepare-library` | Convert an SDF/SMI to molecule LMDB |
| `drugclip-download-weights` | Download model weights from HuggingFace |

### Running a screen via sbatch --wrap

For quick, one-off screens you can use `sbatch --wrap`:

```bash
module load DrugCLIP/1.0

sbatch --partition=ga100 --gres=gpu:1 --cpus-per-task=8 --mem=64G --time=04:00:00 \
    --output=drugclip_%j.log \
    --wrap="drugclip-screen receptor.pdb library.sdf --residue JHN"
```

### Writing a dedicated SLURM script

For reproducibility or repeated use, create a SLURM script:

```bash
#!/bin/bash
#SBATCH --job-name=drugclip_screen
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=drugclip_%j.log

module load DrugCLIP/1.0

# Screen using a HETATM residue to define the binding site
drugclip-screen examples/6QTP.pdb compounds.sdf --residue JHN
```

Save this as `my_screen.sh` and submit:

```bash
sbatch my_screen.sh
```

### More SLURM script examples

Screen using a co-crystallized ligand:

```bash
#!/bin/bash
#SBATCH --job-name=drugclip_CDK2
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=drugclip_%j.log

module load DrugCLIP/1.0

drugclip-screen CDK2.pdb enamine_10k.sdf --ligand cocrystal_ligand.sdf --cutoff 12.0
```

Screen using protein residue numbers:

```bash
#!/bin/bash
#SBATCH --job-name=drugclip_EGFR
#SBATCH --partition=ga100
#SBATCH --gres=gpu:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=04:00:00
#SBATCH --output=drugclip_%j.log

module load DrugCLIP/1.0

drugclip-screen EGFR.pdb library.smi \
    --binding-residues 718 719 720 721 790 791 792 793 854 855 \
    --chain A \
    --name EGFR_kinase
```

### Large-scale screening via module

For libraries over ~1M compounds, run `drugclip-screen-large` from a login node
(it submits SLURM jobs internally):

```bash
module load DrugCLIP/1.0

drugclip-screen-large receptor.pdb enamine_real_15B.smi \
    --residue JHN \
    --chunk-size 2000000 \
    --max-parallel 100
```

### Manual data preparation via module

```bash
module load DrugCLIP/1.0

# Convert a PDB to pocket LMDB
drugclip-prepare-pocket --pdb receptor.pdb --output pocket.lmdb --ligand ligand.sdf

# Convert an SDF or SMILES file to molecule LMDB
drugclip-prepare-library --input compounds.sdf --output compounds.lmdb
drugclip-prepare-library --input compounds.smi --output compounds.lmdb
```

## Output

Results are written to `jobs/<target>_vs_<library>/results.txt`. Each line is:

```
SMILES,score
```

Sorted by descending score (top 2% of the library). Higher scores indicate
stronger predicted binding.

```
jobs/
  logs/                              SLURM log files
  6QTP_vs_compounds/
    results.txt                      Screening hits
    slurm_12345.log                  Symlink to SLURM log
```

## Preparing input data

### Compound libraries

The pipeline accepts SDF (`.sdf`) and SMILES (`.smi`, `.smiles`, `.txt`) files
directly. Conversion to the internal LMDB format happens automatically.

- 3D conformers are generated automatically for molecules with only 2D coordinates
- Duplicate libraries are detected by content hash and skipped
- You can also convert manually:

```bash
pixi run python utils/sdf_to_mol_lmdb.py --input compounds.sdf --output data/libraries/compounds.lmdb
pixi run python utils/sdf_to_mol_lmdb.py --input compounds.smi --output data/libraries/compounds.lmdb
```

### Protein targets

Pocket extraction from PDB files is handled automatically by the screening
scripts. To do it manually:

```bash
# From a co-crystallized ligand
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb receptor.pdb --output data/targets/MY_TARGET/pocket.lmdb \
    --ligand ligand.sdf --cutoff 10.0

# From binding site coordinates
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb receptor.pdb --output data/targets/MY_TARGET/pocket.lmdb \
    --center 12.5 -3.2 8.0

# From residue numbers
pixi run python utils/pdb_to_pocket_lmdb.py \
    --pdb receptor.pdb --output data/targets/MY_TARGET/pocket.lmdb \
    --binding-residues 45 67 89 102 --chain A
```

## Caching

Molecule embeddings are cached automatically so re-screening the same library
against a different target skips the encoding step.

- Cache location is derived from the LMDB content hash, so identical libraries
  share the same cache regardless of filename
- Cached embeddings are stored under `data/encoded_mol_embs/`
- The first run encodes and saves; subsequent runs load from cache

## Benchmarking

Download `DUD-E.zip` and `LIT-PCBA.zip` from
[HuggingFace](https://huggingface.co/datasets/bgao95/DrugCLIP_data), unzip
them, and place them inside `./data/`.

```bash
bash test.sh
```

Set `TASK` to `DUDE` or `PCBA` in `test.sh`.

## Project structure

```
submit_screening.sh          Single-job screening (< 1M compounds)
submit_large_screening.sh    Multi-job screening (> 1M compounds)
screen_pipeline.sh           Internal: called by submit_screening.sh
retrieval.sh                 Internal: called by screen_pipeline.sh
pixi.toml                    Environment definition

utils/
  pdb_to_pocket_lmdb.py      PDB → pocket LMDB
  sdf_to_mol_lmdb.py         SDF/SMI → molecule LMDB
  split_input.py              Split large files into chunks
  screen_streaming.py         Stream-score encoded chunks
  screening_chunk.py          Chunked GPU screening
  retrieve_chunk.py           Retrieve SMILES from chunked results

unimol/                       DrugCLIP model code
data/
  model_weights/              Trained model weights (6 and 8 folds)
  encoded_mol_embs/           Cached molecule embeddings
  targets/                    Pocket LMDB files
  libraries/                  Compound library LMDB files
```

## Other tools

- Pocket pretraining: https://github.com/THU-ATOM/ProFSA
- Virtual screening post-processing: https://github.com/THU-ATOM/DrugCLIP_screen_pipeline
- Pocket detection: https://github.com/THU-ATOM/Pocket-Detection-of-DTWG

## Rebuilding the HPC module

When you make code changes and want to update the installed module, there are
two approaches.

### Quick patch (immediate, no rebuild)

Copy changed files directly into the installed module:

```bash
INSTALL_DIR=/nemo/stp/chemicalbiology/home/shared/easybuild/software/DrugCLIP/1.0

cp utils/pdb_to_pocket_lmdb.py  $INSTALL_DIR/utils/
cp unimol/retrieval.py           $INSTALL_DIR/unimol/
cp unimol/tasks/drugclip.py      $INSTALL_DIR/unimol/tasks/
cp screen_pipeline.sh            $INSTALL_DIR/
```

Takes effect immediately. Won't survive a full rebuild.

### Full rebuild

Run this from the repo root on a GPU node (Uni-Core needs CUDA at build time):

```bash
# 1. Commit your changes
git add -A
git commit -m "describe your changes"

# 2. Regenerate the source tarball
git archive --format=tar.gz -o /nemo/stp/chemicalbiology/home/shared/eb/drugclip-1.0.tar.gz HEAD

# 3. Replace the EasyBuild source cache
cp /nemo/stp/chemicalbiology/home/shared/eb/drugclip-1.0.tar.gz \
   /nemo/stp/chemicalbiology/home/shared/easybuild/sources/d/DrugCLIP/drugclip-1.0.tar.gz

# 4. Remove the old install
rm -rf /nemo/stp/chemicalbiology/home/shared/easybuild/software/DrugCLIP

# 5. Rebuild
eb DrugCLIP-1.0-foss-2023a-CUDA-12.1.1.eb \
    --installpath=/nemo/stp/chemicalbiology/home/shared/easybuild \
    --robot --force
```

The rebuild takes a few minutes (pixi resolves dependencies and Uni-Core
compiles CUDA extensions). The EasyBuild recipe is at
`DrugCLIP-1.0-foss-2023a-CUDA-12.1.1.eb` in the repo root.

## License

- **Source code**: [Apache 2.0](LICENSE) — free for academic and commercial use
- **Database**: [CC BY 4.0](docs/LICENSE.md) — free with attribution
- **Model weights & outputs**: [CC BY-NC 4.0](docs/MODEL_WEIGHTS_LICENSE.md) — non-commercial only. Contact the authors for commercial licensing.
- **Uni-Mol components**: [MIT](unimol/LICENSE) — modified from Uni-Mol, Copyright (c) 2022 DP Technology
