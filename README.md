# reacnet-scope

`reacnet-scope` 是一个面向 ReacNetGenerator 输出（`.reactionabcd` / species CSV）的轻量查询与可视化工具集，提供：

- Web 前端：SMILES/分子式/质量数反应检索、结构渲染、时间曲线绘图、中间体候选筛选
- CLI：批量检索、路径导出、TOP-N 统计、曲线绘制

## Description

`reacnet-scope` is a data-driven analysis toolkit for aligning reactive MD results with experimental interpretation.  
It parses ReacNetGenerator outputs and provides integrated query, filtering, and visualization workflows across:

- Species lookup by formula, SMILES, and mass (nominal/exact).
- Reaction-pathway search by species or formula-level equations.
- Time-series plotting from species files with formula/SMILES aggregation.
- Intermediate candidate mining using abundance, rise-fall behavior, and lifetime criteria.
- SMILES structure rendering and pathway auditing in a lightweight web UI.

The project includes both CLI and web interfaces so the same core logic can be used for scripted batch analysis and interactive exploration.

## 目录结构

- `run_web.sh` / `run_rng_query_web.sh`: 启动 Web 后端
- `run_cli.sh` / `run_rng_query.sh`: 启动 CLI
- `scripts/webapp/server.py`: Web API 与静态页面服务
- `scripts/webapp/static/`: 前端页面与脚本
- `scripts/rng_query_cli.py`: 终端检索入口
- `rng_tools/`: 反应网络解析与统计核心逻辑

## 快速开始

1. 安装依赖

```bash
uv sync --extra plot
```

2. 启动 Web

```bash
uv run ./run_web.sh 127.0.0.1 8876
```

打开 `http://127.0.0.1:8876`

3. 查看 CLI 帮助

```bash
uv run ./run_cli.sh --help
```

4. 指定 reactionabcd 文件查询

```bash
uv run ./run_cli.sh species --reac /path/to/xxx.reactionabcd --formula C6H4
```

## 默认输入文件规则

默认会按以下顺序寻找 reactionabcd：

1. 环境变量 `RNG_REACTION_FILE`
2. `../datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`（相对本工具目录上一级）
3. `<tool_root>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`
4. `<cwd>/datas/1ER_2500K/rng_data/2CP_O2_1ER.lammpstrj.reactionabcd`

建议在跨项目使用时显式传 `--reac` 或设置 `RNG_REACTION_FILE`。

## 依赖

- Python 3.10+
- 基础依赖：`pandas`、`openpyxl`、`rdkit`
- 可选绘图增强：`matplotlib`（CLI `plot --out-png` 时需要）

### 使用 uv 安装

仅安装基础依赖：

```bash
uv sync
```

安装基础依赖 + 绘图增强依赖：

```bash
uv sync --extra plot
```

## 发布到 GitHub/PyPI

- GitHub：建议提交 `README.md`、`LICENSE`、`pyproject.toml`、`uv.lock`、源码目录（`rng_tools` / `scripts`）。
- PyPI：当前配置已提供可发布元数据与命令入口：
  - `reacnet-scope`
  - `reacnet-scope-web`
