#!/usr/bin/env python3
"""
Streaming screening: score pre-encoded molecule embedding chunks against a
pocket without loading all embeddings into memory at once.

Usage:
    python utils/screen_streaming.py \
        --emb-dir data/encoded_mol_embs/<hash>/6_folds/ \
        --pocket-lmdb data/targets/TARGET/pocket.lmdb \
        --output results/TARGET_vs_LIB.txt \
        --top-fraction 0.02 \
        --fold-version 6_folds

Reads embedding chunks (npy/h5) from emb-dir, scores each chunk against the
pocket, and keeps a running top-K across all chunks.
"""

import argparse
import glob
import os
import pickle
import sys
import numpy as np
from tqdm import tqdm

# Add project root to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def load_pocket_embeddings(pocket_lmdb, fold_version, model_weights_dir="./data/model_weights"):
    """Encode a pocket using all folds and return embeddings.

    Returns: list of numpy arrays, one per fold, each shape (n_pockets, 128)
    """
    import torch
    import unicore
    from unicore import checkpoint_utils, options, tasks

    # Build minimal args for the task
    parser = options.get_validation_parser()
    parser.add_argument("--mol-path", type=str, default="")
    parser.add_argument("--pocket-path", type=str, default="")
    parser.add_argument("--fold-version", type=str, default="6_folds")
    parser.add_argument("--use-cache", type=str, default="False")
    parser.add_argument("--save-path", type=str, default="")
    parser.add_argument("--cache-dir", type=str, default="")
    options.add_model_args(parser)

    args = options.parse_args_and_arch(parser, input_args=[
        "--user-dir", "./unimol",
        "./dict",
        "--valid-subset", "test",
        "--task", "drugclip",
        "--loss", "in_batch_softmax",
        "--arch", "drugclip",
        "--max-pocket-atoms", "511",
        "--batch-size", "16",
        "--seed", "1",
    ])

    task = tasks.setup_task(args)
    model = task.build_model(args)

    use_cuda = torch.cuda.is_available()
    if use_cuda:
        model.half().cuda()
    model.eval()

    pocket_dataset = task.load_pockets_dataset(pocket_lmdb)
    pocket_loader = torch.utils.data.DataLoader(
        pocket_dataset, batch_size=16, collate_fn=pocket_dataset.collater
    )

    if fold_version == "6_folds":
        ckpts = [f"{model_weights_dir}/6_folds/fold_{i}.pt" for i in range(6)]
    elif fold_version == "8_folds":
        ckpts = [f"{model_weights_dir}/8_folds/fold_{i}.pt" for i in range(8)]
    else:
        raise ValueError(f"Unknown fold version: {fold_version}")

    all_pocket_reps = []
    for ckpt in ckpts:
        state = checkpoint_utils.load_checkpoint_to_cpu(ckpt)
        model.load_state_dict(state["model"], strict=False)

        pocket_reps = []
        for sample in pocket_loader:
            if use_cuda:
                sample = unicore.utils.move_to_cuda(sample)
            dist = sample["net_input"]["pocket_src_distance"]
            et = sample["net_input"]["pocket_src_edge_type"]
            st = sample["net_input"]["pocket_src_tokens"]
            pocket_padding_mask = st.eq(model.pocket_model.padding_idx)
            pocket_x = model.pocket_model.embed_tokens(st)
            n_node = dist.size(-1)
            gbf_feature = model.pocket_model.gbf(dist, et)
            gbf_result = model.pocket_model.gbf_proj(gbf_feature)
            graph_attn_bias = gbf_result.permute(0, 3, 1, 2).contiguous().view(-1, n_node, n_node)
            pocket_outputs = model.pocket_model.encoder(
                pocket_x, padding_mask=pocket_padding_mask, attn_mask=graph_attn_bias
            )
            pocket_encoder_rep = pocket_outputs[0][:, 0, :]
            pocket_emb = model.pocket_project(pocket_encoder_rep)
            pocket_emb = pocket_emb / pocket_emb.norm(dim=-1, keepdim=True)
            pocket_reps.append(pocket_emb.detach().cpu().numpy())

        all_pocket_reps.append(np.concatenate(pocket_reps, axis=0).astype(np.float32))

    return all_pocket_reps


def find_embedding_chunks(emb_dir):
    """Find all embedding chunk files in a directory.

    Returns list of (chunk_path, names_path) tuples sorted by chunk index.
    """
    chunks = []

    # Look for pickle files (fold-based cache format)
    pkl_files = sorted(glob.glob(os.path.join(emb_dir, "*.pkl")))
    if pkl_files:
        return [("pkl", pkl_files)]

    # Look for npy files
    npy_files = sorted(glob.glob(os.path.join(emb_dir, "**", "*.npy"), recursive=True))
    if npy_files:
        return [("npy", f) for f in npy_files]

    # Look for h5 files
    h5_files = sorted(glob.glob(os.path.join(emb_dir, "**", "*.h5"), recursive=True))
    if h5_files:
        return [("h5", f) for f in h5_files]

    return chunks


def stream_score_pkl(pocket_reps_per_fold, pkl_files, top_k, fold_version):
    """Score using pickle-based fold caches (one pkl per fold)."""
    n_folds = len(pocket_reps_per_fold)

    # Load all folds and accumulate scores
    fold_scores = []
    mol_names = None

    for fold_idx, pkl_path in enumerate(pkl_files):
        if fold_idx >= n_folds:
            break
        with open(pkl_path, "rb") as f:
            mol_reps, names = pickle.load(f)
        mol_reps = mol_reps.astype(np.float32)
        pocket_reps = pocket_reps_per_fold[fold_idx]

        scores = pocket_reps @ mol_reps.T  # (n_pockets, n_mols)
        fold_scores.append(scores)

        if mol_names is None:
            mol_names = names

    # Average across folds
    avg_scores = np.mean(fold_scores, axis=0)

    # Z-score normalize if 6_folds
    if fold_version.startswith("6_folds"):
        medians = np.median(avg_scores, axis=1, keepdims=True)
        mads = np.median(np.abs(avg_scores - medians), axis=1, keepdims=True)
        avg_scores = 0.6745 * (avg_scores - medians) / (mads + 1e-6)

    # Max across pockets
    max_scores = np.max(avg_scores, axis=0)

    # Get top-K
    if top_k >= len(max_scores):
        top_indices = np.argsort(max_scores)[::-1]
    else:
        top_indices = np.argpartition(max_scores, -top_k)[-top_k:]
        top_indices = top_indices[np.argsort(max_scores[top_indices])[::-1]]

    return [(mol_names[i], max_scores[i]) for i in top_indices]


def stream_score_npy(pocket_reps_per_fold, npy_files, top_k, fold_version):
    """Score using npy embedding files, streaming one chunk at a time."""
    import h5py

    n_folds = len(pocket_reps_per_fold)
    all_results = []  # list of (name_or_index, score)
    global_offset = 0

    for chunk_file in tqdm(npy_files, desc="Scoring chunks"):
        # Load chunk embeddings — shape depends on format
        if chunk_file.endswith(".h5"):
            with h5py.File(chunk_file, "r") as hf:
                # H5 stores all folds interleaved: (n_mols, 768) with 128 per fold
                full_embs = hf["mol_reps"][:]
                chunk_size = full_embs.shape[0]

                fold_scores = []
                for fold_idx in range(n_folds):
                    mol_reps = full_embs[:, fold_idx * 128:(fold_idx + 1) * 128].astype(np.float32)
                    scores = pocket_reps_per_fold[fold_idx] @ mol_reps.T
                    fold_scores.append(scores)
        else:
            # NPY: shape (n_mols, n_folds, 128) or (n_mols, 128)
            embs = np.load(chunk_file)
            chunk_size = embs.shape[0]

            if embs.ndim == 3:
                fold_scores = []
                for fold_idx in range(min(n_folds, embs.shape[1])):
                    mol_reps = embs[:, fold_idx, :].astype(np.float32)
                    scores = pocket_reps_per_fold[fold_idx] @ mol_reps.T
                    fold_scores.append(scores)
            else:
                # Single fold
                fold_scores = [pocket_reps_per_fold[0] @ embs.astype(np.float32).T]

        avg_scores = np.mean(fold_scores, axis=0)

        if fold_version.startswith("6_folds"):
            medians = np.median(avg_scores, axis=1, keepdims=True)
            mads = np.median(np.abs(avg_scores - medians), axis=1, keepdims=True)
            avg_scores = 0.6745 * (avg_scores - medians) / (mads + 1e-6)

        max_scores = np.max(avg_scores, axis=0)

        # Keep top-K from this chunk
        chunk_top_k = min(top_k, len(max_scores))
        if chunk_top_k < len(max_scores):
            top_idx = np.argpartition(max_scores, -chunk_top_k)[-chunk_top_k:]
        else:
            top_idx = np.arange(len(max_scores))

        for idx in top_idx:
            all_results.append((global_offset + idx, max_scores[idx]))

        global_offset += chunk_size

    # Final top-K across all chunks
    all_results.sort(key=lambda x: x[1], reverse=True)
    return all_results[:top_k]


def main():
    parser = argparse.ArgumentParser(
        description="Stream-score pre-encoded molecule embeddings against a pocket"
    )
    parser.add_argument("--emb-dir", required=True,
                        help="Directory containing encoded molecule embeddings")
    parser.add_argument("--pocket-lmdb", required=True,
                        help="Path to pocket LMDB file")
    parser.add_argument("--output", required=True,
                        help="Output results file")
    parser.add_argument("--top-fraction", type=float, default=0.02,
                        help="Fraction of library to return as hits (default: 0.02)")
    parser.add_argument("--fold-version", type=str, default="6_folds",
                        help="Model fold version (default: 6_folds)")
    parser.add_argument("--total-mols", type=int, default=None,
                        help="Total molecules in library (for top-K calculation)")
    args = parser.parse_args()

    print("Encoding pocket...")
    pocket_reps = load_pocket_embeddings(args.pocket_lmdb, args.fold_version)
    print(f"  {len(pocket_reps)} folds, pocket shape: {pocket_reps[0].shape}")

    print("Finding embedding chunks...")
    chunks = find_embedding_chunks(args.emb_dir)
    if not chunks:
        print(f"Error: No embedding files found in {args.emb_dir}")
        sys.exit(1)

    # Determine top-K
    if args.total_mols:
        total = args.total_mols
    else:
        # Estimate from first chunk
        total = 10_000_000  # conservative default
    top_k = max(1, int(total * args.top_fraction))
    print(f"  Keeping top {top_k:,} hits ({args.top_fraction * 100:.1f}%)")

    # Score
    fmt, data = chunks[0]
    if fmt == "pkl":
        results = stream_score_pkl(pocket_reps, data, top_k, args.fold_version)
    else:
        all_files = [d if isinstance(d, str) else d for _, d in chunks]
        results = stream_score_npy(pocket_reps, all_files, top_k, args.fold_version)

    # Write results
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as f:
        for name, score in results:
            f.write(f"{name},{score}\n")

    print(f"\nWrote {len(results):,} hits to {args.output}")
    if results:
        print(f"Top score: {results[0][1]:.4f}")


if __name__ == "__main__":
    main()
