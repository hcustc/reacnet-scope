"""Indexed, single-dataset Candidate discovery; no occurrence-chain inference.

The adjacency is published with the event index. Querying it never reads raw
RNG evidence or materializes the global network. Legacy candidate CLI scoring
is intentionally not used by this versioned, anchor-aware API.
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
from .candidate_identity import candidate_identity_from_route

SCHEMA = 'reacnet-scope/indexed-candidates/v5'
ADJACENCY_VERSION = '5'
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
        DROP TABLE IF EXISTS candidate_product_adjacency;
        DROP TABLE IF EXISTS candidate_network_transfers;
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
        CREATE TABLE candidate_network_transfers(
            source_species TEXT NOT NULL, product_species TEXT NOT NULL,
            reaction_key TEXT NOT NULL,
            PRIMARY KEY(product_species,source_species,reaction_key));
        CREATE INDEX candidate_network_transfers_source
            ON candidate_network_transfers(source_species,reaction_key,product_species);
        CREATE TABLE candidate_transfers(
            source_species TEXT NOT NULL, product_species TEXT NOT NULL,
            reaction_key TEXT NOT NULL, supporting_events INTEGER NOT NULL,
            max_shared_atoms INTEGER NOT NULL,
            PRIMARY KEY(source_species,product_species,reaction_key));
        CREATE INDEX candidate_transfers_product
            ON candidate_transfers(product_species,source_species,reaction_key);
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
        connection.executemany('INSERT INTO candidate_network_transfers VALUES(?,?,?)',
                               ((source, product, key) for source in set(reactants)
                                for product in set(products)))
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
                existing = f'v{version[0]}' if version else '未包含候选路径索引'
                raise IndexInvalidError(
                    f'候选路径索引版本不兼容（已有：{existing}；当前需要：v{ADJACENCY_VERSION}）。'
                    '请在“RNG 数据”的准备任务中重建事件索引；原始 RNG 数据无需重新导入。'
                )
            self.connection.execute('SELECT species FROM candidate_species LIMIT 0')
            self.connection.execute('SELECT reaction_key FROM candidate_adjacency LIMIT 0')
            self.connection.execute('SELECT reaction_key FROM candidate_network_transfers LIMIT 0')
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
            if not self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='candidate_transfers_product'"
            ).fetchone():
                raise IndexInvalidError('候选前驱索引缺失；请重建事件索引。')
            policy = self.connection.execute(
                "SELECT value FROM meta WHERE key='candidate_carrier_policy'"
            ).fetchone()
            self.carrier_policy = str(policy[0]) if policy else NETWORK_ONLY_POLICY
            self.direction_view = 'observed'
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

    def _direction_filter(self) -> str:
        # Canonical keys already preserve exact identities, multiplicities and
        # ordering within each side. Reverse lookup uses the existing primary
        # key, including reactions outside this page or carrier neighbourhood.
        if self.direction_view != 'net':
            return ''
        return """ AND r.total_events > COALESCE((
            SELECT reverse.total_events FROM candidate_reactions reverse
            WHERE reverse.reaction_key =
                substr(r.reaction_key,instr(r.reaction_key,'->')+2) || '->' ||
                substr(r.reaction_key,1,instr(r.reaction_key,'->')-1)),0)"""

    def _counts(self, key: str, forward: int) -> dict[str, int]:
        left, right = key.split('->', 1)
        row = self.connection.execute(
            'SELECT total_events FROM candidate_reactions WHERE reaction_key=?',
            (right + '->' + left,)).fetchone()
        reverse = int(row[0]) if row else 0
        return dict(forward_count=forward, reverse_count=reverse,
                    net_count=forward - reverse)

    def outgoing(self, species: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        if self.carrier_policy == ATOM_TRANSFER_POLICY:
            return self._transfers(species, limit, offset=offset)
        rows = self.connection.execute('''
            SELECT r.reaction_key,r.reactants,r.products,r.total_events,t.product_species
            FROM candidate_network_transfers t JOIN candidate_reactions r
            ON r.reaction_key=t.reaction_key WHERE t.source_species=?
            ''' + self._direction_filter() + '''
            ORDER BY r.reaction_key,t.product_species LIMIT ? OFFSET ?
        ''', (species, limit, offset)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n, **self._counts(k, n), carried_products=[product],
                     transfer_event_count=n, max_shared_atoms=None,
                     transfer_basis=NETWORK_ONLY_POLICY) for k, a, b, n, product in rows]

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
            ''' + self._direction_filter() + '''
            ORDER BY a.reaction_key LIMIT ?
        ''', (species, target, limit)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n, **self._counts(k, n), carried_products=[target], transfer_event_count=n,
                     max_shared_atoms=None, transfer_basis=NETWORK_ONLY_POLICY)
                for k, a, b, n in rows]

    def incoming(self, species: str, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        """Read observed forward transfers whose carried product is species."""
        if self.carrier_policy == ATOM_TRANSFER_POLICY:
            return self._transfers(species, limit, incoming=True, offset=offset)
        rows = self.connection.execute('''
            SELECT r.reaction_key,r.reactants,r.products,r.total_events,t.source_species
            FROM candidate_network_transfers t JOIN candidate_reactions r
            ON r.reaction_key=t.reaction_key WHERE t.product_species=?
            AND t.source_species<>t.product_species
            ''' + self._direction_filter() + '''
            ORDER BY t.source_species,r.reaction_key LIMIT ? OFFSET ?
        ''', (species, limit, offset)).fetchall()
        return [dict(reaction_key=k, reactants=json.loads(a), products=json.loads(b),
                     event_count=n, **self._counts(k, n), carried_from=source, carried_to=species,
                     carried_products=[species], transfer_event_count=n,
                     max_shared_atoms=None, transfer_basis=NETWORK_ONLY_POLICY)
                for k, a, b, n, source in rows]

    def _transfers(self, species, limit, target=None, *, incoming=False, offset=0):
        # Filter before LIMIT so folded edges cannot hide retained neighbours.
        sql = '''SELECT r.reaction_key,r.reactants,r.products,r.total_events,
                 t.source_species,t.product_species,t.supporting_events,t.max_shared_atoms
            FROM candidate_transfers t JOIN candidate_reactions r USING(reaction_key)
            WHERE t.'''+('product_species' if incoming else 'source_species')+'=?'
        sql += self._direction_filter()
        params = [species]
        if incoming:
            sql += ' AND t.source_species<>t.product_species'
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
        sql += (' ORDER BY t.source_species,r.reaction_key LIMIT ? OFFSET ?' if incoming
                else ' ORDER BY r.reaction_key,t.product_species LIMIT ? OFFSET ?')
        params.extend([limit, offset])
        result = []
        for key, left, right, total, source, product, support, shared in self.connection.execute(sql, params).fetchall():
            quality = quality_summary(self.connection, source, product, key,
                                      self.return_window_frames, self.return_basis)
            quality['retained_events'] = support - quality['folded_events']
            result.append(dict(reaction_key=key, reactants=json.loads(left), products=json.loads(right),
                               event_count=total, **self._counts(key, total), carried_products=[product], transfer_event_count=support,
                               carried_from=source, carried_to=product, max_shared_atoms=shared,
                               transfer_basis=ATOM_TRANSFER_POLICY, quality=quality))
        return result


def _candidate_path(species: list[str], steps: list[dict[str, Any]]) -> dict[str, Any]:
    identity = {'version': 2, 'species': species,
                'reactions': [step['reaction_key'] for step in steps]}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
    canonical = candidate_identity_from_route({'species': species, 'steps': steps})
    return dict(signature_id=signature, species=species, steps=steps,
                candidate_signature=canonical.signature,
                candidate_identity=canonical.as_dict(), step_count=len(steps),
                continuous_md='not_evaluated',
                quality_warning=any(step.get('quality', {}).get('folded_events', 0)
                                    for step in steps))


def _sample_first_transfer_branches(paths: list[dict[str, Any]],
                                    branch_order: list[tuple[str, str]],
                                    limit: int) -> list[dict[str, Any]]:
    """Give each discovered first transfer a display turn, shortest first within it."""
    groups: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = {}
    for order, path in enumerate(paths):
        key = (path['steps'][0]['reaction_key'], path['species'][1])
        groups.setdefault(key, []).append((order, path))
    ready = deque((key, deque(path for _, path in sorted(
        groups[key], key=lambda item: (item[1]['step_count'], item[0]))))
        for key in branch_order if key in groups)
    sampled = []
    while ready and len(sampled) < limit:
        key, group = ready.popleft()
        sampled.append(group.popleft())
        if group:
            ready.append((key, group))
    return sampled


def _discover_incoming(reader: CandidateReader, target: str, *, max_paths: int,
                       max_seconds: float, offset: int, quality_view: str,
                       return_window_frames: int, return_basis: str) -> dict[str, Any]:
    deadline = time.monotonic() + max_seconds
    reasons: set[str] = set()
    rows: list[dict[str, Any]] = []
    reader.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        rows = reader.incoming(target, max_paths + 1, offset)
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        reasons.add('time_budget')
    finally:
        reader.connection.set_progress_handler(None, 0)
    if len(rows) > max_paths:
        reasons.add('result_limit')
    paths = [_candidate_path([row['carried_from'], target], [row])
             for row in rows[:max_paths]]
    return dict(schema_version=SCHEMA,
                query=dict(direction_view=reader.direction_view,
                    count_scope='published_revision_all_transitions', start='', target=target, mode='reverse', max_steps=1,
                    max_paths=max_paths, quality_view=quality_view,
                    anchor_offset=offset,
                    return_window_frames=return_window_frames, return_basis=return_basis,
                    max_seconds=max_seconds),
                paths=paths, status=('truncated/inconclusive' if reasons else
                    'found' if paths else 'not_found_within_constraints'),
                reachability_status='not_applicable', routes_complete=not reasons,
                query_complete=not reasons, graph_exhaustive=False,
                horizon_limited=True, display_truncated='result_limit' in reasons,
                next_offset=offset + max_paths if 'result_limit' in reasons else None,
                previous_offset=max(0, offset - max_paths) if offset else None,
                truncation_reasons=sorted(reasons), search_algorithm='incoming_one_step_v1',
                ordering='exact_identity', carrier_policy=reader.carrier_policy)


def _discover_forward_one_step(reader: CandidateReader, start: str, *,
                               max_paths: int, max_seconds: float, offset: int,
                               quality_view: str, return_window_frames: int,
                               return_basis: str) -> dict[str, Any]:
    deadline = time.monotonic() + max_seconds
    reasons: set[str] = set()
    rows: list[dict[str, Any]] = []
    reader.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        rows = reader.outgoing(start, max_paths + 1, offset)
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        reasons.add('time_budget')
    finally:
        reader.connection.set_progress_handler(None, 0)
    if len(rows) > max_paths:
        reasons.add('result_limit')
    paths = [_candidate_path([start, product], [dict(row, carried_from=start,
              carried_to=product)]) for row in rows[:max_paths]
              for product in row['carried_products'] if product != start]
    self_cycles = sum(product == start for row in rows[:max_paths]
                      for product in row['carried_products'])
    return dict(schema_version=SCHEMA,
                query=dict(direction_view=reader.direction_view,
                    count_scope='published_revision_all_transitions', start=start, target='', mode='explore', max_steps=1,
                    max_paths=max_paths, anchor_offset=offset,
                    quality_view=quality_view,
                    return_window_frames=return_window_frames, return_basis=return_basis,
                    max_seconds=max_seconds),
                paths=paths, status=('truncated/inconclusive' if reasons else
                    'found' if paths else 'not_found_within_constraints'),
                reachability_status='not_applicable', routes_complete=not reasons,
                query_complete=not reasons, graph_exhaustive=False,
                horizon_limited=True, display_truncated='result_limit' in reasons,
                next_offset=offset + max_paths if 'result_limit' in reasons else None,
                previous_offset=max(0, offset - max_paths) if offset else None,
                truncation_reasons=sorted(reasons), search_algorithm='outgoing_one_step_v1',
                cycle_closures_seen=self_cycles, carrier_policy=reader.carrier_policy,
                ordering='exact_identity')


def _discover_target_routes(reader: CandidateReader, start: str, target: str, *,
                               max_paths: int, max_expansions: int, max_frontier: int,
                               max_seconds: float, max_prefixes: int,
                               max_candidates_examined: int, max_steps: int | None,
                               quality_view: str,
                               return_window_frames: int, return_basis: str) -> dict[str, Any]:
    """Bounded reachability followed by fair, first-branch route enumeration."""
    deadline = time.monotonic() + max_seconds
    graph_reasons: set[str] = set()
    route_reasons: set[str] = set()
    cache: dict[str, list[dict[str, Any]]] = {}
    target_cache: dict[str, list[dict[str, Any]]] = {}
    pending = deque([(start, 0)])
    seen = {start}
    edges_read = target_rows_read = prefixes_examined = 0
    paths_examined: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    branches: dict[tuple[str, str], deque] = {}
    horizon_limited = False
    reader.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        while pending:
            if time.monotonic() >= deadline:
                graph_reasons.add('time_budget'); break
            focal, depth = pending.popleft()
            if len(target_cache) >= max_expansions:
                graph_reasons.add('target_probe_budget')
                continue
            target_rows = reader.outgoing_to(focal, target, max_candidates_examined + 1)
            if len(target_rows) > max_candidates_examined:
                graph_reasons.add('target_match_limit')
            target_cache[focal] = target_rows[:max_candidates_examined]
            target_rows_read += len(target_rows)
            if max_steps is not None and depth >= max_steps - 1:
                horizon_limited = True
                continue
            if len(cache) >= max_expansions:
                graph_reasons.add('expansion_budget')
                continue
            allowance = max_expansions - edges_read
            if allowance <= 0:
                graph_reasons.add('adjacency_budget')
                continue
            neighbors = reader.outgoing(focal, allowance + 1)
            if len(neighbors) > allowance:
                graph_reasons.add('adjacency_budget')
            cache[focal] = neighbors[:allowance]
            edges_read += len(cache[focal])
            for reaction in cache[focal]:
                for product in reaction['carried_products']:
                    if product == target or product in seen:
                        continue
                    if len(pending) >= max_frontier:
                        graph_reasons.add('frontier_budget')
                        continue
                    seen.add(product)
                    pending.append((product, depth + 1))

        out_edges: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        incoming: dict[str, set[str]] = {}
        for focal in target_cache:
            unique: set[tuple[str, str]] = set()
            edges = []
            for reaction in cache.get(focal, []) + target_cache[focal]:
                for product in reaction['carried_products']:
                    edge_key = (reaction['reaction_key'], product)
                    if edge_key in unique:
                        continue
                    unique.add(edge_key)
                    edges.append((product, reaction))
                    incoming.setdefault(product, set()).add(focal)
            out_edges[focal] = edges
        distance = {target: 0}
        reverse = deque([target])
        while reverse:
            product = reverse.popleft()
            for source in sorted(incoming.get(product, ())):
                if source not in distance:
                    distance[source] = distance[product] + 1
                    reverse.append(source)
        reachability = ('found' if start in distance else
                        'inconclusive' if graph_reasons else 'not_found')

        # Each first transfer receives its own queue. Round-robin scheduling
        # prevents one high-degree first branch from occupying every result slot.
        active: deque[tuple[str, str]] = deque()
        for product, reaction in out_edges.get(start, []):
            if product not in distance or product == start:
                continue
            key = (reaction['reaction_key'], product)
            if len(branches) >= max_frontier:
                route_reasons.add('frontier_budget')
                continue
            branches[key] = deque([([start, product],
                                    [dict(reaction, carried_from=start, carried_to=product)])])
            active.append(key)
        queued = len(active)
        cycle_count = 0
        while active:
            if time.monotonic() >= deadline:
                route_reasons.add('time_budget'); break
            if prefixes_examined >= max_prefixes:
                route_reasons.add('path_enumeration_budget'); break
            if len(paths_examined) >= max_candidates_examined:
                route_reasons.add('candidate_exam_budget'); break
            branch = active.popleft()
            species, steps = branches[branch].popleft()
            queued -= 1
            prefixes_examined += 1
            focal = species[-1]
            if focal == target:
                paths_examined.append(_candidate_path(species, steps))
            else:
                if max_steps is not None and len(steps) >= max_steps:
                    horizon_limited = True
                    if branches[branch]:
                        active.append(branch)
                    continue
                for product, reaction in out_edges.get(focal, []):
                    if product not in distance:
                        continue
                    if product in species:
                        cycle_count += 1
                        if len(cycles) < 100:
                            cycles.append(dict(species=product,
                                reaction_key=reaction['reaction_key'],
                                prefix_species=list(species),
                                prefix_reactions=[step['reaction_key'] for step in steps]))
                        continue
                    if queued >= max_frontier:
                        route_reasons.add('frontier_budget')
                        continue
                    branches[branch].append((species + [product], steps + [
                        dict(reaction, carried_from=focal, carried_to=product)]))
                    queued += 1
            if branches[branch]:
                active.append(branch)
        if active and len(paths_examined) >= max_candidates_examined:
            route_reasons.add('candidate_exam_budget')
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        graph_reasons.add('time_budget')
        reachability = 'inconclusive'
        cycle_count = 0
    finally:
        reader.connection.set_progress_handler(None, 0)
    reasons = graph_reasons | route_reasons
    routes_complete = not reasons
    paths = _sample_first_transfer_branches(paths_examined, list(branches), max_paths)
    return dict(schema_version=SCHEMA,
                query=dict(direction_view=reader.direction_view,
                    count_scope='published_revision_all_transitions', start=start, target=target, mode='target', max_steps=max_steps,
                    max_paths=max_paths, max_expansions=max_expansions,
                    max_frontier=max_frontier, max_seconds=max_seconds,
                    max_prefixes=max_prefixes,
                    max_candidates_examined=max_candidates_examined,
                    quality_view=quality_view, return_window_frames=return_window_frames,
                    return_basis=return_basis),
                paths=paths, status=('truncated/inconclusive' if reasons else
                    'found' if paths else 'not_found_within_constraints'),
                reachability_status=reachability, routes_complete=routes_complete,
                query_complete=routes_complete,
                graph_exhaustive=not graph_reasons and not horizon_limited,
                horizon_limited=horizon_limited, display_truncated=len(paths_examined) > max_paths,
                truncation_reasons=sorted(reasons),
                expansions=len(cache), adjacency_rows_read=edges_read,
                search_algorithm='local_reachability_fair_routes_v1',
                path_prefixes_examined=prefixes_examined,
                path_prefix_budget=max_prefixes,
                candidates_examined=len(paths_examined),
                target_probes=len(target_cache), target_rows_read=target_rows_read,
                cycle_closures=cycles, cycle_closures_limit=100,
                cycle_closures_seen=cycle_count,
                cycle_closures_truncated=cycle_count > len(cycles),
                carrier_policy=reader.carrier_policy,
                ordering='first_transfer_round_robin_then_length',
                semantics='Each step is independently observed; continuous MD history is not implied.')


def discover_indexed_candidates(reader: CandidateReader, start: str, *, target: str = '',
                                mode: str = 'target', max_steps: int | None = None,
                                anchor_offset: int = 0,
                                max_paths: int = 20, max_expansions: int = 2000,
                                max_frontier: int = 5000, max_seconds: float = 5,
                                max_prefixes: int = 10000,
                                max_candidates_examined: int = 2000,
                                direction_view: str = 'observed',
                                count_scope: str = 'published_revision_all_transitions',
                                quality_view: str = 'persistent', return_window_frames: int = 3,
                                return_basis: str = 'topology') -> dict[str, Any]:
    if mode not in {'target', 'explore', 'reverse'}:
        raise ValueError('请选择起点到目标、从起点探索或从终点查前驱')
    if quality_view not in {'persistent', 'raw'} or return_basis not in {'exact', 'topology'}:
        raise ValueError('请选择原始/往返折叠视图和精确键级/连接关系返回规则')
    if isinstance(return_window_frames, bool) or not isinstance(return_window_frames, int) or not 1 <= return_window_frames <= 100:
        raise ValueError('往返窗口必须为 1–100 个分析帧间隔')
    if count_scope != 'published_revision_all_transitions':
        raise ValueError('候选方向计数仅支持当前已发布修订的全部观测区间')
    if direction_view not in {'observed', 'net'}:
        raise ValueError('请选择全部观测方向或净转化方向')
    reader.direction_view = direction_view
    # Net counts use all recorded occurrences, independently of temporal returns.
    if direction_view == 'net':
        quality_view = 'raw'
    reader.quality_view = quality_view
    reader.return_window_frames = return_window_frames
    reader.return_basis = return_basis
    for name, value, upper in [('max_paths', max_paths, 100),
                               ('max_expansions', max_expansions, 20000),
                               ('max_frontier', max_frontier, 20000),
                               ('max_prefixes', max_prefixes, 100000),
                               ('max_candidates_examined', max_candidates_examined, 20000)]:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise ValueError(f'{name} 必须是 1–{upper} 的整数')
    if max_steps is not None and (isinstance(max_steps, bool) or not isinstance(max_steps, int)
                                  or not 1 <= max_steps <= 100):
        raise ValueError('max_steps 必须是 1–100 的整数或不设置')
    if (isinstance(anchor_offset, bool) or not isinstance(anchor_offset, int)
            or not 0 <= anchor_offset <= 1000000):
        raise ValueError('anchor_offset 必须是 0–1000000 的整数')
    if not 0 < max_seconds <= 30:
        raise ValueError('查询时间预算必须在 0–30 秒之间')
    if mode != 'reverse' and (not start or not reader.contains(start)):
        raise ValueError('请选择当前数据集中的精确起始结构')
    if mode in {'target', 'reverse'} and (not target or not reader.contains(target)
                                           or (mode == 'target' and target == start)):
        raise ValueError('请选择当前数据集中的精确目标结构；双端搜索的起点和终点必须不同')
    if mode == 'reverse' and max_steps not in {None, 1}:
        raise ValueError('从终点查前驱每次只展开一步')
    if (mode == 'target' or (mode == 'explore' and max_steps not in {None, 1})) and anchor_offset:
        raise ValueError('分页偏移只用于单端的一步探索')
    target = target if mode in {'target', 'reverse'} else ''
    if mode == 'reverse':
        return _discover_incoming(reader, target, max_paths=max_paths,
                                  max_seconds=max_seconds, offset=anchor_offset,
                                  quality_view=quality_view,
                                  return_window_frames=return_window_frames,
                                  return_basis=return_basis)
    if mode == 'target':
        return _discover_target_routes(reader, start, target, max_paths=max_paths,
            max_expansions=max_expansions, max_frontier=max_frontier,
            max_seconds=max_seconds, max_prefixes=max_prefixes,
            max_candidates_examined=max_candidates_examined, max_steps=max_steps,
            quality_view=quality_view, return_window_frames=return_window_frames,
            return_basis=return_basis)
    if max_steps in {None, 1}:
        return _discover_forward_one_step(reader, start, max_paths=max_paths,
            max_seconds=max_seconds, offset=anchor_offset, quality_view=quality_view,
            return_window_frames=return_window_frames, return_basis=return_basis)
    if max_steps is None:
        max_steps = 1
    queue = deque([([start], [])])
    paths: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    cache: dict[str, list[dict[str, Any]]] = {}
    reasons: set[str] = set()
    expansions = edges_read = prefixes_examined = cycle_count = 0
    horizon_limited = False
    deadline = time.monotonic() + max_seconds
    reader.connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        while queue:
            if time.monotonic() >= deadline:
                reasons.add('time_budget'); break
            if prefixes_examined >= max_prefixes:
                reasons.add('path_enumeration_budget'); break
            species, steps = queue.popleft()
            prefixes_examined += 1
            if steps:
                if len(paths) >= max_paths:
                    reasons.add('result_limit'); break
                paths.append(_candidate_path(species, steps))
            if len(steps) >= max_steps:
                horizon_limited = True
                continue
            focal = species[-1]
            if expansions >= max_expansions:
                reasons.add('expansion_budget')
                continue
            expansions += 1
            if focal not in cache:
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
                    if product in species:
                        cycle_count += 1
                        if len(cycles) < 100:
                            cycles.append(dict(species=product, reaction_key=reaction['reaction_key'],
                                prefix_species=list(species),
                                prefix_reactions=[step['reaction_key'] for step in steps]))
                        continue
                    if len(queue) >= max_frontier:
                        reasons.add('frontier_budget')
                        break
                    step = dict(reaction, carried_from=focal, carried_to=product)
                    queue.append((species + [product], steps + [step]))
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        reasons.add('time_budget')
    finally:
        reader.connection.set_progress_handler(None, 0)
    return dict(schema_version=SCHEMA,
                query=dict(direction_view=reader.direction_view,
                    count_scope='published_revision_all_transitions', start=start, target='', mode='explore', max_steps=max_steps,
                    max_paths=max_paths, max_expansions=max_expansions,
                    max_frontier=max_frontier, max_seconds=max_seconds,
                    max_prefixes=max_prefixes,
                    quality_view=quality_view, return_window_frames=return_window_frames,
                    return_basis=return_basis),
                paths=paths, status=('truncated/inconclusive' if reasons else
                    'found' if paths else 'not_found_within_constraints'),
                reachability_status='not_applicable', routes_complete=not reasons,
                query_complete=not reasons, graph_exhaustive=not reasons and not horizon_limited,
                horizon_limited=horizon_limited,
                display_truncated='result_limit' in reasons,
                truncation_reasons=sorted(reasons), expansions=expansions,
                adjacency_rows_read=edges_read, search_algorithm='prefix_bfs_v2',
                path_prefixes_examined=prefixes_examined,
                path_prefix_budget=max_prefixes,
                target_probes=0, target_rows_read=0,
                cycle_closures=cycles, cycle_closures_limit=100,
                cycle_closures_seen=cycle_count,
                cycle_closures_truncated=cycle_count > len(cycles),
                carrier_policy=reader.carrier_policy,
                ordering='step_count_then_exact_identity',
                semantics=('Each step follows an event-local dominant atom descendant; '
                    'the steps remain independent and no continuous chain is claimed.'
                    if reader.carrier_policy == ATOM_TRANSFER_POLICY else
                    'Species connectivity only; atom transfer and a continuous chain are not claimed.'))
