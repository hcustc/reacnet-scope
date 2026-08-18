# Species Fate Analysis（物种命运分析）

状态：已接受  
日期：2026-08-16

本文定义 Species Fate Analysis 的产品语义、统计口径、证据边界、结果契约和
MVP 范围。领域术语以根目录 [`CONTEXT.md`](../CONTEXT.md) 为准，通用产品约束以
[`software-design-baseline.md`](software-design-baseline.md) 为准。

## 1. 目标与结论边界

Species Fate Analysis 回答：一个精确 Species 的具体生成实例形成后，其固定锚点
原子在已观测 Reaction Occurrence 中分别进入了哪些用户定义终点，以及这些结局、
首次出口和时间尺度在形成事件中的分布。

该分析是有界的证据遍历，不是通用自动路径发现。它只沿已经观测到的、具有严格
分子实例和原子连续性的事件前进，不跨越证据断点，不补全未观测步骤，不评价化学
合理性，也不声称因果、唯一机理或绝对“最终产物”。有限观察窗内没有到达终点的
后代属于删失证据，而不是未知结局的反证。

Path Verification、Molecule Lineage 与 Species Fate Analysis 保持独立：

- Path Verification 验证一条用户给定的 Reaction Type 序列；
- Molecule Lineage 以一个具体 Molecule Instance 为中心进行有界双向查看；
- Species Fate Analysis 汇总一个精确 Species 的全部合资格 Formation Episodes。

## 2. 证据与能力契约

Fate 统计的最小依赖是 Reaction Evidence、Molecular Evidence 和由二者离线构建的
molecular continuity substrate：

- 优先使用有效且完整的 `.timeline.h5`；
- 仅当原生来源不存在时，使用能够等价恢复所需证据的
  `.reactionevent.csv + .molecules.csv`；
- 不读取 `.route`，不从轨迹坐标重新检测反应或覆盖 RNG 键变化；
- Species Abundance Index 不是统计依赖，只能提供可选的丰度上下文；
- trajectory 和 Trajectory Index 不是统计依赖，只控制按需局部坐标查看。

局部 unresolved occurrences 可以存在。它们进入证据诊断，并在阻断具体后代时形成
Evidence Censoring，不要求整个来源零缺陷。

Dataset-level capability 状态固定为：

- `READY`：continuity schema 与当前数据修订一致；
- `BUILD_REQUIRED`：证据充分但尚未准备；
- `REBUILD_REQUIRED`：已有 Event Evidence Index 可服务旧功能，但缺少或过期的
  continuity schema；
- `SOURCE_INSUFFICIENT`：缺少 Reaction Evidence 或 Molecular Evidence；
- `SOURCE_INVALID`：来源损坏、互相冲突或 schema 不受支持。

查询参数错误单独返回，不改变 dataset capability。

## 3. 两阶段架构

`prepare event` 离线构建 revision-bound、query-independent 的 molecular
continuity substrate，至少包含：

- 规范化 Molecule Instances；
- Reaction Occurrence 与两侧 participants 的关系；
- 每个实例的下一次事件参与关系；
- 同一 Transition 的歧义、anchor 完整性及其他连续性诊断。

准备阶段不预计算 target-specific anchors、endpoint hits 或 fate results。查询阶段才
根据目标 Species、anchor policy、endpoint categories 和观察参数创建并遍历
Formation Episodes。Dash 不为每个生成实例循环调用单实例 Molecule Lineage；不同
endpoint 配置也不产生永久的预计算 fate 索引。

## 4. 身份与来源范围

所有 Atom IDs 都具有 replicate scope。MVP 每次只分析一个 Current
Dataset/Replicate，但下列身份从第一版起都携带 replicate：

- **Molecule Instance identity**：由 replicate、Analyzed Frame、稳定 Species
  identity、完整 Atom IDs 和规范化分子内键集合确定，与事件侧别和存储布局无关；
- **Formation Episode identity**：由稳定 birth event、birth instance 和固定 anchor
  set 确定，不包含 endpoint 配置；
- **Fate Query identity**：由规范化 target、anchor policy、endpoint categories、
  observation window 和 limits 确定；
- **Fate Result identity**：在 Fate Query 之外绑定 dataset revision、continuity
  algorithm version 和 fate algorithm version。

所有导出保存稳定 ID、来源签名、schema/算法版本、replicate scope 和完整规范化
查询。兼容证据格式之间迁移时，已解析语义身份不得依赖 CSV/HDF5 行号或布局 ID。

## 5. Fate Query

一个规范化 Fate Query 至少包含：

- Current Dataset/Replicate；
- target 的稳定 Species identity；
- anchor policy；
- 命名、互斥的 endpoint categories；
- formation window 与 follow-up endpoint；
- 可选 minimum follow-up；
- per-episode、detail-retention 和 global limits；
- 时间轴显示与已确认的物理时间换算（若有）。

### 5.1 Anchor policy

默认 `heavy_atoms`：episode 创建时，将目标 Species 当前实例中的全部非氢原子固定
为 anchor set，并在整个生命周期中保持不变。后续新加入的原子不会成为 anchors。
用户可以显式选择全部原子、指定元素或指定 Atom IDs。

目标 Species 不含非氢原子，或缺少可靠 atom ID→element 映射时，默认策略失败关闭。
轨迹不是 Fate 统计的必需来源；当元素映射无法从其他可信证据获得时，用户必须显式
改用不依赖元素推断的 policy。软件不得根据 SMILES 顺序猜测 Atom ID。氢转移完整
保留在事件和键变化证据中，但默认不形成独立 fate descendant。

### 5.2 Endpoint categories

MVP 只接受命名的、互斥的精确 Species 集合。Species 从当前数据集的 catalog 选择，
使用稳定内部 identity，RNG SMILES 是其结构表示。

- 同一 Species 不得属于多个类别，冲突在运行前失败关闭；
- target Species 本身不得属于 endpoint category；
- 分子式、SMARTS、组成范围和隐式优先级不属于 MVP；
- 未匹配 endpoint 的 descendant 继续追踪，直到命中终点或被删失。

### 5.3 Observation window

Formation window 与 follow-up endpoint 完全分离：

- 位于 formation window 且满足 birth rule 的形成都进入 `formation_count`；
- episode 可以越过 formation window 继续追踪到 follow-up endpoint；
- 可选 minimum follow-up 定义主 branching cohort；潜在随访不足的 episode 标记为
  `insufficient_followup`，不进入主 cohort；
- 不对未知 fate 做插补。

## 6. Formation Episode 创建与去重

只有 `matched` Reaction Occurrence 的 product side 明确出现一个新生目标 Species
Molecule Instance 时，才创建 formation candidate。同一 occurrence 的 reactant side
若已有 Species、完整 Atom-ID 集合和分子内键集合完全相同的实例，则该实例是持续
存在，不是新形成。

Candidate anchor set 与某一 ACTIVE episode 的固定 anchor set 完全相同时，candidate
是 episode 内 target return，不创建新 episode。只有部分重叠时仍创建新 episode，并
记录全部 `overlapping_active_episode_ids` 和共享 anchor IDs。不同 episodes 可以共享
anchors；统计始终是描述性的，不把 episodes 当作独立实验重复。

同一 candidate anchor set 若同时精确匹配多个 ACTIVE episodes，属于内部 invariant
violation，整个 Fate Result 为 `failed`。

分析窗口起点已经存在、但没有可观测生成事件的目标实例标记为
`left_censored_initial_instances`。它们可以单独追踪，但默认不进入 formation count
或 branching probability。Unresolved occurrences 不创建 episode；报告只陈述已知
unresolved evidence 的数量和范围，不推断未知的 missed formations。

## 7. Descendant continuity

每个 active descendant 只沿最近可观测事件前进：

1. 找到其任一 anchor 首次出现在 reactant side 的后续 Transition；
2. 该 Transition 内必须恰有一个 occurrence，以当前 descendant 的精确 Species 和
   完整 Atom-ID 集合唯一匹配 reactant participant；
3. 只根据该 occurrence 的 product-side Molecule Instances 分配 anchors；
4. 新 descendant 的 anchor subset 是其 product instance Atom IDs 与 episode 固定
   anchor set 的交集。

无法唯一匹配、anchor 重复分配、anchor 丢失，或同一 Transition 内事件依赖不能确定
时，相关 descendant 立即 `evidence_censored`。不得跨过该 Transition 与更晚事件重新
连接，同一 Transition 内也不得人为安排 Reaction Occurrences 的顺序。

同一 episode 的多个 descendants 在一个事件中重组到同一 product instance 时，合并
其 anchor subsets，并只生成一个后续 descendant。不同 episodes 即使进入同一分子，
仍按各自固定 anchors 独立追踪。

一个 occurrence 若在 product side 保留相同 Species、完整 Atom-ID 集合和分子内键
集合，则 Molecule Instance 身份连续；它可以作为旁观参与证据，但不会结束首次驻留。

## 8. Descendant-complete fate

Descendant 首次精确匹配任一 endpoint category 时 first-passage，并冻结为一个
Terminal Instance。冻结后不再追踪该 descendant，也不会因后续离开或重新进入终点
而重复计数。

拆分后，并行追踪所有携带至少一个 anchor 的 descendants。Episode 只有在所有
descendants 已 terminal-frozen 或 censored 后才关闭：

- 所有 frozen terminal anchor subsets 必须两两不重叠；
- 它们的并集必须恰好等于 episode 的完整固定 anchor set；
- 满足上述精确分区且没有 censored descendants 时，episode 才是 fully resolved；
- 任一 missing、duplicate、未分类 active descendant 或 censored anchor subset 都使
  episode unresolved，并保存 Partial Fate 与逐 subset 原因。

不携带 anchor 的共同产物和离去片段只作为 reaction context。多个 descendant anchor
subsets 重组到同一 terminal instance 时先取并集，该 terminal 只计一次。

一个 Descendant-complete Fate 表示为无序多重集合。每个 Terminal Instance 保存：

- endpoint category；
- 继承的 anchor Atom IDs；
- first-passage event、Transition/frame/timestep；
- 从 birth 到 first passage 的原始连续性证据。

跨 episode 聚合时忽略具体 Atom IDs，只按 endpoint category 与 multiplicity 构造
canonical Fate Signature。例如 `{A × 2, B × 1}` 与 `{A × 1, B × 1}` 是不同结局。
`first_hit_fate` 单独记录最早 Transition 首次命中的 endpoint 多重集合，但不作为整个
episode 的默认 fate；同一 Transition 的并列命中不建立内部顺序。

## 9. Raw trace 与路径聚合

算法始终按完整 raw event trace 计算：episode 内 target returns、可逆循环和非终点
循环都按原始 Reaction Occurrences 连续追踪，不折叠、不跳过。每个 episode 至少保存
完整的稳定 event-ID trace spine 与 anchor allocation；显式 detail-retention policy 可以
省略可由 continuity substrate 重建的展开事件 payload，但不能省略 trace identity 或
改变统计。可选 `persistent_view` 可以按严格结构规则折叠显示，但不改变 formation、
fate 或时间统计。

MVP 跨 episode 只聚合：

- canonical Fate Signature；
- target 首次真正失去 Molecule Instance 身份时的精确 `first_exit_channel`；
- lineage topology 始终为单一 descendant 时，从 birth 之后到 terminal first passage
  的规范化 `linear_reaction_type_sequence`。

存在 split、merge/recombination 或其他复杂拓扑的 episode 保存完整 raw trace、anchor
allocation 和事件图，并标记 topology；MVP 不为完整 branching/recombination graph
建立 canonical path identity，也不发布相应路径频次。Observed Fate Paths 只表示已有
原子连续证据，不是机理路径。

## 10. 时间定义

不使用单一含混的 residence time。每个结果至少保留：

- `initial_residence_time`：birth occurrence 的 `after_timestep` 到目标精确 Molecule
  Instance 首次真正失去身份的消费 occurrence 的 `before_timestep`；
- `terminal_first_passage_time`：birth `after_timestep` 到每个 Terminal Instance
  first-passage occurrence 的 `after_timestep`；
- `first_hit_time`：episode 所有 terminal first-passage times 的最小值；
- `descendant_completion_time`：最后一个 descendant 命中终点的时间，只对 fully
  resolved episodes 定义；
- `cumulative_target_branch_time`：episode 内所有 descendant 分支再次成为 target
  Species 时，其连续占据区间之和；并行实例分别累加，因此这是 branch-time。

旁观参与但身份连续的 occurrence 不截断 initial residence。所有时间同时保留 Analyzed
Frame、source timestep 和 Transition 边界。只有转换关系来自证据或经用户确认后才
派生 ps/ns；不得推断 Transition 内部的精确反应时刻。尚未观测到首次退出或终点时，
相应时间是删失区间或 `NA`，不得记为 0。

## 11. 统计契约

默认 fate 与 multiplicity 统计只使用主 cohort 中 fully resolved episodes。Censored
episodes 只进入 censoring statistics；分母为零时派生值为 `NA`，不是 0。

```text
formation_count
  = formation window 内全部非左删失 created episodes

resolved_episode_conditional_probability(signature)
  = 主 cohort 中具有该 Fate Signature 的 fully resolved episodes
    / 主 cohort 中 fully resolved episodes 总数

marginal_occurrence_probability(category)
  = 主 cohort 中至少含一个该 category terminal 的 fully resolved episodes
    / 主 cohort 中 fully resolved episodes 总数

unconditional_mean_multiplicity(category)
  = 主 cohort fully resolved episodes 中该 category terminals 总数
    / 主 cohort fully resolved episodes 总数

conditional_mean_multiplicity(category)
  = 主 cohort fully resolved episodes 中该 category terminals 总数
    / 其中至少含一个该 category terminal 的 episodes 数

first_exit_channel_conditional_probability(channel)
  = 主 cohort fully resolved episodes 中具有该 first-exit channel 的 episodes
    / 主 cohort fully resolved episodes 总数

linear_path_conditional_probability(sequence)
  = 主 cohort fully resolved、且 topology 始终为单一 descendant 的 episodes 中
    具有该 linear Reaction Type sequence 的 episodes
    / 同一 topology-eligible episode 总数

censoring_fraction
  = censored episodes / created episodes
```

`resolved_episode_conditional_probability` 只是在 eligible、fully resolved episodes
中的条件分布，不得无修饰地解释为总体 fate probability。报告必须同时给出：

- formation count、主 cohort size 和 fully resolved count；
- resolution fraction；
- 按原因分解的 censoring counts/fractions；
- topology-eligible fraction 和未聚合的复杂 topology 数量；
- formation-time-dependent resolution rate；
- 潜在和实际 follow-up distributions。

Formation count 是观测次数，不自动解释为速率。可选的每 Analyzed Frame、每 source
timestep 或每物理时间归一化只称为 formation event frequency/rate，并同时报告实际
exposure；它不是化学速率常数。

MVP 不对 Descendant-complete Fate 强制进行删失校正。后续可以针对 first-hit fate
独立设计 competing-risk analysis。

## 12. Censoring 与结果完整性

Episode-level censoring 与计算过程 global incompleteness 必须分开：

- evidence ambiguity、follow-up endpoint、per-episode 最大事件数或 active-branch 数
  等，都按具体原因作用于相应 anchor subset；
- `insufficient_followup` 是主 branching cohort 的 eligibility exclusion，不会仅因潜在
  随访不足而改写一个实际已 resolved episode 的证据状态；
- 只要完整目标 cohort 已扫描和处理，Result 仍是 `complete`，可以发布完整 formation、
  censoring 和 resolved-episode conditional statistics；
- detail retention 只控制 raw-detail 表和 UI 中保留多少证据，不改变统计；
- 任何使 formation scan 或 fate traversal 未覆盖完整目标 cohort 的全局限制、取消或
  错误，都使 Result 为 `incomplete`；主 branching probability 为 `NA`，不得用已处理
  前缀估计总体分布；
- `failed` 表示输入、来源或内部 invariant 已无法形成可信分析结果。

顶层 Result 状态固定为 `complete`、`incomplete` 或 `failed`。`complete` 可以包含正常
censored episodes；这不等于计算不完整。

## 13. Dash 工作流

Species Fate Analysis 是侧栏“事件证据”下的独立页面，并允许从 Species 查询、时间
演化或 Molecule Lineage 携带稳定 Species identity 进入。页面使用四步工作流：

1. **Dataset & Target**：确认 Current Dataset、continuity capability 和 target；
2. **Fate Definition**：配置 anchor policy 与互斥 endpoint categories；
3. **Observation & Limits**：配置 formation window、follow-up、minimum follow-up 和
   limits；
4. **Run & Results**：执行并查看结果。

结果区按 Summary、Fate Signatures、Endpoint Marginals、Pathways、Time
Distributions 和 Episodes/Raw Evidence 组织。通过稳定 `event_id`、
`molecule_instance_id` 和 `formation_episode_id` 在工具之间交接。

## 14. 局部轨迹与导出

Fate Analysis 不读取、缓存或导出 episode-wide MD 坐标。Raw trace 只保存事件身份、
Transition/frame/timestep、Molecule Instance 和 anchor 关系。点击 event 或 Terminal
Instance 时，通过稳定 `event_id` 按需调用现有局部轨迹查看器并读取有限时间窗口。
缺少 Trajectory Index 只禁用坐标查看和相应导出，不影响 Fate 统计。

正式导出有两种：

- `fate-result.json`：完整、层级化、可重建的 canonical Fate Result；
- `fate-tables.zip`：确定性的关系化导出，包含 `manifest.json` 及具有固定行粒度、
  确定排序和版本化 schema 的 CSV 表。

关系表至少覆盖 summary、fate signatures、endpoint marginals、episodes、terminal
instances、episode events、molecule instances、continuity edges、censoring 和 time
statistics，并通过稳定身份连接。Detail retention 影响 raw-detail 表时，manifest 和
episode-level 字段必须记录覆盖范围、选择规则和 `details_complete=false`，但完整聚合
统计不变。JSON/CSV 不嵌入坐标；episode-wide trajectory export 不属于 MVP。

## 15. 多 Replicate 边界

MVP 不跨 replicate pooling。后续多 replicate 分析必须先在每个 replicate 内独立
计算 formation、resolution、censoring、fate 和时间统计。Pooled-episode 结果只能作为
明确标注的描述性统计；condition-level summary 默认以 replicate 为统计单位，等权
汇总 replicate-level probabilities，并报告有效 replicate 数。

跨 replicate 的不确定性和置信区间必须来自 replicate-level variation，不能把同一
轨迹内的 episode 数当作独立实验重复。

## 16. MVP 验收场景

实现至少覆盖以下行为：

1. `target → B → target` 使用相同固定 anchor set 时只创建一个 episode，但 raw trace
   保留全部事件；
2. target 再形成时 anchor set 仅部分重叠会创建新 episode，并记录重叠关系；
3. 同一 candidate 精确匹配多个 ACTIVE episodes 会使 Result `failed`；
4. split 后两个同类 terminal instances 形成 multiplicity 2，且 anchor subsets 构成
   精确分区；
5. descendants 重组到同一 terminal instance 后只计一个 terminal；
6. 同一 Transition 的并列 first hits 不被人为排序；
7. 身份连续的旁观 occurrence 不结束 initial residence；
8. 最近事件歧义、anchor 丢失或重复分配会 censor，且不会连接更晚事件；
9. initial left censoring、follow-up right censoring、insufficient follow-up 和资源删失
   分开报告；
10. 任何 censored anchor subset 都阻止 episode 成为 fully resolved，但保留 Partial
    Fate；
11. 零分母统计返回 `NA`；全局不完整时主 branching probability 也返回 `NA`；
12. 复杂 topology 不产生 MVP canonical path frequency；线性 episode 可以聚合规范化
    Reaction Type sequence；
13. 缺少 trajectory index 时统计仍可完成，局部坐标操作明确禁用；
14. 兼容来源布局迁移保持语义身份，JSON 和多表 ZIP 导出具有确定性。

## 17. 明确延期项

以下内容不阻塞 MVP 的领域和统计契约，但需要在实施计划中另行确定：

- per-episode、global 和 detail-retention limits 的产品默认值；
- endpoint category presets 是否持久化到 Dataset Workspace；
- 结果图的具体图形编码和大数据降采样策略；
- Fate Result 的缓存、失效和后台任务交互；
- 非精确 Species endpoint rules；
- 完整 branching/recombination graph canonicalization；
- first-hit competing-risk analysis；
- episode-wide trajectory export；
- 多 replicate 与 Simulation Condition 分析。
