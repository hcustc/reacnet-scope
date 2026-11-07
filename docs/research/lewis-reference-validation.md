# Lewis 候选结构：公开参考回放与模型取舍

## 当前 v5：含氧自由基与有条件的芳香命名

日期：2026-11-07。回应[最新审查](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6705484722)。
取消中性原子的 C/非 C 壳层优先级差别，并在原子组成、电子状态及较高目标允许时，
优先选择中性 C/N 六元环的单双键交替表示。不是为氯苯酚体系增加物种名称例外。
原始 RNG 身份、氢数、连接关系、丰度和反应证据全部保留。

**12 条指定过氧自由基全部恢复参考表示，苯氧及含氯芳香自由基样例通过。**
原固定集合的严格一致数为 **7371/7775**，低于 v4 的 7439；新增芳香参考集合为
**281/365**，高于 v4 的 263。两个集合分别报告，不把后加集合混进原分母来改善比例。
这些是命名模型的开发回放，不是化学有效率、独立留出验证或电子态判定。

### 选择规则与边界

- 删除 `hetero_shell_deficit`，保留统一的 `shell_deficit`，以及较高优先级的带电原子
  缺壳层项和中性碳孤对状态。没有把“所有原子完整八隅体”直接升到最高优先级。
- 在电荷数之后增加 `nonaromatic_six_rings`：只检查无弦的六元 C/N 连接环；显式氢计入
  度数，C 须为三配位、N 为二配位。选中的环还须满足每个环原子中性、闭壳层、八隅体，
  且六条环键单双交替。该拓扑条件本身不强制芳香化，饱和环和醌的氢数/电子状态仍受约束。
- 不读取原始芳香标记来锁定结果；原键级不同的苯氧代表及共振写法得到相同候选别名。
  偏好限定在上述六元环，不等于通用芳香性、共振穷举或 Clar 模型。芳香性模型本身也有
  边界，参见 [RDKit 芳香性规则](https://www.rdkit.org/docs/RDKit_Book.html#aromaticity)。
- 最多考虑 64 个符合拓扑条件的环，环发现计入单结构时间预算。超时/超量明确返回失败
  状态，不发布部分候选；`eligible_six_ring_count` 记录实际考虑数量。

固定同一组样例后再测试四种方案，结果如下：

| 样例组 | v4 | 仅取消非 C 偏置 | 仅加芳香偏好 | v5 组合 |
| --- | --- | --- | --- | --- |
| 苯氧、邻氯苯氧 | 非芳香候选 | 仍可能选非芳香候选 | 高优先级偏置仍在 | 芳香 O 自由基候选 |
| 苯基过氧、邻氯苯基过氧 | 带电的非芳香候选 | 中性芳香过氧候选 | 偏置仍在 | 中性芳香过氧候选 |
| 乙烯基过氧 | 电荷分离且自由基在 C | 中性过氧候选 | 偏置仍在 | 中性过氧候选 |
| CH₃OO·、C₂H₅OO·、HO₂· | 通过 | 通过 | 通过 | 通过 |

这里“通过”表示满足已声明的候选命名偏好，不表示其他共振贡献式不存在。
CO、碳片段、卡宾、卤素、小环旧回归仍通过；苯、吡啶、萘保留芳香表示，环己烷、
环己烯和对苯醌不会因六元环条件而被强制芳香化。

### 原集合：四组同分母对照

仍是下文固定十四库的 7781 条声明、相同 6 条域外元素排除、7775 条实际比较。
四组全部生成候选，无超时、节点截断或比较器错误。原集合的模型图与 SMILES 在原比较
指标下全部一致。比较器记录独立 SHA-256，四组使用同一份实现。

| 库 | 分母 | v4 | 仅取消偏置 | 仅加芳香偏好 | v5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| primaryThermoLibrary | 45 | 38 | 38 | 38 | 38 |
| halogens | 26 | 26 | 26 | 26 | 26 |
| CHOF_G4 | 3364 | 3216 | 3185 | 3216 | 3185 |
| CHOCl_G4 | 3250 | 3123 | 3089 | 3123 | 3089 |
| CH | 112 | 112 | 112 | 112 | 112 |
| CHO | 354 | 354 | 354 | 354 | 354 |
| CN | 1 | 1 | 1 | 1 | 1 |
| CHN | 141 | 141 | 141 | 141 | 141 |
| CHON | 94 | 94 | 94 | 94 | 94 |
| primaryNS | 72 | 51 | 50 | 51 | 50 |
| BurcatNS | 109 | 98 | 97 | 98 | 97 |
| NOx2018 | 146 | 130 | 129 | 130 | 129 |
| GRI-Mech3.0 | 52 | 47 | 47 | 47 | 47 |
| NISTThermoLibrary | 9 | 8 | 8 | 8 | 8 |
| **合计** | **7775** | **7439** | **7371** | **7439** | **7371** |

| 方法 | 严格结构一致 | SMILES 局部电子一致 | 模型图局部电子一致 | 已声明 N₂O 共振差异 | 其他未分类差异 |
| --- | ---: | ---: | ---: | ---: | ---: |
| v4 | 7439 | 7372 | 7422 | 2 | 334 |
| 仅取消非 C 偏置 | 7371 | 7303 | 7353 | 2 | 402 |
| 仅加芳香偏好 | 7439 | 7372 | 7422 | 2 | 334 |
| v5 组合 | 7371 | 7303 | 7353 | 2 | 402 |

局部电子指标分母同为 7775，不检验多重度或耦合。N₂O 的 `N#[N+][O-]` 与
`[N-]=[N+]=O` 是已声明允许的共振表示，已从“错误样例”测试中移出；两个参考条目的
严格不一致仍保留，另标记 `accepted_n2o_resonance`。该有限白名单不是通用共振判定器。
RMG 的[共振路径文档](https://reactionmechanismgenerator.github.io/RMG-Py/reference/molecule/pathfinder.html#rmgpy.molecule.pathfinder.find_lone_pair_multiple_bond_paths)
也明确列出这组 N₂O 转换。

| 固定分层 | 分母 | v4 结构/SMILES 局部/模型局部 | 取消偏置 | 芳香偏好 | v5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 小 C/N/O | 383 | 340/325/328 | 338/322/325 | 340/325/328 | 338/322/325 |
| 含未配对电子或中性碳孤对 | 172 | 134/121/124 | 131/118/121 | 134/121/124 | 131/118/121 |
| 含中性碳孤对 | 14 | 13/0/9 | 13/0/9 | 13/0/9 | 13/0/9 |

### 指定的 12 条过氧参考

索引均来自固定提交的对应库，表内“是/否”为严格结构一致。候选栏省略独立显示的氢，
实际推断与导出仍保留完整氢数。

| 库 / index | v4 | 取消偏置 | 芳香偏好 | v5 | v5 候选 |
| --- | --- | --- | --- | --- | --- |
| CHOF_G4 / 482 | 否 | 是 | 否 | 是 | `[O]OC(F)=C(F)F` |
| CHOF_G4 / 713 | 否 | 是 | 否 | 是 | `[O]OC(F)=CF` |
| CHOF_G4 / 997 | 否 | 是 | 否 | 是 | `C=C(F)O[O]` |
| CHOF_G4 / 1693 | 否 | 是 | 否 | 是 | `[O]OC=C(F)F` |
| CHOF_G4 / 1773 | 否 | 是 | 否 | 是 | `[O]OC=CF` |
| CHOF_G4 / 2003 | 否 | 是 | 否 | 是 | `[O]OC#CF` |
| CHOCl_G4 / 54 | 否 | 是 | 否 | 是 | `[O]OC=C(Cl)Cl` |
| CHOCl_G4 / 87 | 否 | 是 | 否 | 是 | `C=C(Cl)O[O]` |
| CHOCl_G4 / 1131 | 否 | 是 | 否 | 是 | `[O]OC(Cl)=C(Cl)Cl` |
| CHOCl_G4 / 1297 | 否 | 是 | 否 | 是 | `[O]OC=CCl` |
| CHOCl_G4 / 2109 | 否 | 是 | 否 | 是 | `[O]OC(Cl)=CCl` |
| CHOCl_G4 / 2187 | 否 | 是 | 否 | 是 | `[O]OC#CCl` |

### 新差异的逐条诊断

v4 → v5 有 25 条从严格不一致变为一致，93 条反向变化，净减少 68。
93 条全部是自由基参考（92 条单自由基、1 条双自由基），没有闭壳层参考从一致变为不一致。
在两个版本的规范化输入图局部编号下重新计算模型图，比较元素/同位素、逐原子电荷/孤对、
未配对电子变化和逐边键级：

- **91 条**的电荷、孤对不变；仅两个相邻键一增一减，两个端点之间移动一个未配对电子。
  这是单次共轭自由基位移的形式，符合 RMG 所述的
  [allyl radical shift](https://reactionmechanismgenerator.github.io/RMG-Py/reference/molecule/resonance.html#rmgpy.molecule.resonance.generate_allyl_delocalization_resonance_structures)
  类型。此处是局部表示诊断，没有调用 RMG 的完整共振枚举，未证明相同能量或真实电子态。
  不把这 91 条从严格差异中扣除，也不称为 91 个化学错误。
- **2 条**还改变电荷或孤对，保留为未裁决差异：BurcatNS / 26 (`CON3`)，
  `[N-]=[N+]=N[C]=O → [N]=NN=C=O`；NOx2018 / 86 (`CH2NO2`)，
  `[CH2][N+](=O)[O-] → C=[N+]([O])[O-]`。本轮不为这些个例继续添加特殊规则。

诊断判据可复核：相同模型原子顺序与连接边；逐原子 q/p 不变；恰有两个 u 变化，分别
为 +1/-1；恰有两个相接的键改变一级，分别 +1/-1；u 变化的两端恰是该三原子链的两端。
其余变化不归入这类。完整逐条记录保存在仓库外。

### 补充芳香参考：独立列出，不改变原分母

原十四库只有 32 条满足新增六元环拓扑条件，无法充分覆盖苯氧/芳基过氧问题。因此按
主题选择同一固定 RMG 提交的完整
[SABIC_aromatics.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/SABIC_aromatics.py)，
先选库再回放全部 365 条，不按命中挑条目。它是补充的开发验证，不称独立留出集。
365 条全部生成候选，无排除、超时或比较器错误。

| 方法 | 严格结构一致 / 365 | SMILES 局部电子 / 365 | 模型图局部电子 / 365 |
| --- | ---: | ---: | ---: |
| v4 | 263 | 263 | 262 |
| 仅取消非 C 偏置 | 262 | 262 | 261 |
| 仅加芳香偏好 | 279 | 279 | 280 |
| v5 组合 | 281 | 281 | 282 |

该库 index 10 (`C6H5O_7`) 是苯氧参考，index 32 (`C6H5O2_5`) 是苯基过氧参考：
v4 均不匹配；取消偏置只恢复后者；仅加芳香偏好仍未恢复；组合恢复两者。
含氯对应物目前由明确的命名回归样例覆盖，尚无真实轨迹或独立含氯芳香自由基库验收。

芳香单项与组合各有 2 条“去除 u 后规范化”的模型图/SMILES 比较差异：index 197
(`C10H7_3`) 和 329 (`C12H9_18`)。保留模型未配对电子、禁用 FINDRADICALS 后按
RDKit 芳香性规范化，两者分别与序列化候选一致；去掉 u 会影响稠环芳香性及 Kekulé
选择。报告保留这 2 个比较器限制，不将其掩盖为零，也不因此宣称所有稠环表示均已验证。

补充文件 SHA-256：`988144134081aedf1cf1a8a1e52276ee0441388789efcc3ff3f7c04966ba52b1`。

### 本轮复现与版本

沿用后文固定十四库的输入和参数：中性假设、150 原子、5 秒、10000 节点，批次 600 秒。
源码中的 `benchmark_lewis_rmg.py` 默认运行当前 v5。以下仅在外部目录构造历史和实验版本：

```bash
WORK=$(mktemp -d)
REPO=$(pwd)
git show 7fbf40e426bdc34aad6bffe9394b080f79918703:reacnet_scope/lewis.py > "$WORK/lewis-v4.py"
mkdir "$WORK/unbiased" "$WORK/aromatic"
cp "$WORK/lewis-v4.py" "$WORK/unbiased/lewis.py"
cp "$WORK/lewis-v4.py" "$WORK/aromatic/lewis.py"
git -C "$WORK/unbiased" apply --unidiff-zero "$REPO/docs/research/lewis-v5-unbiased-experiment.patch"
git -C "$WORK/aromatic" apply --unidiff-zero "$REPO/docs/research/lewis-v5-aromatic-experiment.patch"
```

对同一份十四库参数，分别省略 `--implementation-file`、指定 `"$WORK/lewis-v4.py"`、
`"$WORK/unbiased/lewis.py"` 或 `"$WORK/aromatic/lewis.py"`，用不同输出目录即可得到
四组结果。补充集合将输入文件列表替换为单个 `SABIC_aromatics.py`，仍用四组相同参数。
补丁只用于可信历史代码的实验回放，不作为生产选项。

| 实现 | SHA-256 |
| --- | --- |
| v4 | `97db644549e1b4d5d47aa5d92cae1f57c56cd9662d86917266bb3192e6503daf` |
| 仅取消非 C 偏置 | `23a81a2ab16483fae0e0e68e007b630c41477d0a7a2069d68511930c77e77904` |
| 仅加芳香偏好 | `3bd5182cfede8a6b67a20b95e5e89b3fc1a6c0a6415439840c246aeb91a38d45` |
| v5 组合 | `57841e8edb74b7890a168cc441e12881457a76f1f0ddc2269ce5de507b75e89a` |
比较器 SHA-256：`9e56d5a9dfa4dfe0755910cc5b344b75d69cbbbf7b07146ca503af9d8f8bcda3`。

方法升级为 `neutral-main-group-milp/v5`，结果 schema 保持 v2。旧注释需要显式重建。
候选图、别名、JSON/CSV 和原身份交接共用该版本；不会据候选结果删除或合并原物种。

---

## v4 历史回放：电荷位置与小环偏好

日期：2026-11-07。回应[第三轮反馈](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6703813400)。
v4 调整 3 个电荷表示和 4 个小环样例（其中 N₂O 已在 v5 验收中更正为允许的共振差异）；同分母严格结构一致数从 v3 的 **7449/7775**
降至 **7439/7775**。这是明确规则下的命名候选，不宣称整体准确率提高。此前 v3 的
方法、来源、指标定义和同分母历史比较保留在后文。

### 两项新增规则

1. 在未配对电子目标之前，最小化三/四元环内的三键数与相邻双键对数之和。
   每个键/键对只计一次，直接检查短环连接，覆盖可能不在最小环基中的短环。
   环内孤立双键、环外双键和链上多键不受惩罚。这是排序偏好，不是物理张力能，
   更高优先级的带电原子/非 C 壳层目标仍可胜出。
2. 在原有目标之后，最小化 `Σ(形式电荷 × Pauling 电负性 × 100)`，使其余目标相同
   的候选优先把负电放在电负性高的原子、正电放在电负性低的原子。乘 100 是为了使用
   精确整数目标锁定，不是能量换算。CO 的八隅体目标优先，因此仍保留 `[C-]#[O+]`。

电负性表来自 [RSC 元素周期表](https://periodic-table.rsc.org/)，例如
[C=2.55](https://periodic-table.rsc.org/element/6/carbon)、
[N=3.04](https://periodic-table.rsc.org/element/7/nitrogen)、
[O=3.44](https://periodic-table.rsc.org/element/8/oxygen)。完整表与八级顺序在方法元数据中公开。
电负性偏好本身不确定共振贡献、电子态或能量。

| 原始连接图 | v3 | v4 |
| --- | --- | --- |
| `[H][C]([H])[O][O]` | `[H][C-]([H])[O+]=O` | `[H]C([H])=[O+][O-]` |
| `[H][C]([H])[N]([H])[O]` | `[H][C-]([H])[N+]([H])=O` | `[H]C([H])=[N+]([H])[O-]` |
| `[N][N][O]` | `[N-]=[N+]=O` | `N#[N+][O-]` |
| `[H][C]1([H])[C][C]1` | `[H]C1([H])C#C1` | `[H]C1([H])[C][C]1` |
| `[C]1[C][O]1` | `C1#CO1` | `[C]1[C]O1` |
| `[C]1[C][C][C]1` | `C1#CC#C1` | `[C]1[C][C][C]1` |
| `[C]1[C][C]1` | `C1=C=C=1` | `[C]1[C][C]1` |

小环样例选中了带中性碳孤对的模型分配；SMILES 仍可能丢失这种分配，不据此指定自旋。
小环项的最优值为零也不证明结构稳定。先只添加电荷位置项，3 个电荷测试由失败转为通过、
4 个小环仍失败；再加入小环项，7 个样例全部通过。模型图与 SMILES 的结构比较确认这些
错误产生在优化阶段，而不是序列化阶段。

### 同一参考集上的单项对照

仍使用下文列出的十四个库、固定提交和哈希；7781 条声明中排除相同的 6 条域外元素，
比较全部 7775 条。v3、仅加电荷位置的实验、v4 均无超时、节点截断或比较器错误，全部
模型图与 SMILES 在规范化后的结构指标上一致。原始数据和逐条结果保留在仓库外。

| 库 | 分母 | v3 结构一致 | 仅加电荷位置 | v4 结构一致 |
| --- | ---: | ---: | ---: | ---: |
| primaryThermoLibrary | 45 | 37 | 38 | 38 |
| halogens | 26 | 26 | 26 | 26 |
| CHOF_G4 | 3364 | 3222 | 3222 | 3216 |
| CHOCl_G4 | 3250 | 3127 | 3127 | 3123 |
| CH | 112 | 112 | 112 | 112 |
| CHO | 354 | 354 | 354 | 354 |
| CN | 1 | 1 | 1 | 1 |
| CHN | 141 | 141 | 141 | 141 |
| CHON | 94 | 94 | 94 | 94 |
| primaryNS | 72 | 51 | 51 | 51 |
| BurcatNS | 109 | 98 | 98 | 98 |
| NOx2018 | 146 | 130 | 130 | 130 |
| GRI-Mech3.0 | 52 | 48 | 47 | 47 |
| NISTThermoLibrary | 9 | 8 | 8 | 8 |
| **合计** | **7775** | **7449** | **7449** | **7439** |

| 方法 | 严格结构 | SMILES 往返局部电子 | 模型图局部电子 |
| --- | ---: | ---: | ---: |
| v3 | 7449 | 7383 | 7432 |
| charge-only | 7449 | 7383 | 7432 |
| v4 | 7439 | 7372 | 7422 |

上表分母均为 7775；局部电子指标不判断自旋。电荷单项实验中，NOx2018 与 GRI-Mech3.0
的 N2O 参考都采用 `[N-]=[N+]=O`，因此改为本轮要求的 `N#[N+][O-]` 后，严格结构
比较会从一致变为不一致。另有 ONHN、HNCN 两条变为一致；HNCN 不带形式电荷，
其变化属于同分最优解选择，不能归因为负电位置更合理。
固定的小 C/N/O 分层继续保留：

| 分层 | 分母 | v3 结构/SMILES 局部/模型局部 | 仅加电荷位置 | v4 |
| --- | ---: | ---: | ---: | ---: |
| 小 C/N/O | 383 | 340/326/328 | 340/326/328 | 340/325/328 |
| 含未配对电子或中性碳孤对 | 172 | 132/120/122 | 134/122/124 | 134/121/124 |
| 含中性碳孤对 | 14 | 12/0/8 | 12/0/8 | 13/0/9 |

### “少了 42 条”的逐条核算

CHOF_G4/CHOCl_G4 从 v2 到 v3 的 42 条是**净差额**，不是固定的 42 条失败集合：

| 库 | v2 一致→v3 不一致 | v2 不一致→v3 一致 | 净减少 | 失配中 v3 无电荷 | 失配中 v3 引入电荷对 | 同电荷数的位置错误 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| CHOF_G4 | 108 | 77 | 31 | 102 | 6 | 0 |
| CHOCl_G4 | 92 | 81 | 11 | 86 | 6 | 0 |
| 合计 | **200** | **158** | **42** | **188** | **12** | **0** |

按 `(library, index, label)` 对齐条目，对全部 200 条失配检查候选与参考的逐原子形式电荷：
188 条 v3 候选完全无电荷；另 12 条的参考完全无电荷，v3 引入 O⁺/O⁻ 对。
后者属于是否引入电荷的选择，不是相同电荷数下的位置排序问题。因此，按本轮第 1 类
问题定义，归属数是 **0**。作为独立验证，仅加电荷位置项后这两个库所有候选都未改变，
200 条失配没有一条恢复。不能把这 42 条净损失归因于缺少电负性排序。

12 条带电候选的参考索引为 CHOF_G4：482、713、997、1693、1773、2003；
CHOCl_G4：54、87、1131、1297、2109、2187。它们的参考是中性过氧自由基表示，而
v3/v4 偏好局部电荷分离；本轮没有改变决定这些表示的较高优先级目标。

加入小环项后，CHOF/CHOCl 相对 v3 分别有 5/5 条变为一致、11/9 条变为不一致，
净减少 6/4。原 200 条失配恢复 4 条，不能用此忽略同时发生的退步。
原 v3 两库各有 3 条小环惩罚非零，v4 全部降为零；但新约束也改变了一些原本惩罚为零的
同分最优解，涉及键级/自由基位置。它们未被证明全部属于同一共振类，严格比较照常记为
差异。此处报告的是版本输出的变化，不能把所有变化都解释成新增化学规则的直接效果。

### v4 与单项实验复现

沿用后文十四库的下载、哈希、预算及回放命令。当前源码默认运行 v4；历史 v3 与电荷
单项实验可在外部临时目录中生成：

```bash
WORK=$(mktemp -d)
REPO=$(pwd)
git show 029dd82194d21d016e1ff76f8fa01b8881a504dd:reacnet_scope/lewis.py > "$WORK/lewis.py"
cp "$WORK/lewis.py" "$WORK/lewis-v3.py"
git -C "$WORK" apply --unidiff-zero "$REPO/docs/research/lewis-charge-location-experiment.patch"
```

同一回放命令将实现选项设置为 `--implementation-file "$WORK/lewis-v3.py"` 或
`--implementation-file "$WORK/lewis.py"`，分别使用独立输出目录即可重现历史 v3 和
仅加电荷位置的实验。复现 v4 时移除 `--implementation-file`，并使用独立输出目录。
该补丁只用于外部实验，不是生产选项。实际运行的实现 SHA-256：

| 方法 | SHA-256 |
| --- | --- |
| v3 | `951421505492df44c634af5a14a93c08570d79c585acbe660d079f1fcf6457cd` |
| charge-only | `bd0e35130a195fded49d8e435fdd9aa77225551653c2b71b9e590331c3e8632b` |
| v4 | `97db644549e1b4d5d47aa5d92cae1f57c56cd9662d86917266bb3192e6503daf` |

生产方法升级到 `neutral-main-group-milp/v4`，schema 保持 v2，旧方法注释须显式重新准备。
八级目标共享原有时间/节点预算。真实轨迹验收、唯一电子态识别与自动过滤不属于此次结论。

---

## v3 历史回放

日期：2026-11-07。回应 [PR #73 的碳片段与验证反馈](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6703323546)，
保留此前 [v2 含卤修复](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6702833099) 的比较。

v3 修复了四个报告样例的电荷分离偏好，并保存中性碳的模型孤对分配。
**整体严格参考一致性低于 v2；本次不宣称普遍准确率提升，更不构成自动物种过滤的验收。**
原始 Species、丰度、反应、事件与 RNG 工件保持不变。

## 从 v2 到 v3

v2 的 `100 × 未配对电子 + 10 × 电荷数 + 10 × 壳层缺额 + 扩展壳层` 加权罚分，
会让少一个未配对电子的收益超过碳正离子和缺壳层的代价。另一方面，旧中性 C 状态
把所有非键合电子固定为未配对电子，没有给优化器选择孤对的机会。

v3 对中性 C 枚举符合电子记账的孤对/未配对分配；禁止卤素正电，负电只允许 N/O/卤素
或只有一个重原子邻居的 C，并要求带电原子沿已有边一对一匹配异号电荷。按以下顺序
逐级求整数最优并锁定前级结果：带电原子壳层缺额、非 C 壳层缺额、模型未配对电子、
全部壳层缺额、带电原子数、扩展壳层超额。H 以二电子、 中性 B 以六电子、其余以八电子
计算缺额。各级共享时间和节点预算；只有完整目标序列完成才发布候选。

这是保守的**模型选择约定**，不等于普适价态规则、能量排序或自旋识别。
[IUPAC 的 carbene 定义](https://old.goldbook.iupac.org/html/C/C00806.html) 允许两个非键合
电子配对或不配对，不能以本模型偏好配对为理由声称其确定了基态。

| 原始连接图 | v2 候选 | v3 候选 |
| --- | --- | --- |
| `[C][C]` | `[C+]#[C-]` | `[C]=[C]` |
| `[C][C][C]` | `[C+]=C=[C-]` | `[C]=C=[C]` |
| `[C][C][O]` | `[C+]#C[O-]` | `[C]=C=O` |
| `[C][C][C][O]` | `[C-]#CC#[O+]` | `[C]=C=C=O` |

CO 仍为 `[C-]#[O+]`，含卤例子仍不产生卤素正电。以上四条是模型行为回归样例，
不是四个独立的量化化学真值。尤其 NOx2018 的 C2 条目记录三键和两个未配对电子，
C2O 条目记录 C≡C–O、末端 C/O 各一个未配对电子，两者均标记 multiplicity 3。
v3 的中性双键/累积双键表示与这些特定参考态不一致；本轮没有找到 C3/C3O 的直接
参考条目，也没有自行进行电子结构计算。这些限制仍然存在。

## 参考来源与范围

固定 RMG-database 提交 `6391cc4f75341803605d9ad17417e14cf2db1f59`。
原四库全部保留，新增十库，不按命中情况删条目。CH/CHO/CN/CHN/CHON 的一致性很高，
但它们主要补充闭壳层分子，不能单独证明小自由基覆盖。因此另加 primaryNS、BurcatNS、
NOx2018、GRI-Mech3.0 和 NISTThermoLibrary，并预先按参考字段定义小片段分层。
`halogens` 自述 WIP，参考库中的表示也不视为真实轨迹的唯一真值。

| 库 | 声明条目 | 参与比较 | 输入 SHA-256 |
| --- | ---: | ---: | --- |
| [primaryThermoLibrary.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/primaryThermoLibrary.py) | 48 | 45 | `ed776b4f002b13b3bb0c2dddd58d7689703f232047c8935be9e5218bb65441bf` |
| [halogens.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/halogens.py) | 26 | 26 | `13a7277c6eb0d2ea57474bac699f4ba088b0cb657b762010fbba7e4c592063e9` |
| [CHOF_G4.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHOF_G4.py) | 3364 | 3364 | `adfc39babe8b90ed5701eca6441536e5563296289d00efc9403895e27685fd4f` |
| [CHOCl_G4.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHOCl_G4.py) | 3250 | 3250 | `8c5667e252876bba49b8f836a3670d5f01609b2ad8e9bd851152652b1a36bb8e` |
| [CH.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CH.py) | 112 | 112 | `ff947a96ea10fc0af79c4ffa5db9056fd6b39c3169c2dbf2ca749569f05efca2` |
| [CHO.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHO.py) | 354 | 354 | `de9ca9104f54f2fb9d84e18fd7e028671b29c5be334ae17a7f711b19d1988bea` |
| [CN.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CN.py) | 1 | 1 | `bbf8907ec6169f4a8992099c1fd82771654437d8f05acd6f5e79531d5433e747` |
| [CHN.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHN.py) | 141 | 141 | `fe4a973b603eedc216f7f0ca08fd7ea5ce9f15f43995f53e558d88e9f9eb671f` |
| [CHON.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHON.py) | 94 | 94 | `92b6935d79e5d4a89c3455865a87004a44cab12bb9c924a02ba24fcdde5f3549` |
| [primaryNS.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/primaryNS.py) | 72 | 72 | `326c90bd7efb5719e76bcabddbe4fb3d777e078f8d2eee568f718b94e9574afd` |
| [BurcatNS.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/BurcatNS.py) | 109 | 109 | `8f8d86437c5a74af6d5f6e27045f0f7d23b0ffb37752e123c2a3947888f04580` |
| [NOx2018.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/NOx2018.py) | 148 | 146 | `dcd4ed2e999c3f71c968e2d1c21362d782f68d4d9fc107ff492d9d69ca6c8368` |
| [GRI-Mech3.0.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/GRI-Mech3.0.py) | 53 | 52 | `7ae5b5b547506648dc701c192495d3cbb3c586f0490868b4127203fa672f60e6` |
| [NISTThermoLibrary.py](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/NISTThermoLibrary.py) | 9 | 9 | `11ba7c0577f0354ecd3d9396135666a9ecad919a9d8df702709fce896de55f5f` |

总计 7781 个声明条目，7775 个参与比较。六个排除项：primaryThermoLibrary 的 Ar/He/Ne、
NOx2018 的 Ar/He、GRI-Mech3.0 的 Ar，均超出模型元素域。四种方法都为全部 7775 个
条目生成候选，无超时、节点截断或比较器错误；不是只在命中子集上计算一致率。
不同库/参考态可能共享连接图，不是 7775 个独立统计样本。

[回放脚本](../../scripts/benchmark_lewis_rmg.py) 只通过 AST 读取结构字面量，
不导入或执行 RMG 文件；检查键的双向一致性，仅由声明的 u/p/c 与键级恢复省略氢。
字段定义见 [RMG adjacency list 文档](https://reactionmechanismgenerator.github.io/RMG-Py/reference/molecule/adjlist.html)。

## 同分母结果

下表的“组合实验”是此前探索、但未量化披露的负电位置限制 + 邻接异号配对 + 分层优化：
目标为总壳层缺额、未配对电子、电荷数、扩展壳层；没有 v3 的中性碳孤对枚举及前两级
目标拆分。它仍为 C2 返回电荷分离候选，因此单独启用该组合不足以解决此次反馈。
其复现补丁见[历史组合实验](lewis-combined-experiment.patch)，不作为可选的生产模型。

**结构一致**指在 RDKit 芳香性规范化后，元素、氢数、键级和形式电荷相同，排除 u/p。
不穷举其他共振式，不判断能量或稳定性。为避免自由基字段影响芳香性规范化，比较器先
清除自由基并禁用 FINDRADICALS，再规范化；四种方法使用同一比较器。

| 库 | 分母 | v1 结构一致 | v2 结构一致 | 组合实验 | v3 结构一致 |
| --- | ---: | ---: | ---: | ---: | ---: |
| primaryThermoLibrary | 45 | 36 | 36 | 37 | 37 |
| halogens | 26 | 12 | 26 | 26 | 26 |
| CHOF_G4 | 3364 | 3211 | 3253 | 3183 | 3222 |
| CHOCl_G4 | 3250 | 3105 | 3138 | 3088 | 3127 |
| CH | 112 | 112 | 112 | 112 | 112 |
| CHO | 354 | 354 | 354 | 354 | 354 |
| CN | 1 | 1 | 1 | 1 | 1 |
| CHN | 141 | 141 | 141 | 141 | 141 |
| CHON | 94 | 94 | 94 | 94 | 94 |
| primaryNS | 72 | 51 | 51 | 50 | 51 |
| BurcatNS | 109 | 93 | 93 | 95 | 98 |
| NOx2018 | 146 | 132 | 132 | 134 | 130 |
| GRI-Mech3.0 | 52 | 48 | 48 | 48 | 48 |
| NISTThermoLibrary | 9 | 8 | 8 | 8 | 8 |
| **原四库** | **6685** | 6364 | 6453 | 6334 | 6412 |
| **全部十四库** | **7775** | 7398 | 7487 | 7371 | 7449 |

v2 在原四库为 6453/6685（96.53%），组合实验为 6334/6685（94.75%），
v3 为 6412/6685（95.92%）。全部十四库 v2 为 7487/7775（96.30%），
v3 为 7449/7775（95.81%）。v3 的 NOx2018 结构一致也从 132/146 降为 130/146，
不能用总体或其他库的增加掩盖该回退。

**SMILES 往返局部电子一致**在结构一致基础上再比较每原子的 u/p，是前四版都能计算的
指标；它仍不验证总自旋、多重度或耦合。v3 增加**模型图局部电子一致**，从保存的原子
分配计算，单列而不与旧方法的 SMILES 指标混为一谈。

| 范围 | 分母 | v1 SMILES 局部 | v2 SMILES 局部 | 组合 SMILES 局部 | v3 SMILES 局部 | v3 模型图局部 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 原四库 | 6685 | 6351 | 6415 | 6283 | 6356 | 6403 |
| 全部十四库 | 7775 | 7376 | 7440 | 7310 | 7383 | 7432 |

v3 全部 7775 个模型图与其 SMILES 在规范化后的结构指标上一致，但局部电子分配可以不同。
例如 CF₂ 的模型碳为 u0/p1，`F[C]F` 往返变为 u2/p0。界面因此从模型图绘制并标注碳的
孤对数，不再用 SMILES 自由基点代替模型分配。该修复仍不证明真实 CF₂ 自旋。

## 小 C/N/O 片段分层

筛选条件只用参考输入，固定在评价前：仅 C/H/N/O、重原子数 ≤4，共 383 条、259 个
不同输入图；其中参考含未配对电子或中性碳孤对的子集 172 条、118 个图；含中性碳孤对
子集 14 条、7 个图。三个分层嵌套，不能相加，也不按某版本的成功与否挑选。
表内各项均为命中数/该行完整分母，`结构；局部` 中的局部为 SMILES 往返指标。

| 分层 | 分母 | v1 结构；局部 | v2 结构；局部 | 组合 结构；局部 | v3 结构；局部 | v3 模型图局部 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 小 C/N/O | 383 | 338；325 | 338；325 | 341；327 | 340；326 | 328 |
| 含未配对电子或中性碳孤对 | 172 | 131；120 | 131；120 | 133；121 | 132；120 | 122 |
| 含中性碳孤对 | 14 | 11；0 | 11；0 | 11；0 | 12；0 | 8 |

小片段改善有限，仍有大量不同表示；v3 在含中性碳孤对的 14 条参考中仅 8 条模型局部
电子一致，不能从加入孤对状态推导“卡宾问题已全部解决”。

全部 7775 条中，含正电碳候选数 v1/v2/组合/v3 分别为 8/19/1/0；
含“某带电原子没有任何异号邻居”的候选数为 20/24/0/0。后者只是配对的必要条件统计，
不是完整一对一匹配证明；生产算法另行检查完整匹配。这些是模型行为指标，不是正确率。

此集合用于开发和选择方案，**不是独立留出验证集**。没有真实 MD 轨迹验收，没有核对
所有共振类，也没有自旋分辨量化计算。不能将比例解释为 RNG 数据的化学有效率、噪声
识别准确率，或以求解失败/不一致为由自动删除物种。

## 复现与版本

RDKit `2025.09.6`、SciPy `1.17.1`；四种方法显式假设中性，单结构上限 150 原子、
5 秒、10000 节点，整批 600 秒。每轮都处理完全部输入。运行实现 SHA-256：

| 方法 | 实现 SHA-256 |
| --- | --- |
| v1 | `709e12021e59cafe4db4f932768e7329b85e7d6f636cc00973b2716a8acd6eb5` |
| v2 | `337dc8647f4db5c9ab420ab7aca41fc51c74f8a71a0875cdf5868006988a8de2` |
| combined | `416a40dbddc5637ea77c74ae54ff9c1c0f8dabe3617e34501374dc67acd6eee0` |
| v3 | `951421505492df44c634af5a14a93c08570d79c585acbe660d079f1fcf6457cd` |

从上表固定提交下载十四个文件到仓库外的 `INPUTS` 目录并核对哈希。以下命令生成历史
实现；补丁只应用到临时目录中的单个历史文件，不能应用到当前生产核心：

```bash
WORK=$(mktemp -d)
REPO=$(pwd)
git show 193edc85515e9fbd6d1869cb8cea6625a4b9e9c8:reacnet_scope/lewis.py > "$WORK/lewis.py"
cp "$WORK/lewis.py" "$WORK/lewis-v1.py"
git show a13a23ffad5856f7cb7e6680ea385f25e9fac98b:reacnet_scope/lewis.py > "$WORK/lewis-v2.py"
git show 029dd82194d21d016e1ff76f8fa01b8881a504dd:reacnet_scope/lewis.py > "$WORK/lewis-v3.py"
git -C "$WORK" apply --unidiff-zero "$REPO/docs/research/lewis-combined-experiment.patch"
INPUTS=/path/to/reference
uv run --locked python scripts/benchmark_lewis_rmg.py \
  "$INPUTS/primaryThermoLibrary.py" "$INPUTS/halogens.py" \
  "$INPUTS/CHOF_G4.py" "$INPUTS/CHOCl_G4.py" "$INPUTS/CH.py" "$INPUTS/CHO.py" \
  "$INPUTS/CN.py" "$INPUTS/CHN.py" "$INPUTS/CHON.py" "$INPUTS/primaryNS.py" \
  "$INPUTS/BurcatNS.py" "$INPUTS/NOx2018.py" "$INPUTS/GRI-Mech3.0.py" \
  "$INPUTS/NISTThermoLibrary.py" \
  --revision 6391cc4f75341803605d9ad17417e14cf2db1f59 \
  --implementation-file "$WORK/lewis-v3.py" \
  --output-dir "$WORK/v3"
```

相同命令分别将实现选项替换为 `--implementation-file "$WORK/lewis-v1.py"`、
`--implementation-file "$WORK/lewis-v2.py"` 或 `--implementation-file "$WORK/lewis.py"`，
并换用独立输出目录，即得到 v1/v2/组合实验。该选项只用于可信的 Scope 历史代码，
不能指向下载的 RMG 文件。补丁应用后的文件哈希应与上表 combined 完全一致。
`summary.json` 保存方法、来源哈希、分母及分层，`entries.json` 保留逐条状态和比较结果。
完整参考输入/输出不随仓库分发，脚本拒绝将完整结果写入源码仓库。

该轮方法为 `neutral-main-group-milp/v3`，结果 schema 升至
`reacnet-scope/lewis-annotation/v2`：新增 `model_graph` 和命名 `model_objectives`，
移除加权 `model_score`。旧注释须显式重新准备；同分候选和别名可改变，原始身份不变。
