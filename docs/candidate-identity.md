# Candidate 结构身份

对应 [Issue #22](https://github.com/hcustc/reacnet-scope/issues/22)。这是候选生产架构的第一阶段，提供独立、版本化的 Python 身份 API；现有候选发现 v3 JSON、排名和 CLI/Dash 默认行为保持兼容。索引发布、目标约束查询与 Continuous MD Support 由后续工单实现。

## 输入与边界

`SpeciesKey` 使用 RNG 已提供的精确结构字符串，只去除首尾空白，不调用其他化学工具重新规范化 SMILES。分子式、dataset-local ID 和原子编号不是 Species 身份。

`DirectedReactionKey.from_sides` 接收两侧 Species 列表，排序但保留重复项、方向和两侧共同参与物。

`CandidateIdentity(anchor, carried, reactions)` 的 `carried` 每一步恰有一个输出 Species，不包含初始 anchor。每一步输入必须出现在反应物侧，输出必须出现在产物侧；下一步使用前一步的输出。普通路径拒绝重复已访问 Species。这里只校验结构关系，不判断事件证据或分子连续性。

```python
from reacnet_scope import CandidateIdentity, DirectedReactionKey, SpeciesKey

candidate = CandidateIdentity(
    anchor=SpeciesKey("[C]"),
    carried=(SpeciesKey("[C][O]"), SpeciesKey("[C]=O")),
    reactions=(
        DirectedReactionKey.from_sides(["[C]", "[O]"], ["[C][O]"]),
        DirectedReactionKey.from_sides(["[C][O]"], ["[C]=O"]),
    ),
)
document = candidate.as_dict()
assert document["candidate_signature"] == candidate.signature
```

## 版本与证据绑定

结构签名以 UTF-8、固定 JSON key 排序、无多余空白的数组/对象编码后取完整 SHA-256，前缀为 `candidate:v1:`。哈希输入显式含 Species、Reaction Type 与 Candidate signature 的 semantic version。签名不包含排名、频次、查询参数、atom IDs、Occurrence、Replicate、源修订或验证状态。

`candidate.as_dict(dataset_revision=published_revision_id)` 可生成 `candidate_evidence_key`。调用方必须传入发布器提供的、**已包含数据集身份范围**的修订标识；只传本地递增整数或笼统的 `v1` 不足以区分数据集。该 API 只组合身份，不验证来源是否已发布，也不创建 revision。尚无已发布修订时省略参数，结果不会伪造证据键。

证据键使用独立版本 `candidate-evidence/v1`、revision 与结构签名进行 canonical JSON 编码并哈希。更换 revision 会改变证据键，结构签名保持不变。

## 显式兼容入口

`candidate_identity_from_route(route)` 接收明确带 `species`（包含 anchor）和 `reaction_keys` 的路线，返回 `CandidateIdentity`。它忽略旧 `signature_id` 及查询/证据元数据，不修改传入对象；仅提供旧编号或反应序列时拒绝推断 carried chain。新文档使用 `reacnet-scope/candidate-identity/v1`，不会把旧路径 JSON 静默改为新语义。

默认生产查询迁移还依赖 [索引 #23](https://github.com/hcustc/reacnet-scope/issues/23)、[查询 #24](https://github.com/hcustc/reacnet-scope/issues/24) 和 [接口迁移 #26](https://github.com/hcustc/reacnet-scope/issues/26)。身份 API 可单独使用，不代表这些工单已经完成。
