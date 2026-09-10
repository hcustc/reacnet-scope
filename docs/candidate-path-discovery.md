# 有界候选路径发现

“候选路径发现”接收一个或多个精确 RNG SMILES。系统在当前数据集观测到的有向 Reaction Type 网络上执行确定性的有界局部展开：

- 第一个 Reaction Type 的反应物侧包含任一起始 Species；
- 每一步只能使用数据集中实际记录方向的 Reaction Type；
- 相邻步骤通过一个精确 Carried Species 连接；
- 普通候选不会重复已经访问过的 Carried Species；
- `max_expansions` 在每次非终止状态展开前生效；
- `max_frontier_states`（默认 5000）限制队列峰值，`max_generated_states`（默认 10000）限制累计入队状态数（包括起点）。

单次展开以流式 Top-K 选择子分支，只保留剩余预算加一个截断见证，不完整物化所有 children。结果的 `evidence_summary` 报告 `generated_states`、`peak_frontier_states`、`child_candidates_examined`、`budgets` 和 `truncation_reasons`，不能将预算耗尽后的空结果当作不存在路径。当前兼容实现仍会遍历相邻反应/产物引用来选择 Top-K；这些限制不保证邻接读取量或真实大数据耗时达标，也不代替 #23/#24 的生产索引与搜索。

候选结构来自 `.reactionabcd` 的有向反应网络；系统随后只对候选涉及的少量 Reaction Types 查询事件索引中的 Step Evidence。候选发现不会读取全部 Reaction Occurrences、构建全局事件图或声称存在一条贯穿整条候选的原子连续轨迹。

## 排名指标

评分版本 `candidate-path/network-v1` 默认使用：

| 指标 | 权重 | 含义 |
|---|---:|---|
| frequency | 0.35 | 路径最小步骤事件数与各 Reaction Type 的对数频次 |
| structure | 0.15 | 相邻焦点 Species 的 Morgan-Tanimoto 相似度；无法解析时退回元素组成相似度 |
| energy | 0.10 | 可选、由用户提供并归一化到 `[0,1]` 的能量评分 |

排序只读取并校验每条候选声明的 anchor/carried chain，不因请求中存在其他起点而重选结构或复用不匹配的签名。缺少或不符合反应两侧的显式链路会报参数错误；只有旧 Event Path 报告通过独立命名的兼容分支保留历史推断。

时间分和连续性分不属于候选发现评分；它们只能来自候选选定后的 Continuous MD Support。没有能量文件时，energy 项被明确标记为 `not_provided`，其权重按其余可用指标重新归一化。部分步骤有能量时，路径能量分乘以能量覆盖率。

自定义权重必须在当前可用指标上具有有限、正的权重和；全部权重落在不可用指标时返回 `ValueError`，不除零，也不静默恢复默认权重。

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
  --max-frontier-states 5000 \
  --max-generated-states 10000 \
  --energy-csv /data/case/reaction-energy.csv \
  --out-json candidate-paths.json
```

缺少事件索引时，分析会失败并提示准备索引，因为每一步必须有当前数据集的 Reaction Evidence。候选发现本身不要求先构建完整 Event Path。

## 解释边界

Candidate Path 说明每一步的有向 Reaction Type 都有当前数据集的独立证据，并适合进一步执行 Continuous MD Support、检查局部轨迹、导出几何或开展量化计算。不同步骤的证据不必来自同一 Replicate、Molecule Instance 或原子谱系。它不证明事件顺序、因果关系、机理唯一性、过渡态、能垒或完整反应机制；达到展开或候选收集上限时，结果明确标记为有界子集。
