#!/usr/bin/env python3
"""
Convert a PDB file (binding pocket) into the LMDB format expected by DrugCLIP.

Usage:
    python utils/pdb_to_pocket_lmdb.py --pdb pocket.pdb --output data/targets/MY_TARGET/pocket.lmdb

If your PDB is a full receptor, use --center and --cutoff to extract the binding
site residues (all atoms within --cutoff Å of --center x y z).

Examples:
    # Already-extracted pocket PDB:
    python utils/pdb_to_pocket_lmdb.py --pdb pocket.pdb --output pocket.lmdb

    # Extract pocket from full receptor around a known binding site:
    python utils/pdb_to_pocket_lmdb.py --pdb receptor.pdb --output pocket.lmdb \
        --center 12.5 -3.2 8.0 --cutoff 10.0

    # Use a ligand PDB/SDF to define the binding site center:
    python utils/pdb_to_pocket_lmdb.py --pdb receptor.pdb --output pocket.lmdb \
        --ligand ligand.pdb --cutoff 10.0
"""

import argparse
import os
import pickle
import lmdb
import numpy as np


WATER_RESIDUES = {"HOH", "WAT", "H2O", "DOD", "TIP", "TIP3", "SPC"}


def parse_pdb_atoms(pdb_path):
    """Parse ATOM/HETATM records from a PDB file.

    Water molecules and common buffer/salt ions are excluded automatically.

    Returns list of dicts with keys: atom_name, element, coord, res_id
    """
    atoms = []
    with open(pdb_path) as f:
        for line in f:
            if not (line.startswith("ATOM") or line.startswith("HETATM")):
                continue
            res_name = line[17:20].strip()

            # Skip water molecules
            if res_name in WATER_RESIDUES:
                continue

            atom_name = line[12:16].strip()
            chain = line[21]
            res_seq = line[22:26].strip()
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])

            # element from columns 76-78, fallback to first char of atom_name
            element = line[76:78].strip() if len(line) >= 78 else ""
            if not element:
                element = atom_name[0]

            atoms.append({
                "atom_name": atom_name,
                "element": element,
                "coord": np.array([x, y, z], dtype=np.float32),
                "res_id": f"{chain}_{res_name}_{res_seq}",
            })
    return atoms


def get_ligand_center(ligand_path):
    """Compute centroid of a ligand PDB or SDF file."""
    coords = []
    if ligand_path.endswith(".sdf") or ligand_path.endswith(".mol"):
        # Minimal SDF coordinate parsing
        with open(ligand_path) as f:
            lines = f.readlines()
        # counts line is line index 3
        counts = lines[3].split()
        n_atoms = int(counts[0])
        for i in range(4, 4 + n_atoms):
            parts = lines[i].split()
            coords.append([float(parts[0]), float(parts[1]), float(parts[2])])
    else:
        atoms = parse_pdb_atoms(ligand_path)
        coords = [a["coord"] for a in atoms]

    if not coords:
        raise ValueError(f"No atoms found in ligand file: {ligand_path}")
    return np.mean(coords, axis=0)


def get_residues_center(atoms, residue_numbers, chain=None):
    """Compute centroid of specified residue numbers from parsed PDB atoms.

    Args:
        atoms: list of atom dicts from parse_pdb_atoms
        residue_numbers: list of residue sequence numbers (as strings or ints)
        chain: optional chain ID to filter by

    Returns:
        numpy array of centroid [x, y, z]
    """
    res_nums = {str(r) for r in residue_numbers}
    coords = []
    for a in atoms:
        # res_id format: "chain_resname_resseq"
        parts = a["res_id"].split("_")
        a_chain = parts[0]
        a_resseq = parts[2]
        if a_resseq in res_nums:
            if chain is None or a_chain == chain:
                coords.append(a["coord"])

    if not coords:
        raise ValueError(
            f"No atoms found for residue numbers {sorted(res_nums)}"
            + (f" in chain {chain}" if chain else "")
        )
    return np.mean(coords, axis=0)


def extract_pocket(atoms, center, cutoff):
    """Extract residues with any atom within cutoff of center."""
    center = np.array(center, dtype=np.float32)
    close_res_ids = set()
    for a in atoms:
        if np.linalg.norm(a["coord"] - center) <= cutoff:
            close_res_ids.add(a["res_id"])

    return [a for a in atoms if a["res_id"] in close_res_ids]


def write_pocket_lmdb(atoms, output_path, pocket_name="pocket"):
    """Write pocket atoms to an LMDB file in DrugCLIP format.

    Each entry is a pickle dict with keys:
        pocket_atoms: list of atom name strings (PDB-style, e.g. "CA", "CB")
        pocket_coordinates: list of [x, y, z] coordinate lists
        pocket: name string
    """
    pocket_atoms = [a["atom_name"] for a in atoms]
    pocket_coordinates = [a["coord"].tolist() for a in atoms]

    data = {
        "pocket_atoms": pocket_atoms,
        "pocket_coordinates": pocket_coordinates,
        "pocket": pocket_name,
    }

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    # Remove existing file to avoid stale data
    if os.path.exists(output_path):
        os.remove(output_path)

    env = lmdb.open(output_path, subdir=False, map_size=10 * 1024 * 1024)
    with env.begin(write=True) as txn:
        txn.put("0".encode("ascii"), pickle.dumps(data))
    env.close()

    print(f"Wrote {len(pocket_atoms)} atoms to {output_path}")
    print(f"Pocket name: {pocket_name}")


def main():
    parser = argparse.ArgumentParser(
        description="Convert a PDB pocket file to DrugCLIP LMDB format"
    )
    parser.add_argument("--pdb", required=True, help="Input PDB file")
    parser.add_argument("--output", required=True, help="Output LMDB path")
    parser.add_argument("--name", default=None,
                        help="Pocket name (default: derived from filename)")
    parser.add_argument("--center", type=float, nargs=3, metavar=("X", "Y", "Z"),
                        help="Binding site center for pocket extraction")
    parser.add_argument("--ligand", type=str, default=None,
                        help="Ligand file (PDB/SDF) to define binding site center")
    parser.add_argument("--binding-residues", type=str, nargs="+", default=None,
                        metavar="RESNUM",
                        help="Residue numbers to define binding site center "
                             "(e.g., --binding-residues 45 67 89 102)")
    parser.add_argument("--chain", type=str, default=None,
                        help="Chain ID to filter residues (used with --binding-residues)")
    parser.add_argument("--cutoff", type=float, default=10.0,
                        help="Distance cutoff in Å for pocket extraction (default: 10.0)")
    args = parser.parse_args()

    pocket_name = args.name or os.path.splitext(os.path.basename(args.pdb))[0]
    atoms = parse_pdb_atoms(args.pdb)

    if not atoms:
        raise ValueError(f"No ATOM/HETATM records found in {args.pdb}")

    # Extract pocket if center, ligand, or binding residues are provided
    if args.ligand:
        center = get_ligand_center(args.ligand)
        print(f"Ligand center: {center}")
        atoms = extract_pocket(atoms, center, args.cutoff)
    elif args.binding_residues:
        center = get_residues_center(atoms, args.binding_residues, chain=args.chain)
        print(f"Residues {args.binding_residues} center: {center}")
        atoms = extract_pocket(atoms, center, args.cutoff)
    elif args.center:
        atoms = extract_pocket(atoms, args.center, args.cutoff)

    if not atoms:
        raise ValueError("No atoms remaining after pocket extraction. "
                         "Try increasing --cutoff.")

    write_pocket_lmdb(atoms, args.output, pocket_name)


if __name__ == "__main__":
    main()
