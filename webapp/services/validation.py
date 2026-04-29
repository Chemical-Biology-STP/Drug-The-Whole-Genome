"""
Parameter validation functions for the DrugCLIP web application.

Provides helpers for validating file extensions, deriving target names from
PDB filenames, validating numeric screening parameters, and validating binding
site method fields.
"""

import os
from typing import Optional

# Allowed file extensions per upload type (mirrors config.ALLOWED_EXTENSIONS)
_ALLOWED_EXTENSIONS: dict[str, set[str]] = {
    "pdb": {".pdb"},
    "library": {".sdf", ".smi", ".smiles", ".txt"},
    "ligand": {".pdb", ".sdf"},
}


def validate_file_extension(filename: str, file_type: str) -> bool:
    """Check whether *filename* has an allowed extension for *file_type*.

    Parameters
    ----------
    filename:
        The name (or path) of the file to check.
    file_type:
        One of ``'pdb'``, ``'library'``, or ``'ligand'``.

    Returns
    -------
    bool
        ``True`` if the file's extension (case-insensitive) is in the allowed
        set for *file_type*, ``False`` otherwise.  Also returns ``False`` if
        *file_type* is not one of the three recognised values.
    """
    allowed = _ALLOWED_EXTENSIONS.get(file_type)
    if allowed is None:
        return False
    _, ext = os.path.splitext(filename)
    return ext.lower() in allowed


def derive_target_name(pdb_filename: str) -> str:
    """Derive a target name from a PDB filename.

    Strips any leading directory components and the ``.pdb`` extension.

    Parameters
    ----------
    pdb_filename:
        A filename or path ending in ``.pdb``, e.g. ``"/path/to/6QTP.pdb"``
        or ``"6QTP.pdb"``.

    Returns
    -------
    str
        The bare stem of the filename, e.g. ``"6QTP"``.

    Examples
    --------
    >>> derive_target_name("/path/to/6QTP.pdb")
    '6QTP'
    >>> derive_target_name("6QTP.pdb")
    '6QTP'
    """
    basename = os.path.basename(pdb_filename)
    stem, _ = os.path.splitext(basename)
    return stem


def validate_params(cutoff: float, top_fraction: float, chunk_size: int) -> dict:
    """Validate numeric screening parameters.

    Parameters
    ----------
    cutoff:
        Pocket extraction radius in Ångströms.  Must be strictly positive.
    top_fraction:
        Fraction of the library to return as hits.  Must satisfy
        ``0 < top_fraction <= 1.0``.
    chunk_size:
        Number of compounds per chunk for large-scale screening.  Must be
        ``>= 1000``.

    Returns
    -------
    dict
        A mapping of ``field_name -> error_message`` for every invalid field.
        Returns an empty dict when all values are valid.
    """
    errors: dict[str, str] = {}

    if not (cutoff > 0):
        errors["cutoff"] = "Cutoff must be a positive number."

    if not (0 < top_fraction <= 1.0):
        errors["top_fraction"] = (
            "Top fraction must be between 0 (exclusive) and 1 (inclusive)."
        )

    if chunk_size < 1000:
        errors["chunk_size"] = (
            "Chunk size must be a positive integer of at least 1,000."
        )

    return errors


def validate_binding_site(method: str, fields: dict) -> Optional[str]:
    """Validate that the required fields for a binding site method are present.

    Parameters
    ----------
    method:
        One of ``'ligand'``, ``'residue'``, ``'center'``, or
        ``'binding_residues'``.
    fields:
        A dict containing the field values for the selected method.

    Returns
    -------
    Optional[str]
        ``None`` if the method and its fields are valid, or an error message
        string if validation fails.
    """
    if method == "ligand":
        if not fields.get("ligand_path"):
            return "Ligand file is required for this binding site method. Accepted formats: .pdb, .sdf"
        return None

    if method == "residue":
        if not fields.get("residue_name"):
            return "Residue name is required (e.g., JHN)."
        return None

    if method == "center":
        x = fields.get("center_x")
        y = fields.get("center_y")
        z = fields.get("center_z")
        if x is None or y is None or z is None:
            return "All three coordinates (X, Y, Z) are required and must be numbers."
        return None

    if method == "binding_residues":
        if not fields.get("binding_residues"):
            return "At least one residue number is required."
        return None

    # Unknown method
    return (
        "A binding site definition is required. Choose one of the four methods."
    )
