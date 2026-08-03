# 时间有序、原子连续的事件路径

`event-paths` 分析以预建事件索引中的**具体 RNG 事件**为节点，回答聚合
`.reactionabcd` 网络不能回答的问题：某条 `event1 → event2 → event3` 是否真的由
同一条轨迹中的连续原子谱系按时间顺序完成。

## 证据要求

每个重复实验必须具有以下两种 Timed Evidence Source 之一：

- 原生 schema-1 `.timeline.h5`，且同时启用 Reaction Evidence 与 Molecular Evidence；
- 或兼容 CSV 对：`.reactionevent.csv` 与 `.molecules.csv`；

此外还需要：

- 已由 `reacnet-scope-prepare ... --event-only` 建好的事件索引；
- `.reactionabcd`（可选但推荐）：用于比较聚合网络可达路径与实际路径。

只有 Reaction Evidence 的索引可以检索事件和判断事件先后，但没有分子实例与
原子映射，因而不能断言原子连续路径。分析会明确报错，不会把“同名物种先后
出现”伪装成原子连续证据。

## 节点、边和路径的严格定义

### 节点

每个索引中的 RNG 事件是一个独立节点，节点保留：

- 稳定 `event_id` 和规范化 `reaction_key`；
- `timestep_index`、反应前 timestep、反应后 timestep；
- 反应前和反应后的具体分子实例；
- 参与原子 ID。

无法与 Molecular Evidence 匹配的事件仍计入节点总数，但不参与实际路径连接。
为避免跨过未知事件制造假连续性，未解析事件涉及的反应物/产物物种会成为保守的
谱系屏障：此前处于活动状态的同物种分子实例不再允许直接连接到屏障后的事件。

### 边

`event1 → event2` 只在以下条件全部满足时成立：

1. `event2` 的事件区间严格晚于 `event1`；
2. `event1` 的一个产物分子实例与 `event2` 的一个反应物分子实例完全相同；
3. “完全相同”同时要求精确 SMILES 和完整原子 ID 集合相同；
4. `event2` 是该实例在 `event1` 之后的第一次消费事件；
5. 同一时间区间内的事件不被人为排序；一个实例若有多个不唯一的消费者或
   生产者，则该连接被排除并计入歧义数。

这条规则允许分子在两个事件之间跨越多个无反应帧，但不会跨过该分子已经参与的
中间事件直接连接到更晚事件。

### 三事件路径

即使 `event1 → event2` 和 `event2 → event3` 两条分子实例边都存在，也只有当
两条边携带的原子 ID 集合交集非空，三事件路径才成立：

```text
lineage_atoms = atoms(edge1) ∩ atoms(edge2)
keep path iff lineage_atoms != ∅
```

因此，`event1` 生成分子 B、`event2` 消耗 B 并同时生成无 B 原子的 Y、随后
`event3` 消耗 Y，不会被误判为 B 的原子连续谱系。`--path-length 2..8` 使用同一
不变量；默认值为 `3`。

## 统计量

实际路径按规范化 `reaction_key` 序列聚合。每个路径签名报告：

- `occurrence_count`：具体事件 ID 序列的数量；
- `independent_atom_lineage_support_count`：去重后的
  `(replicate, atom_id)` 数量；
- `independent_lineage_set_support_count`：去重后的
  `(replicate, 完整连续原子集合)` 数量；
- `replicate_support_count`：至少出现一次该签名的重复实验数；
- `replicate_reproduction_rate`：`replicate_support_count / 总重复数`；
- 每条边的 interval gap、空闲 physical timestep gap、事件锚点 timestep gap；
- 全路径 interval span 与锚点 timestep span。

时间统计均给出 `count/min/median/mean/max`。其中：

```text
interval_gap = next.timestep_index - previous.timestep_index
idle_timestep_gap = next.before_timestep - previous.after_timestep
anchor_timestep_gap = next.after_timestep - previous.after_timestep
```

连续相邻的 RNG 区间通常具有 `idle_timestep_gap = 0`，但仍有正的
`anchor_timestep_gap`。

原子 ID 在每个重复实验内部有效；跨重复统计始终以 `(replicate, atom_id)` 作为
唯一谱系键，不会把两个重复中恰好相同的数字 ID 当成同一原子。

## 聚合网络与实际路径的差异

若公共前缀旁存在 `.reactionabcd`，分析会枚举相同长度的聚合网络可达路径。聚合
路径只要求：前一步的某个产物 SMILES 也是后一步的某个反应物 SMILES。随后在每个
重复实验内比较规范化反应键序列：

- `confirmed`：聚合网络可达，且存在严格实际事件路径；
- `aggregate_only`：聚合网络可达，但轨迹中没有实际原子连续路径；
- `actual_only`：事件路径存在，但当前聚合网络中缺少对应反应键或物种连接；
- `realization_rate = confirmed / aggregate_reachable`。

`aggregate_only` 正是聚合网络组合产生的“可达但未实际发生”路径。若聚合枚举或
实际路径展开触及上限，相应计数会标为下界，`comparison_complete=false`，且
`realization_rate=null`，避免输出伪精确比例。未提供 `.reactionabcd` 时，实际
路径统计仍可运行，但 `comparison_available=false`。

## Dash 界面

Dash 的“反应路径”页面把两个证据层级串成一个工作流：

| 功能 | 回答的问题 | 基本节点 | 连接条件 | 不能证明什么 |
| --- | --- | --- | --- | --- |
| **① 搜索可能路线** | 聚合网络上可以怎样走？ | 汇总后的反应类型 | 前一步产物与后一步反应物具有相同 SMILES | 同一批原子真的按顺序走完 |
| **② 验证实际发生** | 轨迹里真的这样走过吗？ | 带事件 ID 和时间的具体 RNG 事件 | 严格时间先后、同一分子实例、连续原子 ID | — |

可将两者理解为“路网”和“行车记录”：聚合网络中同时存在 `A → B` 与 `B → C`，
只能推出 `A → B → C` 是一条网络候选；只有某个具体事件先产生 B、后续事件再消费
同一个 B，并且至少一个原子贯穿两步，才能称为实际发生路径。聚合网络来自轨迹中
出现过的反应类型汇总，因此“候选”不是凭空预测，但也不是完整机理证据。

两个功能可以单独使用。“① 搜索可能路线”适合从指定起始物种快速找方向；
“② 验证实际发生”会独立枚举满足条件的具体事件链，并自动与聚合网络做整体路径签名
对照。它目前不是把功能 1 中选中的某一行直接判成真或假。

界面按四步向导运行：

1. **确认数据**：自动读取“管理数据”中已加载的当前数据集，显示重复标签、
   `.reactionevent.csv`、`.molecules.csv` 和事件索引状态。只分析当前数据集时无需
   输入任何文件路径；选择“跨多个重复统计”后，才会显示附加重复输入框，每行填写
   一个 `replicate=/path/to/run.lammpstrj` 公共前缀。点击“检查数据并继续”后，
   向导会同时验证所有文件、索引状态及原子—分子实例关联。
2. **定义路径**：设置 2–8 个事件节点，页面直接预览
   `event1 → event2 → event3`。起始精确 SMILES 可留空；时间间隔限制和审计明细
   上限收在“高级限制”中，通常可保持默认。
3. **确认并运行**：检查数据集数、当前重复、路径长度、起始物种和时间限制的摘要，
   然后点击“开始分析”。
4. **查看结果**：按复现率、原子谱系支持数和时间跨度筛选路径签名；选择一条签名及
   一次具体发生，审计事件节点、精确分子实例边和贯穿路径的原子 ID。需要调整条件时
   点击“修改条件”，会返回第二步并保留已有输入。

结果中的“网络候选实证比例”是
`有整链实证的聚合路径签名数 / 聚合网络可拼接路径签名数`，不是产率、转化率或
事件占比。“各步可查事件”也只说明单个反应步骤存在事件记录，不等于整条路径具有
时间与原子连续性。

界面默认隐藏纯 H/H₂ 循环，但不会从报告中删除它们；关闭过滤开关即可恢复显示。
“审计明细上限”仅控制浏览器中保留的具体发生记录数量，不影响完整汇总统计。附加
重复路径必须位于 Dash 配置的允许根目录中。

## CLI

先为每个重复实验准备索引，并确保准备和查询使用同一个持久缓存目录：

```bash
export REACNET_SCOPE_CACHE_DIR=/data/reacnet-cache

uv run reacnet-scope-prepare /data/case/rep1 --event-only
uv run reacnet-scope-prepare /data/case/rep2 --event-only
```

下例中的 `/data/case/...` 是需要替换的路径占位符。`--source` 的右侧是 RNG
输出的公共前缀，不带扩展名；可以重复传入：

```bash
uv run reacnet-scope event-paths \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --source rep2=/data/case/rep2/run.lammpstrj \
  --path-length 3 \
  --out-json event-paths.json
```

仓库自带的 `ref_data/rng-test-rp3-0523` 只有一个重复，可以从仓库根目录直接运行：

```bash
export REACNET_SCOPE_CACHE_DIR="$PWD/.cache/reacnet-scope"

uv run reacnet-scope-prepare ref_data/rng-test-rp3-0523 --event-only
uv run reacnet-scope event-paths \
  --source rp3="$PWD/ref_data/rng-test-rp3-0523/rp3.lammpstrj" \
  --top 2 \
  --out-json ref_data/rng-test-rp3-0523/event-paths.json
```

终端只缩略显示过长的反应序列；JSON 中始终保留完整反应键、事件节点和分子实例。
同前缀同时存在完整 `.timeline.h5` 与旧 CSV 时，CLI 自动选择原生文件。

常用限制：

- `--start-smiles`：只保留首个事件消耗指定精确 SMILES 的路径；
- `--max-interval-gap`：限制相邻事件的 RNG 区间差；
- `--max-timestep-gap`：限制两个事件之间的空闲物理 timestep；
- `--max-occurrence-details`：仅限制 JSON 中保存的具体路径明细，不影响统计；
- `--max-expansions`：实际路径搜索上限，触及时统计标为不完整；
- `--max-network-paths`：聚合网络路径枚举上限，触及时比较标为不完整。

## Python API

```python
from reacnet_scope.event_paths import EventPathSource, analyze_event_paths

report = analyze_event_paths(
    [
        EventPathSource(
            replicate="rep1",
            reactionevent_file="/data/rep1/run.lammpstrj.reactionevent.csv",
            molecules_file="/data/rep1/run.lammpstrj.molecules.csv",
            reaction_file="/data/rep1/run.lammpstrj.reactionabcd",
        ),
        EventPathSource(
            replicate="rep2",
            reactionevent_file="/data/rep2/run.lammpstrj.reactionevent.csv",
            molecules_file="/data/rep2/run.lammpstrj.molecules.csv",
            reaction_file="/data/rep2/run.lammpstrj.reactionabcd",
        ),
    ],
    path_length=3,
)
```

原生文件的 Python 调用把 timeline 路径传给兼容保留的
`reactionevent_file` 字段，并将 `molecules_file` 留空；索引中的 capability 会证明
Molecular Evidence 是否可用。

报告使用 `schema_version="event-path/v1"`。`occurrences` 保存可复核的具体事件
ID、节点、分子实例边和连续原子；`paths` 保存跨事件序列与跨重复统计；
`comparison` 保存聚合可达与实际发生的差异。
