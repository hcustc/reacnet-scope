# 候选检索方法审查：可疑路线能否反证方法质量

日期：2026-09-21。范围：当前 Scope 源码、有效契约、RNG 官方参数说明。本文不预设截图中的脱 CH 反应不可能，也不把尚未核查的数据结论写成事实；真实事件核查另行记录。未改动算法、索引或原始 RNG 工件。

## 结论

可疑结果应当作为检索方法的质量测试案例。“符合候选定义”只回答入选规则是否满足，不能回答结果是否有助于研究生成路线。当前实现检索的是逐步有事件与局部原子转移支持的网络路线；它没有实现化学合理性排序、事件质量筛选或跨步具体分子验证。因而，用户看到不合理结构时，应同时检查原始事件、代表结构、拼接和排序四层，不能仅加免责声明，也不能直接归因于 BFS。[代码](../../reacnet_scope/path_search.py#L98)，[有效决策](../adr/0013-separate-candidate-discovery-from-continuous-md-support.md)。

## 已经证实的方法事实

1. **最大继承不等于化学合理性。** preparation 对每个 matched occurrence 的每个反应物计算与各产物的 atom-ID 交集，只保留最大正交集、含并列项；它不比较活化能、断键数、价态、寿命、重原子保留率或快速回穿。所有元素的原子在交集计数中权重相同。因此这条规则能排除“接上了完全来自共反应物的 H”一类错误，却不能排除复杂裂解。如果第一步确有 C6H5ClO → CH + C5H4ClO 的对应原子分配，11 对 2 的继承数会使 C5H4ClO 入选；这并不验证其发生机制。[实现 107–149 行](../../reacnet_scope/path_search.py#L107)，[ADR-0016](../adr/0016-require-event-local-carrier-transfer.md)。

2. **搜索优先顺序不是证据质量。** 队列先进先出，目标查询为每个前缀先探测到目标的最后一步；邻接按精确 reaction_key/product_species 字典序读取。event_count、transfer_event_count、max_shared_atoms 被携带输出，但不用于筛选或排序。短路线因每次事件都按一步计费而先出现；某次事件若在一个 Transition 中记录复杂净变化，也只算一步。搜索不会凭空发明该事件，但会把它用作短路。[邻接 204–258 行](../../reacnet_scope/path_search.py#L204)，[搜索 280–361 行](../../reacnet_scope/path_search.py#L280)。

3. **预算截断不仅影响数量，也影响结果组成。** outgoing 的 SQL LIMIT 在字典序之后应用；全局 edges_read 耗尽后不再读新邻接。frontier 满时停止添加该前缀的后续分支。已排队前缀仍可探测目标，这避免一部分漏解，但不能恢复未入队分支。因此截图两条结果没有化学上的“前两名”含义，且可能漏掉其他长短路线；不能把字典序形成的曝光顺序解释成主通道。[实现 316–359 行](../../reacnet_scope/path_search.py#L316)。

4. **跨步原子集合没有一致性约束。** 每步的 dominant transfer 至少由一个独立事件支持，下一步只按 exact RNG Species 标签连接。存在逻辑上的“每步都最大继承，但连续保留的起点原子逐步耗尽”的可能；需要具体 carrier chain 与 anchor intersection 才能检查。该可能性是方法推论，不是已证实发生在截图路线上的事实。[实现 297–301、348–361 行](../../reacnet_scope/path_search.py#L297)，[连续支持契约](../software-design-baseline.md#116-continuous-md-support-与-candidate-evidence)。

5. **miso=1 会丢失用于标签区分的键级信息，不会把开链和闭环直接合并。** RNG 官方说明其合并相同原子与键网络、不同键级的结构。Scope 以代表标签画图并连接，不校验连接两侧的每帧键级。故截图自由基点、单双键组合不能直接当作那次事件当帧电子结构；但开链→闭环涉及键网络变化，不能全归咎于 miso。官方当前版本的代表选择规则不应未经版本核查就倒推到该数据生成版本。[RNG CLI 官方说明](https://deepmodeling.github.io/reacnetgenerator/guide/cli.html)，[Scope 显示逻辑 115–117 行](../../scripts/webapp_dash/candidate_workbench.py#L115)。

6. **截图各步数字应读作主线转移支持事件数。** 当前界面优先显示 transfer_event_count，只有缺字段时退回总 event_count；不是整条路线计数。分子式序列相同也不等于两路线重复：候选签名包含有序精确物种及完整反应式。[界面 256–259 行](../../scripts/webapp_dash/candidate_workbench.py#L256)，[签名 297–300 行](../../reacnet_scope/path_search.py#L297)。

## 对契约与产品问题的区分

ADR-0013 明确拒绝把完整 Event Path 当作网络候选的存在条件；ADR-0015 明确采用 BFS，首版不提供化学评分与 Continuous MD Support；ADR-0016 增加局部原子继承。因此不能把上述缺失直接定为违背既有契约的实现 bug。另一方面，这些契约不能用来证明结果“足够合理”。若任务目标是高效找到有物质来源、可信事件及可解释结构变化的生成路线，当前方法对该目标的保障不足；需要以数据诊断决定是否增加独立证据视图或修改契约。[ADR-0013](../adr/0013-separate-candidate-discovery-from-continuous-md-support.md)，[ADR-0015](../adr/0015-add-indexed-candidate-task-to-reaction-workspace.md)，[设计基准第 11.5、11.6 节](../software-design-baseline.md#115-candidate-path-discovery)。

## 用真实数据证伪或定位的检查

| 假设 | 有界检查 | 能区分的问题 |
| --- | --- | --- |
| 第一步源自索引错误 | 取全部两个支持事件，与 RNG 原始 event/molecule 记录逐项核对反应式、参与者、原子集合和键集合 | 不一致指向索引/关联；一致只证明来源忠实，不证明力场或反应识别正确 |
| 古怪画法主要来自代表标签 | 对这两个事件前后 participant 的实际键集合与绘图代表比较；检查导出键是否含键级 | 不符支持显示/标签压缩解释；无键级证据时须标记不可判定 |
| 中间体只是暂态或快速回穿 | 对事件前后同一 atom-ID 集合读取有限帧区间，记录连续出现长度及是否返回相同 Species/atoms/bonds | 能量化短暂性；短寿命本身不能自动等同噪声 |
| 整条路线仅跨事件拼接 | 从最后一步少量事件逆查最近形成与此前消耗，逐步核对 carrier atoms/bonds 与 Transition 次序，禁止越过消耗或缺口 | 找到链证明一次 MD 实现；穷尽本路线支持且无链才可说此数据未观测到，不否定网络候选定义 |
| 搜索预算漏掉更有用路线 | 固定 exact start/target 与索引版本，只改变执行预算，比较签名集合及截断原因 | 能证明预算敏感性；更长时间仍截断不能证明全量覆盖 |
| 两行结果其实重复 | 比较完整 species 和 reaction_key tuple，而非分子式 | 完全一致才是重复结果；不同共反应物/副产物或连接结构需保留解释 |

在上述检查完成前，不建议以“删去低频事件”“按高频排序”或“禁用所有多键变化”作为修复：它们引入未验证阈值，可能去掉真实稀有通道。先输出可核查的事件质量、寿命、真实键状态、连续来源事实，再讨论哪些维度用于默认筛选或排名；这属于研究建议，尚非已接受契约。
