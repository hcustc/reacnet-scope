# 有界候选路径发现

“反应与事件 → 候选路径”、`candidate-search` CLI 与
`reacnet_scope.search_candidate_paths` 共用事件索引的已发布局部邻接。
仅起点沿观测方向逐步查后续，仅终点在观测方向上逐步查候选前驱；
双端查询默认不要求最大步数，用首步分支轮流展示已检查路线。
单端结果可翻到上一页或下一页，选择一条后可继续展开一步；
浏览记录不等于已验证的完整 MD 路线。
这些展示规则不计算化学重要性评分。见
[ADR-0026](adr/0026-search-candidates-by-anchor-mode.md)。

当前工作台结果为 `reacnet-scope/indexed-candidates/v5`（与下面兼容命令的 v3 不同）。
每条路线还提供版本化 `candidate_signature`；旧 `signature_id` 保留供 v3 交接使用。
身份字段及证据修订边界见 [Candidate 结构身份](candidate-identity.md)。
Dash 默认按净转化方向展开：完整正逆反应按精确 Species 及计量配对，正向次数减去逆向次数，只保留净值为正的方向；例如 111−110 显示“净 1 次”。统计范围为当前 RNG 数据全部观测区间，不代表物种在全网络中的总净生成量。步骤详情和导出同时保留正向、逆向、净数及原始支持事件。净值不是从原始事件中挑出的某一个事件。

可切换“全部观测方向”查看两个方向。净转化视图不作短时返回折叠；Python/CLI 为兼容保留 observed + persistent 默认，用 `--direction-view net` 或 `direction_view="net"` 可得到与 Dash 默认相同的方向口径。见 [ADR-0031](adr/0031-show-net-reaction-directions-in-candidate-workbench.md)。

全部观测方向下可选折叠三个分析帧间隔内、完整原子参与者返回的短暂往返，也可调整窗口或只折叠
精确键级也恢复的事件。低频和短寿命本身不触发折叠；步骤页始终保留原始事件，并可选择具体事件
查看真实键图和首次后续消耗。见 [ADR-0017](adr/0017-qualify-candidate-events-and-check-selected-history.md)。

CLI 的 `--mode explore|reverse|target` 分别对应仅起点、仅终点、双端。
`--max-steps` 是双端查询的可选上限；单端每次展开一步，
可用 `--anchor-offset` 读取下一页转移。
`--quality-view raw|persistent`、`--return-window-frames`、`--return-basis topology|exact`、
`--max-expansions`、`--max-frontier`、`--max-prefixes` 和 `--max-candidates-examined`
限制在线工作，不是化学筛选。`--check-top N` 单独检查前 N 条路线的连续历史（最多 10 条），
Web 可检查所选路线。找到具体链、未找到、证据不足分别展示和导出；没有间隔内逐帧键状态证明时
不会宣称连续。旧候选索引需通过 `reacnet-scope prepare rebuild event <source>` 显式重建。

双端检索使用 `local_reachability_fair_routes_v1`：先按唯一物种读取有界局部邻接，
再在已读取的可达子图中按首步分支轮流枚举路线。`reachability_status`
表示是否已在当前证据视图中确认连接，`routes_complete` 表示备选路线是否查全，
`display_truncated` 表示是否有已检查路线因展示条数限制而未显示。
无路线且预算截断只能解释为未确定；找到路线也不能证明备选路线查全。
仅终点检索需要新 product-leading 索引，因此旧候选索引须显式重建。

Web 默认使用路径合并图浏览本次返回的路线：结构卡片按精确 RNG 物种身份合并，
同分子式的不同结构分别显示；同一完整有向 Reaction Type 共用一个反应菱形，
全部反应物和产物作为独立物种节点连接该菱形。当前路线的 Carried Species 连线高亮，
其他参与物的连线保留为反应上下文；计量重复在连线上标明倍数。
同一反应的不同 Carried Species 仍是不同候选路线，图上的合并不改变路线身份或步骤证据。
点击菱形或连线可定位路线中的步骤并查看证据。
点击物种可查看经过它的路线，通过侧栏路线按钮、下拉框或上一条／下一条切换，当前路线及步骤高亮。
“路线表格与多路线比较”保留勾选入口，可将图切换到只显示当前与勾选路线；逐步结构链也可展开。
菱形在净转化视图标注净计数，在全部观测视图标注 Reaction Type 原始事件数，不因多条路线经过而累加；每条主线的支持事件数在步骤证据中单独显示。图仅是已返回 Candidate 的展示投影，
不从合并后的连接重新组合路线，也不代表连续分子历史；搜索截断与连续历史检查仍单独报告。

A → B → C → A 是物种网络循环，不证明同一分子实际返回。净转化不保证网络无环，也不额外隐藏这类多步循环；普通候选枚举仍使用简单路径，单步浏览可回到先前物种。继续、翻页及返回沿用已提交查询口径，修改搜索条件后重新搜索才生效。

以下内容描述保留兼容的 `candidate-paths` 命令及其 `v3` 结果，不是新 Web 的执行边界。

“候选路径发现”接收一个或多个精确 RNG SMILES。系统在当前数据集观测到的有向 Reaction Type 网络上执行确定性的有界局部展开：

- 第一个 Reaction Type 的反应物侧包含任一起始 Species；
- 每一步只能使用数据集中实际记录方向的 Reaction Type；
- 相邻步骤通过一个精确 Carried Species 连接；
- 普通候选不会重复已经访问过的 Carried Species；
- `max_expansions` 在每次非终止状态展开前生效。

候选结构来自 `.reactionabcd` 的有向反应网络；系统随后只对候选涉及的少量 Reaction Types 查询事件索引中的 Step Evidence。候选发现不会读取全部 Reaction Occurrences、构建全局事件图或声称存在一条贯穿整条候选的原子连续轨迹。

## 排名指标

评分版本 `candidate-path/network-v1` 默认使用：

| 指标 | 权重 | 含义 |
|---|---:|---|
| frequency | 0.35 | 路径最小步骤事件数与各 Reaction Type 的对数频次 |
| structure | 0.15 | 相邻焦点 Species 的 Morgan-Tanimoto 相似度；无法解析时退回元素组成相似度 |
| energy | 0.10 | 可选、由用户提供并归一化到 `[0,1]` 的能量评分 |

时间分和连续性分不属于候选发现评分；它们只能来自候选选定后的 Continuous MD Support。没有能量文件时，energy 项被明确标记为 `not_provided`，其权重按其余可用指标重新归一化。部分步骤有能量时，路径能量分乘以能量覆盖率。

能量 CSV 必须包含 `reaction_key,score`，可选列为 `delta_energy,barrier,unit,source`。软件不自动把不同能量定义或不同模拟条件缩放到同一尺度，因此 `score` 必须由用户按同一分析口径预先归一化。

## CLI

```bash
uv run reacnet-scope candidate-paths \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --source rep2=/data/case/rep2/run.lammpstrj \
  --reac /data/case/rep1/run.lammpstrj.reactionabcd \
  --start 'CCO' \
  --start '[OH]' \
  --min-steps 2 \
  --max-steps 4 \
  --energy-csv /data/case/reaction-energy.csv \
  --out-json candidate-paths.json
```

缺少事件索引时，分析会失败并提示准备索引，因为每一步必须有当前数据集的 Reaction Evidence。候选发现本身不要求先构建完整 Event Path。

## 解释边界

Candidate Path 说明每一步的有向 Reaction Type 都有当前数据集的独立证据，并适合进一步执行 Continuous MD Support、检查局部轨迹、导出几何或开展量化计算。不同步骤的证据不必来自同一 Replicate、Molecule Instance 或原子谱系。它不证明事件顺序、因果关系、机理唯一性、过渡态、能垒或完整反应机制；达到展开或候选收集上限时，结果明确标记为有界子集。
