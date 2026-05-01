# Submitting a Job

All screening jobs are submitted from the **Dashboard** (`/`).

## Step-by-step

### 1. Upload a Receptor PDB

Click **Choose File** under *Receptor PDB File* and select your protein structure in `.pdb` format.

- The **Target Name** field is auto-filled from the filename (e.g. `6QTP.pdb` → `6QTP`). You can edit it.
- The PDB should contain only the protein chain(s) you want to screen against. Waters and ligands are removed automatically during pocket extraction.

### 2. Choose a Compound Library

You have two options:

**Upload a file**  
Supported formats: `.sdf`, `.smi`, `.smiles`, `.txt`  
The file is converted to LMDB format on the cluster before screening begins.

**Use a pre-encoded library**  
Server-side libraries that have already been encoded are listed in the dropdown. Libraries marked ⚡ have pre-computed embeddings — the encoding step is skipped, making screening significantly faster.

### 3. Define the Binding Site

Choose one of four methods to tell DrugCLIP where to focus the screening. See [Binding Site Methods](binding-site-methods.md) for details.

### 4. Set Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| Cutoff (Å) | 10.0 | Radius around the binding site used to extract pocket residues |
| Target Name | from PDB filename | Label for this screening run |
| Top Fraction | 0.02 | Fraction of the library to return as hits (0.02 = top 2%) |

### 5. Screening Mode

The mode is **chosen automatically** based on library size:

- **Standard** — libraries up to ~1 million compounds, runs as a single SLURM job
- **Large-Scale** — larger libraries, split into parallel chunks across multiple GPU nodes

If Large-Scale is selected, additional options appear: chunk size, SLURM partition, and max parallel jobs.

### 6. Submit

Click **Submit Job**. You will be redirected to the job detail page where you can monitor progress.
