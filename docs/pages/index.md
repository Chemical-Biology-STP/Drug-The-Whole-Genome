# DrugCLIP Virtual Screening

Welcome to the DrugCLIP web application — an HPC-backed virtual screening platform that uses a deep learning model to rank compound libraries against a protein target.

## What is DrugCLIP?

DrugCLIP encodes both a protein binding pocket and candidate molecules into a shared embedding space, then ranks compounds by their similarity to the pocket. This approach is significantly faster than traditional docking while maintaining competitive hit rates.

## Quick Start

1. **Upload your receptor** — provide a PDB file of your protein target
2. **Choose a compound library** — upload your own or select a pre-encoded server library
3. **Define the binding site** — specify where on the protein to screen
4. **Submit** — the job runs on the HPC cluster via SLURM
5. **View results** — browse ranked hits with 2D structures and download for docking

## Navigation

| Page | What you can do |
|------|----------------|
| **Dashboard** | Submit new jobs, view your job history |
| **Job Detail** | Monitor job status, view logs, download receptor PDBQT |
| **Results** | Browse ranked hits, download CSV / SDF / PDBQT |
| **Help** | This documentation |

---

Use the navigation tabs above to explore the full user guide.
