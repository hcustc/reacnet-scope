# reacnet-scope

`ReacNet Scope` 是面向 ReacNetGenerator 输出结果的交互式后处理与分析软件，主要用于解析和管理反应分子动力学模拟中生成的物种与反应事件，并提供物种检索、反应路径追踪、中间体候选筛选和时间演化分析等功能，从而提升 ReacNetGenerator 结果的可查询性、可解释性和应用效率，并为复杂反应机理分析及实验质谱结果解释提供辅助支持。

当前支持的主要输出包括 `.reactionabcd`、`.species`、原生
`.timeline.h5`，以及兼容保留的 `.reactionevent.csv`、`.molecules.csv`：

- Web 前端：分子式/SMILES/质量数检索、结构渲染、时间曲线绘图、中间体候选筛选
- Web 前端：RNG 事件检索、参与原子与键展示和索引化局部轨迹提取
- CLI：批量检索、候选路径与原子连续实际事件路径、事件证据包、TOP-N 统计、曲线绘制

它的核心定位是反应 MD 后处理与 ReacNetGenerator 输出解析；质谱实验解释是下游对接场景，而不是把本项目做成峰检测、色谱处理或通用质谱软件。

当前产品范围、领域语义、功能契约和发布验收以
[`docs/software-design-baseline.md`](docs/software-design-baseline.md) 为准；较早的日期化设计稿和实施计划仅作为历史资料保留。

## Description

`reacnet-scope` is a data-driven analysis toolkit for aligning reactive MD results with experimental interpretation.  
It parses ReacNetGenerator outputs and provides integrated query, filtering, and visualization workflows across:

- Species lookup by formula, SMILES, and mass (nominal/exact).
- Reaction-pathway search by species or formula-level equations.
- Time-series plotting from species files with formula/SMILES aggregation.
- Generic element-distribution evolution from indexed `.species` files or tidy tables.
- Intermediate candidate mining using abundance, rise-fall behavior, and lifetime criteria.
- SMILES structure rendering, event evidence inspection, and pathway auditing in a lightweight web UI.

The project includes both CLI and web interfaces so the same core logic can be used for scripted batch analysis and interactive exploration.

## 目录结构

- `reacnet_scope/`：领域对象、数据集发现、索引、查询、路径分析和导出核心逻辑
- `scripts/webapp_dash/`：推荐使用的 Dash Web 界面
- `scripts/rng_query_cli.py`：终端检索入口
- `tests/`：自动化测试；`examples/`：最小数据与可复现实例
- `docs/`：专题说明与设计记录；`deploy/`：远程部署配置示例

## 快速开始

1. 安装依赖

```bash
uv sync
```

使用 Dash 界面时安装 `web` 可选依赖；需要 ASE 轨迹适配能力时再启用
`trajectory`：

```bash
uv sync --extra web
uv sync --extra web --extra trajectory
```

2. 启动 Dash Web（推荐）

```bash
REACNET_SCOPE_ALLOWED_ROOTS="/home/$USER:/media/$USER:/data:/mnt" \
  uv run reacnet-scope serve --host 127.0.0.1 --port 8060
```

打开 `http://127.0.0.1:8060`。远程部署时，目录浏览器看到的是服务端文件系统；请把实际数据挂载点加入 `REACNET_SCOPE_ALLOWED_ROOTS`，多个目录用冒号分隔。
该变量会替换而不是追加默认允许目录，修改后需要重启 Dash 服务。

### 加载 ReacNetGenerator 数据集

进入侧栏“数据工作区”中的“管理数据”页面，点击“选择其他数据集”。服务器浏览器会标记当前目录中的
ReacNetGenerator 数据集；目录中只有一个数据集时自动选中，存在多个数据集时
按文件名前缀列出候选。选择后点击一次“加载数据集”即可。最近成功加载的十个
数据集会保存在当前浏览器本地，方便再次选择；它们不会自动切换当前数据集。

`base` 是同组 RNG 输出的内部公共前缀，通常无需手动填写。手动路径输入保留在
“手动输入服务器路径”中，可填写数据目录或完整公共前缀；所有路径仍受
`REACNET_SCOPE_ALLOWED_ROOTS` 限制。

Dash 默认进入“物种检索”。分析与数据工作区入口直接显示在左侧栏，不使用下拉菜单；
不同类别以分组关系组织：

- **通用工具**：物种检索、反应式检索、时间演化、反应事件、轨迹查看。
- **自动分析**：中间体候选筛选、候选路径、组成演化。
- **数据工作区**：管理数据、批量对比。

各工具保持独立，页面只显示当前工具名称、数据状态和操作区域。需要继续分析时，
已选物种可以直接检索生成/消耗通道，再将所选通道送入“反应事件”；选中事件后
再打开独立的“轨迹查看”。物种也可作为候选路径起点。数据集选择、文件状态与
派生索引准备集中在独立的“管理数据”页面；批量对比同属侧栏“数据工作区”。

“批量对比”可以直接组合当前数据集与最近加载的数据集，也可以递归扫描包含
多条件/多重复模拟的目录。结果按条件组汇总精确反应的检出率、平均 TP、标准差、
平均净 TP 与 95% 置信区间；选中反应可查看各重复实验，表格可按显示列导出 CSV。
为防止不完整结果，任何已选数据源缺失或解析失败都会终止本次比较并明确报错。

候选路径发现采用两阶段
检索：“多步碎片路径”页面首先只读取 `.reactionabcd`，正式默认为
`3 / 5 / 20 / 5000`；用户明确选择快速小分子预设时才切换为 `4 / 4 / 10 / 300`；
不会在粗筛阶段读取事件或 species 时间索引。选中具体反应后，再按需
进入“反应事件”定位，并在“轨迹查看”中核查。

完整的信息架构、功能归属与后续去重计划见
[`docs/usage-logic-redesign.md`](docs/usage-logic-redesign.md)。

最新 ReacNetGenerator 生成的 schema-1 `.timeline.h5` 会被自动识别，其中
Reaction Evidence 是事件检索所需能力，Molecular Evidence 是原子、键与物理
timestep 的增强证据。旧版本仍可生成 CSV：

```bash
# 添加到原 ReacNetGenerator 命令
--reaction-event --show-molecule-time
```

原生 timeline/事件 CSV 和大轨迹都必须先在独立进程中建立索引。Dash 查询只读消费
已发布的索引，不会在查询中顺序扫描 HDF5、完整事件 CSV 或轨迹；“管理数据”页可启动
使用同一准备命令的独立后台任务：

```bash
uv run reacnet-scope prepare build all /data/case
uv run reacnet-scope prepare status /data/case
```

如果只需要准备事件检索：

```bash
uv run reacnet-scope prepare build event /data/case
```

事件索引 schema v4 始终记录反应式、Transition 和反应物/产物侧 SMILES；
原生 Molecular Evidence 或 `.molecules.csv` 会补充参与原子、键变化和物理
timestep。`.timeline.h5` 的聚合 `count` 会展开为独立逻辑事件，范围数据按 molecule
分组、组内排序并合并后以有界内存和磁盘检查点构建。完整有效的原生文件优先；仅当
它不存在时才回退 CSV，存在但 incomplete/损坏/schema 不兼容时会明确失败。
Dash 查询期间只打开 SQLite 索引，不回扫原始证据。已有旧索引升级后需执行
`reacnet-scope prepare rebuild event /data/case` 一次。

统一命令可准备事件、轨迹以及 `.species` 的通用元素分布索引：

```bash
uv run reacnet-scope prepare build element-distribution /data/case
```

### 事件轨迹查看与 OVITO 复核

“反应事件”页只负责检索和选择 RNG 事件；点击“打开轨迹查看”后进入独立页面。
“轨迹查看”只读取轨迹索引返回的帧字节范围，并由 ASE 处理晶胞、周期边界和最小
镜像重定位。页面中的 3Dmol.js 查看器默认只显示参与原子，也可在“完整上下文 /
参与原子 / 仅反应核”之间切换。成键和断键信息始终来自 RNG 事件证据，不根据
坐标重新猜键。

事件索引先按相邻 Molecular Evidence 帧中的原子连通组追踪参与原子，再约去反应
两侧计量相同的净不变物种，与 RNG 的净反应事件匹配。升级前建立的事件索引需
执行 `reacnet-scope prepare rebuild event /data/case` 后才能使用该关联规则。

原始 dump 只有数值 `type` 时，页面会从当前局部轨迹检测 Type，并为
每个 Type 提供可搜索的元素下拉框。点击“应用设置并重新提取”即确认该映射，
设置会保存到当前数据集的 Dataset Workspace；轨迹自带 `element` 列时始终优先使用
原始元素。

点击“下载事件包 ZIP”可得到一个确定性、可复核的最小证据包：

- `event.json`：事件内容、来源签名、原子分组/映射和轨迹提取参数；
- `trajectory.lammpstrj`：当前原子范围的局部轨迹；
- `trajectory.extxyz`：元素映射完整时提供，保留晶胞/PBC 和原子 ID；
- `bonds.csv`：来自 RNG 事件证据的成键、断键与未变键；
- `README.txt`：来源、坐标处理、限制和 ASE/OVITO 打开命令。

元素映射不完整时仍可下载 ZIP 和 LAMMPS 轨迹，仅省略
`trajectory.extxyz`。也可从终端导出同一格式：

```bash
export REACNET_SCOPE_CACHE_DIR="$PWD/.cache/reacnet-scope"
uv run reacnet-scope export-event \
  --case /data/case \
  --event-id EVENT_ID \
  --scope participants \
  --type-map '1=C,2=H,3=O' \
  --out EVENT_evidence.zip
```

命令默认不覆盖已有文件；需要替换时显式传入 `--force`。页面仍保留独立的
“子轨迹”和“OVITO 脚本”下载；将两者放在同一目录后可运行：

```bash
ovitos EVENT_view_ovito.py EVENT_subset.lammpstrj
```

本地模式还提供用户主动点击的“在 OVITO 中打开”。程序会检测 macOS
App、Windows 常见安装位置和 Linux `PATH`；也可通过
`REACNET_SCOPE_OVITO_EXECUTABLE=/path/to/ovito` 显式指定。远程部署设置
`REACNET_SCOPE_DEPLOYMENT_MODE=remote`，界面只保留下载，不会启动服务器 GUI。

网页查看器固定使用 vendored 3Dmol.js 2.5.5，不依赖浏览器访问 CDN。3Dmol.js
及其所含组件的许可证保存在
[`scripts/webapp_dash/assets/3Dmol-min.js.LICENSE.txt`](scripts/webapp_dash/assets/3Dmol-min.js.LICENSE.txt)。

### 当前范围与未来候选

当前版本以“反应式检索 → 有界候选路径 → RNG 事件 → 局部轨迹/事件包”为主要
分析链路。机理网络尚未形成可执行方案，不属于当前版本、发布验收或近期路线图；
旧方案实现和专项实施计划已经删除。

“机理网络”仅保留为未来候选功能名称，不预设数据模型、界面或导出格式。若以后
重新启动，应基于届时确认的用户需求重新立项和设计，不恢复旧实现。

元素分布索引以流式方式读取大型 `.species` 文件，把每个 timestep 压缩为
`元素计数字典 → 数量`，同时保存每个物种的全程峰值和原始行字节偏移；Dash
绘图不再回扫 `.species`。用户指定参考物种时，系统按其精确 SMILES 从索引
按需读取时间序列；点击下钻时只读取一个时间点并查询峰值摘要，避免将数千万
条物种记录展开为内存 DataFrame。
`reacnet-scope prepare` 提供 `status`、`build`、`rebuild`、`cancel` 和
`clear`；能力为 `event`、`trajectory`、`element-distribution` 或 `all`。
取消会保留最近的构建检查点。Route 准备模式和独立旧入口已删除。

在 Dash 的“管理数据”页面中，可见的“派生索引”区域会显示基础分析、事件、
轨迹帧和元素分布索引状态、占用空间与 Dataset Workspace 路径，并每 2 秒刷新
检查点进度。可直接建立、续建、重建或按类型清理索引，也可复制完全等价的 CLI
命令。默认 Workspace 位于数据集 sidecar；只读、共享或远程来源回退到平台用户
工作区，也可显式设置 `REACNET_SCOPE_CACHE_DIR`。清理不会修改 RNG 原始输出。

3. 查看 CLI 帮助

```bash
uv run reacnet-scope --help
```

4. 指定 reactionabcd 文件查询

```bash
uv run reacnet-scope species --reac /path/to/xxx.reactionabcd --formula C6H4
```

## 候选路径分析

CLI 子命令 `reacnet-scope candidate-paths` 和 Dash“多步碎片路径”页面可以从精确 SMILES
出发，按网络净通量、方向性和可用的 RNG 事件证据检索并排序有界候选路径。
Dash 可把 C1–C4（或用户指定的最大碳数）设为搜索目标，并在结果中分别显示
焦点终点、末步反应的全部物种、小分子碎片和终止原因；达到深度上限不会被
标成真实终产物。
这里的“路径”是用于后续核查的候选路线，不是已经确认的原子连续反应机理；
事件索引缺失时会明确降级为 `network_only`，不会在交互请求中扫描事件 CSV
或自动构建索引。

评分公式、搜索边界、事件索引准备、CLI 导出和 Dash 事件跳转的完整说明见
[`docs/pathway-analysis.md`](docs/pathway-analysis.md)。

## 默认输入文件规则

默认会按以下顺序寻找 reactionabcd：

1. 环境变量 `RNG_REACTION_FILE`
2. `../datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`（相对本工具目录上一级）
3. `<tool_root>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`
4. `<cwd>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`

建议在跨项目使用时显式传 `--reac` 或设置 `RNG_REACTION_FILE`。

## 时间有序、原子连续的实际事件路径

`reacnet-scope event-paths` 在已准备的事件索引上把每个具体 RNG 事件作为节点，
只连接“严格更晚、同一精确分子实例、第一次后续消费”的事件；三事件路径还要求
至少一个原子 ID 贯穿两条边。它会统计独立 `(重复实验, 原子 ID)` 谱系支持、
事件时间间隔、跨重复复现率，并在 `.reactionabcd` 可用时列出聚合网络可达但
轨迹中没有实际发生的路径。

```bash
uv run reacnet-scope event-paths \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --source rep2=/data/case/rep2/run.lammpstrj \
  --out-json event-paths.json
```

其中 `/data/case/...` 是路径占位符；请替换为真实公共前缀。例如仓库自带数据可用
`--source rp3="$PWD/ref_data/rng-test-rp3-0523/rp3.lammpstrj"`。

该分析必须使用含 Molecular Evidence 的原生 `.timeline.h5` 索引，或同时含
`.reactionevent.csv` 与 `.molecules.csv` 的兼容索引；只有事件时间而没有原子/分子
实例映射时不会降级为物种名称拼接。完整语义、统计字段和
边界说明见 [时间有序、原子连续的事件路径](docs/event-path-analysis.md)。

Dash 中可在“反应路径 → ② 验证实际发生”通过四步向导运行同一分析引擎：确认数据、
定义路径、确认并运行、查看结果。当前数据集及事件索引会自动识别；只分析当前数据集
时无需手填路径，只有选择跨重复统计后才需追加 `label=公共前缀`。常用项逐步填写，
时间间隔和审计明细上限收在高级设置中。结果展示路径签名、独立原子谱系支持、跨重复
复现率、聚合/实际对照及具体事件—分子实例—原子 ID 图；选择路径签名后可逐次审计
真实发生记录，并下载 JSON 或 CSV。

## 依赖

- Python 3.10+
- 基础依赖：`pandas`、`openpyxl`、`rdkit`
- 可选绘图增强：`matplotlib`、`scipy`（CLI `species-evolution --out-png` 时需要）
- 可选轨迹适配：`ase`（安装 extra：`trajectory`）

### 使用 uv 安装

仅安装基础依赖：

```bash
uv sync
```

安装基础依赖 + 绘图增强依赖：

```bash
uv sync --extra plot
```

## Element Distribution Evolution

Dash 的“元素分布演化”从数据集发现可用元素。用户选择分组元素和最大原子数，
再以任意元素表达存在、不存在或原子数范围筛选。数据含碳时页面可默认选择 C，
但 schema、查询和控件都不写死 C/O/Cl。

可选参考物种只由用户输入的精确 SMILES 决定；软件不会从丰度推断“母体”。指定
后会同时显示参考物种及相同分组元素数量的其他物种。点击曲线可下钻到分子式、
SMILES、当前数量、峰值数量和峰值时间。

CLI 查询同一个预建索引：

```bash
uv run reacnet-scope element-distribution /data/case \
  --group-element N \
  --max-group-count 8 \
  --filter S=present \
  --filter O=range:1:3
```

### Web 输入规范（统一）

- 顶部 `Reaction(.reactionabcd，可选)`：仅用于网络检索类模块（分子式/质量/路径/公式反应）和中间体 `with_flux=true` 富集。
- Species 时间演化和 Element Distribution 查询使用当前数据集的 `.species` 来源，不依赖 `reactionabcd`。
- 单文件输入（`Species 文件`）支持两种后缀：
  - `.species`：直接读取
  - `.reactionabcd`：自动转为同名 `.species`
- 多文件输入（`多文件对比`）每行格式统一为：
  - `system@replicate::/abs/path/file.species`
  - `system@replicate::/abs/path/file.reactionabcd`（自动转 `.species`）
- 示例清单见 [`examples/multi_species_sources.example.txt`](examples/multi_species_sources.example.txt)。

通用元素分布也可读取 tidy CSV/Excel；至少包含 `time`、`species`、`count`，
可选 `dataset` 或 `system` 列用于多数据集对比。分组元素、元素过滤、原子数分箱、
命名区间和平滑参数由同一核心模型处理。

## 发布到 GitHub/PyPI

- GitHub：提交源码、测试、文档、示例以及 `pyproject.toml` / `uv.lock`；构建产物、运行日志、缓存和本地环境均由 `.gitignore` 排除。
- PyPI：`pyproject.toml` 已配置 CLI、Dash、索引准备与 Dataset Workspace 管理命令入口。

## 开发与验证

```bash
uv sync --extra web --extra trajectory
uv run pytest -q
uv build
```
