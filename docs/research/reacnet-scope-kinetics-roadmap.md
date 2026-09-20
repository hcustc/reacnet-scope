# 反应软件生态与 ReacNet Scope 下游量化动力学路线调研

调研日期：2026-08-30

状态：调研与路线建议，不是已接受设计；本文不改变现有领域边界或 ADR。

仓库审查基线：当前工作树（含未提交改动）。`uv run pytest -q` 验证结果为
`498 passed`；当前正式 CLI 只有 DFT initial-geometry 导出，没有 TS、振动频率、
IRC、RRKM 或 master-equation 命令。现有 `kinetics.py` 计算的是有限 MD 窗口中的
一/二阶表观速率，不是 TST/RRKM 速率。

## 结论先行

1. **不要把产品目标定义成“再做一个自动 TS 程序”。** 自动驻点搜索、TS 优化、频率、IRC、量化作业调度、TST 和 master equation 已有相当成熟的开源组件：KinBot、AutoMeKin、SCINE Chemoton、ARC、autodE、pysisyphus、Arkane、MESS 等分别覆盖了其中的大段流程。尤其是 ARC 已把构象、优化、频率、转子、IRC、作业重试和 Arkane 后处理组织成可恢复工作流。[ARC 功能总览](https://reactionmechanismgenerator.github.io/ARC/index.html)；[ARC 作业生命周期](https://reactionmechanismgenerator.github.io/ARC/how_it_works.html)
2. **“MD → QM → kinetic model”本身也不能宣称无人做过。** ChemTraYzer 的论文已经从 reactive MD 识别事件并估计速率，后续混合 RMD/QM 工作又输出 NASA polynomial、modified Arrhenius 和 ChemKin 模型；ChemDyME 则把加速 MD、统计速率理论和 master equation 结合起来，以“kinetic convergence”控制网络扩展。[ChemTraYzer 2015](https://pubs.acs.org/doi/10.1021/acs.jctc.5b00201)；[混合 RMD/QM 2018](https://pubs.acs.org/doi/10.1021/acs.jcim.8b00078)；[ChemDyME 2021](https://pubs.acs.org/doi/abs/10.1021/acs.jctc.1c00335)
3. **ReacNet Scope 真正稀缺的机会是“证据闭环”，而不是某个单点算法。** 当前软件已经具有 Reaction Occurrence、精确方向、Molecule Instance、原子连续性、跨 Replicate 复现、Species Fate、表观速率以及可审计 DFT Initial Geometry。这使它有条件把每个理论结果追溯到“哪些轨迹、哪些具体发生、哪些原子、哪一种代表构象”，并并列保留 MD 观察证据、TS/IRC 驻点证据和 RRKM/ME 动力学证据。所调研工具中有的擅长 MD 统计，有的擅长自动量化，有的擅长动力学和不确定度；**没有一个官方功能说明同时突出本项目这套 occurrence-level 原子谱系审计与完整理论动力学证据链**。这是“少见组合”，但不应写成未经穷尽检索的绝对首创声明。
4. **最优架构是 ReacNet Scope 做 system of record 与决策/审计层，外部程序做执行器。** 短期优先导出/导入 ARC 项目和 Arkane/MESS 输入输出；已知反应物与产物的单步 TS 可按体系选择 ARC 的 TS adapters、autodE 或 pysisyphus，气相单分子 PES 扩展才考虑 KinBot。不要把 Gaussian/ORCA/MESS 的调度和解析逻辑直接写进现有 Dash 进程。
5. **`TS optimization → frequency → IRC → RRKM/master equation` 远不是四个按钮。** 在它们之前还缺电子态、反应复合物、构象/代表 occurrence、TS 初猜和端点 minima；在它们之间缺质量门、能量/热化学校正、转子/对称性、隧穿和无垒通道处理；在 master equation 之前还缺 bath gas、碰撞参数、能量转移模型、T/P 网格、grain 和多势阱拓扑。最先应开发的是稳定的 `Calculation Dossier` / `Kinetics Evidence` 数据契约，而不是直接启动量化程序。

## 调研范围与证据规则

本文优先采用项目官方文档、官方源代码和论文原文。功能判断截至调研日期；“未见”只表示在本次审阅的官方公开材料中没有成为明确能力，不等于数学上证明不存在。

本地产品语义以 [`CONTEXT.md`](../../CONTEXT.md)、[`software-design-baseline.md`](../software-design-baseline.md) 和已接受 ADR 为准。尤其保留以下边界：

- ReacNet Scope 的 Reaction Type / Reaction Occurrence 权威来源仍是 ReacNetGenerator evidence，不由量化程序反向改写。[产品定位](../software-design-baseline.md#1-产品定位)
- DFT Initial Geometry 是 occurrence 派生的起始几何，不是优化结构、TS、IRC 或完整计算作业。[ADR-0010](../adr/0010-derive-dft-geometries-from-matched-occurrences.md)
- Candidate Path 是 MD-observed directed reaction hypergraph 上的候选；TS/IRC 或理论网络只能增加证据层级，不能把未观察边静默改成 MD-observed edge。[ADR-0013](../adr/0013-separate-candidate-discovery-from-continuous-md-support.md)
- 当前软件中的“事件频率”与下文的“振动频率计算”是两个完全不同的概念。

## 当前已有的稀缺资产与明确缺口

| 能力 | 当前 ReacNet Scope | 对下游量化动力学的意义 |
| --- | --- | --- |
| 精确 Reaction Occurrence 与稳定身份 | 聚合记录展开为 occurrence；匹配具体 Transition 与参与原子 | 可把每个计算绑定到可复核的真实事件，而不是只有一条反应式 |
| Molecule Instance、atom IDs 与 lineage | 可做 Event Path、Molecule Lineage、Continuous MD Support、Species Fate | 可保留反应核、锚点、离去片段和后续命运，支持 occurrence-conditioned 计算选择 |
| 多 Replicate / 多 Simulation Condition | 有批量对比、检出率与复现统计 | 可区分偶发现象、可复现通道和条件依赖通道 |
| Apparent Rate Constant Estimate | 用 occurrence count / reactant exposure 计算带 Poisson CI 的一、二阶表观 `k` | 将来可与 TST/ME 结果做同条件一致性诊断，但二者不能混称 |
| DFT Initial Geometry | 从 matched occurrence 的 before/after frame 重建反应物/产物非周期簇，保留 atom map | 已有“从轨迹到量化输入”的关键第一步 |
| 候选路径与 evidence drill-down | 网络级 Candidate 与 Continuous MD Support 分离 | 可先按实际重要性挑选 Top-N 量化，而不是盲算全网 |

当前尚无以下正式能力：

- stationary point、electronic state、conformer、reaction complex、transition-state channel 的稳定数据模型；
- charge / multiplicity / electronic-state policy；现有设计明确不推断它们；
- 代表 occurrence 聚类、构象搜索、反应物/产物最低点优化或复合物构型生成；
- TS guess 生成、TS 优化、振动频率、normal-mode displacement、IRC 与端点结构同一性检查；
- QC engine / parser / scheduler adapter、重试、检查点和部分失败状态；
- 统一参考态的电子能、ZPE、焓、熵、自由能、频率缩放、对称数和 hindered-rotor schema；
- canonical TST、隧穿修正、barrierless/variational channel；
- RRKM state-count/density-of-states 与 master-equation network；
- bath gas、Lennard-Jones、energy transfer、T/P grid、energy grain 和 solver output；
- ChemKin / Cantera / RMG mechanism 导出及 reactor/microkinetic simulation；
- 理论速率与 MD 表观速率、事件计数和 Species Fate 的对齐比较。

## 代表性程序与平台

### 1. Reactive-MD 事件发现与网络分析

| 程序 | 官方定位与已公开能力 | 对本项目的启示 / 空白 |
| --- | --- | --- |
| **ReacNetGenerator** | 从含坐标或 bond order 的 MD 轨迹生成反应网络，使用 HMM 降噪、SMILES 识别 isomer 并做网络可视化。[官方仓库](https://github.com/deepmodeling/reacnetgenerator)；[原始论文](https://pubs.rsc.org/en/content/articlehtml/2020/cp/c9cp05091d) | 是本项目 evidence producer；Scope 的价值不应是重新做一遍事件检测，而应补足 occurrence 查询、原子连续性、统计审计和下游计算闭环。 |
| **ChemTraYzer / ChemTraYzer2** | CT2 从需要 bond order 的 reactive-MD 轨迹检测和区分事件，输出 unique reactions、occurrence count、net flux、population 与基于事件/暴露的 rate constant 和 Poisson CI；当前文档还说明 subgraph descriptor 不区分 stereoisomer。[CT2 官方文档](https://www.scm.com/doc/Workflows/ChemTraYzer2/ChemTraYzer2.html) | 是最接近的 MD 后处理对照。其 2018 混合 RMD/QM 工作已经能产生 ChemKin 模型，因此“接量化”不够构成差异化；应聚焦本项目精确 occurrence、lineage、跨 Replicate 和证据下钻的组合。 |
| **ChemTraYzer 3（beta）** | 当前官方文档已把 reactive event 的 trajectory atom IDs、chain-of-states、bond changes、R/P/近似 TS frame 连接到 preoptimization、TS optimization、frequency/normal-mode、双向 IRC、endpoint optimization 和 ideal-gas TST，并支持 NEB 等搜索；官方同时明确标为 beta、尚未广泛测试。[官方总览](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/)；[QM/TS 文档](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/reference/qm.html)；[reaction sampling](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/reference/reaction_sampling.html) | 这是对“MD occurrence → TS/freq/IRC/TST”最直接的公开重叠，进一步说明基本流水线不是差异点。可把它作为行为与失败分类的 benchmark；Scope 仍应坚持 RNG 为 observation 权威，并以跨 Replicate、lineage/fate、theory-to-sampling closure 和 solver-neutral provenance 拉开差距。 |
| **ChemXDyn（2026 preprint）** | 使用时间分辨距离 signature、valence/coordination 约束来抑制瞬时相遇造成的伪键，并在 ReaxFF 与神经网络势轨迹上比较路径和 rate estimation。[论文原文](https://arxiv.org/abs/2601.08385) | 表明 reaction-detection robustness 仍是活跃竞争点。当前 Scope 不应越过 RNG 权威边界，但可增加“来源/算法对照数据集”，而不是静默合并不同 detector。 |

### 2. 自动反应发现、TS 与 IRC

| 程序 | 官方定位与已公开能力 | 对本项目的启示 / 空白 |
| --- | --- | --- |
| **AutoMeKin** | 从一个 XYZ 出发，以 heuristic/MD + graph theory 搜索 TS，经 IRC 得到 reactant/product，构建网络，之后可算 rate 并用 KMC 预测 population/product ratios；支持低层级发现后高层级重优化。[官方仓库](https://github.com/emartineznunez/AutoMeKin)；[官方教程](https://emartineznunez.github.io/AutoMeKin/docs/tutorial.html) | 适合“主动探索未知 PES”，但它生成自己的 network，不理解 RNG occurrence identity、Replicate 或 atom-lineage evidence。可借鉴 LL→HL 漏斗和 KMC relevance 筛选。 |
| **KinBot + PESViewer** | KinBot 按 reaction families 自动搜索气相多势阱 PES，执行 TS/IRC/频率与多层级计算，并可走到 MESS 输入；PESViewer 画 wells、bimolecular states、TS/barrierless edges 与能量路径。[KinBot 官方仓库](https://github.com/zadorlab/KinBot)；[KinBot kinetics 论文](https://doi.org/10.1021/acs.jpca.2c06558)；[PESViewer 官方仓库](https://github.com/zadorlab/PESViewer) | 气相、单分子/明确双分子通道很有参考价值，但不应替代 MD-observed Candidate。已有专项结论见 [`kinbot-candidate-pathways.md`](kinbot-candidate-pathways.md) 与 [`pesviewer-candidate-path-visualization.md`](pesviewer-candidate-path-visualization.md)。 |
| **ChemDyME** | 用 boxed/accelerated MD 探索新反应，结合 statistical rate theory 与 MESMER master-equation calculation 推进 network，并以目标条件下的 kinetic convergence 约束继续探索哪些 Species。[论文原文](https://pubs.acs.org/doi/abs/10.1021/acs.jctc.1c00335)；[官方仓库](https://github.com/RobinShannon/ChemDyME) | 是“动力学闭环自动探索”的最直接对照，证明 MD + ME steering 不是空白；但它运行自己的探索过程，而不是把独立 ReacNetGenerator dataset 的 occurrence/lineage/Replicate evidence 作为可下钻的来源层。 |
| **SCINE Chemoton + Puffin + Heron + KiNetX** | Chemoton 以 first-principles 自动扩展 reaction network；Puffin 对接 QC 与 scheduler；Heron 可视化并人工控制 rolling exploration；Chemoton 可按 kinetic/path analysis steer network growth，KiNetX 提供 sensitivity 与 uncertainty propagation。[Chemoton 官方说明](https://scine.ethz.ch/download/chemoton)；[Heron 论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC11492315/)；[KiNetX 官方说明](https://scine.ethz.ch/download/kinetx) | 这是“数据库 + worker + UI + kinetics steering”的成熟参照，说明通用探索平台竞争激烈。Scope 应坚持其 MD evidence specialization，并把外部计算作为可插拔执行层。 |
| **autodE** | 从 reactant/product SMILES 或 3D structures 自动生成 reaction profile，含 atom mapping、association complex、conformer、NEB/CI-NEB/TS location 与多个 ESS wrapper。[官方仓库](https://github.com/duartegroup/autodE)；[官方 TS 文档](https://duartegroup.github.io/autodE/examples/tss.html) | 已知两端的单步计算比 KinBot 全 PES 搜索更贴近 Scope 的 selected Reaction Type；但它不是 occurrence/network evidence database，也不负责 RRKM/ME。 |
| **pysisyphus** | 提供 minimum/first-order saddle optimization、NEB/GSM 等 chain-of-states、mass-weighted IRC 与端点优化，并可调用外部 QC code。[官方文档](https://pysisyphus.readthedocs.io/en/latest/index.html)；[IRC 文档](https://github.com/eljost/pysisyphus/blob/master/docs/irc.rst) | 是算法执行器候选，适合保留局部环境或自定义 calculator 的路径；仍需 Scope 负责 job spec、身份回写和科学质量状态。 |

### 3. 完整量化作业与速率工作流

| 程序 | 官方定位与已公开能力 | 对本项目的启示 / 空白 |
| --- | --- | --- |
| **ARC (Automated Rate Calculator)** | 从 graph/XYZ 和 Reaction 定义生成、提交、监控、解析并重试 QC 作业；job types 包括 conformer opt/SP、minimum/TS opt、freq、single point、rotor、IRC、OneDMin；TS adapters 包括 heuristics、AutoTST、KinBot、xTB-GSM、ORCA-NEB 等，kinetics/thermo 默认可交给 Arkane。[官方总览](https://reactionmechanismgenerator.github.io/ARC/index.html)；[高级功能](https://reactionmechanismgenerator.github.io/ARC/advanced.html)；[输入契约](https://reactionmechanismgenerator.github.io/ARC/input_reference.html) | **最值得优先做的外部 adapter。** Scope 可把精确 Reaction Type、occurrence-derived reactant/product geometries、atom map、用户确认电子态写成 ARC project；ARC 做计算，Scope 再导入结构化状态和 artifact，不应复制其 scheduler/troubleshooting。 |
| **T3 + RMG + ARC + Arkane** | T3 用 Cantera/RMG simulation sensitivity 选择需由 QM refinement 的 species/reactions，支持 pressure-dependent network sensitivity、QM budget 和多轮 RMG/ARC 更新。[T3 how-to](https://reactionmechanismgenerator.github.io/T3/how_to/)；[T3 pressure-dependence 示例](https://reactionmechanismgenerator.github.io/T3/examples/) | “根据敏感度只算重要步骤”已有强对照；本项目的差异应是把**实际 MD occurrences、跨 Replicate 和 lineage/fate**也纳入 acquisition score，而不是只复制 sensitivity-driven QM。 |
| **QCArchive / QCFractal** | 面向数千到数百万 QC calculations 的 server/client/worker 数据与分布式执行平台，强调存储、分享、检索和错误管理。[官方架构](https://docs.qcarchive.molssi.org/overview/index.html) | 若规模扩大，可借鉴/对接其计算记录与 worker 模型；它不提供本项目 reaction evidence、TS/IRC 科学状态或 RRKM/ME 语义。 |

### 4. RRKM/master equation 与网络动力学

| 程序 | 官方定位与已公开能力 | 对本项目的启示 / 空白 |
| --- | --- | --- |
| **Arkane / RMG-Py** | 从 QC log 或显式 statmech 数据计算 RRHO + hindered-rotor thermochemistry、canonical TST + Wigner/Eckart tunneling、高压极限速率、RRKM `k(E)` 和一维 master equation `k(T,P)`；压力依赖输入包括 network、collision/energy-transfer model、bath gas、T/P、grain 与 reduction method。[Arkane introduction](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/introduction.html)；[输入文档](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html)；[RRKM API](https://reactionmechanismgenerator.github.io/RMG-Py/reference/pdep/reaction.html) | 最适合作为第一版 statmech adapter；它也清楚展示“频率+IRC 后还缺什么”。Arkane 不知道计算源自哪个 RNG occurrence，Scope 必须维护 crosswalk。 |
| **MESS** | 主要用途是通过一维 master equation 计算 complex-forming reaction 的 temperature/pressure-dependent phenomenological rates；还支持 microcanonical rates、partition functions、stabilization probability 和 time-dependent population propagation。[官方仓库](https://github.com/Auto-Mech/MESS) | 适合作为专家级外部 solver/export target；不要在首版自行实现 master-equation 数值内核。 |
| **Cantera** | 读取 kinetic mechanism，集成 reactor network，提供 reaction path diagram、rates/flux 与 sensitivity analysis。[Reactor Network 文档](https://www.cantera.org/stable/userguide/reactor-tutorial.html)；[交互 reaction-path 示例](https://www.cantera.org/dev/examples/python/kinetics/interactive_path_diagram.html) | 适合最终 mechanism simulation 与可视化，不负责从 occurrence 找 TS 或生成 statmech 参数。 |
| **ReactionMechanismSimulator.jl (RMS)** | 模拟气、液、表面的大反应机理，提供 forward/adjoint/transitory sensitivity、rates、flux diagram 和自动 mechanism analysis。[官方文档](https://reactionmechanismgenerator.github.io/ReactionMechanismSimulator.jl/latest/)；[分析 API](https://reactionmechanismgenerator.github.io/ReactionMechanismSimulator.jl/latest/Analysis/) | 若重点是大网络、可微/adjoint sensitivity，RMS 是 Cantera 的重要并行目标；仍应由独立 adapter 消费导出的 mechanism。 |
| **CatMAP / Zacros（相邻领域）** | CatMAP 面向 descriptor-based heterogeneous-catalysis microkinetics、degree of rate/selectivity control；Zacros 面向 lattice KMC 与结果 GUI。[CatMAP 教程](https://catmap.readthedocs.io/en/latest/tutorials/index.html)；[Zacros 官方说明](https://www.zacros.org/software/obtain-zacros) | 如果未来处理表面反应，气相 RRKM/ME 不是通用答案；需单独设计 adsorption/site/coverage 身份与 surface kinetics，不能把当前 Species/Reaction Type 直接套用。 |

## 能力横向矩阵

符号：`●` = 官方材料明确以此为主要能力；`◐` = 部分覆盖或需组合其它组件；`—` = 不是核心能力。矩阵只用于产品定位，不代替逐版本验收。

| 体系 | 导入既有 MD 事件 | 自动 TS/freq/IRC | RRKM/ME | reactor/microkinetics | 可恢复计算工作流 | occurrence/atom-lineage 审计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ReacNetGenerator | ● | — | — | — | ◐ | ◐ |
| ChemTraYzer2 + hybrid workflow | ● | ◐ | ◐ | ● | ◐ | ◐ |
| AutoMeKin | — | ● | ◐ | ●（KMC） | ● | — |
| KinBot | — | ● | ●（MESS input） | ◐ | ● | — |
| ChemDyME | —（自己运行 accelerated MD） | ● | ●（MESMER） | ● | ◐ | — |
| SCINE stack | — | ● | ◐ | ●（KiNetX） | ● | — |
| ARC + Arkane/T3/RMG | — | ● | ● | ● | ● | — |
| autodE / pysisyphus | — | ● | — | — | ◐ | — |
| ReacNet Scope 当前 | ● | — | — | ◐（表观 `k`，非机理模拟） | ●（索引任务） | ● |

结论不是“别人没有自动化”，而是：**Scope 已经占住了最后一列，应该向中间几列建立有来源的桥，不应放弃最后一列去和通用自动量化平台正面重复建设。**

## 可视化与工作流：不要把五种图混成一张图

相关程序展示的是不同科学对象：PESViewer 画 stationary states 与 TS energy；Heron 让用户查看并控制 rolling quantum exploration；Cantera 画给定条件下的 reaction flux/path；ARC/QCArchive 展示 calculation/job status。它们不能由一个“万能反应网络”无损替代。[PESViewer 专项调研](pesviewer-candidate-path-visualization.md)；[Heron 论文](https://pmc.ncbi.nlm.nih.gov/articles/PMC11492315/)；[Cantera interactive path diagram](https://www.cantera.org/dev/examples/python/kinetics/interactive_path_diagram.html)；[ARC structured status](https://reactionmechanismgenerator.github.io/ARC/how_it_works.html)

建议在同一 selected channel / path 上同步但分离五个视图：

| 视图 | 节点 / 边语义 | 默认编码 | 不可冒充的内容 |
| --- | --- | --- | --- |
| **MD Evidence Graph** | exact Species / directed Reaction Type | observed count、Replicate、direction、lineage coverage | 不能把 theoretical-only edge 加进来 |
| **Occurrence Timeline** | Reaction Occurrence / continuity link | Transition、idle time、atom anchors、censoring | 不能把网络可达性画成一条实采样链 |
| **PES / Energy Profile** | minima/complexes / TS channel | `E0`、`H/G(T)`、barrier、method、coverage | 缺能量不是 barrierless；focal Species 不是完整 state |
| **Kinetic Flux Graph** | model Species / elementary rates | 条件相关 gross/net flux、sensitivity | flux 不是 occurrence count，也不是 TS energy |
| **Workflow / Coverage DAG** | calculation artifacts / dependency | queued、running、failed、validated、stale | 作业成功不等于科学验证成功 |

这些视图共享 selection 和 crosswalk：点击一条 kinetic edge，可回到对应 TS/IRC，再回到 supporting occurrences；点击一条 observed occurrence，也能看到它当前属于哪个 geometry cluster、有哪些计算已覆盖。视觉差异本身就是防止用户混淆证据层级的科学约束。

## “别人少见但很有用”的候选功能

### 候选 A：三层 Evidence Ladder 与冲突可视化（优先级最高）

为每个 directed Reaction Type / channel 同时展示相互独立的三层状态：

1. `md_observed`：哪些 Replicate、Transition、occurrence、Molecule Instance 和 atom lineage 支持它；
2. `stationary_point_validated`：哪些 representative occurrences 产生了哪组 minima/TS、频率质量、IRC 两端和计算层级；
3. `kinetics_characterized`：canonical `k(T)`、RRKM `k(E)`、ME `k(T,P)`、适用条件和 uncertainty。

再增加明确冲突状态，例如：

- MD 观察到，但所有已尝试 TS 都连接到别的端点；
- 理论存在低垒通道，但当前轨迹在足够 exposure 下没有观察到；
- 同一 Reaction Type 由多个不同 TS/conformer channels 实现；
- 只有 partial energy coverage，或 solver 输入依赖估计碰撞参数；
- 量化搜索失败，仅表示 `not_computed / search_exhausted_within_budget`，绝不等于 `barrierless` 或“不存在”。

这不是单纯的状态条。它让用户从一条 `k(T,P)` 一键回到 TS normal mode、IRC endpoints、原始 QC artifact，再回到具体 MD occurrence 和局部轨迹。已有程序通常分别擅长其中一层；这种贯穿式证据审计与当前 Scope 数据模型最匹配。

### 候选 B：Occurrence-conditioned TS ensemble，而不是“一条反应一个几何”

对同一 Reaction Type 的全部 matched occurrences 按反应核几何、接触取向、局部配位和环境描述符聚类，从不同 cluster medoid 生成 reactant/product complex 与 TS guesses。结果记录：

- 每个 cluster 的 Replicate/condition 覆盖和权重；
- 每种 TS channel 的成功率、端点身份、barrier distribution；
- 是否存在“同一计量反应、多个几何机理”；
- 哪些 occurrence 无法被任何已得 TS 解释。

ChemTraYzer 已证明可从 reactive MD 走向 TS/kinetic model，ARC/AutoMeKin 也会尝试多个 TS guesses；真正少见的部分是**把每个 TS channel 的权重和失败明确追溯到 occurrence ensemble 与跨 Replicate 证据**。这比只挑最高频的一帧更稳健，也能发现环境/取向依赖通道。

### 候选 C：Theory-to-sampling closure test（理论—采样闭环诊断）

当 `k(T,P)` 的物理模型与 MD 条件可比时，用当前软件已有的 reactant exposure 计算理论期望事件数：

```text
expected_events = integral(theoretical_rate(condition, time) * reactant_exposure(time))
```

再把 expected count distribution 与 observed occurrence count + Poisson CI 并列，输出而不是隐藏以下可能性：

- `consistent_with_sampling`；
- `undersampled`：理论预期也小，未观察不构成反证；
- `observed_deficit` / `observed_excess`：值得检查势函数、reaction detection、电子态或非平衡条件；
- `not_comparable`：高压 MD、非平衡温度、凝聚相、加速采样或不同标准态不能直接比较。

还可检查正逆方向与 detailed-balance 预期的偏离，但只能作为诊断，不能强制“修正”MD evidence。这个功能直接连接本项目现有表观 `k`/exposure 与下游理论 rate，是非常强的产品差异点。

### 候选 D：MD evidence + kinetic sensitivity 的预算分配器

建立 versioned acquisition score，优先计算：

- 对用户关心 Species Fate / target yield 最敏感的步骤；
- MD 中高 flux、高复现但 theory coverage 低的步骤；
- 低频却是候选路径 bottleneck 或唯一 bridge 的步骤；
- 理论与观察最不一致、且增加一次计算最可能消除歧义的步骤；
- 多 TS/conformer 不确定度最大的步骤。

T3 和 KiNetX 已有 sensitivity/kinetic steering，因此“按敏感度选计算”不是新功能；差异在于把 occurrence count、lineage continuity、censoring、Replicate reproducibility 和 expected-information-gain 一起纳入，并让每次选取理由可审计。

### 候选 E：保留局部环境的 Quantum Context Package

现有 DFT Initial Geometry 可扩展为多种明确模式，而不是一律抽成孤立气相分子：

- `gas_phase_complex`：当前非周期 cluster；
- `embedded_cluster`：反应核 + 距离/分子壳层，记录冻结原子和总电荷；
- `periodic_cell`：保留 cell/PBC，供 NEB 或周期 DFT；
- `qm_mm_partition`：显式 QM/MM atom set 与 link-atom policy；
- `ensemble_package`：同一 channel 的多个 occurrence medoids。

气相程序通常会把问题化成 wells/TS；对溶剂、界面、表面和多体协同事件，这种来源可追溯的 context package 更有价值。它也要求先设立 domain gate：不允许对显著凝聚相事件默认运行气相 RRKM。

### 候选 F：Rate-aware Species Fate 与 counterfactual mechanism analysis

在现有 Species Fate 的 atom-continuous、显式 censoring 结果旁增加独立的 kinetic model 视图：

- 哪些 elementary rates 控制某个 Fate Signature 的概率和 first-passage time；
- 将一个 barrier / collision parameter / channel presence 改变后，endpoint distribution 如何变化；
- 观察到的 fate 与 mechanism simulation 的 fate 是否一致；
- 哪些 conclusion 只由 unresolved/censored episodes 支撑。

Cantera/RMS/KiNetX 擅长 sensitivity，Scope 擅长 occurrence-level fate；把二者对齐而不混同 observational fate 与 model prediction，是有辨识度的交互功能。

### 候选 G：失败、覆盖与 mechanism revision diff 作为一等结果

给每个 network revision 输出：

- observed / computed / validated / kinetics-covered 的边覆盖率；
- TS guess、optimization、frequency、IRC、statmech、ME 各阶段失败分类；
- 从 level-of-theory、代表 occurrence、电子态或 solver 参数改变后，新增/消失/改端点/大幅改 rate 的 channel；
- 对路径排名和 Fate prediction 的影响。

大规模自动计算不可避免有失败；ARC 已证明结构化状态与 restart 很重要。Scope 可以进一步把失败和 revision 对 MD evidence 结论的影响做成用户可读的 coverage map，而不是把失败藏在日志中。

### 候选优先级

| 候选 | 用户价值 | 与现有资产匹配 | 市场差异度（谨慎判断） | 实现成本 | 建议 |
| --- | ---: | ---: | ---: | ---: | --- |
| A Evidence Ladder / 冲突视图 | 极高 | 极高 | 高 | 中 | P0，先做数据契约与只读导入 |
| B occurrence-conditioned ensemble | 极高 | 极高 | 高 | 高 | P1，先做聚类与离线导出 |
| C theory-to-sampling closure | 极高 | 极高 | 很高 | 中高 | P1，需先有可比 rate 与条件契约 |
| D evidence + sensitivity 预算器 | 高 | 高 | 中高 | 高 | P2，避免在 rate coverage 很低时过早做 |
| E Quantum Context Package | 高 | 高 | 高（凝聚相方向） | 高 | P1/P2，按实际数据体系决定 |
| F rate-aware fate / counterfactual | 高 | 极高 | 高 | 高 | P2 |
| G coverage + mechanism diff | 高 | 高 | 中高 | 中 | P1，与工作流状态同时做 |

## 接 `TS optimization + frequency + IRC + RRKM/master equation` 前后到底还缺什么

下面按科学依赖顺序列出。`TS optimization` 不是起点，`master equation` 也不是终点。

### 0. 先做适用域分流

至少区分：

- 气相 isolated molecule / bimolecular complex；
- 高压但可用气相碰撞模型的多势阱体系；
- 凝聚相/显式溶剂；
- 表面/周期体系；
- 非绝热、激发态或明显多重态交叉。

标准 RRKM + 一维 ME 对气相多势阱最自然。凝聚相、表面和非绝热事件可能需要 free-energy profile、periodic NEB、QM/MM、KMC 或非绝热动力学；不能只换一个量化后端就沿用同一语义。

#### 如果计划直接用 DeePMD 势做 TS/frequency/IRC

技术上可行，但还不是可信动力学的即插即用路径。DeePMD-kit 官方 `DP` ASE calculator 可提供 energy/forces 并直接参与 BFGS optimization；该接口公开的 properties 主要是 energy、forces、virial/stress，因此 Hessian/frequency 还需要所选执行器的数值差分或其它明确实现。[DeePMD ASE calculator](https://docs.deepmodeling.com/projects/deepmd/en/stable/third-party/ase.html)；[calculator API](https://docs.deepmodeling.com/projects/deepmd/en/stable/autoapi/deepmd/calculator/index.html)

至少还要增加以下质量门：

- 固定 model file digest、type map、model head、`fparam/aparam`、cell/PBC 和软件版本；
- 对 minima、TS guess、优化路径、IRC images 和端点逐帧计算 committee model deviation；官方文档把 ensemble force/virial deviation 作为给定 frame 是否被训练数据覆盖的误差指标，但它仍只是 indicator，不是准确性的证明。[DeepMD model deviation](https://docs.deepmodeling.com/projects/deepmd/en/latest/test/model-deviation.html)
- 明确数值 Hessian 的 displacement、收敛、acoustic/translation/rotation projection 和 reproducibility；
- 验证从周期高密度轨迹抽出的非周期 cluster 是否仍在模型适用域内，不能只因 calculator 能运行就视为物理可信；
- charge、multiplicity、electronic state 和 spin crossing 仍由独立的 calculation-state contract 处理，不能从普通 DP energy/force 输出倒推；
- LL DeePMD barrier 与 HL DFT/ab-initio refinement 分层保存；用于 RRKM/ME 的 wells 与 TS 必须处在可比较能量口径，并对关键通道做高层级验证。

因此更稳妥的首版定位是：**DeePMD/MLIP 用于 occurrence-conditioned geometry relaxation、NEB/GSM/TS guess 与低层级筛选；最终频率、热化学和关键 rate 可按预算由受控的电子结构层级复核。** 若用户明确只要 MLIP-level kinetics，也必须把适用域与 model-deviation evidence 随结果发布。

### 1. `Channel Calculation Dossier` 身份与来源

建议新建独立于 Reaction Type 的 calculation identity，至少由以下内容决定：

- dataset revision + source occurrence(s) / cluster；
- directed Reaction Type 与 exact atom mapping；
- selected reactant/product Molecule Instances；
- context mode（gas/embedded/periodic/QM-MM）；
- charge、multiplicity 与 electronic-state policy；
- method/basis/solvation/dispersion/model version；
- geometry/conformer/TS-search protocol version。

同一个 Reaction Type 可以有多个 conformer channels、TS channels、electronic states 和环境模型，不能把所有计算结果直接写回一列 `barrier`。计算 identity 也不能改变 Reaction Type / Candidate identity。

### 2. 电子态与化学计量审核

必须显式解决：

- 每个 fragment 和整体的 charge / multiplicity；
- radical count、open-shell/closed-shell、broken-symmetry policy；
- 反应两侧总电荷、原子和自旋可达性；
- association/dissociation 的 standard-state 与 fragment reference；
- spectator、third body、catalyst、surface site 是否属于 elementary channel。

ARC 的输入也把 `charge`、`multiplicity`、reaction state 和 atom mapping 作为独立数据；这证明仅有 SMILES + XYZ 不够。[ARC 输入文档](https://reactionmechanismgenerator.github.io/ARC/input_reference.html)；[ARC reaction/atom-map 数据流](https://reactionmechanismgenerator.github.io/ARC/how_it_works.html)

### 3. 代表 occurrence、构象和 reaction complex

需要：

- 对 occurrence 几何去重/聚类，避免对上千次相同事件全算；
- reactant/product 各自最低能 conformer 搜索；
- 多分子 reactant/product complex 的相对取向与 encounter conformer；
- occurrence-derived geometry 与重新生成低能构象并存，不能覆盖来源；
- 对柔性物种决定 multi-structure thermochemistry / multi-conformer TST 的策略。

ARC 的默认 job family 和 Arkane 的 statmech 输入都包含 conformer、rotor 和 symmetry；省略这一步会让熵和速率严重依赖任意的一帧几何。[ARC advanced](https://reactionmechanismgenerator.github.io/ARC/advanced.html)；[Arkane input](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html)

### 4. 端点 minima 优化与统一能量层级

TS 两端必须有可比较的 optimized minima/complexes。至少保存：

- optimization level、single-point refinement level；
- electronic energy、ZPE、thermal corrections 与 reference；
- 收敛和 geometry/isomorphism checks；
- fragment/complex 与 BSSE/standard-state policy（若适用）；
- frequency scale factor 与单位。

不能用 DeePMD 轨迹瞬时系统总能、局部原子能、DFT optimized electronic energy 和 ZPE-corrected barrier 混在一个无类型字段中。

### 5. TS guess generation

对已知两端至少需要一个或多个策略：

- occurrence 中的最大畸变/键变 frame；
- constrained optimization / scan；
- NEB / CI-NEB / GSM；
- reaction-family heuristic / AutoTST；
- KinBot / AFIR 类探索；
- 用户上传 guess。

必须记录每个 guess 的来源、cluster 与后续去重；“某个 guess 失败”不等于 channel 不存在。ARC 已把多个 TS adapters 和 user guesses 作为并列输入，pysisyphus 提供 NEB/GSM；可优先复用。[ARC TS adapters](https://reactionmechanismgenerator.github.io/ARC/advanced.html#transition-state-search-adapters)；[pysisyphus chain-of-states](https://pysisyphus.readthedocs.io/en/dev/chainofstates.html)

### 6. 可恢复的 QC execution plane

需要的不只是 `subprocess.run`：

- engine adapter（Gaussian/ORCA/Q-Chem 等）和版本化模板；
- local/Slurm/PBS/remote execution；
- resource estimate、队列限制、取消与恢复；
- output parser、artifact checksums、stdout/stderr；
- SCF/optimization/IRC failure taxonomy 与受限自动重试；
- project isolation，不能让 Dash worker 直接长期持有作业状态；
- partial success 与手工导入。

ARC、Puffin 和 QCArchive 都已经解决大量通用基础设施。现有 design baseline 把集群调度列为当前非目标，因此近期最稳妥的是 adapter/export-import，而不是把 HPC scheduler 并入 Scope core。

### 7. TS optimization 的质量门

至少要求：

- stationary-point convergence；
- 恰当数量的显著 imaginary frequency（通常一条目标反应坐标）；
- normal-mode displacement 与预期 bond changes 一致；
- TS conformer/duplicate 聚类；
- energy 相对两端合理；
- 对低频/伪虚频、higher-order saddle、spin contamination 等保留警告而非简单通过。

ARC 的 `successful_normal_mode`、`successful_irc` 和 structured errors 可作为状态设计参照。[ARC TSGuess API](https://reactionmechanismgenerator.github.io/ARC/api/species.html)

### 8. Frequency 不只是“有一条负频”

minimum 与 TS 都需要 Hessian/frequencies，后续还要：

- ZPE、thermal enthalpy/entropy/free energy；
- frequency scaling；
- 低频模式与 hindered-rotor 替换；
- external/internal symmetry、optical isomers；
- rotational constants、mass、moments；
- imaginary mode 强度和位移检查。

Arkane 采用 RRHO 并允许 hindered-rotor correction；其输入明确包含 frequency scale factor、rotor scan、symmetry 等。[Arkane introduction](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/introduction.html)；[Arkane input](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html)

### 9. IRC 与端点身份回写

IRC 必须双向运行或提供等价验证，并把优化后的端点重新映射到：

- exact Species / full reaction side；
- atom mapping 与 expected bond changes；
- source Reaction Type；
- 若端点不同，生成 `connects_elsewhere` 的新 theoretical channel，而不是静默改写 MD Reaction Type。

还需处理 dissociation 后弱结合 fragments、同一端点不同 conformer、IRC 中断和无明显 saddle 的通道。pysisyphus 官方 IRC 支持双向积分与端点优化；ARC 也以 endpoint isomorphism 记录 `successful_irc`。[pysisyphus IRC](https://github.com/eljost/pysisyphus/blob/master/docs/irc.rst)；[ARC TSGuess API](https://reactionmechanismgenerator.github.io/ARC/api/species.html)

### 10. Canonical rate layer：TST、隧穿与无垒通道

在 RRKM 前应先形成清楚的 canonical high-pressure rate contract：

- canonical TST / multi-structural TST；
- Wigner/Eckart 或更高级 tunneling；
- reaction-path degeneracy；
- conformer channel 汇总；
- reverse rate 与 thermodynamic consistency；
- T grid 与 fitting（Arrhenius/modified Arrhenius）；
- barrierless association/dissociation 的 variational/capture/high-pressure input。

Arkane 支持 Wigner/Eckart；对于没有显式 TS 的 barrierless reaction，文档要求外部 high-pressure kinetics 并通过 inverse Laplace transform 构造 `k(E)`，说明“TS 搜不到”绝不能自动标成 barrierless。[Arkane reaction input](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html#reaction)

### 11. RRKM 所需的 microcanonical 数据

RRKM 需要的不仅是 barrier：

- 各 well 和 TS 的 `E0`；
- reactant density of states `rho(E[,J])`；
- TS sum of states `N‡(E[,J])`；
- vibrational/rotational/internal-rotor modes、symmetry 与 degeneracy；
- tunneling 与 angular-momentum treatment；
- association/dissociation 的 channel definition。

Arkane/RMG 的 RRKM API 明确使用 `N‡ / (h rho)` 并要求完整 reactant/TS statmech data。[RRKM theory/API](https://reactionmechanismgenerator.github.io/RMG-Py/reference/pdep/reaction.html)

### 12. Master-equation model inputs

必须为每个 pressure-dependent network 定义：

- wells / isomers、bimolecular reactant/product channels 和 path reactions；
- bath gas composition；
- Lennard-Jones `sigma/epsilon` 或 collision model；
- energy-transfer model（如 single exponential down）及参数；
- T/P list 或 range；
- energy grain size / minimum grain count；
- solver/reduction method（MSC、reservoir state、CSE 等）；
- output interpolation（Chebyshev / PLOG）；
- sensitivity / uncertainty conditions；
- solver convergence 与 detailed-balance checks。

这些都是 Arkane pressureDependence 的正式输入；RMG theory 还明确给出 collision frequency 对 pressure 和 Lennard-Jones 参数的依赖。[Arkane pressure-dependent input](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html#pressure-dependent-rate-calculation)；[RMG collision model theory](https://reactionmechanismgenerator.github.io/RMG-Py/theory/pdep/master_equation.html)

### 13. Solver、结果拟合与可交换导出

第一版应支持至少一种 solver，但 schema 不应绑定 solver：

- Arkane 内置一维 ME：集成最方便；
- MESS：专家用户与复杂 multiwell 的重要导出目标；
- 可选 MESMER：若用户已有生态；
- 输出统一保存 raw `k(E)` / `k(T,P)`、solver input、log、fit coefficients、fit residual 与 units；
- 导出 ChemKin / Cantera YAML / RMG library 时保留 source manifest。

Arkane 能输出 Chemkin，并可转换给 Cantera；MESS 官方定位就是 complex-forming reaction 的 `k(T,P)` solver。[Arkane output](https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/output.html)；[MESS README](https://github.com/Auto-Mech/MESS)

### 14. Network kinetics 与回到 observation 的验证

最终还需：

- Cantera/RMS reactor simulation 或 KMC；
- flux、sensitivity、uncertainty、mechanism reduction；
- 与当前 Simulation Condition / Replicate 的 temperature、pressure、composition、volume 和 observation window 对齐；
- theory-to-sampling closure、Fate distribution 与 first-passage comparison；
- 明确 equilibrium/non-equilibrium、finite-size、accelerated-sampling 和 force-field 差异。

没有这一步，`k(T,P)` 只是单通道参数，并未回答网络在用户条件下产生什么。

## 建议的系统边界

```text
ReacNetGenerator evidence
        |
        v
ReacNet Scope
  occurrence / lineage / fate / replicate / apparent-k
        |
        +--> Calculation Dossier + occurrence ensemble + atom map
        |          |
        |          +--> ARC / autodE / pysisyphus / KinBot adapter
        |                       |
        |                       v
        |               minima / TS / freq / IRC artifacts
        |                       |
        |                       +--> Arkane / MESS
        |                                  |
        |                                  v
        |                            k(T), k(E), k(T,P)
        |                                  |
        +<---------------------------------+
        |
        +--> Evidence Ladder / conflict / coverage / theory-vs-MD
        |
        +--> Cantera / RMS export --> flux, sensitivity, fate prediction
```

职责建议：

- **Scope core 持有**：source identities、occurrence clustering、Calculation Dossier、证据状态机、crosswalk、artifact manifest、coverage/conflict、用户工作流和可视化。
- **ARC/其它 executor 持有**：QC input/output、scheduler、TS-search adapter、重试与计算级 restart。
- **Arkane/MESS 持有**：statmech、RRKM、master-equation 数值与标准输出。
- **Cantera/RMS 持有**：reaction-system time integration 与 sensitivity 数值。
- **禁止反向污染**：外部程序发现的新端点/边进入独立 `theoretical_channel` 层；只有新的 RNG evidence 才能使其成为 `md_observed`。

## 分阶段路线图

### Phase 0：科学数据契约（必须先做）

交付：

- `Calculation Dossier`、`Stationary Point Evidence`、`Kinetics Evidence` schema；
- electronic state、context mode、energy quantity kind、standard state、method provenance；
- evidence ladder / failure / unknown / barrierless 状态机；
- Reaction Type、occurrence、atom map、external job/artifact 的 crosswalk；
- 明确 gas-phase applicability gate。

验收重点：同一 Reaction Type 的两个 conformer/TS channels 不覆盖彼此；一次 TS 失败不会生成负面化学结论；外部新边不会成为 MD-observed edge。

### Phase 1：只做可移植导出/导入，不自动跑集群

交付：

- occurrence cluster/medoid 选择；
- ARC YAML + XYZ/manifest exporter；
- generic folder/artifact importer，首个实现读取 ARC `status.yml` / outputs；
- selected channel 的 Evidence Ladder UI；
- PES/normal-mode/IRC/geometry 的 artifact drill-down。

这一步已经能回答最重要的问题：从哪条 MD evidence 生成了什么计算、成功到哪一步、结果连接了谁。

### Phase 2：Top-N TS/freq/IRC 闭环

交付：

- 一种气相 known-endpoint preset（优先 ARC）；
- 多 TS guesses、normal-mode 与 IRC endpoint quality gates；
- occurrence-conditioned ensemble 的第一版；
- barrier/thermochemistry 可比性检查和 energy profile；
- failure/coverage map。

### Phase 3：Arkane high-pressure rate + RRKM/ME

交付：

- Arkane species/reaction/statmech input adapter；
- conformer/rotor/symmetry coverage；
- `k(T)`、`k(E)`、`k(T,P)` 与 uncertainty/sensitivity artifact；
- 用户显式确认 bath gas、collision/energy-transfer 和 T/P grid；
- MESS export 作为专家路径；
- ChemKin/Cantera export。

### Phase 4：真正差异化的闭环分析

交付：

- theory-to-sampling expected-count diagnostic；
- MD evidence + kinetic sensitivity 预算器；
- rate-aware Species Fate / counterfactual analysis；
- cross-condition mechanism revision diff；
- condensed-phase/periodic Quantum Context Package（若数据需求证明值得）。

## 推荐取舍

### 最值得立即做

1. `Calculation Dossier` + Evidence Ladder 数据模型；
2. occurrence-conditioned representative geometry clustering；
3. ARC export/import adapter；
4. TS/frequency/IRC 的结构化质量门和 artifact drill-down；
5. Arkane input/output adapter；
6. theory-to-sampling closure test。

### 借用，不重造

- QC scheduler、ESS parser、失败重试：优先 ARC；
- TS/IRC 数值算法：ARC adapters / autodE / pysisyphus / KinBot；
- RRKM/ME：Arkane/MESS；
- reactor/sensitivity：Cantera/RMS；
- 通用大规模 QC 数据平台：规模真正需要时再评估 QCArchive。

### 暂缓

- 在 Dash worker 内直接提交和守护集群作业；
- 自己实现 master-equation solver；
- 将 theoretical network 与 MD-observed network 合成一个无来源图；
- 对所有 Reaction Types 全网盲算 TS；
- 在没有 context/domain gate 时把凝聚相 occurrence 抽成气相 RRKM 通道；
- 把“没有 TS 结果”显示成 barrierless 或“反应不存在”。

## 一句话产品定位建议

> **ReacNet Scope 不只是从轨迹画反应网络，而是把每条 MD-observed reaction 从具体 occurrence、原子谱系和跨重复证据，一直追踪到 TS/IRC、统计速率与网络动力学，并明确显示证据覆盖、冲突和不确定度。**

这比“支持 Gaussian/ORCA/MESS”更有辨识度，也最能利用当前软件已经建立的领域模型。

## 主要来源

### Reactive MD 与混合流程

- ReacNetGenerator official repository: <https://github.com/deepmodeling/reacnetgenerator>
- Zeng et al., *ReacNetGenerator: an automatic reaction network generator for reactive molecular dynamic simulations*: <https://doi.org/10.1039/C9CP05091D>
- ChemTraYzer2 official documentation: <https://www.scm.com/doc/Workflows/ChemTraYzer2/ChemTraYzer2.html>
- Döntgen et al., *Automated Discovery of Reaction Pathways, Rate Constants, and Transition States Using Reactive Molecular Dynamics Simulations*: <https://doi.org/10.1021/acs.jctc.5b00201>
- Döntgen et al., *Automated Chemical Kinetic Modeling via Hybrid Reactive Molecular Dynamics and Quantum Chemistry Simulations*: <https://doi.org/10.1021/acs.jcim.8b00078>
- ChemXDyn preprint: <https://arxiv.org/abs/2601.08385>

### Reaction discovery / TS / workflow

- AutoMeKin official repository and tutorial: <https://github.com/emartineznunez/AutoMeKin>, <https://emartineznunez.github.io/AutoMeKin/docs/tutorial.html>
- KinBot official repository and kinetics paper: <https://github.com/zadorlab/KinBot>, <https://doi.org/10.1021/acs.jpca.2c06558>
- ChemDyME official repository and paper: <https://github.com/RobinShannon/ChemDyME>, <https://doi.org/10.1021/acs.jctc.1c00335>
- SCINE Chemoton, Heron and KiNetX: <https://scine.ethz.ch/download/chemoton>, <https://pmc.ncbi.nlm.nih.gov/articles/PMC11492315/>, <https://scine.ethz.ch/download/kinetx>
- autodE official repository/docs: <https://github.com/duartegroup/autodE>, <https://duartegroup.github.io/autodE/>
- pysisyphus official docs: <https://pysisyphus.readthedocs.io/en/latest/index.html>
- ARC official docs: <https://reactionmechanismgenerator.github.io/ARC/index.html>
- T3 official docs: <https://reactionmechanismgenerator.github.io/T3/how_to/>
- QCArchive official docs: <https://docs.qcarchive.molssi.org/overview/index.html>
- DeePMD-kit ASE calculator and model deviation: <https://docs.deepmodeling.com/projects/deepmd/en/stable/third-party/ase.html>, <https://docs.deepmodeling.com/projects/deepmd/en/latest/test/model-deviation.html>

### RRKM/master equation 与 network simulation

- Arkane official documentation: <https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/introduction.html>
- Arkane pressure-dependence input: <https://reactionmechanismgenerator.github.io/RMG-Py/users/arkane/input.html#pressure-dependent-rate-calculation>
- RMG-Py RRKM API: <https://reactionmechanismgenerator.github.io/RMG-Py/reference/pdep/reaction.html>
- RMG master-equation collision theory: <https://reactionmechanismgenerator.github.io/RMG-Py/theory/pdep/master_equation.html>
- MESS official repository: <https://github.com/Auto-Mech/MESS>
- Cantera official docs: <https://www.cantera.org/stable/userguide/index.html>
- ReactionMechanismSimulator.jl official docs: <https://reactionmechanismgenerator.github.io/ReactionMechanismSimulator.jl/latest/>
- CatMAP official docs: <https://catmap.readthedocs.io/en/latest/tutorials/index.html>
- Zacros official site: <https://www.zacros.org/software/obtain-zacros>
