# P5 退役 UI 清理与最终验收记录

日期：2026-09-20

状态：P5 的 C11–C12 已完成；[收敛开发计划](development-convergence-plan.md)的
P0–P5、C01–C12 全部完成。

本批只删除已退出普通 Dash 的页面挂载和页面专用代码，并完成最终验收。公共 CLI/API、已有
JSON/CSV 契约、事件索引、分子连续性、时间、体积、PBC 和几何证据核心均保留。

## 1. C11：退役 UI 清理

- Dash 只挂载五个工作区及其普通内部工具：`species`、`evolution`、
  `element-distribution`、`reactions`、`events`、`trajectory`、`data-management` 和
  `batch-compare`。普通一级入口仍是数据集、物种与趋势、反应与事件、轨迹与谱系、对比。
- 删除 Candidate Path、Path Verification、Species Fate 的冻结布局、页面专用 Store、回调、
  数据集 reset 输出、持久化白名单、CSS 和 `pathway.svg`；删除显式 Event Path 向导与
  Candidate/Species Fate 的 Dash 表现层，没有按模块目录删除共享服务。
- `candidate-paths`、`pathway`、`species-fate` 三个旧页面 ID 仍可从旧 session 读入，但会在
  `navigation.resolve_page_id` 中分别归一到 `reactions`、`reactions`、`trajectory`。恢复只显示
  所属工作区，不运行退役分析，也不保留隐藏轮询。
- Dash callback 数从 159 降为 134。最终 layout 有 420 个 ID；layout/dependency 审计没有发现
  缺失 ID，也没有 `page-candidate-paths`、`page-pathway`、`page-species-fate` 或相应退役控件。
- `reacnet-scope --help` 继续列出 13 个命令；`candidate-paths`、`verify-path`、
  `species-fate`、事件与导出命令均未删除。Python 核心和服务门面未因页面退役改名或删除。

## 2. C12：真实浏览器主线

最终端到端走查使用实际 headless Firefox，而不是只检查 Dash layout JSON。数据来自已有生产
目录：

`/home/huangchen/cal_proc/production_md/runs/1ER_3500K_rep2/analysis/`
`branch_competition_20260914/window_0_200_nohmm`

- Dataset Candidate ID：`841ce3036477657eb4b2`；源修订指纹：
  `0c95f256aff07a98cb50b773e47594f5a3300fcb6d007f8cc5d5460c210f13fa`。
- 原始工件保持只读。为本次验收在
  `/tmp/reacnet-scope-p5-workspaces` 建立临时派生索引：3 个轨迹帧、3 个组成时点、
  13 个精确 Species、585 个事件、24 个 Reaction Type、2 个事件区间；Molecular
  Evidence 与 continuity 均可用。索引不写入生产目录。
- 精确查询
  `[H][O][C]1=[C]([Cl])[C]([H])=[C]([H])[C]([H])=[C]1[H]`
  唯一命中 1 条 Species。浏览器显示 11 条可选生成/消耗通道。
- 选择的生成通道保留完整结构身份：
  `[H][O][C]1=[C]([H])[C]([H])=[C]([H])[C]([H])=[C]1[Cl] ->`
  `[H][O][C]1=[C]([Cl])[C]([H])=[C]([H])[C]([H])=[C]1[H]`。
  事件索引返回 3 条记录；首条 matched 事件为 `rngevt_1_dc17814de2b8`。
- 事件选择生成稳定书签，并下钻到 3 帧局部轨迹；查看器报告 6 个反应核原子、20 个局部
  上下文原子和 7 个环境原子。随后从页面实际下载
  本地验收产物 `analysis_outputs/p5_browser_downloads/rngevt_1_dc17814de2b8_evidence.zip`：
  5,241 bytes，SHA-256
  `5ca7bf9b62b458b0967b79804006e27d044c3ec4795f81d18aea1cd602503ede`。
  ZIP 含 `event.json`、`frames.csv`、`changed_bond_distances.csv`、
  `trajectory.lammpstrj`、`bonds.csv`、`README.txt` 六个成员。
- 1440×1000 下走通整条主线；760×900 下逐个点击五个工作区均能显示目标页面。实际 DOM
  中三个退役页面节点均不存在，页面没有 Dash error overlay。

同一批走查也重新加载了用户指定的 iter32 2500 K 数据集，精确目标唯一命中并返回 37 条
可选通道，完整 Reaction Type 能交接到事件页。该来源在本次临时 cache 中没有已发布轨迹索引，
因此没有在在线请求中重建 19.7 GB 轨迹；大型来源的事件—轨迹—导出闭环使用 P2 已记录的
已发布索引结果，最终浏览器下载则使用上述小型真实生产窗口完成。

## 3. 四个固定真实案例

| 案例 | 最终验收依据 | 结论 |
| --- | --- | --- |
| A：目标标签与原始结构 | [P0–P1 固定来源](development-convergence-p0-p1-record.md)、[P2 身份核查](development-convergence-p2-record.md) | iter32 2500 K 的精确 RNG Species 与 Molecular Evidence 来源分别显示；`miso=1`、HMM 关闭等只来自显式元数据，不把代表标签未命中解释为真实结构不存在。 |
| B：目标生成事件 | [P2 事件闭环](development-convergence-p2-record.md)、本记录的小型真实浏览器闭环 | 大型来源保留 19 条生成 Reaction Type、269 条 matched 记录和四组碳集合的限定语；`rngevt_72544_6f2b9e11023f` 已按修订核验、读取 8 帧并确定性导出。最终浏览器另实际下载了一个 3 帧 matched 事件包。 |
| C：分裂重并与继续追踪分支 | [P3 分支追踪](development-convergence-p3-record.md) | 真实 169,594-event 索引上合并 2 段、40 个唯一事件、96 个唯一分子节点，保留 7 split / 7 merge；预算停止明确为可继续，不写成“无上游”。 |
| D：模型迭代比较 | [P4 可比性记录](development-convergence-p4-record.md) | iter25/iter32 真实比较得到 20 条网络级 Reaction Type；19 个真实来源扫描形成 9 个待确认建议组。来源身份、时间和证据级别分别说明，不从聚合网络自动推断主机理改变。 |

## 4. 自动化与结构检查

- 定向回归：`tests/test_dash_smoke.py tests/test_dataset_switch_callbacks.py`，
  `136 passed, 1500 warnings in 8.76s`。
- 全量：`554 passed, 1692 warnings in 31.42s`。相对 P4 的 560 项净少 6 项，均为退役页面
  行为测试；核心 CLI/API、事件、谱系、比较与导出测试仍在全量集合中。
- Dash 首页、`/_dash-layout` 与 `/_dash-dependencies` 返回 200；134 个回调的所有输入、输出
  和 State 均能解析到现存 layout ID。
- 运行时代码中退役名称只剩 `LEGACY_PAGE_REDIRECTS` 的三个兼容键；退役 CSS 类和
  `pathway.svg` 引用为零。
- 警告仍是 Dash 内置 `dash_table.DataTable` 的既有弃用提示。

## 5. C01–C12 完成矩阵

| 任务 | 验证记录 |
| --- | --- |
| C01–C02：基线、范围和兼容边界 | [P0–P1 第 1–2 节](development-convergence-p0-p1-record.md) |
| C03–C04：五工作区和普通主线收缩 | [P0–P1 第 4 节](development-convergence-p0-p1-record.md) |
| C05–C06：身份、事件书签和轨迹导出 | [P2 记录](development-convergence-p2-record.md) |
| C07–C08：分段继续、摘要、停止原因和交接 | [P3 记录](development-convergence-p3-record.md) |
| C09–C10：统一来源与比较可比性 | [P4 记录](development-convergence-p4-record.md) |
| C11–C12：退役 UI 清理和最终验收 | 本记录第 1–4 节 |

## 6. 已知限制与交付边界

- 默认 `/media/huangchen/T3000/rng_data_2500K` 的精确子目录当前不存在或不可访问；软件会
  明确报告缺数据并继续启动。用户提供的 `/home/huangchen/cal_proc/production_md/runs/...`
  来源已完成实际加载和验收。
- 大型 iter32 轨迹约 19.7 GB。没有已发布轨迹索引的 workspace 只报告“需准备”，在线查询
  不会偷偷顺序扫描或重建；本轮没有为重复验收另建一份大型索引。
- 浏览器验收覆盖 headless Firefox 的 1440×1000 和 760×900；未启动 OVITO，也未做
  macOS/Windows 原生浏览器与人工拖拽验收。
- Candidate Path、Path Verification、Species Fate 和表观速率核心只是兼容保留，不表示它们
  已重新进入普通产品范围。恢复任一独立 Dash 入口需要新的范围决策、实现和真实案例验收。
- 本批没有修改大型索引 schema 或 RNG 原始工件。回退代码不会清理生产数据；本次临时索引
  位于 `/tmp`，可独立丢弃。
