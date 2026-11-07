# Lewis 候选结构注释

本功能实现 [#72](https://github.com/hcustc/reacnet-scope/issues/72) 的代表结构注释阶段。
根据原始代表 SMILES 的元素和连接关系，在明确中性片段假设下生成一个 Lewis 候选。
原始 Species 标签、反应式、计数、丰度和事件身份保持不变；本功能不自动过滤物种。

## Dash

在“物种”页展开 **Lewis 候选结构注释（可选）**，明确选择中性片段假设后点击准备。
计算在后台执行，可取消；任务仍属于启动时的 RNG 数据。完成后刷新或查询候选 SMILES，
结果按原始物种分页显示。留空浏览全部注释，包括不支持、输入不足和超时的记录。
点击原始物种打开详情，可对照原结构与候选结构，查看方法、假设和来源。

JSON / CSV 导出使用已提交的查询，包含全部匹配成员，不限于当前展示页。
CSV 的 `annotation_json` 列保留完整的机器可读契约。

## CLI

```bash
reacnet-scope prepare build lewis /path/to/rng --assume-neutral
reacnet-scope lewis /path/to/rng --alias '[C-]#[O+]'
reacnet-scope lewis /path/to/rng --species '[C][O]'
reacnet-scope lewis /path/to/rng --format csv --out lewis.csv
reacnet-scope prepare cancel lewis /path/to/rng
```

多组文件使用 `--base` 选择；显式集合沿用现有集合引用。
`--net-charge 0` 可表达已知中性，非零电荷及指定 `--multiplicity` 会产生不支持状态。
单结构预算 `--max-solve-seconds` 默认 5 秒；整体预算 `--max-task-seconds` 默认 300 秒，
`--max-species` 默认 10000。达到整体预算不发布部分目录，调整预算后重新启动。
`prepare rebuild lewis` 原子替换旧发布结果；失败或取消保留旧结果。
`prepare build all` 不自动启用 Lewis 推断；清除注释可用 `prepare clear lewis`。

## Python

```python
from reacnet_scope import LewisOptions, annotate_lewis, prepare_lewis_annotations

options = LewisOptions(assume_neutral=True)
annotation = annotate_lewis("[C][O]", options=options, miso=1)
assert annotation["candidate_smiles"] == "[C-]#[O+]"
assert annotation["original_species"] == "[C][O]"

prepare_lewis_annotations(
    {"reaction": "/path/to/rng/demo.reactionabcd",
     "species": "/path/to/rng/demo.species"},
    options=options,
)
```

`get_lewis_annotation`、`search_lewis_annotations` 与 `export_lewis_annotations`
消费已发布注释，缺失或过期时明确报错，不在读取期间创建索引或求解。
同一候选别名可以对应多个原 Species，不能用别名替代原身份。

## 判断范围

- 输入来自 `.reactionabcd` 和/或 `.species` 的代表结构目录；本版本不枚举逐帧分子图。
- `miso=0/1/2` 都可以处理具体可解析的代表结构。`miso=2` 只代表所选结构，
  不说明其整个分组均已处理；元数据未知时不会猜测模式。
- 支持 H、B、C、N、O、F、Si、P、S、Cl、Br、I；所有片段必须中性。
  已知 NH₄⁺、PF₆⁻ 不会被偷偷中和；Na、Se 等不支持元素保留原名称。
- 当前 `neutral-main-group-milp/v2` 禁止候选中的卤素正形式电荷，避免以卤素带正电的
  写法减少未配对电子。其余评分权重保持 v1 的启发式规则并在方法元数据中公开。
  这是命名模型的保守边界；被排除或求解失败不能解释为物种不可能存在。
- 自由基点和 `radical_electrons` 是 RDKit 对候选表示的属性，不是测得或验证的电子态。
  例如 `F[C]F` 不能区分 CF₂ 的单重态/三重态；本功能不据此给卡宾指定自旋。
- 原始 SMILES 定义的显式/隐式氢被保留；无法可靠解析氢数时拒判，不能任意补氢。
- `optimization_complete` 表示有界模型优化是否完成；`candidate_search_complete=false`
  表示没有穷举替代候选。成功返回一个结构不证明唯一性、真实电子态、稳定性或机理。
  `candidate_count` 是返回的候选数，不是已穷举的结构数。`candidate_rejected` 只说明返回的
  这个候选未通过表示检查，不意味着模型内其他候选全部不成立。
- 超时或节点截断不采用中途解。低频、自由基、离子、不支持或未找到候选都不会自动删除物种。

当前算法是独立的小型启发式 MILP 核心，不依赖未发布的 RNG 原型。
其表示校验使用 [RDKit sanitization](https://www.rdkit.org/docs/RDKit_Book.html#molecular-sanitization)，
有界优化使用 [SciPy milp](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)。
规则评分只用于这个模型内选择候选，不是物种可信度或物理能量。
小型样例验收不替代真实轨迹上的科学有效性评估。

公开参考集比较记录见[参考验证](research/lewis-reference-validation.md)。键级/形式电荷
表示一致，不等于未配对电子、孤电子对或自旋状态一致，不能据此声称已验证化学有效性。

升级到 v2 后，旧方法生成的注释会显示过期，需要重新运行
`reacnet-scope prepare rebuild lewis /path/to/rng --assume-neutral`。
结果 schema 仍为 `reacnet-scope/lewis-annotation/v1`；原始 RNG 身份、原始文件和计数不变。

身份、版本和发布决定见 [ADR-0033](adr/0033-annotate-representative-species-with-lewis-candidates.md)。
