# ReacNet Scope 软件设计基准

状态：已接受
日期：2026-08-03

本文档定义 ReacNet Scope 当前版本的产品范围、领域语义、功能契约和发布验收基准。它不是对现有实现状态的声明；代码是否符合本文档，需要另行审查。

术语以根目录 [`CONTEXT.md`](../CONTEXT.md) 为准，难以逆转的设计决策以 [`docs/adr/`](adr/) 中已接受 ADR 为准。早于本文档的日期化设计稿和实施计划保留为历史资料；与本文档冲突时，不构成当前功能承诺。

## 1. 产品定位

ReacNet Scope 是面向 ReacNetGenerator 输出的反应分子动力学证据工作台。它组织并查询 Species Abundance Evidence、Reaction Evidence 和 Molecular Evidence，帮助用户从聚合反应网络逐步下钻到可复核的具体事件和局部轨迹。

ReacNetGenerator 是 Species、Reaction Type、反应计数和逐时事件的权威生产者。ReacNet Scope 负责索引、查询、关联、统计、证据验证、可视化和导出，不从原始轨迹运行第二套反应检测或通用成键判定。

质谱实验解释是下游对接场景。当前产品不是峰检测、色谱处理、通用 `m/z` 解释或同位素包络软件。

## 2. 正式产品界面与运行平台

正式产品界面包括：

- Dash Web：唯一 Web 产品界面，负责交互选择、可视化和跨工具交接。
- `reacnet-scope` CLI：负责批处理、自动化、可复现导出和服务启动。
- `reacnet_scope` Python API：唯一正式支持的 Python 包和领域实现边界。

旧静态 Web、`rng_tools` 公共包、历史启动脚本和旧 CLI 名称不属于兼容承诺，可以删除。Dash、CLI 和 Python API 不要求控件完全相同，但相同分析必须共享同一核心实现、默认值、领域语义、错误类别和导出字段。

正式平台范围为：

- macOS 与 Windows：本地工作台，包括 Dash、CLI、准备任务、事件包和本地 OVITO 启动。
- Linux：服务器部署和本地运行。
- Python `3.10+`，以项目声明且经测试的依赖范围为准。

所有正式能力必须可通过安装后的 `reacnet-scope` 命令运行，不能要求用户依赖 `.sh` 脚本。路径、工作区、文件锁、后台进程、取消信号和允许根目录分隔符必须使用跨平台 API。

## 3. 核心成功路径

发布必过的核心链路是：

> 加载 ReacNetGenerator 数据集 → 检索 Species/Reaction Type → 定位 Reaction Occurrence → 查看局部轨迹 → 导出可复核事件包

Path Verification 接收用户明确给出的 Reaction Type 序列，并按 Event Path 的时间、分子实例和原子谱系连续性核查具体 Reaction Occurrence。它不发现、补全、评分或排名路径。Event Path 只证明相应事件在现有证据中以规定的连续性发生过，不证明因果、唯一性或完整反应机制。

围绕焦点 Species 的 Direct Reaction Channel 是单步生成/消耗 Reaction Type 查询；它不递归扩展路径。用户可据此形成待验证序列，但软件不会自动组合。

## 4. 正式辅助能力

以下能力是正式功能，但不是核心链路的必经步骤：

- Species 时间演化。
- Element Distribution Evolution。
- Species Fate Analysis。
- 跨 Simulation Condition 与 Replicate 的批量对比。

每项能力必须定义输入、输出、失败行为、来源限制和验收测试。页面能够打开不等于功能已实现。

## 5. 当前非目标

当前版本明确不包括：

- 机理网络、通用自动路径发现/评分、自动确认机理或因果推断。Species Fate Analysis
  仅允许按严格原子连续性遍历已观测 descendant evidence 的有界例外；它不发现、
  补全或评价未观测路径。
- 基于丰度曲线、寿命或通量自动筛选中间体候选；现有规则未经充分验证，不作为产品功能或公共 API 提供。
- 从轨迹重新检测反应或根据坐标覆盖 RNG 键变化。
- `.route` 事件回退、Route 索引或 Route 原子迁移分析。
- 旧静态 Web 和与 Dash 对等的第二套 Web 功能。
- 客户端文件上传、账号/角色、多租户权限或全局项目数据库。
- 集群调度、跨节点计算或分布式索引。
- GIF/MP4 渲染。
- 实验峰检测、加合物/电荷解释、完整同位素包络或通用质谱处理。
- 运行时 CDN、默认遥测或数据上传。

## 6. 领域与身份不变量

### 6.1 Species 与 Reaction Type

- Species 以数据集内 ReacNetGenerator 的精确 SMILES 为身份；分子式与质量只是查询和分组属性。
- 同一分子式对应多个 SMILES 时，必须保留并可下钻到全部具体 Species。
- Reaction Type 是有方向、保留重复计量项的精确 Species 多重集合；同一侧的排列顺序不影响身份。
- 分子式反应检索只用于发现。事件、路径、批量统计和导出必须使用精确 Reaction Type。
- 跨工具交接传递精确 SMILES、Occurrence Identity 或稳定反应键，不能只传显示文字。
- Path Verification 必须接收按顺序排列的完整 Reaction Type；不接受只有起点、终点或路径长度的自动搜索请求。

### 6.2 Reaction Occurrence

- Aggregated Reaction Record 的 `count=N` 展开为 N 个独立 Reaction Occurrence。
- 每个发生尽可能关联到不同的原子连通分子变化；无法匹配的发生保留为 `unresolved`。
- `unresolved` 计入事件统计，但不能打开局部轨迹，也不能支持要求分子实例或原子连续性的 Event Path。
- Occurrence Identity 由 Transition、规范化 Reaction Type、参与原子和必要的确定性重复序号产生，不依赖 CSV 行号、HDF5 布局 ID 或存储顺序。
- 同一已解析发生在兼容证据格式之间迁移时应保持身份稳定。

### 6.3 权威来源冲突

轨迹坐标只用于环境选择、周期边界处理和可视化。成键、断键和 Reaction Type 始终来自 RNG 证据。若 RNG 工件互相冲突，系统报告冲突和受影响的 Analysis Capability，不通过坐标猜测一个替代结论。

将来若接入其他事件生产器，必须保留独立来源标识，不能与 RNG 事件静默合并。

## 7. 正式输入矩阵

| 输入 | 提供的能力 | 规则 |
| --- | --- | --- |
| `.reactionabcd` | 聚合反应网络、反应检索、通量 | 保留方向和化学计量 |
| `.species` | Species Abundance Evidence | 经离线物种丰度索引消费 |
| 完整 `.timeline.h5` | Reaction Evidence 与可选 Molecular Evidence | 首选原生 Timed Evidence Source |
| `.reactionevent.csv` + `.molecules.csv` | 旧数据的事件与分子证据 | 仅在原生文件完全不存在时回退 |
| 对应 LAMMPS 轨迹 | 局部帧、环境和事件包 | 必须先建立轨迹索引 |

原生 timeline 存在但不完整、损坏、被禁用或 schema 不兼容时明确失败，不能静默回退 CSV。缺少某类证据只禁用依赖它的 Analysis Capability，不使整个数据集变成笼统的“不可用”。

`.moname`、`.table`、`.reaction`、`.route` 和 RNG 报告 JSON/SVG/HTML 不构成正式 Analysis Capability，也不在 Dash 请求中读取。未识别文件保持只读并被忽略。

## 8. Dataset、Capability 与 Workspace

### 8.1 Dataset 状态

- 普通分析工具一次只使用一个 Current Dataset。
- 检查 Dataset Candidate 不改变当前上下文；只有用户明确加载后才切换。
- 数据集没有单一“全部就绪”状态。Species、Reaction、Event、Trajectory、Species Fate、Element Distribution 等 Analysis Capability 分别可用。
- 批量对比选择多个数据集，但不会把它们逐个设为 Current Dataset。

### 8.2 Dataset 选择器

- 选择目录或粘贴目录/公共前缀后，按 RNG 文件公共前缀发现 Dataset Candidate。
- 单候选自动选中；多候选要求用户明确选择，不能默认第一个。
- `base` 只作为内部字段，不在普通界面暴露“运行组”概念。
- 发现只检查文件名、存在性和索引元数据，不读取大型源文件。
- 无候选、越界、无权限、路径消失或候选变化时，保留原 Current Dataset。

### 8.3 Dataset Workspace

- 可写本地数据集使用同目录 `.reacnet-scope/`；只读、共享或远程位置回退到平台标准用户工作区，管理员可显式配置集中位置。
- 工作区保存索引、检查点、任务记录和数据集设置，但从不修改 RNG 原始工件。
- 移动目录保留数据集身份；同一身份同时出现在两个活动路径时，副本获得独立身份。
- UI 显示实际工作区位置、占用空间和每项索引状态。
- 清理只删除 ReacNet Scope 派生状态。

## 9. Preparation Task

- 加载数据集只做轻量发现和状态检查，不自动启动重型索引。
- 用户在“管理数据”中显式启动、续建、重建、取消或清理任务，并可复制等价 CLI 命令。
- 可提供显式“准备所有可用能力”，但不能隐藏在加载动作中。
- 同一数据集、源修订和能力最多一个活动任务；重复启动返回已有任务。
- 不同能力可并行，但使用独立锁、临时文件和资源限制。
- 取消保留已提交检查点；`resume` 继续，`rebuild` 明确重新开始。
- 服务重启后重新判定遗留任务；源修订变化后旧任务不得发布。
- 索引原子发布，Dash 只读已发布版本。

## 10. Dash 信息架构与会话

侧栏采用始终可见的工具箱：

- 检索与趋势：物种检索、反应式检索、时间演化、元素分布演化。
- 事件证据：反应事件、轨迹查看、路径验证、物种命运分析。
- 数据工作区：管理数据、批量对比。

工具可以独立进入。跨工具按钮只交接稳定身份和必要上下文，目标工具仍调用统一核心实现。

Current Dataset、页面和工作流选择属于浏览器会话；索引、任务和数据集设置属于 Dataset Workspace。数据集切换清空旧选择。页面恢复前重新验证路径权限、数据集身份和源修订。

## 11. 查询与分析契约

### 11.1 Species 与 Reaction 搜索

- 支持精确 SMILES、分子式、中性标称质量和中性单同位素精确质量查询。
- 精确质量容差使用 Da，显式 `0` 必须保留；结果显示 Da 与 ppm 误差。
- 质量结果可按分子式聚合展示，但必须下钻到全部具体 Species。
- 不静默枚举仅针对 Cl 的同位素组合；通用同位素检索需要独立未来设计。
- Reaction 搜索保留方向和化学计量，提供生成/消耗通道，并可交接 Reaction Occurrence。

#### 11.1.1 Direct Reaction Channel 动力学量

- TP、净 TP、事件频率和 Apparent Rate Constant Estimate 是不同量，不得混称。
- 事件频率只在已确认 `timestep → ps` 且具有完整观察窗时给出，单位明确为每 ps。
- Apparent Rate Constant Estimate 的分子必须来自观察窗内的精确 Reaction Occurrence，正向与逆向分别计算，不使用净 TP。
- 分母使用 Species Abundance Evidence 的左端点反应物丰度暴露量；二阶模型还要求轨迹长度单位确认为 Å，并使用离线轨迹索引准备的逐帧晶胞体积。
- MVP 只支持按反应物化学计量显式声明的一阶和二阶质量作用模型；`2 A` 使用下降阶乘 `n_A(n_A-1)`，不静默加入对称因子。
- 每个估计同时报告模型、事件数、观察时长、暴露量、单位和 Poisson 95% 置信区间，并明确称为“表观 k”。
- 任一时间、丰度、事件或体积证据缺失时保留通道 TP，但不得输出表观 k；页面必须显示具体缺失原因。
- 在线查询只读取 Event Evidence、Species Abundance 和 Trajectory 索引，不扫描原始大型来源。

### 11.2 Species 时间演化

普通 Dash 查询必须读取持久化 Species Abundance Index，不得每次完整扫描 `.species`。索引至少提供物种目录、时间点定位、峰值摘要和按 Species 读取的时间序列。

- 精确 SMILES 表示具体 Species。
- 分子式查询显式选择合计、分别显示或两者，并列出聚合成员。
- 归一化、时间对齐、平滑和降采样都记录参数。
- 平滑与降采样只影响显示，不覆盖原始数值或统计结论。
- CSV 默认导出未经平滑的原始/聚合序列；处理后导出同时记录变换。
- 曲线截断必须显示被省略数量。
- 多数据集对比要求每个来源的相应索引就绪。

CLI 默认复用索引，可提供显式一次性流式模式，并在输出中标明来源模式。

### 11.3 时间轴

- 始终保留 Analyzed Frame 和源 timestep，不能混称为物理时间。
- 只有证据提供转换信息，或用户确认并保存 timestep 到 ps 的换算后，才显示 ps/ns。
- 未确认时默认显示 frame 或 timestep，不使用静默物理时间默认值。
- 换算绑定 Dataset Workspace 并写入导出参数。
- 多数据集物理时间对比要求每个数据集分别具有明确换算。

### 11.4 Path Verification 与 Event Path

- 输入是用户按顺序明确给出的 2–8 个完整 Reaction Type；不从起点物种自动扩展。
- 验证结果是 `supported`、`not_observed` 或 `inconclusive`；只有完整且未截断的遍历可以给出 `not_observed`。
- 节点是 Reaction Occurrence，不是 Reaction Type。
- 边要求时间严格向后、共享同一精确分子实例，并连接到该实例第一次后续消耗。
- 三事件及更长路径要求至少一个原子 ID 贯穿相邻边，形成连续原子谱系。
- 跨 Replicate 统计以“Replicate + 原子谱系”为独立支持单位，报告时间间隔和复现率。
- 缺少 Molecular Evidence 时拒绝分析，不退化为同名 Species 拼接。

### 11.5 Reaction Occurrence 与轨迹查看

- 事件页查询并选择具体 Reaction Occurrence；未解析发生可统计但不可打开轨迹。
- 默认显示全部参与原子，可切换仅反应核或周围环境。
- 周围环境默认 `4.0 Å`、最多 `500` 原子；允许在服务器安全范围内修改，截断时显示原始命中数。
- 轨迹读取只使用预建索引返回的有限帧字节范围。
- ASE 负责晶胞、PBC、最小镜像、支持的坐标约定和重居中。
- 元素优先级：轨迹 `element` 列 → 数据集用户确认映射 → `T<type>`。
- 映射保存在独立数据集设置中，允许部分映射；只有所选原子全部映射时生成 ExtXYZ。
- 缺少轨迹索引或 ASE 时仍可查看事件元数据，但明确禁用轨迹和相应导出。

#### 11.5.1 Molecule Lineage

- 输入必须是某个 Reaction Occurrence 一侧的具体 Molecule Instance，不接受只有 Species 的起点。
- 连续性使用精确 Species、atom-ID 集合和最近可解析事件；结构回穿还要求分子内键集合完全相同。
- 默认锚点为全部非氢原子，缺少可靠 atom ID → element 映射时失败关闭；允许显式改为全部原子、指定元素或指定 Atom IDs。
- 拆分时展开所有保留锚点的分支；合并的无锚点共同反应物和无锚点离去片段只作为上下文。
- 默认向前/向后各 3 次持久变化、最多 100 个分子节点、Fast Recrossing Episode 窗口 5 个 Analyzed Frames；所有边界和证据断点必须写入报告。
- 原始视图、事件表和导出保留全部 Reaction Occurrence；持久视图可以折叠严格结构回穿。
- 事件只使用中性结构变化标签；增长/降解仅作重原子规模汇总，不输出机理、因果或唯一历史断言。
- JSON/CSV 必须包含参数、来源签名、分子节点、事件、连接、键变化、回穿段和截断原因，并能按稳定 `event_id` 下钻局部轨迹。

#### 11.5.2 Species Fate Analysis

- 输入是一个精确 target Species、固定 anchor policy、互斥的精确 Species endpoint
  categories、formation window、follow-up endpoint 和显式 limits；MVP 每次只分析一个
  Current Dataset/Replicate。
- 默认统计单位是 Formation Episode。同一固定 anchor set 在 ACTIVE episode 内返回
  target 不重新计数；仅部分重叠的 target formation 创建新 episode 并记录相关性。
- 默认 anchors 是 birth instance 的全部非氢原子，并在 episode 生命周期内固定；缺少
  可靠 atom ID→element 映射时失败关闭，不按 SMILES 顺序猜测。
- 每个 Active Descendant 只连接到最近的、能以精确 Species 和完整 Atom-ID 集合唯一
  匹配的后续 Reaction Occurrence；歧义、anchor 丢失或重复分配产生 Evidence
  Censoring，不能跳过证据断点。
- 拆分后的所有 anchor descendants 分别 first-passage 并冻结。只有 Terminal Instances
  的 anchor subsets 两两不重叠且恰好覆盖初始 anchor set 时，Formation Episode 才
  fully resolved；默认 fate 是 endpoint category 与 multiplicity 构成的无序多重集合。
- 默认 branching probability 是 eligible、fully resolved episodes 中的条件分布，必须
  明确标为 `resolved_episode_conditional_probability`，并同时报告主 cohort、resolution、
  按原因 censoring 和 follow-up 分布；零分母返回 `NA`。
- 时间分别报告 initial residence、terminal first passage、first hit、descendant
  completion 和 cumulative target branch-time，始终保留 Frame、source timestep 与
  Transition 边界，不能推断区间内部反应时刻或化学速率常数。
- Raw trace 保留全部 Reaction Occurrences。MVP 只跨 episode 聚合 Fate Signature、
  first-exit channel 和无复杂拓扑的线性 Reaction Type sequence，不规范化完整
  branching/recombination event graph。
- Fate 统计依赖新版 Event Evidence Index 中离线准备的 molecular continuity substrate；
  trajectory 只用于按 `event_id` 下钻局部坐标。旧索引不影响现有事件工具，但该能力
  显示 `REBUILD_REQUIRED`。
- 正式导出是 canonical `fate-result.json` 与包含 manifest 和版本化关系表的确定性
  `fate-tables.zip`。完整契约见 [`species-fate-analysis.md`](species-fate-analysis.md)。

### 11.6 可复核事件包

事件包是确定性 ZIP，固定包含：

- `event.json`
- `frames.csv`
- `changed_bond_distances.csv`
- `trajectory.lammpstrj`
- 映射完整时的 `trajectory.extxyz`
- `bonds.csv`
- `README.txt`

内容记录事件身份、来源签名、原子范围、键变化、帧、坐标处理、元素映射和提取参数。
`frames.csv` 保留 source timestep、可选的已确认 ps、原始/显示坐标、晶胞/PBC 与
确认单位；`changed_bond_distances.csv` 只计算 RNG 证据中发生变化的原子对逐帧
距离，不从距离推断中间帧键级。未确认时间换算或长度单位时相应字段留空。映射
不完整时仍导出 ZIP 和 LAMMPS 轨迹，只省略 ExtXYZ 并说明原因。CLI 默认不覆盖
目标，覆盖必须显式指定。

#### 11.6.1 DFT Initial Geometry

- 只接受具有精确 Molecular Evidence 的 `matched` Reaction Occurrence；其他事件失败关闭，不按空间距离或 SMILES 顺序猜参与分子。
- 反应物固定使用事件的 `before_timestep`，产物固定使用 `after_timestep`；不得把中间查看帧或导出几何称为过渡态。
- 用户按侧别选择完整 Molecule Instance，可合并为复合物、逐分子导出或同时导出；反应物和产物不得混入同一个 XYZ。
- 使用侧别键图和最小镜像重建跨 PBC 的完整分子，多分子复合物保留反应接触的相对位置；只允许整体平移居中，不旋转、不优化、不修键。
- 元素映射完整且源轨迹 Å 单位已确认后才能导出。电荷和多重度允许未指定，但不得自动推断。
- 原子按原始轨迹 Atom ID 稳定排序。结构完整性错误阻止导出；碰撞、长键和大体系只产生可审计警告，不自动修改几何。
- DFT 几何使用独立确定性 ZIP，包含 XYZ、`manifest.json`、`atom_map.csv` 和 `README.txt`；确定性范围是相同来源签名（含路径）、版本和参数，不为跨目录副本哈希扫描整条轨迹。它是从事件证据派生的初始结构，不改变可复核事件包。
- 第一版只处理当前一个 Reaction Occurrence，不导出环境截断、不生成周期 DFT、过渡态、反应路径或特定量化软件作业。

### 11.7 Element Distribution Evolution

- 用户选择分组元素；数据含碳时可默认 C，但不得写死。
- 默认统计至少含一个分组元素的 Species，提供包含 `E0` 的显式选项。
- 筛选元素和条件从数据集发现，可表达存在、不存在或原子数范围。
- 可选参考 Species 使用精确 SMILES，不根据丰度猜测母体。
- 指定参考 Species 后显示其曲线和相同分组元素数量的其他 Species。
- 支持下钻到分子式、SMILES、当前数量、峰值数量和峰值位置。
- tidy 表、多数据集、分箱、范围合并和平滑等能力复用同一通用模型。

只保留一个通用核心、一个 Dash 页面和 CLI `element-distribution`。删除 C/O/Cl 固定 schema、第二套 Carbon 模式和旧 `carbon-plot`。

### 11.8 Batch Compare

- 每个输入明确归属 Simulation Condition 和 Replicate；目录自动识别只作建议，用户运行前可检查和修改。
- 使用有方向、保留计量数的精确 SMILES Reaction Type 匹配。
- 报告检出率、正向/反向/净 TP 的均值和标准差；置信区间显示方法和样本数，样本不足时标记不可计算。
- 顺序固定为：按全部所选 Replicate 计算检出率 → 过滤 → Top N。
- 任一来源缺失、重复、解析失败或分析期间变化时整次失败，不能输出不完整比较。
- 不切换 Current Dataset，也不合并不同运行的 Occurrence Identity。

## 12. CLI 与 Python API

正式 CLI 命令树为：

- `serve`
- `prepare`
- `species`
- `reactions`
- `events`
- `species-evolution`
- `verify-path`
- `species-fate`
- `export-event`
- `element-distribution`
- `batch-compare`

`prepare` 包含 status、rebuild、cancel 和 clear。删除 `topshare`、`next`、`rxn-formula`、`plot`、旧 Web/Route 构建入口及重复启动命令。

`reacnet_scope` 中显式导出的接口构成公共 API；其他模块默认内部使用。核心包拥有领域对象、索引、查询和导出。Dash 和 CLI 仅负责适配，不互相导入业务逻辑。

## 13. 本地 OVITO 与离线运行

- 核心功能不需要互联网，不使用运行时 CDN，不上传数据，不默认发送遥测。
- OVITO 是可选外部工具，不是运行依赖，也不由软件安装。
- 本地模式下，用户主动点击后可使用已配置或检测到的 OVITO 打开当前导出文件。
- 远程模式只提供下载，不能尝试启动访问者电脑或服务器桌面的 GUI。
- 检测覆盖常见 macOS App、Windows 安装路径和 Linux 可执行文件，并允许显式配置。

## 14. 来源、版本与确定性

- 每个索引和导出记录 Dataset Identity、源签名、索引 schema、算法版本和查询参数。
- 源大小、修改时间或内容签名变化后，相关索引标记 stale。
- 查询基于一致源修订；查询期间变化时明确失败。
- 相同数据、版本和参数产生稳定排序、稳定身份和确定性导出。
- JSON 使用稳定英文键并包含 schema 版本；Python API 与错误 `reason` 使用英文标识。
- Dash 以中文为主要界面。CSV 默认可使用中文显示列，但必须同时提供稳定英文机器接口或 JSON。

## 15. 规模与性能基准

目标单数据集规模为数百万事件、数千万 Species 记录和数百 GB 轨迹。当前不引入分布式基础设施。

发布硬门槛使用结构契约，而非受硬件波动影响的固定秒数：

- 在线查询不得打开或顺序扫描原始大型事件、Species 或轨迹文件。
- 轨迹只读取索引返回的有限帧范围。
- 离线准备有界内存、批量写入、检查点、锁和原子发布。
- 大型真实数据记录准备耗时、查询耗时、峰值内存和索引大小，作为回归报告。
- 无法快速完成的 UI 操作转为后台任务并显示进度，不冻结请求。

## 16. “合理实现”的发布门槛

一项功能只有同时满足以下条件，才可标记为已实现：

1. 完成本文档声明的端到端用户任务。
2. 遵守领域语义并显示证据来源、限制和降级状态。
3. 缺失文件、索引或可选依赖时给出受影响能力与恢复方法，不返回假完整的空结果。
4. 不修改 RNG 原始工件，失败或取消后保持可恢复。
5. 具备核心单元/契约测试、Dash 或 CLI 集成测试和代表性真实数据验收。
6. 通过跨平台核心测试与 Dash smoke test。

RP3 验收至少固定验证反应类型数、事件数、事件关联、已知路径验证结论、Event Path、局部帧和事件包成员。大型数据验收验证结构性能契约与回归指标。自动测试不启动 OVITO；OVITO 打开属于受控人工验收。

## 17. 已知的当前实现偏差

本文档接受时，至少存在以下已知偏差，后续实现审查必须逐项核实：

- 旧静态 Web 仍存在，Dash 仍导入其业务逻辑。
- `.route` 索引和事件回退仍存在。
- `rng_tools` 与 `reacnet_scope` 分裂，Dash 服务和回调文件过大。
- 时间演化仍可能在请求中完整扫描 `.species`。
- 组成索引、UI 和 CLI 仍写死 C/O/Cl 或 Carbon。
- 质量检索仍存在仅针对 Cl 的同位素组合特例。
- 时间相关页面仍可能静默使用 `0.0001 ps`。
- CLI 仍暴露历史命令和独立入口，缺少部分正式批处理能力。
- 输入发现仍包含 `.route`、`.moname` 等非正式能力来源。
- 跨平台路径、后台进程和 OVITO 启动尚未按 macOS/Windows/Linux 完整验收。
- 真实数据和结构性能验收有计划文档，但尚未形成完整发布门槛。
- Species Fate Analysis 已有 continuity substrate、查询核心、正式导出和 Dash/CLI
  纵向切片；仍需补齐从 Species/Molecule Lineage 的稳定身份跳转、Raw Evidence 到局部
  轨迹查看器的点击交接，以及覆盖全帧 Molecular Evidence 的 initial left-censor 检测。

这些偏差是后续 `/code-review` 的审查对象，不应通过修改本基准去迁就现状。
