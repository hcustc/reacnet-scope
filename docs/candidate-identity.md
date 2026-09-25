# Candidate 结构身份

现行 indexed Candidate 搜索的 `reacnet-scope/indexed-candidates/v4` 结果在保留
`signature_id` 的同时，给每条路线增加完整的 `candidate_signature` 和独立版本的
`candidate_identity` 文档。CLI JSON、Dash 查询结果和逐步 CSV 使用同一核心结果。
`signature_id` 仍供结果内选择与交接使用；它不是可跨 schema 复用的结构身份。

`SpeciesKey` 保留 RNG 的精确结构字符串，拒绝空白或非法空 token，不用化学工具再次规范化。
`DirectedReactionKey` 对反应两侧分别排序，保留方向、重复计量和共同参与物。
`CandidateIdentity` 由 anchor、每一步显式 Carried Species 和完整的有向 Reaction Type 构成；
查询模式、排名、频次、Occurrence、原子编号、验证状态和源修订不进入结构签名。
签名使用版本化 UTF-8 canonical JSON 和完整 SHA-256。

```python
from reacnet_scope import candidate_identity_from_route

identity = candidate_identity_from_route(report["paths"][0])
assert identity.signature == report["paths"][0]["candidate_signature"]
```

适配器要求路线明确列出 `species` 链和每步完整的反应两侧、`carried_from`、
`carried_to`；兼容旧路线时也可提供等长的 `reaction_keys`。只有旧 `signature_id`
或缺少 Carried Species 时会拒绝推断。逐步事件和连续历史服务接受新签名或旧
`signature_id`，返回结果同时标明结构签名。

`CandidateIdentity.as_dict(dataset_revision=...)` 可把结构签名与调用方提供的、
已包含 Dataset 身份的发布修订标识组合成 `candidate_evidence_key`。当前事件索引
尚未发布这样的稳定修订标识，现行查询不会从文件时间戳伪造它，因此结果中没有
`candidate_evidence_key`。报告级旧 `evidence_key` 是来源状态指纹，不能当作逐条
Candidate 的证据身份；后续索引发布与迁移工作需补齐这一绑定。

这套身份只描述 Candidate 路线结构，不证明完整分子连续历史或反应机理。
