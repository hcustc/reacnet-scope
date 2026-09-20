# P4 可比性收敛交付与验收记录

日期：2026-09-19

本记录对应[收敛开发计划](development-convergence-plan.md)的 P4（C09–C10）。本批统一多来源
记录、逐来源精确目标与比较证据说明，没有新增跨来源结构归一化、主机理排名或通用报表平台。

## 1. 统一来源与曲线契约

- 新增 `reacnet-scope/comparison-source/v1` 来源记录。每个来源保存 Dataset Identity、源修订、
  主工件与兄弟工件、模型迭代、Simulation Condition、Replicate、RNG 处理参数、时间口径、
  身份口径和可用证据层级。
- 模型迭代、条件和 Replicate 分别记录 `confirmed`、`suggested` 或 `unknown`；显式元数据缺失
  时保持未知。`miso`、HMM、step interval、timestep、RNG 版本和源修订只读取同目录下有界的
  `rng_run.json` / `timed_output_validation.json`，不从目录名、日志或输出外观补值。
- Species 多来源结果升级为 schema 2。每个来源仍单独选择精确 RNG Species，不自动认为不同
  SMILES 等价；查询继续保留旧的 `species_file`、`label`、`target_smiles` 字段。
- 多来源 Species 曲线逐来源调用现有 `build_species_evolution` 时间序列核心，并固定为未归一化、
  未平滑、未降采样的原始序列；不再维护另一套在线曲线计算。ZIP 现在同时导出曲线、汇总、
  查询、来源记录和可比性说明。
- 物理时间采用全来源门槛：任一来源缺少或具有无效的 `timestep → ps` 换算时，状态仍逐项
  报告，但整张 ps/ns 图不绘制部分曲线。原始 timestep 模式继续按各来源独立时间轴显示，不
  暗示物理对齐。

## 2. 条件与 Replicate 确认

- 目录扫描返回的温度、模型迭代、条件分组和 Replicate 全部标为“待确认建议”。扫描后不再
  默认选择任何分组。
- 对比页新增可编辑检查表和显式确认框。用户可修改模型迭代、Simulation Condition 和
  Replicate；编辑表格会清除旧确认，未确认的扫描建议由 UI 和核心服务双重拒绝。
- 直接选择多个已管理数据集只表示多个独立来源，不自动称为重复实验。只有显式确认条件及
  Replicate 的组才使用重复统计文案；单来源组不显示虚假的标准差/置信区间结论。
- Batch Compare 结果升级为 schema 2，保留既有矩阵字段，并新增来源记录、精确有向且保留
  计量数的身份口径，以及 `aggregated_reaction_network` 证据层级。界面明确说明网络记录不等于
  具体 Reaction Occurrence 或原子谱系支持。
- 导出按钮改为 ZIP：包含原结果 CSV、来源 CSV/JSON 和比较契约 JSON。公共
  `batch_comparison_to_csv` 继续保留，已有 CLI/Python 调用不受影响。

选择比较来源只写入页面会话 Store；比较回调没有 `app-store` 输出，不改变 Current Dataset。

## 3. 固定真实案例 D

网络级来源：

- iter25 / 2500 K：
  `/home/huangchen/cal_proc/production_md/runs/1ER_2500K_rep3/rng/2CP_O2_1ER.lammpstrj.reactionabcd`
  （40,384,490 bytes）。本次只读来源记录得到 Dataset ID
  `44c35c9ac90706c2f02d`，来源修订
  `06a5045d65e3f7cf8fed722148124d37d2e550ed3f149f03a877e791808ed7eb`。
- iter32 / 2500 K：
  `/home/huangchen/cal_proc/production_md/runs/phi1_2500K_iter32_000_seed256788_20260914/rng_timed_hdf5_perf_20260915/trajectory.lammpstrj.reactionabcd`
  （2,567,173 bytes）。本次只读来源记录得到 Dataset ID
  `2f63b516233af4cb5962`，来源修订
  `31b8085c7434ff348f79e0a23ae726e64894a6c3c3e314e1010f0cace6eee2b9`。

查询固定为 `min_detection_rate=0.5`、`top_n=20`，两个模型/温度标签由本次验收请求明确给出；
Replicate 保持未知，因此输出为 2 个独立来源、0 个已确认重复组。比较约 9.2 s 完成，得到
20 条精确有向 Reaction Type；结果只声明聚合网络检出与 TP，不据此判断主机理改变。

来源分层符合本地既有报告
`analysis_outputs/chlorocyclopentadienone_20260918/iteration_comparison/report.md`：

- iter25 当前来源只有聚合反应网络，RNG 处理元数据未知，没有 timed molecular evidence；仍可
  参加网络级反应检出比较，但不能给出具体 occurrence、原子谱系或连续路径结论。
- iter32 同目录有 Species 与原生 timeline，显式元数据记录 `miso=1`、`runHMM=false`、
  `stepinterval=1`、`timestep_ps=0.0001` 及 RNG 源修订；这些能力信息不会提高 iter25 证据等级。
- 既有报告中的 iter32 具体碳谱系继续作为更高证据层独立查看，不能和 iter25 网络连接直接当成
  两代模型的主通道比较。

对 `/home/huangchen/cal_proc/production_md/runs` 的真实扫描找到 19 个反应来源，形成 9 个待
确认分组建议；全部建议保持 `suggested` 且 UI 默认不选。`iter32 / 2500 K` 的两套 RNG 输出
被建议为 `T2500K_iter32`，仍需用户判断它们是否真是可统计 Replicate，而不是同一轨迹的不同
处理输出。

两个 iter32 Species 文件存在，但当前 Dataset Workspace 均缺 Species Abundance Index；在线
目录查询正确返回 `missing_index`，本批没有在查询路径偷偷重建，也没有运行新的 RNG/MD。
精确目标曲线、缺目标、有效零值、缺索引和物理时间全来源门槛由小型固定索引测试覆盖。

## 4. 验证

- P4 定向：`tests/test_species_compare.py`、`tests/test_batch_compare.py`、
  `tests/test_time_axis_contract.py`，35 项通过。
- Dash/在线边界回归：`tests/test_dash_smoke.py`、`tests/test_online_index_contract.py`、
  `tests/test_dataset_switch_callbacks.py`，155 项通过。
- 全量：`560 passed, 2628 warnings in 32.70s`。警告均为 Dash 内置
  `dash_table.DataTable` 的既有弃用提示。
- `./start-reacnet-scope.sh --check` 通过；默认
  `/media/huangchen/T3000/rng_data_2500K` 当前未挂载或不可访问，但不阻止启动。
- 在临时 8061 端口实际启动 Dash；`/_dash-layout` 与 `/_dash-dependencies` 均返回 200。
  布局和回调依赖均包含来源检查表、显式确认、
  模型迭代、条件与 Replicate 控件。服务随后用 Ctrl+C 正常停止。未执行人工浏览器视觉回归。

## 5. 兼容与回退

`compare_species_sources`、`run_grouped_batch_comparison` 和既有 CSV 导出入口继续保留；schema 2
只新增来源/可比性字段，不删除旧曲线、汇总、矩阵和详情字段。回退代码不删除 RNG 原始工件、
Dataset Workspace 或已有索引；P4 没有改变大型索引 schema，无需重建。旧客户端可继续读取旧
字段，但不会获得新的来源与证据层级说明。后续退役 UI 清理与最终验收见
[P5 记录](development-convergence-p5-record.md)。
