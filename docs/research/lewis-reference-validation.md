# Lewis v2：含卤候选修复与公开参考回放

日期：2026-11-07。对应 [PR #73 的反馈](https://github.com/hcustc/reacnet-scope/pull/73#issuecomment-6702833099)。

v1 会生成 `[C-]=[F+]`、`[C-]=[Cl+]`、`[H][C-]=[Cl+]`、`F[C-]=[F+]`。
v2 从候选域排除 F/Cl/Br/I 的正形式电荷，并在 RDKit 规范化后复核；上述例子分别返回
`[C]F`、`[C]Cl`、`[H][C]Cl`、`F[C]F`。CO 仍返回 `[C-]#[O+]`。
原始 Species 身份、丰度、反应和事件不变。

## 参考来源与处理

固定 RMG-database 提交为 `6391cc4f75341803605d9ad17417e14cf2db1f59`。
在比较结果出来前选择两个小库和两个完整的含卤 G4 库，处理其中全部 6688 个声明条目。
`halogens` 自述为 WIP，其他库也不作为真实轨迹的唯一真值。

| 库 | 声明条目 | 参与比较 | 输入 SHA-256 |
| --- | ---: | ---: | --- |
| [primaryThermoLibrary](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/primaryThermoLibrary.py) | 48 | 45 | `ed776b4f002b13b3bb0c2dddd58d7689703f232047c8935be9e5218bb65441bf` |
| [halogens](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/halogens.py) | 26 | 26 | `13a7277c6eb0d2ea57474bac699f4ba088b0cb657b762010fbba7e4c592063e9` |
| [CHOF_G4](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHOF_G4.py) | 3364 | 3364 | `adfc39babe8b90ed5701eca6441536e5563296289d00efc9403895e27685fd4f` |
| [CHOCl_G4](https://github.com/ReactionMechanismGenerator/RMG-database/blob/6391cc4f75341803605d9ad17417e14cf2db1f59/input/thermo/libraries/CHOCl_G4.py) | 3250 | 3250 | `8c5667e252876bba49b8f836a3670d5f01609b2ad8e9bd851152652b1a36bb8e` |

唯一排除项是 primaryThermoLibrary 中的 Ar、He、Ne，超出当前模型元素域；分母仍在上表
完整列出。剩余 6685 个条目对应 6619 个不同输入连接图，两版都生成候选，无超时、搜索
截断或比较器错误。不同参考条目可能共享连接图，条目不是独立统计样本。

工具 [benchmark_lewis_rmg.py](../../scripts/benchmark_lewis_rmg.py) 只用 AST 解析 `entry`
中的结构字面量，不导入或执行参考库。它检查键的双向一致性；若参考省略氢，仅根据已声明的
`u`（未配对电子）、`p`（孤电子对）、`c`（形式电荷）及键级恢复完整氢数。
这些字段的定义见 [RMG adjacency list 文档](https://reactionmechanismgenerator.github.io/RMG-Py/reference/molecule/adjlist.html)。

## 指标与结果

两个指标均按全部可比较条目计算，不只选成功命中的结构：

1. **严格结构表示一致**：元素、氢数、键级和形式电荷在 RDKit 芳香性规范化后相同。
   该指标排除未配对电子与孤电子对属性；不穷举其他共振表示，不判断能量或稳定性。
2. **局部电子表示一致**：在前者基础上，再比较每个原子的 `u/p`。这个指标仍不验证
   总自旋多重度、电子耦合或真实电子态，不能称为电子态预测准确率。

| 库 | v1 结构一致 | v2 结构一致 | v1 局部电子一致 | v2 局部电子一致 | 卤素正电候选 v1 → v2 |
| --- | ---: | ---: | ---: | ---: | ---: |
| primaryThermoLibrary | 36/45 | 36/45 | 32/45 | 32/45 | 0 → 0 |
| halogens | 12/26 | 26/26 | 6/26 | 8/26 | 14 → 0 |
| CHOF_G4 | 3211/3364 | 3253/3364 | 3210/3364 | 3246/3364 | 16 → 0 |
| CHOCl_G4 | 3105/3250 | 3138/3250 | 3103/3250 | 3129/3250 | 16 → 0 |
| 合计 | **6364/6685（95.20%）** | **6453/6685（96.53%）** | **6351/6685（95.00%）** | **6415/6685（95.96%）** | **46 → 0** |

逐条对照有 175 条由结构不一致变为一致、86 条由一致变为不一致，净增加 89 条。
改变可行域也可能改变同分候选的选择，不能声称所有条目均改善。部分差异表现为双键或
自由基位置重新分配，但没有逐条证明它们为等价共振式，不能据此全部忽略不一致项。

`halogens` 的 26 条虽全部结构一致，局部电子表示只有 8 条一致。CF₂ 等卡宾尤其说明：
RDKit 从 `F[C]F` 读取的自由基计数不能证明参考中的单重态或孤电子对配置。
v2 仍有 232 条结构不一致及 270 条局部电子不一致；原子/双原子电子态、小环不饱和结构、
以及不同键级/自由基分布均存在限制。没有进行真实 MD 轨迹验收。

这是用于开发与选择修复方案的**参考回放，不是独立留出验证集**。不能将上述比例解释成
实际 RNG 数据上的化学有效率、噪声识别准确率，或自动过滤物种的依据。

## 为何采用最小修复

单纯加大电荷罚分会把 CO 改成另一表示；因此直接约束触发问题的卤素正电状态。
同时探索过负电荷位置限制、邻接异号配对及分层优化的组合，但它没有证明比最小修复有更好的
严格参考一致性，而且引入了额外的化学适用域假设。本次不把该组合设为默认。
v2 保留 v1 的加权启发式与其他原子态规则，并公开权重；更广的模型改变需单独验证。

## 复现与版本

环境：RDKit `2025.09.6`、SciPy `1.17.1`。两版均显式假设中性，单结构上限 150 原子、
5 秒、10000 求解节点；整批回放预算 600 秒，两版均处理完全部条目。
旧实现取自 PR 初始提交 `193edc85515e9fbd6d1869cb8cea6625a4b9e9c8` 的
`reacnet_scope/lewis.py`，SHA-256 为
`709e12021e59cafe4db4f932768e7329b85e7d6f636cc00973b2716a8acd6eb5`；
v2 实现文件 SHA-256 为 `337dc8647f4db5c9ab420ab7aca41fc51c74f8a71a0875cdf5868006988a8de2`。

从上表固定提交下载四个文件并核对哈希后执行（路径替换为实际外部目录）：

```bash
git show 193edc85515e9fbd6d1869cb8cea6625a4b9e9c8:reacnet_scope/lewis.py > /tmp/lewis-v1.py
uv run --locked python scripts/benchmark_lewis_rmg.py \
  /path/to/reference/primaryThermoLibrary.py /path/to/reference/halogens.py \
  /path/to/reference/CHOF_G4.py /path/to/reference/CHOCl_G4.py \
  --revision 6391cc4f75341803605d9ad17417e14cf2db1f59 \
  --output-dir /path/outside/repository/v2
```

同一命令加 `--implementation-file /tmp/lewis-v1.py` 并改用另一输出目录即可运行 v1。
`--implementation-file` 只用于可信的 Scope 历史代码，不能指向下载的 RMG 文件。
输出 `summary.json` 保存文件哈希、方法和完整分母，`entries.json` 保存逐条状态与比较结果。
完整输入与输出不随仓库分发；脚本拒绝把完整结果写进源码仓库。

方法升为 `neutral-main-group-milp/v2`，结果 schema 保持 v1。旧注释索引会过期，需重新准备。
同分候选及别名可能改变，但原始 Species 身份不会被替换。
