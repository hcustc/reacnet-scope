# 有界候选路径发现

Web 第一版使用“反应与事件 → 候选路径”、`candidate-search` CLI 与
`reacnet_scope.search_candidate_paths`。它们从事件索引的已发布邻接目录局部搜索，
支持目标约束，按步数优先展示，不使用下文旧综合评分。见
[ADR-0015](adr/0015-add-indexed-candidate-task-to-reaction-workspace.md)。

当前工作台结果为 `reacnet-scope/indexed-candidates/v3`（与下面兼容命令的 v3 不同）。
默认折叠三个分析帧间隔内、完整原子参与者返回的短暂往返。可切换原始视图、调整窗口，或只折叠
精确键级也恢复的事件。低频和短寿命本身不触发折叠；步骤页始终保留原始事件，并可选择具体事件
查看真实键图和首次后续消耗。见 [ADR-0017](adr/0017-qualify-candidate-events-and-check-selected-history.md)。

CLI 增加 `--quality-view raw|persistent`、`--return-window-frames`、`--return-basis topology|exact`、
`--max-expansions`、`--max-frontier`。`--check-top N` 单独检查前 N 条路线的连续历史（最多 10 条），
Web 可检查所选路线。找到具体链、未找到、证据不足分别展示和导出；没有间隔内逐帧键状态证明时
不会宣称连续。旧候选索引需通过 `reacnet-scope prepare rebuild event <source>` 显式重建。

目标检索使用 `local_graph_then_target_routes_v1`：先按唯一物种读取有界局部邻接，再枚举可达目标的路线。
汇合到同一物种的不同路径保留各自身份，但不重复消耗邻接展开预算。路线枚举的前缀预算为
`max_expansions * max_steps`，与邻接读取共用时间预算；结果和导出保留算法版本与截断原因。
此搜索修复复用已发布的 v3 索引，无需重建。

Web 默认使用路径合并图浏览本次返回的路线：结构卡片按精确 RNG 物种身份合并，
同分子式的不同结构分别显示；菱形按完整有向反应式和主线载体对合并。
共反应物、副产物或计量不同的步骤分别保留，点击菱形或连线可定位路线中的步骤并查看证据。
点击物种可查看经过它的路线，通过侧栏路线按钮、下拉框或上一条／下一条切换，当前路线及步骤高亮。
“路线表格与多路线比较”保留勾选入口，可将图切换到只显示当前与勾选路线；逐步结构链也可展开。
共享步骤的原始事件数不因多条路线经过而累加。图仅是已返回 Candidate 的展示投影，
不从合并后的连接重新组合路线，也不代表连续分子历史；搜索截断与连续历史检查仍单独报告。

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
