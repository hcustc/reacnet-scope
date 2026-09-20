# Dash 使用逻辑

普通 Dash 固定为五个工作区：

| 工作区 | 普通任务 |
| --- | --- |
| 数据集 | 选择 Dataset Candidate、切换 Current Dataset、检查来源与能力、管理派生索引 |
| 物种与趋势 | 精确物种检索、结构查看、时间演化、元素分布 |
| 反应与事件 | 直接生成/消耗通道、反应式检索、时间分布、具体 RNG 事件 |
| 轨迹与谱系 | 局部帧、键变化、分子分支追踪、事件包与必要的 DFT 初始几何交接 |
| 对比 | 在不改变 Current Dataset 的前提下选择多个来源并检查身份、时间和证据可比性 |

核心链路是“精确 Species → Direct Reaction Channel → Reaction Occurrence → 局部轨迹／
分子谱系 → 证据导出”。单步 Direct Reaction Channel 只展示焦点物种的直接生成或消耗
Reaction Type，不递归扩展路线，也不在普通查询中计算表观速率常数。

跨工具交接只传递精确 SMILES、完整 Reaction Type、稳定 `event_id` 及经核验的 Dataset
Identity／源修订。数据集选择、能力状态和派生索引准备集中在“数据集”；切换 Current
Dataset 会清空旧数据集的分析结果，但保留可安全复用的查询文本。对比来源保存在对比工作区，
不会反向改变 Current Dataset。

## 退役页面与兼容核心

Candidate Path Discovery、Path Verification 和 Species Fate Analysis 不再挂载独立 Dash
页面，也不保留页面专用回调、Store、轮询或样式。旧会话中的页面 ID 会安全归一：

- `candidate-paths`、`pathway` → “反应与事件”；
- `species-fate` → “轨迹与谱系”。

归一只恢复所属工作区，不自动运行分析。相应公共 CLI、Python API、JSON/CSV 导出以及共用的
事件、分子连续性、时间、体积、PBC 和几何证据核心继续兼容保留。需要候选路径或明确路径
验证时使用公共 CLI/API；重新进入普通 Dash 必须有独立立项和验收，不通过恢复旧页面完成。

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

谱系、DFT 初始几何和 QC 交接依附具体 Reaction Occurrence，不增加无上下文的一级入口。
未实现或已冻结的规划能力不渲染为可执行按钮，也不提前承诺科学结论。

导航、页面描述、入口能力、旧 ID 归一与使用指引统一放在
`scripts/webapp_dash/navigation.py`。新增或删除工具时同步该配置、`app.py` 页面挂载和相关
回调，不新增第二份 Current Dataset 状态。数据入口卡只导航；科学身份交接使用现有选择按钮
和服务契约。
