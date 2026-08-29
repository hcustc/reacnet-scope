"""Auditable apparent rate estimates for directly observed reaction types.

The estimates in this module deliberately remain separate from reaction TP.
They require physical time and reactant-population exposure, and they report
the mass-action assumption and observation window with every result.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from scipy.stats import chi2


AVOGADRO_CONSTANT = 6.022_140_76e23
ANGSTROM3_TO_LITRE = 1e-27
AVOGADRO_ANGSTROM3_TO_LITRE = (
    AVOGADRO_CONSTANT * ANGSTROM3_TO_LITRE
)


class KineticsInputError(ValueError):
    """Raised when evidence cannot support the requested kinetic estimate."""


def _poisson_count_interval(event_count: int) -> tuple[float, float]:
    """Return the exact two-sided 95% Garwood interval for a count."""
    if event_count < 0:
        raise KineticsInputError("event count must be non-negative")
    lower = (
        0.0
        if event_count == 0
        else 0.5 * float(chi2.ppf(0.025, 2 * event_count))
    )
    upper = 0.5 * float(chi2.ppf(0.975, 2 * (event_count + 1)))
    return lower, upper


def _falling_factorial(population: int, multiplicity: int) -> int:
    if population < multiplicity:
        return 0
    value = 1
    for offset in range(multiplicity):
        value *= population - offset
    return value


def _validated_timesteps(values: Sequence[int]) -> list[int]:
    timesteps = [int(value) for value in values]
    if len(timesteps) < 2:
        raise KineticsInputError("at least two timesteps are required")
    if any(current <= previous for previous, current in zip(timesteps, timesteps[1:])):
        raise KineticsInputError("timesteps must be strictly increasing")
    return timesteps


def _rate_result(
    *,
    event_count: int,
    observation_time_ps: float,
    exposure: float,
    order: int,
    stoichiometry: Mapping[str, int],
    n_intervals: int,
) -> dict[str, Any]:
    event_frequency = event_count / observation_time_ps
    exposure_unit = (
        "molecule·ps" if order == 1 else "molecule²·ps·Å⁻³"
    )
    k_app_unit = "ps⁻¹" if order == 1 else "L·mol⁻¹·ps⁻¹"
    common = {
        "event_count": event_count,
        "observation_time_ps": observation_time_ps,
        "event_frequency_per_ps": event_frequency,
        "reaction_order": order,
        "reactant_stoichiometry": dict(stoichiometry),
        "exposure": exposure,
        "exposure_unit": exposure_unit,
        "k_app_unit": k_app_unit,
        "n_intervals": n_intervals,
        "model": "stoichiometric_mass_action",
    }
    if exposure <= 0:
        return {
            "status": "insufficient_exposure",
            **common,
            "k_app": None,
            "ci95_low": None,
            "ci95_high": None,
        }

    unit_scale = 1.0 if order == 1 else AVOGADRO_ANGSTROM3_TO_LITRE
    estimate = event_count / exposure * unit_scale
    count_lower, count_upper = _poisson_count_interval(event_count)
    return {
        "status": "estimated",
        **common,
        "k_app": estimate,
        "ci95_low": count_lower / exposure * unit_scale,
        "ci95_high": count_upper / exposure * unit_scale,
    }


def estimate_mass_action_rate(
    *,
    event_count: int,
    timesteps: Sequence[int],
    timestep_ps: float,
    reactants: Sequence[str],
    species_counts: Mapping[str, Mapping[int, int]],
    volumes_angstrom3: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    """Estimate an apparent first- or second-order mass-action rate constant.

    Reactant populations and volume use the left endpoint of each observed
    Transition.  Repeated reactants use a falling factorial (for example,
    ``n_A * (n_A - 1)`` for ``2 A``), matching the deterministic mass-action
    convention without silently introducing a symmetry factor.
    """
    count = int(event_count)
    if count < 0:
        raise KineticsInputError("event count must be non-negative")
    try:
        conversion = float(timestep_ps)
    except (TypeError, ValueError) as exc:
        raise KineticsInputError("timestep-to-ps conversion must be positive") from exc
    if not math.isfinite(conversion) or conversion <= 0:
        raise KineticsInputError("timestep-to-ps conversion must be positive")

    timeline = _validated_timesteps(timesteps)
    stoichiometry = Counter(str(value) for value in reactants if str(value))
    order = sum(stoichiometry.values())
    if order not in {1, 2}:
        raise KineticsInputError(
            "only first- and second-order mass-action estimates are supported"
        )
    if order == 2 and volumes_angstrom3 is None:
        raise KineticsInputError(
            "second-order mass-action estimates require cell volume"
        )

    observation_time_ps = 0.0
    exposure = 0.0
    for before, after in zip(timeline, timeline[1:]):
        duration_ps = (after - before) * conversion
        observation_time_ps += duration_ps
        population_factor = 1
        for species, multiplicity in stoichiometry.items():
            counts = species_counts.get(species)
            if counts is None or before not in counts:
                raise KineticsInputError(
                    f"reactant population is missing at timestep {before}: {species}"
                )
            try:
                population = int(counts[before])
            except (TypeError, ValueError) as exc:
                raise KineticsInputError(
                    f"reactant population is invalid at timestep {before}: {species}"
                ) from exc
            if population < 0:
                raise KineticsInputError(
                    f"reactant population is negative at timestep {before}: {species}"
                )
            population_factor *= _falling_factorial(population, multiplicity)

        if order == 1:
            exposure += population_factor * duration_ps
            continue
        assert volumes_angstrom3 is not None
        try:
            volume = float(volumes_angstrom3[before])
        except (KeyError, TypeError, ValueError) as exc:
            raise KineticsInputError(
                f"cell volume is missing at timestep {before}"
            ) from exc
        if not math.isfinite(volume) or volume <= 0:
            raise KineticsInputError(
                f"cell volume must be positive at timestep {before}"
            )
        exposure += population_factor * duration_ps / volume

    return _rate_result(
        event_count=count,
        observation_time_ps=observation_time_ps,
        exposure=exposure,
        order=order,
        stoichiometry=stoichiometry,
        n_intervals=len(timeline) - 1,
    )


def estimate_mass_action_rate_aligned(
    *,
    event_count: int,
    timesteps: Sequence[int],
    timestep_ps: float,
    reactants: Sequence[str],
    species_counts: Mapping[str, Sequence[int]],
    volumes_angstrom3: Mapping[int, float] | None = None,
) -> dict[str, Any]:
    """Vectorized apparent-rate estimate for aligned population arrays."""
    count = int(event_count)
    if count < 0:
        raise KineticsInputError("event count must be non-negative")
    try:
        conversion = float(timestep_ps)
    except (TypeError, ValueError) as exc:
        raise KineticsInputError(
            "timestep-to-ps conversion must be positive"
        ) from exc
    if not math.isfinite(conversion) or conversion <= 0:
        raise KineticsInputError(
            "timestep-to-ps conversion must be positive"
        )

    timeline = _validated_timesteps(timesteps)
    stoichiometry = Counter(str(value) for value in reactants if str(value))
    order = sum(stoichiometry.values())
    if order not in {1, 2}:
        raise KineticsInputError(
            "only first- and second-order mass-action estimates are supported"
        )
    if order == 2 and volumes_angstrom3 is None:
        raise KineticsInputError(
            "second-order mass-action estimates require cell volume"
        )

    durations = np.diff(np.asarray(timeline, dtype=np.int64)).astype(
        np.float64
    ) * conversion
    population_factor = np.ones(len(durations), dtype=np.float64)
    for species, multiplicity in stoichiometry.items():
        raw_counts = species_counts.get(species)
        if raw_counts is None:
            raise KineticsInputError(
                f"reactant population is missing: {species}"
            )
        try:
            populations = np.asarray(raw_counts, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise KineticsInputError(
                f"reactant population is invalid: {species}"
            ) from exc
        if populations.ndim != 1 or len(populations) != len(timeline):
            raise KineticsInputError(
                f"reactant population is not aligned to timesteps: {species}"
            )
        if (
            not np.all(np.isfinite(populations))
            or np.any(populations < 0)
            or np.any(populations != np.floor(populations))
        ):
            raise KineticsInputError(
                f"reactant population is invalid: {species}"
            )
        left = populations[:-1]
        if multiplicity == 1:
            population_factor *= left
        else:
            population_factor *= np.where(
                left >= multiplicity,
                left * (left - 1),
                0,
            )

    if order == 1:
        exposure = float(np.dot(population_factor, durations))
    else:
        assert volumes_angstrom3 is not None
        try:
            volumes = np.asarray(
                [volumes_angstrom3[timestep] for timestep in timeline[:-1]],
                dtype=np.float64,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KineticsInputError(
                "cell volume is missing at an aligned timestep"
            ) from exc
        if not np.all(np.isfinite(volumes)) or np.any(volumes <= 0):
            raise KineticsInputError(
                "cell volume must be positive at every aligned timestep"
            )
        exposure = float(
            np.sum(population_factor * durations / volumes)
        )

    return _rate_result(
        event_count=count,
        observation_time_ps=float(np.sum(durations)),
        exposure=exposure,
        order=order,
        stoichiometry=stoichiometry,
        n_intervals=len(timeline) - 1,
    )
