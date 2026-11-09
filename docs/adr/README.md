# ADR 索引

ADR（Architecture Decision Record，架构决策记录）记录难以逆转的设计决定及其理由。
ADR 只追加、不删除：决定改变时新写一份 ADR，并在旧 ADR 顶部注明替代范围。当前产品契约
以[软件设计基准](../software-design-baseline.md)为准，术语以 [CONTEXT.md](../../CONTEXT.md)
为准；使用与新增规则见 [domain.md](../agents/domain.md)。

状态含义：

- **有效**：全部决定仍有效。
- **部分替代**：只有“替代或细化”列出的条款失效或改变，其余仍有效；使用前读该 ADR 顶部说明。
- **已替代**：仅作历史记录，不作验收依据。

有两个 `0012-*`，引用时写完整文件名。

| ADR | 决定 | 状态 | 替代或细化 |
| --- | --- | --- | --- |
| [0001](0001-prefer-complete-native-timed-evidence.md) | 优先使用完整的原生 timeline 证据，存在但不完整时明确失败 | 有效 | |
| [0002](0002-expand-aggregated-reactions-into-occurrences.md) | 聚合反应计数展开为独立 Reaction Occurrence | 有效 | |
| [0003](0003-use-storage-independent-occurrence-identities.md) | Occurrence Identity 不依赖存储布局 | 有效 | |
| [0004](0004-centralize-timed-evidence-behind-adapters.md) | 用统一适配器读取 timed evidence | 有效 | |
| [0005](0005-prefer-dataset-local-workspaces.md) | 派生状态默认放在数据集旁的工作区 | 有效 | |
| [0006](0006-keep-element-distribution-analysis-generic.md) | 元素分布分析不写死特定元素 | 有效 | |
| [0007](0007-index-species-abundance-evidence-offline.md) | 物种丰度离线建索引 | 有效 | |
| [0008](0008-consolidate-supported-product-surfaces.md) | 只支持 Dash、CLI 和 Python 包三种产品界面 | 有效 | |
| [0009](0009-exclude-route-from-event-evidence.md) | `.route` 不作事件证据 | 有效 | |
| [0010](0010-derive-dft-geometries-from-matched-occurrences.md) | DFT 初始几何只取自已匹配事件的前后帧 | 有效 | |
| [0011](0011-prepare-molecular-continuity-for-query-time-fate.md) | 命运分析：离线准备分子连续性，查询时计算 | 部分替代 | 0034：命运分析移入可选扩展 |
| [0012-discover-only-sampled…](0012-discover-only-sampled-candidate-paths.md) | 候选路径只发现采样到的路径 | 已替代 | 0013 |
| [0012-estimate-channel-rates…](0012-estimate-channel-rates-from-prepared-exposure.md) | 用准备好的暴露量估计表观速率常数 | 部分替代 | 0014：不在普通通道查询中计算；0034：移入可选扩展 |
| [0013](0013-separate-candidate-discovery-from-continuous-md-support.md) | 候选发现与连续 MD 支持分开 | 部分替代 | 0026：双端搜索不强制步数上限 |
| [0014](0014-converge-dash-around-five-workspaces.md) | Dash 收敛为五个工作区，冻结独立工具 | 部分替代 | 0015：候选任务回到反应入口；0025、0029：导航；0034：冻结能力处置 |
| [0015](0015-add-indexed-candidate-task-to-reaction-workspace.md) | 在反应入口加入索引化候选任务 | 部分替代 | 0026：搜索模式；0017：历史检查控件 |
| [0016](0016-require-event-local-carrier-transfer.md) | 候选步骤要求事件内的载体原子传递 | 有效 | |
| [0017](0017-qualify-candidate-events-and-check-selected-history.md) | 候选事件质量视图与所选路线历史检查 | 部分替代 | 0031：Dash 默认改为净方向视图 |
| [0018](0018-explore-segment-occurrence-lineage.md) | 基于分段与事件的分子谱系探索 | 部分替代 | 0020：谱系不再是开发主线 |
| [0019](0019-separate-anchor-continuity-and-atom-provenance.md) | 分开记录锚点连续性与原子来源 | 有效 | 补充 0018 |
| [0020](0020-center-dash-on-candidate-step-occurrences.md) | 候选步骤到具体事件的交接、返回与导出 | 部分替代 | 0029：导航位置；0033：候选不再与直接通道并列为主线 |
| [0021](0021-import-explicit-rng-file-collections.md) | 显式导入 RNG 文件集合 | 部分替代 | 0023：部署范围；0024：主导入流程 |
| [0022](0022-support-server-files-and-resumable-browser-uploads.md) | 支持服务器文件与浏览器断点上传 | 已替代 | 0023 |
| [0023](0023-read-data-on-the-application-host.md) | 只读取软件运行所在机器上的数据 | 部分替代 | 0024：主导入流程 |
| [0024](0024-import-rng-output-folders.md) | 以 RNG 输出文件夹为导入对象 | 有效 | |
| [0025](0025-imported-dataset-library.md) | 已导入 RNG 数据列表 | 部分替代 | 0029：导航分组 |
| [0026](0026-search-candidates-by-anchor-mode.md) | 候选搜索的三种锚点模式 | 有效 | |
| [0027](0027-center-dash-on-persistent-research-species.md) | 以持久“研究物种”为中心 | 已替代 | 0029、0030 |
| [0028](0028-integrate-comparison-into-analysis-tasks.md) | 对比并入分析任务 | 部分替代 | 0029：导航位置；0030：研究物种 |
| [0029](0029-open-analysis-from-species-reactions-evolution-and-events.md) | 五个平级入口：RNG 数据、物种、反应、演化、事件 | 部分替代 | 0030：研究物种 |
| [0030](0030-use-local-species-focus-in-dash.md) | 用物种详情的局部焦点代替全局研究物种 | 有效 | |
| [0031](0031-show-net-reaction-directions-in-candidate-workbench.md) | 候选工作台默认按净反应方向展示 | 有效 | |
| [0032](0032-constrain-candidate-carriers-by-waypoints-and-exclusions.md) | 候选路线支持途经与排除物种 | 有效 | |
| [0033](0033-make-direct-channels-the-mainline-and-candidates-optional.md) | 直接通道为主线，候选路径为可选工具 | 有效 | |
| [0034](0034-deprecate-v3-candidate-ranking-and-split-frozen-capabilities.md) | 弃用 v3 候选评分，冻结能力按用途处置 | 有效 | |
| [0035](0035-annotate-representative-species-with-lewis-candidates.md) | 为代表物种生成可选的 Lewis 候选结构注释 | 有效 | |

新增 ADR 时同步更新本表，并在被替代的 ADR 顶部写明替代范围。
