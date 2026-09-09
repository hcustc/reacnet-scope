# Candidate Path Production Architecture Implementation Plan

状态：Phase 0 设计基准；尚未开始实现

日期：2026-08-28

## 目标与边界

本计划把 [ADR-0013](../../adr/0013-separate-candidate-discovery-from-continuous-md-support.md) 和 [`software-design-baseline.md`](../../software-design-baseline.md) 中确认的 Candidate Path / Continuous MD Support 语义拆成可独立审查、测试、迁移和回滚的小阶段。

本文件只固化实施顺序，不授权在 Phase 0 中修改生产代码、schema 或 UI。实施时每个阶段必须先写契约测试，再做最小实现；任何阶段都不得把 Candidate 描述为完整 sampled occurrence chain，也不得让 Continuous MD Support 改写 Candidate identity、`network_score` 或 `network_rank`。

本计划取代 [`2026-07-24-candidate-pathways.md`](2026-07-24-candidate-pathways.md) 作为 Candidate Path 的后续实施依据。旧计划保留为历史记录；其中固定评分公式、ranker 猜测 focal product、`network_only` 聚合路径、严格 Event Path discovery 和旧 `signature_id` 均不再是目标契约。

## 已固定的架构

```text
raw MD evidence
  -> offline revisioned preparation
  -> published normalized occurrences + observed directed hypergraph
  -> online bounded local Candidate Discovery
  -> network filtering/ranking + Top-K selection
  -> selective sequence-constrained Continuous MD Support
  -> paged Step Evidence / Continuous Support Evidence
```

- Discovery 的图边必须有当前数据集内、当前方向的 normalized Reaction Occurrence 和 Reaction Evidence；聚合网络和推导反向不能单独创建边。
- 普通 Candidate 是 Carried-Species-simple path。每个 carried branch 都是独立 Candidate；重复 Species expansion 只产出 cycle closure evidence。
- `max_steps` 是声明式 horizon；执行预算有独立状态和计数。
- Candidate 有稳定 `candidate_signature`，证据有 revision-bound `candidate_evidence_key`，rank 和查询元数据只属于 Query Result。
- Continuous MD Support 是延迟、可缓存的第二阶段。无显式 retention policy 时只报告事实，不产生二元支持判断。
- 在线 discovery 和单 Candidate validation 都不得扫描 raw source 或构建全局 occurrence graph。

## 未决问题护栏

以下内容必须通过独立设计决策或版本化查询配置解决，不能在任一实现阶段被写成隐藏默认值：

- exploratory ranking 的最终公式、特征权重和缺失特征处理；
- prefix redundancy 的保留、折叠或惩罚策略；
- 长度偏好；
- hub Species 的识别和处理；
- Reaction Cycle Candidate Discovery 的输出、身份和 ranking；
- exploratory Top-K、Continuous Support Top-M 的产品默认值；
- 同一 Candidate 多个 support occurrences 的展示/汇总排序。

在这些决策完成前，可以实现版本化 ranking interface、显式参数和无默认的策略注册，但不能发布隐式产品默认值。

## 跨阶段不变量

1. 所有 canonical keys、signatures、validators、rankers 和结果 schema 都有 semantic version。
2. stable identity 不含 database row ID、数组下标、Occurrence Identity、Replicate、revision、rank、score 或查询参数。
3. 每次在线查询绑定一个已发布 revision；查询期间 active revision 改变不影响已开始查询的一致性。
4. 任何 incomplete/staging revision 均不可被普通读路径发现。
5. stable collections 具有规范化排序；canonical payload 不含 timestamp、job ID、wall time 等 volatile metadata。
6. `not_evaluated`、`complete`、`inconclusive` 与 retention-policy evaluation 分开建模。
7. Step Evidence 永远是逐 step 独立 occurrence 列表；只有 support occurrence 可以声称 ordered molecular provenance。
8. 新实现进入 production 前，旧接口只允许显式 compatibility mode；不得静默复用旧字段名表达新语义。

## Phase 1 — Canonical identity 与 Candidate signature

### 目标

建立独立于存储布局和查询的 canonical Species、directed Reaction Type、Candidate 结构身份。先冻结纯函数契约，不接数据库或 UI。

### 修改模块/文件

- 新建 `reacnet_scope/candidate_identity.py`：canonical key value objects、signature payload 和 semantic versions。
- 复用并收窄 `reacnet_scope/rng_events.py` 的 reaction-side normalization；若旧函数语义不足，新增 API 而非静默改变公共函数。
- 修改 `reacnet_scope/candidate_paths.py`：先仅适配新 identity 类型，不改变 discovery 后端。
- 新建 `tests/test_candidate_identity.py`，补充 `tests/test_rng_event_outputs.py` 和 `tests/test_candidate_paths.py` 的兼容测试。

### Schema / 数据迁移

- 定义 canonical Species key 的规范化输入、输出编码和 `species_identity_semantic_version`。
- 定义 directed Reaction Type key：两侧各自 canonical sort，保留方向和重复计量项。
- 定义 `candidate_signature` 的 canonical payload：anchor Species key、ordered carried Species keys、ordered directed Reaction Type keys。
- 定义 `candidate_evidence_key` 的组合规则，但本阶段不落库。
- 为旧 `signature_id` 提供显式 legacy parser/adapter；不得声明它等价于新 signature。

### 向后兼容

- 旧 JSON 的 `signature_id` 继续只在 v2 compatibility payload 中可读。
- 新 payload 使用新 schema version，同时可暂时附带 `legacy_signature_id`，并明确它不可用于跨 revision 或跨查询身份。
- 不修改 Path Verification 的 Reaction Type sequence 契约。

### 测试

- 单元：side-order 不变、multiplicity 保留、方向反转改变 key、carried chain 改变 signature。
- 性质：任意 side permutation 产生同一 key；任意一项结构字段改变都改变 signature；非身份字段变化不改变 signature。
- fixture：跨不同 dataset-local RNG IDs、row order 和 occurrence IDs 得到相同 signature。
- 反例：相同 reaction-key tuple、不同 carried branch 必须得到不同 signature。

### 确定性要求

- 使用固定字符编码、字段顺序和长度无歧义的 canonical serialization；禁止依赖 Python `repr()`、hash randomization 或 locale。
- golden vectors 固定在测试中，semantic version 不变时不得漂移。

### 完成/验收条件

- identity 模块不依赖 SQLite、Dash、event source 或 ranking。
- golden/property tests 通过，旧 `signature_id` 不再被新代码误称为 Candidate identity。

### 回滚

- 移除新模块和 adapter 即可；尚无持久化迁移。旧 v2 payload 保持原行为。

### 依赖与建议小提交

- 前置：无。
- 后续：Phase 2–10 全部依赖。
- 提交 1：`test: freeze canonical candidate identity vectors`
- 提交 2：`feat: add versioned candidate structural identity`
- 提交 3：`refactor: isolate legacy candidate signature adapter`

## Phase 2 — Normalized occurrences 与 revisioned substrate schema

### 目标

设计并建立可发布 revision 的逻辑 schema，使 direction eligibility、Step Evidence 和后续 continuity 构建都只依赖 normalized records。

### 修改模块/文件

- 修改 `reacnet_scope/event_index.py`，优先把 Candidate substrate 分离到新 `reacnet_scope/candidate_index.py` 或深模块，而不是继续扩大单文件职责。
- 修改 `reacnet_scope/indexes.py`、`reacnet_scope/dataset_context.py`、`reacnet_scope/capabilities.py`。
- 新增 `tests/test_candidate_index_schema.py`、`tests/test_candidate_revision_contract.py`。

### Schema / 数据迁移

逻辑 schema 至少包含：

- `dataset_revisions`：revision identity、source/evidence signatures、schema/semantic versions、state（staging/validated/active/failed）、build provenance。
- `canonical_species` 与 lookup index。
- `directed_reaction_types`：canonical sides、direction、multiplicity、eligibility occurrence count、aggregate metrics。
- `normalized_occurrences`：stable occurrence ID、Replicate、Transition、before/after frame、directed reaction key、association/evidence state、event evidence reference。
- `reaction_reactants` / `reaction_products` 或等价 normalized side table，保留 multiplicity。
- `observed_adjacency`：由 exact product Species 到消费它的 eligible directed Reaction Type 的局部索引；不得存 Candidate enumeration。
- Step Evidence 所需 `(revision, directed_reaction_key, replicate, transition, occurrence_id)` covering index。

首个 production schema 使用新 capability/version，不就地冒充现有 Event Evidence schema。旧 index 可继续服务旧工具，但 Candidate production capability 显示 `REBUILD_REQUIRED`。

### 向后兼容

- 保留现有 events/event-path 读取能力，不修改旧表含义。
- capability 检查区分 `event_evidence`、`candidate_discovery` 和未来 `continuous_support` readiness。
- 新 reader 只接受完整 production revision；禁止从旧 event index 临时拼装“看似 production”的结果。

### 测试

- 单元：聚合 `count=N` 规范化为 N 个 occurrence；只有有证据方向进入 graph。
- 集成：显式 reverse occurrence 可形成反向 directed type；仅 aggregate reverse/net-flux 推导不能形成。
- schema：foreign keys、unique constraints、covering indexes 和 migration version。
- query plan：anchor Species adjacency 与 step evidence page 命中索引。
- 性质：输入 row order 改变不改变 canonical logical content。

### 确定性要求

- stable occurrence ordering 使用 Replicate、Transition、canonical reaction key、participants/duplicate ordinal 的完整 tie-break。
- revision identity 和 source/evidence signatures 的生成规则固定并测试。

### 完成/验收条件

- synthetic dataset 能发布只包含真实记录方向的 hypergraph。
- query plan 无 events full scan；旧 capability 无回归。

### 回滚

- active revision pointer 不切换到新 schema；删除未激活 staging revision。
- 旧 event index 和现有 Candidate compatibility path 继续可读。

### 依赖与建议小提交

- 前置：Phase 1。
- 后续：Phase 3、4、6、8。
- 提交 1：`test: define revisioned candidate substrate schema`
- 提交 2：`feat: materialize normalized directed reaction evidence`
- 提交 3：`feat: expose candidate capability readiness`

## Phase 3 — Streaming preparation、checkpoint 与 atomic publish

### 目标

把 Phase 2 schema 通过 bounded-memory 离线流程可靠构建，并保证失败时旧 active revision 不受影响。

### 修改模块/文件

- 修改 `reacnet_scope/prepare.py`、`reacnet_scope/workspace_services.py`、`reacnet_scope/indexes.py`。
- 新建或扩展 `reacnet_scope/candidate_index.py` writer/read-only publisher seam。
- 修改 `scripts/rng_query_cli.py` 的 `prepare` capability 列表和 Dash 数据管理状态。
- 新增 `tests/test_candidate_preparation.py`、补充 `tests/test_preparation_management.py` 和 `tests/test_online_index_contract.py`。

### Schema / 数据迁移

- staging revision 拥有独立路径或 namespace、checkpoint cursor、phase、row counts 和 checksums。
- 单写者 lock 绑定 dataset identity + capability；重复启动复用同一任务或明确拒绝。
- publish 顺序固定为：完成写入 → integrity checks → fsync/close → 标记 validated → atomic active-pointer replace。
- checkpoint 包含 adapter cursor 和已提交 batch boundary；resume 不重复或跳过 logical rows。

### 向后兼容

- `prepare event` 继续服务旧功能；新增 candidate substrate 可先作为单独 capability，之后再评估合并构建成本。
- CLI/UI 在 active revision 不存在时显示恢复命令，不自动在查询中构建。

### 测试

- 单元：batch boundary、checkpoint serialize/restore、lock ownership。
- 集成：每个 build phase 注入崩溃后 resume；source revision 变化时旧 checkpoint 拒绝发布。
- 原子性：publish 前 reader 只见旧 revision，publish 后新 reader 只见新 revision；failed publish 保持旧 revision。
- 性质：不同 batch size/resume 点得到相同 canonical substrate。
- 资源：fixture 放大时 writer RSS 有界。

### 确定性要求

- batch size、进程中断点和输入物理 row order 不改变 published canonical content。
- volatile build metadata 与 canonical checksum 分离。

### 完成/验收条件

- checkpoint/resume、single-writer、integrity validation、atomic publish 全部有故障注入测试。
- 普通 query 无法打开 staging/failed revision。

### 回滚

- 原子切回前一 active pointer；保留失败 revision 供审计或安全清理。
- 不删除旧 active revision，直到新 revision 通过观察期和空间策略。

### 依赖与建议小提交

- 前置：Phase 2。
- 后续：Phase 4、6、11。
- 提交 1：`test: exercise resumable candidate preparation failures`
- 提交 2：`feat: build candidate substrate with bounded checkpoints`
- 提交 3：`feat: atomically publish candidate revisions`

## Phase 4 — Indexed online hypergraph discovery

### 目标

实现只读 published adjacency 的 bounded local discovery，覆盖 target-constrained 与 exploratory 模式，不访问 occurrences 全集。

### 修改模块/文件

- 新建 `reacnet_scope/candidate_discovery.py`，把 query normalization、state expansion 和 completion accounting 放在一个深模块。
- 修改 `reacnet_scope/candidate_paths.py` 为 domain/result facade；不要复用 `_choose_focal_output`。
- 修改 `reacnet_scope/analysis_services.py`，新增 versioned production service，旧 service 显式标为 compatibility。
- 新增 `tests/test_candidate_discovery.py`、`tests/test_candidate_discovery_queries.py`。

### Schema / 数据迁移

- 只读 Phase 2 的 canonical identity、reaction sides、observed adjacency 和 aggregate metrics。
- 可记录 query telemetry，但不得持久化所有 Candidate Paths。

### 向后兼容

- production endpoint 返回新 schema；旧 `discover_event_paths`/`discover_candidate_paths_for_dash` 暂留但不得作为默认路由。
- compatibility result 明确 `semantics=legacy_sampled_event_path`；新 result 明确 `semantics=network_candidate_path`。

### 测试

- 单元：exact carried joins、co-reactants/context、多 product 分支、multiplicity、显式方向 eligibility。
- 普通路径：Carried-Species-simple；revisit 生成 cycle closure evidence，Fast Recrossing 不参与。
- target-constrained：carried endpoint 才命中、co-product 不命中、首次命中停止、anchor==target 拒绝、中间状态不输出。
- exploratory：短 prefix 和长 prefix 同时输出，短结果后继续扩展。
- budget：分别触发 max expansions/frontier/candidates/wall-time adapter，状态为 truncated/inconclusive。
- horizon：到达 max_steps 只设置 horizon_limited，不设置 execution truncation。
- I/O guard：monkeypatch raw source open 和 full events query 为失败，production discovery 仍通过。

### 确定性要求

- frontier ordering 使用完整 semantic tie-break；数据库返回顺序不得成为隐式排序。
- 同 revision、identity version、query 和 budgets 产生相同 candidates、cycle closures 和 counters。

### 完成/验收条件

- 两种 mode 的状态矩阵完整；本地扩展只读取与 visited carried Species 相关的 index pages。
- `event_paths.py` 不在 production discovery call graph 中。

### 回滚

- 路由 feature flag 切回显式 legacy endpoint；新 read-only index 保留，不影响旧能力。

### 依赖与建议小提交

- 前置：Phase 1–3。
- 后续：Phase 5、7–10、11。
- 提交 1：`test: specify candidate discovery modes and completion states`
- 提交 2：`feat: add indexed carried-species expansion`
- 提交 3：`feat: expose production candidate discovery service`

## Phase 5 — Query status、versioned ranking 与 pagination contracts

### 目标

把 Candidate identity、network metrics、rank 和分页分离；定义稳定完成状态和 Top-K contract，但不偷设未决 ranking 默认值。

### 修改模块/文件

- 新建 `reacnet_scope/candidate_queries.py` 或在 `candidate_discovery.py` 中提供小而完整的 public query/result types。
- 重构 `reacnet_scope/candidate_paths.py` 的 fixed scoring 为 versioned ranker interface。
- 修改 `reacnet_scope/services.py`、`reacnet_scope/analysis_services.py`。
- 新增 `tests/test_candidate_query_contract.py`、`tests/test_candidate_ranking_contract.py`、`tests/test_candidate_pagination.py`。

### Schema / 数据迁移

- Query Result 至少记录 mode、anchor/targets、filters、min/max steps、ranking semantic version/parameters、Top-K/page contract、execution budgets、active revision 和 identity versions。
- Candidate item 保存 `candidate_signature`、`candidate_evidence_key`、结构、network metrics、`network_score`、`network_rank` 及 evidence links；validation 只作为独立 summary reference。
- pagination token 绑定 revision、normalized query、ranking version 和 last semantic sort key；不得只使用 offset 对变化中的结果集翻页。

### 向后兼容

- 旧 `score_version=sampled-candidate-path/v2` 不映射为新 network ranker version。
- adapter 可以输出旧表格列，但必须标明 deprecated/legacy；不能把 `occurrence_count` 当作 Continuous Support。

### 测试

- status matrix：found / not_found_within_constraints / truncated-inconclusive 与 query_complete / graph_exhaustive / horizon_limited 的合法组合。
- pagination：无重复/遗漏、token 与 revision/query 不匹配时拒绝、相同 tie score 稳定。
- ranking interface：相同 metrics/parameters 稳定；missing metric handling 由版本显式定义。
- contract：validation update 不改变 network score/rank；query rank 不改变 candidate signature。
- 未决策略 guard：未选择注册的 ranker 或缺少必需参数时拒绝，不使用隐藏默认。

### 确定性要求

- ranking semantic version、全部参数、tie-break 和 feature normalization 进入 query metadata。
- canonical result payload 与运行时 job metadata 分开。

### 完成/验收条件

- API 能表达所有 completion 状态和分页游标。
- 在 ranking 产品决策未完成时，系统只能接受显式 ranker 配置或保持 capability 非正式；没有隐式 prefix/hub/length 行为。

### 回滚

- 保留旧 response adapter；production route 可暂时禁用新 ranker，而不回退 identity/schema。

### 依赖与建议小提交

- 前置：Phase 4；发布默认 ranking 另需独立决策。
- 后续：Phase 7–11。
- 提交 1：`test: freeze candidate completion and pagination contracts`
- 提交 2：`feat: separate network ranking from candidate identity`
- 提交 3：`feat: add revision-bound candidate pagination`

## Phase 6 — Molecule Continuity Segment 构建

### 目标

把跨连续 Analyzed Frames 的 carrier identity 物化为最大连续 segments，并建立 production/first-consuming Transition links。

### 修改模块/文件

- 新建 `reacnet_scope/continuity_segments.py`，或从 `event_index.py` 的现有 continuity substrate 抽出深模块。
- 修改 `reacnet_scope/event_index.py` / `candidate_index.py` preparation writer。
- 新增 `tests/test_continuity_segments.py`，补充 `tests/test_species_fate.py` 防回归。

### Schema / 数据迁移

- `molecule_continuity_segments`：stable segment ID、revision、Replicate、Species key、atom-ID set reference、bond-set reference、start/end Analyzed Frame。
- production link：product-side normalized occurrence/Transition → segment。
- first-consuming link：segment → first consuming/changing Transition，保留 unique / ambiguous / barrier / disappeared 状态。
- per-Transition compatible occurrence lookup；同一 Transition 不保存人为 occurrence order。
- 旧 `molecule_instances` / `continuity_links` 若复用，必须通过 schema version 证明满足 maximal consecutive interval 语义。

### 向后兼容

- Species Fate 现有 continuity semantics 保持不变；共享底层前先用双写/对照测试证明等价范围。
- 旧 event index 不自动升级为 segments-ready；Continuous Support 显示 `REBUILD_REQUIRED`。

### 测试

- 单元：Species、atom set 或 bond set 任一变化即断段；无变化跨任意 frames；gap 后相同内容产生新 ID。
- Transition：同 Transition 多 occurrences 无顺序；首个 consumer 唯一/竞争/未解析 barrier/无解释消失。
- 性质：每个 molecule-frame record 最多属于一个 segment；segment 区间连续且最大。
- 集成：native HDF5 与 legacy CSV 的等价证据产生相同 logical segments。
- streaming：跨 batch/checkpoint 的 segment 边界正确。

### 确定性要求

- segment ID 基于 revision-scoped stable provenance 和 semantic content，不使用 insertion rowid。
- competing consumers 的排序只用于 canonical presentation，不用于虚构消歧。

### 完成/验收条件

- 全部 segment invariants 和 first-consuming states 可由 index 查询。
- 10^7 目标规模的构建保持 bounded memory；正式数值在 Phase 11 验证。

### 回滚

- 新 segment tables/revision 未激活时删除 staging；激活后可切回前一 revision。
- Species Fate 保留旧 substrate reader，直到共享迁移单独完成。

### 依赖与建议小提交

- 前置：Phase 2–3。
- 后续：Phase 7–9、11。
- 提交 1：`test: define maximal molecule continuity segments`
- 提交 2：`feat: materialize production and first-consumer links`
- 提交 3：`test: preserve species fate continuity behavior`

## Phase 7 — Sequence-constrained Continuous Support validator

### 目标

对选定 Candidate sequence 做 indexed join，生成 threshold-free provenance report，并在显式 retention policy 下附加 policy evaluation。

### 修改模块/文件

- 新建 `reacnet_scope/continuous_support.py`。
- 修改 `reacnet_scope/candidate_paths.py` result references 和 service facade。
- 新增 `tests/test_continuous_support.py`、`tests/test_continuous_support_properties.py`、`tests/test_continuous_support_jobs.py`。

### Schema / 数据迁移

- validator cache/job record key：candidate evidence key、anchor policy、validation constraints、可选 retention policy、validator semantic version。
- support occurrence 记录：stable ID、Candidate identities、revision、Replicate、ordered Transition IDs、normalized occurrence IDs、anchor origin、selected atoms、ordered carried Species/segment IDs、per-step carrier atom/bond refs、provenance profile、constraints、versions、source signatures 和 incomplete states。
- validation selection scope 记录 Top-M/query selection，未选择项保持 `not_evaluated`。

### 向后兼容

- 旧 Event Path verification 保持独立能力，不改名为 Continuous MD Support。
- 旧 lineage/occurrence counts 不自动迁移成 validation result。

### 测试

- anchor：默认 all heavy atoms、纯氢 fallback、all atoms、explicit IDs、非法 IDs。
- sequence：product segment → first consumer → compatible next occurrence；不得跳过更早 consumer/gap/barrier。
- ambiguity：竞争 consumer、同 Transition 多 occurrence、unresolved evidence、预算/取消均返回正确 inconclusive provenance。
- retention：无 policy 时没有 binary evaluation；四类 policy 的 supported/not-observed/inconclusive。
- guard：`require_continuous_support=true` 无 policy 必须拒绝。
- invariant：声称 continuous carrier/anchor 但 max set 为空时抛 semantic inconsistency，不发布正常记录。
- cache：key 的任一组成变化导致 miss；selection scope 不被误解为 score。
- I/O：验证单 Candidate 不读取无关 Species/occurrences，不构建全局 graph。

### 确定性要求

- compatible occurrence/segment 遍历有完整 semantic ordering；ambiguity 不通过 row order 消失。
- `max_continuous_anchor_set`、lost-by-step 和 support occurrence ID 可重复生成。

### 完成/验收条件

- threshold-free report 和显式 policy evaluation 完全分离。
- validator 的 rows/pages read 随 Candidate sequence 和 budget 有界，而非随全库近似线性。

### 回滚

- 禁用 validator capability/job routing；Candidate discovery 和 network ranks 继续可用。
- cache records 按 validator version 隔离，可安全忽略而不修改 Candidate。

### 依赖与建议小提交

- 前置：Phase 1、2、5、6。
- 后续：Phase 8–11。
- 提交 1：`test: specify threshold-free continuous support`
- 提交 2：`feat: validate candidate sequences through continuity segments`
- 提交 3：`feat: add explicit anchor retention policy evaluation`
- 提交 4：`feat: cache resumable continuous support validation`

## Phase 8 — Step Evidence / Support Evidence APIs

### 目标

提供两条不会混淆的稳定、分页证据 API，并让主 Candidate payload 保持轻量。

### 修改模块/文件

- 新建 `reacnet_scope/candidate_evidence.py` 或在 candidate index reader 中形成深模块。
- 修改 `reacnet_scope/services.py`、`reacnet_scope/analysis_services.py`、`reacnet_scope/service_types.py`。
- 新增 `tests/test_candidate_evidence_api.py`、`tests/test_candidate_evidence_pagination.py`。

### Schema / 数据迁移

- Step Evidence endpoint key：revision + candidate signature + step index，查询 directed reaction key 的 independent normalized occurrences。
- Support Evidence endpoint key：candidate evidence key + validation record/selection scope，分页 support occurrences。
- 主 Candidate 返回 stable summary/ref，不嵌入 occurrence、segments、coordinates 或 local trajectory。
- page token 绑定 revision、endpoint kind、filters 和 semantic ordering。

### 向后兼容

- 旧 `event_path_occurrences_for_signature` 继续服务 Path Verification/legacy UI，不作为 Step Evidence adapter。
- 旧页面若只能显示混合证据，先隐藏新入口而不是错误标注。

### 测试

- Step Evidence：不同 steps 可来自不同 Replicates/Transitions，分页不暗示 chain；完整 stoichiometry 和 package ref 保留。
- Support Evidence：ordered IDs、segments、anchors、gaps/ambiguity/truncation 完整。
- pagination：page size 有界、无重复遗漏、stale token 拒绝。
- authorization/path safety：trajectory/package refs 仍走现有受限路径验证。
- query plan：每页只读请求范围，无 full scan。

### 确定性要求

- API ordering 和 tokens 使用稳定 keys；volatile job metadata 在 envelope 外。

### 完成/验收条件

- 用户能够从 Candidate 分别回答“每步为何 eligible”和“是否有连续 molecular provenance”。
- 两条 API 的 schema 和 UI labels 不共享含混的 `support` boolean。

### 回滚

- 关闭新 drill-down routes；Candidate 主查询仍可返回结构和 network metrics。

### 依赖与建议小提交

- 前置：Phase 2、4、5、7。
- 后续：Phase 9、11。
- 提交 1：`test: separate step and continuous evidence contracts`
- 提交 2：`feat: add paged candidate step evidence`
- 提交 3：`feat: add paged continuous support evidence`

## Phase 9 — UI 下钻与状态表达

### 目标

让 Dash 清楚表达 network Candidate、Step Evidence、validation execution、事实 retention 与显式 policy evaluation，避免旧“实际采样路径”暗示。

### 修改模块/文件

- 修改 `scripts/webapp_dash/app.py`、`callbacks.py`、`navigation.py`、`assets/app.css`。
- 修改 `tests/test_dash_smoke.py`，按需要新增小型 UI serializer/helper tests。

### Schema / 数据迁移

- 无持久 schema；浏览器 store 版本升级并绑定 dataset revision + Candidate signature。
- dataset/revision 切换清空 Candidate selection、page tokens 和 validation job selection。

### 向后兼容

- compatibility payload 使用显眼 legacy badge，不与新结果混排。
- 旧 bookmark/store version 失效时给出重新查询提示，不尝试字段猜测。

### 测试

- 文案：Candidate 是 network-level；Step Evidence 不称连续链；`not_evaluated` 显示“未验证”。
- `intact_anchor_support=false` 显示 retained fraction/loss，不显示笼统“不支持”。
- horizon-limited、execution-truncated、inconclusive 分别显示。
- target-constrained 与 exploratory 控件/empty states 分开。
- require continuous support 缺 policy 时前后端都拒绝。
- drill-down：Candidate → step page / support summary → record → event/trajectory。
- pagination 和 dataset switch 防止 stale evidence 混入。

### 确定性要求

- UI 不在浏览器重新计算 rank、carried Species 或 support policy；只渲染核心 payload。

### 完成/验收条件

- smoke tests 覆盖所有状态词和下钻路径。
- 用户无法把不同 step occurrence 列表误读为一条 sampled chain。

### 回滚

- feature flag 隐藏新页面/validation controls；旧 Candidate 页面仍可作为明确 legacy 页面暂存。

### 依赖与建议小提交

- 前置：Phase 5、7、8。
- 后续：Phase 10–11。
- 提交 1：`test: specify candidate and validation UI states`
- 提交 2：`feat: render network candidate discovery modes`
- 提交 3：`feat: add separate candidate evidence drill-downs`

## Phase 10 — Compatibility migration 与旧原型退役

### 目标

完成 Python/CLI/Dash/文档从旧 sampled Event Path Candidate 到新 production contract 的显式迁移，然后退役默认调用链中的原型。

### 修改模块/文件

- 修改 `reacnet_scope/__init__.py`、`services.py`、`analysis_services.py`、`candidate_paths.py`、`event_paths.py`。
- 修改 `scripts/rng_query_cli.py`、Dash 文件、`README.md`、`docs/candidate-path-discovery.md`、旧日期化计划的状态说明。
- 修改 `tests/test_candidate_paths.py`、`test_event_paths.py`、`test_pathway_cli.py`、`test_dash_smoke.py`。

### Schema / 数据迁移

- 提供旧 index readiness → production `REBUILD_REQUIRED` 的明确迁移提示。
- 若持久化过旧 Candidate result/cache，按 schema namespace 隔离；不尝试把 reaction tuple-only ID 升级为新 signature。

### 向后兼容

- 先发布双入口：production 为新名字/schema，legacy 仅显式 flag/endpoint。
- 下一小版本将 legacy 标记 deprecated，记录调用 telemetry（本地、无上传）。
- 达到退役门槛后，从默认 CLI/Dash/Python export 移除 legacy discovery；`verify_event_path` 和 Path Verification 保留。
- `event_paths.py` 可继续承载 Path Verification，但 Candidate discovery wrapper 删除或移入 test/compat module。

### 测试

- contract：旧/new schema 不混用；错误恢复命令正确。
- end-to-end：Python、CLI、Dash 对同一 revision/query 返回相同 Candidate identities/order/status。
- regression：Path Verification、Species Fate、Molecule Lineage 不受影响。
- docs/search：不再出现把正式 Candidate 称为 Sampled Candidate 或 strict Event Path 的当前文案；历史 ADR/计划除外且有 superseded 标记。
- call graph：production Candidate 路径不导入/调用 `discover_event_paths`。

### 确定性要求

- adapter 只做字段映射，不重算 identity/rank。

### 完成/验收条件

- production 默认路径完全基于 published substrate；原型不在正式 capability/readiness 中。
- 旧用户得到明确迁移说明，且不存在静默语义变化。

### 回滚

- 在退役观察期内保留 release-scoped legacy feature flag；回滚只切路由，不回写新 revision。

### 依赖与建议小提交

- 前置：Phase 4–9。
- 后续：Phase 11 发布。
- 提交 1：`feat: add explicit legacy candidate compatibility mode`
- 提交 2：`docs: migrate candidate path terminology and contracts`
- 提交 3：`refactor: remove event-path discovery from production routing`

## Phase 11 — 两规模 benchmark 与发布门禁

### 目标

以可复现 fixture/generator 和结构指标证明 production architecture 满足百万级门槛，而不只测小数据正确性。

### 修改模块/文件

- 新建 `benchmarks/candidate_paths/` 的 dataset generator、runner、query set 和 README。
- 新建 `tests/test_candidate_scale_contract.py`（短结构门禁）及 CI/manual large-profile 配置。
- 修改发布文档或 CI workflow；若仓库尚无 benchmark harness，保持工具独立且不污染普通单元测试。

### Schema / 数据迁移

- benchmark 固定至少两个 scale points，其中发布点包含 `10^6 normalized Reaction Occurrences` 和 `10^7 molecule-frame records`。
- 记录 schema/semantic versions、fixture seed、hardware/runtime envelope、index sizes 和 active revision。

### 向后兼容

- benchmark 不改变产品数据；所有输出写入临时 workspace。

### 测试与指标

- preparation：wall time、peak RSS、throughput、checkpoint/resume、failed publish 与 active revision integrity。
- discovery：wall time、peak RSS、frontier high-water mark、candidates examined、rows/pages read、query plan、raw-source open count。
- Step Evidence：每页 rows/pages read 与 page size 同阶。
- Continuous Support：每 Candidate budget、rows/pages read、取消/resume 和 inconclusive 状态。
- locality scaling：向第二规模点增加与 anchor 局部查询无关的大量 events，比较读量/RSS；不得近似随全局数据量线性增长。
- determinism：相同 revision/versions/params 的 canonical payload byte-stable；volatile metadata 单独比较 schema，不参与 bytes。
- status：horizon termination、execution truncation 和 validation truncation 的独立断言。

### 确定性要求

- generator seed、query corpus 和 semantic versions 固定；结果包含环境信息但 canonical result 单独保存。

### 完成/验收条件

- 两个规模点报告齐全，发布点达到 `10^6`/`10^7`。
- Candidate Discovery 原始事件源打开次数为 0，无非预期 full scan；RSS 与读量符合局部 bounded contract。
- failed/cancelled preparation 不污染 active revision；resume 结果与 uninterrupted build canonical-equivalent。
- 所有 correctness、integration、property、UI 和 benchmark gates 通过后，production capability 才可标记 ready。

### 回滚

- 任一门禁失败则保持 capability experimental/disabled，active revision 和 legacy compatibility path 不变；不得以放宽语义断言替代修复。

### 依赖与建议小提交

- 前置：Phase 3–10。
- 提交 1：`test: add candidate locality and query-plan gates`
- 提交 2：`bench: add two-scale candidate production workload`
- 提交 3：`ci: gate candidate production readiness on scale report`

## 当前一致性审计与迁移安排

| 位置 | 当前冲突 | 迁移阶段 |
| --- | --- | --- |
| `CONTEXT.md`（Phase 0 前的 Candidate 词条） | 把 Candidate 定义为实际 sampled Event Path | Phase 0 已替换为 network Candidate；Phase 10 清理消费方词汇 |
| `docs/adr/0012-discover-only-sampled-candidate-paths.md` | 接受了 strict Event Path discovery | Phase 0 标记由 ADR-0013 supersede |
| `docs/software-design-baseline.md`（Phase 0 前的 3、5、11.5 节） | 要求 strict atom continuity 并固定旧 scoring | Phase 0 已由新基准替代 |
| `docs/candidate-path-discovery.md` | 全文把同一 Molecule Instance/atom lineage 当作 Candidate 必要条件 | Phase 10 重写为 discovery + separate validation 用户文档 |
| `README.md` Candidate 段落 | 使用“实际采样”“严格原子连续”并声称每条结果来自连续事件链 | Phase 10 随新默认入口一起迁移，避免提前宣传未实现功能 |
| `docs/superpowers/plans/2026-07-24-candidate-pathways.md` | 固定 ranking/defaults、允许 `network_only` 聚合路径，并按 focal product 形成路径 | 保留历史；本计划取代其后续实施权威 |
| `reacnet_scope/event_paths.py::discover_event_paths` | 只枚举 strict occurrence chains；`_load_event_nodes` 在线全表读取 `events`，随后构造全局内存 occurrence graph | Phase 4 新建 indexed discovery；Phase 10 从 production routing 退役，Path Verification 保留 |
| `reacnet_scope/event_paths.py::_signature_identifier` | 只哈希 ordered reaction keys，缺 anchor 和 carried Species | Phase 1 引入正式 signature；legacy ID 不升级 |
| `reacnet_scope/candidate_paths.py::_choose_focal_output` | 由结构相似度 ranker 猜 carried product | Phase 1/4 改为 discovery 显式产出 carried branch |
| `reacnet_scope/candidate_paths.py::rank_candidate_paths` | 输入是 Event Path report；continuity/occurrence 参与固定 score，validation 与 network rank 混合 | Phase 5 分离 versioned network rank；Phase 7 只附加 validation |
| `reacnet_scope/candidate_paths.py::RankedCandidatePath` | `signature_id`、rank、score、occurrence support 混成一个对象 | Phase 1/5 拆为 identity、evidence key、query item 和 validation ref |
| `reacnet_scope/analysis_services.py::discover_candidate_paths_for_dash` | 要求 Molecular Evidence，调用 strict Event Path discovery | Phase 4 新 production service；Phase 10 显式 legacy/退役 |
| `scripts/rng_query_cli.py::cmd_candidate_paths` | CLI 文案、输入和输出均为 sampled atom-continuous paths | Phase 9/10 迁移命令 contract 和状态输出 |
| `scripts/webapp_dash/app.py` Candidate 页面 | 页面称“实际采样证据”，只显示 strict continuous candidates | Phase 9 改为 network Candidate + 两条证据通道 |
| `scripts/webapp_dash/callbacks.py` Candidate callbacks | 旧 store/下载以 `signature_id` 和 sampled result 为中心 | Phase 8/9/10 版本化 store、分页和下载 |
| `tests/test_event_paths.py`、`tests/test_candidate_paths.py`、`tests/test_pathway_cli.py`、`tests/test_dash_smoke.py` | 测试固化 reaction tuple signature、strict chain discovery 和旧文案 | 各实现阶段先新增新契约测试；Phase 10 移除仅针对旧默认的断言 |
| 现有 Candidate response 的 `truncated` | Top-K 截断与 traversal truncation 合并，缺少 query_complete/graph_exhaustive/horizon_limited | Phase 5 拆分状态和 counters |
| 当前 schema/API/UI | 尚无 `not_evaluated` 与 `intact_anchor_support` 展示 | Phase 7 定义事实结果，Phase 9 明确避免映射为 unsupported |

## 总体验收顺序

1. Phase 1 identity golden vectors 稳定。
2. Phase 2–3 substrate 能可靠构建、恢复和原子发布。
3. Phase 4–5 production discovery/status/ranking/pagination 不读取 occurrence 全集。
4. Phase 6–7 continuity segments 与 validator 通过 provenance invariants。
5. Phase 8–9 两条证据 API 和 UI 状态不混淆。
6. Phase 10 默认入口迁移且原型退出 production call graph。
7. Phase 11 两规模门禁通过后才宣布 production-ready。

任何阶段失败都回滚其 routing 或 active revision，不修改已经发布的 Candidate structural identity，也不通过降低证据语义来保持旧输出。
