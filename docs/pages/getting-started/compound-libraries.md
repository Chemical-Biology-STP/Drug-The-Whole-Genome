# Compound Libraries

## Uploading Your Own Library

You can upload a compound library in any of these formats:

| Format | Extension | Notes |
|--------|-----------|-------|
| Structure-Data File | `.sdf` | 3D coordinates preferred; 2D also accepted |
| SMILES | `.smi`, `.smiles` | One SMILES string per line |
| Plain text | `.txt` | One SMILES string per line |

The file is converted to LMDB format on the cluster before screening. For SMILES-based formats, 3D conformers are generated automatically.

**File size limit:** 500 MB

---

## Pre-encoded Server Libraries

Server-side libraries that have already been processed are available in the *Use pre-encoded library* dropdown. These skip the conversion and encoding steps entirely, making screening much faster.

Libraries marked **⚡** have pre-computed embeddings. The compound count is shown next to each library name so you know what you're screening against.

### Currently available

| Library | Compounds | Notes |
|---------|-----------|-------|
| enamine_dds10 | 10,238 | Enamine diversity set, pre-encoded ⚡ |

---

## Adding New Libraries to the Server

To make a library available as a pre-encoded option, place the files in the correct locations:

1. **LMDB file** → `data/libraries/<name>.lmdb`
2. **Embedding cache** → `data/encoded_mol_embs/<name>/6_folds/fold0.pkl` … `fold5.pkl`

The library will appear in the dropdown automatically on the next page load.

To generate the embedding cache for a new library, run:

```bash
pixi run bash encode_mols.sh data/libraries/<name>.lmdb
```
