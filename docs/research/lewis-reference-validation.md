# Lewis v3：碳片段修复与同分母参考回放

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
git -C "$WORK" apply --unidiff-zero "$REPO/docs/research/lewis-combined-experiment.patch"
INPUTS=/path/to/reference
uv run --locked python scripts/benchmark_lewis_rmg.py \
  "$INPUTS/primaryThermoLibrary.py" "$INPUTS/halogens.py" \
  "$INPUTS/CHOF_G4.py" "$INPUTS/CHOCl_G4.py" "$INPUTS/CH.py" "$INPUTS/CHO.py" \
  "$INPUTS/CN.py" "$INPUTS/CHN.py" "$INPUTS/CHON.py" "$INPUTS/primaryNS.py" \
  "$INPUTS/BurcatNS.py" "$INPUTS/NOx2018.py" "$INPUTS/GRI-Mech3.0.py" \
  "$INPUTS/NISTThermoLibrary.py" \
  --revision 6391cc4f75341803605d9ad17417e14cf2db1f59 \
  --output-dir "$WORK/v3"
```

相同命令分别添加 `--implementation-file "$WORK/lewis-v1.py"`、
`--implementation-file "$WORK/lewis-v2.py"` 或 `--implementation-file "$WORK/lewis.py"`，
并换用独立输出目录，即得到 v1/v2/组合实验。该选项只用于可信的 Scope 历史代码，
不能指向下载的 RMG 文件。补丁应用后的文件哈希应与上表 combined 完全一致。
`summary.json` 保存方法、来源哈希、分母及分层，`entries.json` 保留逐条状态和比较结果。
完整参考输入/输出不随仓库分发，脚本拒绝将完整结果写入源码仓库。

生产方法为 `neutral-main-group-milp/v3`，结果 schema 升至
`reacnet-scope/lewis-annotation/v2`：新增 `model_graph` 和命名 `model_objectives`，
移除加权 `model_score`。旧注释须显式重新准备；同分候选和别名可改变，原始身份不变。
