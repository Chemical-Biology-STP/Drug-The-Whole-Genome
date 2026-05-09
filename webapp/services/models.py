"""Data models for the DrugCLIP web application.

Defines JobParams (form submission parameters) and JobRecord (persistent job
metadata) as dataclasses with JSON serialization support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class JobParams:
    """Parameters collected from the submission form.

    Passed from route handlers into the service layer for job submission.
    """

    session_id: str
    pdb_path: str                          # Absolute path to uploaded PDB
    library_path: str                      # Absolute path to uploaded library (local) or HPC path
    binding_site_method: str               # 'ligand' | 'residue' | 'center' | 'binding_residues'
    ligand_path: Optional[str] = None      # Path to ligand file (if method='ligand')
    residue_name: Optional[str] = None     # HETATM residue name (if method='residue')
    center_x: Optional[float] = None      # X coordinate (if method='center')
    center_y: Optional[float] = None      # Y coordinate
    center_z: Optional[float] = None      # Z coordinate
    binding_residues: Optional[str] = None  # Space-separated residue numbers (if method='binding_residues')
    chain_id: Optional[str] = None         # Optional chain ID for binding_residues
    cutoff: float = 10.0                   # Pocket extraction radius in Å
    target_name: Optional[str] = None      # Defaults to PDB filename stem
    top_fraction: float = 0.02             # Fraction of library to return
    screening_mode: str = 'standard'       # 'standard' | 'large_scale'
    chunk_size: int = 1_000_000            # Large-scale only
    partition: str = 'ga100'               # Large-scale only
    max_parallel: int = 50                 # Large-scale only
    library_is_remote: bool = False        # True when library_path is already on the HPC

    def to_dict(self) -> Dict:
        """Serialize JobParams to a plain dictionary for JSON storage."""
        return {
            'session_id': self.session_id,
            'pdb_path': self.pdb_path,
            'library_path': self.library_path,
            'library_is_remote': self.library_is_remote,
            'binding_site_method': self.binding_site_method,
            'ligand_path': self.ligand_path,
            'residue_name': self.residue_name,
            'center_x': self.center_x,
            'center_y': self.center_y,
            'center_z': self.center_z,
            'binding_residues': self.binding_residues,
            'chain_id': self.chain_id,
            'cutoff': self.cutoff,
            'target_name': self.target_name,
            'top_fraction': self.top_fraction,
            'screening_mode': self.screening_mode,
            'chunk_size': self.chunk_size,
            'partition': self.partition,
            'max_parallel': self.max_parallel,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'JobParams':
        """Deserialize a JobParams instance from a plain dictionary."""
        return cls(
            session_id=data['session_id'],
            pdb_path=data['pdb_path'],
            library_path=data['library_path'],
            library_is_remote=data.get('library_is_remote', False),
            binding_site_method=data['binding_site_method'],
            ligand_path=data.get('ligand_path'),
            residue_name=data.get('residue_name'),
            center_x=data.get('center_x'),
            center_y=data.get('center_y'),
            center_z=data.get('center_z'),
            binding_residues=data.get('binding_residues'),
            chain_id=data.get('chain_id'),
            cutoff=data.get('cutoff', 10.0),
            target_name=data.get('target_name'),
            top_fraction=data.get('top_fraction', 0.02),
            screening_mode=data.get('screening_mode', 'standard'),
            chunk_size=data.get('chunk_size', 1_000_000),
            partition=data.get('partition', 'ga100'),
            max_parallel=data.get('max_parallel', 50),
        )


@dataclass
class JobRecord:
    """Persistent metadata for a submitted SLURM job."""

    job_id: str
    session_id: str
    email: str                             # Owner's email address
    target_name: str
    library_name: str
    screening_mode: str
    status: str
    submitted_at: str
    updated_at: str
    params: Dict
    job_dir: str
    log_path: Optional[str] = None
    results_path: Optional[str] = None
    error_message: Optional[str] = None
    child_job_ids: Optional[List[str]] = None

    def to_dict(self) -> Dict:
        return {
            'job_id': self.job_id,
            'session_id': self.session_id,
            'email': self.email,
            'target_name': self.target_name,
            'library_name': self.library_name,
            'screening_mode': self.screening_mode,
            'status': self.status,
            'submitted_at': self.submitted_at,
            'updated_at': self.updated_at,
            'params': self.params,
            'job_dir': self.job_dir,
            'log_path': self.log_path,
            'results_path': self.results_path,
            'error_message': self.error_message,
            'child_job_ids': self.child_job_ids,
        }

    @classmethod
    def from_dict(cls, data: Dict) -> 'JobRecord':
        return cls(
            job_id=data['job_id'],
            session_id=data['session_id'],
            email=data.get('email', ''),
            target_name=data['target_name'],
            library_name=data['library_name'],
            screening_mode=data['screening_mode'],
            status=data['status'],
            submitted_at=data['submitted_at'],
            updated_at=data['updated_at'],
            params=data['params'],
            job_dir=data['job_dir'],
            log_path=data.get('log_path'),
            results_path=data.get('results_path'),
            error_message=data.get('error_message'),
            child_job_ids=data.get('child_job_ids'),
        )
