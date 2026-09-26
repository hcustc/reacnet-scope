# Domain Docs

本仓库采用单上下文布局：根目录 `CONTEXT.md` 是唯一领域词汇表，`docs/adr/` 保存决策。
不另建 `UBIQUITOUS_LANGUAGE.md`、`CONTEXT-MAP.md` 或平行术语表，除非任务明确改变此布局。

## 阅读与判定

- [CONTEXT.md](../../CONTEXT.md)：术语和身份。领域相关工作先读它；输出与测试命名沿用
  已定义概念及其边界，避免使用各条目的 `_Avoid_` 同义替代。
- [软件设计基准](../software-design-baseline.md)：产品范围、输入、分析与发布验收契约。
  按功能读相关章节。它不是“这些能力均已实现”的声明。
- [ADR](../adr/)：已接受决策及理由。沿 status、替代说明与引用确认有效版本；旧 ADR
  有些没有 YAML frontmatter，不因缺少该字段认定为草稿。部分替代的 ADR 中，只有明确
  列出的条款失效；先核对替代范围，再使用仍有效的决策。被替代的历史条款不作当前验收依据。
- README、专题说明、研究和日期化 specs/plans：使用说明或背景。它们与有效契约冲突时，
  报告差异，不能仅凭文件更近或测试已通过判断哪一项有效。

当前有两个 `0012-*` ADR，引用时使用完整文件名和链接，不能只写“ADR-0012”。
候选路径相关的 `0012-discover-only-sampled-candidate-paths.md` 已被
[`0013-separate-candidate-discovery-from-continuous-md-support.md`](../adr/0013-separate-candidate-discovery-from-continuous-md-support.md)
替代；动力学的 [`0012-estimate-channel-rates-from-prepared-exposure.md`](../adr/0012-estimate-channel-rates-from-prepared-exposure.md)
是另一个决策。

## 修改领域文档

新术语先检查现有概念能否准确表达；确有缺口时在本次工作中记录定义、边界与理由。
使用 `domain-modeling` 时沿用这一单上下文布局，不复制到该技能示例中的其他文件。

明确获授权的契约变化同步更新相关词条、基准章节与 ADR 替代关系；新 ADR 先检查全部
已有编号。普通实现修复无需新增 ADR，研究提议不能未经采纳就改成已接受契约。

发现规范冲突时给出完整文件引用、冲突行为及影响；能按有效契约继续的局部工作继续执行。
只有无法确定应采用哪种科学语义且影响结果时才需要用户裁决。
