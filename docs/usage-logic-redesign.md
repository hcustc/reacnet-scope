# Dash 使用逻辑

本文说明当前功能组织；面向目标物种的交互依据见
[使用流程方案](target-species-workflow-proposal.md)。其中的路径步骤、实例切换、结构交接和
可选变化追踪入口已经实现；验收范围见[开发计划与交付记录](target-species-workflow-development-plan.md)。
讨论后的主线为“目标物种 → 候选路线/直接通道 → 反应步骤 → 多个具体反应实例 →
结构核查 → 证据或 DFT 初始几何导出”；分子变化追踪为可选辅助，未找到完整连续实例
不作为否定候选路线的依据。当前页面名称和入口仍以以下实现说明为准。
具体开发顺序见[当前开发计划](target-species-workflow-development-plan.md)：优先将路径图中
菱形或连线对应的步骤详情接到支持实例、结构核查和几何导出，再整理可选追踪及外围入口。

普通 Dash 固定为五个工作区：

| 工作区 | 普通任务 |
| --- | --- |
| 数据集 | 选择 Dataset Candidate、切换 Current Dataset、检查来源与能力、管理派生索引 |
| 物种与趋势 | 精确物种检索、结构查看、时间演化、元素分布 |
| 反应与事件 | 直接生成/消耗通道、反应式检索、时间分布、候选路径及步骤证据、具体 RNG 事件 |
| 结构与轨迹 | 所选反应实例、前后键结构、局部帧、事件包、必要的 DFT 初始几何，以及可选分子变化追踪 |
| 对比 | 在不改变 Current Dataset 的前提下选择多个来源并检查身份、时间和证据可比性 |

核心链路是“精确目标 Species → Direct Reaction Channel 或 Candidate Path → 反应步骤 →
Reaction Occurrence → 结构／局部轨迹 → 证据或初始几何导出”。单步 Direct Reaction Channel 只展示焦点物种的直接生成或消耗
Reaction Type，不递归扩展路线，也不在普通查询中计算表观速率常数。

候选路径作为“反应与事件”内的任务，支持起点探索或起点到目标搜索、合并图、路线比较、
步骤事件、实例前后切换、所选路线连续历史检查和 JSON/CSV 导出。点击图中的反应菱形或连线
会打开同一步骤；选中的实例可直接进入“结构与轨迹”，无需在事件页再次选择。步骤支持与整条连续历史分别表达；
“寻找生成路线”仍需精确起点，不能只凭目标物种反向补全未观测反应。
范围与证据规则见 [ADR-0015](adr/0015-add-indexed-candidate-task-to-reaction-workspace.md)
和 [ADR-0017](adr/0017-qualify-candidate-events-and-check-selected-history.md)。

“分子变化追踪”从所选事件中的具体分子实例开始，底层使用 Molecular Lineage Explorer
展开 segment/occurrence 图，并从已展开图中提取已观测路径。它与候选 Species 路线是不同视图；基本查询不以
轨迹坐标为前提。具体入口和限制见 [Explorer 使用说明](molecular-lineage-explorer.md)。

跨工具交接只传递精确 SMILES、完整 Reaction Type、稳定 `event_id` 及经核验的 Dataset
Identity／源修订。数据集选择、能力状态和派生索引准备集中在“数据集”；切换 Current
Dataset 会清空旧数据集的分析结果，但保留可安全复用的查询文本。对比来源保存在对比工作区，
不会反向改变 Current Dataset。

## 退役页面与兼容核心

旧版 Candidate Path Discovery 独立页面、Path Verification 和 Species Fate Analysis
不再挂载普通 Dash 页面，也不保留这些旧页面专用的回调、Store、轮询或样式。
新候选任务使用自己的组件与状态，不恢复旧独立页面。旧会话中的页面 ID 会安全归一：

- `candidate-paths`、`pathway` → “反应与事件”；
- `species-fate` → “结构与轨迹”。

归一只恢复所属工作区，不自动运行分析。相应公共 CLI、Python API、JSON/CSV 导出以及共用的
事件、分子连续性、时间、体积、PBC 和几何证据核心继续兼容保留。明确路径验证和物种命运
分析使用公共 CLI/API；候选任务已按 ADR-0015 恢复在反应工作区内，不受旧页面退役条款限制。

## 界面与状态规则

- 顶部导航的 Current Dataset 始终可见；数据集按钮按状态显示“选择数据集”或“更换数据集”。
- 数据集浏览器区分 Current Dataset 与 Candidate；浏览、筛选和候选检查不会改变当前数据，
  只有“使用此数据集”会在原子验证后提交切换。
- 缺证据、需准备、无匹配结果和未执行必须分别显示，不能都降级为空表。
- 查询产生新结果时清空旧表格选择；数据集切换同时清空通道、事件、轨迹、谱系和对比中的
  数据集绑定状态。后台迟到结果需核对 Dataset Identity 后才能提交。
- 导航使用本地 SVG 图标和可见文字；当前入口提供 `aria-current`，状态区使用适当的
  `role="status"`／`role="alert"` 与 `aria-live`。

## 任务入口与扩展

数据集概览先显示当前数据集，再按五个工作区展示研究问题、起始输入、能力状态和入口。
入口可打开不等于查询可执行；操作仍需检查自己的证据能力。各分析页的使用指引说明输入、
结果去向和主要证据边界。

分子变化追踪、DFT 初始几何和 QC 交接依附具体 Reaction Occurrence，不增加无上下文的一级入口。
未实现或已冻结的规划能力不渲染为可执行按钮，也不提前承诺科学结论。

导航、页面描述、入口能力、旧 ID 归一与使用指引统一放在
`scripts/webapp_dash/navigation.py`。新增或删除工具时同步该配置、`app.py` 页面挂载和相关
回调，不新增第二份 Current Dataset 状态。数据入口卡只导航；科学身份交接使用现有选择按钮
和服务契约。
