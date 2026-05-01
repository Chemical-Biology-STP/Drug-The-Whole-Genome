# Viewing Results

Once a job reaches **COMPLETED** status, a **View Results** button appears on the job detail page.

## Results Table

The results page shows your screening hits in a paginated table with:

| Column | Description |
|--------|-------------|
| Rank | Position in the ranked hit list (1 = best score) |
| Structure | 2D molecular structure rendered in the browser |
| SMILES | Canonical SMILES string for the compound |
| Score | DrugCLIP similarity score (higher = better predicted binding) |

50 results are shown per page. Use the pagination controls at the bottom to navigate.

## Understanding the Score

The DrugCLIP score is a cosine similarity between the pocket embedding and the molecule embedding, normalised using a MAD (median absolute deviation) z-score across the library. It is a **relative ranking** within a single run — scores are not directly comparable across different runs or targets.

- Higher scores indicate stronger predicted binding affinity
- The score is not a binding energy (kcal/mol) — it is a ranking metric
- Use it to prioritise compounds for follow-up docking or experimental validation

## What to Do with the Results

1. **Browse the top hits** — look for chemically diverse, drug-like structures
2. **Download the CSV** — for further analysis in Python, Excel, or R
3. **Download the SDF** — for 3D visualisation or docking input
4. **Download the PDBQT** — ready for AutoDock/Vina docking (see [Docking Preparation](docking-preparation.md))
