# 明确反应序列的路径验证

路径验证只回答一个问题：用户给出的 Reaction Type 序列，是否在轨迹证据中形成过
一条完整、可审计的事件链。系统不会自动发现、补全、评分或排名反应路径。

## 输入与结论

输入为按预期发生顺序排列的 2–8 个完整 Reaction Type，每行或每个参数一个，例如：

```text
A + B -> C
C -> D
D + E -> F
```

反应两侧会按 RNG 的规范化规则排序，但化学计量重复项会保留。验证只匹配这一条
完整序列，不会用同名物种替换步骤，也不会返回其他“相似路径”。结论固定为：

- `supported`（有证据）：至少观察到一次满足全部约束的完整事件链；
- `not_observed`（未观察到）：完整遍历后没有观察到该事件链；
- `inconclusive`（证据不足）：遍历达到显式上限，无法作出否定结论。

索引缺失、过期或没有分子—原子关联时，验证不能运行，界面会要求先准备证据，
不会降级成物种名称拼接。

## 严格证据约束

每个节点是一个带稳定 `event_id` 的具体 RNG 事件。相邻事件只有同时满足以下条件
才会连接：

1. 后一事件的区间严格晚于前一事件；
2. 前一事件的一个产物分子实例与后一事件的一个反应物分子实例具有相同的精确
   SMILES 和完整原子 ID 集合；
3. 后一事件是该分子实例的第一次后续消费；
4. 至少一个原子 ID 贯穿整条路径的所有相邻连接。

同一区间内不会人为排列事件；无法唯一解析的分子实例连接会被保守排除。路径可以
跨越没有反应的帧，但不会跳过同一分子已经参与的中间事件。

## 证据来源

每个重复实验必须提供以下来源之一，并预先建立事件索引：

- 同时包含 Reaction Evidence 与 Molecular Evidence 的 schema-1/2 `.timeline.h5`；
- `.reactionevent.csv` 与 `.molecules.csv` 兼容文件对。

使用 `reacnet-scope prepare build event <公共前缀>` 建立索引。原子 ID 只在单个重复
内部有效；跨重复支持始终以 `(replicate, atom_id)` 计数。

报告保留具体事件、分子实例连接、连续原子、事件间隔、跨重复支持率，以及是否因
明细上限只保留了部分可下钻记录。明细上限不改变完整统计。

## Dash

侧栏“事件证据 → 路径验证”采用四步向导：

1. 确认当前数据集及可选的附加重复；
2. 每行输入一个完整 Reaction Type；
3. 检查序列和可选时间限制后运行；
4. 查看三态结论，并下钻具体事件、分子实例边和连续原子 ID。

页面没有候选路径搜索、Top-N 排名或合并网络。JSON 和 CSV 导出只包含本次明确序列
的验证结果。

## CLI

```bash
export REACNET_SCOPE_CACHE_DIR=/data/reacnet-cache
uv run reacnet-scope prepare build event /data/case/rep1/run.lammpstrj

uv run reacnet-scope verify-path \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --reaction 'A + B -> C' \
  --reaction 'C -> D' \
  --reaction 'D + E -> F' \
  --out-json path-verification.json
```

`--source` 可重复传入以统计跨重复支持；`--reaction` 必须按顺序重复 2–8 次。
`--max-interval-gap`、`--max-timestep-gap` 和 `--max-expansions` 是显式的保守边界。
