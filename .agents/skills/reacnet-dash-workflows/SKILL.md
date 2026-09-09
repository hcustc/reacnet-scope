---
name: reacnet-dash-workflows
description: 修改 ReacNet Scope Dash 的数据集选择/切换、回调状态、后台任务、能力提示或跨页面证据交接时使用。适用于交互链路和迟到结果问题；纯 CSS 配色间距或仅 CLI 的工作不需要此技能。
---

# Dash 状态与交接

先读 [Dash 约定](../../../scripts/webapp_dash/AGENTS.md) 和
[设计基准](../../../docs/software-design-baseline.md) 第 8–10 节；科学功能的控件变化再读
该功能对应章节。目标是完成用户操作链，并保持数据集身份、请求时序与证据上下文一致。

## 追踪一次用户操作

从 `app.py` 的组件与 store，追到 `navigation.py`、`callbacks.py`、相关 assets，
最后到 `reacnet_scope.services`。记录触发输入、读取状态、写入输出和状态所属范围：

| 状态 | 所属范围与提交条件 |
| --- | --- |
| 浏览路径、Dataset Candidate | 选择器的暂存状态，检查不改变 Current Dataset |
| Current Dataset、页面、选中物种/事件 | 浏览器会话；加载验证成功才提交数据集切换 |
| 验证请求与结果 | 绑定 request identity；取消/替代后忽略迟到结果 |
| 索引、设置、Preparation Task | Dataset Workspace；任务绑定启动时的数据集和源修订 |

有现成的两阶段切换服务时沿用它，不能另建绕过取消/迟到检查的 callback shortcut。
发现用户报告的竞态，先在服务/回调边界构造对应时序，不要求靠人工反复点击复现。

## 修改交互

- 把用户动作写成一个可验证场景，例如“检查 B → 取消 → B 的验证返回 → A 仍为当前数据集”。
  只补本次动作需要的状态与控制，不引入另一个全局 current dataset。
- 明确 loading、成功、有效空结果、缺能力和失败的可见反馈。长任务禁用重复提交；
  切换成功清空旧结果，失败保留当前数据；页面切换不改变已启动任务的归属。
- 跨工具交接使用稳定身份和来源上下文；目标页面重新检查能力与修订，不能把旧表格行号
  或显示文案当作事件键。业务计算仍由核心提供。
- 控件 ID 或页面变更时搜索 Python 和 JS 引用，检查 callback Input/State/Output、动态
  layout 与导航注册。避免用宽泛 exception 或空输出隐藏 wiring 错误。
- 普通界面按用户任务表达；内部 `base`、存储布局等不应成为用户必须理解的概念。
  保留本地 assets，目录输入继续经过服务端允许根目录校验。

## 验证

从 [验证指南](../../../docs/agents/testing.md) 选择 Dash smoke、dataset switch、
dataset selection、context/capability 测试。状态问题验证用户动作的结果和迟到/失败分支；
仅 layout 可加载不足以证明交互正确。

有浏览器工具时，用小型数据集实际走完受影响的动作，检查可见提示、按钮状态、页面交接
和控制台错误。没有浏览器工具时明确验证止于回调/HTTP 层，不声称完成视觉验收。
交付描述用户动作前后行为与实际测试，不只罗列修改了哪些 callback。
