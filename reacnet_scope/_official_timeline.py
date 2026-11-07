"""Read-only normalization of the upstream RNG 1.1 timeline contract.

Only the timed-evidence adapter uses this module. Selection inspects metadata;
offline adapter preparation derives transition blocks in bounded reads. No
converted HDF5 file or frame-by-atom membership matrix is materialized.
"""

from __future__ import annotations

import json
from collections import Counter
from itertools import groupby, zip_longest
from typing import Any, Iterator, Sequence

import h5py
import numpy as np

from .rng_events import MoleculeRow, net_reaction_key

BLOCK_ROWS = 65_536


class OfficialTimelineError(ValueError):
    def __init__(self, message: str, *, state: str = "incompatible") -> None:
        super().__init__(f"timeline HDF5 1.1: {message}")
        self.state = state


def _text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def inspect_official_timeline(handle: h5py.File) -> int:
    """Validate metadata/column alignment without scanning payload tables."""
    if _text(handle.attrs.get("schema_version", "")) != "1.1":
        raise OfficialTimelineError("unsupported schema version")
    if _text(handle.attrs.get("format", "")) != "reacnetgenerator-timeline":
        raise OfficialTimelineError("unexpected format")
    if _text(handle.attrs.get("status", "")) != "complete":
        raise OfficialTimelineError("source is not complete", state="incomplete")
    try:
        capabilities = json.loads(_text(handle.attrs["capabilities"]))
        configuration = json.loads(_text(handle.attrs["configuration"]))
        parameters = configuration["parameters"]
        hmm = parameters["runHMM"]
        stride = parameters["stepinterval"]
    except (KeyError, TypeError, ValueError) as exc:
        raise OfficialTimelineError("invalid capabilities or configuration") from exc
    if capabilities != ["molecule_ranges", "reaction_events", "transition_evidence"]:
        raise OfficialTimelineError("incompatible capabilities")
    explicit = configuration.get("explicit_parameters")
    if not isinstance(explicit, list) or not all(isinstance(value, str) for value in explicit):
        raise OfficialTimelineError("invalid explicit parameter names")
    if type(hmm) is not bool or type(stride) is not int or stride < 1:
        raise OfficialTimelineError("invalid HMM or sampling configuration")
    basis = "HMM signal" if hmm else "observed signal"
    if _text(handle.attrs.get("molecule_range_basis", "")) != basis:
        raise OfficialTimelineError("molecule range basis disagrees with configuration")
    if _text(handle.attrs.get("atom_index_convention", "")) != (
        "zero-based RNG canonical atom order"
    ):
        raise OfficialTimelineError("unsupported atom index convention")

    def column(name: str, *, text: bool = False, size: int | None = None) -> int:
        value = handle.get(name)
        if not isinstance(value, h5py.Dataset):
            raise OfficialTimelineError(f"missing dataset {name}", state="incomplete")
        string = h5py.check_string_dtype(value.dtype)
        valid_type = (
            string is not None and string.encoding == "utf-8" and string.length is None
            if text else value.dtype.str == "<i8"
        )
        if value.ndim != 1 or not valid_type or size is not None and len(value) != size:
            raise OfficialTimelineError(f"incompatible column {name}")
        return len(value)

    sources = column("sources/path", text=True)
    column("sources/size_bytes", size=sources)
    column("sources/mtime_ns", size=sources)
    if sources != 1:
        raise OfficialTimelineError("multiple sources are not supported", state="unsupported")
    frames = column("frames/timestep")
    if frames < 1:
        raise OfficialTimelineError("at least one analyzed frame is required")
    column("frames/source_id", size=frames)
    column("frames/source_frame", size=frames)
    column("atoms/type")
    column("atoms/type_name", text=True)
    column("species/name", text=True)
    molecules = column("molecules/species_id")
    atoms = column("molecules/atom_index")
    bonds = column("molecules/bond_order")
    column("molecules/bond_atom_index_1", size=bonds)
    column("molecules/bond_atom_index_2", size=bonds)
    ranges = column("molecule_ranges/molecule_id")
    column("molecule_ranges/start_frame", size=ranges)
    column("molecule_ranges/end_frame", size=ranges)
    types = column("reaction_types/reactant", text=True)
    column("reaction_types/product", text=True, size=types)
    column("reaction_types/total_count", size=types)
    events = column("reaction_events/transition")
    column("reaction_events/reaction_type_id", size=events)
    column("reaction_events/count", size=events)
    exact = column("transition_evidence/transition")
    column("transition_evidence/reaction_type_id", size=exact)
    participants = column("transition_evidence/participant_molecule_id")
    column("transition_evidence/participant_side", size=participants)
    changes = column("transition_evidence/before_order")
    for name in ("after_order", "bond_atom_index_1", "bond_atom_index_2"):
        column(f"transition_evidence/{name}", size=changes)
    for name, rows, payload in (
        ("molecules/atom_offsets", molecules, atoms),
        ("molecules/bond_offsets", molecules, bonds),
        ("transition_evidence/participant_offsets", exact, participants),
        ("transition_evidence/bond_change_offsets", exact, changes),
    ):
        column(name, size=rows + 1)
        if int(handle[name][0]) != 0 or int(handle[name][-1]) != payload:
            raise OfficialTimelineError(f"invalid endpoints in {name}")
    return frames


class _Column:
    """Lazy ID shift, implicit ID vector, or pair of endpoint columns."""

    def __init__(self, first: h5py.Dataset | None = None, *,
                 second: h5py.Dataset | None = None, shift: int = 0,
                 size: int | None = None) -> None:
        self.first, self.second, self.shift = first, second, shift
        self.shape = (size,) if first is None else first.shape
        if second is not None:
            self.shape += (2,)
        self.dtype = np.dtype("int64")
        self.ndim = len(self.shape)

    def __len__(self) -> int:
        return self.shape[0]

    def __getitem__(self, key: Any) -> Any:
        if self.first is None:
            if key is Ellipsis:
                key = slice(None)
            if isinstance(key, slice):
                start, stop, step = key.indices(len(self))
                return np.arange(start + 1, stop + 1, step, dtype=np.int64)
            return np.int64((key if key >= 0 else len(self) + key) + 1)
        values = self.first[key]
        if self.second is not None:
            return np.stack((values, self.second[key]), axis=-1)
        return values + self.shift

    def __array__(self, dtype=None, copy=None) -> np.ndarray[Any, Any]:
        values = np.asarray(self[:], dtype=dtype)
        return values.copy() if copy else values


class _Group:
    def __init__(self, view: OfficialTimelineView, prefix: str) -> None:
        self.view, self.prefix = view, prefix

    def __getitem__(self, name: str) -> Any:
        return self.view[f"{self.prefix}/{name}"]


class OfficialTimelineView:
    """Expose upstream tables in the existing adapter's internal vocabulary."""

    def __init__(self, handle: h5py.File, frame_count: int) -> None:
        self.raw = handle
        self.columns = {
            "molecules/molecule_id": _Column(size=len(handle["molecules/species_id"])),
            "molecules/species_id": _Column(handle["molecules/species_id"], shift=1),
            "molecules/atom_ids": handle["molecules/atom_index"],
        }
        for group in ("molecules", "transition_evidence"):
            self.columns[f"{group}/bond_atoms"] = _Column(
                handle[f"{group}/bond_atom_index_1"],
                second=handle[f"{group}/bond_atom_index_2"],
            )
        for group in ("reaction_events", "transition_evidence"):
            self.columns[f"{group}/transition_index"] = handle[f"{group}/transition"]
            self.columns[f"{group}/reaction_id"] = _Column(
                handle[f"{group}/reaction_type_id"], shift=1
            )
            self.columns[f"{group}/block_start"] = np.zeros(frame_count - 1, dtype=np.uint64)
            self.columns[f"{group}/block_length"] = np.zeros(frame_count - 1, dtype=np.uint64)
        self._validate_columns(frame_count)

    def __getitem__(self, name: str) -> Any:
        if name in self.columns:
            return self.columns[name]
        value = self.raw[name]
        return _Group(self, name) if isinstance(value, h5py.Group) else value

    def close(self) -> None:
        self.raw.close()

    def _chunks(self, *names: str) -> Iterator[tuple[np.ndarray[Any, Any], ...]]:
        size = len(self.raw[names[0]])
        for start in range(0, size, BLOCK_ROWS):
            yield tuple(self.raw[name][start:start + BLOCK_ROWS] for name in names)

    def _validate_columns(self, frames: int) -> None:
        for name in (
            "molecules/atom_offsets", "molecules/bond_offsets",
            "transition_evidence/participant_offsets", "transition_evidence/bond_change_offsets",
        ):
            previous = 0
            for (values,) in self._chunks(name):
                if np.any(values < previous) or np.any(values[1:] < values[:-1]):
                    raise OfficialTimelineError(f"nonmonotonic offsets in {name}")
                previous = int(values[-1])
        stride = json.loads(_text(self.raw.attrs["configuration"]))["parameters"]["stepinterval"]
        frame_offset = 0
        for sources, source_frames in self._chunks("frames/source_id", "frames/source_frame"):
            expected = np.arange(frame_offset, frame_offset + len(sources), dtype=np.int64) * stride
            if np.any(sources != 0) or np.any(source_frames != expected):
                raise OfficialTimelineError("invalid frame source references")
            frame_offset += len(sources)
        species_count = len(self.raw["species/name"])
        for (ids,) in self._chunks("molecules/species_id"):
            if np.any(ids < 0) or np.any(ids >= species_count):
                raise OfficialTimelineError("unknown molecule species")
        molecule_count = len(self.raw["molecules/species_id"])
        for ids, sides in self._chunks(
            "transition_evidence/participant_molecule_id", "transition_evidence/participant_side"
        ):
            if np.any(ids < 1) or np.any(ids > molecule_count) or np.any((sides != 0) & (sides != 1)):
                raise OfficialTimelineError("invalid transition participants")
        # Offsets into the sorted range table allow per-participant presence
        # checks without expanding ranges or retaining their payload in RAM.
        self.range_offsets = np.zeros(molecule_count + 1, dtype=np.int64)
        previous_id, previous_end = 0, -2
        for ids, starts, ends in self._chunks(
            "molecule_ranges/molecule_id", "molecule_ranges/start_frame", "molecule_ranges/end_frame"
        ):
            if (np.any(ids < 1) or np.any(ids > molecule_count) or np.any(ids[1:] < ids[:-1])
                    or int(ids[0]) < previous_id or np.any(starts < 0)
                    or np.any(ends < starts) or np.any(ends >= frames)
                    or ids[0] == previous_id and starts[0] <= previous_end + 1
                    or np.any((ids[1:] == ids[:-1]) & (starts[1:] <= ends[:-1] + 1))):
                raise OfficialTimelineError("invalid or nonmaximal molecule ranges")
            np.add.at(self.range_offsets, ids, 1)
            previous_id, previous_end = int(ids[-1]), int(ends[-1])
        np.cumsum(self.range_offsets, out=self.range_offsets)

        def event_counts(group: str) -> Iterator[tuple[int, int, int]]:
            previous = -1
            position = 0
            for columns in self._chunks(f"{group}/transition", f"{group}/reaction_type_id"):
                transitions, types = columns
                counts = (self.raw[f"{group}/count"][position:position + len(types)]
                          if group == "reaction_events" else np.ones(len(types), dtype=np.int64))
                if (np.any(transitions < 0) or np.any(transitions >= frames - 1)
                        or np.any(transitions[1:] < transitions[:-1]) or transitions[0] < previous
                        or np.any(types < 0) or np.any(types >= len(self.raw["reaction_types/reactant"]))
                        or np.any(counts < 1)):
                    raise OfficialTimelineError(f"invalid {group} references or counts")
                previous = int(transitions[-1])
                for transition, type_id, count in zip(
                    transitions.tolist(), types.tolist(), counts.tolist(), strict=True
                ):
                    lengths = self.columns[f"{group}/block_length"]
                    if lengths[transition] == 0:
                        self.columns[f"{group}/block_start"][transition] = position
                    lengths[transition] += 1
                    position += 1
                    yield transition, type_id, count

        def grouped(group: str) -> Iterator[tuple[int, Counter[int]]]:
            for transition, rows in groupby(event_counts(group), key=lambda row: row[0]):
                counts = Counter()
                for _, type_id, count in rows:
                    if group == "reaction_events" and type_id in counts:
                        raise OfficialTimelineError("duplicate aggregate reaction at a transition")
                    counts[type_id] += count
                yield transition, counts

        totals = Counter()
        for aggregate, exact in zip_longest(grouped("reaction_events"), grouped("transition_evidence")):
            if aggregate != exact:
                raise OfficialTimelineError("exact evidence disagrees with aggregate reaction counts")
            totals.update(aggregate[1])
        offset = 0
        for (values,) in self._chunks("reaction_types/total_count"):
            for i, total in enumerate(values.tolist(), offset):
                if total < 1 or total != totals[i]:
                    raise OfficialTimelineError("reaction type totals disagree with events")
            offset += len(values)

    def participant_present(self, molecule_id: int, frame: int) -> bool:
        left, right = map(int, self.range_offsets[molecule_id - 1:molecule_id + 1])
        starts = self.raw["molecule_ranges/start_frame"]
        begin = left
        while left < right:
            mid = (left + right) // 2
            if int(starts[mid]) <= frame:
                left = mid + 1
            else:
                right = mid
        return left > begin and (
            int(self.raw["molecule_ranges/end_frame"][left - 1]) >= frame
        )

    def validate_occurrence(
        self, row: int, transition: int, ids: np.ndarray[Any, Any],
        sides: np.ndarray[Any, Any], before: Sequence[MoleculeRow],
        after: Sequence[MoleculeRow], terms: tuple[tuple[str, ...], tuple[str, ...]],
    ) -> None:
        """Check the instance consumed by the event builder, before publication."""
        keys = [(int(side), int(molecule)) for molecule, side in zip(ids, sides, strict=True)]
        if not before or not after or len(set(keys)) != len(keys):
            raise OfficialTimelineError("duplicate or missing instance participants")
        if any(not self.participant_present(molecule, transition + side) for side, molecule in keys):
            raise OfficialTimelineError("participant is absent from its reaction frame")
        if net_reaction_key([value.species for value in before], [value.species for value in after]) != terms:
            raise OfficialTimelineError("participant species disagree with reaction type")
        atoms = []
        graphs = []
        for molecules in (before, after):
            all_atoms = [atom for value in molecules for atom in value.atom_ids]
            if len(all_atoms) != len(set(all_atoms)):
                raise OfficialTimelineError("participant atoms overlap within a side")
            atoms.append(set(all_atoms))
            graphs.append({
                (a, b): order for value in molecules for bond in value.bond_ids
                for a, b, order in [tuple(map(int, bond.split("-")))]
            })
        if atoms[0] != atoms[1]:
            raise OfficialTimelineError("participants do not conserve atoms")
        old, new = graphs
        expected = tuple(
            (a, b, old.get((a, b), 0), new.get((a, b), 0))
            for a, b in sorted(old.keys() | new.keys())
            if old.get((a, b), 0) != new.get((a, b), 0)
        )
        group = self.raw["transition_evidence"]
        start, stop = map(int, group["bond_change_offsets"][row:row + 2])
        actual = tuple(zip(*(
            group[name][start:stop].tolist() for name in
            ("bond_atom_index_1", "bond_atom_index_2", "before_order", "after_order")
        ), strict=True))
        if actual != expected:
            raise OfficialTimelineError("bond changes disagree with molecule graphs")
