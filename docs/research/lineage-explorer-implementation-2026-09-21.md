# Molecular Lineage Explorer 实现与验收

实现契约见 [ADR-0018](../adr/0018-explore-segment-occurrence-lineage.md)，
操作入口见 [使用说明](../molecular-lineage-explorer.md)。

## 已实现

- 事件工作区新增 Explorer，从具体事件参与 instance 定位精确 Continuity Segment。
- 分子状态区间经离线 preparation 形成完整压缩占有索引，保留所有帧缺口；事件参与实例
  映射到具体 segment，完整 occurrence 连接全部输入/输出。
- 点击 segment 或 occurrence 查看状态、时间、原子来源及键变化；支持上一/下一事件、
  全部分支逐层展开、选定分支继续和回到起始 instance。
- Lineage View 保留 split/merge 图。Observed Path View 从已展开的具体图提取时间连续投影，
  保留各条路径自己的原子交集，不拼接不同 split 分支。
- 支持精确原始帧、原始 occurrence 回查和带来源修订的 JSON 导出。原端点追踪 UI 放入兼容折叠区。

## 真实数据

不随仓库分发的本地 `lineage-explorer-acceptance-2026-09-21.json` 记录输入、计时、来源修订和发布备份。
数据来自既有 2500 K 案例，原始 RNG 文件只读。

| 项目 | 实测 |
|---|---:|
| 分析帧 | 250,001 |
| 精确分子状态 | 171,198 |
| 逻辑连续区间（以压缩位图完整保存） | 120,217,219 |
| 已物化的事件关联 segments | 313,167 |
| 映射的事件参与 instances | 335,415 |
| 关联 occurrences | 169,594 / 169,594 |
| 新增派生索引构建 | 215.8 秒，另建 segment 查找索引 2.1 秒 |
| 测试进程峰值 RSS | 569,648 KiB，约 556 MiB |
| 一次 segment/occurrence 查询 | 约 0.001 秒 |
| 完成后的整个事件索引大小 | 2,332,930,048 字节 |

原案例的 `rngevt_10744_fe7574f5f963` 裂解和 Transition 10745 重聚被恢复为四个 segments、
两个 occurrences。到重聚 segment 的两条 Event Path 分别沿两个碎片：一个保留 11 个根原子，
另一个保留 2 个根原子。完整 lineage 显示两条来源，不声称任一线性投影独自保留了全部根原子。
后续无法连接的 segment 按规则停止，不开展事件可信度诊断。

已发布该数据集的派生事件索引，旧索引备份为同目录
`events.before-lineage-8017eaaa6e.sqlite3`。发布使用构建锁、源修订校验、SQLite quick_check、
旧索引硬链接备份和同目录原子替换；更新 manifest，原始 RNG 文件未修改。

## 验证与边界

602 项全量测试通过，2030 条现有 Dash DataTable 弃用警告。新增测试覆盖长间隔连续、
隐藏状态变化、消失后重现、split/remerge 的不同投影、同 Transition 歧义、完整事件预算、
原生分块区间、在线禁止读 HDF5/重建、源修订、迟到响应及精确原始帧。
Dash layout/dependencies 和真实数据服务/回调已验证；没有浏览器自动化工具，未完成浏览器视觉验收。

交互初始只显示根 segment，逐步扩展当前局部图。路径提取仅对该已展开范围有效。
第一版每图 500 segments/250 occurrences、每次最多展开 20 次且有 5 秒查询预算；
预算不允许裁掉 occurrence 的部分参与者。

规模实测覆盖本数据的 25 万帧、约 17 万 occurrences 和 1.2 亿逻辑区间，
不等同于已达到百万 occurrences 的完整发布性能门槛或跨平台验收。
运行中的 Scope 进程需要重启以加载新代码；本次未停止现有服务。
