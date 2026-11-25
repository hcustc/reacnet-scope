# ADR 索引

ADR（Architecture Decision Record，架构决策记录）记录难以逆转的设计决定及其理由。
ADR 只追加、不删除：决定改变时新写一份 ADR，并在旧 ADR 顶部注明替代范围。

**单一事实源**：日常开发以根目录 [CONTEXT.md](../../CONTEXT.md)（术语与身份）和
[软件设计基准](../software-design-baseline.md)（现行产品与科学契约）为准；本目录顶层仅保留
现行有效的核心架构与科学决策。已替代或属于早期 UI／导入流程过渡态的 ADR 已归档至
[`archive/`](archive/)，其仍有效的条款均已汇总进设计基准，**日常开发与验收无需逐篇回读归档**。
新增与维护规则见 [domain.md](../agents/domain.md)。

状态含义：

- **有效**：全部决定仍有效。
- **部分替代**：只有“替代或细化”列出的条款失效或改变，其余仍有效；使用前读该 ADR 顶部说明。
- **已归档**：位于 `archive/`，包含已被替代的历史决定以及结论已完全并入设计基准的 UI／流程演进记录，仅作历史背景查阅。

## 现行核心 ADR（22 份）

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
| [0012-estimate-channel-rates…](0012-estimate-channel-rates-from-prepared-exposure.md) | 用准备好的暴露量估计表观速率常数 | 部分替代 | 0014（已归档）：不在普通通道查询中计算；0034：移入可选扩展 |
| [0013](0013-separate-candidate-discovery-from-continuous-md-support.md) | 候选发现与连续 MD 支持分开 | 部分替代 | 0026：双端搜索不强制步数上限 |
| [0016](0016-require-event-local-carrier-transfer.md) | 候选步骤要求事件内的载体原子传递 | 有效 | |
| [0018](0018-explore-segment-occurrence-lineage.md) | 基于分段与事件的分子谱系探索 | 部分替代 | 0020（已归档）：谱系不再是开发主线 |
| [0019](0019-separate-anchor-continuity-and-atom-provenance.md) | 分开记录锚点连续性与原子来源 | 有效 | 补充 0018 |
| [0026](0026-search-candidates-by-anchor-mode.md) | 候选搜索的三种锚点模式 | 有效 | |
| [0031](0031-show-net-reaction-directions-in-candidate-workbench.md) | 候选工作台默认按净反应方向展示 | 有效 | |
| [0032](0032-constrain-candidate-carriers-by-waypoints-and-exclusions.md) | 候选路线支持途经与排除物种 | 有效 | |
| [0033](0033-make-direct-channels-the-mainline-and-candidates-optional.md) | 直接通道为主线，候选路径为可选工具 | 有效 | |
| [0034](0034-deprecate-v3-candidate-ranking-and-split-frozen-capabilities.md) | 弃用 v3 候选评分，冻结能力按用途处置 | 有效 | 2026-11-09 修订：取消 `research` extra，scipy 并入核心依赖 |
| [0035](0035-annotate-representative-species-with-lewis-candidates.md) | 为代表物种生成可选的 Lewis 候选结构注释 | 有效 | |

## 已归档 ADR（`archive/`，14 份）

以下 ADR 已完全替代，或属于早期 Dash 界面／导入选择器演进记录（其现行有效规则已汇总至
[软件设计基准](../software-design-baseline.md) §8、§10、§11），仅保留历史溯源，不作为日常阅读或验收入口：

| ADR | 决定 | 归档原因 |
| --- | --- | --- |
| [0012-discover-only-sampled…](archive/0012-discover-only-sampled-candidate-paths.md) | 候选路径只发现采样到的路径 | 已替代（由 0013 替代） |
| [0014](archive/0014-converge-dash-around-five-workspaces.md) | Dash 收敛为五个工作区，冻结独立工具 | 过渡态 UI 改版（由 0015、0025、0029、0034 部分替代，现行规则见设计基准 §4/§10） |
| [0015](archive/0015-add-indexed-candidate-task-to-reaction-workspace.md) | 在反应入口加入索引化候选任务 | 过渡态 UI 改版（由 0017、0026 部分替代，现行规则见设计基准 §10/§11.5） |
| [0017](archive/0017-qualify-candidate-events-and-check-selected-history.md) | 候选事件质量视图与所选路线历史检查 | 过渡态视图改版（由 0031 部分替代，现行规则见设计基准 §11.5） |
| [0020](archive/0020-center-dash-on-candidate-step-occurrences.md) | 候选步骤到具体事件的交接、返回与导出 | 过渡态 UI 改版（由 0029、0033 部分替代，现行规则见设计基准 §3/§10） |
| [0021](archive/0021-import-explicit-rng-file-collections.md) | 显式导入 RNG 文件集合 | 过渡态导入流程（由 0023、0024 部分替代，现行规则见设计基准 §8.2） |
| [0022](archive/0022-support-server-files-and-resumable-browser-uploads.md) | 支持服务器文件与浏览器断点上传 | 已替代（由 0023 替代） |
| [0023](archive/0023-read-data-on-the-application-host.md) | 只读取软件运行所在机器上的数据 | 过渡态导入流程（由 0024 部分替代，现行规则见设计基准 §8.2） |
| [0024](archive/0024-import-rng-output-folders.md) | 以 RNG 输出文件夹为导入对象 | UI 流程规则已并入设计基准 §8.2 |
| [0025](archive/0025-imported-dataset-library.md) | 已导入 RNG 数据列表 | 过渡态 UI 改版（由 0029 部分替代，现行规则见设计基准 §8.2） |
| [0027](archive/0027-center-dash-on-persistent-research-species.md) | 以持久“研究物种”为中心 | 已替代（由 0029、0030 替代） |
| [0028](archive/0028-integrate-comparison-into-analysis-tasks.md) | 对比并入分析任务 | 过渡态 UI 改版（由 0029、0030 部分替代，现行规则见设计基准 §10/§11.10） |
| [0029](archive/0029-open-analysis-from-species-reactions-evolution-and-events.md) | 五个平级入口：RNG 数据、物种、反应、演化、事件 | 过渡态 UI 改版（由 0030 部分替代，现行规则见设计基准 §10） |
| [0030](archive/0030-use-local-species-focus-in-dash.md) | 用物种详情的局部焦点代替全局研究物种 | UI 状态规则已并入设计基准 §10 |

新增 ADR 时同步更新本表，并在被替代的 ADR 顶部写明替代范围。
