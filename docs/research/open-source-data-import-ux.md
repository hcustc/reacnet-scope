# 成熟开源产品的数据导入与数据源选择 UX 调研

调研日期：2026-08-24

## ReacNet Scope 最终采用的边界

调研用于提供成熟模式，不直接等同于最终产品规格。结合本项目的本地部署方式与实际使用习惯，本轮设计最终收敛为：

- 数据始终来自运行 ReacNet Scope 的当前环境，不提供客户端上传入口；远程使用只是网页转发，不改变数据所在位置。
- 用户选择的是“一个 ReacNetGenerator 数据文件夹”，内部公共前缀只由系统解析，不出现在操作界面。
- 主入口依次为最近使用、直接输入文件夹路径、按需展开的单层文件夹浏览；不展示启动目录，也不使用目录树。
- 选择文件夹后先进入检查页，展示可用功能和源文件证据；只有点击“使用此数据集”才原子切换当前数据集。
- 允许不完整数据集，并按实际证据降级功能；切换成功后停留在数据集概览，由用户选择可用分析功能。
- 最近成功使用的数据集跨应用重启保留；运行期间不监视源文件变化，也不增加手动重载按钮。

## 结论先行

当前 ReacNet Scope 的不顺手不只是 Dash 或某个 Web 组件的问题，核心是**交互对象没有被稳定地定义**：用户想选择的是一个已经存在于服务器上的、由多个文件和派生索引组成的 ReacNetGenerator 数据集；当前界面却在不同阶段把它表现为服务器目录、文件候选、`base`、当前数据集、能力状态和索引任务。更具体地说，“上传本地文件（复制字节）”“登记服务器已有数据（引用/注册）”“切换当前数据集（切换上下文）”和“准备派生索引（后台作业）”是四种不同动作，不应继续压缩成一个含义游移的“导入/加载”。

对标产品给出的共同答案不是“做一个更漂亮的文件选择器”，而是把流程分成五个明确阶段：

1. 先说明数据来自哪里；
2. 把底层文件或连接聚合为用户认识的候选对象；
3. 在提交前预览或轻量验证；
4. 提交后保留一个持久、可恢复的工作区状态；
5. 根据验证出的能力提供多个有效下一步，而不是跳入一个可能不可用的工具。

对 ReacNet Scope 最合适的组合是：

- 用 **Galaxy** 的 History / Collection 模型承接“一个数据集由多项数据和异步状态组成”；
- 用 **OpenRefine** 的“来源选择 → 解析预览 → 明确创建”承接导入前确认；
- 用 **JupyterLab** 的受限服务器文件浏览器承接高级路径导航，而不是主入口；
- 用 **Grafana** 的“Save & test 后仍留在上下文中，并展示能力相关下一步”承接成功落点；
- 借鉴 **Superset** 对连接权限和能力的显式校验，但不要照搬它面向数据库的配置负担。

## 调研范围与证据规则

本调研只使用项目官方文档、官方培训材料和官方仓库源码。下文将“官方资料直接描述的行为”写为“事实”，将对 ReacNet Scope 的适配判断写为“启示”或“判断”，避免把设计推论说成产品事实。

覆盖产品：

- OpenRefine
- Galaxy
- JupyterLab
- Apache Superset
- Grafana

其中 Galaxy 是最贴近服务器端科学数据工作台的主要参照；其余产品用于补充来源分流、预览、服务器浏览、连接测试和成功后引导等不同侧面。

## 横向比较

| 产品 | 首次入口 | 候选确认 | 可用性/校验 | 成功后落点 | 恢复与最近使用 |
| --- | --- | --- | --- | --- | --- |
| OpenRefine | 本机文件、URL、剪贴板、数据库、Google Drive 分入口 | 预览前 100 行并调解析选项 | 自动猜格式，用户检查编码、分隔符、表头、工作表 | 创建后进入项目数据表 | 项目列表可按修改时间等排序；自动保存；Undo/Redo |
| Galaxy | 本机上传、粘贴/URL、配置过的 Remote Files、FTP、共享数据等 | 先选受信数据源，再选文件并回到上传队列；每项可设 datatype/build；多项可建 Collection | 异步状态持续留在 History：排队、运行、成功、失败、暂停 | 数据进入当前 History，不强制跳离；成功项可立即查看或作为工具输入 | 多个持久 History；失败项保留错误和日志，可修正后重跑 |
| JupyterLab | 服务器文件浏览是常驻入口；本机文件上传到当前服务器目录 | 通用文件/目录列表、面包屑、路径编辑、Open With | 上传进度、重名确认、大文件确认/分块；不理解科学数据集完整性 | 通常停留在文件工作区；受支持的小文件可配置为自动打开 | 恢复上次文件浏览目录；失败不会产生“已选科学数据集”语义 |
| Superset | “连接数据库”与“上传 CSV/Excel 到数据库”是分开的任务 | 上传时选数据库、schema、表名和解析字段；连接时选数据库类型 | 数据库连接先 Test Connection；上传受数据库和 schema 权限约束 | 上传后数据出现在 Datasets，用户再显式进入 Explore | 连接保留在连接列表；错误通常在当前配置上下文修正 |
| Grafana | 从连接目录搜索和选择数据源类型 | 针对该数据源填写连接配置 | `Save & test` 将保存与健康检查合成一次明确动作 | 留在数据源上下文，并给出 Explore、Dashboard、Alert 等下一步 | 已配置数据源保留在列表；故障按连接、认证、TLS 等分类排查 |

## 1. OpenRefine：来源分流和提交前预览

### 首次入口如何区分来源

**事实：** OpenRefine 的 Create Project 页面把来源明确分为本机文件、Web URLs、剪贴板、数据库和 Google Drive；这些是并列的来源选择，而不是同一个路径框的不同写法。无论来源为何，下一步都会进入导入预览。官方说明还强调它把输入复制到自己的项目工作区，不修改原始来源。[Starting a project](https://openrefine.org/docs/manual/starting)

**启示：** 来源类型应该在用户输入前确定，因为来源决定传输方式、权限、失败类型和用户心智。只有在 ReacNet Scope 真正支持客户端上传或 URL 抓取后，才应该增加对应的一级入口；当前只支持服务器已有数据时，入口应直说“选择服务器上的 ReacNetGenerator 数据集”。

### 如何浏览和确认候选

**事实：** 本机来源允许一次选多个文件；URL 来源允许添加多个地址；压缩包会被展开并让用户选择其中要导入的文件；Google Drive 列表按最近修改排序。进入 Project preview 后，界面展示前 100 行和识别到的列。[Starting a project — source selection and project preview](https://openrefine.org/docs/manual/starting)

**启示：** ReacNet Scope 不需要复制表格预览，但需要同等强度的“语义预览”：数据集名称、公共前缀、核心文件证据、时间范围/规模摘要，以及“现在可用 / 准备后可用 / 缺失”的能力预览。

### 如何展示可用性和校验

**事实：** OpenRefine 依据扩展名猜测解析器；不确定时提供可能格式和解析设置，并要求用户在创建项目前检查分隔符、表头、工作表和字符编码。创建是预览之后的单独确认动作。[Project preview](https://openrefine.org/docs/manual/starting#project-preview)

**启示：** 自动识别可以减少操作，但不应等同于自动提交。单候选可以自动选中，却仍应保留一次“加载并使用”的原子确认。

### 成功后的落点和下一步

**事实：** 用户在满意预览后点击 Create Project，进入已创建项目的工作区；OpenRefine 的创建 API 返回用于查看项目的界面，而不是回到来源选择器。[OpenRefine API — Create project](https://openrefine.org/docs/technical-reference/openrefine-api#create-project)

**启示：** 成功落点应该是新对象本身的稳定工作区。对于 ReacNet Scope，这对应“当前数据集概览”，而不是某个预设分析页。

### 错误恢复和最近使用

**事实：** Open Project 列出已有项目，并可按修改日期、标题、行数及元数据组织；项目编辑会自动保存，Undo/Redo 保存操作历史。Google Drive 来源列表按最近修改排序，授权错误的官方建议是在原流程中重试。[Project management and autosaving](https://openrefine.org/docs/manual/starting#project-management)

**启示：** “最近使用”应指最近成功加载的数据集，不应只是浏览过的目录。失败或取消导入也不应覆盖当前项目。

### 适合与不适合 ReacNet Scope 的模式

适合：来源先分流、预览后提交、自动检测但允许人工确认、成功进入对象工作区、最近项目。

不适合：用表格前 100 行作为通用预览；把服务器已有大型多文件数据复制成内部项目；在当前尚不支持时展示本机上传、URL 等虚假入口。

## 2. Galaxy：科学数据工作区、集合和持续状态

### 首次入口如何区分来源

**事实：** Galaxy 的 Upload 界面可从本机添加文件，也可通过 Paste/Fetch data 输入小段文本或每行一个 URL；`Choose Remote Files` 以统一界面浏览管理员配置的本地或远程文件源，例如 FTP、Dropbox 和公共 S3。Galaxy Data Libraries 还可以在权限控制下从配置的服务器目录导入整个目录、保留目录结构，并选择链接而不是复制。任意 `file://` 路径粘贴只对管理员开放且须显式配置，不是普通用户的裸服务器文件系统入口。[Getting data into Galaxy](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/get-data/slides.html)；[Remote Files](https://docs.galaxyproject.org/en/latest/releases/21.01_announce_user.html#remote-files)；[Galaxy Data Libraries](https://galaxyproject.org/data-libraries/)

**启示：** 来源方式应以“数据如何到达服务器”区分，而不是以路径字符串长什么样区分。ReacNet Scope 当前的数据已在服务器，不属于浏览器本机上传；首屏更适合显示管理员配置的数据根或命名数据源，而不是从裸 `/home` 开始。

### 如何浏览和确认候选

**事实：** Remote Files 顶层先展示可用的数据源或存储位置，用户进入目录选择文件后，候选回到 Upload Manager 队列，并不会在选中文件时立刻开始；上传列表中的文件可统一或逐项设置 datatype 和 genome build，用户须再点击 Start。多项输入还可直接构造成 Collection，先选择集合类型、设置元数据、命名，再 Create。[Remote Files](https://docs.galaxyproject.org/en/latest/releases/21.01_announce_user.html#remote-files)；[Getting data into Galaxy — upload and collections](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/get-data/slides.html)

**启示：** Galaxy Collection 是最接近 ReacNet 数据集的参照：界面操作的是一个有结构的集合，而不是要求用户理解每个底层文件。ReacNet 候选卡应该以公共前缀把 `.species`、反应 CSV、轨迹和索引聚合成一个可确认对象。

### 如何展示可用性和校验

**事实：** 上传结果立即出现在当前 History，并经历灰色排队、黄色运行、绿色成功、红色失败等状态；datatype 可以自动检测或由用户指定，检测错误可在之后通过 Edit Attributes 修正或转换。工具只接受适当 datatype 的数据集。[A short introduction to Galaxy](https://training.galaxyproject.org/training-material/topics/introduction/tutorials/galaxy-intro-short/tutorial.html)；[Introduction to Galaxy — datatypes](https://training.galaxyproject.org/training-material/topics/introduction/tutorials/introduction/slides.html)

**启示：** 大型校验或索引准备应成为可持续观察的状态，而不是同步按钮中的瞬时提示。颜色只能作为辅助手段，仍要显示文字状态和原因。

### 成功后的落点和下一步

**事实：** 上传开始后窗口可以关闭，数据项继续留在当前 History；变绿后可以查看内容，也可以成为兼容工具的输入。History 是主分析页右侧的持久工作区，按时间记录上传数据和后续工具输出。[Getting data into Galaxy](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/get-data/slides.html)；[Understanding Galaxy history system](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/history/tutorial.html)

**启示：** 成功后无需强制跳转。当前数据集应更新在全局工作区中，用户从“可用能力”进入适配的分析工具。

### 错误恢复和最近使用

**事实：** 红色失败项保留在 History；展开后可看错误，bug 图标可查看错误信息，Job Information 提供 stdout/stderr，也可重新载入同一工具参数修正后再运行。注册用户可以保留多个 History 并切换；History 本身即按执行时间保存数据和处理来源。[Troubleshooting errors](https://training.galaxyproject.org/training-material/faqs/galaxy/analysis_troubleshooting.html)；[Understanding Galaxy history system](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/history/tutorial.html)

**启示：** 失败候选不应把用户丢回空白初始页，也不应清空仍可工作的旧数据集。应保留候选及失败步骤，让用户修正路径、补文件、刷新状态或查看准备命令。

### 适合与不适合 ReacNet Scope 的模式

适合：按信任边界暴露命名数据源而不是裸文件系统、结构化数据集合、候选队列后再提交、持久工作区、异步状态、错误就地展开、失败可重试、兼容性驱动的工具选择。

不适合：为已有服务器数据再复制一份到内部 History；照搬 FTP/共享库等部署基础设施；把所有派生索引都伪装成独立用户数据项。

## 3. JupyterLab：受限服务器浏览器应是高级入口

### 首次入口如何区分来源

**事实：** JupyterLab 的 Files 侧栏直接浏览 Jupyter Server 暴露的文件系统；客户端上传是文件浏览器工具栏里的 Upload Files 或向当前目录拖放。它不是一个把“本机 / URL / 服务器文件”并列呈现的导入向导。[Working with Files](https://jupyterlab.readthedocs.io/en/latest/user/files.html)

**启示：** JupyterLab 很好地证明了客户端上传与服务器已有文件是两个动作，但它不应成为 ReacNet 主流程的完整范本。

### 如何浏览和确认候选

**事实：** 文件浏览器支持目录列表、双击导航、面包屑、可编辑路径和目录补全；文件可以双击打开，或用 Open With 选择不同查看器。服务器可限制根目录，是否显示隐藏文件也同时受服务端和 UI 设置控制。[Working with Files — Opening Files and Editable Breadcrumbs](https://jupyterlab.readthedocs.io/en/latest/user/files.html)

**启示：** 可编辑面包屑、路径补全、允许根边界和上次位置恢复适合 ReacNet 的“高级选择”。普通用户入口仍应优先列出已发现的数据集，而不是所有目录。

### 如何展示可用性和校验

**事实：** JupyterLab 的文件浏览器模型暴露上传进度；大文件会确认并支持分块上传，重名文件会请求覆盖确认，上传禁用或过大时抛出明确错误。它只校验文件操作，不理解某个目录是否构成有效科学数据集。[Official file browser model source](https://github.com/jupyterlab/jupyterlab/blob/main/packages/filebrowser/src/model.ts)

**启示：** 文件存在和可读只是最底层校验，不能替代 ReacNet 数据集完整性和能力检查。

### 成功后的落点和下一步

**事实：** 上传目标就是文件浏览器当前目录；JupyterLab 可配置为自动打开受支持且未超过阈值的上传文件，服务器已有文件则由用户显式打开或选择查看器。[File browser settings](https://github.com/jupyterlab/jupyterlab/blob/main/packages/filebrowser-extension/schema/browser.json)

**启示：** “自动打开”仅适合明确可查看的单文件，不适合根据不完整能力推断某个默认 ReacNet 分析页。

### 错误恢复和最近使用

**事实：** 文件浏览器会把最后目录写入状态数据库并在恢复时验证路径；若目录已失效，会删除该恢复状态。上传过程提供进度信号，冲突和大文件可取消。[Official file browser model source — restore and upload](https://github.com/jupyterlab/jupyterlab/blob/main/packages/filebrowser/src/model.ts)

**启示：** 应恢复“最近有效的服务器位置”，但失效路径只能是导航便利信息；它不能覆盖最近成功数据集，也不能自动成为 Current Dataset。

### 适合与不适合 ReacNet Scope 的模式

适合：允许根、面包屑、路径补全、恢复上次目录、普通项与不可访问项的文件系统级反馈。

不适合：把完整文件管理器放在首次主入口；暴露删除、重命名、复制等与分析无关且危险的文件操作；仅按扩展名选择单文件。

## 4. Apache Superset：连接/上传分工与权限能力

### 首次入口如何区分来源

**事实：** Superset 把“连接数据库”和“上传 CSV/Excel 到数据库”作为不同任务。连接流程先选择数据库类型；上传流程要求该功能在目标数据库上显式启用。[Creating your first dashboard — Connect Database](https://superset.apache.org/docs/using-superset/creating-your-first-dashboard/)；[Exploring Data in Superset — Enabling Data Upload](https://superset.apache.org/docs/using-superset/exploring-data/)

**启示：** 不同生命周期的来源不应塞进同一个通用弹窗。ReacNet 当前选择数据集是“绑定服务器已有对象”，不是上传，也不是配置长期数据库连接。

### 如何浏览和确认候选

**事实：** CSV 上传时用户选择客户端文件、目标 Database 和 Schema、输入 Table Name，并可设置日期列等文件解析选项；数据库连接则先选择引擎类型，再填写对应连接字段。[Exploring Data in Superset — Loading CSV Data](https://superset.apache.org/docs/using-superset/exploring-data/)

**启示：** 只要求与当前来源真正相关的字段。ReacNet 不应让普通用户填写类似 `base` 的实现细节，正如连接 Postgres 时不会要求用户选择驱动内部对象名称以完成每次查询。

### 如何展示可用性和校验

**事实：** 数据库连接提供 Test Connection，再由 Connect 保存；官方 API 为连接测试定义独立端点及 200、400、422、500 等响应。文件上传还受 `allow_file_upload` 与允许 schema 的安全配置约束。[Creating your first dashboard](https://superset.apache.org/docs/using-superset/creating-your-first-dashboard/)；[Test a database connection API](https://superset.apache.org/developer-docs/api/test-a-database-connection/)

**启示：** 校验应该同时回答“能否访问”和“允许做什么”。ReacNet 的状态摘要应区分：路径访问失败、源文件不完整、源文件可加载、某项索引尚未准备。

### 成功后的落点和下一步

**事实：** 官方 CSV 教程在 Upload 后让用户到 Datasets 查看新表，并点击该 dataset 才进入 Explore；也就是说，注册成功与开始分析是两个明确动作。[Exploring Data in Superset — Table Visualization](https://superset.apache.org/docs/using-superset/exploring-data/)

**启示：** 数据集成功加载后先进入或保留在数据集概览，再由用户显式选择分析任务，是成熟产品中常见且安全的落点。

### 错误恢复和最近使用

**事实：** 数据库连接会保存为可管理对象；连接失败时用户仍在同一配置上下文中修正 URL、认证或网络设置。Superset 的官方流程没有提供与 OpenRefine 最近项目或 Galaxy History 同等强的“最近数据”模型。[Database connection workflow](https://superset.apache.org/docs/using-superset/creating-your-first-dashboard/)

**启示：** 借鉴“失败不丢配置”，但不要把 Superset 当作最近数据设计的主要参照。

### 适合与不适合 ReacNet Scope 的模式

适合：连接测试与保存分阶段、权限决定可见能力、成功后先注册数据集再进入 Explore。

不适合：Database / Schema / Table 三层目标选择、管理员先启用上传、把文件复制进数据库、BI 图表优先的下一步。

## 5. Grafana：能力相关的下一步，而不是单一路由

### 首次入口如何区分来源

**事实：** Grafana 的对象不是文件，而是到存储后端的长期 data source 连接。管理员从 Connections → Data sources 搜索具体数据源类型，可按 Data source 过滤，再进入该类型专属配置。[Data sources — Add a data source](https://grafana.com/docs/grafana/latest/datasources/)

**启示：** 候选首先按“是什么”组织，再显示配置；ReacNet 可以直接搜索/展示已发现的数据集，而不是先让用户在目录树里寻找技术文件。

### 如何浏览和确认候选

**事实：** 选择数据源类型后，Grafana 使用该类型自己的配置和查询编辑器；不同数据源的编辑器可以提供自动补全、指标建议或可视化构建器。[Data sources — Query editors](https://grafana.com/docs/grafana/latest/datasources/)

**启示：** 选择器和加载后的工具都应由数据集能力描述符驱动，而不是由统一默认页驱动。

### 如何展示可用性和校验

**事实：** 例如 Prometheus 配置以 Save & test 完成保存和连接测试，并显示成功查询 Prometheus API 的确认；故障文档按连接、TLS、认证和配置等类型给出原因与修复方向。[Configure the Prometheus data source](https://grafana.com/docs/grafana/latest/datasources/prometheus/configure/)；[Troubleshoot general data source issues](https://grafana.com/docs/grafana/latest/datasources/troubleshooting/)

**启示：** ReacNet 的“加载并使用”也可以是一次原子验证和切换，但反馈必须指出验证通过了什么，以及哪些能力仍需准备。

### 成功后的落点和下一步

**事实：** Grafana 在数据源配置后提供多个下一步：Explore、构建 Dashboard、设置 Alert、使用变量等；部分数据源配置页还提供 Build a dashboard 的建议入口。默认数据源只是让它在 Explore、新 panel 或 alert rule 中预选，不会把保存动作强制路由到其中一个页面。[Data sources — Next steps and default data source](https://grafana.com/docs/grafana/latest/datasources/)

**启示：** 这是对 ReacNet 最直接的落点参照：加载成功留在数据集上下文，展示“物种检索”“反应式检索”“准备时间演化索引”等能力相关 CTA；默认能力可以预选，但不能替用户跳转。

### 错误恢复和最近使用

**事实：** 配置后的数据源保留在 Connections → Data sources 管理列表；Save & test 失败后可依据同一配置页和针对性故障指南修正。Grafana 强调的是已配置连接，而不是最近访问文件。[Data source management and troubleshooting](https://grafana.com/docs/grafana/latest/datasources/)

**启示：** Current Dataset 与最近数据集应是持久的可管理对象；失败候选则保持为候选，不能污染已验证对象。

### 适合与不适合 ReacNet Scope 的模式

适合：按类型搜索、原子“保存并测试”、健康检查反馈、默认项只负责预选、成功后多个能力相关下一步。

不适合：插件目录、管理员连接配置、认证和网络字段、把每个数据集建成长生命周期共享连接。

## 对 ReacNet Scope 的推荐操作模型

### 先明确三种对象

界面和状态中应始终区分：

1. **浏览位置**：服务器上当前正在查看的目录，只影响导航；
2. **候选数据集**：由轻量发现得到、尚未替换当前数据集的 ReacNetGenerator 数据集；
3. **当前数据集**：已通过原子验证并供分析工具消费的对象。

这比“导入”一词更重要。当前部署并没有把数据从客户端传入服务器；主动作更准确的名称是“选择并使用服务器数据集”。

### 推荐主流程

```text
选择数据集
  ├─ 最近使用的数据集
  ├─ 已配置的数据根目录
  └─ 高级：浏览/粘贴服务器路径
          ↓
发现候选数据集（按公共前缀聚合，不展示 base 术语）
          ↓
候选预览
  ├─ 识别证据：核心源文件、时间范围、规模
  ├─ 现在可用：可立即进入的工具
  ├─ 准备后可用：所缺索引和准备动作
  └─ 不可用：缺少的源文件或访问错误
          ↓
加载并使用（一次原子验证和切换）
          ↓
当前数据集概览
  ├─ 开始物种检索（仅当可用）
  ├─ 开始反应式检索（仅当可用）
  ├─ 查看已有事件/轨迹（仅当可用）
  └─ 准备索引（对尚未准备的能力）
```

### 推荐状态语义

| 状态 | 用户看见什么 | 是否替换 Current Dataset |
| --- | --- | --- |
| 浏览中 | 当前服务器位置、子目录、发现进度 | 否 |
| 未发现候选 | “这里没有 ReacNetGenerator 数据集”，仍可继续导航 | 否 |
| 候选已发现 | 数据集卡和能力预览 | 否 |
| 验证中 | 持续可见的步骤/状态；允许关闭选择器后继续观察 | 否 |
| 验证失败 | 失败原因、保留候选、刷新/修正路径/查看准备动作 | 否 |
| 加载成功 | Current Dataset 更新，展示能力和下一步 | 是 |
| 索引准备中/失败 | 只影响对应能力，不把整个数据集标为不可用 | 否 |

关键原则来自 Galaxy 的持续状态和错误保留、OpenRefine 的提交前预览，以及 Grafana 的能力相关下一步，而不是某一个产品的逐像素模仿。

## 应采用和应避免的模式

### 应采用

- **来源先于路径。** 当前只支持服务器数据就只显示这个主入口；未来新增客户端上传或 URL 时再并列分流。
- **候选以数据集而不是目录呈现。** 目录是导航手段，数据集卡才是选择对象。
- **自动发现不等于自动切换。** 单候选可自动选中，但 Current Dataset 只在明确确认后原子替换。
- **校验输出能力，而不只输出文件数。** 用“可开始物种检索”“需先准备时间索引”替代“1 项源数据 / 2 项稍后启用”。
- **失败不清空旧工作区。** 候选、错误细节和修复动作留在原位；已加载数据集继续可用。
- **成功留在数据集概览。** 下一步由已验证能力产生，避免硬编码到某个分析页。
- **最近成功数据集优先。** 最近浏览目录可恢复，但地位低于最近成功加载的数据集。

### 应避免

- 把 JupyterLab 式通用服务器文件管理器直接作为首次主界面；
- 为尚未实现的本地上传或 URL 导入增加装饰性入口；
- 照搬 Superset 的数据库、schema、表名等实现字段；
- 只用颜色或抽象计数表示状态；
- 验证失败后回到空白选择页，或在失败前就清空 Current Dataset；
- 加载成功后根据一个固定默认页自动跳转；
- 为使用已有大型数据而做一次不必要的服务器内复制。

## 对“是否只是当前 Web UI 问题”的判断

不是单纯的视觉或组件问题，也不是 Web 应用天然做不好服务器数据选择。Galaxy、JupyterLab、Superset 和 Grafana 都证明 Web UI 可以清楚处理服务器资源、权限、异步验证和成功后的工作区。

当前问题可以分成三层：

1. **领域对象层：** 数据集、目录、文件、索引和能力的边界没有稳定呈现；这是首要问题。
2. **工作流层：** 浏览、候选、验证、应用和分析跳转耦合；这是第二问题。
3. **界面表现层：** 文案、按钮层级、摘要和加载反馈不足；这是结果而不是根因。

因此，不建议先重做视觉样式。应先把服务层输出固化为一个 dataset descriptor，并让选择器、Current Dataset 概览和分析导航共同消费同一份能力描述。完成这一层后，再调整布局和文案才不会继续互相矛盾。

## 建议的最小改动顺序

1. 加载成功后固定停留在 Current Dataset 概览，移除硬编码自动跳转。
2. 用能力描述符生成下一步按钮，并把摘要改为“现在可用 / 准备后可用 / 缺少”。
3. 把最近成功数据集和已配置数据根目录放到选择器首屏；完整服务器浏览放入高级入口。
4. 将耗时验证/索引准备改为可持续观察的状态，并确保失败保留旧 Current Dataset。
5. 若未来真正实现客户端上传或 URL 抓取，再按 OpenRefine/Galaxy 模式增加明确的来源一级分流。

这五步不要求更换 Dash，也不要求先引入新的前端框架；它们首先是领域状态和操作边界的调整。
