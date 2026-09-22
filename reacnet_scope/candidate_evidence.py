"""Prepared return evidence and bounded, selective carrier-chain validation.

Return detection describes recorded topology; it does not classify noise or
infer elementary chemistry. Raw occurrences are never removed.
"""
from __future__ import annotations

from itertools import groupby
import json
import sqlite3
import time


def _parts(raw):
    return tuple(sorted((p['species'], tuple(sorted(p['atom_ids']))) for p in json.loads(raw)))


def _topology(raw):
    return frozenset(tuple(map(int, b.split('-')[:2])) for b in json.loads(raw))


def _reverse_kind(previous, current):
    if (_parts(previous[3]) != _parts(current[4]) or
            _parts(previous[4]) != _parts(current[3])):
        return 'none'
    if (_topology(previous[5]) != _topology(current[6]) or
            _topology(previous[6]) != _topology(current[5])):
        return 'none'
    if (set(json.loads(previous[5])) == set(json.loads(current[6])) and
            set(json.loads(previous[6])) == set(json.loads(current[5]))):
        return 'exact'
    return 'topology'


def materialize_candidate_evidence(connection: sqlite3.Connection) -> None:
    """Run only on the unpublished preparation database, after continuity."""
    connection.executescript('''
        DROP TABLE IF EXISTS candidate_event_history;
        DROP TABLE IF EXISTS candidate_quality_counts;
        DROP TABLE IF EXISTS candidate_carrier_pairs;
        CREATE TABLE candidate_event_history(
            event_id TEXT PRIMARY KEY, previous_event_id TEXT,
            prior_gap INTEGER, prior_kind TEXT NOT NULL DEFAULT 'unknown',
            return_event_id TEXT, return_gap INTEGER,
            return_kind TEXT NOT NULL DEFAULT 'unknown');
        CREATE TABLE candidate_quality_counts(
            source_species TEXT, product_species TEXT, reaction_key TEXT,
            prior_gap INTEGER, prior_kind TEXT, return_gap INTEGER,
            return_kind TEXT, event_count INTEGER);
        CREATE INDEX candidate_quality_lookup ON candidate_quality_counts(
            source_species,product_species,reaction_key);
        CREATE TABLE candidate_carrier_pairs(
            event_id TEXT, source_species TEXT, product_species TEXT,
            reactant_instance_id TEXT, product_instance_id TEXT, shared_atoms INTEGER,
            PRIMARY KEY(event_id,reactant_instance_id,product_instance_id));
        CREATE INDEX candidate_carrier_lookup ON candidate_carrier_pairs(
            source_species,product_species,event_id);
    ''')
    columns = {r[1] for r in connection.execute('PRAGMA table_info(events)')}
    required = {'event_id', 'timestep_index', 'atom_ids_json', 'association_status',
                'reactant_participants_json', 'product_participants_json',
                'reactant_bonds_json', 'product_bonds_json'}
    if required <= columns:
        connection.execute('CREATE INDEX IF NOT EXISTS candidate_events_transition ON events(timestep_index,event_id)')
        # Only the most recent occurrence for each atom remains in memory.
        # Same-transition overlaps become barriers, never an internal ordering.
        last = {}
        cursor = connection.execute('''SELECT event_id,timestep_index,association_status,
            reactant_participants_json,product_participants_json,
            reactant_bonds_json,product_bonds_json,atom_ids_json
            FROM events ORDER BY timestep_index,event_id''')
        for transition, batch in groupby(cursor, key=lambda row: row[1]):
            unresolved = connection.execute('''SELECT 1 FROM events
                WHERE timestep_index=? AND association_status IS NOT 'matched'
                AND json_array_length(atom_ids_json)=0 LIMIT 1''', (transition,)).fetchone() is not None
            if unresolved:
                last.clear()
            touched = {}
            for row in batch:
                atoms = json.loads(row[7])
                old_ids = {last.get(atom, (None,))[0] for atom in atoms}
                previous = last.get(atoms[0]) if atoms and len(old_ids) == 1 else None
                kind = 'unknown'
                if previous and previous[0] and row[2] == previous[2] == 'matched':
                    kind = _reverse_kind(previous, row)
                gap = transition - previous[1] if previous and previous[0] else None
                connection.execute('INSERT INTO candidate_event_history(event_id,previous_event_id,prior_gap,prior_kind) VALUES(?,?,?,?)',
                                   (row[0], previous[0] if previous else None, gap, kind))
                if kind in ('exact', 'topology'):
                    connection.execute('''UPDATE candidate_event_history SET
                        return_event_id=?,return_gap=?,return_kind=? WHERE event_id=?''',
                        (row[0], gap, kind, previous[0]))
                for atom in atoms:
                    touched[atom] = row if atom not in touched else (None, transition)
            if not unresolved:
                last.update(touched)
    # Queries accept windows of at most 100 analyzed-frame intervals. Collapse
    # longer gaps so one edge has a fixed number of quality buckets regardless
    # of how many occurrences support it.
    connection.execute('''INSERT INTO candidate_quality_counts
        SELECT source_species,product_species,reaction_key,prior_gap,prior_kind,
            return_gap,return_kind,COUNT(*) FROM (
            SELECT t.source_species,t.product_species,t.reaction_key,
                CASE WHEN h.prior_gap BETWEEN 1 AND 100 THEN h.prior_gap END AS prior_gap,
                COALESCE(h.prior_kind,'unknown') AS prior_kind,
                CASE WHEN h.return_gap BETWEEN 1 AND 100 THEN h.return_gap END AS return_gap,
                COALESCE(h.return_kind,'unknown') AS return_kind
            FROM candidate_transfer_events t LEFT JOIN candidate_event_history h USING(event_id)
        ) GROUP BY source_species,product_species,reaction_key,
            prior_gap,prior_kind,return_gap,return_kind''')
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {'event_participants', 'molecule_instances'} <= tables:
        return
    cursor = connection.execute('''SELECT p.event_id,p.side,p.instance_id,p.species_smiles,m.atom_ids_json
        FROM event_participants p JOIN molecule_instances m USING(instance_id)
        ORDER BY p.event_id,p.side,p.participant_index''')
    for event_id, group in groupby(cursor, key=lambda row: row[0]):
        participants = [(r[1], r[2], r[3], set(json.loads(r[4]))) for r in group]
        products = [p for p in participants if p[0] == 'product']
        for _, source_id, source, atoms in (p for p in participants if p[0] == 'reactant'):
            overlaps = [(p, len(atoms & p[3])) for p in products]
            maximum = max((n for _, n in overlaps), default=0)
            for (_, product_id, product, _), shared in overlaps:
                if shared and shared == maximum:
                    connection.execute('INSERT OR IGNORE INTO candidate_carrier_pairs VALUES(?,?,?,?,?,?)',
                                       (event_id, source, product, source_id, product_id, shared))


def quality_summary(connection, source, product, reaction, window, basis):
    kinds = "('exact','topology')" if basis == 'topology' else "('exact')"
    row = connection.execute(f'''SELECT
        COALESCE(SUM(event_count),0),
        COALESCE(SUM(CASE WHEN (prior_kind IN {kinds} AND prior_gap BETWEEN 1 AND ?)
            OR (return_kind IN {kinds} AND return_gap BETWEEN 1 AND ?)
            THEN event_count ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN return_kind IN {kinds} AND return_gap BETWEEN 1 AND ?
            THEN event_count ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN prior_kind IN {kinds} AND prior_gap BETWEEN 1 AND ?
            THEN event_count ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN return_kind='exact' AND return_gap BETWEEN 1 AND ?
            THEN event_count ELSE 0 END),0),
        COALESCE(SUM(CASE WHEN return_kind='topology' AND return_gap BETWEEN 1 AND ?
            THEN event_count ELSE 0 END),0)
        FROM candidate_quality_counts WHERE source_species=? AND product_species=? AND reaction_key=?''',
        (window, window, window, window, window, window, source, product, reaction)).fetchone()
    summary = dict(zip(('evaluated_events','folded_events','rapid_return_events',
                        'reclosure_events','exact_return_events','topology_return_events'), row))
    summary.update(window_frames=window, return_basis=basis,
                   semantics='recorded_return_not_noise_classification')
    return summary


def event_details(connection, event_id):
    """Bounded evidence for one displayed event, retaining exact RNG bonds."""
    row = connection.execute('''SELECT reactant_participants_json,product_participants_json,
        reactant_bonds_json,product_bonds_json,timestep_index FROM events WHERE event_id=?''', (event_id,)).fetchone()
    if row is None:
        raise ValueError('事件不存在')
    history = connection.execute('SELECT * FROM candidate_event_history WHERE event_id=?', (event_id,))
    names = [c[0] for c in history.description]
    record = history.fetchone()
    observations = []
    molecular = bool(connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_participants'").fetchone())
    products = connection.execute("SELECT instance_id,species_smiles FROM event_participants WHERE event_id=? AND side='product' LIMIT 101", (event_id,)) if molecular else []
    for instance, species in products:
        link = connection.execute('SELECT status,next_timestep_index,next_event_ids_json,reason FROM continuity_links WHERE from_instance_id=?', (instance,)).fetchone()
        observations.append(dict(instance_id=instance, species=species,
            followup_status=link[0] if link else 'no_recorded_successor',
            observed_frame_intervals=(link[1] - row[4] if link and link[0] == 'matched' else None),
            next_event_ids=json.loads(link[2]) if link else [],
            reason=link[3] if link else 'followup_endpoint_or_missing_evidence'))
    return dict(event_id=event_id, history=dict(zip(names, record)) if record else None,
        molecular_evidence_available=molecular and bool(json.loads(row[0])),
        reactants=json.loads(row[0]), products=json.loads(row[1]),
        reactant_bonds=json.loads(row[2]), product_bonds=json.loads(row[3]),
        product_followup=observations[:100], product_followup_truncated=len(observations) > 100,
        atom_id_basis=1, structure_basis='exact_event_bonds',
        duration_note='采样间隔计数；不等于精确连续时间寿命。无后续记录不证明稳定。')


def validate_carrier_chain(connection, path, *, max_states=1000, max_seconds=5):
    """Find an exact first-consumption witness; preserve gaps as inconclusive."""
    if isinstance(max_states, bool) or not isinstance(max_states, int) or not 1 <= max_states <= 10000:
        raise ValueError('连续检查状态预算必须为 1–10000')
    if not 0 < max_seconds <= 30:
        raise ValueError('连续检查时间预算必须为 0–30 秒')
    deadline = time.monotonic() + max_seconds
    steps = path['steps']
    examined = 0
    uncertain = False
    breaks = []
    witness = None

    def pairs(step, event=None, instance=None):
        sql = '''SELECT p.event_id,p.reactant_instance_id,p.product_instance_id
            FROM candidate_transfer_events t JOIN candidate_carrier_pairs p
              ON p.event_id=t.event_id AND p.source_species=t.source_species AND p.product_species=t.product_species
            WHERE t.source_species=? AND t.product_species=? AND t.reaction_key=?'''
        params = [step['carried_from'], step['carried_to'], step['reaction_key']]
        if event is not None:
            sql += ' AND p.event_id=?'; params.append(event)
        if instance is not None:
            sql += ' AND p.reactant_instance_id=?'; params.append(instance)
        sql += ' ORDER BY p.event_id,p.reactant_instance_id,p.product_instance_id LIMIT ?'
        params.append(max_states + 1)
        return connection.execute(sql, params).fetchall()

    def atoms(instance):
        r = connection.execute('SELECT atom_ids_json FROM molecule_instances WHERE instance_id=?', (instance,)).fetchone()
        return set(json.loads(r[0])) if r else set()

    def boundary(event, index, reason, **extra):
        if len(breaks) < 25:
            breaks.append(dict(event_id=event, step_index=index, reason=reason, **extra))

    connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
    try:
        seeds = pairs(steps[0])
        uncertain = len(seeds) > max_states
        stack = []
        for event, reactant, product in seeds[:max_states]:
            anchors = atoms(reactant)
            stack.append((0, event, product, anchors, anchors & atoms(product), [dict(event_id=event, reactant_instance_id=reactant, product_instance_id=product)]))
        if not seeds:
            uncertain = True
            boundary('', 0, 'missing_carrier_instances')
        while stack:
            if examined >= max_states or time.monotonic() >= deadline:
                uncertain = True
                boundary('', 0, 'validation_budget')
                break
            index, event, instance, anchors, retained, chain = stack.pop()
            examined += 1
            if not retained:
                boundary(event, index, 'anchor_lineage_lost')
                continue
            if index == len(steps) - 1:
                witness = dict(carrier_chain=chain, selected_anchor_atom_ids=sorted(anchors),
                               max_continuous_anchor_set=sorted(retained), retained_count=len(retained),
                               retained_fraction=len(retained) / len(anchors), intact_anchor_support=anchors == retained)
                break
            link = connection.execute('''SELECT status,next_timestep_index,next_event_ids_json,
                to_reactant_instance_id,reason FROM continuity_links WHERE from_instance_id=?''', (instance,)).fetchone()
            if not link or link[0] != 'matched' or len(json.loads(link[2])) != 1:
                uncertain = True
                boundary(event, index, link[4] or link[0] if link else 'no_recorded_successor')
                continue
            successor = json.loads(link[2])[0]
            current_t = connection.execute('SELECT timestep_index FROM events WHERE event_id=?', (event,)).fetchone()[0]
            if link[1] <= current_t:
                uncertain = True
                boundary(event, index, 'non_increasing_transition')
                continue
            states = connection.execute('''SELECT instance_id,structure_key,analyzed_frame
                FROM molecule_instances WHERE instance_id IN (?,?)''', (instance, link[3])).fetchall()
            by_id = {r[0]: (r[1], r[2]) for r in states}
            if instance not in by_id or link[3] not in by_id or by_id[instance][0] != by_id[link[3]][0]:
                uncertain = True
                boundary(event, index, 'incompatible_carrier_bonds', next_event_id=successor)
                continue
            # The existing first-consumption substrate contains event endpoints,
            # not a certificate for every intervening frame. Do not invent one.
            if by_id[instance][1] != by_id[link[3]][1]:
                uncertain = True
                boundary(event, index, 'intervening_bond_history_not_indexed', next_event_id=successor)
                continue
            options = pairs(steps[index + 1], successor, link[3])
            if not options:
                boundary(event, index + 1, 'first_consumption_differs', next_event_id=successor)
            if len(options) + len(stack) > max_states - examined:
                uncertain = True
                options = options[:max(0, max_states - examined - len(stack))]
                boundary(event, index, 'validation_budget')
            for nxt, reactant, product in options:
                stack.append((index + 1, nxt, product, anchors, retained & atoms(reactant) & atoms(product),
                              chain + [dict(event_id=nxt, reactant_instance_id=reactant, product_instance_id=product)]))
    except sqlite3.OperationalError as exc:
        if getattr(exc, 'sqlite_errorcode', None) != sqlite3.SQLITE_INTERRUPT:
            raise
        uncertain = True
        boundary('', 0, 'validation_time_budget')
    finally:
        connection.set_progress_handler(None, 0)
    status = 'chain_found' if witness else 'inconclusive' if uncertain else 'not_observed_within_evidence'
    return dict(validation_execution='inconclusive' if status == 'inconclusive' else 'complete',
                status=status, anchor_policy='all_atoms', witness=witness, breakpoints=breaks,
                examined_states=examined, max_states=max_states, max_seconds=max_seconds,
                enumeration_complete=not uncertain and not witness,
                semantics='selected_route_first_consumption_witness_not_mechanistic_proof')
