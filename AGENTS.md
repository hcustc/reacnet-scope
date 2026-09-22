# ReacNet Scope：仓库工作约定

## 项目与事实来源

本项目是 ReacNetGenerator 输出的反应 MD 证据工作台。正式入口是
`reacnet_scope` Python API、`reacnet-scope` CLI 和 Dash Web；三者共享核心分析。

- 开始领域相关工作前读 [CONTEXT.md](CONTEXT.md)，按任务读取
  [软件设计基准](docs/software-design-baseline.md) 的相关章节和有效 ADR。
- 术语由 `CONTEXT.md` 定义；产品契约由设计基准定义；决策理由与替代关系见
  [docs/adr/](docs/adr/)。文档使用规则见 [domain.md](docs/agents/domain.md)。
- 设计基准描述目标契约，代码与测试描述当前实现。发现差异时明确指出，不能用旧实现、
  旧测试或日期化计划反向改写已接受语义，也不借小任务修复所有历史偏差。
- 用户明确要求改变契约时，在该任务内同步相关文档和实现；skill 的流程不扩大任务范围。

## 仓库导航

路径均相对于仓库根目录。编辑下列目录前读取对应的局部约定；从根目录启动的会话也要读取。

| 工作区域 | 入口与局部约定 |
| --- | --- |
| 领域、数据源、索引、服务与导出 | `reacnet_scope/`；[局部 AGENTS.md](reacnet_scope/AGENTS.md) |
| CLI 参数与命令路由 | `scripts/rng_query_cli.py`；分析逻辑仍放在核心包 |
| Dash 布局、导航、回调与静态资源 | `scripts/webapp_dash/`；[局部 AGENTS.md](scripts/webapp_dash/AGENTS.md) |
| 回归测试与小型样例 | `tests/`；[局部 AGENTS.md](tests/AGENTS.md) |
| 启动与部署 | `start-reacnet-scope.sh`、`deploy/`、`docs/dash_remote_deployment.md` |

## 跨模块不变量

- Species 用 RNG 的精确结构身份；分子式和质量仅用于搜索、分组。Reaction Type
  保留方向与化学计量重复项；跨工具传递稳定身份，不以展示文本代替身份。
- RNG 是反应、参与原子与键变化的权威来源。坐标服务于几何和可视化，不能覆盖 RNG
  证据；不恢复 `.route` 事件回退或第二套反应检测。
- 原始 RNG 工件只读。索引、任务和设置写入 Dataset Workspace；清理仅影响派生状态。
- 大型证据解析与索引构建在 preparation 中进行；在线分析消费已发布索引和有界数据。
  缺索引、证据不完整与有效空结果必须区分，不能偷偷重建或返回假完整结果。
- Candidate Path Discovery、Continuous MD Support、Path Verification 是不同契约。
  候选路径和观测事件不构成机理证明；具体规则按任务读设计基准及有效 ADR。
- 时间换算、长度单位与证据限制必须显式表达；不能猜测物理单位或把 TP 当速率常数。

## 开发与验证

在仓库根目录执行，依赖以 `pyproject.toml`、`uv.lock` 和 CI 为准：

```bash
uv sync --locked
uv run --locked pytest -q tests/test_timed_evidence.py  # 示例：只跑受影响的测试
uv run --locked pytest -q                              # 全量，与 CI 一致
uv run --locked reacnet-scope --help
./start-reacnet-scope.sh --check                        # 本地启动配置检查
```

- 按影响面选择 [验证指南](docs/agents/testing.md) 中的测试。跨模块、公共契约或依赖变更
  跑全量；纯文档改动检查内容和链接即可。没有配置统一 lint/typecheck，不虚构命令。
- 延续现有 Python、pytest、uv 工具链；依赖变更才更新锁文件。
- 不覆盖工作区已有改动。交付说明改动行为、实际验证结果和未验证部分；小型 fixture
  通过不代表大型真实数据或跨平台验收完成。

## 提交与上传检查

- 每次向 GitHub 推送或创建、更新 PR 前，检查将进入远端的完整差异，而不只看本次暂存区：
  核对 `git status --short`、新增文件、已暂存文件，以及当前分支相对目标分支的文件清单和
  大小。逐项确认新增文件与大文件的用途；未经检查不批量 `git add -A`。
- 排除不属于仓库交付物的本地研究完整结果、原始 RNG/MD 数据、派生索引、缓存、日志、
  截图和临时文件；检查是否含凭据、私有配置或不应公开的本机路径。需要保留研究依据时，
  优先提交结论、复现方法和必要的小型样例，并为本地生成物添加针对性的忽略规则。
- 推送前复核暂存清单与 `git diff --cached --check`，确认文档没有指向被排除文件的失效链接。
  `.gitignore` 不会使已跟踪文件自动退出版本控制；先停止跟踪并保留本地文件，再核对
  最终提交和远端分支。交付时说明排除了什么，以及尚未消除的历史记录风险。

## Skills 与协作

按任务选用，不在每个任务开始时加载整个 skills 库：

| 任务 | 项目 skill |
| --- | --- |
| 使用已有 RNG 数据检索物种、解释路径或比较多个来源 | [reacnet-analysis-usage](.agents/skills/reacnet-analysis-usage/SKILL.md) |
| RNG 适配器、离线索引、准备任务、在线读取边界 | [reacnet-evidence-indexing](.agents/skills/reacnet-evidence-indexing/SKILL.md) |
| 新增或修改科学分析的身份、证据、结果与导出契约 | [reacnet-analysis-contracts](.agents/skills/reacnet-analysis-contracts/SKILL.md) |
| Dash 数据集切换、跨工具交接、回调状态与能力提示 | [reacnet-dash-workflows](.agents/skills/reacnet-dash-workflows/SKILL.md) |

通用 debugging、TDD、review、research 等技能按各自适用范围使用；普通实现不自动转成
访谈、工单拆分或发布流程。用户已经明确的范围和授权不因 skill 的通用步骤而重复确认。
已有 skills 的取舍与本配置的维护方法见 [配置方案](docs/agents/agent-configuration.md)。

Issues/PRDs 使用 GitHub Issues 和 `gh`，操作约定见
[issue-tracker.md](docs/agents/issue-tracker.md)；triage 使用
[triage-labels.md](docs/agents/triage-labels.md) 的默认 Matt Pocock 词汇。
仅在任务涉及这些工作流时读取；调用 skill 本身不代表授权向外发布内容。
