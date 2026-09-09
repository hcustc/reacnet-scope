# 核心包约定

本文件补充根目录 AGENTS.md，适用于 `reacnet_scope/`。

## 模块边界

- `timed_evidence.py` 统一原生 HDF5 / 兼容 CSV 的选择、验证、规范化与批读取。
  发现、preparation 和查询调用该边界，不各自复制格式判断。
- `prepare.py` 与各 index/store 模块负责离线构建、续建和发布；`datasets.py`、
  `dataset_context.py`、`capabilities.py` 负责数据集身份、切换与能力状态。
- `services.py` 是现有 Dash 服务门面，分工在 `workspace_services.py`、
  `analysis_services.py`、`evidence_services.py`、`batch_services.py`。
  新业务逻辑放进所属领域模块或服务，避免继续堆进门面。
- CLI 和 Dash 复用核心实现；核心分析不依赖 Dash 控件或 callback context。
  公共导出变化检查 `__init__.py`、服务门面和实际调用方。

## 证据与执行边界

- 完整且兼容的 `.timeline.h5` 优先；只有它不存在时才允许 CSV 回退。
  原生文件损坏、incomplete、禁用或 schema 不兼容时报告具体失败。
- 聚合 `count=N` 保留 N 个逻辑 Reaction Occurrence；未解析发生保留为 `unresolved`，
  参与统计但不能提供要求原子连续性的路径或轨迹证据。身份不绑定存储行号。
- 构建使用有界批次、检查点和明确源修订；源修订变化的旧任务不得发布。
  失败、取消或未完成构建不能污染当前已发布索引。
- 在线查询不扫描全部原始证据，不因缺索引调用构建；局部轨迹只读索引命中的字节范围。
  仅把原始全扫描改成 SQLite 全表加载，仍不满足局部查询的规模契约。
- 对能力缺失、无命中、截断和证据不足保留可区分的状态及恢复方法。
  查询限额和来源修订属于可复核结果，不能用静默裁剪掩盖。

## 修改分析时

先确认使用的是 Species、Reaction Type、Reaction Occurrence 还是 Molecule Instance
身份，再确认时间轴、来源与输出限制。候选发现、路径验证、命运分析、动力学和 DFT
各自的语义以设计基准对应章节为准，不把一个工作流的条件套给另一个。

索引变更按 `reacnet-evidence-indexing` 的流程处理；科学分析契约变更按
`reacnet-analysis-contracts` 处理。测试入口见 [验证指南](../docs/agents/testing.md)。
