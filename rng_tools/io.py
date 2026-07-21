"""Lightweight IO helpers for tabular inputs used by analysis scripts.

The newer ReacNetGenerator ``.lammpstrj.table`` output is a sparse
species-to-species event matrix.  It is deliberately kept separate from the
tidy time-series loaders because the first row/column are SMILES labels rather
than ordinary data columns.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import pandas as pd

REQUIRED_COLUMNS = {
    "left_formulas",
    "right_formulas",
}


def load_table(path: Path, required: Iterable[str] = REQUIRED_COLUMNS) -> pd.DataFrame:
    """Load CSV or Excel with basic validation.

    Raises FileNotFoundError if the path is missing and ValueError if
    required columns are not present.
    """

    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path, encoding="utf-8")
    else:
        df = pd.read_excel(path)

    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}; available: {list(df.columns)}")

    return df


def load_transition_table(path: Path) -> dict[str, Any]:
    """Parse a ReacNetGenerator ``.lammpstrj.table`` transition matrix.

    The format is whitespace-delimited and consists of one header row with
    species SMILES followed by rows of ``species count...``.  Counts are
    returned as integers and the parser accepts a leading blank cell in the
    header as well as either integer or integer-valued float tokens.
    """

    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)

    lines = [line.strip() for line in path.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Transition table is empty: {path}")

    header = lines[0].split()
    if not header:
        raise ValueError(f"Transition table has no species header: {path}")

    labels = list(header)
    n = len(labels)
    rows: list[str] = []
    values: list[list[int]] = []
    for line_no, line in enumerate(lines[1:], 2):
        tokens = line.split()
        if len(tokens) == n:
            # Header-only row labels are uncommon, but accepting this makes
            # the parser compatible with matrix exports that omit the first
            # column and rely on header order.
            row_label = labels[len(rows)] if len(rows) < n else f"row_{len(rows) + 1}"
            count_tokens = tokens
        elif len(tokens) == n + 1:
            row_label = tokens[0]
            count_tokens = tokens[1:]
        else:
            raise ValueError(
                f"Invalid transition-table row {line_no}: expected {n} or {n + 1} fields, got {len(tokens)}"
            )
        try:
            row_values = [int(float(token)) for token in count_tokens]
        except ValueError as exc:
            raise ValueError(f"Invalid count in transition-table row {line_no}") from exc
        rows.append(row_label)
        values.append(row_values)

    if len(values) != n:
        raise ValueError(f"Transition table must have {n} data rows, got {len(values)}")

    # Keep the canonical column labels for indexing.  In valid RNG output the
    # row labels match them; preserving the observed row labels is useful for
    # diagnosing malformed or hand-edited files.
    row_labels = rows
    if row_labels != labels:
        if sorted(row_labels) != sorted(labels):
            raise ValueError("Transition-table row labels do not match the header species labels")
        reorder = [row_labels.index(label) for label in labels]
        values = [values[index] for index in reorder]
        row_labels = labels

    return {
        "path": str(path),
        "labels": labels,
        "matrix": values,
        "n_species": n,
        "n_rows": len(values),
    }
