# Molecular Lineage Explorer

Explorer 位于事件/轨迹工作区，从所选 Reaction Occurrence 一侧的具体 molecule instance 出发，
读取逐帧状态建立的 Continuity Segment，再逐步展开真实 occurrence 的全部输入和输出。
Species 检索用于定位事件，候选 Species 路径不参与 Explorer 的路径恢复。

## 准备与入口

1. 在数据集准备中重建事件索引。新增 `lineage_segments_version=1` 是独立能力要求，旧索引
   仍可用于其他已有工具；打开 Explorer 不会自动重建。
2. 在事件工作区选择具体 occurrence。Explorer 的“打开所选 instance”列表包括其反应物和产物。
3. 选择具体分子与路径锚点并打开，即可看到根 segment 的帧区间、Species、原子与键状态。默认锚点为起点全部原子；可选碳、全部非氢原子或指定 Atom IDs。
   按元素选择要求可靠映射：优先使用显式提供的 Atom ID → 元素 JSON，否则有界读取已索引原始轨迹的起点精确帧（element 列或用户确认的 type 映射）。缺映射时可以指定 Atom IDs；不会从 SMILES 原子顺序猜测。更改锚点后重新打开 instance。

等价 preparation 命令为 `reacnet-scope prepare rebuild event <source>`，其中 `<source>`
是数据集所选 RNG 来源；CSV 来源需使用已有命令的分子证据参数。数据集准备页可复制完整命令。

## 逐步查看

- 点击 Segment 查看身份、分析帧/timestep 区间、Atom IDs、键状态、连通分量，以及相对起始
  所选锚点的 retained/lost/gained atoms。这里的 gained 是“锚点集合以外”，不表示凭空产生原子；这些是单段集合比较，不代表沿路径连续保留。
- “上一事件 / 下一事件”和“沿该分支继续”展开所选 segment 的事件边界。点击 occurrence
  查看全部来源/去向 segments、每个输入到输出的原子转移与形成/断裂键。
- “展开全部分支”对当前图各个尚未展开的向前边界各展开一次；每请求最多处理 20 个 occurrence。
  可以重复点击，不会预先选择最大继承产物。每图上限 500 segments、250 occurrences。
- “回到起始 instance”恢复根节点详情，保留已展开图。
- 遇到无法继续的边界停止该分支；不会跳过中间事件，也不会开展事件可信度诊断。

Lineage View 展示的是当前已展开范围，不宣称一开始就覆盖整条轨迹的所有后代。
相同 Species 在不同时间段重现时有不同 segment 身份，A→B→A 是允许的观测历史。

## Observed Path View

从已展开图选择目标 segment，点击提取，再选择返回的 Event Path。提取仅使用已记录的
segment/occurrence 连接，时间严格向前，且至少有非空原子集合沿整条投影持续传递。
每条投影报告连续保留/丢失锚点，不设置骨架保留比例阈值。图中粗蓝边突出所选投影，同时保留所涉事件的全部共同参与者和完整计量，不排除双分子或 merge 事件。

逐步表显示离开、起点原子重新加入、外来原子加入，以及可确定的 Cl 来源。外来是相对起始实例而言，不保证是该外来原子第一次加入。相同 Atom ID 的 Cl 返回和另一个 Cl 加入分开记录；元素映射缺失或部分缺失会明确显示。返回原子离开期间的完整支路，只有在当前已展开图中存在严格连续的原子传递时才标为 observed；否则标为 not_resolved_in_expanded_graph。

状态返回可跨越多个事件和任意已展开帧间隔，区分精确 Species/键状态返回与仅拓扑返回。间隔按离开与返回 transition 之差记录，单位是分析帧间隔，不是物理时间。返回只是观测事实，不自动计为净生成、噪声或主要机理。

split 后两个分支又合并时，完整 lineage 保留两条来源；单条 Event Path 只代表一个投影，
不会把不同分支各自保留的原子拼成一条“全部保留”的线性路径。
未展开分支不在提取范围内，返回零条路径不表示整份数据不存在该历史。

## 回查与导出

“查看原始 reaction occurrence”显示所选事件的原始索引记录。选择 Segment 范围内的分析帧，
“查看原始 frame”读取已经关联并准备索引的原始轨迹，展示该实例的坐标与完整帧文本。
必须精确命中 timestep，不能用最近帧替代。缺少原始轨迹不影响 segment/occurrence 查询。

JSON 导出携带完整已展开图、锚点选择规则、原子来源、完整计量、返回历史、路径及数据源修订，并重新从索引读取事件事实。当前报告为 `molecular-lineage-explorer/v2`；旧 v1 会话需重新打开 instance，已有版本 1 的 segment 索引无需重建。数据集切换会清空本次交互，
迟到响应不会覆盖新请求；来源修订变化时需重新打开起点。

Python 入口为 `start_lineage_explorer`、`expand_lineage_explorer`、`observed_lineage_paths`、
`lineage_frame_reference`、`lineage_frame_data`、`lineage_occurrence_record` 与 `export_lineage_explorer`。
`start_lineage_explorer` 支持 `anchor_mode`（all_atoms / elements / heavy_atoms / atom_ids）、`anchor_elements`、`anchor_atom_ids` 与可选 `atom_elements`。
科学边界见 [ADR-0018](adr/0018-explore-segment-occurrence-lineage.md)。
