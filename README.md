# reacnet-scope

`ReacNet Scope` 是面向 ReacNetGenerator 输出结果的交互式后处理与分析软件，主要用于解析和管理反应分子动力学模拟中生成的物种与反应事件，并提供物种检索、有界候选路径发现、明确反应序列的证据验证和时间演化分析等功能，从而提升 ReacNetGenerator 结果的可查询性、可解释性和应用效率，并为复杂反应机理分析及实验质谱结果解释提供辅助支持。

当前支持的主要输出包括 `.reactionabcd`、`.species`、原生
`.timeline.h5`、`.lammpstrj`，以及兼容保留的 `.reactionevent.csv`、`.molecules.csv`：

- Web 前端：分子式/SMILES/质量数检索、结构渲染和时间曲线绘图
- Web 前端：精确物种的直接生成/消耗通道、事件时间与证据交接
- Web 前端：RNG 事件检索、参与原子与键展示和索引化局部轨迹提取
- CLI：批量检索、候选路径发现、明确路径验证、事件证据包、TOP-N 统计、曲线绘制

它的核心定位是反应 MD 后处理与 ReacNetGenerator 输出解析；质谱实验解释是下游对接场景，而不是把本项目做成峰检测、色谱处理或通用质谱软件。

当前产品范围、领域语义、功能契约和发布验收以
[`docs/software-design-baseline.md`](docs/software-design-baseline.md) 为准；较早的日期化设计稿和实施计划仅作为历史资料保留。

## Description

`reacnet-scope` is a data-driven analysis toolkit for aligning reactive MD results with experimental interpretation.  
It parses ReacNetGenerator outputs and provides integrated query, filtering, and visualization workflows across:

- Species lookup by formula, SMILES, and mass (nominal/exact).
- Sampled candidate-path discovery from one or more exact Species.
- Exact Reaction Type sequence verification against event evidence.
- Time-series plotting from species files with formula/SMILES aggregation.
- Generic element-distribution evolution from indexed `.species` files or tidy tables.
- SMILES structure rendering, event evidence inspection, and local trajectory auditing in a lightweight web UI.

The project includes both CLI and web interfaces so the same core logic can be used for scripted batch analysis and interactive exploration.

## 目录结构

- `reacnet_scope/`：领域对象、数据集发现、索引、查询、事件路径验证和导出核心逻辑
- `scripts/webapp_dash/`：推荐使用的 Dash Web 界面
- `scripts/rng_query_cli.py`：终端检索入口
- `tests/`：自动化测试；`examples/`：最小数据与可复现实例
- `docs/`：专题说明与设计记录；`deploy/`：远程部署配置示例

## 快速开始

1. 安装依赖

```bash
uv sync
```

源码开发环境默认包含完整测试栈，以及 Web 和轨迹适配依赖，因此
`uv run pytest -q` 在干净环境中即可运行全量测试。仅部署精简运行环境时，
显式排除开发组并选择发布 extras：

```bash
uv sync --locked --no-dev --extra web --extra trajectory
```

2. 启动 Dash Web（推荐）

```bash
./start-reacnet-scope.sh
```

脚本会自动使用项目的 `.venv`、开放当前用户的主目录与
`/media/$USER:/data:/mnt`，并启动 `http://127.0.0.1:8060`。如果虚拟环境尚未
创建，脚本会先根据 `uv.lock` 完成安装。运行
`./start-reacnet-scope.sh --check` 可以只检查配置而不启动服务；额外挂载点可通过
`REACNET_SCOPE_EXTRA_ROOTS` 追加，多个目录用冒号分隔。

启动脚本默认启用紧凑导航；如需经典布局，可运行
`REACNET_SCOPE_COMPACT_NAV=0 ./start-reacnet-scope.sh`。

远程部署时，目录浏览器看到的是服务端文件系统，实际数据挂载点必须包含在允许
根目录中。

### 加载 ReacNetGenerator 数据集

点击“选择数据”，打开包含 RNG 处理结果的文件夹。软件自动识别该目录中的结果文件，
单一运行直接点击“开始分析”；多套结果时先选择本次要分析的一套。默认不递归合并子目录。
所有来源仍受 `REACNET_SCOPE_ALLOWED_ROOTS` 限制；远程部署选择服务器目录，本地运行选择本地目录。

文件明细默认只读展示。跨目录补充、手动调整文件选择和包含子目录等例外操作放在高级选项中。
同一角色存在多个来源时必须明确处理，不按目录位置推断同一次运行。原始文件不移动、不复制、不修改。

成功后直接进入对应分析页；丰度和事件索引按页面需要在后台准备，匹配的已有索引直接
复用。准备期间提交的丰度、元素分布或事件查询会在准备成功后执行一次。可以取消准备，
或打开“数据集”查看任务进度和手动维护索引。“补充当前数据集”可添加缺少的证据；
已登记文件集合保留身份。最近成功使用的数据集保留在浏览器中，重新打开时会校验来源。

Dash 默认进入“物种与趋势”，普通入口固定为五个工作区：

- **物种与趋势**：物种检索、结构、时间演化和元素分布。
- **反应与事件**：直接生成/消耗通道、反应式、时间分布和具体 RNG 事件。
- **轨迹与谱系**：局部帧、键变化、分子分支追踪和事件证据导出。
- **数据集**：Current Dataset、来源能力和派生索引准备。
- **对比**：逐来源目标和多条件/重复比较，不改变 Current Dataset。

主线从一个精确 RNG Species 开始：查看直接通道，将完整 Reaction Type 交给事件页，选择
稳定 `event_id` 后进入轨迹页并导出证据包。Candidate Path Discovery、Path Verification 和
Species Fate Analysis 不再挂载独立 Dash 页面；公共 CLI、Python API 和既有导出继续兼容。
旧页面 ID 只会返回所属工作区，不自动执行退役分析。

直接反应通道保留 TP、逆向 TP、净 TP、事件计数和可用的时间范围；TP 不等于速率常数。
普通 Dash 查询不会隐式计算表观 `k`，也不要求体积准备。已确认的 `timestep → ps` 只用于
明确的时间换算。既有表观速率核心和结果字段作为公共 API 兼容能力保留，但不属于五工作区
主线；需要使用时必须显式调用并自行满足丰度、时间、体积和单位证据条件。

“批量对比”可以直接组合当前数据集与最近加载的数据集，也可以递归扫描一个
或多个包含多条件/多重复模拟的目录（每行一个根目录）。结果按条件组汇总精确反应的检出率、平均 TP、标准差、
平均净 TP 与 95% 置信区间；选中反应可查看各重复实验，表格可按显示列导出 CSV。
为防止不完整结果，任何已选数据源缺失或解析失败都会终止本次比较并明确报错。

CLI/API 的候选路径从当前数据集观测到的有向 Reaction Types 生成，并通过精确 Carried
Species 连接；事件索引只为候选涉及的步骤提供独立证据，不在发现阶段构建全局事件图。
该能力已冻结扩展且不在普通 Dash 中推广。完整输入和证据边界见
[`docs/candidate-path-discovery.md`](docs/candidate-path-discovery.md)。

完整的信息架构、功能归属与后续去重计划见
[`docs/usage-logic-redesign.md`](docs/usage-logic-redesign.md)。

ReacNetGenerator 生成的 schema-1/2 `.timeline.h5` 会被自动识别，其中
Reaction Evidence 是事件检索所需能力，Molecular Evidence 是原子、键与物理
timestep 的增强证据。schema 2 的逐事件 Transition Evidence 会被直接用于索引，
无需重建完整的帧—原子成员矩阵。旧版本仍可生成 CSV：

```bash
# 添加到原 ReacNetGenerator 命令
--reaction-event --show-molecule-time
```

原生 timeline/事件 CSV 和大轨迹都必须先在独立进程中建立索引。Dash 查询只读消费
已发布的索引，不会在查询中顺序扫描 HDF5、完整事件 CSV 或轨迹；“数据集”工作区可启动
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

轨迹索引 schema v4 会在离线扫描时同时记录逐帧模拟盒体积，供二阶表观速率估计使用。
旧版轨迹索引需要执行 `reacnet-scope prepare rebuild trajectory /data/case`；轨迹
长度单位仍需由用户确认为 Å，软件不会根据数值静默猜测。

### 事件轨迹查看与 OVITO 复核

“反应事件”页只负责检索和选择 RNG 事件；点击“打开轨迹查看”后进入独立页面。
“轨迹查看”只读取轨迹索引返回的帧字节范围，并由 ASE 处理晶胞、周期边界和最小
镜像重定位。页面中的 3Dmol.js 查看器默认只显示参与原子，也可在“完整上下文 /
参与原子 / 仅反应核”之间切换。成键和断键信息始终来自 RNG 事件证据，不根据
坐标重新猜键。

局部轨迹打开后可从该 Reaction Occurrence 的任一具体反应物或产物启动“分子实例
谱系”。默认以全部非氢原子为锚点，向前/向后各追踪 3 次持久结构变化，并把 5 个
Analyzed Frames 内返回完全相同结构的快速回穿折叠为一段；原始事件始终保留在事件表和
JSON/CSV 导出中。缺少可靠元素映射时不会猜测氢原子对应的 Atom ID，需先确认
Type → Element，或显式选择全部原子/指定 Atom IDs。详细边界见
[`docs/molecule-lineage-analysis.md`](docs/molecule-lineage-analysis.md)。

事件索引先按相邻 Molecular Evidence 帧中的原子连通组追踪参与原子，再约去反应
两侧计量相同的净不变物种，与 RNG 的净反应事件匹配。升级前建立的事件索引需
执行 `reacnet-scope prepare rebuild event /data/case` 后才能使用该关联规则。

原始 dump 只有数值 `type` 时，页面会从当前局部轨迹检测 Type，并为
每个 Type 提供可搜索的元素下拉框。点击“应用设置并重新提取”即确认该映射，
设置会保存到当前数据集的 Dataset Workspace；轨迹自带 `element` 列时始终优先使用
原始元素。

点击“下载事件包 ZIP”可得到一个确定性、可复核的最小证据包：

- `event.json`：事件内容、来源签名、原子分组/映射和轨迹提取参数；
- `frames.csv`：逐帧 source timestep、可选 ps、原始/显示坐标、晶胞/PBC 和确认单位；
- `changed_bond_distances.csv`：RNG 证据中发生键变化的原子对在每帧的几何距离；
- `trajectory.lammpstrj`：当前原子范围的局部轨迹；
- `trajectory.extxyz`：元素映射完整时提供，保留晶胞/PBC 和原子 ID；
- `bonds.csv`：来自 RNG 事件证据的成键、断键与未变键；
- `README.txt`：来源、坐标处理、限制和 ASE/OVITO 打开命令。

轨迹页还可单独下载帧 CSV 和键变距离 CSV。只有数据集已经确认
`timestep → ps` 换算时才写入 `time_ps`；只有坐标长度单位已经确认为 Å 时，距离
单位才写为 `angstrom`，否则相应单位字段保持空白。距离只针对 RNG 证据中发生
形成、断裂或键级变化的原子对计算，不据此推断中间帧键级。

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

### DFT 初始几何导出

对具有精确 Molecular Evidence 的 `matched` Reaction Occurrence，轨迹页面会显示
独立的“DFT 初始几何”区域。反应物固定来自事件的 `before_timestep`，产物固定
来自 `after_timestep`。可按两侧的具体 Molecule Instance 自选，并合并为反应物/
产物复合物、逐分子导出或同时生成两种文件。

导出器按侧别键图通过 PBC 重建完整分子，保留多分子反应接触的相对位置，再把
非周期分子簇整体居中。它不旋转、优化或修键，也不会把中间轨迹帧声明为过渡态。
元素映射必须完整，并需要确认源轨迹坐标单位为 Å。普通几何构建 API 仍可把电荷和
自旋多重度留作 `unspecified`；量化交接预检要求两侧合并几何都由用户明确填写，
软件不会自动猜测。

页面和 CLI 输出 `blocked / needs_input / review_required / ready`，不计算分数。
`blocked` 与 `needs_input` 不生成交接 ZIP；`review_required` 需要显式确认警告。
`ready` 只表示可把 occurrence 包交给外部 TS optimization/frequency/IRC 流程，
不表示已验证基元步骤、可直接计算速率或适用气相 TST/RRKM。

正式 ZIP 包含所选 XYZ、`manifest.json`、`atom_map.csv`、`README.txt`、
`reaction_readiness.json` 和 `occurrence.json`。检查报告绑定 Dataset Identity、
source revision、Replicate、event_id 和两侧 Atom IDs；这是派生交接包，不会改变
现有事件证据包。终端可导出同一格式：

```bash
uv run reacnet-scope export-dft-geometry \
  --case /data/case \
  --event-id EVENT_ID \
  --reactants all \
  --products all \
  --layout both \
  --type-map '1=C,2=H,3=O' \
  --source-unit angstrom \
  --state reactants=0,1 \
  --state products=0,1 \
  --confirm-isolated-cluster \
  --replicate replicate-01 \
  --out EVENT_qc_handoff.zip
```

`--reactants` 和 `--products` 也接受 `none` 或从 1 开始的分子序号（例如
`1,3`）。使用 `--save-unit-confirmation` 可把 Å 确认保存到当前 Dataset
Workspace；以后可省略 `--source-unit`。命令默认不覆盖已有文件，覆盖需要
显式传入 `--force`。`--state` 的键必须与实际输出 XYZ 文件名（去掉 `.xyz`）
完全一致；例如逐分子文件 `reactant-01-atoms-1-12.xyz` 使用
`--state reactant-01-atoms-1-12=0,2`，未知或重复键会被拒绝。
出现 `review_required` 时，人工复核报告中的全部警告后可传
`--acknowledge-review` 导出；该确认不会把状态改写为 `ready`。

确定性以相同来源签名、路径、版本和导出参数为范围。manifest 保留绝对来源路径
用于审计；在线导出不会为了跨目录副本生成内容哈希而扫描整条大型轨迹。

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

当前普通 Dash 以“精确 Species → 直接通道 → RNG 事件 → 局部轨迹/事件包”为主要分析
链路。CLI/API 继续提供“精确起始 Species → 有界候选路径发现”和“明确 Reaction Type
序列 → 路径验证”两个彼此独立的兼容工作流；它们不挂载独立 Dash 页面。机理网络、网络
连通即路径和自动确认机理不属于当前版本。

“机理网络”仅保留为未来候选功能名称，不预设数据模型、界面或导出格式。

元素分布索引以流式方式读取大型 `.species` 文件，把每个 timestep 压缩为
`元素计数字典 → 数量`，同时保存每个物种的全程峰值和原始行字节偏移；Dash
绘图不再回扫 `.species`。用户指定参考物种时，系统按其精确 SMILES 从索引
按需读取时间序列；点击下钻时只读取一个时间点并查询峰值摘要，避免将数千万
条物种记录展开为内存 DataFrame。
`reacnet-scope prepare` 提供 `status`、`build`、`rebuild`、`cancel` 和
`clear`；能力为 `event`、`trajectory`、`element-distribution` 或 `all`。
取消会保留最近的构建检查点。Route 准备模式和独立旧入口已删除。

在 Dash 的“数据集”工作区中，“索引构建与状态”默认展开；基础检索无需等待。
需要物种时间演化、事件、轨迹帧或元素分布能力时，可建立、续建或重建对应索引，
并查看 Dataset Workspace 位置、占用空间和等价 CLI 命令。运行或失败的后台任务
直接显示，已完成任务收进历史记录。默认 Workspace 位于数据集 sidecar；只读、
共享或远程来源回退到平台用户工作区，也可显式设置
`REACNET_SCOPE_CACHE_DIR`。清理不会修改 RNG 原始输出。

3. 查看 CLI 帮助

```bash
uv run reacnet-scope --help
```

4. 指定 reactionabcd 文件查询

```bash
uv run reacnet-scope species --reac /path/to/xxx.reactionabcd --formula C6H4
```

## 默认输入文件规则

默认会按以下顺序寻找 reactionabcd：

1. 环境变量 `RNG_REACTION_FILE`
2. `../datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`（相对本工具目录上一级）
3. `<tool_root>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`
4. `<cwd>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`

建议在跨项目使用时显式传 `--reac` 或设置 `RNG_REACTION_FILE`。

## 有界候选路径发现

`reacnet-scope candidate-paths` 从一个或多个精确起始 Species 出发，在当前数据集观测到的
有向 Reaction Type 网络上执行有界局部展开，以具有单步局部原子继承证据的精确 Carried Species 连接步骤，再对候选涉及的
Reaction Types 查询逐步事件证据。原子连续 Event Path 留给独立的路径验证。

```bash
uv run reacnet-scope candidate-paths \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --reac /data/case/rep1/run.lammpstrj.reactionabcd \
  --start 'CCO' \
  --start '[OH]' \
  --min-steps 2 \
  --max-steps 4 \
  --out-json candidate-paths.json
```

可选 `--energy-csv` 输入需要 `reaction_key,score` 两列，其中 `score` 是用户按同一口径
归一化到 `[0,1]` 的能量评分。详细契约见
[有界候选路径发现](docs/candidate-path-discovery.md)。

## 时间有序、原子连续的路径验证

`reacnet-scope verify-path` 在已准备的事件索引上把每个具体 RNG 事件作为节点，
只连接“严格更晚、同一精确分子实例、第一次后续消费”的事件；三事件路径还要求
至少一个原子 ID 贯穿两条边。它会统计独立 `(重复实验, 原子 ID)` 谱系支持、
事件时间间隔和跨重复复现率。它只检查用户明确给出的反应序列，不搜索其他路径。

```bash
uv run reacnet-scope verify-path \
  --source rep1=/data/case/rep1/run.lammpstrj \
  --source rep2=/data/case/rep2/run.lammpstrj \
  --reaction 'A + B -> C' \
  --reaction 'C -> D' \
  --out-json path-verification.json
```

其中 `/data/case/...` 是路径占位符；请替换为真实公共前缀。例如仓库自带数据可用
`--source rp3="$PWD/ref_data/rng-test-rp3-0523/rp3.lammpstrj"`。

该分析必须使用含 Molecular Evidence 的原生 `.timeline.h5` 索引，或同时含
`.reactionevent.csv` 与 `.molecules.csv` 的兼容索引；只有事件时间而没有原子/分子
实例映射时不会降级为物种名称拼接。完整语义、统计字段和
边界说明见 [时间有序、原子连续的事件路径](docs/event-path-analysis.md)。

普通 Dash 不再挂载独立路径验证页；请使用上述 CLI 或对应 Python API。旧
`pathway` 会话只返回“反应与事件”工作区，不自动重跑验证。结果仍明确区分
“有证据 / 未观察到 / 证据不足”，并保留 JSON/CSV 兼容导出。

## 候选路径工作区（第一版）

在 **反应与事件 → 候选路径** 中选择“起点到目标”或“从起点探索”。输入分子式或精确 RNG
SMILES 检索后选择具体结构；多个同分子式结构不会自动合并。物种详情也提供“从该物种探索”
和“寻找生成路线”。

结果按步数优先展示，可勾选 2–3 条路线比较，查看完整计量与每步独立事件，分页后送入现有事件
工作区核查轨迹。不同分子分别支持的步骤允许连接；连续 MD 历史标为“未检查”，次数不代表产率
或整条路线出现次数。已知 miso=1 时按 RNG 代表标签解释，不声称精确键级已经核查。

候选搜索要求事件索引包含新增邻接目录，不要求分子关联。旧事件索引仍能支持原有功能；请在
“数据集”中显式重建事件索引以启用本功能。缺证据、需准备、约束内无结果和预算截断分别提示。
查询不会扫描原始大文件或自动建索引。

同一第一版核心可通过 Python `reacnet_scope.search_candidate_paths` 或 CLI 使用：

```bash
uv run --locked reacnet-scope candidate-search \
  --source /data/run.lammpstrj.timeline.h5 \
  --start '精确起始 RNG SMILES' --target '精确目标 RNG SMILES' \
  --max-steps 4 --max-paths 20 \
  --out-json candidate-paths.json --out-csv candidate-steps.csv
```

这里的路径与 SMILES 是占位符。探索模式使用 `--mode explore` 并省略 `--target`。
CSV 事件源可加 `--molecules`，须与准备索引时的来源一致。
旧 `candidate-paths` 命令保留兼容，和本版 `candidate-search` 的 schema/排序口径不同。

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

- 顶部 `Reaction(.reactionabcd，可选)`：仅用于网络检索类模块（分子式/质量/路径/公式反应）。
- Species 时间演化和 Element Distribution 查询使用当前数据集的 `.species` 来源，不依赖 `reactionabcd`。
- 单文件输入（`Species 文件`）支持两种后缀：
  - `.species`：直接读取
  - `.reactionabcd`：自动转为同名 `.species`
- 多文件输入（`多文件对比`）每行格式统一为：
  - `system@replicate::/abs/path/file.species`
  - `system@replicate::/abs/path/file.reactionabcd`（自动转 `.species`）
- 示例清单见 [`examples/multi_species_sources.example.txt`](examples/multi_species_sources.example.txt)。

在“Species 时间演化”页重绘多温度物种消耗曲线：

1. 展开“数据源”，将不同温度/重复实验的文件逐行粘贴到“多文件列表”；
2. 点击“读取物种目录”，应用会从已准备的 Species Abundance Index 合并分子式目录；
3. 在可搜索的多选框中选择一个或多个分子式，再点击“绘制”；
4. 每个来源文件会保留独立曲线，图例使用清单中的 `system@replicate` 标签。

物种目录和曲线查询只读取预建索引，不在交互请求中扫描完整 `.species` 文件。
如果页面提示索引未就绪，先在数据管理页为相应文件构建 Species Abundance Index。

通用元素分布也可读取 tidy CSV/Excel；至少包含 `time`、`species`、`count`，
可选 `dataset` 或 `system` 列用于多数据集对比。分组元素、元素过滤、原子数分箱、
命名区间和平滑参数由同一核心模型处理。

## 发布到 GitHub/PyPI

- GitHub：提交源码、测试、文档、示例以及 `pyproject.toml` / `uv.lock`；构建产物、运行日志、缓存和本地环境均由 `.gitignore` 排除。
- PyPI：`pyproject.toml` 已配置 CLI、Dash、索引准备与 Dataset Workspace 管理命令入口。

## 开发与验证

```bash
uv sync --locked
uv run --locked pytest -q
uv build
```

### 选择数据

数据在哪里，就在哪里运行软件：服务器部署选择服务器文件，本地运行选择本地文件。
在浏览器中打开 RNG 输出文件夹，软件自动识别结果文件；只有一套结果时直接点击“开始分析”，多套结果时先选择要分析的一套。
直接读取目录中的原文件，无需上传服务或输入存储配置。跨目录补充等操作保留在高级选项中。
