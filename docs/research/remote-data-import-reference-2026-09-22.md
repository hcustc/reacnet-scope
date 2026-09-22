# 远程部署的数据导入参考

调研日期：2026-09-22。以下保留历史调研；上传方案已撤回，当前同机读取需求以 [ADR-0023](../adr/0023-read-data-on-the-application-host.md) 为准。

当前 ReacNet Scope 的 [ADR-0021](../adr/0021-import-explicit-rng-file-collections.md)
与[设计基准 §5](../software-design-baseline.md)以服务器可访问文件为输入，明确排除浏览器上传。
下述客户端上传是后续设计建议，不代表当前已实现，也不通过研究文档修改上述契约。
本部分只核对 Galaxy 与 Girder 的官方文档及公开源码；没有部署这两个系统进行运行验收。

## Galaxy：统一入口，区分来源与接收状态

### 已核实事实

- Galaxy 区分 file source（可以浏览、导入的原始文件来源）与 object store（接收数据集的存储位置）。来源可以是服务器 POSIX 文件系统、S3、WebDAV 等；普通导入通常会复制数据，因此不能把“浏览远程文件”直接理解为“不复制导入”。[官方管理员文档](https://docs.galaxyproject.org/en/latest/admin/data.html#datasets-vs-files)
- POSIX 来源源码提供 `prefer_links` 配置，默认关闭；普通 `_realize_to` 分支会复制文件，且访问受配置根目录及链接检查约束。它说明复制策略需要来源/部署配置，不能从文件选择器外观推断。[官方 POSIX 来源源码](https://docs.galaxyproject.org/en/latest/_modules/galaxy/files/sources/posix.html)
- 官方入门材料把本机文件选择/拖放、URL 获取及已配置来源放在数据获取流程中；选择后显示队列，启动后在历史记录呈现数据状态。URL 获取由服务器完成，可以省去先下载到本机再上传这一轮转运。[官方数据导入教程](https://training.galaxyproject.org/training-material/topics/galaxy-interface/tutorials/get-data/slides-plain.html)
- 当前官方服务说明明确介绍基于 tus 的可恢复浏览器上传，且说明 usegalaxy.org 不提供 FTP 上传。旧教程中的浏览器 100 MB、1 GB、2 GB 阈值不应当作为今天的产品规则。[官方服务说明](https://galaxyproject.org/ftp-upload/)
- 官方部署教程使用独立 tusd 进程接收数据，以免大规模上传占用主要 Galaxy 进程；反向代理、上传目录和认证交接都是实际部署的一部分，不能只替换前端按钮。[官方 tus 部署教程](https://training.galaxyproject.org/training-material/topics/admin/tutorials/tus/tutorial.html)
- 上传本机文件仍需要浏览器保持运行；服务器上的分析任务和 URL 获取则可以在关闭浏览器后继续。这是不同阶段的生命周期，不能因为“可恢复”就承诺关闭电脑后仍持续上传。[官方任务运行 FAQ](https://training.galaxyproject.org/training-material/faqs/galaxy/analysis_jobs_keep_running.html)

版本说明：Galaxy `latest` 页面标记为开发版（此次访问显示 26.2.dev0）；以上不是对任意已部署 Galaxy 版本的保证。产品交互参考与部署选择应分别核验。

### 对 ReacNet 的建议（推论）

复用“一个添加数据入口、明确的数据来源、统一的后续队列”模式；避免照搬 Galaxy 的通用元数据表单。
界面用“服务器文件”和“本机上传”区分文件所在位置；本机上传项额外显示传输进度，完成后接入同一 RNG 文件识别、分组与准备流程。
不能把传输完成显示成分析就绪，也不把浏览器选中的本机路径当作服务器路径。

## Girder：文件登记与字节存储分离

### 已核实事实

- Girder 的 Item 可以容纳多个共同构成一份数据的 File；File 与存储字节的 Assetstore 分离。普通文件系统上传进入服务器的内容寻址存储；S3 后端可以让浏览器与 S3 直接传输，Girder 提供授权。[官方用户指南](https://girder.readthedocs.io/en/stable/user-guide.html#concepts)
- `FilesystemAssetstoreAdapter.importFile` 只登记现有文件的绝对路径、大小、修改时间并标记 `imported`；未复制文件字节。删除已登记文件时，源码对 `imported` 文件直接返回，不删除外部原始文件。它是“服务器数据原位登记”的明确例子；文件移动或消失后仍需处理失效引用。[官方文件系统适配器源码](https://girder.readthedocs.io/en/latest/_modules/girder/utility/filesystem_assetstore_adapter.html)
- 上传 API 创建上传记录，按分块接收；`requestOffset` 查询恢复偏移，文件系统适配器返回临时文件大小，取消则删除上传临时文件。可恢复传输依赖持久的上传会话和接收端状态，不是仅靠进度条。[官方 API 文档](https://girder.readthedocs.io/en/stable/api-docs.html#girder.models.upload.Upload.requestOffset)

版本说明：此次访问 Girder stable API 显示 5.0.18；latest 适配器源码为开发版本。
此处核实的是后端模型与 API，不声称默认网页已经具备指定的跨浏览器/关闭页面恢复体验。

### 对 ReacNet 的建议（推论）

服务器现有文件继续只读登记；本机上传写入专门的接收区，上传完整后再成为可登记的源文件。
两种来源共用 Dataset Candidate 与角色识别，但保留不同的所有权与删除策略。
用户清空选择不得删除服务器原文件；取消上传可以清理其未完成的临时数据。
后续如需对象存储，再考虑直传；当前不必为了文件选择器引入完整 Girder 服务。

## 补充参考：ParaView、JupyterLab 与 Uppy

### ParaView：浏览计算端文件

官方 6.1 文档 §8.1、§8.2.5 明确说明：客户端连接 `pvserver` 后，文件选择器浏览服务器文件系统，数据处理发生在服务器。它还在 Pipeline Browser 显示服务器连接身份。这与 RNG 和程序同在远程服务器的使用方式直接对应。[官方文档](https://docs.paraview.org/en/latest/ReferenceManual/parallelDataVisualization.html)

**对本项目的建议**：服务器来源明确显示部署名称与当前位置，用文件列表、面包屑、最近位置完成选择；已有文件保持原位引用。选择目录只做批量添加，不重新定义 Dataset Candidate 的成员边界。不要依靠长说明段落解释文件位于哪台机器。

### JupyterLab：上传是文件浏览器中的明确动作

JupyterLab 支持向文件浏览器当前文件夹拖放文件，或点击 Upload Files 上传；文档也区分“隐藏按钮”和“禁用全部上传（包括拖放）”。这提供了清晰的动作语义，但该页面不构成对任意大小文件的性能保证。[官方文档](https://jupyterlab.readthedocs.io/en/stable/user/files.html#uploading-and-downloading)

**对本项目的建议**：本机来源应有明确的“本机上传”和拖放区域；服务器文件列表只浏览服务器来源。两种来源汇入一份已选文件列表，复用文件识别、归组和冲突处理。

### Uppy + tus：上传进度与可恢复传输

Uppy 的 Tus 插件接入基于 HTTP 的可恢复上传协议，需要客户端和服务端配套实现；Dashboard 提供上传进度、暂停/恢复、重试与取消。这是传输组件参考，不是 RNG 数据集管理方案。[Tus 插件](https://uppy.io/docs/tus/)、[Dashboard](https://uppy.io/docs/dashboard/)

Dash 原生 `dcc.Upload.contents` 将文件内容作为 base64 字符串交给回调。**工程判断**：不宜把大型 MD 工件全部送入这条回调/Store 数据通道；优先评估独立的流式可恢复上传端点，再把完成后的文件标识交给 Dash。此判断来自传输表示与本项目大型证据边界，并非 Dash 官方给出的固定大小阈值。[Dash Upload 文档](https://dash.plotly.com/dash-core-components/upload)

采用这些组件时应本地打包静态资源，上传端点由项目自身部署。tus 支持恢复并不表示浏览器关闭后继续发送本机文件；重开页面后的记录恢复、重新选择原文件及验证属于需要单独验收的行为。

## 建议的 ReacNet Scope 交互（提案，未实施）

一个“添加 RNG 数据”入口，提供“服务器文件”和“本机上传”两个来源页签，当前部署默认前者。两种来源共享已选列表，文件与文件夹始终只是选择与批量添加方式，不新增目录层级限制。

| 场景 | 选择过程 | 主操作与后续 |
| --- | --- | --- |
| RNG 与程序都在服务器 | 浏览允许的数据位置，多选文件或批量添加文件夹内容 | 开始分析 → 引用原文件 → 按需准备 |
| RNG 在本机，程序在服务器 | 本机选择/拖放多个文件，显示大小与传输目标 | 上传并分析 → 可恢复传输 → 校验 → 按需准备 |
| 文件已经提前传到共享存储 | 从服务器文件来源选择计算端可见的挂载位置 | 按第一种方式处理，避免再次上传 |

交互收敛建议：

- 初始状态显示来源选择和文件浏览区域，去掉空的右侧“选择要分析的数据”卡片。
- 一组可分析数据自动选中，显示紧凑摘要；仅在多组或角色冲突时展开归组操作。名称不能证明属于同一次 RNG 运行，开始前仍需简短的归属确认语义。
- “补充当前数据集”放到已有数据集的上下文中；空列表不展示无意义的清空操作。
- 待上传文件存在时主按钮为“上传并分析”，记录这一次用户意图；传输/校验成功后自动衔接，失败或取消保留原 Current Dataset，不自行切换到另一草稿。
- 文件行显示上传进度或失败及重试；页面后台准备信息独立呈现。上传成功不等于索引已发布，也不等于所有分析能力可用。
- 大型坐标工件不应成为只做物种/反应分析的强制上传项，缺少时只影响相应能力。

## 实现边界与分期建议

当前 `scripts/webapp_dash/file_import.py` 的选择器解析服务器路径，使用允许根目录限制；`docs/dash_remote_deployment.md` 要求数据在服务端可访问的挂载位置。上传前端尚不存在于这条导入链。当前设计基准明确把客户端上传排除在已接受范围，ADR-0021 也把浏览器上传视为独立能力；本调研不修改这些已接受契约。

1. **近期：服务器文件选择体验**。明确来源名称，直接展示浏览区域、最近位置、文件大小；复用现有显式文件集合和切换事务。
2. **随后：本机可恢复上传**。引入受管理的输入存储、上传会话及完成校验，复用现有分析入口。实现时同步更新上传范围及原始输入保存规则。

输入存储必须与可随时清理的派生索引分开：已上传的 RNG 工件是分析源文件，不能被“清理缓存”删除。上传中间文件只在完成并验证后发布，取消/失败不能被分析器读取。服务进程和准备任务都须能访问最终保存位置；容器或多节点部署不能只检查网页服务所在节点的路径。磁盘配额、同名隔离、访问控制和取消后的临时文件回收属于上传能力本身的工程要求。

本轮仅检索官方资料并核对代码/文档，未部署上述外部产品，未测试大文件吞吐、网络中断恢复或跨平台上传，不据此承诺文件大小上限。
