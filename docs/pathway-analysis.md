# 候选路径分析

ReacNet Scope 的候选路径功能从 `.reactionabcd` 中的聚合反应网络出发，按固定、
可审计的公式排序有界路线，并在预建事件索引可用时关联
`.reactionevent.csv` 与 `.molecules.csv` 的汇总证据。Python、CLI 和 Dash
共用同一套搜索与评分实现。

## 证据边界：候选路径不等于确认机理

输出中的 path 是 **candidate route（候选路线）**，不是 confirmed mechanism
（已确认机理）。一条候选路径能够说明：

- `.reactionabcd` 中存在组成这条路线的聚合反应超边；
- 每一步在当前查询方向上具有正净 TP，并通过了用户设置的阈值；
- 若标记为 `evidence_linked`，预建 SQLite 索引中存在可用于评分的 RNG
  事件汇总快照。

它不能单独证明：

- 相邻步骤由同一组原子连续完成；
- 各步骤按输出顺序发生在同一条时间轨迹中；
- 聚合反应代表唯一的基元反应或唯一机理；
- 高分路线已经被实验或局部轨迹确认。

确认机理仍需逐步检查代表性事件、参与原子、断键/成键和局部轨迹，并结合
体系条件与外部化学证据。`evidence_linked` 只表示“已关联事件索引并纳入
评分”，不等于“实验确认”或“原子连续性确认”。

## 路径和反应超边语义

搜索起点必须是当前 `.reactionabcd` 网络中的精确 SMILES。

- `downstream`：查看消耗当前焦点物种的反应，并沿产物方向继续。
- `upstream`：查看生成当前焦点物种的反应，并沿反应物方向回溯。
- 无论向下游还是上游遍历，每一步都保留 `.reactionabcd` 中记录的原始方向，
  以及完整的 `reactants` 和 `products`。
- 反应按超边处理。一次反应可以有多个反应物和多个产物；焦点链只记录本次
  继续搜索的 `focal_input → focal_output`，不会丢弃超边中的其他参与物种。
- 重复的化学计量项会保留。例如 `A + X → B + B` 的 `products` 中仍有两个
  `B`，分支份额的分母也计入这两个出现次数。
- 同一焦点物种不能在一条路径中重复，因此不会产生
  `A → B → A` 形式的焦点循环。超边中的非焦点参与物种仍保持原样。

正向 TP、反向 TP 和净 TP 的定义为：

```text
net_tp = forward_tp - reverse_tp
directionality = net_tp / forward_tp
```

只有 `net_tp > 0` 的记录方向可参与搜索。`net_share` 是该分支的 `net_tp`
除以当前焦点物种所有正净候选分支的 `net_tp` 之和；这个分母在循环过滤、
阈值过滤和每步分支裁剪之前计算，并保留重复化学计量项的贡献。

## 评分协议 `candidate-path/v1`

所有 JSON、CSV 和 Dash 结果使用未四舍五入的原始数值，并发布
`score_version="candidate-path/v1"`。

事件索引可用时，单步评分为：

```text
step_score =
    0.40 * net_share
  + 0.25 * directionality
  + 0.20 * event_coverage
  + 0.15 * time_coverage
```

其中：

```text
event_coverage = matched_event_total / event_total
time_coverage  = distinct_intervals / available_intervals
```

分母为零时相应覆盖率为 `0.0`。若索引已经就绪，但某个 reaction key 没有
汇总行，该步仍属于同一个 `evidence_linked` 快照，事件计数和两项覆盖率均为
零。

没有可用事件索引时，不把缺失证据误当成零证据，而是只对网络项重新归一化：

```text
step_score =
    (0.40 * net_share + 0.25 * directionality) / 0.65

等价权重：
    net_share      = 0.40 / 0.65
    directionality = 0.25 / 0.65
```

此时状态为 `network_only`，事件覆盖率和事件计数为 `null`。因此
`network_only` 高分不能解释为事件证据较强，也不宜把不同证据状态下的绝对
分数当作同一实验置信度比较。

路径分数同时考虑所有步骤的几何均值和最弱一步：

```text
path_score =
    0.70 * geometric_mean(step_scores)
  + 0.30 * min(step_scores)
```

## 搜索边界、排序和结果原因

| 参数 | 默认值 | 有效范围 | 含义 |
| --- | ---: | ---: | --- |
| `direction` | `downstream` | `downstream` / `upstream` | 生成方向或溯源方向 |
| `max_depth` | 3 | 1–12 | 一条路径的最大反应步数 |
| `max_branches` | 5 | 1–100 | 每个搜索状态保留的最大分支数 |
| `max_paths` | 20 | 1–500 | 返回的候选路径上限 |
| `max_expansions` | 5000 | 1–1,000,000 | 非终止搜索状态的展开上限 |
| `min_net_tp` | 1 | 整数，至少 1 | 每步最小正净 TP |
| `min_directionality` | 0.05 | 0–1 | 每步最小方向性 |

搜索是确定性的有界 best-first 枚举。分支先按评分和稳定的语义键排序，再应用
`max_branches`。在不触及展开上限时，搜索只会在剩余队列的理论最高完成分数
严格低于已保留第 N 名时提前停止，因此同分候选仍会继续比较。

每个被弹出且深度尚未达到 `max_depth` 的状态都计为一次 expansion，即使它
随后因循环或阈值没有子分支。若到达 `max_expansions`：

- `truncated=true`；
- `expansions` 给出实际展开数；
- 已完成路径与当时队列中的确定性部分路径一起参与返回排序；
- 结果不能解释为穷尽了所有候选。

`reason` 的稳定取值如下：

| `reason` | 含义 |
| --- | --- |
| `ok` | 返回了至少一条候选路径 |
| `species_absent` | 起始 SMILES 不在反应网络中 |
| `no_positive_net_continuation` | 起点没有可继续的正净分支，或只有焦点循环 |
| `filtered_by_thresholds` | 正净的新分支全部被净 TP 或方向性阈值过滤 |

结果最终按路径分数降序排列；分数相同时使用物种链和 reaction key 链稳定
排序。`max_paths` 只是返回上限，`truncated` 专门表示是否撞到 expansion
上限。

## 人工准备事件索引

ReacNetGenerator 运行时需要生成事件及分子时间线：

```text
--reaction-event --show-molecule-time
```

候选路径请求本身始终是只读的：它不会构建索引，也不会顺序扫描
`.reactionevent.csv` 或 `.molecules.csv`。请先给准备命令和运行 CLI/Dash
的进程设置同一个持久、可写缓存目录：

```bash
export REACNET_SCOPE_CACHE_DIR="/data/reacnet-cache"
uv run reacnet-scope-prepare "/data/case with spaces" --event-only
uv run reacnet-scope-prepare "/data/case with spaces" --status
```

缺失或尚未准备的索引会使查询降级为 `network_only`，同时输出可复制的
`--event-only` 命令。若索引陈旧、损坏或与源文件不一致，请人工重建：

```bash
export REACNET_SCOPE_CACHE_DIR="/data/reacnet-cache"
uv run reacnet-scope-prepare "/data/case with spaces" --rebuild event
```

准备工作应在独立终端或作业中运行。不要把示例目录原样使用；替换为服务器上
真实的数据目录和缓存目录。CLI 会由 `--reac` 自动推导同前缀的
`.reactionevent.csv` 与 `.molecules.csv`，例如：

```text
run.lammpstrj.reactionabcd
run.lammpstrj.reactionevent.csv
run.lammpstrj.molecules.csv
```

## CLI

先查看当前安装版本的精确参数：

```bash
uv run reacnet-scope pathway --help
```

一个同时导出 JSON 和逐步 CSV 的完整示例：

```bash
uv run reacnet-scope pathway \
  --reac "/data/case with spaces/run.lammpstrj.reactionabcd" \
  --start-smiles '[CH3]' \
  --direction downstream \
  --max-depth 3 \
  --max-branches 5 \
  --max-paths 20 \
  --max-expansions 5000 \
  --min-net-tp 1 \
  --min-directionality 0.05 \
  --out-json "/data/analysis/candidate-pathways.json" \
  --out-csv "/data/analysis/candidate-pathway-steps.csv"
```

SMILES 建议用单引号包围，文件路径建议用双引号包围，以避免 shell 把方括号、
空格或其他字符解释为语法。终端摘要的格式为：

```text
# candidate_paths=<数量>, reason=<原因>, truncated=<True|False>, evidence=<evidence_linked|network_only>
rank,score,steps,evidence_status,species
...
```

如果事件索引不可用，候选网络结果仍写入输出文件，人工准备命令会写到标准错误。
缺少或无效的 `.reactionabcd` 会以退出码 `2` 报告使用错误。

### JSON 输出

JSON 顶层 schema 为 `reacnet-scope/pathways/v1`。下面只展示结构；数值和
reaction key 是说明性占位，不代表任何真实体系已经确认：

```json
{
  "schema_version": "reacnet-scope/pathways/v1",
  "score_version": "candidate-path/v1",
  "evidence_status": "evidence_linked",
  "query": {
    "start_smiles": "[CH3]",
    "direction": "downstream",
    "max_depth": 3,
    "max_branches": 5,
    "max_paths": 20,
    "max_expansions": 5000,
    "min_net_tp": 1,
    "min_directionality": 0.05,
    "interpretation": "candidate route, not mechanistic proof"
  },
  "reason": "ok",
  "truncated": false,
  "expansions": 12,
  "source_signatures": {
    "reactionabcd": {
      "path": "/data/example/run.lammpstrj.reactionabcd",
      "size": 1234,
      "mtime_ns": 123456789
    },
    "reactionevent": {
      "path": "/data/example/run.lammpstrj.reactionevent.csv",
      "size": 2345,
      "mtime_ns": 123456790
    },
    "molecules": {
      "path": "/data/example/run.lammpstrj.molecules.csv",
      "size": 3456,
      "mtime_ns": 123456791
    },
    "event_index": {
      "path": "/data/reacnet-cache/datasets/example/events.sqlite3",
      "size": 4567,
      "mtime_ns": 123456792,
      "schema_version": 1
    }
  },
  "paths": [
    {
      "rank": 1,
      "species": ["[CH3]", "[CH3][O]"],
      "score": 0.7,
      "evidence_status": "evidence_linked",
      "score_version": "candidate-path/v1",
      "steps": [
        {
          "reaction_key": "[CH3]+[O]->[CH3][O]",
          "traversal_direction": "downstream",
          "focal_input": "[CH3]",
          "focal_output": "[CH3][O]",
          "reactants": ["[CH3]", "[O]"],
          "products": ["[CH3][O]"],
          "forward_tp": 10,
          "reverse_tp": 2,
          "net_tp": 8,
          "net_share": 0.8,
          "directionality": 0.8,
          "event_coverage": 0.75,
          "time_coverage": 0.2,
          "event_total": 4,
          "matched_event_total": 3,
          "distinct_intervals": 2,
          "evidence_status": "evidence_linked",
          "source_references": ["/data/reacnet-cache/datasets/example/events.sqlite3"],
          "score": 0.7,
          "score_version": "candidate-path/v1"
        }
      ]
    }
  ]
}
```

实际服务还会为物种和每一步补充分子式字段。JSON 先写到目标文件旁的 `.tmp`
文件，再以原子替换发布，避免留下半写入目标。

### CSV 输出

CSV 每个路径步骤一行，而不是每条路径一行。它保留路径排名、步骤序号、完整
反应物/产物列表、所有评分输入、事件计数、证据状态、版本和来源引用。主要列为：

```text
path_rank,step_index,path_species,reaction_key,traversal_direction,
focal_input,focal_output,reactants,products,forward_tp,reverse_tp,net_tp,
net_share,directionality,event_coverage,time_coverage,event_total,
matched_event_total,distinct_intervals,path_score,step_score,
evidence_status,score_version,source_references
```

`path_species`、`reactants`、`products` 和 `source_references` 是 JSON 数组文本，
所以能够无损保留重复项。空结果仍会写出合法的 JSON 空数组和只有表头的 CSV。

## Dash“关键路径”页面

1. 加载含 `.reactionabcd` 的数据集。
2. 从右上角“高级工具”进入“关键路径”，或在物种/反应检索结果中点击
   “作为路径起点”/“从所选反应起点找路径”。
3. 输入或确认精确 SMILES，选择下游或上游方向，设置深度、每步分支、路径
   上限、最小净 TP 和最小方向性，然后点击“搜索候选路径”。
4. 表格显示分子式链、SMILES 链、路径分数、最弱步分数、深度和证据状态；
   下方二部超图保留每一步的完整反应物/产物。
5. 在表格选择一条路径，可点击“在网络中高亮路径”。要检查某一步，继续点击
   图中的黄色菱形反应节点，再点击“查看该步事件”；界面会进入事件页并填入
   该步的完整 `reactants → products` 文本。
6. 使用“下载 JSON”或“下载 CSV”导出当前内存中的同一排序结果，不会重新
   发起搜索。

灰色虚线反应节点表示 `network_only`。如果事件页没有可用索引，先按界面或
CLI 输出的准备命令离线建立索引，再重新执行路径搜索。

## 来源签名与证据限制

每次服务查询都会记录 `reactionabcd` 的 `path`、`size` 和 `mtime_ns`。
事件索引可用时，`source_signatures` 还包含：

- `reactionevent`：事件 CSV 的路径、大小和修改时间；
- `molecules`：分子时间线 CSV 的路径、大小和修改时间；
- `event_index`：SQLite 路径、大小、修改时间和 schema version。

服务会在查询前后检查这些文件是否被替换或修改，避免把不同时间点的来源混入
一次结果。签名是可复现性元数据，不是内容加密哈希；移动文件、保留相同时间戳
的外部改写以及上游数据语义错误仍需人工审计。

事件汇总通过一次批量只读 SQLite 查询获取，不会为每个展开状态访问磁盘。
`source_references` 指向用于该步汇总的事件索引。汇总计数能够支持“该反应类型
有多少事件被关联、覆盖多少时间区间”的判断，但不能把不同步骤自动串成同一组
原子的连续轨迹。对关键路线仍应从 Dash 跳转到逐事件和局部轨迹视图完成核查。
