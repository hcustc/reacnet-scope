# 验证入口

本文件提供选测地图，规范来自设计基准；测试文件只说明现有验证入口。
命令在仓库根目录运行。开发环境用 `uv sync --locked`，默认 dev 组包含测试、Web 与轨迹依赖。

## 按影响面选测

选取相关测试文件并传给 `uv run --locked pytest -q`，必要时用 `-k` 缩小复现范围。

| 改动 | 现有测试入口 |
| --- | --- |
| 原生/CSV 选择、聚合事件、身份与关联 | `tests/test_timed_evidence.py`、`tests/test_rng_event_outputs.py`、`tests/test_event_evidence_index.py` |
| preparation、工作区、取消与续建 | `tests/test_preparation_management.py`、`tests/test_workflow_services.py` |
| 在线读取边界与轨迹持久化 | `tests/test_online_index_contract.py`、`tests/test_trajectory_index_persistence.py` |
| 数据集发现、权限、身份与能力 | `tests/test_dataset_discovery.py`、`tests/test_directory_browser.py`、`tests/test_dataset_context.py`、`tests/test_analysis_capabilities.py` |
| 反应/物种索引、组成、质量 | `tests/test_network_indexes.py`、`tests/test_composition_index.py`、`tests/test_isotopic_mass_search.py` |
| 物理时间与表观动力学 | `tests/test_time_axis_contract.py`、`tests/test_kinetics.py` |
| 候选发现与明确序列验证 | `tests/test_candidate_paths.py`、`tests/test_event_paths.py`、`tests/test_pathway_cli.py` |
| 分子谱系与物种命运 | `tests/test_molecule_lineage.py`、`tests/test_species_fate.py` |
| 事件包、DFT 与 QC 交接 | `tests/test_event_package.py`、`tests/test_event_export_cli.py`、`tests/test_dft_geometry.py`、`tests/test_reaction_readiness.py` |
| 批量条件/重复对比 | `tests/test_batch_compare.py` |
| Dash 布局与会话交互 | `tests/test_dash_smoke.py`、`tests/test_dataset_switch_callbacks.py`、`tests/test_dataset_selection_ux.py` |
| 依赖和安装入口 | `tests/test_dependency_contract.py`、`tests/test_preparation_management.py` |

跨模块、公共结果 schema、共享服务或依赖变化，完成定向验证后跑完整 CI 命令：

```bash
uv run --locked pytest -q
```

`.github/workflows/test.yml` 当前在 Linux / Python 3.11 上运行该命令。
设计基准的 Python 3.10+ 与 macOS/Windows 支持目标需单独验证，不能从这一项 CI 推导。
仓库没有配置统一 lint、formatter 或 typecheck 命令。

## Dash 浏览器验收

UI 的共享组件约定见 [工作台 UI](../ui-workbench.md)。浏览器工具是可选的 `browser` 依赖组，常规全量测试会跳过浏览器模块；CI 另有 Chromium job。

```bash
uv sync --locked --group browser
uv run --locked --group browser playwright install chromium
REACNET_SCOPE_BROWSER_TESTS=1 uv run --locked --group browser pytest -q tests/browser
```

Linux CI 安装浏览器系统库时使用 `playwright install --with-deps chromium`。可设置 `REACNET_SCOPE_SCREENSHOTS=/tmp/reacnet-ui-screenshots` 保存截图。测试服务器绑定本机临时端口，数据源和 Dataset Workspace 均位于 pytest 临时目录；页面请求必须保持本机来源。

## 可观察的验收

- CLI 改动通过安装入口 `uv run --locked reacnet-scope ...` 验证参数、错误和导出；
  `--help` 只证明入口可用，不能代替行为测试。
- Dash 改动至少检查 layout / dependencies；改动实际交互时，有可用浏览器工具就验证
  用户操作链和可见状态。工具不可用时明确未完成浏览器验收。
- 启动配置变更可运行 `./start-reacnet-scope.sh --check`；部署相关任务再读取部署文档。
- 在线性能变更除耗时外，还看原始源读取、查询计划、读取范围和 peak RSS；用两个数据规模
  检查无关数据增长是否影响局部查询。规模门槛见设计基准第 15 节，不能用小 fixture 宣称达标。
- 有用户提供的代表性数据时，保留来源工件，记录准备/查询命令和固定预期；没有真实数据时
  报告该验收缺口，不自动下载大型数据或将私有数据提交仓库。

纯文档/skill 修改检查链接、命令来源、元数据和范围；新脚本才需要执行脚本验证。
无需为了文案变更跑科学计算全量测试。
