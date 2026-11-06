"""Query-only constraints on a Candidate's carried Species sequence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


def _species_list(values: Sequence[str] | None, label: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f'{label}必须是精确 Species 标签的列表')
    if len(values) > 100:
        raise ValueError(f'{label}最多指定 100 个精确物种')
    if any(not isinstance(value, str) or not value
           or any(character.isspace() or ord(character) < 32 for character in value)
           for value in values):
        raise ValueError(f'{label}必须使用非空、无空白的精确 Species 标签')
    return tuple(values)


@dataclass(frozen=True)
class RouteConstraints:
    via_species: tuple[str, ...] = ()
    excluded_species: tuple[str, ...] = ()

    @classmethod
    def validate(cls, via_species: Sequence[str] | None,
                 excluded_species: Sequence[str] | None, *, start: str,
                 target: str, mode: str, contains: Callable[[str], bool]) -> RouteConstraints:
        via = _species_list(via_species, '途经物种')
        excluded = tuple(sorted(set(_species_list(excluded_species, '排除主线物种'))))
        if via and mode != 'target':
            raise ValueError('途经物种仅用于起点到终点搜索')
        if len(set(via)) != len(via):
            raise ValueError('途经物种不能重复；普通候选主线不能重复经过同一物种')
        anchors = {value for value in (start, target) if value}
        if anchors.intersection(via):
            raise ValueError('途经物种必须是起点和终点之外的中间物种')
        if (anchors | set(via)).intersection(excluded):
            raise ValueError('起点、终点和途经物种不能同时被排除')
        for species in dict.fromkeys((*via, *excluded)):
            if not contains(species):
                raise ValueError(f'约束物种不在当前候选索引中，请选择精确结构：{species}')
        return cls(via, excluded)

    def as_query(self) -> dict[str, list[str]]:
        return dict(via_species=list(self.via_species),
                    excluded_species=list(self.excluded_species))

    def advance(self, product: str, progress: int, target: str) -> int | None:
        """Advance one complete route prefix; never join independently found legs."""
        if product == target and progress != len(self.via_species):
            return None
        if product in self.via_species:
            if progress >= len(self.via_species) or product != self.via_species[progress]:
                return None
            return progress + 1
        return progress
