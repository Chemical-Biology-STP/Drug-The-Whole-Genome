"""
Results parsing and pagination for the DrugCLIP web application.

Results are downloaded from the HPC by the JobMonitor when a job completes.
The results_path stored on the JobRecord points to the local cached copy.
"""

import math


def parse_results(results_path: str) -> list[tuple[int, str, float]]:
    """Parse a results file and return ranked results sorted by descending score.

    The results file is expected to contain lines in CSV format: SMILES,score
    (no header row). Lines are sorted by descending score and assigned
    sequential ranks starting at 1.

    Args:
        results_path: Path to the results file.

    Returns:
        A list of (rank, smiles, score) tuples sorted by descending score.
    """
    entries: list[tuple[str, float]] = []

    with open(results_path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # Split on the last comma to handle SMILES that may contain commas
            last_comma = line.rfind(",")
            if last_comma == -1:
                continue
            smiles = line[:last_comma]
            score_str = line[last_comma + 1:]
            try:
                score = float(score_str)
            except ValueError:
                continue
            entries.append((smiles, score))

    # Sort by descending score
    entries.sort(key=lambda x: x[1], reverse=True)

    # Assign sequential ranks starting at 1
    return [(rank, smiles, score) for rank, (smiles, score) in enumerate(entries, start=1)]


def paginate(items: list, page: int, per_page: int) -> dict:
    """Paginate a list of items.

    Args:
        items: The full list of items to paginate.
        page: The 1-based page number to retrieve.
        per_page: The number of items per page (must be > 0).

    Returns:
        A dict with keys:
            - items: The slice of items for the requested page.
            - total_pages: Total number of pages (ceil(N/P)).
            - current_page: The current page number.
            - has_prev: Whether there is a previous page.
            - has_next: Whether there is a next page.
            - total_items: Total number of items.
    """
    total_items = len(items)
    total_pages = math.ceil(total_items / per_page) if total_items > 0 else 0

    # Clamp page to valid range
    page = max(1, min(page, total_pages)) if total_pages > 0 else 1

    start = (page - 1) * per_page
    end = start + per_page
    page_items = items[start:end]

    return {
        "items": page_items,
        "total_pages": total_pages,
        "current_page": page,
        "has_prev": page > 1,
        "has_next": page < total_pages,
        "total_items": total_items,
    }
