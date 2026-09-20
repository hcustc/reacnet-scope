# P0–P1 收敛实施记录

日期：2026-09-19。

状态：P0 基线已固定；P1 五工作区实现已完成，验证结果见本文末尾。

依据：[收敛开发计划](development-convergence-plan.md)、
[软件设计基准](software-design-baseline.md)、
[ADR 0014](adr/0014-converge-dash-around-five-workspaces.md)。

## 1. P0 可回退基线

开工前工作树已有大量其他任务改动，不能以 `HEAD` 代替实际基线：

- 分支 `main`，父提交 `bfe704e950c84343a51a280811b3af4f95f181de`。
- 33 个已跟踪路径有修改，其中包括 Dash、核心服务、CLI、文档和测试；
  `scripts/webapp_dash/assets/dataset_browser.js` 已处于删除状态。
- 72 个未跟踪路径，包括项目 skills、Candidate/比较/QC 实现与测试、分析脚本和报告。
- `analysis_outputs/` 约 2.0 GiB，是已有真实案例工件，不属于本批代码删除或提交范围。

为避免修改当前索引或工作树，使用独立临时 index 保存了开工前源代码、文档和测试快照：

- 引用：`refs/reacnet-scope/baselines/p0-p1-20260919`
- 快照提交：`b5c74d83e0b828b7284c2eef5650e0a6b478570f`
- 父提交：`bfe704e950c84343a51a280811b3af4f95f181de`
- 快照包含已跟踪工作树以及未跟踪的源代码、文档、tests 和 skills；刻意不复制
  `analysis_outputs/**`。原分析工件和 RNG 源文件均保持原位、只读。

该引用可用 `git show`、`git diff` 或独立 worktree 检查；恢复时应先保留当前工作树，不能对
含用户改动的目录执行强制 reset。

### 可运行环境与测试基线

- Python 3.13.12，Dash 4.4.0，Flask 3.1.3；依赖来自当前 `uv.lock`。
- 基线命令：`uv run --locked pytest -q`。
- 基线结果：`543 passed, 2380 warnings in 24.51s`。警告均为当前 Dash
  `dash_table.DataTable` 弃用提示。

### 开工前产品入口

Dash 共挂载 11 个页面 ID：

`species`、`reactions`、`evolution`、`events`、`species-fate`、`trajectory`、
`candidate-paths`、`pathway`、`element-distribution`、`data-management`、
`batch-compare`。开工前它们全部由侧栏或数据集任务卡直接推广。

统一 CLI 共 13 个命令，P0–P1 不删除或改名：

`serve`、`prepare`、`element-distribution`、`species`、`reactions`、`events`、
`species-fate`、`batch-compare`、`verify-path`、`candidate-paths`、`export-event`、
`export-dft-geometry`、`species-evolution`。

`reacnet_scope.__all__` 的公共符号保持不变，包括 index 错误/清理、Event Path 验证、
Candidate Path schema/评分/能量证据、Reaction Readiness，以及 species compare API。
`reacnet_scope.services.__all__` 继续作为 Dash 服务门面，覆盖数据集事务、准备任务、物种/
反应/事件、时间演化、谱系、Fate、Candidate/Path Verification、事件包、DFT、OVITO/VMD
与比较。精确清单可由以下只读命令复核：

```bash
uv run --locked reacnet-scope --help
uv run --locked python -c 'import reacnet_scope, reacnet_scope.services as s; print(reacnet_scope.__all__); print(s.__all__)'
```

## 2. C02 入口与依赖处置映射

| 能力/入口 | P1 普通界面 | 回调与服务 | CLI/API/导出 | P1 测试与以后处理 |
| --- | --- | --- | --- | --- |
| 数据集 | 五工作区一级入口；任务卡保留能力与准备状态 | 两阶段 Dataset Candidate 验证、迟到结果和 Current Dataset 提交不变 | `prepare` 与数据集服务不变 | dataset selection/switch/capability；P5 不得删除工作区或索引维护 |
| 物种与趋势 | 一级工作区；物种、时间演化、元素分布为内部任务 | 现有查询与交接不变 | `species`、`species-evolution`、`element-distribution` 保留 | task-nav、物种下钻和时间演化回归 |
| 反应与事件 | 一级工作区；直接通道、反应式与事件为内部任务 | 直接通道调用显式 `include_kinetics=False`；事件身份交接不变 | `reactions`、`events`、`export-event` 保留 | 回调边界断言不计算 k；事件页由所属工作区高亮 |
| 轨迹与谱系 | 一级工作区；局部轨迹、谱系和事件导出留在同一上下文 | 轨迹、lineage、事件包与 DFT geometry 服务不变 | `export-event`、`export-dft-geometry` 及 API 保留 | 轨迹/谱系/DFT smoke；P3 再做继续追踪分支，不在 P1 扩展 |
| 对比 | 一级工作区；来源选择不改变 Current Dataset | species/batch compare 服务不变 | `batch-compare` 与 compare API 保留 | compare 与 dataset switch 测试 |
| Candidate Path 综合评分/能量 CSV | 从导航、数据集卡和任务导航撤下；旧页面只显示冻结说明 | 页面回调和 `candidate_paths` 核心兼容挂载，不由普通动作触发 | `candidate-paths`、API 和 JSON 保留 | 冻结页恢复/返回测试；P5 核对引用后才删退役 UI |
| Path Verification | 独立入口撤下；旧页面只显示冻结说明 | `event_paths` 与验证回调兼容保留 | `verify-path`、API、CSV/JSON 保留 | 旧 session 恢复不执行分析；以后只在明确事件序列需求下恢复入口 |
| Species Fate | 独立入口和普通任务卡撤下；旧页面只显示冻结说明 | Fate 核心、continuity substrate 和回调兼容保留 | `species-fate`、API、JSON/tables ZIP 保留 | 数据集 reset 仍清理 Fate 状态；P5 不得按页面删除共用索引 |
| Continuous MD Support 控件 | 不展示未完成时间约束控件 | 已接受契约及共用 continuity evidence 保留；不伪造已验证状态 | 无新增接口或 schema 变化 | Candidate 兼容页整体冻结；后续另立实现与规模验收 |
| Apparent Rate Constant Estimate | 默认 k 设置、体积准备和“显示速率列”撤下；物理时间确认保留 | 普通通道与时间刷新不计算 k；隐藏兼容回调不属于普通链路 | 研究用核心、既有字段和公共接口保留 | 强制隐藏 rate 列并检查 `include_kinetics=False`；P5 再清理退役 UI |
| DFT/QC | 无独立工作区；止于事件绑定的 DFT Initial Geometry 导出和必要检查 | geometry/readiness 核心保持，普通通道不调用 QC 预检 | `export-dft-geometry` 与 API 保留 | 既有 geometry/readiness 测试；不新增 TS/IRC、调度或理论速率 |

普通侧栏最终只有五个工作区：数据集、物种与趋势、反应与事件、轨迹与谱系、对比。
内部页面仍使用既有 ID；`PAGE_WORKSPACES` 决定内部页恢复时高亮哪个工作区。

## 3. 固定真实案例

源修订指纹由 Dataset Candidate 的有界文件描述符（kind、size、mtime）生成。它用于本轮
复核是否仍是同一批工件，不表示内容哈希；任何指纹变化都必须先重新核验预期。

### A：目标标签与原始结构

目标连接图：`O=C1C=CC=C1Cl`。四个 iter32 timed-evidence 来源及本轮修订：

| 温度 | Dataset ID | 源路径 | 源修订指纹 |
| --- | --- | --- | --- |
| 2000 K | `f50301af725e47ea8d9c` | `/home/huangchen/cal_proc/production_md/runs/phi1_2000K_iter32_000_seed256788_20260914/rng_timed_hdf5_perf_20260916_retry1/trajectory.lammpstrj` | `7844d73dbd79f0ef4d1e942e87c95f2596bf8c1895c3c05d443b0ff8af19ee32` |
| 2500 K | `1044f75c314b4383a040` | `/home/huangchen/cal_proc/production_md/runs/phi1_2500K_iter32_000_seed256788_20260914/rng_timed_hdf5_perf_20260915/trajectory.lammpstrj` | `eaf7a1bd2f34319375d6c3cc4ad457ba792325b979622eb18139de9c0f376a12` |
| 3000 K | `b2aca6b9ec9b475eae61` | `/home/huangchen/cal_proc/production_md/runs/phi1_3000K_iter32_000_seed256788_20260914/rng_timed_hdf5_perf_20260916/trajectory.lammpstrj` | `c7e67d6f7b16ac6ba50e7ddf9869e3d938567419e8a93108b7520c159027b7b4` |
| 3500 K | `23dc6596f729493c978d` | `/home/huangchen/cal_proc/production_md/runs/phi1_3500K_iter32_000_seed256788_20260914/rng_timed_hdf5_perf_20260916/trajectory.lammpstrj` | `ccfefcd2dd2ae71daa6f8d4fd8df901e20bb5a07041d5fd34f0365b1c05de4b9` |

固定参数：`miso=1`、`runHMM=false`、`stepinterval=1`；四组均为 250001 个采样帧、
10 fs 间隔。预期：2500/3000/3500 K 的原始分子键级证据命中目标，2000 K 不命中；
RNG 代表标签未命中不能直接表述为真实结构不存在。复核依据：
本地 `analysis_outputs/chlorocyclopentadienone_20260918/miso_assessment.md`。

### B：目标生成事件

来源为上表 iter32 2500 K 修订。精确 RNG Species 标签：
`[H][C]1[C]([H])[C]([O])[C]([Cl])[C]1[H]`；`timestep_ps=0.0001`，
事件时间取 after frame。固定预期为 19 条生成 Reaction Type、269 条 matched 生成记录、
4 组五碳集合，且能下钻事件 `rngevt_72544_6f2b9e11023f` 等具体记录。269 条记录和
4 组碳集合均不得称为独立实验或正式 Formation Episode 数。复核依据：
本地 `analysis_outputs/target_generation_20260918/report.md`。

### C：分裂重并与继续追踪分支

来源仍为 iter32 2500 K 修订。固定实例锚点为 Scope/LAMMPS 1-based atom IDs
`301,302,303,304,305`，从 `rngevt_72544_6f2b9e11023f` 向后；每段
`depth_backward=20`、`max_molecule_nodes=500`。真实报告中的四组查询分别为
17/1/16/30 段，前三组停止于 `2cp_label_reached`，第四组停止于 `chunk_limit`。
必须保留碳片段分离再重并及全部分支；预算耗尽表示未完成，不表示无上游。

### D：模型迭代比较

主要来源与修订：

- iter25 2000 K `.../1ER_2000K_rep1/rng/2CP_O2_1ER.lammpstrj`，
  指纹 `1e46e0a63d6173ba5a66f0701358bb20ea8c91a62f638d0a215ae4aa0b46e369`。
- iter25 2500 K `.../1ER_2500K_rep3/rng/2CP_O2_1ER.lammpstrj`，
  指纹 `fb011f1053da07a0d654005ce994ea450bb8c63b27ca7ebec85a70b7a0d20a77`。
- iter32 2000/2500 K 使用案例 A 对应修订；3000/3500 K 只作补充。

网络比较保持方向、完整计量和共参与物；载体筛选为每侧一个 C≥2、C5/C6、Cl1，其他
参与物至多 C1，最长 12 步、最多 20000 条最短连接。预期：2500 K 两代有相同三步聚合
网络连接，但 iter32 的具体原子谱系不是该最短网络路线；iter25 缺 timed molecular evidence，
不能输出同强度的事件时序/谱系结论，也不能由现有差异自动宣称主机理改变。复核依据：
本地 `analysis_outputs/chlorocyclopentadienone_20260918/iteration_comparison/report.md`。

## 4. P1 验证记录

### 自动化回归与启动边界

- Dash 定向回归：
  `uv run --locked pytest -q tests/test_dash_smoke.py tests/test_dataset_switch_callbacks.py tests/test_dataset_selection_ux.py`
  结果为 `143 passed, 2210 warnings in 9.92s`。覆盖五工作区、任务导航、冻结页恢复、
  数据集选择/切换，以及普通通道不展示/不计算表观速率的边界。
- 全量回归：`uv run --locked pytest -q`，结果为
  `546 passed, 2431 warnings in 25.03s`。总数相对 P0 基线净增 3 项，相关新增/更新断言
  集中在 P1 导航、冻结边界和会话恢复；警告仍是既有 `dash_table.DataTable` 弃用提示。
- `./start-reacnet-scope.sh --check` 通过；配置、允许根目录和缺数据状态均能正常报告。
  `/media/huangchen/T3000` 所在的 `/dev/sdb2` 已挂载且可访问，但当前配置的精确子目录
  `/media/huangchen/T3000/rng_data_2500K` 不存在。随后改用用户提供的 iter32 2500 K 数据集
  完成真实加载和有界通道复核，见下文。
- `uv run --locked reacnet-scope --help` 仍列出 P0 记录的全部 13 个命令，没有删除或改名
  公共 CLI 入口。
- `git diff --check` 通过；本轮 4 个相关文档共检查 18 个本地 Markdown 链接，均存在。

### 真实浏览器

使用 headless Firefox 走查实际 Dash，而非只检查 layout JSON：

- 1440×1000：普通导航和数据集概览均只有五个工作区；冻结工具名称不出现在普通页面。
  “物种与趋势”任务导航只列物种检索、时间演化、元素分布；“反应与事件”只列直接通道/
  反应式和具体事件。普通反应页没有可见表观速率或 `k_app` 控件。
- 请求 390×844 时 Firefox 给出的最小 viewport 为 500×844；五个入口均可见且可点击，
  “轨迹与谱系”正常激活。页面 `scrollWidth=494`、viewport `500`，未出现页面级横向溢出。
- 向 session storage 注入旧 `candidate-paths` 状态并刷新后，状态保持为
  `candidate-paths`，显示“兼容页面 · 已冻结”和返回“反应与事件”按钮，所属工作区正确高亮。
  验收过程中发现并修复了首轮水合的零值 `n_clicks` 覆盖恢复页竞态；导航初始调用和零点击
  通知现在不会改写已恢复的 `page-store`。
- 新会话与普通点击路径均无 Dash error overlay。浏览器验收使用临时截图复核，截图不进入仓库。
- 在新会话中通过“选择本地数据集”实际选择并加载
  `/home/huangchen/cal_proc/production_md/runs/phi1_2500K_iter32_000_seed256788_20260914/`
  `rng_timed_hdf5_perf_20260915`。切换后顶栏显示该数据集，event、trajectory 和
  composition 均为 ready，五个工作区均可用，页面无 Dash error overlay。
- 该来源修订指纹为
  `eaf7a1bd2f34319375d6c3cc4ad457ba792325b979622eb18139de9c0f376a12`，与固定案例 A/B 一致。
  已发布索引报告 250001 个轨迹帧、250001 个组成时点、8201 个精确物种、
  169594 条事件和 18496 个 Reaction Type；本次只读消费现有索引，未重建索引或
  改写 RNG 原始工件。
- 候选预览阶段一度将 trajectory 显示为缺少来源，但提交切换后当前数据集的
  trajectory 索引为 ready，并可报告 250001 帧。这是候选能力预览与已发布
  workspace 状态的展示不一致；不影响本次加载，但应在后续数据集选择改善中处理。

### 固定案例状态

P0 已固定案例 A–D 的来源修订、参数和预期；P1 没有改写 RNG 原始工件、索引 schema、
科学身份或结果 schema。本批没有完整重跑四个科学案例；除下列 A/B 有界复核外，不把
历史报告计数当作本次运行结果：

- 案例 A、B：真实 2500 K 修订已加载。以分子式 `C5H3ClO` 搜索得到 68 个 RNG
  Species 候选，目标精确身份 `[H][C]1[C]([H])[C]([O])[C]([Cl])[C]1[H]` 排名第一；
  对该精确身份的直接通道查询返回 18 个消耗 Reaction Type、19 个生成 Reaction Type，
  `tp_consume=269`、`tp_produce=269`。TP 仅是聚合转移计数，不是速率、产率或独立事件数。
  精确身份解释与事件到轨迹闭环仍留给 P2 完整验收。
- 案例 C：来源修订和分段预算已固定；继续追踪分支与停止原因留给 P3 实际验收。
- 案例 D：iter25/iter32 来源修订已固定；可比性与统一来源选择留给 P4 实际验收。

因此，P0–P1 的交付结论限于“范围已固定、普通产品表面已收敛、兼容边界与启动/浏览器
链路可运行”。它不提前宣称 P2–P4 的科学主线和真实案例已经完成。
