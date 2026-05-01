"""PDBQT conversion utilities using AutoDockTools_py3.

Provides helpers to convert:
- A receptor PDB file → PDBQT (for AutoDock/Vina docking)
- A list of (name, smiles, score) tuples → multi-molecule PDBQT (ligands)

Both functions return bytes ready to be served as file downloads.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile

logger = logging.getLogger(__name__)

# AutoDockTools prepare scripts invoked as Python modules
_PREPARE_RECEPTOR = "AutoDockTools.Utilities24.prepare_receptor4"
_PREPARE_LIGAND   = "AutoDockTools.Utilities24.prepare_ligand4"


def receptor_pdb_to_pdbqt(pdb_path: str) -> bytes:
    """Convert a receptor PDB file to PDBQT format.

    Adds hydrogens, removes waters/non-standard residues, and assigns
    Gasteiger charges via AutoDockTools prepare_receptor4.

    Parameters
    ----------
    pdb_path:
        Absolute path to the input PDB file.

    Returns
    -------
    bytes
        The PDBQT file content.

    Raises
    ------
    RuntimeError
        If the conversion fails or produces no output.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "receptor.pdbqt")
        result = subprocess.run(
            [
                sys.executable, "-m", _PREPARE_RECEPTOR,
                "-r", pdb_path,
                "-o", out_path,
                "-A", "hydrogens",
                "-U", "nphs_lps_waters_nonstdres",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"prepare_receptor4 failed (rc={result.returncode}): {result.stderr.strip()}"
            )
        if not os.path.exists(out_path):
            raise RuntimeError("prepare_receptor4 produced no output file.")
        with open(out_path, "rb") as f:
            return f.read()


def smiles_list_to_pdbqt(
    entries: list[tuple[int, str, float]],
) -> tuple[bytes, int, int]:
    """Convert a list of (rank, smiles, score) hits to a multi-molecule PDBQT.

    Each molecule is:
    1. Embedded as a 3D conformer with RDKit ETKDGv3
    2. MMFF-optimised
    3. Converted to PDBQT via AutoDockTools prepare_ligand4

    Individual molecules that fail at any step are skipped.

    Parameters
    ----------
    entries:
        List of (rank, smiles, score) tuples as returned by parse_results().

    Returns
    -------
    tuple[bytes, int, int]
        (pdbqt_bytes, n_ok, n_fail)
    """
    try:
        from rdkit import Chem
        from rdkit.Chem import AllChem
    except ImportError as e:
        raise RuntimeError("RDKit is required for ligand PDBQT generation.") from e

    pdbqt_blocks: list[str] = []
    n_ok = 0
    n_fail = 0

    with tempfile.TemporaryDirectory() as tmpdir:
        for rank, smiles, score in entries:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                n_fail += 1
                continue

            mol = Chem.AddHs(mol)
            params = AllChem.ETKDGv3()
            params.randomSeed = 42
            if AllChem.EmbedMolecule(mol, params) == -1:
                if AllChem.EmbedMolecule(mol, AllChem.ETKDG()) == -1:
                    logger.warning("Conformer generation failed for rank %d", rank)
                    n_fail += 1
                    continue

            AllChem.MMFFOptimizeMolecule(mol)
            mol = Chem.RemoveHs(mol)

            mol.SetProp("_Name", f"rank_{rank}")

            pdb_path = os.path.join(tmpdir, f"lig_{rank}.pdb")
            pdbqt_path = os.path.join(tmpdir, f"lig_{rank}.pdbqt")
            Chem.MolToPDBFile(mol, pdb_path)

            result = subprocess.run(
                [
                    sys.executable, "-m", _PREPARE_LIGAND,
                    "-l", pdb_path,
                    "-o", pdbqt_path,
                    "-A", "hydrogens",
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0 or not os.path.exists(pdbqt_path):
                logger.warning(
                    "prepare_ligand4 failed for rank %d: %s", rank, result.stderr.strip()
                )
                n_fail += 1
                continue

            with open(pdbqt_path) as f:
                block = f.read().strip()

            # Prepend metadata as REMARK lines
            header = (
                f"REMARK DrugCLIP rank={rank} score={score:.6f}\n"
                f"REMARK SMILES={smiles}\n"
            )
            pdbqt_blocks.append(header + block)
            n_ok += 1

    return "\n".join(pdbqt_blocks).encode("utf-8"), n_ok, n_fail
