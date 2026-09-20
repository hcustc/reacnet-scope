"""Occurrence-level QC handoff readiness for DFT Initial Geometry packages.

This module reports whether one exact Reaction Occurrence can be handed to an
external TS optimization/frequency/IRC workflow.  It never scores reactions,
asserts elementary-step identity, or claims that kinetics can be calculated.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping

from .dft_geometry import (
    DftGeometryBundle,
    DftGeometryError,
    DftGeometryRequest,
    build_dft_geometry_bundle,
)


REACTION_READINESS_SCHEMA_VERSION = "reacnet-scope/reaction-readiness/v1"
QC_HANDOFF_STATUSES = frozenset(
    {"blocked", "needs_input", "review_required", "ready"}
)


@dataclass(frozen=True)
class ReactionReadinessRequest:
    """One explicit QC handoff question for a Reaction Occurrence."""

    geometry: DftGeometryRequest = field(default_factory=DftGeometryRequest)
    isolated_cluster_confirmed: bool = False


@dataclass(frozen=True)
class ReactionReadinessResult:
    """A versioned report and, only when handoff is possible, its bundle."""

    report: dict[str, Any]
    bundle: DftGeometryBundle | None


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "item"):
        try:
            return _json_safe(value.item())
        except (TypeError, ValueError):
            pass
    return str(value)


def _derived_source_revision(artifacts: Mapping[str, str]) -> dict[str, Any]:
    descriptors: list[dict[str, Any]] = []
    for kind in sorted(
        {
            "reaction",
            "species",
            "trajectory",
            "timeline",
            "reactionevent",
            "molecules",
        }
    ):
        path = Path(str(artifacts.get(kind) or ""))
        if not path.is_file():
            continue
        stat = path.stat()
        descriptors.append(
            {
                "kind": kind,
                "size": int(stat.st_size),
                "mtime_ns": int(stat.st_mtime_ns),
            }
        )
    encoded = json.dumps(
        descriptors,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return {
        "fingerprint": hashlib.sha256(encoded).hexdigest(),
        "artifacts": descriptors,
    }


def _check(
    check_id: str,
    status: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    remediation: str = "",
    claim_limit: str = "",
) -> dict[str, Any]:
    item = {
        "id": check_id,
        "status": status,
        "evidence": _json_safe(dict(evidence or {})),
    }
    if status != "pass":
        if remediation:
            item["remediation"] = remediation
        if claim_limit:
            item["claim_limit"] = claim_limit
    return item


def _participant_selection(
    event: Mapping[str, Any],
    side: str,
    *,
    included: bool,
    requested_indices: tuple[int, ...] | None,
) -> tuple[list[dict[str, Any]], str]:
    if not included:
        return [], ""
    raw_values = event.get(f"{side}_participants") or []
    if not isinstance(raw_values, list) or not raw_values:
        return [], f"{side} 缺少 Molecule Instance"
    normalized: list[dict[str, Any]] = []
    occupied: set[int] = set()
    for index, raw in enumerate(raw_values):
        if not isinstance(raw, Mapping):
            return [], f"{side} Molecule Instance {index + 1} 无效"
        species = str(raw.get("species") or "").strip()
        try:
            atom_ids = sorted({int(value) for value in raw.get("atom_ids") or []})
        except (TypeError, ValueError):
            return [], f"{side} Molecule Instance {index + 1} 的 Atom IDs 无效"
        if not species or not atom_ids or any(value <= 0 for value in atom_ids):
            return [], f"{side} Molecule Instance {index + 1} 缺少 Species 或 Atom IDs"
        overlap = occupied.intersection(atom_ids)
        if overlap:
            return [], f"{side} Molecule Instance 重复使用 Atom IDs {sorted(overlap)}"
        occupied.update(atom_ids)
        normalized.append(
            {"index": index, "species": species, "atom_ids": atom_ids}
        )
    indices = (
        tuple(range(len(normalized)))
        if requested_indices is None
        else tuple(requested_indices)
    )
    if not indices:
        return [], f"{side} 未选择 Molecule Instance"
    if len(set(indices)) != len(indices) or any(
        isinstance(index, bool)
        or not isinstance(index, int)
        or index < 0
        or index >= len(normalized)
        for index in indices
    ):
        return [], f"{side} Molecule Instance 选择无效"
    return [normalized[index] for index in indices], ""


def _bond_set(raw: Any) -> tuple[set[tuple[int, int, str]], str]:
    values = raw.split(";") if isinstance(raw, str) else list(raw or [])
    bonds: set[tuple[int, int, str]] = set()
    for value in values:
        if not str(value).strip():
            continue
        parts = str(value).split("-")
        if len(parts) < 3:
            return set(), f"无效键标识 {value!r}"
        try:
            first, second = int(parts[0]), int(parts[1])
        except ValueError:
            return set(), f"无效键标识 {value!r}"
        if first <= 0 or second <= 0 or first == second:
            return set(), f"无效键标识 {value!r}"
        bonds.add((min(first, second), max(first, second), "-".join(parts[2:])))
    return bonds, ""


def _occurrence_atom_ids(event: Mapping[str, Any]) -> list[int]:
    values = event.get("atom_id_list") or []
    if values:
        try:
            return sorted({int(value) for value in values})
        except (TypeError, ValueError):
            pass
    return sorted(
        {
            int(atom_id)
            for side in ("reactant", "product")
            for participant in event.get(f"{side}_participants") or []
            if isinstance(participant, Mapping)
            for atom_id in participant.get("atom_ids") or []
        }
    )


def _top_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in checks}
    if "blocked" in statuses:
        return "blocked"
    if "needs_input" in statuses:
        return "needs_input"
    if "review_required" in statuses:
        return "review_required"
    return "ready"


def _error_check(exc: Exception) -> dict[str, Any]:
    reason = str(getattr(exc, "reason", "geometry_validation_failed") or "")
    needs_input = {
        "unconfirmed_length_unit",
        "incomplete_element_mapping",
    }
    status = "needs_input" if reason in needs_input else "blocked"
    return _check(
        "geometry_validation",
        status,
        evidence={"reason": reason, "message": str(exc)},
        remediation=(
            "确认轨迹 Å 单位并补全 Type→Element 映射后重新预检。"
            if status == "needs_input"
            else "修复精确帧、拓扑、PBC 或来源索引问题后重新预检。"
        ),
    )


def _electronic_checks(bundle: DftGeometryBundle) -> list[dict[str, Any]]:
    combined = {
        str(item.get("side") or ""): item
        for item in bundle.manifest.get("geometries") or []
        if item.get("kind") == "combined"
    }
    missing = [
        side
        for side in ("reactant", "product")
        if side not in combined
        or (combined[side].get("electronic_state") or {}).get("status")
        != "user_supplied"
    ]
    checks = [
        _check(
            "electronic_states_complete",
            "needs_input" if missing else "pass",
            evidence={"missing_sides": missing},
            remediation=(
                "为 reactants 与 products 合并几何填写总电荷和自旋多重度。"
                if missing
                else ""
            ),
        )
    ]
    if missing:
        return checks

    from ase.data import atomic_numbers

    parity_errors: list[dict[str, Any]] = []
    states: dict[str, dict[str, int]] = {}
    for side in ("reactant", "product"):
        metadata = combined[side]
        state = metadata["electronic_state"]
        charge = int(state["charge"])
        multiplicity = int(state["multiplicity"])
        electrons = sum(
            int(atomic_numbers[element]) * int(count)
            for element, count in (metadata.get("element_counts") or {}).items()
        ) - charge
        states[side] = {
            "charge": charge,
            "multiplicity": multiplicity,
            "electron_count": electrons,
        }
        if (
            electrons < 0
            or multiplicity > electrons + 1
            or (electrons + multiplicity) % 2 == 0
        ):
            parity_errors.append({"side": side, **states[side]})
    checks.append(
        _check(
            "electron_multiplicity_parity",
            "blocked" if parity_errors else "pass",
            evidence={"states": states, "invalid": parity_errors},
            remediation=(
                "更正总电荷或自旋多重度；软件不会猜测电子态。"
                if parity_errors
                else ""
            ),
        )
    )
    charges = {side: state["charge"] for side, state in states.items()}
    checks.append(
        _check(
            "charge_conservation",
            "blocked" if len(set(charges.values())) != 1 else "pass",
            evidence={"charges": charges},
            remediation=(
                "为两侧提供守恒且与所选原子一致的总电荷。"
                if len(set(charges.values())) != 1
                else ""
            ),
        )
    )
    multiplicities = {
        side: state["multiplicity"] for side, state in states.items()
    }
    checks.append(
        _check(
            "spin_crossing_review",
            "review_required"
            if len(set(multiplicities.values())) != 1
            else "pass",
            evidence={"multiplicities": multiplicities},
            remediation=(
                "人工确认可能的自旋面变化与外部 TS 流程设置。"
                if len(set(multiplicities.values())) != 1
                else ""
            ),
        )
    )
    return checks


def _warning_check(bundle: DftGeometryBundle) -> dict[str, Any]:
    warnings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for geometry in bundle.manifest.get("geometries") or []:
        for warning in geometry.get("warnings") or []:
            key = (
                str(warning.get("code") or "geometry_warning"),
                str(warning.get("message") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            warnings.append(
                {
                    "code": key[0],
                    "message": key[1],
                }
            )
    return _check(
        "geometry_warnings",
        "review_required" if warnings else "pass",
        evidence={"warnings": warnings},
        remediation=(
            "逐项复核大体系、拉伸键、短接触、远距离片段"
            "或受限碰撞检查。"
            if warnings
            else ""
        ),
    )


def _report(
    *,
    event: Mapping[str, Any],
    dataset_id: str,
    source_revision: Mapping[str, Any],
    replicate: str,
    selected_atom_ids: Mapping[str, list[int]],
    checks: list[dict[str, Any]],
    source_signatures: Mapping[str, Any],
) -> dict[str, Any]:
    status = _top_status(checks)
    assert status in QC_HANDOFF_STATUSES
    event_id = str(event.get("event_id") or "")
    reaction_key = str(
        event.get("reaction_key")
        or event.get("reaction_smiles")
        or f"{event.get('reactant') or ''}->{event.get('product') or ''}"
    )
    return {
        "schema_version": REACTION_READINESS_SCHEMA_VERSION,
        "subject": {
            "dataset_id": str(dataset_id or ""),
            "source_revision_fingerprint": str(
                source_revision.get("fingerprint") or ""
            ),
            "replicate": str(replicate or ""),
            "event_id": event_id,
            "reaction_key": reaction_key,
            "atom_ids": _json_safe(selected_atom_ids),
        },
        "qc_handoff": {
            "status": status,
            "meaning": (
                "ready means this occurrence package can be handed to an "
                "external TS optimization/frequency/IRC workflow"
            ),
            "claim_limit": (
                "ready does not mean the Reaction Occurrence is elementary, "
                "that a transition state is validated, or that a rate can be calculated"
            ),
            "checks": checks,
        },
        "supporting_evidence": {
            "occurrence_identity": event_id,
            "persistence_observation": {
                "status": "unknown",
                "claim_limit": "persistence was not evaluated by this preflight",
            },
        },
        "kinetics_applicability": {
            "status": "insufficient_evidence",
            "claim_limit": (
                "QC handoff readiness does not establish gas-phase TST/RRKM "
                "applicability or rate-calculation completeness"
            ),
            "checks": [
                {
                    "id": "elementary_step",
                    "status": "unknown",
                    "claim_limit": "a Transition has no internal event ordering",
                },
                {
                    "id": "stationary_points_frequencies_irc",
                    "status": "insufficient_evidence",
                    "remediation": "run and validate the external QC workflow",
                },
                {
                    "id": "kinetics_model_inputs",
                    "status": "insufficient_evidence",
                    "remediation": (
                        "supply validated thermochemistry, conformers/rotors, "
                        "symmetry and any pressure-dependence inputs"
                    ),
                },
            ],
        },
        "data_version": _json_safe(source_revision),
        "source_signatures": _json_safe(source_signatures),
    }


def evaluate_reaction_readiness(
    artifacts: Mapping[str, str],
    event: Mapping[str, Any],
    request: ReactionReadinessRequest,
    *,
    dataset_id: str = "",
    source_revision: Mapping[str, Any] | None = None,
    replicate: str = "",
    geometry_builder: Callable[
        [Mapping[str, str], Mapping[str, Any], DftGeometryRequest],
        DftGeometryBundle,
    ] = build_dft_geometry_bundle,
) -> ReactionReadinessResult:
    """Evaluate one occurrence without producing a misleading numeric score."""

    geometry = request.geometry
    current_revision = _derived_source_revision(artifacts)
    revision = dict(source_revision or current_revision)
    checks: list[dict[str, Any]] = []
    provenance_complete = bool(
        str(dataset_id or "").strip()
        and str(replicate or "").strip()
        and str(revision.get("fingerprint") or "").strip()
    )
    checks.append(
        _check(
            "auditable_provenance",
            "pass" if provenance_complete else "needs_input",
            evidence={
                "dataset_id": dataset_id,
                "replicate": replicate,
                "source_revision_fingerprint": revision.get("fingerprint"),
            },
            remediation=(
                "提供 Dataset Identity、Replicate 和 source revision。"
                if not provenance_complete
                else ""
            ),
        )
    )
    revision_current = bool(
        not source_revision
        or str(revision.get("fingerprint") or "")
        == str(current_revision.get("fingerprint") or "")
    )
    checks.append(
        _check(
            "source_revision_current",
            "pass" if revision_current else "blocked",
            evidence={
                "expected_fingerprint": revision.get("fingerprint"),
                "current_fingerprint": current_revision.get("fingerprint"),
            },
            remediation=(
                "源数据版本已变化；重新验证 Current Dataset 后再运行预检。"
                if not revision_current
                else ""
            ),
        )
    )
    matched = str(event.get("association_status") or "") == "matched"
    checks.append(
        _check(
            "matched_occurrence",
            "pass" if matched else "blocked",
            evidence={"association_status": event.get("association_status")},
            remediation=(
                "选择具有精确 Molecular Evidence 的 matched Reaction Occurrence。"
                if not matched
                else ""
            ),
        )
    )
    event_id = str(event.get("event_id") or "").strip()
    checks.append(
        _check(
            "occurrence_identity",
            "pass" if event_id else "blocked",
            evidence={"event_id": event_id},
            remediation=(
                "重新建立事件索引以获得稳定 event_id。" if not event_id else ""
            ),
        )
    )
    paired = bool(geometry.include_reactants and geometry.include_products)
    checks.append(
        _check(
            "paired_reactant_product_sides",
            "pass" if paired else "blocked",
            evidence={
                "include_reactants": geometry.include_reactants,
                "include_products": geometry.include_products,
            },
            remediation=(
                "外部 TS/IRC 交接必须同时选择反应物和产物。"
                if not paired
                else ""
            ),
        )
    )
    combined = str(geometry.layout or "").lower() in {"combined", "both"}
    checks.append(
        _check(
            "paired_combined_geometries",
            "pass" if combined else "blocked",
            evidence={"layout": geometry.layout},
            remediation=(
                "选择 combined 或 both，生成两侧合并复合物。"
                if not combined
                else ""
            ),
        )
    )

    reactants, reactant_error = _participant_selection(
        event,
        "reactant",
        included=geometry.include_reactants,
        requested_indices=geometry.reactant_indices,
    )
    products, product_error = _participant_selection(
        event,
        "product",
        included=geometry.include_products,
        requested_indices=geometry.product_indices,
    )
    participant_error = reactant_error or product_error
    checks.append(
        _check(
            "participant_selection",
            "blocked" if participant_error else "pass",
            evidence={"error": participant_error},
            remediation=(
                "选择有效且同侧不重叠的完整 Molecule Instances。"
                if participant_error
                else ""
            ),
        )
    )
    selected_atom_ids = {
        "reactant": sorted(
            {atom_id for value in reactants for atom_id in value["atom_ids"]}
        ),
        "product": sorted(
            {atom_id for value in products for atom_id in value["atom_ids"]}
        ),
    }
    balanced = bool(
        selected_atom_ids["reactant"]
        and selected_atom_ids["reactant"] == selected_atom_ids["product"]
    )
    checks.append(
        _check(
            "selected_atoms_balanced",
            "pass" if balanced else "blocked",
            evidence=selected_atom_ids,
            remediation=(
                "两侧必须选择完全相同的轨迹 Atom IDs。"
                if not balanced
                else ""
            ),
        )
    )

    reactant_bonds, reactant_bond_error = _bond_set(event.get("reactant_bonds"))
    product_bonds, product_bond_error = _bond_set(event.get("product_bonds"))
    bond_error = reactant_bond_error or product_bond_error
    changed_bonds = reactant_bonds.symmetric_difference(product_bonds)
    checks.append(
        _check(
            "discernible_rng_bond_change",
            "blocked" if bond_error or not changed_bonds else "pass",
            evidence={
                "error": bond_error,
                "changed_bonds": [list(value) for value in sorted(changed_bonds)],
            },
            remediation=(
                "使用包含有效且可区分成断键证据的 Reaction Occurrence。"
                if bond_error or not changed_bonds
                else ""
            ),
        )
    )
    core_ids = sorted(
        {atom_id for bond in changed_bonds for atom_id in bond[:2]}
    )
    core_complete = bool(
        core_ids
        and set(core_ids).issubset(selected_atom_ids["reactant"])
        and set(core_ids).issubset(selected_atom_ids["product"])
    )
    checks.append(
        _check(
            "reaction_core_complete",
            "pass" if core_complete else "blocked",
            evidence={"reaction_core_atom_ids": core_ids},
            remediation=(
                "两侧选择必须包含全部 RNG changed-bond endpoints。"
                if not core_complete
                else ""
            ),
        )
    )
    occurrence_ids = _occurrence_atom_ids(event)
    omitted = sorted(set(occurrence_ids).difference(selected_atom_ids["reactant"]))
    checks.append(
        _check(
            "unchanged_participants_omitted",
            "review_required" if omitted and balanced and core_complete else "pass",
            evidence={"omitted_atom_ids": omitted},
            remediation=(
                "确认遗漏的 unchanged spectator 不属于所需环境模型。"
                if omitted and balanced and core_complete
                else ""
            ),
        )
    )
    checks.append(
        _check(
            "isolated_cluster_scope_confirmed",
            "pass" if request.isolated_cluster_confirmed else "needs_input",
            evidence={"confirmed": request.isolated_cluster_confirmed},
            remediation=(
                "确认本次交接使用非周期孤立簇；"
                "该确认不证明气相动力学适用。"
                if not request.isolated_cluster_confirmed
                else ""
            ),
        )
    )

    bundle: DftGeometryBundle | None = None
    if not any(item["status"] == "blocked" for item in checks):
        try:
            bundle = geometry_builder(artifacts, event, geometry)
        except (DftGeometryError, OSError, RuntimeError, ValueError) as exc:
            checks.append(_error_check(exc))
        else:
            checks.append(
                _check(
                    "geometry_validation",
                    "pass",
                    evidence={"geometry_files": sorted(bundle.geometries)},
                )
            )
            checks.extend(_electronic_checks(bundle))
            checks.append(_warning_check(bundle))

    source_signatures = (
        dict(bundle.manifest.get("source_signatures") or {}) if bundle else {}
    )
    report = _report(
        event=event,
        dataset_id=dataset_id,
        source_revision=revision,
        replicate=replicate,
        selected_atom_ids=selected_atom_ids,
        checks=checks,
        source_signatures=source_signatures,
    )
    status = report["qc_handoff"]["status"]
    if status in {"blocked", "needs_input"}:
        bundle = None
    elif bundle is not None:
        manifest = {
            **bundle.manifest,
            "qc_handoff": {
                "status": status,
                "report_file": "reaction_readiness.json",
            },
            "provenance": {
                "dataset_id": str(dataset_id or ""),
                "source_revision_fingerprint": str(
                    revision.get("fingerprint") or ""
                ),
                "replicate": str(replicate or ""),
            },
        }
        bundle = replace(
            bundle,
            manifest=manifest,
            readiness_report=report,
            occurrence=_json_safe(event),
        )
    return ReactionReadinessResult(report=report, bundle=bundle)


__all__ = [
    "QC_HANDOFF_STATUSES",
    "REACTION_READINESS_SCHEMA_VERSION",
    "ReactionReadinessRequest",
    "ReactionReadinessResult",
    "evaluate_reaction_readiness",
]
