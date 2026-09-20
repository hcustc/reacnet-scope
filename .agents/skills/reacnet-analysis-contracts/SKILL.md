---
name: reacnet-analysis-contracts
description: 新增或修改 ReacNet Scope 科学分析的身份、证据条件、时间/单位、结果 schema 或导出契约时使用，覆盖候选路径、路径验证、谱系、命运、动力学与 DFT 交接。纯索引存储、样式或机械重命名不需要此技能。
---

# 科学分析契约

目标是让核心分析、CLI、Dash 和导出对同一科学问题给出一致且可复核的结果。
先读 [领域词汇](../../../CONTEXT.md)、[核心包约定](../../../reacnet_scope/AGENTS.md)，
再读 [设计基准](../../../docs/software-design-baseline.md) 中本次分析对应的章节。

## 确定问题与证据

从用户请求、有效规范与现有调用点提取一个简短契约：

- 输入的精确身份、方向、计量、查询窗及限制；哪些值已由用户指定。
- 依赖的 Analysis Capability、已发布数据修订、物理时间与单位条件。
- 正常结果、有效空结果、缺证据、截断/未完成的差别。
- Python 结果与 CLI/Dash/导出中必须保持的字段、身份和来源信息。

这是实现依据，不是强制用户填写的问卷。已明确的设计直接执行；只澄清会改变科学结论且
无法从上下文解决的歧义。术语/决策需要改变时按 [领域文档规则](../../../docs/agents/domain.md)
同步记录，不能为迎合旧测试改写设计基准。

## 选择正确的分析语义

只读任务相关的行与引用，不把各类分析串成一个必经流程。

| 分析 | 关键区分与规范入口 |
| --- | --- |
| 搜索与 Direct Reaction Channel | 分子式用于发现，精确 Species/Reaction Type 用于分析；直接通道只有一步。见基准 6.1、11.1 |
| Candidate Path Discovery / Continuous MD Support | 有方向的逐步观测证据允许候选存在；完整分子链验证独立进行，未评估不等于不支持。结构身份与 revision/rank/validation 分离。见基准 6.2、11.5–11.6 及 [有效 ADR](../../../docs/adr/0013-separate-candidate-discovery-from-continuous-md-support.md) |
| Path Verification | 验证用户给出的完整有向反应序列；严格事件连续性，不能变成自动找路。同一 Transition 内没有可推导顺序。见基准 11.4 |
| Molecule Lineage / Species Fate | 使用具体分子实例与固定 anchor atoms；区分断裂后重现、terminal 与 censoring、first-hit 与 descendant-complete。见基准 11.7 及 [continuity ADR](../../../docs/adr/0011-prepare-molecular-continuity-for-query-time-fate.md) |
| 表观动力学 | 事件数除以已确认物理时间中的反应物暴露；二阶还需体积和 Å 确认。TP/净 TP 不能代替 k。见 [动力学 ADR](../../../docs/adr/0012-estimate-channel-rates-from-prepared-exposure.md) |
| DFT / QC 交接 | 几何来自 matched occurrence 的完整分子和明确帧；ready 不证明 TS、机理或速率计算完整。见基准 11.8 及 [DFT ADR](../../../docs/adr/0010-derive-dft-geometries-from-matched-occurrences.md) |
| 元素分布 / Batch Compare | 元素集合来自数据；Replicate 是统计单位，不能跳过已选失败数据源。见基准 11.9–11.10 |

候选路径迁移尤其要检查实现现状：旧 sampled/Event Path 逻辑和历史测试不代表有效生产契约。
检查实际 schema、调用方与迁移状态；改变字段含义必须显式版本化，不能只换 UI 标签。

## 实现和验证

沿“核心领域函数 → 服务门面/公共导出 → CLI/Dash 调用 → 文件导出”检查实际受影响的链路。
在核心实现规则、默认值与错误分类；前端只负责输入和展示。参数改变时检查结果缓存是否
绑定源修订、语义版本和必要查询参数，避免把旧结论交给新请求。

用可手算的小例验证目标结论及必要反例，例如计量重复、同式异构、连续性缺口或缺单位；
通过实际受影响的 CLI/服务/回调核对相同结果。测试入口见
[验证指南](../../../docs/agents/testing.md)。只更新与本次契约相关的文档和测试。

交付区分“已实现并验证的行为”“证据不支持的结论”“尚未完成的验收”，并说明公共结果
是否需要迁移。不要把候选、观测谱系或初始几何表述为已证明的反应机理。
