"""Indexed, single-dataset Candidate discovery; no occurrence-chain inference.

The adjacency is published with the event index. Querying it never reads raw
RNG evidence or materializes the global network. Legacy candidate CLI scoring
is intentionally not used by this versioned, shortest-first API.
"""
from __future__ import annotations

from collections import deque
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .indexes import IndexInvalidError
from .network import smiles_to_formula_fast
from .rng_events import reaction_key

SCHEMA = 'reacnet-scope/indexed-candidates/v1'
ADJACENCY_VERSION = '1'


@lru_cache(maxsize=4096)
def candidate_formula(species: str) -> str:
    """Display/search attribute only; never normalize the source identity."""
    from rdkit import Chem
    from rdkit.Chem.rdMolDescriptors import CalcMolFormula
    from rdkit import rdBase
    with rdBase.BlockLogs():
        mol = Chem.MolFromSmiles(species)
    return str(CalcMolFormula(mol)) if mol is not None else smiles_to_formula_fast(species)


def materialize_candidate_adjacency(connection: sqlite3.Connection) -> None:
    """Offline, streaming derivation inside the unpublished event-index build."""
    connection.executescript('''
        DROP TABLE IF EXISTS candidate_adjacency;
        DROP TABLE IF EXISTS candidate_reactions;
        DROP TABLE IF EXISTS candidate_species;
        CREATE TABLE candidate_species(
            species TEXT PRIMARY KEY, formula TEXT NOT NULL);
        CREATE INDEX candidate_species_formula ON candidate_species(formula,species);
        CREATE TABLE candidate_reactions(
            reaction_key TEXT PRIMARY KEY, reactants TEXT NOT NULL,
            products TEXT NOT NULL, total_events INTEGER NOT NULL);
        CREATE TABLE candidate_adjacency(
            species TEXT NOT NULL, reaction_key TEXT NOT NULL,
            PRIMARY KEY(species,reaction_key));
    ''')
    # Iterate the published-direction summaries, never infer a reverse edge.
    for key, count in connection.execute(
        'SELECT reaction_key,total_events FROM reaction_summary WHERE total_events>0 ORDER BY reaction_key'
    ):
        left, right = key.split('->', 1)
        reactants, products = reaction_key(left, right)
        connection.execute('INSERT INTO candidate_reactions VALUES(?,?,?,?)',
                           (key, json.dumps(reactants), json.dumps(products), count))
        for species in set((*reactants, *products)):
            connection.execute('INSERT OR IGNORE INTO candidate_species VALUES(?,?)',
                               (species, candidate_formula(species)))
        connection.executemany('INSERT INTO candidate_adjacency VALUES(?,?)',
                               ((species, key) for species in set(reactants)))
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                       ('candidate_adjacency_version', ADJACENCY_VERSION))


class CandidateReader:
    """One validated, read-only index snapshot for a bounded query."""
    def __init__(self, opened: dict[str, Any]):
        self.opened = opened
        self.connection = sqlite3.connect(Path(opened['index_path']).as_uri() + '?mode=ro', uri=True)
        try:
            version = self.connection.execute(
                "SELECT value FROM meta WHERE key='candidate_adjacency_version'"
            ).fetchone()
            if not version or version[0] != ADJACENCY_VERSION:
                raise IndexInvalidError('候选路径索引尚未准备；请在数据集中重建事件索引。')
            self.connection.execute('SELECT species FROM candidate_species LIMIT 0')
            self.connection.execute('SELECT reaction_key FROM candidate_adjacency LIMIT 0')
            self.connection.execute('SELECT reaction_key,reactants,products,total_events FROM candidate_reactions LIMIT 0')
            if not self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='candidate_species_formula'"
            ).fetchone():
                raise IndexInvalidError('候选结构检索索引缺失；请重建事件索引。')
        except Exception:
            self.connection.close()
            raise

    def close(self) -> None:
        self.connection.close()

    def species(self, query: str, limit: int = 100) -> dict[str, Any]:
        text = str(query).strip()
        if not text:
            return {'rows': [], 'has_more': False}
        rows = self.connection.execute('''
            SELECT species,formula FROM candidate_species WHERE species=?
            UNION SELECT species,formula FROM candidate_species WHERE formula=?
            ORDER BY species LIMIT ?
        ''', (text, text, limit + 1)).fetchall()
        return {'rows': [{'species': s, 'formula': f} for s, f in rows[:limit]],
                'has_more': len(rows) > limit}

    def contains(self, species: str) -> bool:
        return self.connection.execute('SELECT 1 FROM candidate_species WHERE species=?',
                                       (species,)).fetchone() is not None

    def outgoing(self, species: str, limit: int) -> list[dict[str, Any]]:
        rows = self.connection.execute('''
            SELECT r.reaction_key,r.reactants,r.products,r.total_events
            FROM candidate_adjacency a JOIN candidate_reactions r
            ON r.reaction_key=a.reaction_key WHERE a.species=?
            ORDER BY a.reaction_key LIMIT ?
        ''', (species, limit)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n) for k, a, b, n in rows]


def discover_indexed_candidates(reader: CandidateReader, start: str, *, target: str = '',
                                mode: str = 'target', max_steps: int = 4,
                                max_paths: int = 20, max_expansions: int = 2000,
                                max_frontier: int = 5000, max_seconds: float = 5) -> dict[str, Any]:
    if mode not in {'target', 'explore'}:
        raise ValueError('请选择起点到目标或从起点探索')
    for name, value, upper in [('max_steps', max_steps, 8), ('max_paths', max_paths, 100),
                               ('max_expansions', max_expansions, 20000),
                               ('max_frontier', max_frontier, 20000)]:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f'{name} 必须是 1–{upper} 的整数')
    if not 0 < max_seconds <= 30:
        raise ValueError('查询时间预算必须在 0–30 秒之间')
    if not start or not reader.contains(start):
        raise ValueError('请选择当前数据集中的精确起始结构')
    if mode == 'target' and (not target or not reader.contains(target) or target == start):
        raise ValueError('请选择与起点不同的精确目标结构')
    target = target if mode == 'target' else ''
    queue = deque([([start], [])])
    paths: list[dict[str, Any]] = []
    cycles: list[dict[str, str]] = []
    cache: dict[str, list[dict[str, Any]]] = {}
    reasons: set[str] = set()
    cycle_count = 0
    expansions = 0
    edges_read = 0
    horizon_limited = False
    deadline = time.monotonic() + max_seconds
    while queue:
        if time.monotonic() >= deadline:
            reasons.add('time_budget'); break
        species, steps = queue.popleft()
        if steps and (mode == 'explore' or species[-1] == target):
            if len(paths) >= max_paths:
                reasons.add('result_limit'); break
            identity = {'version': 1, 'species': species,
                        'reactions': [s['reaction_key'] for s in steps]}
            signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
            paths.append(dict(signature_id=signature, species=species, steps=steps,
                              step_count=len(steps), continuous_md='not_evaluated'))
            if mode == 'target':
                continue
        if len(steps) >= max_steps:
            horizon_limited = True
            continue
        if expansions >= max_expansions:
            reasons.add('expansion_budget'); break
        expansions += 1
        focal = species[-1]
        if focal not in cache:
            # A hub cannot allocate an unbounded local edge list.
            allowance = min(256, max_expansions - edges_read)
            if allowance <= 0:
                reasons.add('adjacency_budget'); break
            neighbors = reader.outgoing(focal, allowance + 1)
            if len(neighbors) > allowance:
                reasons.add('adjacency_budget')
            cache[focal] = neighbors[:allowance]
            edges_read += len(cache[focal])
        for reaction in cache[focal]:
            for product in sorted(set(reaction['products'])):
                if product in species:
                    cycle_count += 1
                    if len(cycles) < 100:
                        cycles.append(dict(species=product, reaction_key=reaction['reaction_key'],
                                           prefix_species=list(species), prefix_reactions=[s['reaction_key'] for s in steps]))
                    continue
                if len(queue) >= max_frontier:
                    reasons.add('frontier_budget'); break
                step = dict(reaction, carried_from=focal, carried_to=product)
                queue.append((species + [product], steps + [step]))
    return dict(schema_version=SCHEMA, query=dict(start=start, target=target, mode=mode,
                max_steps=max_steps, max_paths=max_paths, max_expansions=max_expansions,
                max_frontier=max_frontier, max_seconds=max_seconds), paths=paths,
                status=('truncated/inconclusive' if reasons else 'found' if paths else 'not_found_within_constraints'),
                query_complete=not reasons, graph_exhaustive=not reasons and not horizon_limited,
                horizon_limited=horizon_limited, truncation_reasons=sorted(reasons),
                expansions=expansions, adjacency_rows_read=edges_read,
                cycle_closures=cycles, cycle_closures_limit=100,
                cycle_closures_seen=cycle_count, cycle_closures_truncated=cycle_count > len(cycles),
                ordering='step_count_then_exact_identity',
                semantics='Each step independently observed; no continuous chain claimed.')
