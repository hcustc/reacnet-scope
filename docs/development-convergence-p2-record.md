# P2 身份—事件—轨迹实施记录

日期：2026-09-19。

状态：P2 的 C05–C06 已完成。本记录保留当批验收事实；后续 P3–P5 的完成状态见
[收敛开发计划](development-convergence-plan.md)。

依据：[收敛开发计划](development-convergence-plan.md)、
[软件设计基准](software-design-baseline.md)、
[P0–P1 实施记录](development-convergence-p0-p1-record.md)。

## 1. 交付行为

### C05：Species 身份与来源说明

- Species 详情继续以 RNG 的精确标签为身份，并明确分子式只用于检索和分组。
- 只读取 RNG 工件旁不超过 1 MiB 的显式 `rng_run.json` 和
  `timed_output_validation.json`。`miso`、HMM、`stepinterval`、`timestep_ps`、RNG
  版本、分支和修订均保留各自来源路径；未记录字段显示未知。
- 详情区分 Species 标签与 Molecular Evidence。存在原生 `.timeline.h5` 或兼容 CSV
  证据只表示可通过已发布索引核查具体 Molecule Instance，不把标签本身当作原始分子实例。
- 在线详情不打开大型 HDF5、`.species` 或轨迹文件，也不从目录名和日志推测处理参数。

### C06：事件交接与可恢复书签

- 精确 Species、直接通道、Reaction Occurrence、局部轨迹和事件包继续传递精确
  Species／Reaction Type／`event_id`，没有新增第二套身份。
- 新增 `reacnet-scope/event-bookmark/v1`。书签只保存 Dataset Identity、源修订指纹、
  稳定 `event_id` 和前／后展示帧数；不缓存事件行或轨迹结果。
- 书签使用 session storage 跨页面保留。恢复顺序为：核验 Dataset Identity → 核验源修订
  → 从已发布事件索引按 `event_id` 重新读取。数据集或修订变化、事件缺失、索引未准备／
  过期／无效时拒绝旧结果，不改变 Current Dataset，也不自动读取轨迹。
- 数据集切换仍清空内存中的事件选择、查看器和谱系结果。保存的书签即使仍在 session 中，
  也不能在另一个数据集或修订上恢复。
- 修复显式 `0` 帧窗口被 `or 3` 覆盖的问题；零前帧或零后帧现在能写入书签并传到轨迹读取。

## 2. Dataset Identity 边界修复

真实数据的公共前缀 `trajectory.lammpstrj` 是指向外部 MD 轨迹的符号链接，而
`.reactionabcd`、`.species` 和 `.timeline.h5` 位于 RNG 结果目录。此前 Current Dataset
验证若直接从公共前缀取身份，会跟随符号链接而绑定轨迹工作区；事件、组成等 RNG 证据则
属于结果目录工作区，造成同一候选身份分裂。

验证与只读候选检查现在优先从候选的 RNG 证据工件取得 Dataset Identity；只有纯轨迹候选
才回退到公共前缀。轨迹索引仍可位于外部轨迹自己的工作区。该修复不迁移、不重建索引，
也不修改符号链接或 RNG 原始文件。

## 3. 自动化验证

- P2 定向回归：dataset context/discovery/selection/switch、event index、event CLI/export、
  event package、RNG event output、time axis、timed evidence 和 Dash smoke，共
  `255 passed, 2210 warnings in 13.48s`。
- 全量标准命令：`uv run --locked pytest -q`，结果
  `550 passed, 2431 warnings in 24.83s`。
- 警告仍是既有 `dash_table.DataTable` 弃用提示。
- `git diff --check` 通过。
- Dash HTTP 回调测试覆盖书签创建、同修订恢复、修订变化拒绝、选择卡恢复和 Species
  身份说明。P2 未另做新的人工浏览器视觉走查；P1 的五工作区浏览器结果仍有效，P5 将做
  最终浏览器验收。

## 4. 真实案例 A、B

来源：
`/home/huangchen/cal_proc/production_md/runs/phi1_2500K_iter32_000_seed256788_20260914/`
`rng_timed_hdf5_perf_20260915/trajectory.lammpstrj`。

- Dataset ID：`1044f75c314b4383a040`。
- 源修订：`eaf7a1bd2f34319375d6c3cc4ad457ba792325b979622eb18139de9c0f376a12`。
- 显式设置来自同目录 `rng_run.json`：`miso=1`、`runHMM=false`、
  `stepinterval=1`、`timestep_ps=0.0001`。
- 核查 Species：`[H][C]1[C]([H])[C]([O])[C]([Cl])[C]1[H]`；详情将其显示为精确
  RNG Species，并将原生 `.timeline.h5` 标为可下钻的 Molecular Evidence 来源。
- 核查事件：`rngevt_72544_6f2b9e11023f`，关联状态 `matched`，12 个参与原子。
- 书签窗口为前 2、后 4 帧；按书签重新核验后读取 8 个有界轨迹帧：
  `7254200` 至 `7254900`，步长 100。读取使用现有事件和轨迹索引，没有顺序扫描原始
  19.7 GB 轨迹，也没有启动 preparation。
- participants 事件包大小 7709 bytes，连续两次内存导出 byte-stable，SHA-256 为
  `a6a73cb3916b5241e09d056361946ac6db90e40c16c54609e4b998425a05aa3e`。成员为
  `event.json`、`frames.csv`、`changed_bond_distances.csv`、`trajectory.lammpstrj`、
  `bonds.csv` 和 `README.txt`。

P1 已在同一修订确认目标精确身份具有 19 条生成 Reaction Type、269 条 matched 生成记录。
P2 本次证明其中一个稳定事件可以经修订校验下钻到局部轨迹并确定性导出；这些记录仍不称为
独立实验或正式 Formation Episode。

## 5. 未扩大的范围

- 未做全轨迹同构搜索、Species 身份重建或坐标成键检测。
- 未改变 Event Evidence schema，现有大型索引无需重建。
- 未进入 P3 的继续追踪分支，也未处理 P4 的批量自动分组和单样本置信区间问题。
