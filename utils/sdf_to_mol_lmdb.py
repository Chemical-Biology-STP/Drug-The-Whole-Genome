#!/usr/bin/env python3
"""
Convert a compound library (SDF or SMILES file) into the LMDB format expected
by DrugCLIP.

Usage:
    python utils/sdf_to_mol_lmdb.py --input compounds.sdf --output mols.lmdb
    python utils/sdf_to_mol_lmdb.py --input compounds.smi --output mols.lmdb

Supported input formats (auto-detected from extension):
    .sdf, .sd, .mol  — SDF / MDL Molfile
    .smi, .smiles, .csv, .tsv, .txt — SMILES file (one SMILES per line,
        optionally followed by whitespace and a name/ID)

Each molecule entry in the LMDB contains:
    - atoms: list of element symbols
    - coordinates: 3D conformer coordinates (N, 3)
    - smiles: canonical SMILES string

3D conformers are generated automatically for molecules that lack them or
have only 2D coordinates.

Deduplication:
    Before converting, the script hashes the source file and checks existing
    LMDB files in the output directory (and a configurable search path) for a
    matching __source_hash__. If a match is found, conversion is skipped and the
    existing LMDB path is printed.
"""

import argparse
import glob
import hashlib
import os
import pickle
import lmdb
import numpy as np
from tqdm import tqdm


SDF_EXTENSIONS = {".sdf", ".sd", ".mol"}
SMI_EXTENSIONS = {".smi", ".smiles", ".csv", ".tsv", ".txt"}


def hash_file(path, algo="sha256"):
    """Compute a hex digest of a file's contents."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)  # 1 MB chunks
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def find_existing_lmdb(source_hash, search_dirs):
    """Search for an LMDB that was built from the same source file.

    Checks sidecar .meta files first (new format), then falls back to
    checking __source_hash__ keys inside the LMDB (legacy format).
    """
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for lmdb_path in glob.glob(os.path.join(search_dir, "*.lmdb")):
            # Check sidecar meta file first (new format — no metadata in LMDB)
            meta_path = lmdb_path + ".meta"
            if os.path.exists(meta_path):
                try:
                    with open(meta_path) as f:
                        for line in f:
                            if line.startswith("source_hash="):
                                if line.strip().split("=", 1)[1] == source_hash:
                                    return lmdb_path
                except Exception:
                    pass
                continue
            # Legacy: check __source_hash__ key inside LMDB
            try:
                env = lmdb.open(lmdb_path, subdir=False, readonly=True, lock=False)
                with env.begin() as txn:
                    raw = txn.get(b"__source_hash__")
                    if raw is not None and raw.decode("ascii") == source_hash:
                        env.close()
                        return lmdb_path
                env.close()
            except Exception:
                continue
    return None


def _is_2d_conformer(mol):
    """Check if a molecule's conformer is 2D (all x, y, or z coords are zero)."""
    if mol.GetNumConformers() == 0:
        return True
    conf = mol.GetConformer()
    n = mol.GetNumAtoms()
    if n == 0:
        return True
    coords = np.array(
        [list(conf.GetAtomPosition(j)) for j in range(n)],
        dtype=np.float32,
    )
    return np.all(coords[:, 0] == 0) or np.all(coords[:, 1] == 0) or np.all(coords[:, 2] == 0)


def _generate_3d(mol, index):
    """Generate a 3D conformer for a molecule. Returns the mol or None on failure."""
    from rdkit import Chem
    from rdkit.Chem import AllChem

    mol_h = Chem.AddHs(mol)
    result = AllChem.EmbedMolecule(mol_h, AllChem.ETKDGv3())
    if result == -1:
        print(f"Warning: skipping molecule {index} (conformer generation failed)")
        return None
    AllChem.MMFFOptimizeMoleculeConfs(mol_h)
    return mol_h


def _mol_to_entry(mol):
    """Extract (atoms, coordinates, smiles) from an RDKit mol with a 3D conformer."""
    from rdkit import Chem

    conf = mol.GetConformer()
    atoms = [atom.GetSymbol() for atom in mol.GetAtoms()]
    coords = np.array(
        [list(conf.GetAtomPosition(j)) for j in range(mol.GetNumAtoms())],
        dtype=np.float32,
    )
    smiles = Chem.MolToSmiles(Chem.RemoveHs(mol))
    return atoms, coords, smiles


def process_sdf(sdf_path, gen_3d=False):
    """Read an SDF and yield (atoms, coordinates, smiles) per molecule."""
    from rdkit import Chem

    supplier = Chem.SDMolSupplier(sdf_path, removeHs=False)

    for i, mol in enumerate(supplier):
        if mol is None:
            print(f"Warning: skipping molecule {i} (failed to parse)")
            continue

        if gen_3d or mol.GetNumConformers() == 0 or _is_2d_conformer(mol):
            mol = _generate_3d(mol, i)
            if mol is None:
                continue

        yield _mol_to_entry(mol)


def process_smiles(smi_path):
    """Read a SMILES file and yield (atoms, coordinates, smiles) per molecule.

    Expects one SMILES per line, optionally followed by whitespace and a name.
    Lines starting with # are skipped. A header line is auto-detected and
    skipped if the first token is not a valid SMILES.
    """
    from rdkit import Chem

    with open(smi_path) as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    if not lines:
        return

    # Auto-detect and skip header: if the first token doesn't parse as a
    # molecule, treat the line as a header.
    first_token = lines[0].split()[0]
    if Chem.MolFromSmiles(first_token) is None:
        lines = lines[1:]

    for i, line in enumerate(lines):
        parts = line.split()
        smi_str = parts[0]

        mol = Chem.MolFromSmiles(smi_str)
        if mol is None:
            print(f"Warning: skipping line {i} (failed to parse SMILES: {smi_str})")
            continue

        mol = _generate_3d(mol, i)
        if mol is None:
            continue

        yield _mol_to_entry(mol)


def detect_format(path):
    """Detect input format from file extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in SDF_EXTENSIONS:
        return "sdf"
    if ext in SMI_EXTENSIONS:
        return "smi"
    raise ValueError(
        f"Unrecognized file extension '{ext}'. "
        f"Supported: {sorted(SDF_EXTENSIONS | SMI_EXTENSIONS)}"
    )


def main():
    parser = argparse.ArgumentParser(
        description="Convert a compound library (SDF or SMILES) to DrugCLIP LMDB format"
    )
    # Accept both --input and legacy --sdf flag
    parser.add_argument("--input", "--sdf", required=True, dest="input",
                        help="Input file (SDF, SMI, or SMILES)")
    parser.add_argument("--output", required=True, help="Output LMDB path")
    parser.add_argument("--gen-3d", action="store_true",
                        help="Force 3D conformer generation for all molecules")
    parser.add_argument("--map-size-gb", type=int, default=50,
                        help="LMDB map size in GB (default: 50)")
    parser.add_argument("--search-dirs", type=str, nargs="*", default=None,
                        help="Extra directories to search for existing LMDBs "
                             "(default: output directory and ./data/)")
    parser.add_argument("--force", action="store_true",
                        help="Skip deduplication check and always convert")
    args = parser.parse_args()

    input_path = args.input
    fmt = detect_format(input_path)

    # --- Deduplication check ---
    if not args.force:
        print(f"Hashing source file: {input_path}")
        source_hash = hash_file(input_path)
        print(f"Source hash: {source_hash[:16]}...")

        search_dirs = args.search_dirs or []
        output_dir = os.path.dirname(args.output) or "."
        for d in [output_dir, "./data", "./data/libraries"]:
            if d not in search_dirs:
                search_dirs.append(d)

        existing = find_existing_lmdb(source_hash, search_dirs)
        if existing:
            print(f"Identical library already exists: {existing}")
            print(f"Skipping conversion. Use --force to convert anyway.")
            return
    else:
        source_hash = hash_file(input_path)

    # --- Convert ---
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    if os.path.exists(args.output):
        os.remove(args.output)

    if fmt == "sdf":
        mol_iter = process_sdf(input_path, gen_3d=args.gen_3d)
    else:
        mol_iter = process_smiles(input_path)

    map_size = args.map_size_gb * (1024 ** 3)
    env = lmdb.open(args.output, subdir=False, map_size=map_size)

    count = 0
    content_hash = hashlib.sha256()
    with env.begin(write=True) as txn:
        for atoms, coords, smiles in tqdm(mol_iter, desc="Converting molecules"):
            data = {
                "atoms": atoms,
                "coordinates": coords,
                "smiles": smiles,
            }
            serialized = pickle.dumps(data)
            content_hash.update(serialized)
            txn.put(str(count).encode("ascii"), serialized)
            count += 1

        # Store metadata as separate keys — NOTE: these are filtered out
        # by the validation step and not read by unicore's LMDBDataset
        digest = content_hash.hexdigest()
        txn.put(b"__content_hash__", digest.encode("ascii"))
        txn.put(b"__source_hash__", source_hash.encode("ascii"))

    env.close()

    # --- Validate: verify every molecule entry can be unpickled ---
    print("Validating LMDB entries...")
    env = lmdb.open(args.output, subdir=False, readonly=True, lock=False)
    bad_keys = []
    with env.begin() as txn:
        cursor = txn.cursor()
        for key, value in cursor.iternext():
            if key in (b"__content_hash__", b"__source_hash__"):
                continue
            try:
                pickle.loads(value)
            except Exception:
                bad_keys.append(key)
    env.close()

    if bad_keys:
        print(f"Warning: {len(bad_keys)} corrupt entries found, removing them...")
        env = lmdb.open(args.output, subdir=False, map_size=map_size)
        with env.begin(write=True) as txn:
            for key in bad_keys:
                txn.delete(key)
        env.close()
        count -= len(bad_keys)
        print(f"Removed {len(bad_keys)} corrupt entries. Final count: {count}")
    else:
        print("All entries validated successfully.")

    # --- Remove metadata keys so unicore LMDBDataset only sees molecule entries ---
    env = lmdb.open(args.output, subdir=False, map_size=map_size)
    with env.begin(write=True) as txn:
        txn.delete(b"__content_hash__")
        txn.delete(b"__source_hash__")
    env.close()

    # Write metadata to a sidecar file instead
    meta_path = args.output + ".meta"
    with open(meta_path, "w") as f:
        f.write(f"source_hash={source_hash}\n")
        f.write(f"content_hash={digest}\n")
        f.write(f"count={count}\n")

    print(f"Wrote {count} molecules to {args.output}")
    print(f"Content hash: {digest}")
    print(f"Source hash:  {source_hash}")
    print(f"Content hash: {digest}")
    print(f"Source hash:  {source_hash}")


if __name__ == "__main__":
    main()
