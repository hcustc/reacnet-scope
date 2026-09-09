---
name: reacnet-evidence-indexing
description: 修改 ReacNet Scope 的 RNG 证据适配器、离线索引、preparation 生命周期或在线读取边界时使用；涵盖 HDF5/CSV 选择、事件身份、续建与原子发布。普通查询参数或 Dash 样式改动不需要此技能。
---

# RNG 证据与索引变更

在仓库根目录执行命令。目标是让输入选择、离线构建和在线消费遵守同一证据契约。
修改前读取 [核心包约定](../../../reacnet_scope/AGENTS.md)。

## 定位这次变更

沿用户问题追踪实际调用路径，区分是 source selection、adapter normalization、
preparation、index query 还是 capability reporting。不要从某个错误一路扩大为全库迁移。

按涉及范围读 [设计基准](../../../docs/software-design-baseline.md) 第 6–9、14–15 节；
用 [领域词汇](../../../CONTEXT.md) 区分 Aggregated Reaction Record、Reaction Occurrence、
Analyzed Frame 和 Transition。只读取相关决策：

- 数据源与身份：[原生优先](../../../docs/adr/0001-prefer-complete-native-timed-evidence.md)、
  [事件展开](../../../docs/adr/0002-expand-aggregated-reactions-into-occurrences.md)、
  [稳定身份](../../../docs/adr/0003-use-storage-independent-occurrence-identities.md)、
  [适配器边界](../../../docs/adr/0004-centralize-timed-evidence-behind-adapters.md)。
- 工作区与丰度：[工作区](../../../docs/adr/0005-prefer-dataset-local-workspaces.md)、
  [离线丰度索引](../../../docs/adr/0007-index-species-abundance-evidence-offline.md)。
- 分子连续性或候选生产索引：[continuity](../../../docs/adr/0011-prepare-molecular-continuity-for-query-time-fate.md)、
  [候选与独立验证](../../../docs/adr/0013-separate-candidate-discovery-from-continuous-md-support.md)。

## 形成可验证的改动

1. 找到最小可复现输入。复用现有测试的 fixture 生成模式，在临时目录生成必要工件。
   数据源问题保留“原生缺失”和“原生存在但无效”的差别；身份问题保留计量与参与原子。
2. 在所属边界修改：格式解释留在 `timed_evidence.py`，构建协调留在 `prepare.py`，
   持久化与读取留在相应 index/store。调用方消费统一能力，不复制原生/CSV 分支。
3. schema 或语义变化时检查读写双方、source fingerprint、schema/version、checkpoint
   兼容性和恢复路径。需要重建就明确报告；不能把旧索引成功打开当作语义兼容。
4. 检查生命周期中与改动相关的失败点：构建中断/取消保留已提交检查点，旧源任务不能发布，
   不完整 staging 对查询不可见，发布失败时旧已发布版本仍可读。
5. 从查询调用方验证在线边界。准备完成后拦截 raw reader 和 builder，实际查询仍应成功；
   索引缺失/过期时应给出能力状态与恢复动作。局部查询还要检查是否加载全部 SQLite 记录。

性能问题先保留基线，再比较查询计划、读取量与 peak RSS；按任务规模运行数据，不默认扫描
用户的完整轨迹。原始工件保持只读，真实数据不会成为提交内容。

## 验证与交付

从 [验证指南](../../../docs/agents/testing.md) 选择 timed evidence、event index、
preparation、online index 等受影响测试；跨模块或公共 schema 变化后跑全量。

交付说明采用哪类来源、如何保留身份、索引兼容或重建要求、在线边界证据与实际测试结果。
没有运行代表性大数据时，明确性能结论只适用于已测规模。
