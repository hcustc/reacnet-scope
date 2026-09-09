# 测试约定

本文件补充根目录 AGENTS.md，适用于 `tests/` 和 fixtures。

- 使用 pytest；测试分组与现有入口见 [验证指南](../docs/agents/testing.md)。
  从仓库根目录运行 `uv run --locked pytest ...`。
- 用 `tmp_path` 生成小型 HDF5、CSV、species 和轨迹 fixture；优先参考已有生成辅助函数。
  fixture 应保留故障所需的 Transition、原子身份和计量关系，不导入大型真实轨迹。
- 文件、索引、workspace 与任务状态隔离在临时目录；替换启动 OVITO、远程服务等外部副作用。
  不能让自动测试写用户真实 Dataset Workspace 或要求联网。
- 从领域函数、服务、CLI 或 Dash 回调可观察的行为验证；预期计数、身份与单位来自手算小例
  或明确契约，不能由被测实现重新算一遍充当预期值。
- 索引准备在 fixture setup 中完成。在线边界测试可在查询阶段拦截原始文件读取和构建调用，
  使“偷偷扫描/重建”可检测；只 mock 底层非法操作，不 mock 掉整段被测查询。
- 对身份/证据变更选择相关反例：同式异构、计量重复、同一 Transition 无内部顺序、
  unresolved、断裂后重现、来源修订过期。无需为无关改动机械加入全部情形。
- 不为纯文档或低风险样式改动新增逐字匹配测试。既有断言冲突时先核对有效规范，
  不为得到绿色结果直接删断言或降低预期。
- Dash smoke 验证布局和 callback wiring；它不能证明浏览器渲染和竞态都正确。
  小样本测试也不能代替真实规模、内存与跨平台验收。
