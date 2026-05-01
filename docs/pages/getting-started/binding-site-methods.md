# Binding Site Methods

DrugCLIP needs to know where on the protein to extract the binding pocket. Four methods are available — choose the one that matches the structural information you have.

---

## Co-crystallized Ligand

Upload a separate file (`.pdb` or `.sdf`) containing the coordinates of a ligand already bound in the crystal structure. The pocket is defined as all protein residues within the cutoff distance of this ligand.

**When to use:** You have a PDB structure with a known ligand in the active site (e.g. from a co-crystal structure in the PDB). This is the most reliable method.

**Example:** Download `6QTP.pdb` from the PDB, then export the bound ligand as a separate `.sdf` file from PyMOL or ChimeraX.

---

## HETATM Residue Name

Specify the 3-letter residue code of a heteroatom molecule present in the PDB file. The tool locates that molecule and defines the pocket around it.

**When to use:** You know the 3-letter code of a bound molecule in your PDB (e.g. `JHN`, `ATP`, `NAG`) but don't have a separate ligand file.

**How to find the code:** Open your PDB in a text editor and search for `HETATM` lines. The residue name is in columns 18–20.

---

## Explicit XYZ Coordinates

Provide the X, Y, Z coordinates (in Ångströms) of the binding site centre. All residues within the cutoff radius of this point form the pocket.

**When to use:** You know the centre of the binding site from literature, a previous docking study, or visual inspection in a molecular viewer. Useful when no ligand is present in the structure.

**Tip:** In PyMOL, you can get coordinates by clicking on an atom and reading the position from the bottom bar, or using `get_position` after centering the view on the site.

---

## Protein Residue Numbers

Specify the residue numbers that form the binding pocket directly, with an optional chain ID.

**When to use:** You know which residues form the binding pocket from mutagenesis data, literature, or sequence analysis. This gives the most precise control.

**Format:** Space-separated residue numbers, e.g. `45 67 89 102`. Optionally specify a chain ID (e.g. `A`) if your structure has multiple chains.

---

## Cutoff Radius

All methods use a **cutoff radius** (default: 10.0 Å) to define which residues are included in the pocket. Residues with any atom within this distance of the binding site centre are included.

| Pocket type | Suggested cutoff |
|-------------|-----------------|
| Typical drug-sized pocket | 10.0 Å (default) |
| Large or shallow site | 12–15 Å |
| Small, buried pocket | 8 Å |
