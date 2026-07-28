# Mechanism Network Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded, evidence-aware `.reactionabcd` mechanism network alongside the existing `.table` observation network, with stable NetworkX adapters and standard graph exports.

**Architecture:** A chemistry-facing builder selects positive-net reversible reaction pairs around an anchor species and emits a stable bipartite payload. A separate NetworkX adapter converts that payload into a `MultiDiGraph` for graph algorithms and serialization. Dash presents mechanism and observation views as different semantics, never relabeling passage/event counts as kinetic flux.

**Tech Stack:** Python 3.10+, existing `ReactionNetwork`, NetworkX 3.2–3.x, Dash Cytoscape, GraphML/GEXF/Cytoscape JSON, pytest.

## Global Constraints

- Complete Plans 1 and 2 first.
- `mechanism`, `event_transfer`, and reserved `kinetic_flux` are distinct `network_semantics` values.
- The existing `.table` view uses `network_semantics="event_transfer"` and `evidence_level="aggregate_observation"`.
- The new `.reactionabcd` view uses `network_semantics="mechanism"` and passage-count fields. Do not name them rates or fluxes.
- Reaction nodes are explicit hyperedge nodes; never collapse a multi-reactant/multi-product reaction to one species-to-species edge.
- Default mechanism rendering is a bounded anchor neighborhood, not the complete global graph.
- NetworkX is an adapter/algorithm/export layer, not the pathway-ranking engine.
- Graph exports use scalar GraphML/GEXF attributes; structured values are canonical JSON strings.
- Preserve unrelated changes and commit only task-scoped files.

---

### Task 1: Stable mechanism-network payload

**Files:**
- Create: `rng_tools/mechanism_graph.py`
- Create: `tests/test_mechanism_graph.py`

**Interfaces:**
- Produces: `build_mechanism_network(network, *, anchor_smiles, direction="both", max_depth=2, min_net_tp=1, max_nodes=200, evidence_provider=None) -> dict`
- Produces schema: `reacnet-scope/mechanism-network/v1`

- [ ] **Step 1: Write failing stoichiometry, reversible-pair, and bounds tests**

```python
def test_mechanism_payload_is_bipartite_and_preserves_stoichiometry() -> None:
    net = ReactionNetwork([
        Reaction(("A", "A", "X"), ("B", "C"), 12),
        Reaction(("B", "C"), ("A", "A", "X"), 2),
    ])
    payload = build_mechanism_network(net, anchor_smiles="A", max_depth=1)
    reaction = next(node for node in payload["nodes"] if node["kind"] == "reaction")
    assert reaction["reactants"] == ["A", "A", "X"]
    assert reaction["products"] == ["B", "C"]
    assert reaction["forward_tp"] == 12
    assert reaction["reverse_tp"] == 2
    assert reaction["net_tp"] == 10
    reactant_edges = [edge for edge in payload["edges"] if edge["role"] == "reactant"]
    assert next(edge for edge in reactant_edges if edge["species_smiles"] == "A")["coefficient"] == 2
```

Also assert:

- the explicit reverse record is not emitted as a second reaction node;
- if reverse TP dominates, sides orient toward the positive-net direction;
- direction `downstream`, `upstream`, and `both` select the expected neighborhood;
- `max_depth` counts species-to-reaction-to-species expansions as one chemical step;
- a reaction is skipped atomically when adding its node plus missing species would exceed `max_nodes`;
- skipped work sets `truncated=True` and preserves deterministic order;
- missing anchor returns `reason="species_absent"`;
- every ID is stable across input reaction order.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py
```

Expected: import failure because the module does not exist.

- [ ] **Step 3: Implement reversible-pair normalization**

For each reaction and its reverse, choose the positive-net orientation:

```python
if reaction.tp > reverse_tp:
    oriented = reaction
    forward_tp, backward_tp = reaction.tp, reverse_tp
elif reverse is not None and reverse.tp > reaction.tp:
    oriented = reverse
    forward_tp, backward_tp = reverse.tp, reaction.tp
else:
    # zero net; exclude because the mechanism view requires positive net TP
```

Deduplicate pairs by `min(reaction.key, reverse_key)`. Generate IDs:

```python
species_id = "species:" + sha256(smiles.encode()).hexdigest()[:20]
reaction_id = "reaction:" + sha256(oriented.key.encode()).hexdigest()[:20]
```

Store exact SMILES/formula labels on nodes. Aggregate repeated side members into an edge coefficient while preserving repeated terms in the reaction node.

- [ ] **Step 4: Implement bounded neighborhood traversal**

Use a deterministic breadth-first queue keyed by `(depth, species_smiles)`. Direction controls which oriented reactions may be followed from the current species:

- downstream: current species is on the reactant side;
- upstream: current species is on the product side;
- both: either side.

Sort reaction candidates by `(-net_tp, reaction_key)` and species continuations lexically. Before adding a reaction, calculate all new node IDs required; if the atomic addition exceeds `max_nodes`, skip it and set `truncated`.

Payload fields:

```python
{
    "schema_version": "reacnet-scope/mechanism-network/v1",
    "network_semantics": "mechanism",
    "evidence_level": "reaction_passage_counts",
    "anchor_smiles": anchor_smiles,
    "query": {
        "direction": direction,
        "max_depth": max_depth,
        "min_net_tp": min_net_tp,
        "max_nodes": max_nodes,
    },
    "nodes": nodes,
    "edges": edges,
    "meta": {
        "node_count": len(nodes),
        "edge_count": len(edges),
        "reaction_count": sum(node["kind"] == "reaction" for node in nodes),
        "truncated": truncated,
        "reason": reason,
    },
}
```

When an evidence provider is ready, batch summary lookup once and add `event_total`, `matched_event_total`, `event_coverage`, and `evidence_status` to reaction nodes. Missing evidence uses `None` metrics and `network_only`.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py
git add rng_tools/mechanism_graph.py tests/test_mechanism_graph.py
git commit -m "feat: build bounded mechanism network payloads"
```

---

### Task 2: NetworkX adapter and interoperable serializers

**Files:**
- Modify: `rng_tools/mechanism_graph.py`
- Modify: `tests/test_mechanism_graph.py`
- Modify: `tests/test_dependency_contract.py`

**Interfaces:**
- Produces: `to_networkx_mechanism_graph(payload) -> nx.MultiDiGraph`
- Produces: `serialize_mechanism_graph(graph, *, format="cytoscape-json") -> dict | bytes`
- Supports: `cytoscape-json`, `graphml`, `gexf`

- [ ] **Step 1: Write failing adapter and round-trip tests**

```python
def test_networkx_projection_retains_bipartite_roles(payload) -> None:
    graph = to_networkx_mechanism_graph(payload)
    assert isinstance(graph, nx.MultiDiGraph)
    assert nx.is_weakly_connected(graph)
    reaction_id = next(node for node, data in graph.nodes(data=True) if data["kind"] == "reaction")
    assert {data["role"] for *_, data in graph.in_edges(reaction_id, data=True)} == {"reactant"}
    assert {data["role"] for *_, data in graph.out_edges(reaction_id, data=True)} == {"product"}
```

For each format:

- Cytoscape JSON has `data.schema_version` and all element IDs;
- `nx.read_graphml(io.BytesIO(graphml_bytes))` returns the same node/edge count;
- `nx.read_gexf(io.BytesIO(gexf_bytes))` returns the same node/edge count;
- structured reaction sides are canonical JSON strings in GraphML/GEXF;
- serialization never mutates the input graph.

Assert dependency version bounds with `packaging.version.Version` only if packaging is already transitively available; otherwise compare the integer major version.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py tests/test_dependency_contract.py
```

Expected: adapter/serializer imports fail.

- [ ] **Step 3: Build the MultiDiGraph**

Set graph attributes:

```python
graph.graph.update(
    schema_version=payload["schema_version"],
    network_semantics="mechanism",
    evidence_level=payload["evidence_level"],
    anchor_smiles=payload["anchor_smiles"],
)
```

Add nodes and directed edges without losing stable IDs. Use edge keys derived
from `role`, reaction ID, and species ID. Coerce `None` to `""`, booleans to
integers, and lists/dicts to
`json.dumps(value, sort_keys=True, separators=(",", ":"))` for GraphML/GEXF.

- [ ] **Step 4: Implement serializers using NetworkX**

Use:

```python
nx.cytoscape_data(graph)
nx.write_graphml(graph, buffer, encoding="utf-8")
nx.write_gexf(graph, buffer, encoding="utf-8")
```

Reject unknown formats with a `ValueError` listing the three valid values. Do not write files inside the serializer; return data/bytes so CLI/Dash decides the destination.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py tests/test_dependency_contract.py
git add rng_tools/mechanism_graph.py tests/test_mechanism_graph.py tests/test_dependency_contract.py
git commit -m "feat: add networkx mechanism graph adapters"
```

---

### Task 3: Mechanism service, CSV exports, and semantic firewall

**Files:**
- Modify: `scripts/webapp_dash/services.py`
- Create: `tests/test_mechanism_network_services.py`
- Modify: `tests/test_transition_table.py`

**Interfaces:**
- Produces: `build_mechanism_elements(artifacts, **query) -> dict`
- Produces: `export_mechanism_graph(payload, format) -> dict | str | bytes`
- Extends existing observation payload labels without changing its element shape

- [ ] **Step 1: Write failing service/export tests**

Assert:

```python
mechanism = svc.build_mechanism_elements(artifacts, anchor_smiles="A")
observation = svc.build_observation_elements(artifacts)
assert mechanism["network_semantics"] == "mechanism"
assert mechanism["evidence_level"] in {"reaction_passage_counts", "event_evidence_linked"}
assert observation["network_semantics"] == "event_transfer"
assert observation["evidence_level"] == "aggregate_observation"
assert "kinetic_flux" not in json.dumps([mechanism, observation])
```

Test node CSV columns:

```text
id,kind,label,smiles,formula,reaction_key,reactants_json,products_json,
forward_tp,reverse_tp,net_tp,event_total,matched_event_total,event_coverage,evidence_status
```

Test edge CSV columns:

```text
id,source,target,role,species_smiles,coefficient,reaction_key
```

Monkeypatch event source CSV opens to fail and prove enrichment is SQLite-only.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_network_services.py tests/test_transition_table.py
```

Expected: mechanism service missing and observation labels absent.

- [ ] **Step 3: Implement the service and semantic labels**

Load/cache `.reactionabcd` with the existing service convention, optionally construct `EventIndexEvidenceProvider`, call the domain builder, convert through NetworkX, and map `nx.cytoscape_data` into the existing Cytoscape `elements` shape.

Add only top-level labels to `build_observation_elements`; do not rename existing observation node/edge count fields.

- [ ] **Step 4: Implement exports from one stored payload**

`export_mechanism_graph` accepts the already computed mechanism payload. Graph formats rebuild the NetworkX graph from that payload. CSV formats serialize its node/edge arrays. Supported values:

```text
cytoscape-json, graphml, gexf, node-csv, edge-csv
```

Graph JSON includes schema version, network semantics, evidence level, query, and source signatures. CSV writes structured sides as compact JSON cells.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_network_services.py tests/test_transition_table.py
git add scripts/webapp_dash/services.py tests/test_mechanism_network_services.py tests/test_transition_table.py
git commit -m "feat: serve and export mechanism networks"
```

---

### Task 4: Dual-semantics Dash network workspace

**Files:**
- Modify: `scripts/webapp_dash/app.py`
- Modify: `scripts/webapp_dash/callbacks.py`
- Modify: `scripts/webapp_dash/assets/app.css`
- Modify: `tests/test_dash_smoke.py`
- Modify: `tests/test_mechanism_network_services.py`

**Interfaces:**
- Adds control: `network-semantics`
- Adds mechanism controls: anchor, direction, depth, min net TP, max nodes, evidence filter
- Adds export downloads: JSON, GraphML, GEXF, node CSV, edge CSV
- Consumes: Plan 2 `pathway-store` highlight selection

- [ ] **Step 1: Write failing layout and callback tests**

Assert these IDs are present:

```text
network-semantics
network-anchor-smiles
network-direction
network-depth
network-min-net-tp
network-max-nodes
network-evidence-filter
network-json-btn / network-json-download
network-graphml-btn / network-graphml-download
network-gexf-btn / network-gexf-download
network-node-csv-btn / network-node-csv-download
network-edge-csv-btn / network-edge-csv-download
network-detail-panel
```

Callback tests must prove:

- observation mode calls only `build_observation_elements`;
- mechanism mode calls only `build_mechanism_elements`;
- a selected reaction node renders full sides and passage/evidence metrics;
- selected species from species/pathway pages becomes the mechanism anchor;
- path highlighting adds stylesheet classes without removing nonpath elements;
- all exports use `network-store` and do not rebuild.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_mechanism_network_services.py
```

Expected: new controls and callbacks are absent.

- [ ] **Step 3: Refactor the current network controls by semantics**

Keep one Cytoscape canvas and shared layout selector. Use a clear two-option selector:

- `机制网络（reactionabcd）`
- `观察网络（table）`

Show only the applicable query controls. Observation defaults remain unchanged. Mechanism defaults are `both`, depth `2`, min net TP `1`, max nodes `200`.

Display a persistent badge from the payload:

```text
mechanism · reaction passage counts
event_transfer · aggregate observation
```

Never render “kinetic flux” for either current view.

- [ ] **Step 4: Wire selection, event handoff, highlighting, and downloads**

Reaction-node detail includes a button that sets `event-reaction-text` and navigates to the event page. Path highlight compares exact `reaction_key` and species SMILES from `pathway-store`; it must not infer matches from formulas.

Use `dcc.send_bytes` for GraphML/GEXF and `dcc.send_string` for JSON/CSV. File names include the dataset ID, semantics, and schema version-safe suffix.

- [ ] **Step 5: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_dash_smoke.py tests/test_mechanism_network_services.py tests/test_transition_table.py
git add scripts/webapp_dash/app.py scripts/webapp_dash/callbacks.py scripts/webapp_dash/assets/app.css tests/test_dash_smoke.py tests/test_mechanism_network_services.py
git commit -m "feat: separate mechanism and observation networks"
```

---

### Task 5: Graph algorithm contract tests

**Files:**
- Modify: `rng_tools/mechanism_graph.py`
- Modify: `tests/test_mechanism_graph.py`

**Interfaces:**
- Produces: `mechanism_graph_metrics(graph) -> dict`
- Uses: NetworkX reachability, weak components, and centrality

- [ ] **Step 1: Write failing graph-analysis tests**

On a known two-component graph, assert:

- weak component count and sizes;
- exact anchor-reachable species IDs;
- degree centrality includes species and reaction nodes but is reported by stable ID;
- output ordering is deterministic;
- an empty graph returns zero/empty values rather than raising.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py
```

Expected: metrics function is missing.

- [ ] **Step 3: Implement bounded analysis**

Use `nx.weakly_connected_components`, `nx.descendants`, `nx.ancestors`, and `nx.degree_centrality`. Return only JSON-safe, sorted summaries. Do not run expensive all-pairs algorithms or centrality beyond the already bounded `max_nodes` graph.

- [ ] **Step 4: Verify GREEN and commit**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py
git add rng_tools/mechanism_graph.py tests/test_mechanism_graph.py
git commit -m "feat: report bounded mechanism graph metrics"
```

---

### Task 6: Documentation and milestone verification

**Files:**
- Modify: `README.md`
- Create: `docs/network-semantics-and-export.md`

- [ ] **Step 1: Document the semantic model and interoperability**

Include:

- a bipartite species/reaction diagram;
- why mechanism passages, event transfer, and future kinetic flux are not interchangeable;
- neighborhood limits and truncation;
- export schemas and NetworkX loading examples;
- Cytoscape JSON, GraphML, and GEXF usage;
- event and pathway handoffs.

- [ ] **Step 2: Run final verification**

```bash
.venv/bin/python -m pytest -q tests/test_mechanism_graph.py tests/test_mechanism_network_services.py tests/test_transition_table.py tests/test_dash_smoke.py
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q reacnet_scope rng_tools scripts tests
git diff --check
```

Expected: all tests pass, compileall exits `0`, and `git diff --check` prints nothing.

- [ ] **Step 3: Commit**

```bash
git add README.md docs/network-semantics-and-export.md
git commit -m "docs: define reaction network semantics and exports"
```

## Milestone Acceptance

- Mechanism and observation networks have different, tested semantics and evidence labels.
- Multi-reactant/multi-product stoichiometry survives payload, NetworkX, Cytoscape, GraphML, GEXF, and CSV serialization.
- The mechanism view is bounded, deterministic, evidence-aware, and can highlight Plan 2 paths.
- No current code or UI labels event counts/passages as kinetic flux.
