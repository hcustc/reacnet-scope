# Candidate Pathways Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic, auditable multi-step candidate-path ranking with Python, CLI, export, and Dash interfaces linked to indexed RNG event evidence.

**Architecture:** A new domain module treats reactions as hyperedges and performs bounded loopless best-first enumeration over the existing `ReactionNetwork`. Scoring publishes every input metric. An optional read-only evidence provider enriches steps; unavailable evidence degrades explicitly to `network_only`.

**Tech Stack:** Python 3.10+, dataclasses, heap-based best-first search, existing `rng_tools.network`, SQLite event-evidence provider, Dash/Cytoscape, pytest.

## Global Constraints

- Complete Plan 1 first; do not build or scan an event source from a pathway request.
- Preserve complete reactant/product stoichiometry at every step, including repeated species.
- A “path” is a ranked candidate route, not proof of an atom-continuous mechanism.
- Rank directly from `ReactionNetwork` hyperedges; static additive edge weights
  cannot represent query-time evidence and co-reactant continuation.
- Defaults are fixed: downstream, depth 3, branches 5, results 20, expansions 5,000, minimum positive net TP 1, minimum directionality 0.05.
- A focal species may not repeat within one path.
- Every output includes `score_version="candidate-path/v1"`, unrounded metrics, query limits, truncation state, and evidence status.
- Preserve unrelated working-tree changes and commit only current-task files.

---

### Task 1: Versioned pathway domain and scoring

**Files:**
- Create: `rng_tools/pathways.py`
- Create: `tests/test_candidate_pathways.py`

**Interfaces:**
- Produces: `EvidenceProvider` protocol
- Produces: frozen `PathwayStep`, `CandidatePath`, `CandidatePathResult`
- Produces:
  `score_step(*, net_share, directionality, event_coverage, time_coverage) -> tuple[float, str]`
- Produces: `score_path(step_scores) -> float`

The implementation must encode these formulas literally:

```text
step_score =
    0.40 * net_share
  + 0.25 * directionality
  + 0.20 * event_coverage
  + 0.15 * time_coverage

path_score =
    0.70 * geometric_mean(step_scores)
  + 0.30 * min(step_scores)
```

When event evidence is unavailable, the step formula becomes
`(0.40 * net_share + 0.25 * directionality) / 0.65`.

- [ ] **Step 1: Write failing score and serialization tests**

```python
def test_evidence_linked_step_score_uses_v1_weights() -> None:
    score, status = score_step(
        net_share=0.50,
        directionality=0.80,
        event_coverage=0.75,
        time_coverage=0.10,
    )
    assert score == pytest.approx(0.40 * 0.50 + 0.25 * 0.80 + 0.20 * 0.75 + 0.15 * 0.10)
    assert status == "evidence_linked"


def test_network_only_score_renormalizes_available_terms() -> None:
    score, status = score_step(
        net_share=0.50,
        directionality=0.80,
        event_coverage=None,
        time_coverage=None,
    )
    assert score == pytest.approx((0.40 * 0.50 + 0.25 * 0.80) / 0.65)
    assert status == "network_only"


def test_path_score_combines_geometric_mean_and_weakest_step() -> None:
    assert score_path([0.81, 0.49]) == pytest.approx(
        0.70 * math.sqrt(0.81 * 0.49) + 0.30 * 0.49
    )
```

Construct a `PathwayStep` with duplicate reactants and assert `as_dict()` retains both entries, raw floats, source references, and the score version.

- [ ] **Step 2: Verify RED**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_candidate_pathways.py
```

Expected: import failure because `rng_tools.pathways` does not exist.

- [ ] **Step 3: Implement immutable domain objects**

Use these fields:

```python
@dataclass(frozen=True)
class PathwayStep:
    reaction_key: str
    traversal_direction: Literal["downstream", "upstream"]
    focal_input: str
    focal_output: str
    reactants: tuple[str, ...]
    products: tuple[str, ...]
    forward_tp: int
    reverse_tp: int
    net_tp: int
    net_share: float
    directionality: float
    event_coverage: float | None
    time_coverage: float | None
    event_total: int | None
    matched_event_total: int | None
    distinct_intervals: int | None
    evidence_status: Literal["evidence_linked", "network_only"]
    source_references: tuple[str, ...]
    score: float
    score_version: str = "candidate-path/v1"


@dataclass(frozen=True)
class CandidatePath:
    rank: int
    species: tuple[str, ...]
    steps: tuple[PathwayStep, ...]
    score: float
    evidence_status: str


@dataclass(frozen=True)
class CandidatePathResult:
    paths: tuple[CandidatePath, ...]
    query: Mapping[str, Any]
    source_signatures: Mapping[str, Any]
    reason: str
    truncated: bool
    expansions: int
```

`as_dict()` must recursively produce JSON-safe lists/dicts without rounding.

- [ ] **Step 4: Implement scores and validate inputs**

Reject metrics outside `[0, 1]`; reject empty path scores. If both evidence metrics are `None`, renormalize weights `0.40/0.65` and `0.25/0.65`. Do not silently accept only one missing evidence metric: the provider contract supplies both or neither.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_candidate_pathways.py
git add rng_tools/pathways.py tests/test_candidate_pathways.py
git commit -m "feat: define candidate pathway scoring domain"
```

Expected: all score/domain tests pass.

---

### Task 2: Bounded loopless best-first enumeration

**Files:**
- Modify: `rng_tools/pathways.py`
- Modify: `tests/test_candidate_pathways.py`

**Interfaces:**
- Produces:
  `find_candidate_paths(network, start_smiles, *, direction="downstream", max_depth=3, max_branches=5, max_paths=20, max_expansions=5000, min_net_tp=1, min_directionality=0.05, evidence_provider=None) -> CandidatePathResult`
- Consumes: `ReactionNetwork.consume_idx`, `produce_idx`, and `net_flux`

- [ ] **Step 1: Write failing search-behavior tests**

Build small synthetic networks and cover:

```python
def test_hyperedge_branches_retain_all_stoichiometric_terms() -> None:
    net = ReactionNetwork([
        Reaction(("A", "X"), ("B", "C"), 20),
        Reaction(("B",), ("D",), 10),
        Reaction(("C",), ("E",), 8),
    ])
    result = find_candidate_paths(net, "A", max_depth=2, max_paths=10)
    first_steps = [path.steps[0] for path in result.paths]
    assert {step.focal_output for step in first_steps} >= {"B", "C"}
    assert all(step.reactants == ("A", "X") for step in first_steps)
    assert all(step.products == ("B", "C") for step in first_steps)
```

Add tests asserting:

- upstream traversal is symmetric and retains the recorded reaction orientation;
- `A -> B -> A` never revisits `A`;
- reverse-dominated and below-directionality branches are filtered;
- `max_branches` is applied after deterministic ordering;
- paths sort by `(-score, species, reaction_keys)`;
- `max_expansions=1` returns deterministic partial results and `truncated=True`;
- absent start gives `reason="species_absent"`;
- present start with no positive-net continuation gives `reason="no_positive_net_continuation"`;
- threshold removal gives `reason="filtered_by_thresholds"`.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_candidate_pathways.py
```

Expected: `find_candidate_paths` is missing.

- [ ] **Step 3: Implement oriented candidate generation**

For downstream traversal, inspect reactions consuming the focal species and branch to each distinct product species. For upstream traversal, inspect reactions producing the focal species and branch to each distinct reactant species. Always retain full original sides.

For each recorded reaction:

```python
forward_tp, reverse_tp, signed_net, _ = network.net_flux(reaction)
positive_net = signed_net
directionality = positive_net / forward_tp if forward_tp > 0 and positive_net > 0 else 0.0
```

Only the recorded direction that moves toward the current traversal is eligible. Deduplicate a reversible pair by its oriented canonical reaction key, so the forward and explicit reverse records are not expanded twice.

Compute `net_share` over all eligible positive-net continuations from the focal species before applying the branch cap:

```python
net_share = candidate.net_tp / sum(item.net_tp for item in eligible)
```

- [ ] **Step 4: Implement deterministic best-first search**

Use `heapq` with a monotonic sequence only after semantic tie-break fields:

```python
priority = (
    -partial_path_score,
    tuple(next_species_path),
    tuple(step.reaction_key for step in next_steps),
    sequence,
)
```

Count every popped nonterminal state as one expansion. A state is terminal when it reaches `max_depth` or has no eligible continuation. Retain shorter terminal paths. Stop when either `max_paths` completed paths are known and no queued state can outrank the worst retained path, or `max_expansions` is reached.

Validate bounds before search:

- `1 <= max_depth <= 12`
- `1 <= max_branches <= 100`
- `1 <= max_paths <= 500`
- `1 <= max_expansions <= 1_000_000`
- `min_net_tp >= 1`
- `0 <= min_directionality <= 1`

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_candidate_pathways.py
git add rng_tools/pathways.py tests/test_candidate_pathways.py
git commit -m "feat: enumerate bounded candidate pathways"
```

Expected: all search, symmetry, cycle, threshold, and truncation tests pass.

---

### Task 3: Read-only event-evidence enrichment and pathway service

**Files:**
- Modify: `reacnet_scope/event_index.py`
- Modify: `rng_tools/pathways.py`
- Modify: `scripts/webapp_dash/services.py`
- Create: `tests/test_pathway_services.py`

**Interfaces:**
- Extends: every `reaction_summary` row with `available_intervals`
- Produces: `EventIndexEvidenceProvider`
- Produces: `find_pathways(artifacts, start_smiles, **limits) -> dict`

- [ ] **Step 1: Write failing evidence-linked and degraded-service tests**

```python
def test_pathway_service_enriches_steps_from_event_summary(indexed_artifacts) -> None:
    payload = svc.find_pathways(indexed_artifacts, "A", max_depth=1)
    step = payload["paths"][0]["steps"][0]
    assert step["event_coverage"] == pytest.approx(3 / 4)
    assert step["time_coverage"] == pytest.approx(2 / 10)
    assert step["evidence_status"] == "evidence_linked"


def test_pathway_service_degrades_without_event_index(reaction_only_artifacts) -> None:
    payload = svc.find_pathways(reaction_only_artifacts, "A", max_depth=1)
    assert payload["evidence_status"] == "network_only"
    assert "--event-only" in payload["preparation_command"]
    assert payload["paths"][0]["steps"][0]["event_coverage"] is None
```

Monkeypatch source CSV `open` calls to fail, proving evidence enrichment uses only SQLite.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_pathway_services.py
```

Expected: service/provider interfaces are missing.

- [ ] **Step 3: Complete the evidence-provider contract**

Plan 1 metadata must expose `available_intervals = max(molecule_frame_count - 1, 0)`. Each `reaction_summary` result includes:

```python
{
    "reaction_key": key,
    "total_events": 4,
    "matched_events": 3,
    "distinct_intervals": 2,
    "available_intervals": 10,
}
```

`EventIndexEvidenceProvider` batches all candidate reaction keys once per search. It never performs one query per expansion.
The store chunks SQLite `IN` queries into at most 500 keys so large reaction
catalogues do not exceed SQLite parameter limits. When the event index is
ready but a reaction key has no summary row, the provider returns zero event
and time coverage; `None` is reserved for an unavailable event index.

- [ ] **Step 4: Implement the service boundary**

`find_pathways`:

1. validates the `.reactionabcd` artifact;
2. loads the cached `ReactionNetwork` using existing `build_network`/service cache conventions;
3. tries to open the event index without building;
4. passes a provider only for a ready index;
5. returns serialized paths, formulas, query parameters, source signatures, evidence status, and the exact preparation command when degraded.

Translate invalid user limits to `ServiceError(reason="bad_pathway_query")`.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_pathway_services.py tests/test_event_evidence_index.py
git add reacnet_scope/event_index.py rng_tools/pathways.py scripts/webapp_dash/services.py tests/test_pathway_services.py
git commit -m "feat: link candidate paths to event evidence"
```

---

### Task 4: CLI pathway query and reproducible exports

**Files:**
- Modify: `scripts/rng_query_cli.py`
- Create: `tests/test_pathway_cli.py`

**Interfaces:**
- Produces CLI: `reacnet-scope pathway`
- Produces: JSON document and flattened step CSV
- Does not build: event index

- [ ] **Step 1: Write failing parser and command tests**

Call
`build_parser().parse_args(["pathway", "--reac", str(reaction_file), "--start-smiles", "A"])`
and `cmd_pathway` against a tiny fixture. Assert support for:

```text
--reac
--start-smiles
--direction downstream|upstream
--max-depth
--max-branches
--max-paths
--max-expansions
--min-net-tp
--min-directionality
--out-json
--out-csv
```

The JSON must contain `schema_version="reacnet-scope/pathways/v1"`. The CSV must contain one row per step with `path_rank`, `step_index`, complete sides, all four metrics, path score, step score, evidence status, and score version.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_pathway_cli.py
```

Expected: parser rejects `pathway`.

- [ ] **Step 3: Implement command and serializers**

Infer event sources from the reaction base:

```python
reaction_base = (
    args.reac[: -len(".reactionabcd")]
    if args.reac.endswith(".reactionabcd")
    else args.reac
)
reactionevent_file = f"{reaction_base}.reactionevent.csv"
molecules_file = f"{reaction_base}.molecules.csv"
```

If their prepared index is unavailable, continue in `network_only` mode and
print
`f"reacnet-scope-prepare {shlex.quote(str(Path(reaction_base).parent))} --event-only"`
to stderr. Never call `build`.

Write JSON atomically with a sibling `.tmp` plus `os.replace`. Write CSV through the existing `write_csv`. If no output path is requested, print a concise ranked table while retaining unrounded values in export objects.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_pathway_cli.py
git add scripts/rng_query_cli.py tests/test_pathway_cli.py
git commit -m "feat: expose candidate pathway CLI"
```

---

### Task 5: Dedicated Dash “关键路径” page

**Files:**
- Modify: `scripts/webapp_dash/app.py`
- Modify: `scripts/webapp_dash/callbacks.py`
- Modify: `scripts/webapp_dash/services.py`
- Modify: `scripts/webapp_dash/assets/app.css`
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_pathway_services.py`

**Interfaces:**
- Adds page ID: `pathway`
- Adds store: `pathway-store`
- Adds selected-species handoff and selected-step event handoff

- [ ] **Step 1: Write failing layout and callback tests**

Assert the layout contains:

```text
pathway-start-smiles
pathway-direction
pathway-max-depth
pathway-max-branches
pathway-max-paths
pathway-min-net-tp
pathway-min-directionality
pathway-search-btn
pathway-grid
pathway-cytoscape
pathway-json-download
pathway-csv-download
pathway-open-events-btn
pathway-store
```

Invoke the search callback with exact values and monkeypatch `svc.find_pathways`; assert no truthiness default overwrites `min_directionality=0`. Test the reactions/species handoff sets the exact SMILES. Test selected step → event page transfers its full reaction text.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_pathway_services.py
```

Expected: missing page IDs/callbacks.

- [ ] **Step 3: Build the page and pathway graph payload**

Add `"pathway"` to `PAGE_IDS`, labels, descriptions, navigation, layout, and stores. The result table displays rank, formula chain, SMILES chain, path score, weakest-step score, depth, and evidence badge.

Build Cytoscape elements directly from serialized path domain objects:

- one species node per exact SMILES;
- one reaction node per `(path_rank, step_index, reaction_key)`;
- all reactant and product edges, not only the focal continuation;
- classes `species`, `reaction`, `f"path-rank-{rank}"`, and `network-only`
  where applicable.

Only event handoff is in scope; no graph-view handoff or cross-highlighting is
reserved.

- [ ] **Step 4: Wire searches, selection, handoffs, and downloads**

The search callback returns three distinct empty messages: species absent, no positive-net continuation, or filtered by thresholds. Reaching the expansion cap shows `truncated` and the expansion count.

Downloads serialize the exact store payload through `dcc.send_string`; do not
recompute the search. Event handoff navigates to `events` and sets
`event-reaction-text`. No network handoff is required in the current scope.

- [ ] **Step 5: Verify and commit**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_pathway_services.py tests/test_pathway_cli.py tests/test_candidate_pathways.py
git add scripts/webapp_dash/app.py scripts/webapp_dash/callbacks.py scripts/webapp_dash/services.py scripts/webapp_dash/assets/app.css tests/test_dash_smoke.py tests/test_pathway_services.py
git commit -m "feat: add evidence-linked pathway workspace"
```

Expected: focused domain, service, CLI, and Dash tests pass.

---

### Task 6: Documentation and milestone verification

**Files:**
- Modify: `README.md`
- Create: `docs/pathway-analysis.md`

- [ ] **Step 1: Document semantics and examples**

Document:

- candidate-path versus confirmed-mechanism language;
- exact score formula and `network_only` renormalization;
- all search bounds and truncation behavior;
- human-run event-index preparation;
- CLI examples with JSON/CSV outputs;
- how a selected step opens supporting RNG events.

- [ ] **Step 2: Run final verification**

```bash
.venv/bin/python -m pytest -q tests/test_candidate_pathways.py tests/test_pathway_services.py tests/test_pathway_cli.py tests/test_dash_smoke.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
```

Expected: all tests pass, compileall exits `0`, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/pathway-analysis.md
git commit -m "docs: explain candidate pathway evidence and scoring"
```

## Milestone Acceptance

- Python, CLI, and Dash return the same ranked path order for identical inputs.
- Every step preserves the full hyperedge and publishes all score inputs.
- Ready evidence is linked through one batched SQLite read; missing evidence degrades to `network_only`.
- Cycle prevention, upstream symmetry, branch/depth/result/expansion limits, and deterministic truncation are tested.
