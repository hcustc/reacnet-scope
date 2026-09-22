# 候选检索预算敏感性：2026-09-21

结论：同一数据、同一起终点，仅增加 frontier 预算，就从截图中的 2 条三步路线变成 6 条三步路线。因此默认结果不仅未穷尽较长路线，也遗漏了其他同样短的路线。这个实验支持“结果覆盖强烈依赖执行预算”的判断，不能用于判定任何一条路线的化学可行性。

## 方法与来源

只读打开该运行的实际已发布 `events.sqlite3` 索引，使用当前 [CandidateReader 与 discover_indexed_candidates](../../reacnet_scope/path_search.py)。固定截图的精确起终点、`max_steps=6`、`max_paths=20`。基线时间预算 5 秒，其余为 30 秒；实测每次均不足 0.3 秒，没有触发 time_budget。数据库以 SQLite `mode=ro` 打开，查询前后文件大小与 mtime_ns 不变。未修改代码、原始 RNG 或索引。

本地生成的 `candidate-search-sensitivity-2026-09-21.json` 保存全部返回路径、精确身份、逐步事件数、预算、结果状态、软件版本及复现脚本，不随仓库分发。循环闭合列表从 JSON 中省略，保留总数与截断状态。初步实验在临时索引上执行后，本最终记录已对实际发布索引重新执行全部 12 组查询，结果数量、步数和截断原因一致。索引 meta 收录于本地 JSON，源工件一致性核对见父报告。

## 结果

E 为 max_expansions，F 为 max_frontier。所有行均 `truncated/inconclusive`，不能将零结果解释为无路线。

| 排除截图第一步 | E | F | 返回条数与步数 | 截断原因 |
|---|---:|---:|---|---|
| 否 | 2000 | 5000 | 2 条 3 步 | expansion_budget, frontier_budget |
| 否 | 5000 | 5000 | 2 条 3 步 | expansion_budget, frontier_budget |
| 否 | 2000 | 20000 | 6 条 3 步 | expansion_budget |
| 否 | 20000 | 5000 | 2 条 3 步，18 条 6 步 | frontier_budget, result_limit |
| 否 | 10000 | 10000 | 6 条 3 步，14 条 5 步 | expansion_budget, frontier_budget, result_limit |
| 否 | 20000 | 20000 | 6 条 3 步，14 条 5 步 | expansion_budget, frontier_budget, result_limit |
| 是 | 2000 | 5000 | 0 条 | expansion_budget, frontier_budget |
| 是 | 5000 | 5000 | 0 条 | expansion_budget, frontier_budget |
| 是 | 2000 | 20000 | 4 条 3 步 | expansion_budget |
| 是 | 20000 | 5000 | 20 条 6 步 | frontier_budget, result_limit |
| 是 | 10000 | 10000 | 4 条 3 步，15 条 5 步 | expansion_budget, frontier_budget |
| 是 | 20000 | 20000 | 4 条 3 步，16 条 5 步 | expansion_budget, frontier_budget, result_limit |

## 如何解释

- **默认 F=5000 的裁剪具有实际影响。**E 固定 2000，F 提高至 20000，即增加 4 条三步路线。反过来只把 E 提高至 20000、F 仍为 5000，得到 18 条六步路线，仍只有原来的 2 条三步路线。这说明被丢弃的短前缀不会因后续增加展开量而恢复。对应代码在队列达到 F 时停止加入产品分支（`path_search.py` 的 frontier_budget 分支），不是按化学优先级保留前缀。
- **原截图两行不是重复反应路线。**精确主线结构相同，末步分别是 Cl 参与生成 HCl 和 O2 参与生成 HOO，反应身份不同。它们都为 `2 / 77 / 1`，这不是整条链发生 1 或 2 次的证据。
- **新增三步路线仍是 CH 脱除类型。**三个不同精确开链中间体的事件数序列分别为 `2 / 77 / 1`、`2 / 6 / 1`、`3 / 3 / 1`，各自接同一五元环结构，再有两种末步。因此增加预算并没有自动换成更可信的化学路线。
- **排除一条边不是化学过滤方案。**只读包装器仅剔除截图第一步的精确 reaction_key（两个 reader 方法均过滤，并多取一行补足页大小）。它没有剔除其他 CH 脱除，也没有修改 DB。默认变成 0 条而大预算仍有其他三步、五步路线，说明“禁用一条可疑边后无结果”同样可能是预算造成的。
- **五步路线的分子式序列发生更广泛变化。**20k/20k 的输出包括 `C6H5ClO → C6H4ClO → C6H5ClO2 → C5H4ClO → C5H4ClO → C5H3ClO`，以及 `C6H5ClO → C6H4ClO → C9H7ClO → C9H6ClO → C5H3ClO → C5H3ClO`。公式只用于概览；完整反应与精确结构见 JSON。没有连续历史或能量证据时，不能将这些更长路线解释为更合理。

## 对方法的具体含义

当前 [设计基准 §11.5](../software-design-baseline.md#115-candidate-path-discovery) 已将预算与化学重要性区分，并明确按步数与精确身份展开。本实验没有发现“代码违背这一约定”的证据；它证明这一展示/截断规则对真实查询的候选覆盖有明显影响。若产品目标是让用户首先获得值得核查的化学路线，预算敏感性和前缀丢弃需要作为方法评估问题处理，不能只以“候选不是机理”解释输出。

本实验未核查 event 当帧键变化、中间体持续时间、连续谱系、能垒或模型可靠性；这些由父任务的事件审计补充。

## 新增短路线的第一步证据

以下均仅有局部 dominant atom-descendant 支持；连续历史与原始键审计未在本预算实验中执行。精确第一步及各事件记录同时保存在 JSON 的 `three_step_first_edge_evidence`。

```text
[H][O][C]1=[C]([Cl])[C]([H])=[C]([H])[C]([H])=[C]1[H]->[H][C]+[H][C][C]([H])[C]([O][H])[C]([Cl])[C][H]
```

event_count = transfer_event_count = 2；每个支持事件 shared_atoms=11。

`rngevt_63274_816b79a5fe88`、`rngevt_64737_722e970a1b71`。

```text
[H][O][C]1=[C]([Cl])[C]([H])=[C]([H])[C]([H])=[C]1[H]->[H][C]+[H][C][C]([O][H])[C]([Cl])=[C]([H])[C][H]
```

event_count = transfer_event_count = 3；每个支持事件 shared_atoms=11。

`rngevt_178970_c01f729e4683`、`rngevt_6376_13095290d99a`、`rngevt_9150_7ce8edc14b11`。
