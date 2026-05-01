# Docking Preparation

After virtual screening, the typical next step is to re-score your top hits with a physics-based docking program such as **AutoDock Vina** or **Gnina**. The webapp generates the files you need directly.

---

## Files You Need

| File | Where to get it | Format |
|------|----------------|--------|
| Receptor | Job Detail → *Download Receptor PDBQT* | `.pdbqt` |
| Ligands | Results → *Download PDBQT (AutoDock)* | `.pdbqt` |

---

## AutoDock Vina Workflow

### 1. Download the files

From the **Job Detail** page, click **Download Receptor PDBQT**.  
From the **Results** page, click **Download PDBQT (AutoDock)**.

### 2. Define the search box

You need to specify the docking box centre and dimensions. Use the same binding site coordinates you used for screening. In AutoDock Vina:

```bash
vina --receptor receptor.pdbqt \
     --ligand ligand.pdbqt \
     --center_x X --center_y Y --center_z Z \
     --size_x 20 --size_y 20 --size_z 20 \
     --out docked.pdbqt \
     --exhaustiveness 8
```

### 3. Batch docking

The ligand PDBQT contains all hits concatenated. Split it into individual files first:

```python
from vina import Vina  # pip install vina

# Or split manually:
with open("hits.pdbqt") as f:
    content = f.read()

blocks = content.split("MODEL")
for i, block in enumerate(blocks[1:], 1):
    with open(f"lig_{i}.pdbqt", "w") as f:
        f.write("MODEL" + block)
```

### 4. Analyse results

Rank docked poses by Vina score (kcal/mol). Compounds that score well in both DrugCLIP (screening) and Vina (docking) are your highest-confidence hits for experimental follow-up.

---

## Tips

- **Box size**: 20×20×20 Å is a good starting point for a typical drug-sized pocket. Increase to 25–30 Å for larger sites.
- **Exhaustiveness**: 8 is the default. Increase to 16–32 for more thorough sampling of flexible ligands.
- **Protonation**: The receptor PDBQT has hydrogens added at neutral pH. If your target has a known protonation state at the active site (e.g. a catalytic histidine), consider using `reduce` or `Protoss` before conversion.
- **Gnina**: If you want CNN-rescoring, replace `vina` with `gnina` — it accepts the same PDBQT inputs.
