# 候选超图与网络图导出

本功能把候选查询已返回的有界路线转成可复用超图，支持网络可视化与后续分析。
它消费已发布索引的查询结果，不读取原始 RNG 文件、重建索引或加载全局网络。
合并图不生成新的 Candidate，不建立事件顺序，也不证明连续分子历史。

## 使用

Web 候选结果的“导出”菜单中：

- **当前图超图 JSON**：全部本页路线，或当前与勾选路线，按图的范围选择导出。
- **当前图 SVG**：同一范围的可缩放网络图；结构内嵌，可离线打开。

两项均包含已显示的浏览历史；历史步骤标为 `historical=true`，当前步骤索引为 `null`。
没有当前结果但存在浏览历史时可单独导出历史；空查询也可导出，保留空结果或截断状态。
SVG 使用适合文件的固定布局，合并相同精确 Species，保留图示 S/R 编号；不保存浏览器拖动和缩放。
两个下载均在导出前后校验来源修订，修订变化时要求重新搜索。
浏览历史的每段步骤保留生成时的来源修订；与当前结果不一致或缺少修订信息时，
停止合并展示和导出，并提示重新搜索。继续、翻页及返回请求也绑定原结果修订，
排队期间重建索引后返回的新修订不能接入旧浏览链。
原有候选 JSON 与逐步 CSV 的契约不变。

```bash
uv run --locked reacnet-scope candidate-search \
  --source /data/run.timeline.h5 --start 'CCO' --mode explore \
  --out-hypergraph-json graph.json --out-svg graph.svg
```

示例路径为占位符，需要先准备事件索引。CLI 导出本次返回的路线，不加入 Web 浏览历史。
SVG 最多允许 500 个物种及反应节点，超限明确报错；Web 可选较少路线，CLI 可减小 `--max-paths`。
JSON 完整保留本次返回的有界结果，不施加 SVG 节点上限。

```python
import json
from pathlib import Path
from reacnet_scope import (
    search_candidate_paths, build_candidate_hypergraph, candidate_hypergraph_svg,
)

report = search_candidate_paths({"timeline": "/data/run.timeline.h5"}, "CCO", mode="explore")
graph = build_candidate_hypergraph(report)
# 可用 signature_ids=[report["paths"][0]["signature_id"]] 限定已有路线。
Path("graph.json").write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")
Path("graph.svg").write_text(candidate_hypergraph_svg(graph), encoding="utf-8")
```

Python 转换是纯结果转换，来源有效性由调用方负责；未知路线、失败报告、缺完整参与物或
同一反应身份下不一致的计量/计数会报错。正式查询缺能力或缺索引仍由原服务报错，不转成空图。

## v1 数据契约

schema 为 `reacnet-scope/candidate-hypergraph/v1`，独立于候选报告版本。

| 字段 | 含义 |
| --- | --- |
| `scope` | 固定为 `returned_candidate_projection`，不宣称全网完整 |
| `species` | 精确 RNG Species 身份；同分子式的异构体分别保留 |
| `hyperedges` | 精确有向 Reaction Type；`tail` 为反应物，`head` 为产物 |
| `tail` / `head` 的项 | `species_id`、精确 `species`、正整数 `coefficient`；例如 A+A 计量为 2 |
| `members` / `carrier_members` | 涉及此节点/参与物的路线及步骤，载体子集保留主线身份 |
| `routes[].display_steps` | 完整超边引用、当前步骤索引/历史标志、载体端点、各步支持事件数与证据口径 |
| `routes` 的 Candidate 身份与连续支持 | 原当前查询 Candidate 的值；加入浏览历史不重新签名，也不扩展原连续检查的结论 |
| `selection` | 全部返回路线或显式选择的 `signature_id` 列表 |
| `provenance` | 原查询参数、报告版本、来源修订、处理元数据、证据口径、完成/截断状态与实际预算消耗 |
| `display_number`、`layout_layer` / `layout_order` | 图示编号和布局提示，不充当 Species、Reaction 或 Candidate 身份 |

Species/Reaction/参与连线 ID 使用既有图的精确身份摘要，方向和重复计量都参与 Reaction 身份。
相同反应经过多条路线时，原始 `event_count`、`forward_count`、`reverse_count` 与 `net_count`
仅保留一次；载体专属 `transfer_event_count` 留在各条路线的展示步骤上，不相加。
选中路线仍保留完整共同反应物和其他产物，但剔除隐藏路线的 membership。

净转化图显示净计数；全部观测方向显示 Reaction Type 原始计数。范围继承原查询的
`count_scope`，不换算物理时间、不把计数视为速率。缺失的原报告元数据不补造。
`query_complete` 等字段只描述原查询，不能说明全局网络已穷尽；分页或展示条数限制仍保留。

SVG 的节点提示保留精确结构/反应键，`metadata#candidate-hypergraph` 内嵌同一超图 JSON。
仅物种连通性的退化结果在 SVG 中明确标注未核查原子转移，载体连线不构成原子继承证明。
不引入 CDN、外部图片请求或新的依赖。当前图导出仍需代表性大型数据和跨平台单独验收。

## 实现来源与适配

组织方式参考 `adaptive_sampling` 的 `exploration_tools` 分支，核对版本
`8fbcc33a3e21fd0a8f1d8e1e886a9014f29e3ef5` 的
[pattern_mining.py](https://github.com/ochsenfeld-lab/adaptive_sampling/blob/8fbcc33a3e21fd0a8f1d8e1e886a9014f29e3ef5/adaptive_sampling/processing_tools/exploration/pattern_mining.py)
和 [nanoreactor_network.py](https://github.com/ochsenfeld-lab/adaptive_sampling/blob/8fbcc33a3e21fd0a8f1d8e1e886a9014f29e3ef5/adaptive_sampling/processing_tools/exploration/nanoreactor_network.py)。
本实现延伸 Scope 已有的完整反应菱形投影，自行实现共享核心和导出；没有复制对方源码，
也没有引入它的全图 pickle、固定时间换算、外部命令或事件识别逻辑。
频繁模式挖掘、跨重复显著性检验和量化计算调度属于后续独立功能。
