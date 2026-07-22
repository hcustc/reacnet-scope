# reacnet-scope

`ReacNet Scope` 是面向 ReacNetGenerator 输出结果的交互式后处理与分析软件，主要用于解析和管理反应分子动力学模拟中生成的物种、反应事件与反应网络数据，并提供物种检索、反应路径追踪、中间体筛选、时间演化分析及网络可视化等功能，从而提升 ReacNetGenerator 结果的可查询性、可解释性和应用效率，并为复杂反应机理分析及实验质谱结果解释提供辅助支持。

当前支持的主要输出包括 `.reactionabcd`、`.species`、`.reactionevent.csv`、
`.molecules.csv` 和 `.lammpstrj.table`：

- Web 前端：分子式/SMILES/质量数检索、结构渲染、时间曲线绘图、中间体候选筛选
- Web 前端：RNG 事件检索、参与原子与键展示、索引化局部轨迹提取，以及 `.lammpstrj.table` 观察网络
- CLI：批量检索、路径导出、TOP-N 统计、曲线绘制

它的核心定位是反应 MD 后处理与 ReacNetGenerator 输出解析；质谱实验解释是下游对接场景，而不是把本项目做成峰检测、色谱处理或通用质谱软件。网络数据协议和交互设计正在按 ReaxTools、NOCTIS、Cytoscape.js 等项目落地；GasRMDKit、ReNView、SCINE Heron 和 RMG-Py 目前作为后续扩展参考，不代表已经完全实现对应功能。

## Description

`reacnet-scope` is a data-driven analysis toolkit for aligning reactive MD results with experimental interpretation.  
It parses ReacNetGenerator outputs and provides integrated query, filtering, and visualization workflows across:

- Species lookup by formula, SMILES, and mass (nominal/exact).
- Reaction-pathway search by species or formula-level equations.
- Time-series plotting from species files with formula/SMILES aggregation.
- Carbon-number evolution plotting from tidy species tables for parent decay,
  fragment growth, molecular growth, and oxidation tracking.
- Intermediate candidate mining using abundance, rise-fall behavior, and lifetime criteria.
- SMILES structure rendering, event evidence inspection, and pathway auditing in a lightweight web UI.

The project includes both CLI and web interfaces so the same core logic can be used for scripted batch analysis and interactive exploration.

## 目录结构

- `run_web.sh` / `run_rng_query_web.sh`: 启动 Web 后端
- `run_cli.sh` / `run_rng_query.sh`: 启动 CLI
- `scripts/webapp/server.py`: Web API 与静态页面服务
- `scripts/webapp/static/`: 前端页面与脚本
- `scripts/rng_query_cli.py`: 终端检索入口
- `rng_tools/`: 反应网络解析与统计核心逻辑

## 快速开始

1. 安装依赖

```bash
uv sync --extra plot
```

2. 启动 Dash Web（推荐）

```bash
uv sync --extra web
REACNET_SCOPE_ALLOWED_ROOTS="/home/$USER:/data:/mnt" \
  uv run ./run_dash.sh 127.0.0.1 8060
```

打开 `http://127.0.0.1:8060`。远程部署时，目录浏览器看到的是服务端文件系统；请把实际数据挂载点加入 `REACNET_SCOPE_ALLOWED_ROOTS`，多个目录用冒号分隔。

生成数据时建议启用 RNG 的事件输出；事件检索直接消费这两个文件，不再从
Route 重建事件：

```bash
# 添加到原 ReacNetGenerator 命令
--reaction-event --show-molecule-time
```

大轨迹仍必须先在独立进程中建立帧偏移索引。Dash 只读消费索引，
不会构建索引，也不会顺序扫描完整轨迹：

```bash
export REACNET_SCOPE_CACHE_DIR=/path/to/nvme/reacnet-cache
uv run reacnet-scope-prepare /data/case
uv run reacnet-scope-prepare /data/case --status
```

统一命令默认只准备轨迹索引；`--route-only` 仅作为旧数据兼容入口。
命令仍支持 `--trajectory-only`、`--status`、`--clear` 和 `--rebuild`；
使用 Ctrl+C 取消时会保留最近的构建检查点。旧的两个
`reacnet-scope-build-*-index` 命令暂时保留为兼容入口。

在 Dash 的“管理数据”窗口中，“数据准备状态”区域会只读显示基础分析、
RNG 事件输出和轨迹帧索引状态，并在离线构建运行时自动刷新检查点进度。
该区域可复制 RNG 输出参数和轨迹准备命令，并可安全清理轨迹索引；
它不会启动构建任务，也不会删除原始 ReacNetGenerator 输出。

旧版静态 Web 界面仍可通过以下命令启动：

```bash
uv run ./run_web.sh 127.0.0.1 8876
```

3. 查看 CLI 帮助

```bash
uv run ./run_cli.sh --help
```

4. 指定 reactionabcd 文件查询

```bash
uv run ./run_cli.sh species --reac /path/to/xxx.reactionabcd --formula C6H4
```

## 默认输入文件规则

默认会按以下顺序寻找 reactionabcd：

1. 环境变量 `RNG_REACTION_FILE`
2. `../datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`（相对本工具目录上一级）
3. `<tool_root>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`
4. `<cwd>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`

建议在跨项目使用时显式传 `--reac` 或设置 `RNG_REACTION_FILE`。

## 依赖

- Python 3.10+
- 基础依赖：`pandas`、`openpyxl`、`rdkit`
- 可选绘图增强：`matplotlib`、`scipy`（CLI `plot --out-png`、Carbon-number evolution plot 时需要）

### 使用 uv 安装

仅安装基础依赖：

```bash
uv sync
```

安装基础依赖 + 绘图增强依赖：

```bash
uv sync --extra plot
```

## `.lammpstrj.table` 观察网络可视化

ReacNetGenerator 新版输出的 `*.lammpstrj.table` 是一个带 SMILES 行列标签的物种转移矩阵，单元格表示来源物种到目标物种的观察事件数。Web 端的 `观察网络` 模块：

- 默认复用顶部导入文件夹中检测到的 Table，也可在 `RNG Table(.lammpstrj.table)` 输入框指定其他文件；
- `矩阵` 视图按事件数对数着色，点击单元格可查看完整 SMILES 与原始事件数；
- `强通道` 视图显示高事件数有向边，节点大小对应总通量；
- 可按最小事件数和显示物种数裁剪结果，并导出主转移通道 CSV。

后端 API 也提供结构化结果：`GET /api/transition_table?table=/path/to/file.table`。

### 观察网络的数据边界

`*.lammpstrj.table` 只有聚合的 Species → Species 观察次数，没有原始事件 ID、原子重叠配对或转移原子明细。因此 API 会明确标注：

- `schema_version: observation-network/v1`
- `model: species_reaction_bipartite`
- `evidence_level: aggregate_observation`
- `audit.status: not_available`

每个非零矩阵单元会被表示为一个 `observed_transition` Reaction 节点，并通过 `reactant_of` / `produces` 连接两个 Species 节点。这是对聚合观察的可追溯表示，不等同于原子级反应事件；真实事件证据由 ReacNetGenerator 的 `.reactionevent.csv` 和 `.molecules.csv` 提供，不再由 Dash 扫描 `.route` 重建。网络视图使用 Cytoscape.js，矩阵视图仍使用 ECharts。

## Carbon-Number Evolution Plotter

`rng_tools.carbon_plot` 提供了一个可复用的碳数演化绘图模块，核心接口包括：

- `parse_formula_to_atom_counts(...)`
- `aggregate_counts_by_carbon_number(...)`
- `summarize_carbon_evolution(...)`
- `plot_carbon_number_evolution(...)`

这个图把所有物种按碳原子数聚合，而不是逐个分子式分别作图，因此更适合回答以下问题：

- 母体碳骨架是否正在被持续消耗
- 小碎片 `C1-C4` 是否正在累积，表征裂解/氧化分解
- 大碳数物种是否出现并增长，表征并聚或 soot precursor 倾向
- 不同气氛下碳骨架是更快向小分子迁移，还是保留/增长为更大分子

最小示例数据和演示脚本位于：

- `examples/carbon_number_evolution_minimal.csv`
- `examples/carbon_number_evolution_demo.py`

运行示例：

```bash
MPLCONFIGDIR=/tmp python examples/carbon_number_evolution_demo.py
```

CLI 已接入独立子命令，可直接从 tidy CSV/Excel 或 RNG `.species` 文件绘图：

```bash
MPLCONFIGDIR=/tmp python scripts/rng_query_cli.py carbon-plot \
  --data examples/carbon_number_evolution_minimal.csv \
  --system-col system \
  --replicate-col replicate \
  --parent-carbon-number 24 \
  --layout subplots \
  --layout-regions 'Small fragments:1-4;Intermediate fragments:5-15;Parent neighborhood:16-30;Growth region:31+' \
  --legend-mode detailed \
  --out-fig /tmp/carbon_plot.png \
  --out-summary /tmp/carbon_plot_summary.json \
  --out-csv /tmp/carbon_plot_data.csv
```

Web 端也已接入新面板：

- 打开 `reacnet-scope-web`
- 在 `时间演化绘图` 中切换 `Species Time-Series / Carbon-Number Evolution`
- 前后端统一走 evolution plot facade（模式 `species | carbon`），内部复用各自引擎
- 可直接从 `.species` 构建碳数演化图并导出 SVG/CSV
- `Display Ranges` 可动态筛选要显示的碳数/区间（如 `C1;C2;C24;C30+`）
- `Merge Ranges` 可把多个碳数区间合并为单曲线（如 `Small:1-4;Growth:30+`）
- 绘图后可在 `Curve Filter / 曲线列表` 中勾选曲线即时重绘，不会重新读取 `.species`
- `Local Merge Ranges` 支持在同一绘图窗口内本地合并曲线做对比
- 默认使用通用 `single` 布局，不再预设 `Small/Intermediate/Parent` 子图；原始 Matplotlib SVG 改为可选展开

### Web 输入规范（统一）

- 顶部 `Reaction(.reactionabcd，可选)`：仅用于网络检索类模块（分子式/质量/路径/公式反应）和中间体 `with_flux=true` 富集。
- `Species Time-Series / Carbon-Number Evolution` 绘图：优先使用模块内的 species 输入，不强依赖顶部 `reactionabcd`。
- 单文件输入（`Species 文件`）支持两种后缀：
  - `.species`：直接读取
  - `.reactionabcd`：自动转为同名 `.species`
- 多文件输入（`多文件对比`）每行格式统一为：
  - `system@replicate::/abs/path/file.species`
  - `system@replicate::/abs/path/file.reactionabcd`（自动转 `.species`）
- 示例清单见 [`examples/multi_species_sources.example.txt`](examples/multi_species_sources.example.txt)。

最小调用示例：

```python
import pandas as pd

from rng_tools import plot_carbon_number_evolution

data = pd.read_csv("examples/carbon_number_evolution_minimal.csv")
fig, ax, summary, plot_data = plot_carbon_number_evolution(
    data=data,
    time_col="time",
    species_col="species",
    count_col="count",
    system_col="system",
    replicate_col="replicate",
    parent_carbon_number=24,
    mode="exact",
    layout="subplots",
    layout_regions=[
        ("Small fragments", 1, 4),
        ("Intermediate fragments", 5, 15),
        ("Parent neighborhood", 16, 30),
        ("Growth region", 31, None),
    ],
    system_mode="facet",
    legend_mode="detailed",
    smoothing={"method": "rolling", "window": 2},
    output_path="carbon_number_evolution_demo.png",
)
```

输入表至少需要三列：

- `time`
- `species`
- `count`

可选列：

- `system`
- `replicate`
- 其他业务列会被保留在原始输入里，但不会参与碳数聚合

## 发布到 GitHub/PyPI

- GitHub：建议提交 `README.md`、`LICENSE`、`pyproject.toml`、`uv.lock`、源码目录（`rng_tools` / `scripts`）。
- PyPI：当前配置已提供可发布元数据与命令入口：
  - `reacnet-scope`
  - `reacnet-scope-web`
