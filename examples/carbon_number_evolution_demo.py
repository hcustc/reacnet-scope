"""Generate a demo carbon-number evolution plot from example data."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rng_tools import plot_carbon_number_evolution


def main() -> None:
    root = Path(__file__).resolve().parent
    input_path = root / "carbon_number_evolution_minimal.csv"
    artifact_dir = root / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    output_png = artifact_dir / "carbon_number_evolution_demo.png"
    output_json = artifact_dir / "carbon_number_evolution_demo_summary.json"
    output_csv = artifact_dir / "carbon_number_evolution_demo_plot_data.csv"

    data = pd.read_csv(input_path)
    fig, _, summary, plot_data = plot_carbon_number_evolution(
        data=data,
        time_col="time",
        species_col="species",
        count_col="count",
        system_col="system",
        replicate_col="replicate",
        parent_carbon_number=24,
        mode="exact",
        layout="subplots",
        layout_regions=[
            ("Small fragments", 1, 4),
            ("Intermediate fragments", 5, 15),
            ("Parent neighborhood", 16, 30),
            ("Growth region", 31, None),
        ],
        system_mode="facet",
        legend_mode="detailed",
        smoothing={"method": "rolling", "window": 2},
        theme="light",
        figsize=(12, 7),
        output_path=output_png,
    )

    plot_data.to_csv(output_csv, index=False)
    output_json.write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    plt.close(fig)

    print(f"[OK] plot: {output_png}")
    print(f"[OK] summary: {output_json}")
    print(f"[OK] plot_data: {output_csv}")


if __name__ == "__main__":
    main()
