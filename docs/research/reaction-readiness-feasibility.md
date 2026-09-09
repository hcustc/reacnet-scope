# Reaction Readiness Checker 可行性调研

调研日期：2026-08-30

范围：以当前工作树的领域契约、源码 API 和测试为主，并对照 ARC、ChemTraYzer3、QCSchema 与 Arkane/RMG 的官方资料。这里的 checker 判断一个具体 RNG Reaction Occurrence 能否交给外部 TS optimization / frequency / IRC 流程，不判断机理是否真实。

## Go / No-Go

**Go：开发 occurrence-level “QC handoff readiness”。** 当前数据足以自动检查事件身份、两侧原子映射、RNG 成断键、精确轨迹帧、Å、元素映射、PBC 重建、分子拓扑、坐标质量和来源签名。最小实现可复用现有 DFT geometry builder，不需要新页面。

**No-Go：当前不能自动给出 “gas-phase TST/RRKM ready”。** 现有 schema 没有相态/环境模型、elementary-step 证明、优化 minima/TS、振动频率、IRC、统一能量参考、构象/转子/对称数或碰撞能量转移参数。`matched` 只表示 occurrence 已关联精确 Molecular Evidence，不表示它是基元步骤或适合气相动力学。[领域定义](../../CONTEXT.md#L39-L48)

必须输出两个独立层级，不设顶层 `reaction_ready=true`：

- `qc_handoff.status`: `blocked | needs_input | review_required | ready`
- `kinetics_applicability.status`: v1 只允许 `insufficient_evidence | manual_review_required | not_applicable`，不提供 `ready`

产品名称建议用“量化交接准备检查”；“动力学适用性”作为独立人工复核区。

## 现有基础

### 1. occurrence 身份和证据字段已经足够

Event Evidence payload 已有稳定 `event_id`、`reaction_key`、before/after timestep、Atom IDs、两侧 bonds、两侧 participants、association status 和 occurrence 序号。[payload](../../reacnet_scope/event_index.py#L275-L368) 数据库以 `event_id` 为主键保存同样字段。[schema](../../reacnet_scope/event_index.py#L723-L739)

`get_event` 按稳定主键读取并校验 payload；`query_events` 按 canonical Reaction Type 分页，返回 source signatures、association availability 和 time basis。[单事件读取](../../reacnet_scope/event_index.py#L3306-L3339)；[分页查询](../../reacnet_scope/event_index.py#L3341-L3418)

checker 主体应是 `dataset_id + source_revision_fingerprint + event_id`。`reaction_key` 只用于聚合支持度；一个 occurrence 的结论不能自动提升为整个 Reaction Type 或机理的结论。

### 2. 已有廉价预筛，但不是 QC readiness

`rank_representative_events` 已按 matched atom association、trajectory index 覆盖 before/after、是否有可区分成断键，把 occurrence 分成 `recommended / reviewable / unavailable`。[实现](../../reacnet_scope/analysis_services.py#L1943-L2015)

它的 docstring 只声称适合 local trajectory validation，没有检查原子平衡、元素、单位、电子态、PBC/拓扑质量或动力学适用性。[语义](../../reacnet_scope/analysis_services.py#L1949-L1954) 当前 Dash 事件页也直接调用 `locate_rng_events`，没有把这个排序当成最终 readiness。[回调](../../scripts/webapp_dash/callbacks.py#L6751-L6807)

结论：可保留为事件列表 cheap prefilter，不能重命名成 QC/TST ready。

### 3. DFT geometry builder 是主要复用点

当前 builder 明确只生成 initial geometry，不搜索 TS、不优化、不生成完整 QC job。[模块边界](../../reacnet_scope/dft_geometry.py#L1-L7) 它已经 fail closed 地检查：matched event、`event_id`、trajectory、精确 R/P frames、Å、participants/Atom IDs、元素、有限坐标、分子键图、PBC 环闭合、contact image、原子数，以及用户所给 charge/multiplicity 的基本格式。[participants](../../reacnet_scope/dft_geometry.py#L158-L225)；[精确帧](../../reacnet_scope/dft_geometry.py#L228-L263)；[PBC/拓扑](../../reacnet_scope/dft_geometry.py#L305-L476)；[电子态](../../reacnet_scope/dft_geometry.py#L479-L509)；[几何构建](../../reacnet_scope/dft_geometry.py#L609-L796)

它还产生 large system、stretched evidence bond、short nonbonded contact、pair-check-limited 和 distant fragments warnings。[warnings](../../reacnet_scope/dft_geometry.py#L512-L590) 但这些 warning 分支目前缺少直接单元测试。

## 外部程序调研：流程可执行，但不应重写执行器

- **ARC 已经覆盖执行层。** ARC 的输入契约支持 Species/Reaction、XYZ、charge、multiplicity、多个 TS guesses，以及 optimization、frequency 和 IRC job；其结果模型还记录 imaginary frequencies、normal-mode 与 IRC 检查，并可用两侧 well isomorphism 验证 IRC。因此 Scope 只需生成明确的 occurrence contract、首个 ARC adapter 和导入验证，不必自建 scheduler、parser、restart 或 troubleshooting。[ARC input](https://reactionmechanismgenerator.github.io/ARC/input_reference.html)；[ARC TS/result checks](https://reactionmechanismgenerator.github.io/ARC/api/species.html)
- **ChemTraYzer3 证明了 MD event → QC 的工程先例。** 它的 `ReactiveEvent` 保存 trajectory atom IDs、几何链、path bond orders、bond changes 和近似 TS，TS investigation 区分 TS optimization、IRC、wrong reaction 和 not-a-reaction 等失败。这说明整条工作流可实现，同时也说明当前 Scope 缺少的逐中间帧 bond order 不能用坐标猜测替代。[ReactiveEvent](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/reference/reaction_sampling.html)；[QM/TS workflow](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/reference/qm.html)
- **QCSchema 可作为后续交换层。** `Molecule` 能表达固定 atom order、geometry、symbols、molecular charge/multiplicity、connectivity、fragments 和 provenance；但其默认 charge/multiplicity 不能替代 Scope 的“未知/待确认”。v1 无需为 readiness checker 引入新依赖，做 ARC adapter 时再评估 QCSchema。[QCElemental Molecule](https://molssi.github.io/QCElemental/v0.30.2/api/qcelemental.models.Molecule.html)
- **Arkane/RMG 证实 kinetics gate 必须独立。** 高压极限动力学除驻点能量和频率外还需要 symmetry、optical isomers、rotors、frequency scaling 等 statmech 数据；pressure dependence 还需要 network、bath gas、collision/energy-transfer model、T/P 和 energy-grain 设置。`can_tst()` 一类完整性判断只说明“数据够计算”，不能证明 TST/RRKM 的物理假设成立。[Arkane input](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html)；[RMG pressure dependence](https://reactionmechanismgenerator.github.io/RMG-Py/theory/pdep/introduction.html)；[RMG Reaction API](https://reactionmechanismgenerator.github.io/RMG-Py/reference/reaction/reaction.html)

这形成了明确分工：Scope 负责 **occurrence 身份、证据质量门、可审计导出和结果 crosswalk**；ARC/量化软件负责实际计算；未来导入器逐项验证收敛、index-1 saddle、normal mode、双向 IRC endpoint 与原 atom map/目标 Species 是否一致。即使这些全部通过，TST/RRKM 模型适用性仍需单独确认。

## 可自动判断 / 需确认 / 不可判断

### A. 可自动判断

| Check | 现有依据 | MVP 结论 |
| --- | --- | --- |
| source revision / index current | Dataset validation 返回 `dataset_id`、revision fingerprint、artifacts；Event Index 对 stale/invalid fail closed。[validation](../../reacnet_scope/dataset_context.py#L62-L142)；[stale 测试](../../tests/test_event_evidence_index.py#L327-L358) | stale/invalid → `blocked` |
| exact matched occurrence | `association_status`、`atom_id_list`、participants | 非 matched → `blocked`；builder 已有 gate。[实现](../../reacnet_scope/dft_geometry.py#L879-L886) |
| R/P 都被选择 | `DftGeometryRequest.include_reactants/products` | paired TS/IRC handoff 必须两侧都有；普通 geometry export 可保留单侧兼容 |
| participant 合法 | 每侧 Species + Atom IDs | 空、非法、同侧重叠 → `blocked` |
| 两侧 Atom IDs 相同 | manifest 已给 `cross_side_atom_ids_match` | paired handoff 必须为 true；当前只是记录，需升级为 gate。[manifest](../../reacnet_scope/dft_geometry.py#L1014-L1065) |
| reaction core 完整 | 两侧 bond difference + selected IDs | changed-bond endpoints 必须被两侧包含；viewer 已计算 core IDs。[实现](../../reacnet_scope/evidence_services.py#L1259-L1265) |
| 有可区分 bond change | RNG 两侧键集合差 | 无变化不能支持定向 TS search → blocked/review；不得从坐标补键 |
| exact R/P frames | trajectory index + frame identity | 缺帧/错帧 → `blocked` |
| 元素、有限坐标、拓扑、PBC | builder normalized geometry | hard error → `blocked` |
| geometry warnings / atom size | manifest warnings + atom thresholds | hard max blocked；其余 → `review_required`，绝不自动修键 |
| provenance | dataset revision + DFT source signatures | report 绑定 revision；revision 变化后 stale |
| Reaction Type 支持度 | `reaction_summary`: total/matched/distinct intervals | 仅信息项，不作 QC blocker。[schema](../../reacnet_scope/event_index.py#L786-L793)；[测试](../../tests/test_event_evidence_index.py#L283-L301) |

### B. 需要用户确认或补充

| Check | 当前状态 | MVP 处理 |
| --- | --- | --- |
| Å 单位 | 已有确认控件和 Workspace setting；软件不猜 | 未确认 → `needs_input`。[实现](../../reacnet_scope/dft_geometry.py#L898-L908) |
| Type → Element | 可来自 trajectory element 列、保存映射或 request | 不完整 → `needs_input`，列出 Atom IDs/types |
| charge/multiplicity | 当前可留空为 `unspecified` | 普通 geometry 可留空；QC handoff profile 必填 |
| charge/electron parity | 所需元素和电子态已有，但当前只检查整数/正 multiplicity | 新增必要条件检查；charge 不守恒 fail，可能 spin crossing 只 warning，不能猜 state |
| partial selection | UI 默认全选但允许取消。[回调](../../scripts/webapp_dash/callbacks.py#L7180-L7228) | 两侧同 Atom IDs + core 完整为硬条件；只遗漏 unchanged spectator → warning并列出遗漏项 |
| gas-phase/isolated-cluster scope | event/trajectory 没有可靠 phase/environment model | 必须人工声明；声明不等于软件验证 |

### C. 当前不可自动判断

| 问题 | 缺口 | v1 输出 |
| --- | --- | --- |
| 是否 elementary step | Transition 是相邻 analyzed frames 之间的区间，内部没有顺序。[定义](../../CONTEXT.md#L15-L17) | `unknown` |
| 连续 product persistence | 中间坐标帧没有 RNG instantaneous bond order。[实现](../../reacnet_scope/evidence_services.py#L1374-L1382) | 只能输出有界回穿三态，见下节 |
| 气相 TST 是否适用 | 缺 phase、solvent/surface/embedding、standard state | `manual_review_required` |
| R/P minima、TS、frequency、IRC | 当前只有 before/after initial geometry | `insufficient_evidence` |
| 能量/热化学可比性 | `EnergyEvidence` 只有 score、ΔE、barrier、unit、source，无 method/reference/ZPE/state identity。[字段](../../reacnet_scope/candidate_paths.py#L181-L193) | `insufficient_evidence` |
| conformers/rotors/symmetry、tunneling | 没有字段或 result importer | `insufficient_evidence` |
| RRKM/master-equation inputs | 缺 wells/channels thermochemistry、bath gas、LJ、energy transfer、T/P grid | `insufficient_evidence` |

## Persistence 的硬边界

Molecule Lineage 只使用 authored RNG events；identity 要求 exact Species + Atom-ID set，fast recrossing 还要求 intramolecular bond set 相同。[模块说明](../../reacnet_scope/molecule_lineage.py#L1-L8) 它能在用户给定 `recrossing_window` 内记录 exact-structure return，并受 depth/node limits 约束。[识别](../../reacnet_scope/molecule_lineage.py#L439-L485)；[API](../../reacnet_scope/molecule_lineage.py#L998-L1083)

因此 persistence check 必须是：

| 状态 | 允许表述 |
| --- | --- |
| `observed_recrossing` | “在 N 个 analyzed transitions 的有界 RNG event evidence 中观测到 exact-structure return” |
| `not_observed_within_bounds` | “本次有界查询未观测到；**不证明持续稳定**” |
| `unknown` | source 无 molecule association，或查询 truncation/ambiguity，或尚未运行 |

现有测试覆盖 observed exact return 和 persistent-view folding。[测试](../../tests/test_molecule_lineage.py#L54-L112) 但 `aggregate_trend == "structurally stable"` 只比较 root/terminal heavy-atom count，不是键态 persistence，checker 不应使用它。[实现](../../reacnet_scope/molecule_lineage.py#L779-L800)

Changed-bond distance trace 同样只能是几何诊断：契约明确说 coordinates 决定 distance，bond states 只来自 RNG before/after evidence。[实现](../../reacnet_scope/event_package.py#L354-L374)

## 最小 schema / API

```json
{
  "schema_version": "reacnet-scope/reaction-readiness/v1",
  "subject": {"dataset_id": "...", "source_revision_fingerprint": "...", "event_id": "...", "reaction_key": "A+B->C"},
  "qc_handoff": {"status": "ready", "checks": [{"id": "selected_atoms_balanced", "status": "pass", "severity": "blocker", "evidence": {}, "remediation": ""}]},
  "supporting_evidence": {"reaction_summary": {}, "persistence_observation": {"status": "not_observed_within_bounds", "claim_limit": "absence is not proof of persistence"}},
  "kinetics_applicability": {"status": "insufficient_evidence", "checks": []},
  "source_signatures": {}
}
```

约束：不设顶层 bool/score；每个非 pass 项必须有 remediation 或 claim limit；report 绑定 source revision；不把 rank、TP、event frequency 或 apparent k 放进 readiness identity。

建议核心 API 与现有 builder 保持同一 seam：

```python
evaluate_reaction_readiness(
    artifacts: Mapping[str, str],
    event: Mapping[str, Any],
    request: ReactionReadinessRequest,
    *, dataset_id: str = "", source_revision: Mapping[str, Any] | None = None,
) -> dict[str, Any]
```

低改动 MVP 可先做便宜 checks，再调用一次 `build_dft_geometry_bundle`，把 manifest/warnings 或 `DftGeometryError.reason` 映射成 report。长期应抽出 normalized geometry validation，供 readiness 和 ZIP serializer 共用，避免复制 private rules。验收不变量：同 request 的 `ready/review_required` report 后，bundle 构建必定成功。[builder](../../reacnet_scope/dft_geometry.py#L872-L877)

Persistence 默认关闭；用户显式选择后才对 product participants 运行 bounded lineage，且必须回传 bounds/truncation。

## MVP UI

不新增页面。现有 trajectory “DFT 初始几何”卡片已有 participant 选择、Å、charge/multiplicity、预检、preview 和 ZIP。[卡片](../../scripts/webapp_dash/app.py#L1914-L2064) 它只在 matched event 与 viewer identity 一致时显示。[gate](../../scripts/webapp_dash/callbacks.py#L7180-L7205)

最小改动：

1. 把当前单一“几何完整性检查通过” alert 改为分组 checklist；callback 已取得 bundle manifest/warnings。[回调](../../scripts/webapp_dash/callbacks.py#L7334-L7419)
2. 默认展开“量化交接准备”；默认折叠“动力学适用性”，固定写“量化交接包就绪不等于气相 TST/RRKM 适用”。
3. `blocked/needs_input` 禁用 handoff ZIP；`review_required` 要求确认 warning；`ready` 允许。
4. persistence 默认“未检查”，按钮触发 bounded check；`not_observed` 必带“不证明稳定”。
5. 不做 0–100 分数、整网批量扫描、新图或新导航入口。

## 性能风险

| 操作 | 风险 | MVP 限制 |
| --- | --- | --- |
| metadata/set checks | O(participants + bonds)，低 | 同步 fail fast |
| `get_event` / summary | readonly indexed SQLite，低 | 每次一 occurrence + 一 summary |
| exact R/P geometry | index 直接 seek 两帧，但 parser 仍读取两份完整 frame | 只对选中 occurrence 运行，不扫事件列表 |
| collision/fragment warnings | selected atoms ≤1000 时 O(n²)；>1000 跳过并 warning。[实现](../../reacnet_scope/dft_geometry.py#L546-L590) | 不提高阈值；未来用 neighbor list |
| multi-component placement | 无 contact edge 时最坏接近 O(n²)。[实现](../../reacnet_scope/dft_geometry.py#L367-L475) | 单事件 + hard atom limit |
| distance window | F 个完整 frames | 仅展开诊断时运行 |
| lineage | 随 participants、visited nodes、分支增长 | opt-in/background；复用 limits；truncated → unknown |

最大风险不是 CPU，而是语义误报：不能把 absence of recrossing 写成 persistence、matched 写成 elementary、geometry export success 写成 kinetics ready、distance 写成 authoritative bond order。

## 验收样例

当前工作树已运行：

```text
uv run pytest -q tests/test_dft_geometry.py tests/test_event_evidence_index.py \
  tests/test_molecule_lineage.py tests/test_event_package.py tests/test_event_export_cli.py
75 passed in 3.87s
```

可复用 fixtures：

- matched `[C]+[O] -> [C][O]`、两侧 `[1,2]`、精确帧/Å/mapping/state → `qc_handoff=ready`，kinetics 仍 insufficient。[测试](../../tests/test_dft_geometry.py#L71-L128)
- `unresolved_hmm_timeline` → `blocked: unresolved_event`。[测试](../../tests/test_dft_geometry.py#L153-L195)
- 未确认 Å → `needs_input`。[CLI](../../tests/test_event_export_cli.py#L221-L243)
- 缺元素 mapping → `needs_input`，失败不得错误保存 unit。[测试](../../tests/test_event_export_cli.py#L246-L290)
- triclinic/partial PBC → pass；inconsistent contact images → blocked。[PBC](../../tests/test_dft_geometry.py#L232-L280)；[contact](../../tests/test_dft_geometry.py#L379-L428)
- exact structure 快速返回 → `observed_recrossing`，kinetics warning，不阻塞普通 geometry export。[测试](../../tests/test_molecule_lineage.py#L54-L112)
- source revision 改变 → stale/blocked。[测试](../../tests/test_event_evidence_index.py#L327-L340)

必须新增：

1. R/P Atom IDs 不同；partial selection 漏 core；只漏 unchanged spectator。
2. electronic state 缺失、charge 不守恒、electron/multiplicity parity 不可能、可能 spin crossing。
3. stretched bond、short contact、distant fragments、`n>1000` warning 分类。
4. lineage 无 episode时只能 `not_observed_within_bounds`；truncation/ambiguity 必须 unknown。
5. intermediate coordinate frames 无论 distance 如何都不能让 persistence pass。
6. 同输入 readiness JSON 确定；source revision 变化后 stale。
7. `ready/review_required` 的同 request bundle 必须成功；blocked 不得产生 handoff ZIP。
8. 默认只读两个 exact frames、不运行 lineage、不枚举全部 occurrence。

## 实施顺序与停止线

1. **MVP-1：QC hard checks + versioned report + 现有 DFT 卡片 checklist。** 停止线：readiness 与 ZIP 构建一致。
2. **MVP-2：Reaction Type 支持摘要 + opt-in bounded recrossing。** 停止线：absence 绝不产生“稳定”。
3. **MVP-3：只展示 kinetics applicability gap list。** 停止线：v1 不存在 kinetics `ready`；不实现 QC executor/parser、TST/RRKM solver。

## 最终判断

工程判断：**Go，但只做 occurrence-level QC handoff preflight。** 当前代码已覆盖大多数硬条件；新增工作主要是统一 check schema，把 R/P Atom-ID balance、reaction-core completeness 和 electronic-state completeness 提升为 gates，并正确呈现 warning/unknown。

科学判断：**No-Go for automatic kinetics readiness；Go for an explicit gap checklist。** 这样能减少用户把“MD occurrence 可导出初始几何”误读为“已验证基元通道、可直接做 RRKM”的风险，而不把 ReacNet Scope 扩张成量化作业或动力学求解平台。
