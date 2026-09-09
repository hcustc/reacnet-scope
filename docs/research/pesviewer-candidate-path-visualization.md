# PESViewer 对候选路径可视化的适用性调研

调研日期：2026-08-28

PESViewer 对 ReacNet Scope **有帮助，但更适合作为视觉设计参照和有条件的离线导出目标，不适合直接成为当前候选路径页面的渲染内核，也不能替代我们的路径发现算法**。它最值得借鉴的是：把分子二维结构放进节点、在节点和边上显示能量、悬停查看名称/能量、选择节点后突出局部连接，以及在证据完整时为单条路径增加“能量—反应坐标”视图。当前最合适的方向是在已有 Dash + Cytoscape + RDKit/Matplotlib 技术栈内原生实现这些交互，而不是把 PESViewer 嵌入 Web 进程。

## 调研范围与版本

本文只使用两个项目的官方仓库 README、源码、发布页和本仓库当前代码/文档。PESViewer 源码判断固定在调研时 `master` 的提交 [`ee0e18f`](https://github.com/zadorlab/PESViewer/tree/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73)；GitHub Releases 将 [`1.2.0`](https://github.com/zadorlab/PESViewer/releases/tag/1.2.0) 标记为 Latest，并说明该版改善了图可视化、允许能量平移和第二套节点能量等功能。[发布页](https://github.com/zadorlab/PESViewer/releases)

## PESViewer 实际接受什么数据

PESViewer 处理的是一个由**势阱、双分子产物、过渡态和无垒反应**组成的势能面，而不是一般的事件路径 JSON。它读取一个分节文本文件：

- `wells`：每个单分子物种的名称、**绝对/同参考能量**和可选 SMILES；
- `bimolec`：每个双分子产物状态的名称、能量和可选 SMILES；
- `ts`：过渡态名称、**过渡态能量**、反应物状态名和产物状态名，可选颜色；
- `barrierless`：无垒通道名称、反应物状态名和产物状态名，不输入 TS 能量；
- `options`：单位、能量参考平移、显示方式、路径报告等选项。

这些格式由官方 README 明确定义，解析器也先建立 well/bimolecular 状态，再把 TS 或 barrierless 边解析为两个已存在状态之间的连接。[README 输入格式](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L49-L72)；[`read_input`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L366-L377)；[节点与边解析](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L533-L588)

二维结构可以直接由 SMILES 生成；如果没有 SMILES，则默认需要输入文件工作目录下的 `xyz/`：well 使用 `name.xyz`，双分子状态使用多个 `name1.xyz`、`name2.xyz`……。程序用 Open Babel 从 XYZ 推断 SMILES，再用 RDKit 生成透明背景 PNG，并把结果写入 `<id>_2d/`。[README 的 XYZ 约定](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L81-L86)；[XYZ 文件查找](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L224-L263)；[二维结构生成](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L976-L1005)

这意味着仅有“物种序列”或“反应出现次数”还不够生成有科学含义的 PES 图；关键缺口是每个稳定状态的统一参考能量，以及每条边的 TS 能量或可信的无垒分类。

## 它能画什么、怎样交互

PESViewer 同时提供两类输出：

1. **传统势能—反应坐标图**：Matplotlib 图中展示 well、产物和 TS 能量，可拖动 stationary point 的横向位置、拖动分子图片、缩放；布局位置写入辅助文本文件以便下次复用；选择一个 stationary point 时，与它直接相连的节点和通道保持突出，其余内容变淡。[README 输出说明](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L124-L150)；[局部高亮实现](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1284-L1340)
2. **交互网络图**：Pyvis 生成独立 HTML；well 和 bimolecular state 是带二维分子图片的节点，节点标签为相对能量，悬停显示名称；TS/无垒通道是边，悬停显示能量，边宽和可选颜色由能量控制，并暴露 physics 布局控件。[README 图模型](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L146-L150)；[`create_interactive_graph`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1411-L1492)

它的 `path_report` 也不是“把外部找到的路径画出来”的通用接口。该功能接收起点和终点，在 PESViewer 自己建立的**无向** NetworkX 图中枚举 simple paths，以路径上最高边能量最小为目标，并用较短路径打破并列；随后输出只包含该最小能垒路径的新 `.inp` 文件。[选项定义](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L108-L110)；[图与 MEP 搜索实现](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1542-L1614)

## 与 ReacNet Scope 候选路径的对应关系

ReacNet Scope 的对象是 Sampled Candidate Path：事件严格按时间向后，相邻事件连接同一个精确 Molecule Instance，至少一个原子 ID 贯穿整条链，且第一步包含指定起始 Species。聚合反应网络只补充频次、方向和计量关系，不产生候选。[本项目候选路径语义](../candidate-path-discovery.md#L1-L10)

每个候选结果包含一条为展示选择的 `species` 焦点链，以及每步的完整 `reactants` / `products`、正逆与净事件数、结构相似度；能量证据是可选的归一化 `score`，另可携带 `delta_energy`、`barrier`、单位和来源。整个结果还保留实际发生次数、原子谱系数、跨重复复现率和时间指标。[`CandidatePathStep` / `RankedCandidatePath`](../../reacnet_scope/candidate_paths.py#L32-L89)；[步骤构造](../../reacnet_scope/candidate_paths.py#L280-L329)；[评分与语义声明](../../reacnet_scope/candidate_paths.py#L333-L425)

当前 Web 页面已经用 Dash Cytoscape 画所选候选：节点是焦点 Species 的 SMILES 文本，边显示 forward events 与结构相似度，使用有向 breadth-first 布局；表格同时展示频次、结构、时间、连续性和能量覆盖率。[当前页面](../../scripts/webapp_dash/app.py#L2923-L3142)；[当前图数据](../../scripts/webapp_dash/callbacks.py#L7429-L7527)

| 维度 | PESViewer | ReacNet Scope 当前候选路径 | 结论 |
| --- | --- | --- | --- |
| 核心语义 | 有 stationary points 和 TS/无垒通道的 PES | 轨迹中实际出现且原子连续的事件链 | 不能互相替代 |
| 节点 | well 或 bimolecular product state，要求能量 | 焦点 Species；每步另保留完整反应两侧 | 需要显式状态映射 |
| 边 | TS 或明确 barrierless；以能量表达 | Reaction Type，含频次、方向、连续性，可无能量 | 不能把“缺能量”当作“无垒” |
| 方向 | MEP 搜索使用无向图；交互边关闭箭头 | 严格时间方向，另有正逆与净通量 | 直接导入会丢失核心方向语义 |
| 排名 | 最小化路径上的最高边能量 | 频次、结构、时间、连续性、可选能量的版本化综合分 | `path_report` 不是我们的候选发现器 |
| 视觉 | 分子图、相对能量、能量边、局部高亮、PES 曲线 | SMILES 文本节点、事件数与相似度边、证据指标表 | 视觉模式值得借鉴 |

## 硬不匹配与集成风险

### 1. 当前能量证据不足以构造 PES

我们的 `delta_energy` 和 `barrier` 是按 Reaction Type 可选附加字段，`energy_score` 还是用户预先归一化的排名分；没有规定每个 stationary state 的统一绝对能量，也没有规定 `barrier` 相对哪一端的状态能量。文档还明确说明：缺少能量时会重分配权重，候选本身不证明过渡态、能垒或完整机理。[能量输入边界](../candidate-path-discovery.md#L12-L26)；[解释边界](../candidate-path-discovery.md#L43-L47)

因此在当前 schema 上无法可靠生成 PESViewer 的 `wells` 和 `ts` 段。尤其不能把所有缺少 `barrier` 的反应写成 `barrierless`，那会把“未知”误写成科学结论。

### 2. 焦点 Species 链不是完整的反应状态链

候选路径为每一步从完整 products 中选择一个 `focal_output`，优先选择能连接下一反应的物种，否则按结构相似度选择；与此同时完整计量两侧仍单独保存在 step 中。[焦点输出选择](../../reacnet_scope/candidate_paths.py#L158-L173)；[构造逻辑](../../reacnet_scope/candidate_paths.py#L280-L329) 直接把 `species` 链映射成 PESViewer well 节点会丢掉共反应物、共生产物和反应复合物；反过来，把每个完整反应侧都映射为复合状态，又需要解决相邻步骤如何共享同一个 PES state、能量如何定义等问题。

### 3. 方向、并行通道和路径含义会丢失

PESViewer 的 MEP 图是 `nx.Graph()`，边能量写在无向的节点对上；其 Pyvis 图虽然以 `directed=True` 创建，但所有反应边显式设置 `arrows=''`。[`gen_graph`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1542-L1558)；[Pyvis 边](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1460-L1488) 这与候选路径的严格时间方向、forward/reverse/net TP 以及同一节点对之间可能存在不同完整 Reaction Type 的数据模型不相容。

### 4. 不能安全地直接嵌入当前 Dash Web 进程

PESViewer 在导入时强制 `TkAgg`，使用模块级全局 `wells`、`bimolecs`、`tss` 等可变集合；`main()` 每次读取输入都向这些集合追加，但没有按请求清空。程序还大量在当前工作目录创建 PNG、TXT、HTML、`reaction_smi.out` 和派生 `.inp` 文件。[后端与全局状态](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L4-L50)；[输出和主流程](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1490-L1492)；[`main`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L1617-L1659) 这些特征不适合多请求、可能并发且通常无桌面显示的 Dash 服务。

解析 TS 能量时源码还直接执行 `eval(t[1])`，所以不能把不受信任的 PESViewer 文本交给服务器进程解析。[TS 解析](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pesviewer/pesviewer.py#L567-L578) 即使未来提供导出，也应由我们生成受控文本；若确需调用 PESViewer，应使用隔离的临时工作目录和独立子进程，而不是在 Web worker 内 import 后反复调用。

## 技术、打包与许可证

PESViewer 是 Python 控制台程序，核心依赖 NumPy、Matplotlib、NetworkX、Pillow、Pyvis 和 RDKit；Open Babel 是必需依赖，但官方明确因其 PyPI 构建需要 C++ 库和 SWIG 而没有写进 `pyproject.toml`，推荐另用 conda 安装。官方 README 给出 conda-forge 安装和 GitHub editable install 两条路径。[安装说明](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/README.md#L14-L43)；[`pyproject.toml`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pyproject.toml#L39-L57)

主分支打包元数据目前并不完全一致：`pyproject.toml` 写版本 `1.1.0` / Python `>=3.6`，conda recipe 写 `1.2.0` / Python `>=3.8`，README 又写 Python `>=3.7`；`pyproject` classifier 写 BSD，但仓库 `LICENSE` 和 conda recipe 都是 MIT。[`pyproject.toml`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/pyproject.toml#L8-L46)；[`meta.yaml`](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/meta.yaml#L1-L46)；[MIT LICENSE](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/LICENSE#L1-L20) 若引入依赖，应固定并实测明确提交/版本，不要依赖这些范围自动推断环境。

许可证本身不是障碍：PESViewer 的权威 LICENSE 是 MIT，允许使用、修改和再分发，但复制或修改其代码时要保留版权和许可声明。[MIT LICENSE](https://github.com/zadorlab/PESViewer/blob/ee0e18fcdb6bc8de6fddaa3e4726a0143c785a73/LICENSE#L1-L20)

## 建议的采用边界

### 现在值得采用：原生复刻视觉模式

- 在现有 Cytoscape 节点中加入 RDKit 生成的 2D 分子图，名称/SMILES 放到悬停或详情面板；本项目已经依赖 RDKit、Dash Cytoscape 和 Matplotlib，不必为此引入 Pyvis/Open Babel。[本项目依赖](../../pyproject.toml#L11-L38)
- 所选候选仍保持清晰的时间方向箭头；边默认编码实际发生次数、净方向、结构相似度和证据完整性，而不是伪装成能垒。
- 点击节点时突出相邻步骤，并在侧栏展示完整 reactants/products、Reaction Type、发生次数、谱系支持、跨重复复现率和能量来源；这是 PESViewer “局部高亮”思路与我们证据模型的自然结合。
- 只有能量定义、单位、参考态和覆盖率满足要求时，才增加所选路径的势能—反应坐标副图；缺失位置明确画为未知，不插值成 TS，也不标成 barrierless。

### 以后可选：受限的 PESViewer 导出

可以设计一个独立 adapter，把**用户选择的一条候选路径**导出为 PESViewer `.inp` + 可选 `xyz/`，但必须先有比当前 schema 更严格的能源数据契约：

1. 每个完整反应状态的稳定 ID、组成和统一参考能量；
2. 每个步骤明确为“有 TS 且给出 TS 绝对能量”“经验证的 barrierless”或“未知”；
3. 全路径单位和计算口径一致；
4. 明确如何把多反应物/多产物状态映射为 PESViewer 的 well/bimolecular node；
5. 导出结果标注它来自 sampled candidate，并保留原事件证据链接，避免把 PES 图误读成机理证明。

满足这些条件后，PESViewer 适合做专家用户的离线排版/出版图工具。否则导出的图外观会很像势能面，科学内容却没有被当前数据支持。

## 最终判断

**短期建议：借鉴，不集成。** 先在当前候选路径页原生增加“分子图片节点 + 局部高亮 + 证据详情”，这是价值高、语义安全且与现有技术栈吻合的部分。

**中期建议：能量契约先于能量图。** 当我们能提供统一参考的 state/TS 能量和明确的 barrierless 分类后，再原生增加 energy profile，并评估 `.inp` 导出。

**不建议：** 用 PESViewer `path_report` 重新发现/排名路径、把缺失能量的边当无垒边、把当前 focal Species 链直接冒充完整 PES，或把 PESViewer 作为共享 Dash 进程内的库调用。
