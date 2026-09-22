# Dash 工作台 UI 开发约定

四个工作区共用紧凑桌面布局：当前RNG 数据、工作区导航、任务导航、查询条件、结果工具栏、结果和详情。领域身份、能力、索引及导出契约仍以 [软件设计基准](software-design-baseline.md) 为准。

## 组件与依赖

- `scripts/webapp_dash/ui_components.py` 提供 Community AG Grid、列定义适配、稳定行身份、反馈和结果工具栏。
- `scripts/webapp_dash/ui_state.py` 管理表单反馈、Enter 提交、详情关闭和任务入口。科学计算仍调用核心服务。
- `assets/workbench.css` 统一布局尺寸、字体、间距、控件、表格和响应式详情；`app.css` 保留图、轨迹、数据选择器等功能样式。新通用样式集中写入前者，避免在后者追加同类覆盖。
- `assets/grid_components.js` 提供行身份和结构预览。预览使用本应用 SVG 接口，按需加载，不执行数据中的 HTML。
- `assets/workbench.js` 支持 Escape 关闭非模态详情；打开模态框时由 Bootstrap 接管 Escape。
- 使用 Dash AG Grid Community，不启用 Enterprise 模块。保留 Dash / dash-bootstrap-components、Plotly、Cytoscape 和 3Dmol，不增加 Node 构建链。
- `assets/bootstrap-local.css` 为 Bootstrap **5.3.8** 的完整本地 CSS，并保留同目录 MIT 许可证。通过 external_stylesheets 的本地地址先加载，随后加载应用 CSS；不依赖 CDN 或 Bootstrap JavaScript。

更新 Bootstrap 时从官方发行包取得 CSS，核对许可证和完整性，并重验 Modal、Dropdown、Offcanvas、Collapse、焦点和移动布局；不要在 vendor 文件中混入业务覆盖样式。

## 表格约定

所有结果表使用 `result_grid()`。原有服务列元数据通过 `columns()` 转换；服务与 CSV schema 不因为 UI 库迁移而改变。

| 旧 DataTable 属性 | 当前接口 |
| --- | --- |
| `data` / `columns` | `rowData` / `columnDefs` |
| `selected_rows` / `selected_row_ids` | `selectedRows`，内容为原始数据行对象 |
| `active_cell` | `cellClicked`，使用 `rowId` |
| `page_current` | `paginationGoTo` |
| `hidden_columns` | `columnState` 中的 `hide` |
| `tooltip_data` | `<grid-id>-previews` Store，由共享回调按行身份关联 |
| `page_size` | `<grid-id>-page-size` Store |
| `data_timestamp` | 编辑回调使用 `cellValueChanged` |

不能将 DOM 顺序、可见行号或排序后的下标作为 Species、Reaction、Occurrence 或候选路径身份。服务已有 `id` 时沿用；物种使用精确 SMILES，质量聚合行使用分子式。选择对象需要在当前结果中重新解析，不能直接信任旧选择中的数值。

浏览器端排序只作用于已加载结果。使用服务端事件分页的表格关闭本页排序，继续使用原有翻页服务与总数说明，避免把局部排序呈现为全体排序。列宽可调整；默认不启用第二套浏览器筛选，避免与科学查询表单混淆。

## 查询与状态

- 物种和反应查询记录已提交条件；改动输入时保留上次结果并显示“条件已修改”。用户点击查询或按 Enter 才重新计算。
- 查询运行时禁用重复提交。初始等待、运行、有效空结果、缺少能力和失败分别反馈；错误不包装成成功空结果。
- 自动类型识别为质量或手动选择质量时显示容差；零值有效，负数和非有限数值拒绝提交到服务。
- `guarded_query()` 将服务器计算和浏览器提交分开。响应携带提交序号及 dataset_id/source_revision/artifacts；提交时任一不匹配即丢弃，防止迟到结果覆盖当前上下文。
- RNG 数据切换继续使用原有两阶段事务；检查候选不更改当前RNG 数据。表格初始化的空选择不能清空已恢复的当前RNG 数据。
- 多来源对比的上下文仍由其来源集合决定；后台候选分析和 preparation 继续使用原有任务状态、取消和修订检查。

## 布局与键盘

桌面顶栏 56px、侧栏 192px、主要控件 36px、表格行 36px、主要间距 12px。使用帮助默认折叠；导出与结果操作位于结果区。反应工作区通过同一层导航进入直接反应、候选路径和具体事件。

物种详情在宽屏与结果并排；低于 1200px 时显示右侧非模态面板。进入详情聚焦关闭按钮，Escape 或关闭按钮返回结果；再次点击同一行可以重开。窄屏查询条件换行，表格在自身区域滚动，页面不应横向溢出。

## 验证入口

行为单元测试：`tests/test_workbench_ui.py`；既有 Dash HTTP、身份交接、索引、对比与导出测试继续覆盖服务边界。

浏览器验收：`tests/browser/test_workbench.py`，使用临时 RNG 数据和本机服务器；包含排序后精确选择、结构预览、CSV 下载、Enter/Escape、旧请求/旧RNG 数据/旧修订响应、四个工作区导航、质量容差零值和首屏结果空间。安装和运行方式见 [验证指南](agents/testing.md)。

小型 fixture 和 Chromium 验收不能代替大型真实数据吞吐、物理单位确认、复杂轨迹渲染或跨平台验收。

## 多文件夹导入与使用

1. 打开左侧“RNG 数据”，点击列表中的“添加RNG 数据”，或使用顶部同名按钮。在目录列表点“加入”，或点“加入当前文件夹”；也可展开“一次填写多个文件夹路径”，每行填写一个软件运行机器可访问的目录。
2. 核对待导入列表，点击“导入所选文件夹”。成功项保存在当前浏览器；失败项显示原因。每个文件夹保持独立，导入不切换正在分析的数据，也不自动准备索引。
3. 在任意分析页的顶部选择已导入文件夹，点“使用”；验证成功后在当前页面使用新数据。失败时原数据保留。
4. 在“物种与趋势”或“反应与事件”的“多来源对比”任务中选择多个已导入RNG 数据。比较来源选择不改变普通分析使用的当前RNG 数据；所需索引与精确结构仍按来源分别确认。

“已导入的RNG 数据”可以移出引用，不删除原始文件或工作区。列表按浏览器与站点保存，刷新保留，不在不同浏览器之间同步。相关回归见 `tests/test_dataset_library.py` 和浏览器多文件夹用例。

RNG 数据页面默认显示全部已导入引用，可逐项使用或移出列表，不提供对比控件或数量门槛。索引管理在“当前数据与准备任务”页签；从能力提示进入准备管理时直接打开该页签。

导入页没有待导入列表时，“导入当前文件夹”直接提交已打开目录；存在列表时按钮显示数量，仅导入列表中的文件夹。尚未打开目录时给出操作提示，不把空请求报告成文件导入失败。
