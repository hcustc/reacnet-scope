# ReacNet Scope 软件设计基准

状态：已接受
日期：2026-08-03
最近产品范围修订：2026-09-19（五工作区收敛与高级能力冻结）

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

Candidate Path Discovery 是与 Path Verification 分离的网络级辅助工作流。它在当前数据集的 MD-observed directed reaction hypergraph 上，从一个或多个精确 Species 出发，以具有 event-local dominant atom-descendant 证据的 Carried Species 连接相邻 Reaction Type；每一步至少有一次 matched Reaction Occurrence 支持局部原子传递，但不同步骤不要求同一 Replicate、时间邻近、共享 Molecule Instance 或完整 Event Path。Continuous MD Support 是排名后对有限 Candidate 的独立分子谱系验证，不决定 Candidate 是否存在。

围绕焦点 Species 的 Direct Reaction Channel 是单步生成/消耗 Reaction Type 查询；它本身不递归扩展路径。

普通 Dash 的发布主线收敛为“精确 Species → Direct Reaction Channel → Reaction
Occurrence → 局部轨迹/分子谱系 → 证据导出”。Path Verification、Candidate Path
Discovery 与 Species Fate Analysis 的科学契约继续有效，但不再挂载独立 Dash 页面，也不
进入普通推荐链路；公共 CLI/API 默认兼容保留。旧页面 ID 恢复时只归一到所属工作区，
不运行退役分析。

## 4. 正式辅助能力

以下能力继续作为普通工作区内的正式辅助功能，但不是核心链路的必经步骤：

- Species 时间演化。
- Element Distribution Evolution。
- 跨 Simulation Condition 与 Replicate 的批量对比。

每项能力必须定义输入、输出、失败行为、来源限制和验收测试。页面能够打开不等于功能已实现。

以下已有能力冻结扩展并保留兼容核心：Species Fate Analysis、Apparent Rate
Constant Estimate、Path Verification、Candidate Path Discovery 及 QC 交接扩展。冻结表示
不在普通导航、任务卡或跨工具主线中推广，也不作为主线前置条件；不改变其已有身份、证据、
结果或导出语义，不删除共用的事件、分子连续性、时间、体积、PBC 与几何检查基础。

## 5. 当前非目标

当前版本明确不包括：

- 自动确认机理、因果推断，以及仅凭聚合网络或反向推导补全未观测 Reaction Type。Candidate
  Path Discovery 只使用当前数据集中有具体方向证据的 Reaction Type，但不要求整条 Candidate
  已作为一个 occurrence lineage 被采样；Species Fate Analysis 仍遵守其独立的严格原子连续性语义。
- Reaction Cycle Candidate Discovery。普通 Candidate 只记录 cycle closure evidence，不把它输出为普通路径；Fast Recrossing Episode 仍是 occurrence-level、时间局部概念。
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
- Candidate Path Discovery 接收一个或多个精确起始 Species 和显式搜索限制；它不能改变 Path Verification 的输入契约。

### 6.2 Candidate、Evidence 与 Query Result 身份

- `candidate_signature` 是跨查询复用的结构身份，由 canonical anchor Species、按序 canonical Carried Species 和按序 canonical directed Reaction Type 组成。
- canonical Species key 是稳定结构身份，不使用 dataset-local RNG ID、数组下标或 occurrence-local ID。directed Reaction Type key 规范化每一侧的 Species 顺序，保留方向与计量 multiplicity。
- occurrence、Transition、Molecule Instance/Segment、atom IDs、Replicate、frequency、score、rank、dataset revision、validation state、anchor/retention policy、query mode、target、filters、horizon 和 ranking 参数不得进入 `candidate_signature`。
- `candidate_evidence_key = dataset_revision + candidate_signature`，用于绑定当前发布修订中的证据；canonical identity 与 signature 算法分别具有明确 semantic version。
- Query Result 只保存查询相对的 rank、过滤/排名参数、完成状态和 Candidate 引用；相同 Candidate 在不同查询中不得因 rank 或查询参数获得新的结构身份。

### 6.3 Reaction Occurrence

- Aggregated Reaction Record 的 `count=N` 展开为 N 个独立 Reaction Occurrence。
- 每个发生尽可能关联到不同的原子连通分子变化；无法匹配的发生保留为 `unresolved`。
- `unresolved` 计入事件统计，但不能打开局部轨迹，也不能支持要求分子实例或原子连续性的 Event Path。
- Occurrence Identity 由 Transition、规范化 Reaction Type、参与原子和必要的确定性重复序号产生，不依赖 CSV 行号、HDF5 布局 ID 或存储顺序。
- 同一已解析发生在兼容证据格式之间迁移时应保持身份稳定。

### 6.4 权威来源冲突

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

`.moname`、`.table`、`.reaction`、`.route` 和 RNG 报告 JSON/SVG/HTML 不构成正式 Analysis Capability。Species 来源说明可以有界读取同目录、小于 1 MiB 的显式 `rng_run.json` 和 `timed_output_validation.json`，仅展示已记录的处理参数及来源；这些元数据不替代 RNG 科学证据、不触发能力就绪，也不能扩展为在线日志或大型报告扫描。其他未识别文件保持只读并被忽略。

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
- 当 Dataset Candidate 的公共前缀本身是指向外部轨迹的符号链接、而 RNG 证据位于候选目录时，Current Dataset 身份绑定 RNG 证据工作区；轨迹仍按其自身索引工作区读取，不能用符号链接目标替换分析数据集身份。
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
- Candidate production substrate 使用 staging revision 构建。准备任务必须 streaming / bounded-memory、可 checkpoint/resume，并由单写者锁或等价机制保护；只有完整性验证成功后才能原子切换 active revision。
- 构建失败、取消或进程中断时，旧 active revision 继续可读；未完成 revision 对所有在线查询不可见。

## 10. Dash 信息架构与会话

普通导航固定为五个工作区：

- 数据集：Dataset Candidate 检查、Current Dataset 切换、来源、能力与 Preparation Task。
- 物种与趋势：物种检索、结构、时间演化；质量检索和 Element Distribution Evolution 为辅助任务。
- 反应与事件：Direct Reaction Channel、反应式检索、时间分布和 Reaction Occurrence 列表。
- 轨迹与谱系：局部帧、键变化、分子谱系、事件包及最低必要的 DFT Initial Geometry 导出与检查。
- 对比：在不切换 Current Dataset 的前提下选择多个来源并比较物种、反应和条件。

内部工具可以继续使用原页面 ID，但不再为每个概念提供一级导航。数据集概览只推广上述五个
工作区；工作区内的任务导航只列出当前普通任务。能力不足时仍显示受影响能力、原因和恢复动作，
不能通过隐藏按钮把“缺证据”“需准备”或“尚未验证”伪装为有效空结果。

Candidate Path Discovery 作为“反应与事件”内的“候选路径”任务提供第一版入口，不新增一级导航。
支持单数据集的单起点探索、起点到目标搜索、精确结构选择、路线比较、逐步事件分页与 JSON/CSV 导出。
已知 `miso=1` 显示 RNG 代表身份与键级限制。按步数优先展示，不提供综合评分、能量 CSV 排名或
未完成的 Continuous MD Support 按钮。每步可以来自不同分子；连续历史始终单独标为未检查。

Path Verification 与 Species Fate Analysis 不挂载普通 Dash 页面。旧 `page-store` 中的
`candidate-paths`、`pathway`、`species-fate` 仍分别归一到“反应与事件”“反应与事件”和
“轨迹与谱系”，只恢复所属工作区，不自动运行分析。公共 CLI/API 与兼容导出继续保留。
第一版候选任务的范围与 ADR-0014 局部替代关系见
[ADR-0015](adr/0015-add-indexed-candidate-task-to-reaction-workspace.md)。

Apparent Rate Constant Estimate 不由普通 Direct Reaction Channel 查询隐式计算；普通界面保留
显式物理时间换算、事件计数和观察窗口，但不提供默认 k 设置或“显示速率列”开关。已有结果字段、
研究用核心与公共接口按兼容政策保留。谱系、DFT 初始几何与必要检查仍位于具体事件的轨迹上下文，
不扩展为量化调度、TS/IRC、理论速率或主方程平台。

普通内部工具可以独立进入。跨工具按钮只交接稳定身份和必要上下文，目标工具仍调用统一核心
实现；退役能力不提供 Dash 入口。

Current Dataset、页面和工作流选择属于浏览器会话；索引、任务和数据集设置属于 Dataset Workspace。数据集切换清空旧选择。页面恢复前重新验证路径权限、数据集身份和源修订。

## 11. 查询与分析契约

### 11.1 Species 与 Reaction 搜索

- 支持精确 SMILES、分子式、中性标称质量和中性单同位素精确质量查询。
- 精确质量容差使用 Da，显式 `0` 必须保留；结果显示 Da 与 ppm 误差。
- 质量结果可按分子式聚合展示，但必须下钻到全部具体 Species。
- 不静默枚举仅针对 Cl 的同位素组合；通用同位素检索需要独立未来设计。
- Reaction 搜索保留方向和化学计量，提供生成/消耗通道，并可交接 Reaction Occurrence。
- Species 详情必须显示精确 RNG Species 标签，并说明分子式只用于检索和分组。`miso`、HMM、采样间隔、物理 timestep 与 RNG 版本等处理设置只有在有界、显式元数据中记录时才显示值和来源；缺失项显示未知，不从目录名、日志文字或输出外观推断。
- Species 与原始 Molecular Evidence 的关系必须分层表达：存在 Timed Evidence Source 只说明可以通过已发布索引核查具体 Molecule Instance，不把 Species 标签本身表述为某个原始分子实例。

#### 11.1.1 Direct Reaction Channel 动力学量

- TP、净 TP、事件频率和 Apparent Rate Constant Estimate 是不同量，不得混称。
- 事件频率只在已确认 `timestep → ps` 且具有完整观察窗时给出，单位明确为每 ps。
- Apparent Rate Constant Estimate 的分子必须来自观察窗内的精确 Reaction Occurrence，正向与逆向分别计算，不使用净 TP。
- 分母使用 Species Abundance Evidence 的左端点反应物丰度暴露量；二阶模型还要求轨迹长度单位确认为 Å，并使用离线轨迹索引准备的逐帧模拟盒体积。
- MVP 只支持按反应物化学计量显式声明的一阶和二阶质量作用模型；`2 A` 使用下降阶乘 `n_A(n_A-1)`，不静默加入对称因子。
- 每个估计同时报告模型、事件数、观察时长、暴露量、单位和 Poisson 95% 置信区间，并明确称为“表观 k”。
- 任一时间、丰度、事件或体积证据缺失时保留通道 TP，但不得输出表观 k；页面必须显示具体缺失原因。
- Direct Reaction Channel 页面必须允许用户就地确认 Dataset Workspace 的 `timestep → ps` 换算，并在保存后原地重新计算当前通道，不要求跳转到其他工具。
- Direct Reaction Channel 页面必须允许将当前数据集显式关联到其他目录中的 `.lammpstrj`、确认 Å 并启动后台轨迹索引；关联保存在 Dataset Workspace，显式关联优先于同目录自动发现，且不得修改或复制原始轨迹。
- 正向和逆向表观 k 必须分别保存缺失原因；汇总提示按缺少轨迹、单位未确认、索引未就绪/过期/无效、模拟盒时间点不对齐、零暴露量和不支持的高阶反应分别计数，不使用笼统的“晶胞证据不足”。
- 一个通道查询涉及的全部 Species Abundance 必须在一次对齐的索引读取中取得，不得按 Species 重复解码同一批时间点；暴露量积分应使用对齐数组批量计算。
- 在线计算期间页面必须显示明确的运行中状态，并保留已有通道内容，避免把服务器等待表现为无响应。
- 在线查询只读取 Event Evidence、Species Abundance 和 Trajectory 索引，不扫描原始大型来源。

#### 11.1.2 Reaction Type 发生时间分布与事件定位

- 反应式检索和直接生成／消耗通道的首次、末次时间来自当前数据集完整、已发布的 Event Evidence Index，按该行精确有向 Reaction Type 统计，不取事件分页或 Top-N 样本。有效零结果保留空时间。
- 正向与严格逆向使用精确 Species 多重集合键；完全自反的类型只展示一次。事件计数包含 `unresolved`，但它不能进入要求完整分子关联的轨迹或谱系功能。
- 反应只知发生在前后采样帧之间。后帧时间是定位约定；首末跨度不是分子寿命，也不表示期间持续发生反应。直方图纵轴是发生次数，不称速率或路径贡献率。
- 两个方向共用时间轴和半开时间箱 `[start,end)`；每箱点击后按同一键与边界分页取稳定事件 ID、分析区间、前后原始坐标、可用的 ps 换算和关联状态。分箱之和等于窗内事件数，不能挑第一条代替整个集合。
- 仅在真实 source timestep 映射和已确认 `timestep → ps` 换算均存在时显示 ps。缺单位时显示 source timestep；只有分析帧序号时显示 analyzed frame，不套用换算。导出保留精确反应键、事件身份、原始坐标、时间单位与可用换算值。
- 时间分布在线只读取已发布事件索引；`events_by_reaction_time` 随 Event Evidence schema 5 在 preparation 中建立。旧版索引需重建，缺源、未准备、过期、无效及有效零结果分别报告。

### 11.2 Species 时间演化

普通 Dash 查询必须读取持久化 Species Abundance Index，不得每次完整扫描 `.species`。索引至少提供物种目录、时间点定位、峰值摘要和按 Species 读取的时间序列。

- 精确 SMILES 表示具体 Species。
- 分子式查询显式选择合计、分别显示或两者，并列出聚合成员。
- 归一化、时间对齐、平滑和降采样都记录参数。
- 平滑与降采样只影响显示，不覆盖原始数值或统计结论。
- CSV 默认导出未经平滑的原始/聚合序列；处理后导出同时记录变换。
- 曲线截断必须显示被省略数量。
- 多数据集对比要求每个来源的相应索引就绪。
- 多来源物种丰度对比由用户逐来源选择精确 Species；不同代表 SMILES 只有经用户分别确认后才进入同一比较。来源名称仅用于展示，导出保留文件身份和每个来源的目标。
- 默认按各来源原始 timestep 显示独立序列；逐来源汇总未经平滑的初始值、末值、峰值和峰值时间。未找到目标、全零结果、缺索引和缺物理时间换算分别报告，不推断来源间独立重复或自动计算组均值。
- 单源时间演化与逐来源比较复用同一 Species 时间序列核心；比较结果额外保存每个来源的 Dataset Identity、源修订、精确目标和身份口径。模型迭代、Simulation Condition、Replicate 与 RNG 处理参数均带 `confirmed`、`suggested` 或 `unknown` 状态，未知值不从路径补成事实。

CLI 默认复用索引，可提供显式一次性流式模式，并在输出中标明来源模式。

### 11.3 时间轴

- 始终保留 Analyzed Frame 和源 timestep，不能混称为物理时间。
- 只有证据提供转换信息，或用户确认并保存 timestep 到 ps 的换算后，才显示 ps/ns。
- 未确认时默认显示 frame 或 timestep，不使用静默物理时间默认值。
- 换算绑定 Dataset Workspace 并写入导出参数。
- 多数据集物理时间对比要求每个数据集分别具有明确换算。
- 任一所选来源缺少或具有无效换算时，整次物理时间比较不绘制部分曲线；用户可以改回各自原始 timestep 查看独立序列。

### 11.4 Path Verification 与 Event Path

- 输入是用户按顺序明确给出的 2–8 个完整 Reaction Type；不从起点物种自动扩展。
- 验证结果是 `supported`、`not_observed` 或 `inconclusive`；只有完整且未截断的遍历可以给出 `not_observed`。
- 节点是 Reaction Occurrence，不是 Reaction Type。
- 边要求时间严格向后、共享同一精确分子实例，并连接到该实例第一次后续消耗。
- 三事件及更长路径要求至少一个原子 ID 贯穿相邻边，形成连续原子谱系。
- 跨 Replicate 统计以“Replicate + 原子谱系”为独立支持单位，报告时间间隔和复现率。
- 缺少 Molecular Evidence 时拒绝分析，不退化为同名 Species 拼接。

### 11.5 Candidate Path Discovery

- Discovery graph 只包含当前发布 revision 中至少有一次 normalized Reaction Occurrence 和具体 Reaction Evidence 支持的记录方向；聚合网络、推导反向或 `count=0` 不能创建方向。`count >= 1` 只表示 eligible，不代表 mechanistically significant。
- 相邻步骤必须由明确的 exact Carried Species 连接：它是前一步的 product，也是后一步的 reactant。至少一个 matched Reaction Occurrence 必须证明该产物是 focal reactant 的 event-local dominant atom descendant，即与 focal reactant 具有所有产物 participant 中最大的正 atom-ID 交集。所有 co-reactants 和其他 products 保留为完整 Reaction Type context，但不决定主路径连接。
- 多产物 Reaction Type 只对满足上述局部原子传递规则的 product Species 产生 carried branch；最大交集并列时分别保留。ranker 不得按分子式、结构相似度或人工类别猜测 Carried Species。不同步骤仍可来自不同事件和 Molecule Instance；这一局部规则不等于 Continuous MD Support。
- 普通 Candidate 使用 Carried-Species-simple path；已访问 Carried Species 的 expansion 不进入普通 Candidate，而记录为可审计 cycle closure evidence。到达 `max_steps` 是正常 discovery horizon termination，不是 cycle 或 execution truncation。
- `max_steps` 是 declarative query horizon。`max_expansions`、`max_frontier_states`、`max_candidates_examined`、wall-time 和 memory 是 execution budgets，必须与 horizon 分开报告。

#### 11.5.1 Discovery modes 与完成状态

- `target-constrained` 查询从 anchor Species 寻找一个或多个 target Species。只有 carried endpoint 到达 target 才形成结果；target 仅作为其他 product 出现不算到达，首次到达任一 target 后停止扩展该分支，未到达的中间状态不输出，且普通路径拒绝 `target_species == anchor_species`。
- `exploratory` 查询不要求 target；`min_steps <= length <= max_steps` 的每个 simple prefix 都可成为 Candidate，短 Candidate 输出后仍可继续扩展。`max_steps` 不宣称 endpoint 是稳定产物或化学终点。
- target-constrained 状态为 `found`、`not_found_within_constraints` 或 `truncated/inconclusive`。所有模式分别报告 `query_complete`、`graph_exhaustive` 和 `horizon_limited`；在完整搜索 `length <= max_steps` 后未命中只能说明约束内未找到，不能声明任意长度均不存在。
- exploratory 输出由确定性、版本化 ranking 和 Top-K 有界化。第一版依 ADR-0015 采用按步数优先、同层按精确身份确定性展开的展示顺序，不计算综合评分。hub 邻接、frontier 和结果数预算均显式报告；它们不是化学重要性过滤。

#### 11.5.2 Discovery execution boundary

- 正式数据流是 `raw MD evidence → offline indexed substrate → online bounded local discovery → selective Continuous MD Support → paged evidence drill-down`。
- Online Candidate Discovery 只读已发布的 hypergraph adjacency、aggregate metrics 和 canonical identity indexes，围绕 exact Carried Species 局部展开。
- 在线请求不得扫描 raw event source、加载全部 occurrences 或 continuity segments、构造全局 occurrence graph，也不得预枚举或持久化全部 Candidate Paths。
- `reacnet_scope/event_paths.py` 当前从完整事件集合构建 occurrence graph 的 Candidate discovery 只能作为原型/兼容实现保留。正式 production endpoint 上线并完成兼容迁移前不删除；上线后不得作为默认或百万级发布路径。

### 11.6 Continuous MD Support 与 Candidate Evidence

Continuous MD Support 在 hypergraph discovery 与 network filtering/ranking 之后，只验证 Top-M 或用户显式选择的 Candidate。它不参与 Candidate identity，不决定 Candidate 是否存在，不得反向修改稳定的 `network_score` 或 `network_rank`；`not_evaluated` 不等于 unsupported 或零分。

#### 11.6.1 Anchor 与无阈值事实结果

- 默认 `anchor_policy=all_heavy_atoms`，纯氢 Species 回退 `all_atoms`；允许显式 `all_atoms` 或 `explicit_atom_ids`。anchor policy 只选择 anchors，不定义 retention 是否足够。
- 一般验证默认不隐含 retention threshold。`validation_execution = not_evaluated | complete | inconclusive`，事实结果至少报告 carrier chain、selected anchor atom IDs、`max_continuous_anchor_set`、逐步丢失 anchors、retained count/fraction 和 `intact_anchor_support`。
- `max_continuous_anchor_set = selected_anchor_atom_set ∩ carrier_0 ∩ carrier_1 ... ∩ carrier_n`。`intact_anchor_support=false` 只表示并非全部 selected anchors 完整贯穿，不得显示为一般意义的“Continuous MD Support = false”。
- 只有查询显式提供 `anchor_retention_policy` 时才计算 `supported`、`not_observed_within_constraints` 或 `inconclusive`。政策可以是 `all_selected`、`min_fraction=x`、`min_count=n` 或 `explicit_atom_ids`。
- `require_continuous_support=true` 必须同时给出 retention policy；缺失时拒绝执行，不采用隐式阈值。

#### 11.6.2 Molecule Continuity Segment 与验证顺序

- Molecule Continuity Segment 是单个 Replicate 内，在连续 Analyzed Frames 上保持相同 Species、atom-ID set 和 intramolecular bond set 的最大连续区间。中断后的相同结构、atom IDs 和 bonds 是新 segment，不得远距离拼接。
- 每一步从前一步 Transition 的 product side 确定 carrier segment；segment 可跨越任意数量未改变它的 Analyzed Frames，但下一步必须匹配第一次明确消费或改变该 segment 的 Transition 中的 compatible normalized occurrence。
- 验证不得跳过更早 consumption、continuity gap 或 unresolved evidence barrier。排序单位是 Transition；同一 Transition 内的多个 occurrences 没有内部先后，竞争 consumers 无法消歧时为 ambiguous/inconclusive。
- 表示层重复必须先由 event normalization 归并。segment 无法解释地消失或遇到相关 evidence barrier 时为 inconclusive。
- 如果结果同时声称 carrier chain 与 anchor provenance 连续，但 `max_continuous_anchor_set` 为空，必须报告 semantic inconsistency / carrier-selection error，不得发布为正常 validation result。

#### 11.6.3 Evidence drill-down 与缓存

- Step Evidence 按 Candidate step 独立分页，展示支持该记录方向的 normalized occurrences，包括 Replicate、Transition、before/after frame、完整 stoichiometry、association 状态和 event evidence package。不同 step 的 occurrence 列表不得被呈现为一条 sampled chain。
- Continuous Support Evidence 单独分页。每个 `support_occurrence` 至少稳定记录 `support_occurrence_id`、`candidate_signature`、`candidate_evidence_key`、dataset revision、Replicate、按序 Transition IDs、normalized Reaction Occurrence IDs、Carried Species 和 Molecule Continuity Segment IDs、anchor Molecule Instance/origin reference、selected anchor atom IDs、per-step carrier atom/bond references、provenance profile、validation constraints、validator semantic version、source/evidence signatures 和 ambiguity/evidence-gap/truncation 状态。
- Candidate 主响应只包含稳定引用、汇总指标和分页入口。坐标与局部轨迹通过稳定 reference 按需读取，不嵌入 Candidate 或 support record。
- support cache key 至少包含 `candidate_evidence_key`、anchor policy、validation constraints、可选 retention policy 和 validator semantic version；还必须保存 validation selection scope，防止把未进入 Top-M 解释为证据较弱。
- 下钻顺序为 `Candidate → Step Evidence / Continuous Support summary → paged evidence records → Transition / occurrence / continuity segment → local trajectory / evidence package`。

### 11.7 Reaction Occurrence 与轨迹查看

- 事件页查询并选择具体 Reaction Occurrence；未解析发生可统计但不可打开轨迹。
- 默认显示全部参与原子，可切换仅反应核或周围环境。
- 周围环境默认 `4.0 Å`、最多 `500` 原子；允许在服务器安全范围内修改，截断时显示原始命中数。
- 轨迹读取只使用预建索引返回的有限帧字节范围。
- ASE 负责晶胞、PBC、最小镜像、支持的坐标约定和重居中。
- 元素优先级：轨迹 `element` 列 → 数据集用户确认映射 → `T<type>`。
- 映射保存在独立数据集设置中，允许部分映射；只有所选原子全部映射时生成 ExtXYZ。
- 缺少轨迹索引或 ASE 时仍可查看事件元数据，但明确禁用轨迹和相应导出。
- 事件选择保存最小、版本化书签：Dataset Identity、源修订指纹、稳定 `event_id` 和前／后展示帧数。书签不保存事件行或轨迹结果；恢复时先核验数据集身份与源修订，再按 `event_id` 从已发布事件索引重新读取。任一不匹配、事件缺失或索引不可用均拒绝旧结果，且不改变 Current Dataset。
- 书签恢复只重建事件选择和展示窗口，不自动读取轨迹；用户显式打开后才按预建轨迹索引读取有界帧。

#### 11.7.1 Molecule Lineage

- 输入必须是某个 Reaction Occurrence 一侧的具体 Molecule Instance，不接受只有 Species 的起点。
- 连续性使用精确 Species、atom-ID 集合和最近可解析事件；结构回穿还要求分子内键集合完全相同。
- 默认锚点为全部非氢原子，缺少可靠 atom ID → element 映射时失败关闭；允许显式改为全部原子、指定元素或指定 Atom IDs。
- 拆分时展开所有保留锚点的分支；合并的无锚点共同反应物和无锚点离去片段只作为上下文。
- 默认向前/向后各 3 次持久变化、最多 100 个分子节点、Fast Recrossing Episode 窗口 5 个 Analyzed Frames；所有边界和证据断点必须写入报告。
- 用户从预算停止的具体分支显式执行“继续追踪分支”。每个查询片段绑定 Molecule Instance、保留锚点、单一方向、Dataset Identity、源修订和本段预算；证据边界与连续性断点不可作为可继续分支。
- 合并多个查询片段时按稳定分子节点、`event_id`、连接和回穿段身份去重；已交接停止点保留历史记录。继续追踪失败不覆盖已完成片段，来源身份或修订不匹配时失败关闭。
- 分支摘要至少给出停止原因、能否继续、已查 frame/timestep 范围、下一事件提示和可下钻的事件身份。界面术语使用“继续追踪分支”，领域对象和导出仍称 Molecule Lineage。
- 原始视图、事件表和导出保留全部 Reaction Occurrence；持久视图可以折叠严格结构回穿。
- 事件只使用中性结构变化标签；增长/降解仅作重原子规模汇总，不输出机理、因果或唯一历史断言。
- `molecule-lineage/v2` JSON/CSV 必须包含参数、数据集/源修订上下文、查询片段、分支摘要、已查范围、分子节点、事件、连接、键变化、回穿段和截断原因，并能按稳定 `event_id` 下钻局部轨迹。

#### 11.7.2 Species Fate Analysis

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

### 11.8 可复核事件包

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

#### 11.8.1 DFT Initial Geometry

- 只接受具有精确 Molecular Evidence 的 `matched` Reaction Occurrence；其他事件失败关闭，不按空间距离或 SMILES 顺序猜参与分子。
- 反应物固定使用事件的 `before_timestep`，产物固定使用 `after_timestep`；不得把中间查看帧或导出几何称为过渡态。
- 用户按侧别选择完整 Molecule Instance，可合并为复合物、逐分子导出或同时导出；反应物和产物不得混入同一个 XYZ。
- 使用侧别键图和最小镜像重建跨 PBC 的完整分子，多分子复合物保留反应接触的相对位置；只允许整体平移居中，不旋转、不优化、不修键。
- 元素映射完整且源轨迹 Å 单位已确认后才能导出。电荷和多重度允许未指定，但不得自动推断。
- 原子按原始轨迹 Atom ID 稳定排序。结构完整性错误阻止导出；碰撞、长键和大体系只产生可审计警告，不自动修改几何。
- DFT 几何使用独立确定性 ZIP，包含 XYZ、`manifest.json`、`atom_map.csv` 和 `README.txt`；确定性范围是相同来源签名（含路径）、版本和参数，不为跨目录副本哈希扫描整条轨迹。它是从事件证据派生的初始结构，不改变可复核事件包。
- 第一版只处理当前一个 Reaction Occurrence，不导出环境截断、不生成周期 DFT、过渡态、反应路径或特定量化软件作业。

#### 11.8.2 QC Handoff Readiness

- 复用现有 DFT 卡片和 CLI，不新增页面；检查主体是 source revision + Replicate + event_id，而不是 Reaction Type。
- 顶层只输出 `blocked / needs_input / review_required / ready`，不输出数值评分或 `reaction_ready` 布尔值。
- paired handoff 要求两侧合并几何、相同 Atom IDs、完整 changed-bond endpoints、明确电子态与非周期孤立簇确认；来源、精确帧、元素、PBC 或拓扑错误失败关闭。
- `blocked` 与 `needs_input` 不产生 handoff ZIP；`review_required` 保留预览，但必须显式确认警告后才能下载，且状态不改写为 `ready`。
- `ready` 只表示可交给外部 TS optimization/frequency/IRC 流程。动力学适用性独立固定为证据不足，不得声称已验证基元步骤、过渡态、TST/RRKM 或可直接计算速率。
- handoff ZIP 额外包含 `reaction_readiness.json` 和 `occurrence.json`；报告与 occurrence、两侧 Atom IDs、Replicate、Dataset Identity、source revision 和来源签名一起审计。

### 11.9 Element Distribution Evolution

- 用户选择分组元素；数据含碳时可默认 C，但不得写死。
- 默认统计至少含一个分组元素的 Species，提供包含 `E0` 的显式选项。
- 筛选元素和条件从数据集发现，可表达存在、不存在或原子数范围。
- 可选参考 Species 使用精确 SMILES，不根据丰度猜测母体。
- 指定参考 Species 后显示其曲线和相同分组元素数量的其他 Species。
- 支持下钻到分子式、SMILES、当前数量、峰值数量和峰值位置。
- tidy 表、多数据集、分箱、范围合并和平滑等能力复用同一通用模型。

只保留一个通用核心、一个 Dash 页面和 CLI `element-distribution`。删除 C/O/Cl 固定 schema、第二套 Carbon 模式和旧 `carbon-plot`。

### 11.10 Batch Compare

- 每个输入明确归属 Simulation Condition 和 Replicate；目录自动识别只作建议，用户运行前可检查和修改。
- 目录扫描不得默认选择或确认推断分组。只有用户检查、必要时修改并显式确认后，建议值才可作为条件/Replicate 元数据进入统计；普通多来源选择默认只表示独立来源。
- 使用有方向、保留计量数的精确 SMILES Reaction Type 匹配。
- 报告检出率、正向/反向/净 TP 的均值和标准差；置信区间显示方法和样本数，样本不足时标记不可计算。
- 顺序固定为：按全部所选 Replicate 计算检出率 → 过滤 → Top N。
- 任一来源缺失、重复、解析失败或分析期间变化时整次失败，不能输出不完整比较。
- 不切换 Current Dataset，也不合并不同运行的 Occurrence Identity。
- 结果和导出保存来源记录、RNG 处理来源、身份口径及证据层级。仅有 `.reactionabcd` 时结论限于聚合反应网络；不能据此声称具体 Reaction Occurrence、原子谱系或连续路径在来源间可比。

## 12. CLI 与 Python API

正式 CLI 命令树为：

- `serve`
- `prepare`
- `species`
- `reactions`
- `events`
- `species-evolution`
- `verify-path`
- `candidate-paths`
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
- Candidate canonical identity、Candidate signature、network ranking 和 Continuous Support validator 分别版本化；版本变化不得被隐藏在相同标识下。
- 源大小、修改时间或内容签名变化后，相关索引标记 stale。
- 查询基于一致源修订；查询期间变化时明确失败。
- 相同数据、版本和参数产生稳定排序、稳定身份和确定性导出。
- byte-stable 只约束 canonical result payload；timestamp、job ID、wall time 等 volatile metadata 不参与字节比较。
- JSON 使用稳定英文键并包含 schema 版本；Python API 与错误 `reason` 使用英文标识。
- Dash 以中文为主要界面。CSV 默认可使用中文显示列，但必须同时提供稳定英文机器接口或 JSON。

## 15. 规模与性能基准

目标单数据集规模为数百万事件、数千万 molecule-frame 记录和数百 GB 轨迹。当前不引入分布式基础设施。

发布硬门槛使用结构契约，而非受硬件波动影响的固定秒数：

- 在线查询不得打开或顺序扫描原始大型事件、Species 或轨迹文件。
- 轨迹只读取索引返回的有限帧范围。
- 离线准备有界内存、批量写入、检查点、锁和原子发布。
- 大型真实数据记录准备耗时、查询耗时、峰值内存和索引大小，作为回归报告。
- 无法快速完成的 UI 操作转为后台任务并显示进度，不冻结请求。

Candidate Path / Continuous MD Support 的发布门槛还必须在 `10^6 normalized Reaction Occurrences`、`10^7 molecule-frame records` 及至少两个不同数据规模点上验证：

- Candidate Discovery 不打开 raw event source；query plan 命中预期 adjacency/identity/metric indexes，且无非预期 full scan。
- query RSS 主要由 frontier、Top-K、page size 和显式 budget 决定。向数据集添加与局部查询无关的大量 events 后，本地查询读取量和 RSS 不得近似线性增长。
- Step Evidence 每页只读取请求的有界记录；单条 Candidate 的 support validation 使用 sequence-constrained indexed join，不重建全局 graph，并严格受 budget 控制。
- 相同 active revision、semantic versions 和参数产生规范化、确定性结果；horizon termination、execution truncation 和 validation inconclusive 分别报告。
- preparation 支持中断续建；failed publish 不污染 active revision，旧 revision 继续可读。
- benchmark 固定输出 wall time、peak RSS、index size、rows/pages read 和 throughput，并保存 query plan 证据。

## 16. “合理实现”的发布门槛

一项功能只有同时满足以下条件，才可标记为已实现：

1. 完成本文档声明的端到端用户任务。
2. 遵守领域语义并显示证据来源、限制和降级状态。
3. 缺失文件、索引或可选依赖时给出受影响能力与恢复方法，不返回假完整的空结果。
4. 不修改 RNG 原始工件，失败或取消后保持可恢复。
5. 具备核心单元/契约测试、Dash 或 CLI 集成测试和代表性真实数据验收。
6. 通过跨平台核心测试与 Dash smoke test。

收敛版本还必须保证：普通导航只有五个工作区；退役页面不再挂载；冻结能力不作为主线前置
条件且不会因旧会话恢复自动运行；普通通道查询不调用表观 k、候选评分、Species Fate 或
QC 预检。

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
- Species Fate Analysis 已有 continuity substrate、查询核心、正式导出和 CLI 纵向切片；
  独立 Dash 页面已退役。兼容核心仍缺少从 Species/Molecule Lineage 的稳定身份跳转、
  Raw Evidence 到局部轨迹查看器的点击交接，以及覆盖全帧 Molecular Evidence 的 initial
  left-censor 检测。
- Candidate Path 当前实现仍由 `reacnet_scope/event_paths.py` 在线扫描完整 `events` 表并构造内存 occurrence graph，且把严格 Event Path 当作 Candidate 产生条件；它只属于原型/兼容路径。
- Candidate schema 当前以 Reaction Type tuple 派生的 `signature_id` 作为路径标识，由 ranker 猜测 focal output，并把 occurrence continuity 指标混入固定 ranking；尚未实现显式 Carried Species、两层 Candidate identity、discovery mode/完成状态或独立 Continuous MD Support。
- Candidate CLI、专题文档和兼容测试仍有旧响应字段；独立 Dash 页面已退役。任何语义迁移
  必须按迁移计划版本化切换，不能在旧字段上静默改变含义。

这些偏差是后续 `/code-review` 的审查对象，不应通过修改本基准去迁就现状。
