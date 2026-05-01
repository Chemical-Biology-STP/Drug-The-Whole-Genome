# Downloading Files

Three download formats are available from the results page, plus a receptor PDBQT from the job detail page.

---

## Download CSV

A plain-text file with one compound per line:

```
SMILES,score
CCN(Cc1ccc2...)C(=O)...,3.4521
...
```

Use this for:

- Further filtering or analysis in Python/R
- Importing into a spreadsheet
- Sharing hit lists with collaborators

---

## Download SDF (3D)

A multi-molecule SDF file with a 3D conformer for each hit. Each molecule has these properties set:

| Property | Value |
|----------|-------|
| `_Name` | `rank_N` |
| `SMILES` | canonical SMILES |
| `DrugCLIP_Score` | similarity score |
| `Rank` | rank in the hit list |

Use this for:

- 3D visualisation in PyMOL, ChimeraX, or Maestro
- Input to docking software that accepts SDF
- Pharmacophore analysis

Conformers are generated using RDKit ETKDGv3 with MMFF geometry optimisation. Molecules that fail conformer generation are skipped.

---

## Download PDBQT (AutoDock)

A multi-molecule PDBQT file ready for AutoDock or AutoDock Vina. Each molecule block is prefixed with:

```
REMARK DrugCLIP rank=1 score=3.4521
REMARK SMILES=CCN(Cc1ccc2...)...
```

Gasteiger charges are assigned and rotatable bonds are detected automatically via AutoDockTools.

See [Docking Preparation](docking-preparation.md) for the full docking workflow.

---

## Receptor PDBQT

Available from the **Job Detail** page (not the results page). Downloads the receptor PDB converted to PDBQT format with:

- Hydrogens added
- Waters and non-standard residues removed
- Gasteiger charges assigned

This is the receptor file you need for AutoDock/Vina docking.
