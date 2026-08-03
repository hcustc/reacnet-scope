"""Select and validate time-resolved ReacNetGenerator evidence sources."""

from __future__ import annotations

import csv
import hashlib
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

import h5py
import numpy as np

from .rng_events import MoleculeRow, reaction_key


TimedEvidenceKind = Literal["native_hdf5", "legacy_csv"]


class TimedEvidenceDataError(RuntimeError):
    """Timed evidence is missing, incomplete, incompatible, or unsupported."""

    def __init__(self, message: str, *, state: str) -> None:
        super().__init__(message)
        self.state = str(state)


@dataclass(frozen=True)
class TimedEvidenceSelection:
    """One validated timed evidence source selected for a dataset."""

    kind: TimedEvidenceKind
    primary_file: str
    source_files: tuple[str, ...]
    timeline_file: str = ""
    reactionevent_file: str = ""
    molecules_file: str = ""
    schema_version: str = ""
    reaction_enabled: bool = True
    molecule_enabled: bool = False
    frame_count: int | None = None

    @property
    def capabilities(self) -> tuple[str, ...]:
        values = ["reaction"] if self.reaction_enabled else []
        if self.molecule_enabled:
            values.append("molecule")
        return tuple(values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "primary_file": self.primary_file,
            "source_files": list(self.source_files),
            "timeline_file": self.timeline_file,
            "reactionevent_file": self.reactionevent_file,
            "molecules_file": self.molecules_file,
            "schema_version": self.schema_version,
            "reaction_enabled": self.reaction_enabled,
            "molecule_enabled": self.molecule_enabled,
            "frame_count": self.frame_count,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class AggregatedReactionRecord:
    """One compressed reaction-type/count record in one transition."""

    source_row: int
    transition_index: int
    reactant: str
    product: str
    count: int

    @property
    def reaction_terms(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return reaction_key(self.reactant, self.product)


@dataclass(frozen=True)
class TransitionEvidence:
    """Storage-independent evidence for one boundary between two frames."""

    transition_index: int
    before_timestep: int
    after_timestep: int
    reactions: tuple[AggregatedReactionRecord, ...]
    before_molecules: tuple[MoleculeRow, ...] = ()
    after_molecules: tuple[MoleculeRow, ...] = ()


class NativeHdf5EvidenceAdapter:
    """Strict schema-1 streaming adapter for one native timeline source."""

    def __init__(self, selection: TimedEvidenceSelection) -> None:
        if selection.kind != "native_hdf5":
            raise TypeError("native HDF5 adapter requires native evidence")
        self.selection = selection
        self._handle: h5py.File | None = None
        self._definition_cache: OrderedDict[int, MoleculeRow] = OrderedDict()
        self._definition_cache_bytes = 0
        self._definition_state_by_id: np.ndarray[Any, Any] | None = None

    def __enter__(self) -> "NativeHdf5EvidenceAdapter":
        self._handle = h5py.File(self.selection.timeline_file, "r")
        self._load_indexes()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._handle is not None:
            self._handle.close()
        self._handle = None

    @property
    def handle(self) -> h5py.File:
        if self._handle is None:
            raise RuntimeError("native HDF5 adapter is not open")
        return self._handle

    def _load_indexes(self) -> None:
        handle = self.handle
        self.timesteps = np.asarray(handle["frames/timestep"], dtype=np.int64)
        self.reactants = tuple(str(value) for value in handle["reaction_types/reactant"].asstr()[...])
        self.products = tuple(str(value) for value in handle["reaction_types/product"].asstr()[...])
        self.block_start = np.asarray(handle["reaction_events/block_start"], dtype=np.uint64)
        self.block_length = np.asarray(handle["reaction_events/block_length"], dtype=np.uint64)
        self.event_record_count = int(handle["reaction_events/count"].shape[0])
        if np.any(self.block_start + self.block_length > self.event_record_count):
            raise TimedEvidenceDataError(
                "timeline HDF5 reaction blocks exceed event arrays",
                state="incompatible",
            )
        self.molecule_ids = np.asarray([], dtype=np.uint64)
        self.species_ids = np.asarray([], dtype=np.uint32)
        self.atom_offsets = np.asarray([], dtype=np.uint64)
        self.bond_offsets = np.asarray([], dtype=np.uint64)
        self.species_names: tuple[str, ...] = ()
        self._atom_ids_data: np.ndarray[Any, Any] | None = None
        self._bond_atoms_data: np.ndarray[Any, Any] | None = None
        self._bond_orders_data: np.ndarray[Any, Any] | None = None
        if not self.selection.molecule_enabled:
            return
        self.molecule_ids = np.asarray(
            handle["molecules/molecule_id"], dtype=np.uint64
        )
        if (
            self.molecule_ids.size
            and (
                np.any(self.molecule_ids == 0)
                or np.any(self.molecule_ids[1:] <= self.molecule_ids[:-1])
            )
        ):
            raise TimedEvidenceDataError(
                "timeline HDF5 molecule ids must be unique and sorted",
                state="incompatible",
            )
        self.species_ids = np.asarray(
            handle["molecules/species_id"], dtype=np.uint64
        )
        self.atom_offsets = np.asarray(
            handle["molecules/atom_offsets"], dtype=np.uint64
        )
        self.bond_offsets = np.asarray(
            handle["molecules/bond_offsets"], dtype=np.uint64
        )
        self.species_names = tuple(
            str(value) for value in handle["species/name"].asstr()[...]
        )
        if self.atom_offsets.size and (
            int(self.atom_offsets[-1]) != handle["molecules/atom_ids"].shape[0]
            or np.any(self.atom_offsets[1:] < self.atom_offsets[:-1])
        ):
            raise TimedEvidenceDataError(
                "timeline HDF5 molecule atom offsets are incompatible",
                state="incompatible",
            )
        if self.bond_offsets.size and (
            int(self.bond_offsets[-1]) != handle["molecules/bond_order"].shape[0]
            or np.any(self.bond_offsets[1:] < self.bond_offsets[:-1])
        ):
            raise TimedEvidenceDataError(
                "timeline HDF5 molecule bond offsets are incompatible",
                state="incompatible",
            )
        if self.species_ids.size and (
            np.any(self.species_ids == 0)
            or np.any(self.species_ids > len(self.species_names))
        ):
            raise TimedEvidenceDataError(
                "timeline HDF5 molecule species ids are incompatible",
                state="incompatible",
            )
        # HDF5 has substantial overhead for the tiny random slices needed by
        # changed molecules.  Cache only when all flattened definition arrays
        # fit under one fixed cap; larger sources remain streaming/bounded.
        definition_names = (
            "molecules/atom_ids",
            "molecules/bond_atoms",
            "molecules/bond_order",
        )
        definition_bytes = sum(
            int(np.prod(handle[name].shape)) * int(handle[name].dtype.itemsize)
            for name in definition_names
        )
        if definition_bytes <= 64 * 1024**2:
            self._atom_ids_data = np.asarray(handle[definition_names[0]])
            self._bond_atoms_data = np.asarray(handle[definition_names[1]])
            self._bond_orders_data = np.asarray(handle[definition_names[2]])

    @property
    def membership_shape(self) -> tuple[int, int]:
        if not self.selection.molecule_enabled:
            return int(self.selection.frame_count or 0), 0
        atom_ids = self.handle["molecules/atom_ids"]
        if self._atom_ids_data is not None:
            maximum = (
                int(np.max(self._atom_ids_data))
                if self._atom_ids_data.size
                else -1
            )
        else:
            maximum = -1
            for start in range(0, int(atom_ids.shape[0]), 1_000_000):
                values = np.asarray(
                    atom_ids[start : start + 1_000_000], dtype=np.uint64
                )
                if values.size:
                    maximum = max(maximum, int(np.max(values)))
        atom_count = maximum + 1
        return int(self.selection.frame_count or 0), atom_count

    @property
    def membership_dtype(self) -> np.dtype[Any]:
        maximum = int(self.molecule_ids[-1]) if self.molecule_ids.size else 0
        return np.dtype(np.uint32 if maximum <= np.iinfo(np.uint32).max else np.uint64)

    def build_membership(
        self,
        target: Path,
        *,
        start_offset: int = 0,
        resume: bool = False,
        checkpoint: Callable[[int, int], None] | None = None,
    ) -> np.memmap[Any, Any]:
        """Expand molecule ranges into a disk-backed frame/atom lookup."""

        shape = self.membership_shape
        mode = "r+" if resume else "w+"
        membership = np.memmap(
            target,
            dtype=self.membership_dtype,
            mode=mode,
            shape=shape,
        )
        range_ids = self.handle["molecule_ranges/molecule_id"]
        range_starts = self.handle["molecule_ranges/start_frame"]
        range_ends = self.handle["molecule_ranges/end_frame"]
        total = int(range_ids.shape[0])
        offset = max(0, int(start_offset))
        pending_id: int | None = None
        pending_starts: list[np.ndarray[Any, Any]] = []
        pending_ends: list[np.ndarray[Any, Any]] = []
        group_count = 0

        def apply_group(molecule_id: int, completed_offset: int) -> None:
            nonlocal group_count
            starts = np.concatenate(pending_starts).astype(np.int64, copy=False)
            ends = np.concatenate(pending_ends).astype(np.int64, copy=False)
            if (
                np.any(starts < 0)
                or np.any(ends < starts)
                or np.any(ends >= shape[0])
            ):
                raise TimedEvidenceDataError(
                    "timeline HDF5 molecule range bounds are incompatible",
                    state="incompatible",
                )
            definition_index = int(np.searchsorted(self.molecule_ids, molecule_id))
            if (
                definition_index >= self.molecule_ids.size
                or int(self.molecule_ids[definition_index]) != molecule_id
            ):
                raise TimedEvidenceDataError(
                    "timeline HDF5 molecule range refers to an unknown molecule",
                    state="incompatible",
                )
            atom_start = int(self.atom_offsets[definition_index])
            atom_end = int(self.atom_offsets[definition_index + 1])
            atoms = np.asarray(
                self.handle["molecules/atom_ids"][atom_start:atom_end],
                dtype=np.int64,
            )
            order = np.lexsort((ends, starts))
            starts = starts[order]
            ends = ends[order]
            merged: list[tuple[int, int]] = []
            for start, end in zip(starts.tolist(), ends.tolist(), strict=True):
                if merged and start <= merged[-1][1] + 1:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], end))
                else:
                    merged.append((start, end))
            for start, end in merged:
                membership[start : end + 1, atoms] = molecule_id
            group_count += 1
            if checkpoint is not None and (
                group_count % 256 == 0 or completed_offset == total
            ):
                membership.flush()
                checkpoint(completed_offset, molecule_id)

        chunk_size = 1_000_000
        while offset < total:
            chunk_end = min(offset + chunk_size, total)
            ids = np.asarray(range_ids[offset:chunk_end], dtype=np.uint64)
            starts = np.asarray(range_starts[offset:chunk_end], dtype=np.int64)
            ends = np.asarray(range_ends[offset:chunk_end], dtype=np.int64)
            if ids.size == 0 or np.any(ids == 0) or np.any(ids[1:] < ids[:-1]):
                raise TimedEvidenceDataError(
                    "timeline HDF5 molecule ranges must be grouped by molecule id",
                    state="incompatible",
                )
            boundaries = np.flatnonzero(ids[1:] != ids[:-1]) + 1
            segment_starts = np.concatenate((np.asarray([0]), boundaries))
            segment_ends = np.concatenate((boundaries, np.asarray([ids.size])))
            for segment_start, segment_end in zip(
                segment_starts.tolist(), segment_ends.tolist(), strict=True
            ):
                molecule_id = int(ids[segment_start])
                if pending_id is None:
                    pending_id = molecule_id
                elif molecule_id != pending_id:
                    if molecule_id <= pending_id:
                        raise TimedEvidenceDataError(
                            "timeline HDF5 molecule ranges must be grouped by molecule id",
                            state="incompatible",
                        )
                    apply_group(pending_id, offset + segment_start)
                    pending_id = molecule_id
                    pending_starts = []
                    pending_ends = []
                pending_starts.append(starts[segment_start:segment_end])
                pending_ends.append(ends[segment_start:segment_end])
            offset = chunk_end
        if pending_id is not None:
            apply_group(pending_id, total)
        membership.flush()
        return membership

    def _molecule(self, molecule_id: int) -> MoleculeRow:
        cached = self._definition_cache.get(molecule_id)
        if cached is not None:
            self._definition_cache.move_to_end(molecule_id)
            return cached
        index = int(np.searchsorted(self.molecule_ids, molecule_id))
        if index >= self.molecule_ids.size or int(self.molecule_ids[index]) != molecule_id:
            raise TimedEvidenceDataError(
                "timeline HDF5 frame lookup refers to an unknown molecule",
                state="incompatible",
            )
        atom_start = int(self.atom_offsets[index])
        atom_end = int(self.atom_offsets[index + 1])
        atom_source = (
            self._atom_ids_data
            if self._atom_ids_data is not None
            else self.handle["molecules/atom_ids"]
        )
        atoms = frozenset(int(value) for value in atom_source[atom_start:atom_end])
        bond_start = int(self.bond_offsets[index])
        bond_end = int(self.bond_offsets[index + 1])
        bond_atom_source = (
            self._bond_atoms_data
            if self._bond_atoms_data is not None
            else self.handle["molecules/bond_atoms"]
        )
        bond_order_source = (
            self._bond_orders_data
            if self._bond_orders_data is not None
            else self.handle["molecules/bond_order"]
        )
        bond_atoms = bond_atom_source[bond_start:bond_end]
        bond_orders = bond_order_source[bond_start:bond_end]
        bonds = tuple(
            f"{int(pair[0])}-{int(pair[1])}-{int(order)}"
            for pair, order in zip(bond_atoms, bond_orders, strict=True)
        )
        row = MoleculeRow(
            self.species_names[int(self.species_ids[index]) - 1],
            atoms,
            bonds,
        )
        self._definition_cache[molecule_id] = row
        self._definition_cache_bytes += (
            256
            + len(row.species.encode("utf-8"))
            + 40 * len(row.atom_ids)
            + sum(96 + len(value) for value in row.bond_ids)
        )
        self._definition_cache.move_to_end(molecule_id)
        while self._definition_cache_bytes > 48 * 1024**2:
            _old_id, old = self._definition_cache.popitem(last=False)
            self._definition_cache_bytes -= (
                256
                + len(old.species.encode("utf-8"))
                + 40 * len(old.atom_ids)
                + sum(96 + len(value) for value in old.bond_ids)
            )
        return row

    def _definition_states(self) -> np.ndarray[Any, Any]:
        """Map occurrence-specific molecule IDs to stable structural states."""

        if self._definition_state_by_id is not None:
            return self._definition_state_by_id
        maximum_id = int(self.molecule_ids[-1]) if self.molecule_ids.size else 0
        state_by_id = np.zeros(maximum_id + 1, dtype=np.uint32)
        fingerprints: dict[bytes, int] = {}
        atom_source = (
            self._atom_ids_data
            if self._atom_ids_data is not None
            else self.handle["molecules/atom_ids"]
        )
        bond_atom_source = (
            self._bond_atoms_data
            if self._bond_atoms_data is not None
            else self.handle["molecules/bond_atoms"]
        )
        bond_order_source = (
            self._bond_orders_data
            if self._bond_orders_data is not None
            else self.handle["molecules/bond_order"]
        )
        next_state = 1
        for index, raw_id in enumerate(self.molecule_ids):
            atom_start = int(self.atom_offsets[index])
            atom_end = int(self.atom_offsets[index + 1])
            atoms = np.sort(
                np.asarray(atom_source[atom_start:atom_end], dtype="<u8")
            )
            bond_start = int(self.bond_offsets[index])
            bond_end = int(self.bond_offsets[index + 1])
            pairs = np.asarray(
                bond_atom_source[bond_start:bond_end], dtype="<u8"
            ).reshape((-1, 2))
            orders = np.asarray(
                bond_order_source[bond_start:bond_end], dtype="<i8"
            )
            if pairs.size:
                pairs = np.sort(pairs, axis=1)
                bond_order = np.lexsort((orders, pairs[:, 1], pairs[:, 0]))
                pairs = pairs[bond_order]
                orders = orders[bond_order]
            digest = hashlib.blake2b(digest_size=16)
            digest.update(int(self.species_ids[index]).to_bytes(8, "little"))
            digest.update(len(atoms).to_bytes(8, "little"))
            digest.update(atoms.tobytes())
            digest.update(len(orders).to_bytes(8, "little"))
            digest.update(pairs.tobytes())
            digest.update(orders.tobytes())
            fingerprint = digest.digest()
            state_id = fingerprints.get(fingerprint)
            if state_id is None:
                state_id = next_state
                fingerprints[fingerprint] = state_id
                next_state += 1
            state_by_id[int(raw_id)] = state_id
        self._definition_state_by_id = state_by_id
        return state_by_id

    def transition(
        self,
        transition_index: int,
        membership: np.memmap[Any, Any] | None,
    ) -> TransitionEvidence:
        index = int(transition_index)
        start = int(self.block_start[index])
        length = int(self.block_length[index])
        stop = start + length
        transition_ids = np.asarray(
            self.handle["reaction_events/transition_index"][start:stop],
            dtype=np.int64,
        )
        if transition_ids.size and np.any(transition_ids != index):
            raise TimedEvidenceDataError(
                "timeline HDF5 reaction block transition ids are incompatible",
                state="incompatible",
            )
        reaction_ids = np.asarray(
            self.handle["reaction_events/reaction_id"][start:stop],
            dtype=np.int64,
        )
        counts = np.asarray(
            self.handle["reaction_events/count"][start:stop],
            dtype=np.int64,
        )
        if (
            np.any(reaction_ids < 1)
            or np.any(reaction_ids > len(self.reactants))
            or np.any(counts < 1)
        ):
            raise TimedEvidenceDataError(
                "timeline HDF5 reaction record values are incompatible",
                state="incompatible",
            )
        reactions = tuple(
            AggregatedReactionRecord(
                source_row=start + position + 1,
                transition_index=index,
                reactant=self.reactants[int(reaction_id) - 1],
                product=self.products[int(reaction_id) - 1],
                count=int(count),
            )
            for position, (reaction_id, count) in enumerate(
                zip(reaction_ids, counts, strict=True)
            )
        )
        before: tuple[MoleculeRow, ...] = ()
        after: tuple[MoleculeRow, ...] = ()
        if membership is not None and membership.shape[1]:
            states = self._definition_states()
            before_state = states[membership[index]]
            after_state = states[membership[index + 1]]
            changed = before_state != after_state
            before_ids = np.unique(membership[index, changed])
            after_ids = np.unique(membership[index + 1, changed])
            before = tuple(self._molecule(int(value)) for value in before_ids if value)
            after = tuple(self._molecule(int(value)) for value in after_ids if value)
        return TransitionEvidence(
            transition_index=index,
            before_timestep=int(self.timesteps[index]),
            after_timestep=int(self.timesteps[index + 1]),
            reactions=reactions,
            before_molecules=before,
            after_molecules=after,
        )

    def iter_transitions(
        self,
        membership: np.memmap[Any, Any] | None,
        *,
        start: int = 0,
    ) -> Iterator[TransitionEvidence]:
        for index in range(max(0, int(start)), len(self.timesteps) - 1):
            yield self.transition(index, membership)


def _attribute_text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _require_dataset(
    handle: h5py.File,
    name: str,
    *,
    shape: tuple[int | None, ...] | None = None,
) -> h5py.Dataset:
    value = handle.get(name)
    if not isinstance(value, h5py.Dataset):
        raise TimedEvidenceDataError(
            f"timeline HDF5 required dataset is missing: {name}",
            state="incomplete",
        )
    if shape is not None:
        if len(value.shape) != len(shape) or any(
            expected is not None and actual != expected
            for actual, expected in zip(value.shape, shape, strict=True)
        ):
            raise TimedEvidenceDataError(
                f"timeline HDF5 dataset shape is incompatible: {name}",
                state="incompatible",
            )
    return value


def _inspect_native_timeline(path: Path) -> TimedEvidenceSelection:
    try:
        with h5py.File(path, "r") as handle:
            schema_version = _attribute_text(
                handle.attrs.get("schema_version", "")
            )
            if schema_version != "1":
                raise TimedEvidenceDataError(
                    f"timeline HDF5 schema is incompatible: {schema_version or 'missing'}",
                    state="incompatible",
                )
            status = _attribute_text(handle.attrs.get("status", ""))
            if status != "complete":
                raise TimedEvidenceDataError(
                    f"timeline HDF5 is not complete: {status or 'missing status'}",
                    state="incomplete",
                )
            try:
                frame_count = int(handle.attrs["frame_count"])
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise TimedEvidenceDataError(
                    "timeline HDF5 frame_count is incompatible",
                    state="incompatible",
                ) from exc
            if frame_count < 1:
                raise TimedEvidenceDataError(
                    "timeline HDF5 frame_count is incompatible",
                    state="incompatible",
                )

            source_paths = _require_dataset(handle, "sources/path")
            source_ordinals = _require_dataset(handle, "sources/ordinal")
            if source_paths.shape != source_ordinals.shape:
                raise TimedEvidenceDataError(
                    "timeline HDF5 source arrays are incompatible",
                    state="incompatible",
                )
            if source_paths.shape != (1,):
                raise TimedEvidenceDataError(
                    "timeline HDF5 multiple sources are not supported",
                    state="unsupported",
                )
            for name in (
                "frames/source_id",
                "frames/source_frame",
                "frames/timestep",
            ):
                _require_dataset(handle, name, shape=(frame_count,))

            reaction_enabled = bool(
                handle.attrs.get("reaction_enabled", False)
            )
            molecule_enabled = bool(
                handle.attrs.get("molecule_enabled", False)
            )
            if not reaction_enabled:
                raise TimedEvidenceDataError(
                    "timeline HDF5 does not contain reaction evidence",
                    state="missing",
                )

            reactants = _require_dataset(handle, "reaction_types/reactant")
            products = _require_dataset(handle, "reaction_types/product")
            totals = _require_dataset(handle, "reaction_types/total_count")
            if not (reactants.shape == products.shape == totals.shape):
                raise TimedEvidenceDataError(
                    "timeline HDF5 reaction type arrays are incompatible",
                    state="incompatible",
                )
            _require_dataset(
                handle,
                "reaction_events/block_start",
                shape=(frame_count - 1,),
            )
            _require_dataset(
                handle,
                "reaction_events/block_length",
                shape=(frame_count - 1,),
            )
            event_shapes = {
                _require_dataset(handle, name).shape
                for name in (
                    "reaction_events/transition_index",
                    "reaction_events/reaction_id",
                    "reaction_events/count",
                )
            }
            if len(event_shapes) != 1:
                raise TimedEvidenceDataError(
                    "timeline HDF5 reaction event arrays are incompatible",
                    state="incompatible",
                )

            if molecule_enabled:
                species = _require_dataset(handle, "species/name")
                molecule_ids = _require_dataset(
                    handle, "molecules/molecule_id"
                )
                species_ids = _require_dataset(
                    handle, "molecules/species_id"
                )
                if molecule_ids.shape != species_ids.shape:
                    raise TimedEvidenceDataError(
                        "timeline HDF5 molecule arrays are incompatible",
                        state="incompatible",
                    )
                molecule_count = molecule_ids.shape[0]
                atom_offsets = _require_dataset(
                    handle,
                    "molecules/atom_offsets",
                    shape=(molecule_count + 1,),
                )
                atom_ids = _require_dataset(handle, "molecules/atom_ids")
                bond_offsets = _require_dataset(
                    handle,
                    "molecules/bond_offsets",
                    shape=(molecule_count + 1,),
                )
                bond_atoms = _require_dataset(handle, "molecules/bond_atoms")
                bond_orders = _require_dataset(handle, "molecules/bond_order")
                if len(atom_offsets.shape) != 1 or len(atom_ids.shape) != 1:
                    raise TimedEvidenceDataError(
                        "timeline HDF5 molecule atom arrays are incompatible",
                        state="incompatible",
                    )
                if (
                    bond_atoms.ndim != 2
                    or bond_atoms.shape[1] != 2
                    or bond_atoms.shape[0] != bond_orders.shape[0]
                    or len(bond_offsets.shape) != 1
                ):
                    raise TimedEvidenceDataError(
                        "timeline HDF5 molecule bond arrays are incompatible",
                        state="incompatible",
                    )
                range_shapes = {
                    _require_dataset(handle, name).shape
                    for name in (
                        "molecule_ranges/molecule_id",
                        "molecule_ranges/start_frame",
                        "molecule_ranges/end_frame",
                    )
                }
                if len(range_shapes) != 1 or species.ndim != 1:
                    raise TimedEvidenceDataError(
                        "timeline HDF5 molecule range arrays are incompatible",
                        state="incompatible",
                    )
    except TimedEvidenceDataError:
        raise
    except (OSError, ValueError) as exc:
        raise TimedEvidenceDataError(
            f"timeline HDF5 is invalid: {exc}",
            state="invalid",
        ) from exc

    resolved = str(path.resolve())
    return TimedEvidenceSelection(
        kind="native_hdf5",
        primary_file=resolved,
        source_files=(resolved,),
        timeline_file=resolved,
        schema_version=schema_version,
        reaction_enabled=True,
        molecule_enabled=molecule_enabled,
        frame_count=frame_count,
    )


def _legacy_fields(path: Path, required: set[str], label: str) -> None:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            fields = set(csv.DictReader(handle).fieldnames or [])
    except (OSError, UnicodeError, csv.Error) as exc:
        raise TimedEvidenceDataError(
            f"{label} CSV is invalid: {exc}", state="invalid"
        ) from exc
    if not required.issubset(fields):
        raise TimedEvidenceDataError(
            f"{label} CSV columns are incompatible", state="incompatible"
        )


def select_timed_evidence(
    *,
    timeline_file: str = "",
    reactionevent_file: str = "",
    molecules_file: str = "",
) -> TimedEvidenceSelection:
    """Select native HDF5 evidence first, falling back only when it is absent."""

    timeline = Path(str(timeline_file or "")).expanduser()
    if str(timeline_file or "").strip() and timeline.is_file():
        return _inspect_native_timeline(timeline)

    reaction = Path(str(reactionevent_file or "")).expanduser()
    if not str(reactionevent_file or "").strip() or not reaction.is_file():
        raise TimedEvidenceDataError(
            "timed reaction evidence is missing", state="missing"
        )
    _legacy_fields(
        reaction,
        {"Timestep_Index", "Reactant", "Product"},
        "reactionevent",
    )
    molecule_text = str(molecules_file or "").strip()
    molecule = Path(molecule_text).expanduser() if molecule_text else None
    molecule_enabled = bool(molecule is not None and molecule.is_file())
    if molecule_enabled and molecule is not None:
        _legacy_fields(
            molecule,
            {"Timestep", "Species", "AtomIDs", "BondIDs"},
            "molecules",
        )
    reaction_resolved = str(reaction.resolve())
    molecule_resolved = (
        str(molecule.resolve())
        if molecule_enabled and molecule is not None
        else ""
    )
    return TimedEvidenceSelection(
        kind="legacy_csv",
        primary_file=reaction_resolved,
        source_files=tuple(
            value
            for value in (reaction_resolved, molecule_resolved)
            if value
        ),
        reactionevent_file=reaction_resolved,
        molecules_file=molecule_resolved,
        reaction_enabled=True,
        molecule_enabled=molecule_enabled,
    )


def source_signatures(
    selection: TimedEvidenceSelection,
) -> dict[str, dict[str, int | str]]:
    """Return stable stat signatures for all files in one selection."""

    signatures: dict[str, dict[str, int | str]] = {}
    for label, path in (
        ("timeline", selection.timeline_file),
        ("reactionevent", selection.reactionevent_file),
        ("molecules", selection.molecules_file),
    ):
        if not path:
            continue
        stat = os.stat(path)
        signatures[label] = {
            "path": os.path.abspath(path),
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
        }
    return signatures


def native_membership_bytes(selection: TimedEvidenceSelection) -> int:
    """Estimate the exact disk-backed molecule-membership allocation."""

    if selection.kind != "native_hdf5" or not selection.molecule_enabled:
        return 0
    with NativeHdf5EvidenceAdapter(selection) as adapter:
        return int(np.prod(adapter.membership_shape)) * int(
            adapter.membership_dtype.itemsize
        )
