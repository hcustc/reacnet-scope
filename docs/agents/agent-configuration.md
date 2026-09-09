# AGENTS.md 与 skills 方案

本方案针对 ReacNet Scope 的 Python / uv / pytest / Dash 工具链，以及 RNG 证据驱动的
分析架构。配置落在仓库内，随代码版本管理；不依赖本机用户名、全局 Codex 设置或新插件。

## 分层与职责

| 层次 | 文件 | 放什么 | 为什么 |
| --- | --- | --- | --- |
| 全仓库约定 | [AGENTS.md](../../AGENTS.md) | 事实来源、项目入口、跨模块不变量、基础命令、skill 路由 | 每个任务都需要知道的少量信息 |
| 核心局部约定 | [reacnet_scope/AGENTS.md](../../reacnet_scope/AGENTS.md) | 适配器、preparation、store、服务边界 | 核心改动才能触及的证据与执行约束 |
| Dash 局部约定 | [scripts/webapp_dash/AGENTS.md](../../scripts/webapp_dash/AGENTS.md) | 会话/工作区状态、回调、稳定身份交接、本地资源 | 防止交互逻辑越过核心边界 |
| 测试局部约定 | [tests/AGENTS.md](../../tests/AGENTS.md) | 小型证据 fixture、状态隔离、行为断言、验证限制 | 让测试能发现科学语义与在线扫描回归 |
| 按需流程 | `.agents/skills/reacnet-*/SKILL.md` | 某类任务的定位、执行、验证与交付方法 | 只有匹配任务才加载具体流程 |
| 按需参考 | [domain.md](domain.md)、[testing.md](testing.md)、[issue-tracker.md](issue-tracker.md)、[triage-labels.md](triage-labels.md) | 领域文档判定、选测地图、GitHub 操作与既有标签 | 保持命令与规则只有一个维护入口 |

CLI 文件的职责已由根约定覆盖，暂不为单个入口增加 `scripts/AGENTS.md`；也不为每个
领域 `.py` 文件单建 skill。当前不增加 hooks、agent 自动循环、执行脚本或项目级 Codex 配置。

## 加载方式

Codex 启动时沿项目根目录到当前工作目录读取 AGENTS.md，按目录合并。因此根约定显式
要求：即使从根目录启动，编辑核心、Dash 或测试前也要读取相应局部文件；不能假定所有
后代目录的规则已经注入。[官方 AGENTS.md 说明](https://learn.chatgpt.com/docs/agent-configuration/agents-md)

项目 skills 放在根目录 `.agents/skills/`，靠名称与 description 匹配任务后再读正文。
三个新技能保留默认自动匹配，也可显式引用；`agents/openai.yaml` 提供中文显示信息与
示例 prompt，不改变权限或启用策略。更新后若未显示，重启 Codex 再检查技能列表。
[官方 skills 说明](https://learn.chatgpt.com/docs/build-skills)

## 三个项目技能

| 技能 | 输入和完成结果 | 边界 |
| --- | --- | --- |
| [reacnet-evidence-indexing](../../.agents/skills/reacnet-evidence-indexing/SKILL.md) | 来源/索引问题或变更要求 → 可验证的适配、构建、兼容和查询行为 | 不自动扫描真实大轨迹；不接管普通查询或样式任务 |
| [reacnet-analysis-contracts](../../.agents/skills/reacnet-analysis-contracts/SKILL.md) | 分析问题及语义要求 → 核心、调用方、导出的一致契约与验证 | 不把每次任务变成领域访谈；不替用户决定无法推导的科学含义 |
| [reacnet-dash-workflows](../../.agents/skills/reacnet-dash-workflows/SKILL.md) | 用户操作链或状态问题 → 正确的状态归属、回调和可见反馈 | 不接管纯 CSS 修改；不在回调重写科学计算 |

索引实现、分析含义和交互状态是三个不同变更轴。跨层任务只组合实际需要的技能，例如
新增分析页面需要 analysis + Dash；修改其索引存储时再加入 evidence。所有技能引用现有
规范和测试地图，不复制 HDF5 schema 手册、完整领域词汇表或测试执行脚本。

显式调用示例：

```text
使用 $reacnet-evidence-indexing 修复 timeline 无效时错误回退 CSV 的问题。
使用 $reacnet-analysis-contracts 修改候选路径结果，分离 Step Evidence 与 Continuous MD Support。
使用 $reacnet-dash-workflows 修复取消数据集切换后迟到回调覆盖当前数据集的问题。
```

## 现有 41 个通用 skills 的处理

保留既有文件和 `allow_implicit_invocation` 策略，不批量删除、改名、禁用或移动到用户目录。
本次新增后共 44 个仓库技能。下面是任务路由建议，不是另一套启用配置：

| 组别 | 示例 | 使用方式 |
| --- | --- | --- |
| 通用工程方法 | `diagnosing-bugs`、`tdd`、`code-review`、`codebase-design`、`design-an-interface`、`resolving-merge-conflicts` | 按具体任务使用，领域约束由本仓库文档补充 |
| 领域、方案与调研 | `domain-modeling`、`research`、`prototype`、`request-refactor-plan`、`grilling` | 需要这些产物/流程时使用；研究提议与已接受决策分开 |
| 规划、工单与编排 | `triage`、`to-spec`、`to-tickets`、`implement`、`wayfinder`、handoff 系列 | 保持原调用策略；本地开发不自动升级为发工单、任务循环或外部发布 |
| 其他技术栈与个人工作流 | `setup-pre-commit`、`setup-ts-deep-modules`、`migrate-to-shoehorn`、`scaffold-exercises`、`obsidian-vault`、写作/教学系列 | 可继续用于相应请求；不作为这个 Python 项目的默认工具链 |

若以后要精简仓库，可把多仓复用的个人流程迁到用户级 skills 或插件，但应先确认调用方和
实际用途。同名技能不会自动合并，迁移要避免重复发现；这次不实施迁移。
[官方 skills 发现规则](https://learn.chatgpt.com/docs/build-skills)

现有技能还存在需要按任务辨别的限制：`code-review` 的主流程使用
`git diff <fixed-point>...HEAD`，不能自动覆盖未提交或 untracked 内容；工作区审查需要
明确补充相应 diff 范围。`tdd` 含测试边界确认步骤，范围已在会话中明确时不重复征询。
不为了使用新配置而强制启用这些流程，也不把通用技能的发布步骤当作用户授权。

## 规则维护

- 全任务都会改变决策的规则放根 AGENTS；仅一个目录适用的规则放局部 AGENTS；
  可重复的操作方法放 skill；长说明、测试地图或设计理由放现有 docs。
- 领域语义只维护在 CONTEXT、基准和有效 ADR。agent 文件保留简要提醒和链接；发现不一致
  回到规范解决，不能独立维护第二套科学定义。
- 当前候选路径处于契约迁移背景，且存在两个 `0012-*` ADR。
  [领域文档规则](domain.md) 明确完整文件引用和替代关系，避免误用已替代的 sampled-only 决策。
- 对新增规则先给出真实适用请求和不应触发的请求；只修复已观察到的流程问题，不堆叠
  “每次都必须”式规则。目录与测试改名时同步维护路由链接。

## 验收场景

以下是人工检查技能路由与行为的场景，不是自动化测试已经通过的声明：

| 请求 | 应选择/读取 | 应观察到的行为 |
| --- | --- | --- |
| 修复 timeline incomplete 被当成无命中 | evidence + 核心约定 | 区分无效原生和原生缺失，保留 CSV 回退边界 |
| 候选路径新增独立支持验证 | analysis；改索引时再用 evidence | 读取有效 ADR-0013；候选结构身份不含 rank/validation |
| 修复切换 B 取消后迟到结果覆盖 A | Dash；bug 方法按需组合 | 构造请求时序，验证 A 保留及旧结果被忽略 |
| 修改按钮颜色 | Dash 局部约定 | 直接改相关 CSS，不启动三项完整项目流程 |
| 更新 README 拼写 | 根约定 | 检查文案/链接，不跑全量科学分析测试 |
| 审查未提交改动 | 通用 review，明确工作区范围 | 包含 tracked 与新增文件，不能只比较提交间 diff |
| 给现有缺陷建 GitHub issue | QA/tracker 与 triage 文档 | 使用授权的工单流程和既有标签 |

新技能用 skill-creator 的 `scripts/quick_validate.py` 检查 frontmatter，再检查本地链接、
UI 元数据和描述边界。格式校验不证明运行中的 agent 已正确选择技能；实际使用后再根据
误触发、遗漏或多余确认的具体例子调整。本次没有增加可执行逻辑，验证不需要业务全量测试。
