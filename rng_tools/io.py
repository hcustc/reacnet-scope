"""Lightweight IO helpers for tabular inputs used by analysis scripts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

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

