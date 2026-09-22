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
from .candidate_evidence import materialize_candidate_evidence, quality_summary

SCHEMA = 'reacnet-scope/indexed-candidates/v3'
ADJACENCY_VERSION = '4'
ATOM_TRANSFER_POLICY = 'event_local_dominant_atom_descendant'
NETWORK_ONLY_POLICY = 'species_connectivity_only'


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
        DROP TABLE IF EXISTS candidate_transfer_events;
        DROP TABLE IF EXISTS candidate_transfers;
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
        CREATE TABLE candidate_transfers(
            source_species TEXT NOT NULL, product_species TEXT NOT NULL,
            reaction_key TEXT NOT NULL, supporting_events INTEGER NOT NULL,
            max_shared_atoms INTEGER NOT NULL,
            PRIMARY KEY(source_species,product_species,reaction_key));
        CREATE INDEX candidate_transfers_target
            ON candidate_transfers(source_species,product_species,reaction_key);
        CREATE TABLE candidate_transfer_events(
            source_species TEXT NOT NULL, product_species TEXT NOT NULL,
            reaction_key TEXT NOT NULL, event_id TEXT NOT NULL,
            shared_atoms INTEGER NOT NULL,
            PRIMARY KEY(source_species,product_species,reaction_key,event_id));
        CREATE INDEX candidate_transfer_events_lookup
            ON candidate_transfer_events(
                source_species,product_species,reaction_key,event_id);
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
    event_columns = {
        str(row[1]) for row in connection.execute('PRAGMA table_info(events)')
    }
    required = {'event_id', 'reaction_key', 'association_status',
                'reactant_participants_json', 'product_participants_json'}
    matched_events = 0
    if required <= event_columns:
        cursor = connection.execute('''
            SELECT event_id,reaction_key,reactant_participants_json,
                   product_participants_json
            FROM events WHERE association_status='matched'
            ORDER BY event_id
        ''')
        for event_id, key, reactant_json, product_json in cursor:
            try:
                reactants = json.loads(reactant_json)
                products = json.loads(product_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(reactants, list) or not isinstance(products, list):
                continue
            event_transfers: dict[tuple[str, str, str], int] = {}
            for reactant in reactants:
                if not isinstance(reactant, dict):
                    continue
                source = str(reactant.get('species') or '')
                source_atoms = {
                    int(value) for value in reactant.get('atom_ids') or []
                    if isinstance(value, int) and not isinstance(value, bool)
                }
                if not source or not source_atoms:
                    continue
                overlaps: list[tuple[str, int]] = []
                for product in products:
                    if not isinstance(product, dict):
                        continue
                    target = str(product.get('species') or '')
                    target_atoms = {
                        int(value) for value in product.get('atom_ids') or []
                        if isinstance(value, int) and not isinstance(value, bool)
                    }
                    if target:
                        overlaps.append((target, len(source_atoms & target_atoms)))
                maximum = max((count for _, count in overlaps), default=0)
                if maximum <= 0:
                    continue
                for target, count in overlaps:
                    if count == maximum:
                        transfer = (source, target, str(key))
                        event_transfers[transfer] = max(
                            event_transfers.get(transfer, 0), count
                        )
            if event_transfers:
                matched_events += 1
            for (source, target, reaction), shared in event_transfers.items():
                connection.execute('''
                    INSERT INTO candidate_transfers VALUES(?,?,?,1,?)
                    ON CONFLICT(source_species,product_species,reaction_key)
                    DO UPDATE SET
                        supporting_events=supporting_events+1,
                        max_shared_atoms=MAX(max_shared_atoms,excluded.max_shared_atoms)
                ''', (source, target, reaction, shared))
                connection.execute('''
                    INSERT INTO candidate_transfer_events VALUES(?,?,?,?,?)
                ''', (source, target, reaction, str(event_id), shared))
    materialize_candidate_evidence(connection)
    policy = ATOM_TRANSFER_POLICY if matched_events else NETWORK_ONLY_POLICY
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                       ('candidate_adjacency_version', ADJACENCY_VERSION))
    connection.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',
                       ('candidate_carrier_policy', policy))


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
            self.connection.execute('SELECT reaction_key FROM candidate_transfers LIMIT 0')
            self.connection.execute('SELECT event_id FROM candidate_transfer_events LIMIT 0')
            self.connection.execute('SELECT event_id FROM candidate_event_history LIMIT 0')
            self.connection.execute('SELECT event_count FROM candidate_quality_counts LIMIT 0')
            self.connection.execute('SELECT event_id FROM candidate_carrier_pairs LIMIT 0')
            self.connection.execute('SELECT reaction_key,reactants,products,total_events FROM candidate_reactions LIMIT 0')
            if not self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='candidate_species_formula'"
            ).fetchone():
                raise IndexInvalidError('候选结构检索索引缺失；请重建事件索引。')
            policy = self.connection.execute(
                "SELECT value FROM meta WHERE key='candidate_carrier_policy'"
            ).fetchone()
            self.carrier_policy = str(policy[0]) if policy else NETWORK_ONLY_POLICY
            self.quality_view = 'persistent'
            self.return_window_frames = 3
            self.return_basis = 'topology'
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
        if self.carrier_policy == ATOM_TRANSFER_POLICY:
            return self._transfers(species, limit)
        rows = self.connection.execute('''
            SELECT r.reaction_key,r.reactants,r.products,r.total_events
            FROM candidate_adjacency a JOIN candidate_reactions r
            ON r.reaction_key=a.reaction_key WHERE a.species=?
            ORDER BY a.reaction_key LIMIT ?
        ''', (species, limit)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n, carried_products=sorted(set(json.loads(b))),
                     transfer_event_count=n, max_shared_atoms=None,
                     transfer_basis=NETWORK_ONLY_POLICY) for k, a, b, n in rows]

    def outgoing_to(self, species: str, target: str, limit: int) -> list[dict[str, Any]]:
        """Probe an exact final step within one indexed reactant neighborhood.

        Filtering precedes LIMIT so a target after a hub's first page is eligible.
        The search installs a SQLite deadline handler, including JSON filtering.
        """
        if self.carrier_policy == ATOM_TRANSFER_POLICY:
            return self._transfers(species, limit, target)
        rows = self.connection.execute('''
            SELECT r.reaction_key,r.reactants,r.products,r.total_events
            FROM candidate_adjacency a JOIN candidate_reactions r
            ON r.reaction_key=a.reaction_key WHERE a.species=?
            AND EXISTS (SELECT 1 FROM json_each(r.products) WHERE value=?)
            ORDER BY a.reaction_key LIMIT ?
        ''', (species, target, limit)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n, carried_products=[target], transfer_event_count=n,
                     max_shared_atoms=None, transfer_basis=NETWORK_ONLY_POLICY)
                for k, a, b, n in rows]

    def _transfers(self, species, limit, target=None):
        # Filter before LIMIT so folded edges cannot hide retained neighbours.
        sql = '''SELECT r.reaction_key,r.reactants,r.products,r.total_events,
                 t.product_species,t.supporting_events,t.max_shared_atoms
            FROM candidate_transfers t JOIN candidate_reactions r USING(reaction_key)
            WHERE t.source_species=?'''
        params = [species]
        if target is not None:
            sql += ' AND t.product_species=?'
            params.append(target)
        if self.quality_view == 'persistent':
            kinds = "('exact','topology')" if self.return_basis == 'topology' else "('exact')"
            sql += f''' AND t.supporting_events > COALESCE((SELECT SUM(q.event_count)
                FROM candidate_quality_counts q WHERE q.source_species=t.source_species
                AND q.product_species=t.product_species AND q.reaction_key=t.reaction_key
                AND ((q.prior_kind IN {kinds} AND q.prior_gap BETWEEN 1 AND ?)
                  OR (q.return_kind IN {kinds} AND q.return_gap BETWEEN 1 AND ?))),0)'''
            params.extend([self.return_window_frames] * 2)
        sql += ' ORDER BY r.reaction_key,t.product_species LIMIT ?'
        params.append(limit)
        result = []
        for key, left, right, total, product, support, shared in self.connection.execute(sql, params).fetchall():
            quality = quality_summary(self.connection, species, product, key,
                                      self.return_window_frames, self.return_basis)
            quality['retained_events'] = support - quality['folded_events']
            result.append(dict(reaction_key=key, reactants=json.loads(left), products=json.loads(right),
                               event_count=total, carried_products=[product], transfer_event_count=support,
                               max_shared_atoms=shared, transfer_basis=ATOM_TRANSFER_POLICY, quality=quality))
        return result


def discover_indexed_candidates(reader: CandidateReader, start: str, *, target: str = '',
                                mode: str = 'target', max_steps: int = 4,
                                max_paths: int = 20, max_expansions: int = 2000,
                                max_frontier: int = 5000, max_seconds: float = 5,
                                quality_view: str = 'persistent', return_window_frames: int = 3,
                                return_basis: str = 'topology') -> dict[str, Any]:
    if mode not in {'target', 'explore'}:
        raise ValueError('请选择起点到目标或从起点探索')
    if quality_view not in {'persistent', 'raw'} or return_basis not in {'exact', 'topology'}:
        raise ValueError('请选择原始/往返折叠视图和精确键级/连接关系返回规则')
    if isinstance(return_window_frames, bool) or not isinstance(return_window_frames, int) or not 1 <= return_window_frames <= 100:
        raise ValueError('往返窗口必须为 1–100 个分析帧间隔')
    reader.quality_view = quality_view
    reader.return_window_frames = return_window_frames
    reader.return_basis = return_basis
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
    target_cache: dict[str, list[dict[str, Any]]] = {}
    target_rows_read = 0
    prefixes_examined = 0
    distance_to_target: dict[str, int] = {}

    def emit(species, steps):
        if len(paths) >= max_paths:
            reasons.add('result_limit')
            return False
        identity = {'version': 2, 'species': species,
                    'reactions': [s['reaction_key'] for s in steps]}
        signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        paths.append(dict(signature_id=signature, species=species, steps=steps,
                          step_count=len(steps), continuous_md='not_evaluated',
                          quality_warning=any(s.get('quality', {}).get('folded_events', 0) for s in steps)))
        return True

    reader.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        if mode == 'target':
            # Read each reachable species neighborhood once. Spending this
            # budget on path prefixes starves deeper layers in reconvergent
            # networks even when the local graph itself is small.
            pending = deque([(start, 0)])
            seen = {start}
            while pending:
                if time.monotonic() >= deadline:
                    reasons.add('time_budget'); break
                focal, depth = pending.popleft()
                if len(target_cache) >= max_expansions:
                    reasons.add('target_probe_budget')
                    continue
                target_cache[focal] = reader.outgoing_to(focal, target, max_paths + 1)
                target_rows_read += len(target_cache[focal])
                if depth == max_steps - 1:
                    horizon_limited = True
                    continue
                if expansions >= max_expansions:
                    reasons.add('expansion_budget')
                    continue
                expansions += 1
                allowance = max_expansions - edges_read
                if allowance <= 0:
                    reasons.add('adjacency_budget')
                    continue
                neighbors = reader.outgoing(focal, allowance + 1)
                if len(neighbors) > allowance:
                    reasons.add('adjacency_budget')
                cache[focal] = neighbors[:allowance]
                edges_read += len(cache[focal])
                for reaction in cache[focal]:
                    for product in reaction['carried_products']:
                        if product == target or product in seen:
                            continue
                        if len(pending) >= max_frontier:
                            reasons.add('frontier_budget')
                            continue
                        seen.add(product)
                        pending.append((product, depth + 1))
            # Lower bounds in the retrieved graph safely prune enumeration.
            # If graph retrieval was truncated its reasons remain in the report;
            # absence from this subgraph never proves global unreachability.
            incoming: dict[str, set[str]] = {}
            for focal, neighbors in cache.items():
                for reaction in neighbors:
                    for product in reaction['carried_products']:
                        incoming.setdefault(product, set()).add(focal)
            for focal, neighbors in target_cache.items():
                if neighbors:
                    incoming.setdefault(target, set()).add(focal)
            distance_to_target = {target: 0}
            pending_targets = deque([target])
            while pending_targets:
                product = pending_targets.popleft()
                for focal in sorted(incoming.get(product, ())):
                    if focal not in distance_to_target:
                        distance_to_target[focal] = distance_to_target[product] + 1
                        pending_targets.append(focal)
            if start not in distance_to_target:
                queue.clear()
        while queue:
            if time.monotonic() >= deadline:
                reasons.add('time_budget'); break
            if mode == 'target' and prefixes_examined >= max_expansions * max_steps:
                reasons.add('path_enumeration_budget'); break
            prefixes_examined += 1
            species, steps = queue.popleft()
            focal = species[-1]
            if mode == 'explore' and steps and not emit(species, steps):
                break
            if len(steps) >= max_steps:
                horizon_limited = True
                continue
            if mode == 'target':
                # Close each BFS prefix against the target before spending the
                # remaining expansion budget on unrelated product branches.
                for reaction in target_cache.get(focal, []):
                    step = dict(reaction, carried_from=focal, carried_to=target)
                    if not emit(species + [target], steps + [step]):
                        break
                if 'result_limit' in reasons:
                    break
                if len(steps) == max_steps - 1:
                    horizon_limited = True
                    continue
            if mode == 'explore' and expansions >= max_expansions:
                reasons.add('expansion_budget')
                continue  # Already queued prefixes may still close at the target.
            if mode == 'explore':
                expansions += 1
            if mode == 'explore' and focal not in cache:
                allowance = max_expansions - edges_read
                if allowance <= 0:
                    reasons.add('adjacency_budget')
                    continue
                neighbors = reader.outgoing(focal, allowance + 1)
                if len(neighbors) > allowance:
                    reasons.add('adjacency_budget')
                cache[focal] = neighbors[:allowance]
                edges_read += len(cache[focal])
            for reaction in cache.get(focal, []):
                for product in reaction['carried_products']:
                    if product in species:
                        cycle_count += 1
                        if len(cycles) < 100:
                            cycles.append(dict(species=product, reaction_key=reaction['reaction_key'],
                                               prefix_species=list(species), prefix_reactions=[s['reaction_key'] for s in steps]))
                        continue
                    if mode == 'target' and product == target:
                        continue  # Already emitted by the exact final-step probe.
                    if mode == 'target' and distance_to_target.get(product, max_steps + 1) > max_steps - len(steps) - 1:
                        continue
                    if len(queue) >= max_frontier:
                        reasons.add('frontier_budget'); break
                    step = dict(reaction, carried_from=focal, carried_to=product)
                    queue.append((species + [product], steps + [step]))
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        reasons.add('time_budget')
    finally:
        reader.connection.set_progress_handler(None, 0)
    return dict(schema_version=SCHEMA, query=dict(start=start, target=target, mode=mode,
                max_steps=max_steps, max_paths=max_paths, max_expansions=max_expansions,
                max_frontier=max_frontier, max_seconds=max_seconds,
                quality_view=quality_view, return_window_frames=return_window_frames,
                return_basis=return_basis), paths=paths,
                status=('truncated/inconclusive' if reasons else 'found' if paths else 'not_found_within_constraints'),
                query_complete=not reasons, graph_exhaustive=not reasons and not horizon_limited,
                horizon_limited=horizon_limited, truncation_reasons=sorted(reasons),
                expansions=expansions, adjacency_rows_read=edges_read,
                search_algorithm='local_graph_then_target_routes_v1' if mode == 'target' else 'prefix_bfs_v1',
                path_prefixes_examined=prefixes_examined,
                path_prefix_budget=max_expansions * max_steps if mode == 'target' else max_expansions,
                target_probes=len(target_cache), target_rows_read=target_rows_read,
                cycle_closures=cycles, cycle_closures_limit=100,
                cycle_closures_seen=cycle_count, cycle_closures_truncated=cycle_count > len(cycles),
                carrier_policy=reader.carrier_policy,
                ordering='step_count_then_exact_identity',
                semantics=(
                    'Each step follows an event-local dominant atom descendant; '
                    'the steps remain independent and no continuous chain is claimed.'
                    if reader.carrier_policy == ATOM_TRANSFER_POLICY else
                    'Species connectivity only; atom transfer and a continuous chain are not claimed.'))
