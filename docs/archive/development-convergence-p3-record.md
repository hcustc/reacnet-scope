# P3 分支追踪交付与验收记录

日期：2026-09-19

本记录对应[收敛开发计划](development-convergence-plan.md)的 P3（C07–C08）。本批只完善
已有 Molecule Lineage 的分段交接，没有扩展 Species Fate、自动主路径排名或 Continuous
MD Support。

## 1. 用户可见名称

界面统一使用“分子分支追踪”“继续追踪分支”和“继续追踪所选分支”。领域对象、Python
模块和导出文件仍保留 `Molecule Lineage` 名称，稳定控件 ID 与公共入口不因文案调整而改名。

## 2. 结果与交接契约

- 结果 schema 升级为 `molecule-lineage/v2`。每个 `segment` 记录起点 Molecule
  Instance、保留锚点、方向、Dataset Identity、源修订、本段预算、已查范围和停止记录。
- `branch_summaries` 区分预算停止、观测边界和连续性断点。只有
  `persistent_depth_limit` 与 `molecule_node_limit` 可以由用户显式继续；软件不会越过证据
  断点。
- 没有更近索引事件时现在显式记录 `observation_boundary`，不再静默结束。
- 继续后的分子节点、Reaction Occurrence、边和 Fast Recrossing Episode 按稳定身份合并；
  已交接的停止点保留 `continued_by_segment_id`，不会从审计记录中消失。
- 来源文件签名、Dataset Identity 或源修订不匹配时拒绝继续。继续失败在 Dash 中返回
  `no_update`，已完成片段、图和导出仍保留。
- JSON/CSV 新增 context、segment 和 branch summary；事件和分子节点均可交接到现有局部
  轨迹入口。

旧 `molecule-lineage/v1` 结果仍可查看和导出，但不能直接继续；用户需在当前已验证来源上
重新执行一次分支追踪。Event Evidence Index schema 未改变，不需要重建大型索引。

## 3. 固定真实案例 C

来源：

`/home/huangchen/cal_proc/production_md/runs/phi1_2500K_iter32_000_seed256788_20260914/`
`rng_timed_hdf5_perf_20260915/trajectory.lammpstrj.timeline.h5`

- Dataset ID：`1044f75c314b4383a040`。
- 验收所用源修订：
  `eaf7a1bd2f34319375d6c3cc4ad457ba792325b979622eb18139de9c0f376a12`。
- Event Evidence Index：169,594 条事件，已有索引只读使用；未启动 preparation，也未扫描
  19.7 GB 原轨迹。
- 起点：`rngevt_72544_6f2b9e11023f` 的 `product:1`。
- 固定锚点：Scope/LAMMPS 1-based atom IDs `301,302,303,304,305`。
- 方向：backward；每段 `persistent_depth=20`、`max_molecule_nodes=500`，Fast
  Recrossing 窗口 5 个 Analyzed Frames。

首段得到 20 个唯一事件、42 个分子节点，查询范围为 analyzed transition
`70135–72544`、source frame `7013500–7254500`；停止原因为
`persistent_depth_limit`，分支明确标为可继续。

继续一次后合并结果为 2 段、40 个唯一事件和 96 个唯一分子节点；唯一 ID 数与记录数完全
相等，没有跨段重复累计。合并范围扩展至 analyzed transition `66578–72544`、source frame
`6657800–7254500`。原停止点保留为 1 条历史交接记录，新活动分支仍以
`persistent_depth_limit` 停止并可继续。原始事件中保留 7 个 split 和 7 个 merge，没有把
分裂/重并压成一条线。两次查询分别约 0.37 s 和 0.38 s；独立复测两段合计约 1.13 s。

## 4. 两个规模点的有界性

使用 `tracemalloc` 在分析调用前开始记录 Python 新分配峰值；数字用于确认查询规模，而不是
宿主总 RSS 基准。

| 来源规模 | 查询与合并结果 | 已查范围 | Python traced peak | 用时 |
| --- | --- | --- | ---: | ---: |
| 4-event fixture | 2 段、4 事件、12 分子节点 | transition 0–3 / frame 0–40 | 0.131 MiB | 0.034 s |
| 169,594-event 真实索引 | 2 段、40 事件、96 分子节点 | transition 66578–72544 / frame 6657800–7254500 | 1.350 MiB | 1.130 s |

在线路径只调用已发布索引的稳定事件读取和 nearest-atom-event 查询；结果内存随访问片段增长，
没有加载完整 169,594-event occurrence 集合。

## 5. 验证

- `tests/test_molecule_lineage.py`：继续合并、稳定身份去重、旧结果不变、修订不匹配拒绝、
  v2 CSV 记录。
- Dash smoke：初次追踪、继续追踪、失败保留旧结果、分支控件和新文案。
- Event Evidence 回归：50 个谱系/事件索引相关测试通过。
- 全量：`555 passed, 2465 warnings in 25.48s`。警告均为 Dash 内置
  `dash_table.DataTable` 的既有弃用提示。
- `./start-reacnet-scope.sh --check` 通过；默认
  `/media/huangchen/T3000/rng_data_2500K` 当前未挂载或不可访问，但不阻止启动。
- 在临时 8061 端口实际启动 Dash，`/_dash-layout` 返回 200；布局包含“继续追踪分支”和
  “继续追踪所选分支”，不包含旧称。未在本批执行人工浏览器拖拽和视觉回归。

## 6. 回退边界

代码回退不删除 RNG 原始工件或现有 Event Evidence Index。若回退到只理解 v1 的版本，v2
JSON/CSV 保留为普通审计文件，但继续交接功能不可用；重新升级后可在同一源修订上重新构建
追踪结果。后续多来源比较结果见
[P4 记录](development-convergence-p4-record.md)。
