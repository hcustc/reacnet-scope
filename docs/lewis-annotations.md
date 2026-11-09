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

## 依赖

候选推断使用 SciPy 的 MILP 求解器。SciPy 不在核心依赖中：安装 `reacnet-scope[web]`
（Dash）或 `reacnet-scope[research]` 时已包含，仅装核心包时需另行安装 `scipy>=1.11`。
未安装时准备任务在写入前明确报错；读取已发布注释、状态与导出不需要 SciPy。

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
- 当前 `neutral-main-group-milp/v5` 禁止卤素正电，负电限制在 N/O/卤素或只有一个
  重原子邻居的 C，并要求带电原子沿连接边一对一匹配异号电荷。中性 C 允许孤电子对。
  八级优化顺序在方法元数据中公开，各级共享时间和节点预算；没有物理能量评分。
  中性原子的壳层缺额不按 C/非 C 区别对待，避免优先把 O 自由基移到碳上。
  在更高优先级目标相同的情况下，合法的中性 C/N 六元环优先选单双键交替表示；
  不强制饱和环或醌变成芳香环。这一偏好最多检查 64 个环，超限明确报告。
  模型优先减少三/四元环内的三键和相邻双键；其余目标相同时，负电偏向电负性高的
  原子、正电偏向电负性低的原子。CO 等由更高优先级决定的表示保留。
  这是命名模型的保守边界；被排除或求解失败不能解释为物种不可能存在。
- 图示从 `model_graph` 保留的模型电子分配生成；碳旁的 LP 是孤电子对数，电子点是
  模型未配对电子。它们不判定实际自旋，`model_unpaired_electrons` 也不是自旋测量。
  图中的局部原子编号不是 RNG/轨迹 Atom IDs。
- `candidate_smiles` 可能丢失孤对分配；`radical_electrons` 仍是 RDKit 序列化表示属性。
  例如模型可给 CF₂ 的碳分配一个孤对和零个未配对电子，而 `F[C]F` 往返会得到两个
  RDKit 自由基电子；两者都不能证明真实 CF₂ 的单重态/三重态。
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

公开参考集比较结果见下文[参考验证](#参考验证)。键级/形式电荷
表示一致，不等于未配对电子、孤电子对或自旋状态一致，不能据此声称已验证化学有效性。
不同共振写法也可能合理；例如 N₂O 的 `N#[N+][O-]` 与 `[N-]=[N+]=O`，不能仅按
是否与参考 SMILES 相同判断对错。报告把这组已声明的共振差异与严格一致分开计数。

升级到方法 v5 后，旧方法生成的注释会显示过期，需要重新运行
`reacnet-scope prepare rebuild lewis /path/to/rng --assume-neutral`。
当前结果 schema 为 `reacnet-scope/lewis-annotation/v2`，保存模型图和命名目标值
`model_objectives`；原始 RNG 身份、原始文件和计数不变。

身份、版本和发布决定见 [ADR-0035](adr/0035-annotate-representative-species-with-lewis-candidates.md)。

## 参考验证

以下是 `neutral-main-group-milp/v5` 在公开参考集上的开发回放结果，用于说明候选命名的
取舍，**不是独立留出验证、化学有效率或电子态判定**。逐条结果和各版本消融实验过程见
[PR #73](https://github.com/hcustc/reacnet-scope/pull/73) 的讨论与提交历史。

**输入。** 固定 RMG-database 提交 `6391cc4f75341803605d9ad17417e14cf2db1f59` 的
`input/thermo/libraries/` 下十四个库：primaryThermoLibrary、halogens、CHOF_G4、
CHOCl_G4、CH、CHO、CN、CHN、CHON、primaryNS、BurcatNS、NOx2018、GRI-Mech3.0、
NISTThermoLibrary。共 7781 条声明，排除 6 条超出元素域的稀有气体（Ar/He/Ne），
7775 条参与比较；不同库可能共享连接图，不是 7775 个独立样本。芳香补充集为同一提交的
`SABIC_aromatics.py` 全部 365 条，单独报告，不并入原分母。

**结果。**

| 集合 | 分母 | v4 严格一致 | v5 严格一致 |
| --- | ---: | ---: | ---: |
| 原十四库 | 7775 | 7439 | 7371 |
| SABIC 芳香补充 | 365 | 263 | 281 |

- v5 去掉了中性原子按 C/非 C 区别的壳层优先级，并加入有条件的芳香六元环偏好。
  审查指出的 12 条过氧自由基参考全部恢复，苯氧（SABIC index 10）和苯基过氧
  （index 32）参考恢复。
- 原集合比 v4 少的部分中，93 条新增差异有 91 条是单个自由基沿两根键移动、原子级
  电荷与孤对数不变的表示差异；其余 2 条为更复杂的电子分配，未归类。
- 严格表示差异不直接等于化学错误。N₂O 的两种已声明共振写法单独标注，不计入严格一致。
  SABIC 中 2 条稠环自由基（index 197、329）在“去除未配对电子后比较”下不一致，
  保留未配对电子后一致，属于比较器限制。
- 全部条目都生成了候选，无超时、节点截断或比较器错误。没有真实 MD 轨迹验收，也没有
  自旋分辨的量化计算；不能把比例解释为 RNG 数据的化学有效率，或据此删除物种。

**复现。** RMG 库文件只按 AST 读取结构字面量，不导入或执行。下载上述固定提交的库文件后，
在仓库外的目录运行（完整输出不提交到仓库）：

```bash
uv run --locked python scripts/benchmark_lewis_rmg.py \
  --revision 6391cc4f75341803605d9ad17417e14cf2db1f59 \
  --output-dir /path/to/out \
  /path/to/RMG-database/input/thermo/libraries/{primaryThermoLibrary,halogens,CHOF_G4,CHOCl_G4,CH,CHO,CN,CHN,CHON,primaryNS,BurcatNS,NOx2018,GRI-Mech3.0,NISTThermoLibrary}.py
```

默认参数为中性假设、单结构 5 秒、批次 600 秒。比较历史版本时，用
`--implementation-file` 指向从 git 历史取出的旧 `reacnet_scope/lewis.py`（例如 v4 为提交
`7fbf40e` 中的版本）。
