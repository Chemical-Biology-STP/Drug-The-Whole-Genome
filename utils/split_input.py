#!/usr/bin/env python3
"""
Split a large SDF or SMILES file into chunks for parallel processing.

Usage:
    python utils/split_input.py --input library.sdf --output-dir data/chunks/ --chunk-size 1000000
    python utils/split_input.py --input library.smi --output-dir data/chunks/ --chunk-size 1000000

For SDF files, splits on $$$$ boundaries.
For SMILES files, splits on line boundaries.
"""

import argparse
import os


def count_sdf_molecules(path):
    """Count molecules in an SDF file by counting $$$$ delimiters."""
    count = 0
    with open(path) as f:
        for line in f:
            if line.strip() == "$$$$":
                count += 1
    return count


def count_smi_lines(path):
    """Count non-empty, non-header lines in a SMILES file."""
    count = 0
    with open(path) as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Skip header if first token doesn't look like SMILES
            if i == 0 and not any(c in line.split()[0] for c in "()=@[]#"):
                continue
            count += 1
    return count


def split_sdf(input_path, output_dir, chunk_size):
    """Split an SDF file into chunks of chunk_size molecules each."""
    os.makedirs(output_dir, exist_ok=True)

    chunk_idx = 0
    mol_count = 0
    current_lines = []
    chunk_paths = []

    with open(input_path) as f:
        for line in f:
            current_lines.append(line)
            if line.strip() == "$$$$":
                mol_count += 1
                if mol_count >= chunk_size:
                    chunk_path = os.path.join(output_dir, f"chunk_{chunk_idx:06d}.sdf")
                    with open(chunk_path, "w") as out:
                        out.writelines(current_lines)
                    chunk_paths.append(chunk_path)
                    print(f"  Wrote {mol_count} molecules to {chunk_path}")
                    chunk_idx += 1
                    mol_count = 0
                    current_lines = []

    # Write remaining molecules
    if current_lines:
        chunk_path = os.path.join(output_dir, f"chunk_{chunk_idx:06d}.sdf")
        with open(chunk_path, "w") as out:
            out.writelines(current_lines)
        chunk_paths.append(chunk_path)
        print(f"  Wrote {mol_count} molecules to {chunk_path}")

    return chunk_paths


def split_smi(input_path, output_dir, chunk_size):
    """Split a SMILES file into chunks of chunk_size lines each."""
    os.makedirs(output_dir, exist_ok=True)

    chunk_idx = 0
    line_count = 0
    current_lines = []
    chunk_paths = []
    header = None

    with open(input_path) as f:
        for i, line in enumerate(f):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Detect header
            if i == 0 and not any(c in stripped.split()[0] for c in "()=@[]#"):
                header = line
                continue

            current_lines.append(line)
            line_count += 1

            if line_count >= chunk_size:
                chunk_path = os.path.join(output_dir, f"chunk_{chunk_idx:06d}.smi")
                with open(chunk_path, "w") as out:
                    if header:
                        out.write(header)
                    out.writelines(current_lines)
                chunk_paths.append(chunk_path)
                print(f"  Wrote {line_count} molecules to {chunk_path}")
                chunk_idx += 1
                line_count = 0
                current_lines = []

    if current_lines:
        chunk_path = os.path.join(output_dir, f"chunk_{chunk_idx:06d}.smi")
        with open(chunk_path, "w") as out:
            if header:
                out.write(header)
            out.writelines(current_lines)
        chunk_paths.append(chunk_path)
        print(f"  Wrote {line_count} molecules to {chunk_path}")

    return chunk_paths


def main():
    parser = argparse.ArgumentParser(description="Split a compound library into chunks")
    parser.add_argument("--input", required=True, help="Input SDF or SMILES file")
    parser.add_argument("--output-dir", required=True, help="Output directory for chunks")
    parser.add_argument("--chunk-size", type=int, default=1_000_000,
                        help="Molecules per chunk (default: 1,000,000)")
    args = parser.parse_args()

    ext = os.path.splitext(args.input)[1].lower()
    sdf_exts = {".sdf", ".sd", ".mol"}
    smi_exts = {".smi", ".smiles", ".csv", ".tsv", ".txt"}

    if ext in sdf_exts:
        total = count_sdf_molecules(args.input)
        print(f"Input: {args.input} ({total:,} molecules)")
        n_chunks = (total + args.chunk_size - 1) // args.chunk_size
        print(f"Splitting into ~{n_chunks} chunks of {args.chunk_size:,} each")
        paths = split_sdf(args.input, args.output_dir, args.chunk_size)
    elif ext in smi_exts:
        total = count_smi_lines(args.input)
        print(f"Input: {args.input} ({total:,} molecules)")
        n_chunks = (total + args.chunk_size - 1) // args.chunk_size
        print(f"Splitting into ~{n_chunks} chunks of {args.chunk_size:,} each")
        paths = split_smi(args.input, args.output_dir, args.chunk_size)
    else:
        raise ValueError(f"Unsupported extension: {ext}")

    # Write manifest
    manifest = os.path.join(args.output_dir, "manifest.txt")
    with open(manifest, "w") as f:
        for p in paths:
            f.write(p + "\n")
    print(f"\nManifest: {manifest} ({len(paths)} chunks)")


if __name__ == "__main__":
    main()
