"""Carbon-number evolution plotting for ReacNetGenerator species tables.

This module aggregates time-resolved species counts by the number of carbon
atoms in each formula and visualizes their evolution over time. The resulting
plots are useful for tracking parent-molecule depletion, fragment generation,
molecular growth, and oxidation trends in reactive MD simulations.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Mapping, Sequence

import pandas as pd

from .formula import parse_formula

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure


FormulaParser = Callable[[str], Mapping[str, int]]
SpeciesResolver = Callable[[str], str]
ProgressCallback = Callable[[Mapping[str, Any]], None]
RangeSpec = (
    tuple[int | None, int | None]
    | tuple[str, int | None, int | None]
    | Mapping[str, Any]
)
TableLike = (
    pd.DataFrame
    | str
    | Path
    | Sequence[Mapping[str, Any]]
    | Mapping[str, Sequence[Any]]
)
SmoothingSpec = str | Mapping[str, Any] | None

_STANDARD_FORMULA_PATTERN = re.compile(r"([A-Z][a-z]?)(\d*)")
_SPECIES_TIMESTEP_RE = re.compile(r"^Timestep\s+(\d+):(.*)$")
_SERIES_COL = "__series_key"
_SYSTEM_VALUE_COL = "__system_value"
_REGION_VALUE_COL = "__region_value"


@dataclass(frozen=True)
class CarbonRange:
    """Inclusive carbon-number range used for binning or panel assignment."""

    label: str
    start: int | None
    end: int | None

    def contains(self, carbon_number: int) -> bool:
        """Return True if ``carbon_number`` falls in the range."""

        if self.start is not None and carbon_number < self.start:
            return False
        if self.end is not None and carbon_number > self.end:
            return False
        return True

    def fully_contains(self, start: int | None, end: int | None) -> bool:
        """Return True if another range is fully contained in this range."""

        if start is None or end is None:
            return False
        return self.contains(start) and self.contains(end)

    def overlaps(self, start: int | None, end: int | None) -> bool:
        """Return True if another range overlaps this range."""

        lo = start if start is not None else self.start
        hi = end if end is not None else self.end
        if lo is None:
            lo = -math.inf
        if hi is None:
            hi = math.inf
        this_lo = self.start if self.start is not None else -math.inf
        this_hi = self.end if self.end is not None else math.inf
        return not (hi < this_lo or lo > this_hi)


def parse_formula_to_atom_counts(
    species: str,
    parser: FormulaParser | None = None,
) -> dict[str, int]:
    """Parse a molecular formula or species label into atom counts.

    Parameters
    ----------
    species:
        Molecular formula-like string, for example ``"C24H12"`` or ``"CO2"``.
    parser:
        Optional custom parser. It should return a mapping from element symbol
        to non-negative integer atom counts.

    Returns
    -------
    dict[str, int]
        Element count dictionary.

    Raises
    ------
    ValueError
        If the formula is empty, invalid, or cannot be parsed.
    TypeError
        If a custom parser returns a non-mapping object.
    """

    formula = str(species).strip()
    if not formula:
        raise ValueError("Encountered an empty species label; expected a molecular formula.")

    raw_counts = parser(formula) if parser is not None else _parse_standard_formula(formula)
    return _validate_atom_counts(raw_counts, formula)


def aggregate_counts_by_carbon_number(
    data: TableLike,
    time_col: str = "time",
    species_col: str = "species",
    count_col: str = "count",
    system_col: str | None = None,
    replicate_col: str | None = None,
    formula_parser: FormulaParser | None = None,
    complete_missing: bool = True,
) -> pd.DataFrame:
    """Aggregate molecule counts by time and carbon number.

    Parameters
    ----------
    data:
        Tidy species table, CSV/Excel path, or DataFrame-like object with at
        least ``time``, ``species``, and ``count`` columns.
    time_col, species_col, count_col:
        Column names describing time, species label, and molecule count.
    system_col, replicate_col:
        Optional grouping columns. When present, aggregation is performed within
        each system and/or replicate independently.
    formula_parser:
        Optional custom parser for non-standard species naming conventions.
    complete_missing:
        If True, missing ``(time, carbon_number)`` combinations are filled with
        zero within each system/replicate group.

    Returns
    -------
    pandas.DataFrame
        Aggregated tidy table with columns ``time``, ``carbon_number``, and
        ``count`` plus optional system/replicate columns.
    """

    source = _prepare_source_table(
        data=data,
        time_col=time_col,
        species_col=species_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        formula_parser=formula_parser,
    )
    aggregated = _aggregate_enriched_table(
        source=source,
        time_col=time_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        complete_missing=complete_missing,
    )
    order_cols = [col for col in (system_col, replicate_col, time_col) if col] + ["carbon_number"]
    return aggregated.sort_values(order_cols).reset_index(drop=True)


def species_file_to_tidy_table(
    species_file: str | Path,
    *,
    time_axis: Literal["step", "ps", "ns"] = "ps",
    timestep_ps: float = 0.0001,
    species_resolver: SpeciesResolver | None = None,
    system: str | None = None,
    replicate: str | int | None = None,
    time_col: str = "time",
    species_col: str = "species",
    count_col: str = "count",
    progress_callback: ProgressCallback | None = None,
) -> pd.DataFrame:
    """Parse an RNG ``.species`` file into a tidy table.

    Parameters
    ----------
    species_file:
        Path to the ReacNetGenerator ``.species`` file.
    time_axis:
        Output time unit. ``"step"`` keeps integer timesteps, ``"ps"`` converts
        to picoseconds, and ``"ns"`` converts to nanoseconds.
    timestep_ps:
        Per-step duration in picoseconds used when ``time_axis`` is ``"ps"`` or
        ``"ns"``.
    species_resolver:
        Optional function that maps each token in the species file to a tidy
        species label. Use this to convert SMILES to formulas.
    system, replicate:
        Optional constant columns appended to every row.
    time_col, species_col, count_col:
        Output column names.

    Returns
    -------
    pandas.DataFrame
        Tidy species table compatible with ``plot_carbon_number_evolution``.
    """

    path = Path(species_file).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    if time_axis not in {"step", "ps", "ns"}:
        raise ValueError("time_axis must be one of {'step', 'ps', 'ns'}.")
    if timestep_ps <= 0:
        raise ValueError("timestep_ps must be positive.")

    resolver = species_resolver or (lambda species: species)
    resolved_cache: dict[str, str] = {}
    rows: list[dict[str, Any]] = []
    parsed_timesteps = 0
    examples: list[str] = []
    file_size = max(path.stat().st_size, 1)
    bytes_read = 0
    next_progress_mark = 0.0
    last_emit = 0.0

    _emit_progress(
        progress_callback,
        phase="reading_species",
        progress=0.0,
        message=f"Reading {path.name}",
        timesteps=0,
        rows=0,
    )

    with path.open(encoding="utf-8") as fh:
        for raw_line in fh:
            bytes_read += len(raw_line)
            parsed = _parse_species_timestep_line(raw_line)
            if parsed is None:
                continue
            timestep, pairs = parsed
            parsed_timesteps += 1
            mapped_counts: dict[str, int] = {}
            for raw_species, raw_count in pairs:
                if raw_species not in resolved_cache:
                    try:
                        mapped = str(resolver(raw_species)).strip()
                    except Exception as exc:
                        if len(examples) < 5:
                            examples.append(f"{raw_species!r}: {exc}")
                        continue
                    if not mapped:
                        if len(examples) < 5:
                            examples.append(f"{raw_species!r}: resolved to an empty species label")
                        continue
                    resolved_cache[raw_species] = mapped
                mapped_species = resolved_cache[raw_species]
                mapped_counts[mapped_species] = mapped_counts.get(mapped_species, 0) + int(raw_count)

            time_value: int | float
            if time_axis == "step":
                time_value = int(timestep)
            elif time_axis == "ns":
                time_value = float(timestep) * float(timestep_ps) / 1000.0
            else:
                time_value = float(timestep) * float(timestep_ps)

            for mapped_species, count in mapped_counts.items():
                row = {
                    time_col: time_value,
                    species_col: mapped_species,
                    count_col: int(count),
                }
                if system is not None:
                    row["system"] = system
                if replicate is not None:
                    row["replicate"] = replicate
                row["frame"] = int(timestep)
                rows.append(row)

            fraction = min(bytes_read / file_size, 1.0)
            now = time.monotonic()
            if fraction >= next_progress_mark or (now - last_emit) >= 1.0:
                _emit_progress(
                    progress_callback,
                    phase="reading_species",
                    progress=fraction,
                    message=f"Reading species file: {fraction * 100:.1f}%",
                    timesteps=parsed_timesteps,
                    rows=len(rows),
                    frame=timestep,
                )
                next_progress_mark = min(fraction + 0.01, 1.0)
                last_emit = now

    if parsed_timesteps == 0:
        raise ValueError(f"No valid timestep rows found in species file: {path}")
    if examples:
        joined = "; ".join(examples)
        raise ValueError(f"Failed to resolve some species labels from {path.name}. Examples: {joined}")
    if not rows:
        raise ValueError(f"No species rows were parsed from {path}")

    table = pd.DataFrame.from_records(rows)
    order_cols = [col for col in (time_col, "frame", species_col) if col in table.columns]
    table = table.sort_values(order_cols).reset_index(drop=True)
    _emit_progress(
        progress_callback,
        phase="reading_species",
        progress=1.0,
        message=f"Loaded {parsed_timesteps} timesteps from {path.name}",
        timesteps=parsed_timesteps,
        rows=len(table),
    )
    return table


def parse_carbon_range_specs(text: str) -> list[tuple[str, int | None, int | None]]:
    """Parse carbon-range text such as ``\"C1-C4; Parent: C16-C30; C31+\"``."""

    if not text or not text.strip():
        return []

    specs: list[tuple[str, int | None, int | None]] = []
    tokens = [token.strip() for token in re.split(r"[;\n,]+", text) if token.strip()]
    for token in tokens:
        if ":" in token:
            label, range_text = token.split(":", 1)
            label = label.strip()
            range_text = range_text.strip()
        else:
            label = ""
            range_text = token.strip()

        start, end = _parse_carbon_range_token(range_text)
        if not label:
            label = _format_range_label(start, end)
        specs.append((label, start, end))
    return specs


def summarize_carbon_evolution(
    aggregated_data: pd.DataFrame,
    time_col: str = "time",
    count_col: str = "count",
    carbon_number_col: str = "carbon_number",
    system_col: str | None = None,
    parent_carbon_number: int | None = None,
    highlight_small: tuple[int, int] = (1, 4),
    highlight_large: int = 30,
    parent_decay_threshold: float = 0.05,
    species_data: pd.DataFrame | None = None,
    oxidized_count_col: str = "__oxidized_product_count",
    oxidized_carbon_col: str = "__oxidized_product_carbon_count",
) -> dict[str, Any]:
    """Summarize parent depletion, fragment growth, and oxidation indicators.

    Parameters
    ----------
    aggregated_data:
        Carbon-number aggregated table produced by
        :func:`aggregate_counts_by_carbon_number`.
    time_col, count_col, carbon_number_col:
        Column names in ``aggregated_data``.
    system_col:
        Optional system/facet column. When present, both overall and per-system
        summaries are returned.
    parent_carbon_number:
        Optional known parent carbon number. If omitted, the function infers it
        from the earliest time slice by taking the most abundant carbon class.
    highlight_small:
        Inclusive small-fragment carbon-number range.
    highlight_large:
        Carbon-number threshold treated as the large-growth regime.
    parent_decay_threshold:
        Relative drop threshold used to define the onset of parent decay.
    species_data:
        Optional species-level table enriched by :func:`plot_carbon_number_evolution`.
        When present, oxidation metrics are also reported.
    oxidized_count_col, oxidized_carbon_col:
        Internal column names used when oxidation metrics are available.

    Returns
    -------
    dict[str, Any]
        JSON-serializable summary dictionary.
    """

    required = {time_col, carbon_number_col, count_col}
    missing = [col for col in required if col not in aggregated_data.columns]
    if missing:
        raise ValueError(
            f"Aggregated data is missing required columns: {missing}; "
            f"available: {list(aggregated_data.columns)}"
        )

    if aggregated_data.empty:
        raise ValueError("Aggregated data is empty; cannot summarize carbon evolution.")

    base = aggregated_data.copy()
    base[count_col] = pd.to_numeric(base[count_col], errors="raise")

    if system_col and system_col in base.columns and base[system_col].nunique(dropna=False) > 1:
        overall = _summarize_single_group(
            group_df=base,
            time_col=time_col,
            count_col=count_col,
            carbon_number_col=carbon_number_col,
            parent_carbon_number=parent_carbon_number,
            highlight_small=highlight_small,
            highlight_large=highlight_large,
            parent_decay_threshold=parent_decay_threshold,
            species_data=species_data,
            species_filters=None,
            oxidized_count_col=oxidized_count_col,
            oxidized_carbon_col=oxidized_carbon_col,
        )
        by_system: dict[str, Any] = {}
        for system_value, subset in base.groupby(system_col, dropna=False, sort=False):
            filters = {system_col: system_value}
            system_key = str(system_value)
            by_system[system_key] = _summarize_single_group(
                group_df=subset,
                time_col=time_col,
                count_col=count_col,
                carbon_number_col=carbon_number_col,
                parent_carbon_number=parent_carbon_number,
                highlight_small=highlight_small,
                highlight_large=highlight_large,
                parent_decay_threshold=parent_decay_threshold,
                species_data=species_data,
                species_filters=filters,
                oxidized_count_col=oxidized_count_col,
                oxidized_carbon_col=oxidized_carbon_col,
            )
        return {
            "group_by": system_col,
            "overall": overall,
            "by_system": by_system,
        }

    return _summarize_single_group(
        group_df=base,
        time_col=time_col,
        count_col=count_col,
        carbon_number_col=carbon_number_col,
        parent_carbon_number=parent_carbon_number,
        highlight_small=highlight_small,
        highlight_large=highlight_large,
        parent_decay_threshold=parent_decay_threshold,
        species_data=species_data,
        species_filters=None,
        oxidized_count_col=oxidized_count_col,
        oxidized_carbon_col=oxidized_carbon_col,
    )


def plot_carbon_number_evolution(
    data: TableLike,
    time_col: str = "time",
    species_col: str = "species",
    count_col: str = "count",
    system_col: str | None = None,
    replicate_col: str | None = None,
    formula_parser: FormulaParser | None = None,
    carbon_bins: Sequence[RangeSpec | int] | None = None,
    display_ranges: Sequence[RangeSpec] | str | None = None,
    merge_ranges: Sequence[RangeSpec] | str | None = None,
    mode: Literal["exact", "binned", "topk"] = "exact",
    top_k: int = 12,
    max_exact_lines: int = 24,
    parent_carbon_number: int | None = None,
    highlight_small: tuple[int, int] = (1, 4),
    highlight_large: int = 30,
    smoothing: SmoothingSpec = None,
    layout: Literal["single", "subplots"] = "single",
    layout_regions: Sequence[RangeSpec] | None = None,
    system_mode: Literal["facet", "overlay"] | None = None,
    legend_mode: Literal["detailed", "compact"] = "compact",
    palette: str = "viridis",
    theme: Literal["light", "dark"] = "light",
    figsize: tuple[float, float] = (10, 6),
    subplot_max_columns: int | None = None,
    max_points_per_series: int | None = None,
    parent_decay_threshold: float = 0.05,
    return_summary: bool = True,
    show_uncertainty: bool = True,
    oxidized_product_selector: Callable[[Mapping[str, int]], bool] | None = None,
    output_path: str | Path | None = None,
    ax: Axes | None = None,
) -> tuple[Figure, Any, dict[str, Any] | None, pd.DataFrame]:
    """Plot time evolution of molecule counts aggregated by carbon number.

    Parameters
    ----------
    data:
        Tidy species table, CSV/Excel path, or DataFrame-like object with at
        least ``time``, ``species``, and ``count`` columns.
    time_col, species_col, count_col:
        Column names for time, species, and molecule count.
    system_col, replicate_col:
        Optional columns for system/facet identity and replicate identity.
    formula_parser:
        Optional custom parser for non-standard ReacNetGenerator species labels.
    carbon_bins:
        Carbon-number bin specification for ``mode="binned"``. Supported forms:
        ``[(1, 4), (5, 15), ("C16-C30", 16, 30), (31, None)]`` or monotonic
        integer boundaries such as ``[0, 4, 15, 30]``.
    display_ranges:
        Optional carbon ranges to keep on the plot. Curves outside these ranges
        are removed after mode transformation. Supports the same range syntax as
        ``carbon_bins`` and also accepts text such as ``"C1-C4; C24; C30+"``.
    merge_ranges:
        Optional carbon ranges to merge into single curves after mode
        transformation. Example: ``"small:1-4; parent:24; growth:30+"``.
    mode:
        ``"exact"`` plots one line per carbon number, ``"binned"`` aggregates by
        ranges, and ``"topk"`` keeps the most abundant carbon classes while
        merging the remainder into ``others``.
    top_k:
        Number of carbon classes retained in ``mode="topk"``.
    max_exact_lines:
        Automatic crowding threshold. If ``mode="exact"`` exceeds this number
        of carbon classes, the function switches to ``"binned"`` when
        ``carbon_bins`` is provided, otherwise to ``"topk"``.
    parent_carbon_number:
        Optional known parent carbon number. If omitted, the dominant carbon
        number at the earliest time is used.
    highlight_small:
        Inclusive small-fragment range used for coloring and summary metrics.
    highlight_large:
        Inclusive lower bound of the large-growth regime.
    smoothing:
        Optional smoothing specification. Supported values are ``"rolling"``,
        ``"savgol"``, or a mapping such as ``{"method": "rolling", "window": 5}``.
    layout:
        ``"single"`` for one panel or ``"subplots"`` to split carbon-number
        ranges across multiple panels.
    layout_regions:
        Range specification for ``layout="subplots"``.
    system_mode:
        When multiple systems are present, use ``"facet"`` for separate rows or
        ``"overlay"`` to plot the same carbon classes on shared axes with
        distinct line styles.
    legend_mode:
        ``"detailed"`` lists every visible series. ``"compact"`` keeps a
        smaller discrete legend without falling back to a colorbar.
    palette:
        Base matplotlib colormap name for the middle carbon-number regime.
    theme:
        ``"light"`` or ``"dark"``.
    figsize:
        Base figure size in inches.
    subplot_max_columns:
        Maximum number of region panels per row when ``layout="subplots"``.
        ``None`` selects an automatic layout tuned for readability.
    max_points_per_series:
        Optional display downsampling cap. When provided, each plotted series is
        reduced to at most this many points before rendering and before the
        returned ``plot_data`` is emitted.
    parent_decay_threshold:
        Relative drop threshold used by the summary.
    return_summary:
        If True, return a summary dictionary alongside the figure.
    show_uncertainty:
        If True and replicate data is present, draw mean ± standard deviation.
    oxidized_product_selector:
        Optional predicate operating on atom-count mappings. The default marks
        carbon-oxygen-only species such as CO and CO2 as oxidation products.
    output_path:
        Optional path used with ``Figure.savefig``. PNG/SVG/PDF all work through
        matplotlib based on the file suffix.
    ax:
        Existing matplotlib axes. This is only supported for a single-panel
        plot without system faceting.

    Returns
    -------
    tuple
        ``(fig, ax, summary, plot_data)`` where ``plot_data`` is the
        aggregated tidy table used for plotting.
    """

    try:
        import matplotlib.pyplot as plt
        from matplotlib.lines import Line2D
    except ImportError as exc:
        raise ImportError(
            "matplotlib is required for plot_carbon_number_evolution. "
            "Install the 'plot' extra or add matplotlib to your environment."
        ) from exc

    if mode not in {"exact", "binned", "topk"}:
        raise ValueError("mode must be one of {'exact', 'binned', 'topk'}.")
    if layout not in {"single", "subplots"}:
        raise ValueError("layout must be either 'single' or 'subplots'.")
    if legend_mode not in {"detailed", "compact"}:
        raise ValueError("legend_mode must be either 'detailed' or 'compact'.")
    if theme not in {"light", "dark"}:
        raise ValueError("theme must be either 'light' or 'dark'.")
    if top_k <= 0:
        raise ValueError("top_k must be a positive integer.")
    if max_exact_lines <= 0:
        raise ValueError("max_exact_lines must be a positive integer.")
    if subplot_max_columns is not None and subplot_max_columns <= 0:
        raise ValueError("subplot_max_columns must be positive when provided.")
    resolved_display_ranges = _coerce_optional_range_specs(display_ranges)
    resolved_merge_ranges = _coerce_optional_range_specs(merge_ranges)

    source = _prepare_source_table(
        data=data,
        time_col=time_col,
        species_col=species_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        formula_parser=formula_parser,
        oxidized_product_selector=oxidized_product_selector,
    )
    aggregated = _aggregate_enriched_table(
        source=source,
        time_col=time_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        complete_missing=True,
    )
    summary_stats = _collapse_replicates(
        aggregated=aggregated,
        time_col=time_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
    )
    overall_parent = parent_carbon_number or _infer_parent_carbon_number(
        summary_stats,
        time_col=time_col,
        carbon_number_col="carbon_number",
        count_col="mean_count",
    )

    plot_source = _apply_smoothing(
        aggregated=aggregated,
        time_col=time_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
        smoothing=smoothing,
    )
    plot_stats = _collapse_replicates(
        aggregated=plot_source,
        time_col=time_col,
        count_col=count_col,
        system_col=system_col,
        replicate_col=replicate_col,
    )

    effective_mode = _resolve_effective_mode(
        plot_stats=plot_stats,
        requested_mode=mode,
        carbon_bins=carbon_bins,
        max_exact_lines=max_exact_lines,
    )
    plot_data = _transform_plot_groups(
        stats=plot_stats,
        time_col=time_col,
        system_col=system_col,
        mode=effective_mode,
        carbon_bins=carbon_bins,
        top_k=top_k,
        parent_carbon_number=overall_parent,
    )
    plot_data = _apply_display_filter_and_merges(
        plot_data=plot_data,
        time_col=time_col,
        system_col=system_col,
        parent_carbon_number=overall_parent,
        display_ranges=resolved_display_ranges,
        merge_ranges=resolved_merge_ranges,
    )

    regions = _resolve_layout_regions(
        layout=layout,
        layout_regions=layout_regions,
        highlight_small=highlight_small,
        highlight_large=highlight_large,
        max_carbon_number=int(plot_data["series_end_carbon"].max()),
    )
    plot_data["plot_region"] = plot_data.apply(
        lambda row: _assign_region(
            regions=regions,
            start=row["series_start_carbon"],
            end=row["series_end_carbon"],
            default_label=regions[0].label,
        ),
        axis=1,
    )
    plot_data[_REGION_VALUE_COL] = plot_data["plot_region"]
    if max_points_per_series is not None:
        if max_points_per_series <= 1:
            raise ValueError("max_points_per_series must be greater than 1 when provided.")
        plot_data = _downsample_plot_series_data(
            plot_data=plot_data,
            time_col=time_col,
            max_points_per_series=max_points_per_series,
        )
    active_region_labels = set(plot_data["plot_region"].dropna())
    regions = [region for region in regions if region.label in active_region_labels]
    if not regions:
        regions = [CarbonRange(label="All carbon numbers", start=None, end=None)]

    systems = [None]
    if system_col and system_col in plot_data.columns:
        systems = list(plot_data[system_col].drop_duplicates())
    if len(systems) > 1:
        system_mode = system_mode or "facet"
    else:
        system_mode = None

    panel_systems = systems if system_mode == "facet" else [None]
    region_rows, region_cols = _resolve_subplot_grid(
        region_count=len(regions),
        layout=layout,
        subplot_max_columns=subplot_max_columns,
    )
    grid_rows = region_rows * len(panel_systems)
    grid_cols = region_cols
    if ax is not None and (grid_rows != 1 or grid_cols != 1):
        raise ValueError("ax can only be supplied for a single-panel plot.")

    fig_width = max(float(figsize[0]), 5.2 * grid_cols)
    fig_height = max(float(figsize[1]), 3.8 * grid_rows)
    if ax is None:
        fig, axes_grid = plt.subplots(
            nrows=grid_rows,
            ncols=grid_cols,
            figsize=(fig_width, fig_height),
            squeeze=False,
            sharex=True,
            sharey=False,
            constrained_layout=True,
        )
        fig.set_constrained_layout_pads(w_pad=0.04, h_pad=0.05, wspace=0.04, hspace=0.05)
    else:
        fig = ax.figure
        axes_grid = [[ax]]

    theme_cfg = _theme_config(theme)
    _apply_theme(fig=fig, axes_grid=axes_grid, theme_cfg=theme_cfg)

    all_representative_carbons = sorted(
        {
            int(round(value))
            for value in plot_data["representative_carbon"]
            if pd.notna(value)
        }
    )
    color_lookup = _build_exact_color_lookup(
        carbon_numbers=all_representative_carbons,
        palette=palette,
        highlight_small=highlight_small,
        highlight_large=highlight_large,
        parent_carbon_number=overall_parent,
        theme=theme,
        plt=plt,
    )
    base_cmap = plt.get_cmap(palette)
    system_styles = _build_system_styles(systems if systems != [None] else [])

    for system_idx, system_value in enumerate(panel_systems):
        for region_idx, region in enumerate(regions):
            row_idx = system_idx * region_rows + (region_idx // region_cols)
            col_idx = region_idx % region_cols
            axis = axes_grid[row_idx][col_idx]
            panel = plot_data[plot_data["plot_region"] == region.label].copy()
            if system_mode == "facet" and system_value is not None:
                panel = panel[panel[system_col] == system_value]
            panel = panel.sort_values(
                [_SERIES_COL, time_col]
                if _SERIES_COL in panel.columns
                else [time_col]
            )

            if panel.empty:
                axis.set_visible(False)
                continue

            legend_entries: list[dict[str, Any]] = []
            for series_key, series_df in panel.groupby(_SERIES_COL, sort=False, dropna=False):
                first = series_df.iloc[0]
                carbon_value = first["representative_carbon"]
                line_color = _resolve_series_color(
                    carbon_value=carbon_value,
                    color_lookup=color_lookup,
                    base_cmap=base_cmap,
                )
                linewidth = 2.6 if bool(first["is_parent_highlight"]) else 1.7
                alpha = 1.0 if bool(first["is_parent_highlight"]) else 0.88
                linestyle = "-"
                if system_mode == "overlay" and system_col and pd.notna(first[_SYSTEM_VALUE_COL]):
                    linestyle = system_styles[str(first[_SYSTEM_VALUE_COL])]
                label = _build_series_label(
                    row=first,
                    system_mode=system_mode,
                    system_col=system_col,
                )

                line = axis.plot(
                    series_df[time_col],
                    series_df["mean_count"],
                    color=line_color,
                    linewidth=linewidth,
                    linestyle=linestyle,
                    alpha=alpha,
                    label=label,
                )[0]
                legend_entries.append(
                    {
                        "handle": line,
                        "label": label,
                        "display_sort": float(first.get("display_sort", 0)),
                        "peak_count": float(series_df["mean_count"].max()),
                        "is_parent_highlight": bool(first["is_parent_highlight"]),
                        "priority": _legend_priority(
                            carbon_value=carbon_value,
                            is_parent_highlight=bool(first["is_parent_highlight"]),
                            highlight_small=highlight_small,
                            highlight_large=highlight_large,
                        ),
                    }
                )
                if show_uncertainty and (series_df["std_count"] > 0).any():
                    axis.fill_between(
                        series_df[time_col],
                        series_df["mean_count"] - series_df["std_count"],
                        series_df["mean_count"] + series_df["std_count"],
                        color=line_color,
                        alpha=0.12,
                        linewidth=0,
                    )

            axis.set_xlabel(time_col)
            axis.set_ylabel("number of molecules")
            axis.grid(True, alpha=0.22)
            axis.set_title(_panel_title(region_label=region.label, system_value=system_value), pad=8)
            axis.margins(x=0.02, y=0.08)

            filtered = _select_legend_entries(
                legend_entries=legend_entries,
                legend_mode=legend_mode,
                effective_mode=effective_mode,
            )
            if filtered:
                axis.legend(
                    [item[0] for item in filtered],
                    [item[1] for item in filtered],
                    loc="upper right",
                    title=_legend_title(effective_mode),
                    fontsize=8.5,
                    title_fontsize=9,
                    ncol=_legend_ncols(len(filtered), legend_mode),
                    frameon=True,
                    framealpha=0.92,
                    borderpad=0.35,
                    labelspacing=0.28,
                )

    for system_idx in range(len(panel_systems)):
        total_slots = region_rows * region_cols
        for region_idx in range(len(regions), total_slots):
            row_idx = system_idx * region_rows + (region_idx // region_cols)
            col_idx = region_idx % region_cols
            axes_grid[row_idx][col_idx].set_visible(False)

    if system_mode == "overlay" and systems and systems != [None]:
        handles = [
            Line2D([0], [0], color=theme_cfg["text"], linewidth=2.0, linestyle=system_styles[str(system_value)])
            for system_value in systems
        ]
        labels = [str(system_value) for system_value in systems]
        fig.legend(handles, labels, loc="upper right", title=system_col, frameon=True)

    if output_path is not None:
        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output, bbox_inches="tight")

    summary = None
    if return_summary:
        oxidation_summary = _build_oxidation_time_series(
            source=source,
            time_col=time_col,
            count_col=count_col,
            system_col=system_col,
            replicate_col=replicate_col,
        )
        summary = summarize_carbon_evolution(
            aggregated_data=summary_stats.rename(columns={"mean_count": count_col}),
            time_col=time_col,
            count_col=count_col,
            carbon_number_col="carbon_number",
            system_col=system_col,
            parent_carbon_number=overall_parent,
            highlight_small=highlight_small,
            highlight_large=highlight_large,
            parent_decay_threshold=parent_decay_threshold,
            species_data=oxidation_summary,
        )
        if isinstance(summary, dict):
            summary["plot_mode"] = effective_mode
            if effective_mode != mode:
                summary["requested_plot_mode"] = mode

    axes_obj: Any
    if ax is not None:
        axes_obj = ax
    elif grid_rows == 1 and grid_cols == 1:
        axes_obj = axes_grid[0][0]
    else:
        axes_obj = axes_grid

    plot_data = plot_data.sort_values(
        [col for col in (_SYSTEM_VALUE_COL, _REGION_VALUE_COL, _SERIES_COL, time_col) if col in plot_data.columns]
    ).reset_index(drop=True)
    return fig, axes_obj, summary, plot_data


def _parse_standard_formula(formula: str) -> dict[str, int]:
    """Strict parser for standard molecular formulas."""

    cursor = 0
    while cursor < len(formula):
        match = _STANDARD_FORMULA_PATTERN.match(formula, cursor)
        if match is None:
            raise ValueError(
                f"Could not parse species label {formula!r} as a standard molecular formula. "
                "Provide a custom parser via formula_parser=... for ReacNetGenerator-specific labels."
            )
        cursor = match.end()
    counts = parse_formula(formula)
    if not counts:
        raise ValueError(f"Could not parse species label {formula!r}.")
    return counts


def _emit_progress(
    progress_callback: ProgressCallback | None,
    **payload: Any,
) -> None:
    """Safely emit a progress update."""

    if progress_callback is None:
        return
    progress_callback(payload)


def _parse_species_timestep_line(line: str) -> tuple[int, list[tuple[str, int]]] | None:
    """Parse one ``Timestep N: species count ...`` line from an RNG species file."""

    match = _SPECIES_TIMESTEP_RE.match(line.strip())
    if match is None:
        return None

    timestep = int(match.group(1))
    tokens = match.group(2).strip().split()
    pairs: list[tuple[str, int]] = []
    cursor = 0
    while cursor < len(tokens) - 1:
        species = tokens[cursor]
        try:
            count = int(tokens[cursor + 1])
        except ValueError:
            cursor += 1
            continue
        pairs.append((species, count))
        cursor += 2
    return timestep, pairs


def _parse_carbon_range_token(text: str) -> tuple[int | None, int | None]:
    """Parse a single carbon-range token."""

    token = text.strip().replace(" ", "")
    token = token.replace("≤", "<=").replace("≥", ">=")
    token = re.sub(r"(?i)c(?=\d)", "", token)
    if not token:
        raise ValueError("Carbon range token cannot be empty.")

    if token.endswith("+"):
        return int(token[:-1]), None
    if token.startswith(">="):
        return int(token[2:]), None
    if token.startswith(">"):
        return int(token[1:]) + 1, None
    if token.startswith("<="):
        return None, int(token[2:])
    if token.startswith("<"):
        return None, int(token[1:]) - 1
    if "-" in token:
        left, right = token.split("-", 1)
        start = int(left)
        end = int(right)
        if end < start:
            raise ValueError(f"Invalid carbon range {text!r}: end < start.")
        return start, end
    value = int(token)
    return value, value


def _validate_atom_counts(raw_counts: Mapping[str, Any], species: str) -> dict[str, int]:
    """Validate parser output and normalize values to integers."""

    if not isinstance(raw_counts, Mapping):
        raise TypeError(
            f"Formula parser must return a mapping of element counts; got {type(raw_counts)!r}."
        )
    counts: dict[str, int] = {}
    for element, value in raw_counts.items():
        if not isinstance(element, str) or not element:
            raise ValueError(f"Invalid element key {element!r} returned for species {species!r}.")
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid atom count {value!r} for element {element!r} in species {species!r}."
            ) from exc
        if number < 0:
            raise ValueError(
                f"Atom counts must be non-negative; got {number} for {element!r} in {species!r}."
            )
        if number > 0:
            counts[element] = counts.get(element, 0) + number
    if not counts:
        raise ValueError(
            f"Species {species!r} did not yield any atoms. Provide a valid molecular formula "
            "or a custom parser."
        )
    return counts


def _default_oxidized_product_selector(atom_counts: Mapping[str, int]) -> bool:
    """Identify carbon-oxygen-only oxidation products such as CO and CO2."""

    element_set = {element for element, count in atom_counts.items() if count > 0}
    return atom_counts.get("C", 0) > 0 and atom_counts.get("O", 0) > 0 and element_set <= {"C", "O"}


def _prepare_source_table(
    data: TableLike,
    time_col: str,
    species_col: str,
    count_col: str,
    system_col: str | None,
    replicate_col: str | None,
    formula_parser: FormulaParser | None = None,
    oxidized_product_selector: Callable[[Mapping[str, int]], bool] | None = None,
) -> pd.DataFrame:
    """Load, validate, and enrich the input table with carbon metadata."""

    required = [time_col, species_col, count_col]
    optional = [col for col in (system_col, replicate_col) if col]
    source = _coerce_table(data=data, required_columns=required + optional)

    for col in required:
        if source[col].isna().any():
            raise ValueError(f"Column {col!r} contains missing values; clean the table before plotting.")

    source = source.copy()
    source[species_col] = source[species_col].astype(str).str.strip()
    if (source[species_col] == "").any():
        raise ValueError(f"Column {species_col!r} contains empty species labels.")

    source[count_col] = pd.to_numeric(source[count_col], errors="raise")
    if (source[count_col] < 0).any():
        raise ValueError(f"Column {count_col!r} contains negative counts, which are unsupported.")

    unique_species = pd.Index(source[species_col].drop_duplicates())
    records = []
    errors: list[str] = []
    selector = oxidized_product_selector or _default_oxidized_product_selector
    for species in unique_species:
        try:
            atom_counts = parse_formula_to_atom_counts(species, parser=formula_parser)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))
            if len(errors) >= 5:
                break
            continue
        records.append(
            {
                species_col: species,
                "carbon_number": atom_counts.get("C", 0),
                "oxygen_number": atom_counts.get("O", 0),
                "__is_oxidized_product": bool(selector(atom_counts)),
            }
        )

    if errors:
        examples = "; ".join(errors)
        raise ValueError(
            "Failed to parse one or more species labels into molecular formulas. "
            f"Examples: {examples}"
        )

    metadata = pd.DataFrame.from_records(records)
    enriched = source.merge(metadata, on=species_col, how="left", validate="m:1")
    if enriched["carbon_number"].isna().any():
        raise ValueError("Failed to annotate all species with carbon numbers.")
    enriched["carbon_number"] = enriched["carbon_number"].astype(int)
    enriched["oxygen_number"] = enriched["oxygen_number"].astype(int)
    enriched["__is_oxidized_product"] = enriched["__is_oxidized_product"].fillna(False).astype(bool)
    return enriched


def _coerce_table(data: TableLike, required_columns: Sequence[str]) -> pd.DataFrame:
    """Convert DataFrame-like or path-like input to a validated DataFrame."""

    if isinstance(data, pd.DataFrame):
        table = data.copy()
    elif isinstance(data, (str, Path)):
        path = Path(data).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        if path.suffix.lower() == ".csv":
            table = pd.read_csv(path)
        else:
            table = pd.read_excel(path)
    else:
        table = pd.DataFrame(data)

    if table.empty:
        raise ValueError("Input data is empty; at least one row is required.")

    missing = [col for col in required_columns if col not in table.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}; available: {list(table.columns)}"
        )
    return table


def _aggregate_enriched_table(
    source: pd.DataFrame,
    time_col: str,
    count_col: str,
    system_col: str | None,
    replicate_col: str | None,
    complete_missing: bool,
) -> pd.DataFrame:
    """Aggregate an enriched species table by carbon number."""

    group_cols = [col for col in (system_col, replicate_col) if col]
    aggregated = (
        source.groupby(group_cols + [time_col, "carbon_number"], dropna=False, as_index=False)[count_col]
        .sum()
    )
    if not complete_missing:
        return aggregated

    carbon_numbers = sorted(int(value) for value in aggregated["carbon_number"].drop_duplicates())
    if not carbon_numbers:
        return aggregated

    if group_cols:
        completed_parts = []
        for group_values, subset in aggregated.groupby(group_cols, dropna=False, sort=False):
            if not isinstance(group_values, tuple):
                group_values = (group_values,)
            times = _sorted_unique(subset[time_col])
            full_index = pd.MultiIndex.from_product(
                [times, carbon_numbers],
                names=[time_col, "carbon_number"],
            ).to_frame(index=False)
            merged = full_index.merge(
                subset[[time_col, "carbon_number", count_col]],
                on=[time_col, "carbon_number"],
                how="left",
            )
            merged[count_col] = merged[count_col].fillna(0.0)
            for col, value in zip(group_cols, group_values):
                merged[col] = value
            completed_parts.append(merged[group_cols + [time_col, "carbon_number", count_col]])
        return pd.concat(completed_parts, ignore_index=True)

    times = _sorted_unique(aggregated[time_col])
    full_index = pd.MultiIndex.from_product(
        [times, carbon_numbers],
        names=[time_col, "carbon_number"],
    ).to_frame(index=False)
    merged = full_index.merge(
        aggregated[[time_col, "carbon_number", count_col]],
        on=[time_col, "carbon_number"],
        how="left",
    )
    merged[count_col] = merged[count_col].fillna(0.0)
    return merged


def _sorted_unique(series: pd.Series) -> list[Any]:
    """Return stable sorted unique values."""

    unique = series.drop_duplicates()
    try:
        return unique.sort_values(kind="mergesort").tolist()
    except TypeError:
        return unique.tolist()


def _normalize_smoothing(smoothing: SmoothingSpec) -> dict[str, Any] | None:
    """Normalize smoothing configuration."""

    if smoothing is None:
        return None
    if isinstance(smoothing, str):
        spec = {"method": smoothing}
    elif isinstance(smoothing, Mapping):
        spec = dict(smoothing)
    else:
        raise ValueError("smoothing must be None, a method string, or a mapping.")

    method = str(spec.get("method", "")).strip().lower()
    if method in {"", "none"}:
        return None
    if method in {"savitzky-golay", "savitzky_golay"}:
        method = "savgol"
    if method not in {"rolling", "savgol"}:
        raise ValueError("Unsupported smoothing method. Use 'rolling' or 'savgol'.")

    spec["method"] = method
    if method == "rolling":
        spec.setdefault("window", 3)
        spec.setdefault("center", True)
        spec.setdefault("min_periods", 1)
    else:
        spec.setdefault("window_length", 5)
        spec.setdefault("polyorder", 2)
    return spec


def _apply_smoothing(
    aggregated: pd.DataFrame,
    time_col: str,
    count_col: str,
    system_col: str | None,
    replicate_col: str | None,
    smoothing: SmoothingSpec,
) -> pd.DataFrame:
    """Smooth carbon-number series inside each system/replicate/carbon group."""

    spec = _normalize_smoothing(smoothing)
    if spec is None:
        return aggregated.copy()

    group_cols = [col for col in (system_col, replicate_col) if col] + ["carbon_number"]
    sorted_df = aggregated.sort_values(group_cols + [time_col]).copy()
    parts = []
    if group_cols:
        grouped = sorted_df.groupby(group_cols, dropna=False, sort=False)
    else:
        grouped = [(None, sorted_df)]

    for _, subset in grouped:
        subset = subset.copy()
        values = subset[count_col].astype(float)
        if spec["method"] == "rolling":
            subset[count_col] = values.rolling(
                window=int(spec["window"]),
                center=bool(spec.get("center", True)),
                min_periods=int(spec.get("min_periods", 1)),
            ).mean()
        else:
            subset[count_col] = _apply_savgol(values, spec)
        parts.append(subset)

    return pd.concat(parts, ignore_index=True)


def _apply_savgol(values: pd.Series, spec: Mapping[str, Any]) -> pd.Series:
    """Apply Savitzky-Golay smoothing with safe window adjustments."""

    try:
        from scipy.signal import savgol_filter
    except ImportError as exc:
        raise ImportError(
            "Savitzky-Golay smoothing requires scipy. Install scipy or use rolling smoothing."
        ) from exc

    count = len(values)
    if count <= 2:
        return values

    window = int(spec.get("window_length", 5))
    polyorder = int(spec.get("polyorder", 2))
    if window % 2 == 0:
        window += 1
    if window > count:
        window = count if count % 2 == 1 else count - 1
    if window <= polyorder or window < 3:
        return values

    filtered = savgol_filter(
        values.to_numpy(dtype=float),
        window_length=window,
        polyorder=polyorder,
        mode="interp",
    )
    return pd.Series(filtered, index=values.index)


def _collapse_replicates(
    aggregated: pd.DataFrame,
    time_col: str,
    count_col: str,
    system_col: str | None,
    replicate_col: str | None,
) -> pd.DataFrame:
    """Convert replicate-resolved data to mean/std time series for plotting."""

    group_cols = [col for col in (system_col,) if col]
    if replicate_col and replicate_col in aggregated.columns:
        stats = (
            aggregated.groupby(group_cols + [time_col, "carbon_number"], dropna=False)[count_col]
            .agg(["mean", "std", "size"])
            .reset_index()
            .rename(columns={"mean": "mean_count", "std": "std_count", "size": "n_replicates"})
        )
        stats["std_count"] = stats["std_count"].fillna(0.0)
        return stats

    stats = aggregated.copy()
    stats = stats.rename(columns={count_col: "mean_count"})
    stats["std_count"] = 0.0
    stats["n_replicates"] = 1
    keep_cols = group_cols + [time_col, "carbon_number", "mean_count", "std_count", "n_replicates"]
    return stats[keep_cols]


def _infer_parent_carbon_number(
    stats: pd.DataFrame,
    time_col: str,
    carbon_number_col: str,
    count_col: str,
) -> int:
    """Infer the parent carbon number from the earliest time slice."""

    earliest_time = _sorted_unique(stats[time_col])[0]
    initial = stats[stats[time_col] == earliest_time].copy()
    initial = initial.sort_values([count_col, carbon_number_col], ascending=[False, False])
    if initial.empty:
        raise ValueError("Unable to infer parent carbon number from empty statistics table.")
    return int(initial.iloc[0][carbon_number_col])


def _resolve_effective_mode(
    plot_stats: pd.DataFrame,
    requested_mode: str,
    carbon_bins: Sequence[RangeSpec | int] | None,
    max_exact_lines: int,
) -> str:
    """Auto-switch from exact mode when the plot would be too crowded."""

    if requested_mode != "exact":
        return requested_mode
    unique_carbons = plot_stats["carbon_number"].nunique(dropna=False)
    if unique_carbons <= max_exact_lines:
        return requested_mode
    return "binned" if carbon_bins is not None else "topk"


def _transform_plot_groups(
    stats: pd.DataFrame,
    time_col: str,
    system_col: str | None,
    mode: str,
    carbon_bins: Sequence[RangeSpec | int] | None,
    top_k: int,
    parent_carbon_number: int,
) -> pd.DataFrame:
    """Map carbon-number statistics to exact, binned, or top-k display groups."""

    base = stats.copy()
    if mode == "exact":
        base["display_label"] = base["carbon_number"].map(lambda value: f"C{int(value)}")
        base["series_start_carbon"] = base["carbon_number"].astype(int)
        base["series_end_carbon"] = base["carbon_number"].astype(int)
        base["representative_carbon"] = base["carbon_number"].astype(float)
        base["display_mode"] = "exact"
        base["display_sort"] = base["carbon_number"].astype(int)
    elif mode == "binned":
        ranges = _normalize_ranges(
            specs=carbon_bins,
            max_carbon_number=int(base["carbon_number"].max()),
            default_label="carbon bin",
        )
        mapped = base["carbon_number"].map(lambda value: _range_for_value(ranges, int(value)))
        base["display_label"] = mapped.map(lambda item: item.label)
        base["series_start_carbon"] = mapped.map(lambda item: _none_safe(item.start, default=0))
        base["series_end_carbon"] = mapped.map(lambda item: _none_safe(item.end, default=int(base["carbon_number"].max())))
        base["representative_carbon"] = mapped.map(
            lambda item: _representative_carbon(item.start, item.end)
        )
        base["display_mode"] = "binned"
        base["display_sort"] = base["series_start_carbon"]
    else:
        totals = (
            base.groupby("carbon_number", dropna=False)["mean_count"]
            .sum()
            .sort_values(ascending=False)
        )
        keep = {int(value) for value in totals.head(top_k).index}
        keep.add(int(parent_carbon_number))
        base["display_label"] = base["carbon_number"].map(
            lambda value: f"C{int(value)}" if int(value) in keep else "others"
        )
        base["series_start_carbon"] = base["carbon_number"].map(
            lambda value: int(value) if int(value) in keep else int(base["carbon_number"].min())
        )
        base["series_end_carbon"] = base["carbon_number"].map(
            lambda value: int(value) if int(value) in keep else int(base["carbon_number"].max())
        )
        base["representative_carbon"] = base["carbon_number"].map(
            lambda value: float(value) if int(value) in keep else math.nan
        )
        base["display_mode"] = "topk"
        base["display_sort"] = base["carbon_number"].map(
            lambda value: int(value) if int(value) in keep else int(base["carbon_number"].max()) + 1
        )

    base["variance_count"] = base["std_count"].astype(float) ** 2
    aggregate_cols = [col for col in (system_col, time_col, "display_label", "series_start_carbon", "series_end_carbon", "representative_carbon", "display_mode", "display_sort") if col]
    grouped = (
        base.groupby(aggregate_cols, dropna=False, as_index=False)[["mean_count", "variance_count"]]
        .sum()
    )
    grouped["std_count"] = grouped["variance_count"].map(math.sqrt)
    grouped = grouped.drop(columns=["variance_count"])
    grouped["is_parent_highlight"] = (
        grouped["series_start_carbon"] <= parent_carbon_number
    ) & (grouped["series_end_carbon"] >= parent_carbon_number)
    grouped[_SYSTEM_VALUE_COL] = grouped[system_col] if system_col else None
    grouped[_SERIES_COL] = grouped.apply(
        lambda row: _series_identifier(row=row, system_col=system_col),
        axis=1,
    )
    grouped[_REGION_VALUE_COL] = ""
    return grouped


def _coerce_optional_range_specs(
    specs: Sequence[RangeSpec] | str | None,
) -> list[RangeSpec] | None:
    """Normalize optional range specifications to a list form."""

    if specs is None:
        return None
    if isinstance(specs, str):
        parsed = parse_carbon_range_specs(specs)
        return parsed or None
    values = list(specs)
    return values or None


def _apply_display_filter_and_merges(
    plot_data: pd.DataFrame,
    time_col: str,
    system_col: str | None,
    parent_carbon_number: int,
    display_ranges: Sequence[RangeSpec] | None,
    merge_ranges: Sequence[RangeSpec] | None,
) -> pd.DataFrame:
    """Filter visible ranges and optionally merge selected ranges into one curve."""

    result = plot_data.copy()
    if result.empty:
        return result

    max_carbon = int(result["series_end_carbon"].max())
    if display_ranges:
        filters = _normalize_ranges(
            specs=display_ranges,
            max_carbon_number=max_carbon,
            default_label="display",
        )
        mask = _match_rows_by_ranges(result, filters)
        result = result.loc[mask].copy()
        if result.empty:
            raise ValueError("display_ranges filtered out all curves; adjust the selected carbon ranges.")
        max_carbon = int(result["series_end_carbon"].max())

    if merge_ranges:
        ranges = _normalize_ranges(
            specs=merge_ranges,
            max_carbon_number=max_carbon,
            default_label="merged",
        )
        result = _merge_rows_by_ranges(
            plot_data=result,
            merge_ranges=ranges,
            time_col=time_col,
            system_col=system_col,
            parent_carbon_number=parent_carbon_number,
        )

    result[_SYSTEM_VALUE_COL] = result[system_col] if system_col else None
    result[_SERIES_COL] = result.apply(
        lambda row: _series_identifier(row=row, system_col=system_col),
        axis=1,
    )
    result[_REGION_VALUE_COL] = ""
    return result


def _match_rows_by_ranges(
    plot_data: pd.DataFrame,
    ranges: Sequence[CarbonRange],
) -> pd.Series:
    """Return a boolean mask for rows overlapping any target range."""

    if plot_data.empty:
        return pd.Series(dtype=bool)

    starts = plot_data["series_start_carbon"].astype(float)
    ends = plot_data["series_end_carbon"].astype(float)
    mask = pd.Series(False, index=plot_data.index)
    for item in ranges:
        lo = -math.inf if item.start is None else float(item.start)
        hi = math.inf if item.end is None else float(item.end)
        mask = mask | ((ends >= lo) & (starts <= hi))
    return mask


def _merge_rows_by_ranges(
    plot_data: pd.DataFrame,
    merge_ranges: Sequence[CarbonRange],
    time_col: str,
    system_col: str | None,
    parent_carbon_number: int,
) -> pd.DataFrame:
    """Merge rows that overlap configured carbon ranges into single display curves."""

    merged = plot_data.copy()
    merged["__merge_label"] = merged["display_label"]
    merged["__merge_start"] = merged["series_start_carbon"].astype(int)
    merged["__merge_end"] = merged["series_end_carbon"].astype(int)
    merged["__merge_rep"] = merged["representative_carbon"].astype(float)
    merged["__merge_sort"] = merged["display_sort"].astype(float)
    merged["__merge_mode"] = merged["display_mode"].astype(str)
    merged["__merge_assigned"] = False

    max_carbon = int(merged["series_end_carbon"].max())
    starts = merged["series_start_carbon"].astype(float)
    ends = merged["series_end_carbon"].astype(float)
    for item in merge_ranges:
        lo = -math.inf if item.start is None else float(item.start)
        hi = math.inf if item.end is None else float(item.end)
        mask = ((ends >= lo) & (starts <= hi)) & (~merged["__merge_assigned"])
        if not bool(mask.any()):
            continue

        start_val = _none_safe(item.start, default=0)
        end_val = _none_safe(item.end, default=max_carbon)
        merged.loc[mask, "__merge_label"] = str(item.label)
        merged.loc[mask, "__merge_start"] = int(start_val)
        merged.loc[mask, "__merge_end"] = int(end_val)
        merged.loc[mask, "__merge_rep"] = _representative_carbon(item.start, item.end)
        merged.loc[mask, "__merge_sort"] = float(start_val)
        merged.loc[mask, "__merge_mode"] = "merged"
        merged.loc[mask, "__merge_assigned"] = True

    merged["__variance_count"] = merged["std_count"].astype(float) ** 2
    group_cols = [col for col in (system_col, time_col) if col] + [
        "__merge_label",
        "__merge_start",
        "__merge_end",
        "__merge_rep",
        "__merge_mode",
        "__merge_sort",
    ]
    grouped = (
        merged.groupby(group_cols, dropna=False, as_index=False)[["mean_count", "__variance_count"]]
        .sum()
    )
    grouped["std_count"] = grouped["__variance_count"].map(math.sqrt)
    grouped = grouped.drop(columns=["__variance_count"])
    grouped = grouped.rename(
        columns={
            "__merge_label": "display_label",
            "__merge_start": "series_start_carbon",
            "__merge_end": "series_end_carbon",
            "__merge_rep": "representative_carbon",
            "__merge_mode": "display_mode",
            "__merge_sort": "display_sort",
        }
    )
    grouped["is_parent_highlight"] = (
        grouped["series_start_carbon"] <= parent_carbon_number
    ) & (grouped["series_end_carbon"] >= parent_carbon_number)
    return grouped


def _normalize_ranges(
    specs: Sequence[RangeSpec | int] | None,
    max_carbon_number: int,
    default_label: str,
) -> list[CarbonRange]:
    """Normalize bin or subplot range specifications."""

    if not specs:
        width = 5 if max_carbon_number <= 40 else 10
        ranges = []
        start = 0
        while start <= max_carbon_number:
            end = min(start + width - 1, max_carbon_number)
            ranges.append(CarbonRange(label=_format_range_label(start, end), start=start, end=end))
            start = end + 1
        return ranges

    if all(isinstance(item, int) for item in specs):
        edges = sorted(int(item) for item in specs)
        if not edges:
            raise ValueError("carbon_bins cannot be empty.")
        if edges[0] > 0:
            edges = [0] + edges
        ranges = []
        for idx, start in enumerate(edges):
            if idx + 1 < len(edges):
                end = edges[idx + 1]
                if idx > 0:
                    start = start + 1
                label = _format_range_label(start, end)
            else:
                start = start + 1 if idx > 0 else start
                end = None
                label = _format_range_label(start, end)
            ranges.append(CarbonRange(label=label, start=start, end=end))
        return ranges

    ranges: list[CarbonRange] = []
    for idx, item in enumerate(specs):
        if isinstance(item, Mapping):
            start = item.get("start")
            end = item.get("end")
            label = item.get("label") or _format_range_label(start, end)
        elif isinstance(item, tuple) and len(item) == 2:
            start, end = item
            label = _format_range_label(start, end)
        elif isinstance(item, tuple) and len(item) == 3:
            label, start, end = item
        else:
            raise ValueError(
                "Range specifications must be integers, (start, end), "
                "(label, start, end), or mappings with start/end keys."
            )
        ranges.append(
            CarbonRange(
                label=str(label) if label is not None else f"{default_label} {idx + 1}",
                start=None if start is None else int(start),
                end=None if end is None else int(end),
            )
        )

    if not ranges:
        raise ValueError("At least one range is required.")
    return ranges


def _range_for_value(ranges: Sequence[CarbonRange], carbon_number: int) -> CarbonRange:
    """Return the first range that contains the provided carbon number."""

    for item in ranges:
        if item.contains(carbon_number):
            return item
    last = ranges[-1]
    if last.end is None and carbon_number >= _none_safe(last.start, default=0):
        return last
    raise ValueError(
        f"Carbon number C{carbon_number} is not covered by carbon_bins. "
        "Adjust carbon_bins to span the full observed carbon-number range."
    )


def _format_range_label(start: int | None, end: int | None) -> str:
    """Format a human-readable range label."""

    if start is None and end is None:
        return "all carbon numbers"
    if end is None:
        if start is None:
            return "all carbon numbers"
        return f"C{start}+"
    if start is None:
        return f"<=C{end}"
    if start == end:
        return f"C{start}"
    return f"C{start}-C{end}"


def _representative_carbon(start: int | None, end: int | None) -> float:
    """Return a representative carbon number for color mapping."""

    if start is None and end is None:
        return math.nan
    if start is None:
        return float(end)
    if end is None:
        return float(start)
    return float((start + end) / 2.0)


def _resolve_layout_regions(
    layout: str,
    layout_regions: Sequence[RangeSpec] | None,
    highlight_small: tuple[int, int],
    highlight_large: int,
    max_carbon_number: int,
) -> list[CarbonRange]:
    """Build subplot regions."""

    if layout == "single":
        return [CarbonRange(label="All carbon numbers", start=None, end=None)]
    if layout_regions:
        return _normalize_ranges(layout_regions, max_carbon_number, default_label="panel")

    small_end = highlight_small[1]
    mid_end = max(small_end + 1, highlight_large - 1)
    return [
        CarbonRange(label=_format_range_label(1, small_end), start=1, end=small_end),
        CarbonRange(label=_format_range_label(small_end + 1, min(mid_end, 15)), start=small_end + 1, end=min(mid_end, 15)),
        CarbonRange(label=_format_range_label(16, max(highlight_large - 1, 30)), start=16, end=max(highlight_large - 1, 30)),
        CarbonRange(label=_format_range_label(max(highlight_large, 31), None), start=max(highlight_large, 31), end=None),
    ]


def _resolve_subplot_grid(
    region_count: int,
    layout: str,
    subplot_max_columns: int | None,
) -> tuple[int, int]:
    """Choose a readable subplot grid for carbon-number regions."""

    if layout != "subplots" or region_count <= 1:
        return 1, 1

    if subplot_max_columns is None:
        max_columns = 1 if region_count <= 2 else 2
    else:
        max_columns = max(1, int(subplot_max_columns))

    ncols = min(region_count, max_columns)
    nrows = int(math.ceil(region_count / float(ncols)))
    return nrows, ncols


def _assign_region(
    regions: Sequence[CarbonRange],
    start: int | None,
    end: int | None,
    default_label: str,
) -> str:
    """Assign a plot series to a subplot region."""

    for region in regions:
        if region.fully_contains(start, end):
            return region.label
    for region in regions:
        if region.overlaps(start, end):
            return region.label
    return default_label


def _series_identifier(row: pd.Series, system_col: str | None) -> str:
    """Build a unique series identifier for grouped plot data."""

    start = _serialize_bound(row.get("series_start_carbon"))
    end = _serialize_bound(row.get("series_end_carbon"))
    span = f"{start}-{end}"
    if system_col:
        return f"{row.get(system_col)}::{row['display_label']}::{span}"
    return f"{row['display_label']}::{span}"


def _serialize_bound(value: Any) -> str:
    """Serialize a carbon-range bound for internal series keys."""

    if value is None:
        return "None"
    if pd.isna(value):
        return "NaN"
    return str(int(value))


def _theme_config(theme: str) -> dict[str, str]:
    """Return colors for the selected theme."""

    if theme == "dark":
        return {
            "figure_face": "#111827",
            "axes_face": "#111827",
            "grid": "#475569",
            "text": "#f8fafc",
            "spine": "#94a3b8",
            "parent": "#f8fafc",
        }
    return {
        "figure_face": "#ffffff",
        "axes_face": "#ffffff",
        "grid": "#cbd5e1",
        "text": "#0f172a",
        "spine": "#94a3b8",
        "parent": "#111827",
    }


def _downsample_plot_series_data(
    plot_data: pd.DataFrame,
    time_col: str,
    max_points_per_series: int,
) -> pd.DataFrame:
    """Downsample each plotted series to a bounded number of display points."""

    if plot_data.empty:
        return plot_data

    parts = []
    for _, series_df in plot_data.groupby(_SERIES_COL, sort=False, dropna=False):
        series_df = series_df.sort_values(time_col)
        count = len(series_df)
        if count <= max_points_per_series:
            parts.append(series_df)
            continue

        step = (count - 1) / float(max_points_per_series - 1)
        indices = [int(round(idx * step)) for idx in range(max_points_per_series)]
        indices[0] = 0
        indices[-1] = count - 1
        sampled = series_df.iloc[sorted(set(indices))]
        parts.append(sampled)

    return pd.concat(parts, ignore_index=True)


def _apply_theme(fig: Figure, axes_grid: Sequence[Sequence[Axes]], theme_cfg: Mapping[str, str]) -> None:
    """Apply light/dark theme settings to a figure."""

    fig.patch.set_facecolor(theme_cfg["figure_face"])
    for row in axes_grid:
        for axis in row:
            axis.set_facecolor(theme_cfg["axes_face"])
            axis.tick_params(colors=theme_cfg["text"])
            axis.xaxis.label.set_color(theme_cfg["text"])
            axis.yaxis.label.set_color(theme_cfg["text"])
            axis.title.set_color(theme_cfg["text"])
            for spine in axis.spines.values():
                spine.set_color(theme_cfg["spine"])


def _build_system_styles(system_values: Sequence[Any]) -> dict[str, str]:
    """Assign deterministic line styles to systems for overlay plots."""

    styles = ["-", "--", ":", "-."]
    return {str(value): styles[idx % len(styles)] for idx, value in enumerate(system_values)}


def _resolve_series_color(
    carbon_value: float,
    color_lookup: Mapping[int, Any],
    base_cmap: Any,
) -> Any:
    """Resolve line color from carbon number and highlighting rules."""

    if pd.notna(carbon_value):
        return color_lookup.get(int(round(carbon_value)), base_cmap(0.5))
    return base_cmap(0.15)


def _build_series_label(
    row: pd.Series,
    system_mode: str | None,
    system_col: str | None,
) -> str:
    """Return the series label shown in legends."""

    if system_mode == "overlay" and system_col:
        return f"{row['display_label']} ({row[_SYSTEM_VALUE_COL]})"
    return str(row["display_label"])


def _build_exact_color_lookup(
    carbon_numbers: Sequence[int],
    palette: str,
    highlight_small: tuple[int, int],
    highlight_large: int,
    parent_carbon_number: int,
    theme: str,
    plt: Any,
) -> dict[int, Any]:
    """Build a discrete color lookup keyed by carbon number."""

    if not carbon_numbers:
        return {}

    small_values = sorted(
        value for value in carbon_numbers if highlight_small[0] <= int(value) <= highlight_small[1]
    )
    large_values = sorted(value for value in carbon_numbers if int(value) >= highlight_large)
    blocked = set(small_values) | set(large_values) | {int(parent_carbon_number)}
    middle_values = sorted(value for value in carbon_numbers if int(value) not in blocked)

    lookup: dict[int, Any] = {}
    lookup.update(_sample_discrete_colors(small_values, plt.get_cmap("autumn"), start=0.35, stop=0.9))
    lookup.update(_sample_discrete_colors(middle_values, plt.get_cmap(palette), start=0.15, stop=0.9))
    lookup.update(_sample_discrete_colors(large_values, plt.get_cmap("Blues"), start=0.45, stop=0.95))
    if int(parent_carbon_number) in carbon_numbers:
        lookup[int(parent_carbon_number)] = "#111827" if theme == "light" else "#f8fafc"
    return lookup


def _sample_discrete_colors(
    values: Sequence[int],
    cmap: Any,
    start: float,
    stop: float,
) -> dict[int, Any]:
    """Sample a colormap at discrete, well-separated positions."""

    if not values:
        return {}
    if len(values) == 1:
        return {int(values[0]): cmap((start + stop) / 2.0)}

    span = stop - start
    return {
        int(value): cmap(start + span * (idx / max(len(values) - 1, 1)))
        for idx, value in enumerate(values)
    }


def _legend_priority(
    carbon_value: float,
    is_parent_highlight: bool,
    highlight_small: tuple[int, int],
    highlight_large: int,
) -> int:
    """Prioritize parent, small fragments, and large-growth lines in compact legends."""

    if is_parent_highlight:
        return 3
    if pd.notna(carbon_value) and highlight_small[0] <= int(round(carbon_value)) <= highlight_small[1]:
        return 2
    if pd.notna(carbon_value) and int(round(carbon_value)) >= highlight_large:
        return 1
    return 0


def _select_legend_entries(
    legend_entries: Sequence[Mapping[str, Any]],
    legend_mode: str,
    effective_mode: str,
    compact_limit: int = 8,
) -> list[tuple[Any, str]]:
    """Select readable legend entries for a panel."""

    if not legend_entries:
        return []

    deduped: dict[str, Mapping[str, Any]] = {}
    for entry in legend_entries:
        label = str(entry["label"])
        current = deduped.get(label)
        if current is None:
            deduped[label] = entry
            continue
        current_key = (
            int(current.get("is_parent_highlight", False)),
            float(current.get("peak_count", 0.0)),
            -float(current.get("display_sort", 0.0)),
        )
        entry_key = (
            int(entry.get("is_parent_highlight", False)),
            float(entry.get("peak_count", 0.0)),
            -float(entry.get("display_sort", 0.0)),
        )
        if entry_key > current_key:
            deduped[label] = entry

    ordered = sorted(
        deduped.values(),
        key=lambda item: (float(item.get("display_sort", 0.0)), str(item["label"])),
    )
    if legend_mode == "detailed" or effective_mode in {"binned", "topk"} or len(ordered) <= compact_limit:
        return [(item["handle"], str(item["label"])) for item in ordered]

    compact = sorted(
        ordered,
        key=lambda item: (
            -int(item.get("priority", 0)),
            -float(item.get("peak_count", 0.0)),
            float(item.get("display_sort", 0.0)),
            str(item["label"]),
        ),
    )[:compact_limit]
    compact = sorted(
        compact,
        key=lambda item: (float(item.get("display_sort", 0.0)), str(item["label"])),
    )
    return [(item["handle"], str(item["label"])) for item in compact]


def _legend_title(effective_mode: str) -> str:
    """Return the legend title for the current display mode."""

    if effective_mode == "exact":
        return "carbon number"
    if effective_mode == "binned":
        return "carbon bin"
    return "carbon class"


def _legend_ncols(entry_count: int, legend_mode: str) -> int:
    """Use more legend columns only when the entry count would otherwise be tall."""

    if legend_mode == "compact":
        return 1
    return 2 if entry_count >= 8 else 1


def _panel_title(region_label: str, system_value: Any) -> str:
    """Return a concise panel title."""

    if system_value is None:
        return str(region_label)
    return f"{system_value} | {region_label}"


def _summarize_single_group(
    group_df: pd.DataFrame,
    time_col: str,
    count_col: str,
    carbon_number_col: str,
    parent_carbon_number: int | None,
    highlight_small: tuple[int, int],
    highlight_large: int,
    parent_decay_threshold: float,
    species_data: pd.DataFrame | None,
    species_filters: Mapping[str, Any] | None,
    oxidized_count_col: str,
    oxidized_carbon_col: str,
) -> dict[str, Any]:
    """Summarize a single system or the overall dataset."""

    pivot = (
        group_df.pivot_table(
            index=time_col,
            columns=carbon_number_col,
            values=count_col,
            aggfunc="sum",
            fill_value=0.0,
        )
        .sort_index()
    )
    if pivot.empty:
        raise ValueError("Cannot summarize an empty carbon-number table.")

    if parent_carbon_number is None:
        initial = pivot.iloc[0].sort_values(ascending=False)
        parent_carbon_number = int(initial.index[0])

    parent_series = pivot.get(parent_carbon_number, pd.Series(0.0, index=pivot.index))
    parent_initial_count = float(parent_series.iloc[0])
    parent_peak_count = float(parent_series.max())
    parent_final_count = float(parent_series.iloc[-1])
    threshold_base = parent_initial_count if parent_initial_count > 0 else parent_peak_count
    decay_limit = threshold_base * (1.0 - float(parent_decay_threshold))
    onset_time = None
    for time_value, count_value in parent_series.items():
        if float(count_value) < decay_limit:
            onset_time = _serialize_value(time_value)
            break

    small_total = pivot.loc[
        :,
        [col for col in pivot.columns if highlight_small[0] <= int(col) <= highlight_small[1]],
    ].sum(axis=1)
    large_total = pivot.loc[:, [col for col in pivot.columns if int(col) >= int(highlight_large)]].sum(axis=1)
    carbonaceous_total = pivot.loc[:, [col for col in pivot.columns if int(col) > 0]].sum(axis=1)
    carbonaceous_total = carbonaceous_total.where(carbonaceous_total > 0, other=1.0)

    small_fraction = small_total / carbonaceous_total
    large_fraction = large_total / carbonaceous_total
    entropy = _shannon_entropy_over_time(pivot)

    summary = {
        "parent_carbon_number": int(parent_carbon_number),
        "parent_initial_count": parent_initial_count,
        "parent_peak_count": parent_peak_count,
        "parent_final_count": parent_final_count,
        "parent_decay_onset_time": onset_time,
        "small_fragment_peak_time": _peak_time(small_total),
        "small_fragment_peak_count": float(small_total.max()) if not small_total.empty else 0.0,
        "large_hydrocarbon_peak_time": _peak_time(large_total),
        "large_hydrocarbon_peak_count": float(large_total.max()) if not large_total.empty else 0.0,
        "max_carbon_number_observed": int(max(pivot.columns)),
        "observed_carbon_numbers": [int(value) for value in pivot.columns],
        "carbon_distribution_entropy": _series_payload(entropy),
        "small_fragment_fraction_over_time": _series_payload(small_fraction),
        "large_hydrocarbon_fraction_over_time": _series_payload(large_fraction),
    }

    oxidation = _summarize_oxidation(
        species_data=species_data,
        species_filters=species_filters,
        time_col=time_col,
        oxidized_count_col=oxidized_count_col,
        oxidized_carbon_col=oxidized_carbon_col,
        parent_carbon_number=int(parent_carbon_number),
        parent_initial_count=parent_initial_count,
    )
    if oxidation:
        summary.update(oxidation)
    return summary


def _shannon_entropy_over_time(pivot: pd.DataFrame) -> pd.Series:
    """Compute Shannon entropy of the carbon-number distribution over time."""

    carbonaceous = pivot.loc[:, [col for col in pivot.columns if int(col) > 0]]
    totals = carbonaceous.sum(axis=1).replace(0, pd.NA)
    probabilities = carbonaceous.div(totals, axis=0)
    def entropy_row(row: pd.Series) -> float:
        value = 0.0
        for prob in row.dropna():
            prob = float(prob)
            if prob > 0:
                value -= prob * math.log(prob)
        return value
    return probabilities.apply(entropy_row, axis=1).fillna(0.0)


def _peak_time(series: pd.Series) -> Any:
    """Return the earliest time value at the series maximum."""

    if series.empty:
        return None
    peak_value = series.max()
    peak_index = series[series == peak_value].index[0]
    return _serialize_value(peak_index)


def _series_payload(series: pd.Series) -> dict[str, list[Any]]:
    """Serialize a time series to a JSON-friendly payload."""

    return {
        "time": [_serialize_value(index) for index in series.index],
        "value": [float(value) for value in series.values],
    }


def _serialize_value(value: Any) -> Any:
    """Convert pandas/numpy scalars and timestamps to JSON-safe values."""

    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            return value
    return value


def _build_oxidation_time_series(
    source: pd.DataFrame,
    time_col: str,
    count_col: str,
    system_col: str | None,
    replicate_col: str | None,
) -> pd.DataFrame:
    """Aggregate oxidation-product counts and carbon inventories over time."""

    enriched = source.copy()
    enriched["__oxidized_product_count"] = enriched[count_col].where(enriched["__is_oxidized_product"], other=0.0)
    enriched["__oxidized_product_carbon_count"] = (
        enriched[count_col] * enriched["carbon_number"].where(enriched["__is_oxidized_product"], other=0)
    )
    group_cols = [col for col in (system_col, replicate_col, time_col) if col]
    aggregated = (
        enriched.groupby(group_cols, dropna=False)[["__oxidized_product_count", "__oxidized_product_carbon_count"]]
        .sum()
        .reset_index()
    )
    if replicate_col and replicate_col in aggregated.columns:
        summary_group_cols = [col for col in (system_col, time_col) if col]
        aggregated = (
            aggregated.groupby(summary_group_cols, dropna=False)[["__oxidized_product_count", "__oxidized_product_carbon_count"]]
            .mean()
            .reset_index()
        )
    return aggregated


def _summarize_oxidation(
    species_data: pd.DataFrame | None,
    species_filters: Mapping[str, Any] | None,
    time_col: str,
    oxidized_count_col: str,
    oxidized_carbon_col: str,
    parent_carbon_number: int,
    parent_initial_count: float,
) -> dict[str, Any]:
    """Build optional oxidation metrics when carbon-oxygen products are available."""

    if species_data is None or species_data.empty:
        return {}

    subset = species_data.copy()
    if species_filters:
        for col, value in species_filters.items():
            if col in subset.columns:
                subset = subset[subset[col] == value]
    if subset.empty:
        return {}

    oxidation_counts = subset.groupby(time_col, dropna=False)[oxidized_count_col].sum().sort_index()
    oxidation_carbons = subset.groupby(time_col, dropna=False)[oxidized_carbon_col].sum().sort_index()
    if oxidation_counts.empty:
        return {}

    parent_initial_carbon_inventory = float(parent_carbon_number) * float(parent_initial_count)
    if parent_initial_carbon_inventory > 0:
        oxidation_fraction = oxidation_carbons / parent_initial_carbon_inventory
    else:
        oxidation_fraction = oxidation_carbons * 0.0

    return {
        "oxidized_product_count_over_time": _series_payload(oxidation_counts),
        "parent_carbon_to_oxidized_products_fraction_over_time": _series_payload(oxidation_fraction),
    }


def _none_safe(value: int | None, default: int) -> int:
    """Return a default integer when value is None."""

    return default if value is None else int(value)
