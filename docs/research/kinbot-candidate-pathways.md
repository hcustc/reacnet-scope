# KinBot 与 DeePMD 实采样候选反应路径功能的关联调研

调研日期：2026-08-28

KinBot 源码快照：[`95082e37b6d66a1a4018f093bd9a0f28886b7d0f`](https://github.com/zadorlab/KinBot/commit/95082e37b6d66a1a4018f093bd9a0f28886b7d0f)

## 结论先行

**有联系，但不是同一个问题，也不应让 KinBot 取代现有路径发现器。**

- KinBot 从一个反应物结构出发，按反应家族主动生成候选反应，调用量化化学或机器学习势搜索过渡态、做 IRC、识别产物，并递归扩展多势阱势能面；它回答的是“理论上可能有哪些经过驻点验证的气相反应通道”。官方项目也把自身定义为气相物种的自动反应/动力学工具。[README](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/README.md#L3-L15)；[KinBot 论文](https://doi.org/10.1016/j.cpc.2019.106947)
- 拟开发功能从 ReacNetGenerator 已提取的具体 MD 事件出发，要求严格时间顺序、分子实例和原子连续性，再统计实际出现次数并排序；它回答的是“DeePMD 轨迹实际采样支持了哪些候选事件链”。这是**观测证据挖掘**，不是势能面主动搜索。
- 当前工作树已经实现了大部分核心链路：[`event_paths.py`](../../reacnet_scope/event_paths.py#L1-L8) 只连接严格更晚、共享精确分子实例且保持非空原子谱系的具体 RNG 事件；[`candidate_paths.py`](../../reacnet_scope/candidate_paths.py#L21-L40) 已按频次、结构、时间、连续性和可选能量五类指标排名，并接受一个或多个精确起始物种。2026-08-28 本地运行 `uv run pytest tests/test_candidate_paths.py tests/test_event_paths.py`，结果为 **22 passed**。但它尚有两个发布前必须修正的科学语义缺口：谱系没有锚定到输入起始物种的具体反应物实例；聚合后也没有保留实际承载链的 Molecule Instance / Species，而由排名器事后猜测中间体。详见下文“当前原型的优先修正项”。
- 因此，KinBot 对本功能最合适的定位是：**设计参照 + 可选的离线能量/过渡态增益器**。其最高能垒最小化、能垒阈值、TS/IRC 验证和分层能量计算值得借鉴；其物种 ID、无向图、简单路径枚举和量化化学作业编排不应进入当前事件路径核心。
- 最稳妥的集成方式是保留现有 `discover_event_paths → rank_candidate_paths` 接口，在其 `EnergyEvidence` seam 后增加可选的 KinBot/DFT 结果导入器。KinBot 结果只能增强“能量合理性/驻点验证”维度，不能把没有 RNG 事件证据的边升级为“DeePMD 实际采样路径”。

## 调研范围与证据规则

KinBot 部分只使用项目官方仓库、作者官方 workshop 和两篇项目论文。所有源码结论都固定到上述 commit，避免 `master` 后续变化造成引用漂移。ReacNet Scope 部分以 2026-08-28 当前未提交工作树为准；因此这些本地链接描述的是正在开发的实际 seam，而不是已经发布的版本。

KinBot 仓库自身同时出现 README 中的“2.2.1”和 `pyproject.toml` 中的“2.3.0”；本文不依赖 README 的旧版本文本，源码行为均以固定 commit 为准。[README 版本文本](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/README.md#L9-L12)；[`pyproject.toml`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/pyproject.toml#L11-L17)

## KinBot 实际做什么

### 1. 从结构主动生成反应候选，而不是读取 MD 事件

标准 PES 入口从输入中的 SMILES 或笛卡尔结构创建一个初始 `StationaryPoint`，表征后把它写入待探索的 `chemids` 列表。[`pes.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L82-L102)

`ReactionFinder` 在每个势阱上按已实现的反应家族和结构 motif 枚举候选；源码明确把它定义为“find all the potential reactions starting from a well”，并根据 `families` / `skip_families` 调用相应搜索函数。[`reaction_finder.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_finder.py#L57-L90)；[反应家族调度](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_finder.py#L92-L169) 作者 workshop 也说明，候选来自分子模式识别和类似 RMG 的反应家族，再通过约束优化生成 TS 初猜。[官方 workshop](https://hackmd.io/@jzador/kinbot_workshop_2023)

这与 ReacNet Scope 的证据边界相反：当前 [`event_paths.py`](../../reacnet_scope/event_paths.py#L1-L8) 明确只读取已准备的事件索引，不从坐标重新检测反应。

### 2. 用 TS、能垒和 IRC 验证通道

KinBot 的 `ReactionGenerator` 依次进行 TS 搜索、能垒检查、IRC、产物优化、频率/高层级检查；成功和失败由状态机管理。[`reaction_generator.py` 状态机](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L41-L62) 在 L1 上，超过 `barrier_threshold` 的候选会被删除；通过阈值后才运行 IRC，并从 IRC 末端构造产物。[`reaction_generator.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L220-L280) 高层级 L2 能垒还会再次过滤。[`reaction_generator.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L565-L577)

这里的“frequency”主要是驻点振动频率/负频检查，不是 MD 事件发生频次。KinBot 的单步摘要保存成功状态、能垒、反应名和产物标识，也没有事件发生次数或时间戳序列。[`postprocess.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/postprocess.py#L75-L85)；[摘要写出](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/postprocess.py#L102-L168)

### 3. 只递归扩展新的单分子产物势阱

IRC 产物按 `chemid` 去重；同一势阱中和跨反应得到的相同产物会复用对象。[`reaction_generator.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L274-L297)；[产物优化复用](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L488-L515) `chemid` 是由原子递归 ID 的求和再附加多重度得到的精确拓扑式标识，不是结构相似度分数。[`stationary_pt.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/stationary_pt.py#L649-L676)

全 PES 模式只对“恰好一个产物”的新势阱继续启动 KinBot；同时把新势阱相对能量从后续能垒预算中扣除，使整个搜索维持相对于初始势阱的能量上限。[`reaction_generator.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L578-L604) 多分子产物会进入 PES 图，但不作为普通中间势阱继续递归；路径函数也明确要求中间节点必须是 well，而不能是 bimolecular product。[`pes.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L834-L862)

### 4. 后处理图和“最低路径”是势能面语义

KinBot 把每条成功反应整理成 `[reactant, reaction_name, products, barrier]`。若相同端点有多条通道，后处理主要保留能垒较低者，而不是保留每次 occurrence 的统计分布。[`pes.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L308-L313)；[同端点折叠](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L407-L449)

它随后构造**对称的无向邻接矩阵**，两个方向写入同一连接和同一能垒。[`pes.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L812-L831) 两个指定端点之间使用 `networkx.all_simple_paths`，固定 cutoff 为 5；`lowestpath` 并非总能量最小，而是选择“路径中最高 TS 能量最小”的 minimax 路径。[简单路径枚举](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L834-L862)；[minimax 选择](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L651-L668)

这套路径定义适合静态多势阱 PES，却不能表达 MD 中的反应方向、事件重复次数、先后时间、同一分子实例的首次消费和跨事件原子连续性。

## 与当前 ReacNet Scope 实际 seam 的逐项比较

| 目标能力 | 当前工作树 | KinBot | 判断 |
| --- | --- | --- | --- |
| 一个或多个起始物种 | `discover_event_paths` 接受多个精确 Species（OR 语义），并返回设定长度区间内的路径。[实现](../../reacnet_scope/event_paths.py#L1174-L1215) 但当前只检查首事件反应物侧是否出现该 Species，未把谱系锚定到其具体 Molecule Instance。[起点过滤](../../reacnet_scope/event_paths.py#L570-L580) | 标准 PES 从一个初始 well 启动；另有专门的双分子入口，但不是多 seed 的事件检索。[`kb.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/kb.py#L206-L232) | 方向正确，但本地原型须先修复起点锚定。 |
| 后续事件检索 | 读取具体 RNG occurrence，按严格时间顺序建边。 | 主动生成并计算理论候选，没有 RNG/MD event ingestion。 | 无可直接复用部分。 |
| 中间体连续衔接 | 同一精确 Species + 完整 atom-ID 集合的产物实例只连接到第一次无歧义后续消费；同区间不发明顺序。[建边规则](../../reacnet_scope/event_paths.py#L284-L370) 具体 occurrence 的边保存了实际承载实例，但 Reaction Type 聚合签名没有保存该承载 Species 链。[聚合结构](../../reacnet_scope/event_paths.py#L128-L165) | 以 `chemid` 合并相同驻点，图上相邻即视为可连接。 | KinBot 的 well 概念可作术语参照；当前聚合结果需保留实际承载链。 |
| 全路径原子连续性 | DFS 每扩展一步都取 carrier atom-ID 交集，空集即停止。[遍历](../../reacnet_scope/event_paths.py#L531-L568) 但首条边把任意 carrier 直接作为初始谱系，尚未要求其来自被查询起始物种的反应物实例。 | 无轨迹 atom-ID 或事件 lineage。 | 事件间连续性是正确底座；起点锚定仍是必要不变量。 |
| 事件频次 | 聚合 occurrence count、独立原子谱系、重复实验支持率；评分还结合 reaction throughput。[聚合](../../reacnet_scope/event_paths.py#L604-L635)；[评分](../../reacnet_scope/candidate_paths.py#L333-L347) | 同端点通道主要按低能垒折叠；没有 MD occurrence count。 | 不应采用 KinBot 的去重方式，否则会丢掉频次证据。 |
| 结构相似性 | 相似度只用于排序、不用于建边，这一分层是对的；但当前聚合丢失 carrier 后，排名器会从相邻 Reaction Type 的共有 Species 或最高相似产物中重新选择 focal continuation。[实现](../../reacnet_scope/candidate_paths.py#L158-L173) | 反应家族用 motif 识别候选，产物用精确 `chemid` 判同，没有结构相似度排名。 | 应对真实 carrier Species 链评分，不能让相似度重新决定中间体身份。 |
| 时间关联 | 保存 interval、idle timestep、anchor timestep gap 和 span，并据中位间隔评分。[事件边](../../reacnet_scope/event_paths.py#L372-L397)；[时间评分](../../reacnet_scope/candidate_paths.py#L338-L346) | 静态 PES 图没有 MD 时间轴；作业日志时间也不是化学事件时间。 | KinBot 无直接帮助。 |
| 能量信息 | `EnergyEvidence` 显式保存归一化分数、ΔE、barrier、unit、source；CSV 要求用户先统一量纲和定义。[接口](../../reacnet_scope/candidate_paths.py#L32-L44)；[CSV 契约](../../reacnet_scope/candidate_paths.py#L199-L229) | 强项是驻点能量、ZPE、TS 能垒、L1/L2/L3 筛选与 master equation 输入。 | 最适合做可选离线 enrichment。 |
| 路径排序 | 五类默认权重为频次 0.35、结构 0.15、时间 0.20、连续性 0.20、能量 0.10；缺少能量时不把它混入分母。[权重](../../reacnet_scope/candidate_paths.py#L21-L29)；[合成](../../reacnet_scope/candidate_paths.py#L333-L367) | `lowestpath` 是最高 TS 能量的 minimax；另有按 Boltzmann 因子过滤分支的后处理。 | 可增加可选“瓶颈能垒”排序/筛选，但不应替代多证据总分。 |
| DeePMD 原生支持 | 当前只预留通用、显式的能量 CSV seam；尚没有 DeePMD 能量提取器。 | 当前固定 commit 文档化的 MLIP 后端是 FAIRChem/UMA；QC dispatch 只列 Gaussian、Q-Chem、NWChem、ORCA、`nn_pes` 和 `fc`，未知值报错，没有 DeepMD backend。[README](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/README.md#L51-L63)；[`qc.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/qc.py#L369-L397) | 两边都需要单独设计适配；引入 KinBot 不会自动解决。但 DeePMD-kit 官方提供 ASE `DP` calculator，因此做 KinBot/Sella 风格扩展在技术上可行，只是不是即插即用。[DeePMD-kit ASE 文档](https://docs.deepmodeling.com/projects/deepmd/en/latest/third-party/ase.html) |
| 结论边界 | 输出明确写为 concrete RNG events 支持的 candidate route，不声称因果、唯一性、TS 或完整机理。[结果语义](../../reacnet_scope/candidate_paths.py#L405-L412) | 成功通道经过 TS/IRC/能量层面的理论表征。 | 两种证据应并列展示，不能互相冒充。 |

## 当前原型的优先修正项

前两项不是 KinBot 能解决的问题，而是“由输入物种出发且由实际采样支持”这一产品声明成立的前提。现有 22 个针对性测试通过，说明已实现行为稳定；它们并不消除以下尚未覆盖的不变量。第三项则决定该方案能否在目标数据规模上可靠运行和复核。

### 1. 把原子谱系锚定到输入物种的具体反应物实例

当前起点过滤只要求首事件反应物侧包含输入 Species，随后 DFS 在第一条事件边上把任意 carrier atom 集合作为初始 lineage。[起点与首边初始化](../../reacnet_scope/event_paths.py#L531-L580) 对 `A + X -> B + C`，查询 `A` 时，如果后续真正被消费和连续追踪的是来自 `X` 的 `C`，这条链仍可能被报告为“从 A 出发”。这证明的是 A 参与了首事件，而不是后续谱系源自 A。

应把每个命中的起始 Species 解析到首事件反应物侧的具体 Molecule Instance，以该实例的 atom IDs 初始化 lineage；第一条及后续 carrier 都必须与该集合保持非空交集。多个起始物种保持 OR 查询，但每个 occurrence 必须记录实际命中的 `anchor_species` 和 `anchor_molecule_instance`。

### 2. 聚合时保留实际承载的中间体链

具体 occurrence 的每条边已经保存 `molecule_instances` 和 `carrier_atom_ids`，[`_EventEdge`](../../reacnet_scope/event_paths.py#L117-L125) 但 `_SignatureAggregate` 只按 Reaction Type 序列聚合，没有保存 carried Species 序列。[`_SignatureAggregate`](../../reacnet_scope/event_paths.py#L128-L165) `candidate_paths.py` 随后从相邻反应的共有 Species 中选择中间体；存在多个共有 Species 时又按结构相似度猜测。[`_choose_focal_output`](../../reacnet_scope/candidate_paths.py#L158-L173) 因而展示的中间体链和结构分数可能不属于真实承载该 occurrence 的实例。

候选签名至少应由 `anchor Species + Reaction Type 序列 + carried Species 序列` 区分；每个 occurrence 继续保留具体 Molecule Instance 和 atom IDs。结构相似度只对已由证据确定的 carried Species 打分，不能用于选择哪一个物种承担路径连续性。上述两项应先写入 [`ADR 0012`](../adr/0012-discover-only-sampled-candidate-paths.md) 的规范性不变量，并增加多反应物、多产物、多共享中间体的反例测试。

### 3. 大规模查询还需要索引化和可审计下钻

当前实现有路径长度与 expansion 上限，但查询仍会读取全部事件并在内存重建事件图；同步 UI 对真正的大型索引可能成为瓶颈。[事件读取与建图](../../reacnet_scope/event_paths.py#L211-L418) 下一步应优先复用已物化的 Event Evidence Index，以起始 Species/实例过滤邻接，再做有界 best-first 或 top-k 遍历。排名结果还应保留可下钻的 example occurrence/event IDs，以及事件索引、`.reactionabcd`、能量文件和算法版本的来源签名，否则“实际采样支持”难以复核。

## 可以借鉴什么

### 1. 最高能垒最小化，但应作为独立、可解释的 energy ranker

KinBot 的 minimax 规则非常适合表达“整条路径由最困难的一步控制”的 PES 直觉。当前候选路径能量分数是各步归一化 energy score 的覆盖率加权平均；可以额外发布：

```text
path_bottleneck_barrier = max(step_barrier)
barrier_coverage = 有可比 barrier 的步骤数 / 总步骤数
```

仅当所有 barrier 具有同一物理定义、单位、计算层级和基准时，才允许用 `path_bottleneck_barrier` 排序。部分覆盖时应显示 NA 或显式 lower-confidence，不应把缺值当成低能垒。KinBot 源码本身也是在统一的相对能量基准上比较路径最高 TS。[`pes.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/pes.py#L651-L668)

### 2. 保持绝对能量预算，而不是逐步局部阈值

KinBot 扩展到更高能中间体时，会从下一层阈值中扣除中间体相对能量。这避免每一步都“局部看起来可行”，组合后却越过从初态计算的总能量上限。[`reaction_generator.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/reaction_generator.py#L578-L592)

如果后续导入的是可比较的驻点/TS 能量，可为查询增加 `maximum_bottleneck_barrier`。如果只有 DeePMD 轨迹瞬时总能或局部能代理，则不能直接套用该阈值，因为它们不是 TS barrier；必须把字段命名为 `observed_energy_proxy` 并保留定义和来源。

### 3. 把“采样支持”和“驻点验证”做成两个证据层级

建议保留当前主结论：

1. `sampled`：有严格 RNG 事件链、时间顺序和原子连续性；
2. `energy_enriched`：上述路径的部分或全部步骤附有同定义能量；
3. `ts_irc_validated`：代表性转化又经 TS + IRC 验证；
4. `kinetics_enriched`：进一步有速率/master-equation 结果。

KinBot 适合产生第 3–4 层，但第 1 层始终只能来自 ReacNetGenerator/DeePMD 实际事件证据。没有被 MD 观察到的 KinBot 通道可以单独列为“理论补充候选”，不能混入 `sampled` 排名。

### 4. 针对高排名步骤做离线、定向验证

KinBot 支持按 `families` 限制搜索，也有显式 `break_bonds` / `form_bonds` 和 specific-reaction 参数，适合避免对整个网络重新做盲搜索。[`parameters.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/parameters.py#L60-L106)

可选离线流程应是：

```text
RNG 原子连续事件链
  -> 当前多指标排名
  -> 选 Top-N 步骤并导出代表性反应物/产物结构与键变化
  -> 用户在独立工作目录运行 KinBot/DFT TS + IRC
  -> 将 barrier、ΔE、方法、基组、构象、环境模型和来源导回 EnergyEvidence
  -> 重排并并列显示 sampled 与 ts_irc_validated 状态
```

这一路径在气相、小分子、单分子/明确双分子转化中最有价值。对凝聚相、显著周期环境、溶剂/表面参与或多体协同事件，KinBot 的气相 well 模型与“只递归单分子产物”边界会显著削弱适用性；此时更适合使用能够保留局部环境的专门 NEB/TS 工作流。

### 5. DeePMD 计算后端是可行扩展，不是现成功能

DeePMD-kit 官方把 `deepmd.calculator.DP` 暴露为 ASE calculator，可返回势能和力。[官方 ASE 集成文档](https://docs.deepmodeling.com/projects/deepmd/en/latest/third-party/ase.html) KinBot 又已经使用 ASE/Sella 组织优化和 TS 作业，所以为 KinBot 新增 `qc = deepmd` 一类后端在工程上是**可行推论**。但固定 commit 的 QC 分派是显式枚举，仓库没有 DeepMD backend；要完成适配仍需新增模板、参数、结果持久化、频率/Hessian 或其替代方案，以及针对 TS、IRC、周期边界和模型适用域的验证。[`qc.py`](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/qc.py#L338-L349)；[后端拒绝分支](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/kinbot/qc.py#L369-L397)

即使完成该后端，它也只会让 KinBot 用同一个 Deep Potential 计算驻点/路径能量，不会自动获得 RNG occurrence、时间顺序或 atom-lineage；因此仍应放在离线 enrichment 层。

## 不应直接复用什么

1. **不以 KinBot `chemid` 替换 RNG Species / Molecule Instance 身份。** 当前链路需要 replicate-scoped atom IDs 和精确分子实例；`chemid` 只服务 KinBot 自己的驻点去重，不能提供轨迹连续性。
2. **不把事件网络转为无向图。** RNG 路径的方向由具体 occurrence 的反应物/产物侧和严格时间顺序确定；KinBot 的对称连接会丢掉这一证据。
3. **不照搬 `all_simple_paths(cutoff=5)`。** 当前实现按时间有向事件 DAG、长度/间隔/扩展上限遍历，更适合大规模事件数据；KinBot 的枚举只面向后处理后的较小 PES。
4. **不按“同端点只保留最低能垒”折叠事件。** 对本产品而言，不同 occurrence、独立 atom lineage、replicate reproduction 和时间间隔正是排名证据；只能在展示层聚合，原始支持计数必须保留。
5. **不把 KinBot 的振动频率当成事件频次。** 两者只是在中文里都可能叫“频次/频率”，物理含义完全不同。
6. **不将 KinBot 设为核心运行时依赖。** 固定 commit 要求 Python ≥3.11、ASE ≥3.26，并依赖 NetworkX、RMSD、Sella；本项目当前最低 Python 是 3.10。直接依赖会无谓抬高运行门槛并把量化化学作业系统带入在线查询进程。[KinBot 依赖](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/pyproject.toml#L11-L47)

## 建议落点

### 近期：保持现有路径核心，先修证据不变量，再收紧能量契约

当前实际 seam 已与目标功能高度一致：

- `event_paths.py` 负责“什么路径真的被采样”，不看能量和结构相似度；
- `candidate_paths.py` 负责“如何解释性排名”，不负责发明边；
- `analysis_services.py` / CLI 负责数据源和可选 energy CSV 接入。[服务编排](../../reacnet_scope/analysis_services.py#L355-L430)

在接入任何 KinBot 结果之前，应先完成上文的起点实例锚定和 carrier 链保真；否则更精细的能垒只会给一条身份可能错误的 Species 链增加虚假的精确感。

建议为 `EnergyEvidence` 增加或在导入校验中强制保存：

- `source_kind`: `deepmd_event_proxy`、`kinbot_ts`、`dft_ts`、`user_normalized`；
- `scope`: `reaction_type`、`representative_occurrence`、`event_occurrence`；
- `method/model`, `reference_state`, `temperature`, `environment`, `conformer_id`；
- `quantity_kind`: `delta_energy`、`activation_barrier`、`frame_energy_proxy`；
- `comparability_group`，只有同组值才可做路径 bottleneck 比较。

尤其要避免把 DeePMD 每帧系统总能、局部原子能之和和 KinBot 的 ZPE 修正驻点/TS 相对能量自动归一到同一列。当前 CSV 要求用户显式提供归一化 score 的设计是正确的保守边界。[`candidate_paths.py`](../../reacnet_scope/candidate_paths.py#L199-L229)

### 中期：增加可选的 KinBot 结果导入，不先增加 KinBot 执行依赖

优先实现一个只读 importer，而不是在 Dash 请求中启动 KinBot：

- 输入 KinBot `summary_<chemid>.out`、PES/MESS 结果和一个显式的 RNG Reaction Type 映射表；
- 校验反应两侧、方向、构象/电荷/多重度、能量单位和理论层级；
- 映射成功后生成 `EnergyEvidence`；映射不唯一时拒绝自动合并；
- 保留 KinBot commit、输入文件摘要和原始结果路径以便审计。

只有在 importer 和真实数据验证稳定后，再考虑“导出代表性 RNG 事件包 → 用户离线运行 KinBot”的助手命令。

### 远期：按体系决定是否值得做 TS/IRC 自动化

- **值得优先做**：气相燃烧/热解、小到中等有机物、候选路径数已经被 RNG 证据大幅压缩、用户确实需要从“采样候选”走向“驻点验证”。
- **价值有限**：目标只是从大规模 MD 中找高频、可重复、时间连续的实际事件链；当前实现已经直接解决，KinBot 会增加大量与核心问题无关的计算和依赖。
- **不宜直接做**：反应依赖周期凝聚相、表面、溶剂或复杂多体环境，却把事件截成孤立气相分子送入 KinBot；这可能改变反应物、产物和势垒的物理语义。

## 最终判断

KinBot 与本功能的联系主要位于**候选路径之后**：

```text
ReacNetGenerator / DeePMD 事件证据
  └─ event_paths：严格时间 + 起点实例锚定 + 精确 carrier + 原子连续性
      └─ 当前 candidate_paths：频次 + 结构 + 时间 + 连续性 + 可选能量
          └─ 可选 KinBot：气相驻点、TS、IRC、能垒和动力学增益
```

所以建议是：**先补齐起点锚定与真实 carrier 链，再不引入 KinBot 来生成或拼接路径；只借鉴其 bottleneck-energy 思路，并把它作为 Top-N 路径的可选离线 TS/IRC 验证器。** 这样才能保住“由 DeePMD 实际采样所支持”的核心证据声明，并在需要时增加比瞬时 MD 能量更接近反应势垒的理论证据。

## 主要来源

- Van de Vijver, R.; Zádor, J. *KinBot: Automated stationary point search on potential energy surfaces*. Computer Physics Communications 248, 106947 (2020). [DOI](https://doi.org/10.1016/j.cpc.2019.106947)
- Zádor, J. et al. *Automated reaction kinetics of gas-phase organic species over multiwell potential energy surfaces*. J. Phys. Chem. A 127, 565–588 (2023). [DOI](https://doi.org/10.1021/acs.jpca.2c06558)
- KinBot official repository, pinned source snapshot [`95082e3`](https://github.com/zadorlab/KinBot/tree/95082e37b6d66a1a4018f093bd9a0f28886b7d0f).
- Zádor, J.; Martí, C. [KinBot Workshop, ACS Fall Meeting 2023](https://hackmd.io/@jzador/kinbot_workshop_2023).
- DeePMD-kit official documentation, [Use deep potential with ASE](https://docs.deepmodeling.com/projects/deepmd/en/latest/third-party/ase.html).

## 许可证边界

KinBot 使用 BSD 3-Clause，允许修改和再分发，但复制源码时必须保留版权、条件和免责声明，且不得用权利人/贡献者姓名作背书。[LICENSE](https://github.com/zadorlab/KinBot/blob/95082e37b6d66a1a4018f093bd9a0f28886b7d0f/LICENSE#L1-L29) 本调研建议优先独立实现小型评分规则和结果 importer；若以后直接复制 KinBot 代码，须同时完成许可证归属记录。
