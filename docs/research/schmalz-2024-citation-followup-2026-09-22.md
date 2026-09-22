# Schmalz 2024：后续引用与方法发展

检索日期：2026-09-22（Asia/Shanghai）。这是文献研究，不是软件功能承诺或正式契约变更。

目标论文：Schmalz et al., *Reaction path identification and validation from molecular dynamics simulations of hydrocarbon pyrolysis*, DOI [10.1002/kin.21719](https://doi.org/10.1002/kin.21719)。此前的论文与软件对照见[精读笔记](schmalz-2024-path-validation.md)。

## 检索范围与计数

实际读取公开 API：OpenAlex 目标记录 `W4394569757` 的 `cited_by_count=16`，`cites` 查询返回 16 条；Semantic Scholar 返回 14 条；Crossref 目标记录的 `is-referenced-by-count=16`。这些是检索时快照，不是 16 个独立方法后续，也不是完整引文普查。[OpenAlex 目标记录](https://api.openalex.org/works/W4394569757)；[OpenAlex 引用列表](https://api.openalex.org/works?filter=cites:W4394569757&per-page=100)；[Semantic Scholar 记录](https://www.semanticscholar.org/paper/ae32a2992265acfaa0569c96143ba7d7f77a273c)；[Crossref 目标记录](https://api.crossref.org/works/10.1002/kin.21719)。

OpenAlex 的 16 条由 13 个期刊文章记录和 3 个预印本记录组成。煤热解与 Maillard 两个预印本有相应正式论文；carbon black 的预印本与正式版标题改变，作者与研究内容对应，作为版本线索处理，不将其计为独立方法突破。Semantic Scholar 另有一篇 2026-09 的泛函预印本。全文检索还发现一篇 NEB 博士论文引用。数据库日期会使用 1 月 1 日等占位值，期刊卷期与 online 日期也会不同；下表优先使用出版页面/出版社元数据，保留不能独立核实的部分。

引文核查分级：**R**＝正文/参考文献全文核实；**M**＝出版商登记到 Crossref 的参考文献 DOI 核实；**I**＝仅引文索引返回，尚未独立核对参考文献。研究内容另外以可读主文、官方摘要或软件文档为依据，不由引用关系直接推出方法继承。

## 检索到的直接引用清单

| 年份 | 论文与 DOI | 引文依据 | 本次判定与阅读深度 |
| --- | --- | --- | --- |
| 2025 | [AUTOGRAPH: Chemical Reaction Networks in 3D](https://doi.org/10.1021/acs.jcim.4c02106) | M，ref19 | 方法/可视化；重点阅读见下文 |
| 2025 | [An experimental and modeling study on norbornane pyrolysis aided by chemical information from neural network-assisted molecular dynamics](https://doi.org/10.1016/j.combustflame.2025.114039) | M，ref29 | DPMD 辅助动力学模型与实验比较；已读出版社摘要 |
| 2025 | [Nucleation rate of carbonaceous nanoparticles by n-heptane pyrolysis at high pressure and temperature via molecular dynamics simulations](https://doi.org/10.1080/02786826.2025.2480625) | M，ref54 | 核实出版信息与引文；未精读，不能称已验证原流程扩展 |
| 2025 | [Investigation of soot precursor molecules during inception by acetylene pyrolysis using reactive molecular dynamics](https://doi.org/10.5194/ar-3-185-2025) | R、M | 已读全文相关方法与引用上下文；侧重 soot precursor 结构与演化 |
| 2025 | [ReaxANA: Analysis of Reactive Dynamics Trajectories for Reaction Network Generation](https://doi.org/10.1021/acs.jcim.5c00521) | M，ref80 | 方法/轨迹分析；重点阅读见下文 |
| 2025 | [Assessing accelerated reaction network exploration with ChemTraYzer-TAD and PESmapping](https://doi.org/10.1016/j.comptc.2025.115461) | R，作者预印本 ref30 | 原作者团队直接后续；探索覆盖与方法互补性 |
| 2026 卷期；2025 在线记录 | [Nucleation, surface growth and coagulation of soot by hierarchical modeling](https://doi.org/10.1016/j.powtec.2025.121747) | M | 核实出版信息与引文；未精读 |
| 2026 卷期；2025 在线记录 | [Molecular dynamics investigation on supercritical water co-gasification of cellulose and PET plastic model compounds](https://doi.org/10.1016/j.ijhydene.2025.152807) | M，ref50 | 核实出版信息与引文；未精读 |
| 2025 | [Evaluating Fuel Properties of SAF Blends: From Component-Based Estimation to Molecular Dynamics](https://doi.org/10.3390/en18246401) | M，ref160 | 核实出版信息与引文；未精读 |
| 2026 | [Mechanistic insights into CO₂-enhanced in-situ pyrolysis of tar-rich coal: Experiments and ReaxFF molecular dynamics simulations](https://doi.org/10.1016/j.fuel.2026.138749) | M，ref19 | 核实出版信息与引文；未精读 |
| 2026 | [Phase-dependent evolution of crystallinity in carbon black via reaction-resolved molecular dynamics](https://doi.org/10.1016/j.carbon.2026.121423) | M，ref14 | 已读出版社摘要/引言；以指定反应网络研究相态、工艺与结晶度，不能等同无模板路径发现 |
| 2026 | [Formation of 5-methyl-2-furanmethanol which is a caramel flavor compound in the glutamic acid-glucose Maillard reaction: A ReaxFF-DFT study](https://doi.org/10.1016/j.crfs.2026.101454) | R、M，ref31 | 已读 Europe PMC 全文 XML；ReaxFF/DFT 与实验的应用扩展 |
| 2026；卷期标为 12 月 | [Atomic-scale insights into laser-induced patterned graphitization of combustion-derived soot](https://doi.org/10.1016/j.chphi.2026.101088) | I，两库均返回 | 已读出版社摘要；在线可读先于卷期，具体在线日期仅索引给出，参考文献未独立核实 |
| 2026-09-08，预印本 | [Balanced and broadly-normed meta-generalized gradient approximation](https://arxiv.org/abs/2609.09034) | R，ref36 | 引言以原文说明反应路径精度的重要性；泛函研究，不是 RMD 后处理流程延续 |
| 封面 2024-12，学位论文 | [A robust, automated nudged elastic band method in internal coordinates](https://ediss.uni-goettingen.de/handle/11858/16428) | R，搜索索引可读参考文献 ref23 | Björn Hein-Janke；机构 PDF 直接访问 403，只核实封面/参考文献片段，未作为精读结论依据 |

M 级记录通过 `https://api.crossref.org/works/<DOI>` 读取出版商登记的 `reference` 字段。关键两篇 JCIM 的 DOI 由 publisher 直接声明；多数 Elsevier 条目的 DOI 由 Crossref 对参考文献匹配。引用核实不等于审查了整篇论文。

版本线索：[煤热解 SSRN](https://doi.org/10.2139/ssrn.5798933)、[Maillard SSRN](https://doi.org/10.2139/ssrn.6292208)、[carbon black Research Square](https://doi.org/10.21203/rs.3.rs-6630540/v1)。CTY-TAD/PESmapping 的 [2024 ChemRxiv 版本](https://doi.org/10.26434/chemrxiv-2024-79drl-v2)与正式论文合并讨论。

## 两篇直接引用的方法论文

### AUTOGRAPH：网络探索与反应物依赖

Kuboth、Meissner、Kopp、Meisner，JCIM 2025，65(7)，3127–3136。在线 2025-01-15。官方摘要给出 3D 网络、交互筛选、最短路径及 CHEMKIN 导入。[出版社](https://pubs.acs.org/doi/10.1021/acs.jcim.4c02106)；[官方代码](https://github.com/failip/autograph)。

当前源码的改造 Dijkstra 维护已出现物种集合，检查其他共同反应物是否具备；界面支持局部展开、过滤撤销、路径高亮和 XYZ 联动。这比单纯物种两两连边更能表达反应依赖。不过，该集合不消耗反应物、不记录分子重数、事件时间或原子连续性；权重之和是算法代价，不能叫路线活化能。[路径算法](https://github.com/failip/autograph/blob/main/src/lib/graphs/graph.ts)；[图交互](https://github.com/failip/autograph/blob/main/src/lib/graphs/Graph.svelte)。

对 Scope 的推论：优先借鉴局部展开、共同反应物说明、结构联动和可撤销筛选。网络视图与具体事件证据分开，不能因 3D 展示或路径算法更复杂就提高科学结论等级。

### ReaxANA：显式图去噪与网络生成

Zhu、Chen、Gao，JCIM 2025，65(16)，8549–8562。在线 2025-08-06。官方摘要描述均相/非均相轨迹分析、结构异构体识别、图规则去振荡/节点收缩，以 TNT ReaxFF 分解演示。[官方摘要](https://pubmed.ncbi.nlm.nih.gov/40767103/)；[出版社](https://pubs.acs.org/doi/10.1021/acs.jcim.5c00521)。

官方代码按 `Tlag` 窗口消除不同振荡模式，并以 `StableMolLag` 等条件收缩节点。出版社 SI 列有量化计算坐标包，故不能说它完全没有 QC；本次未证实通用自动 TS/IRC 验证闭环。[振荡过滤源码](https://github.com/XinChenQC/ReaxANA/blob/main/tool_removeOscill.py)；[节点收缩源码](https://github.com/XinChenQC/ReaxANA/blob/main/tool_contract.py)。

对 Scope 的推论：可研究规则化、可逆、保留原始记录的折叠视图及敏感性比较；不能引入坐标重新成键作为第二套 RNG 反应检测，也不能默认短寿命等于无意义。

ReaxANA 官方 README 指向后续项目 Phlox，但后者当前自述振荡过滤和节点收缩暂未启用、分类仅部分完成；软件迁移不等于功能成熟。[ReaxANA](https://github.com/XinChenQC/ReaxANA)；[Phlox](https://github.com/XinChenQC/Phlox)。

两篇的直接引用均由出版社登记参考文献核实，但主文未取得，未逐句确认引用语境。上述实现分析针对检索时官方源码，不声称复现原发表版本。

## 原作者团队：确有推进，但不是统一的第二版流程

### CTY-TAD/PESmapping：比较探索盲区

2025 年正式论文研究乙基甲酸酯自由基，两种探索方法合计发现 27 种反应，交集 11 种；对解离与环化存在不同偏好。它使用 CTY2，主要推进采样覆盖/互补性，不是原 2024 热解流程的全自动改版，也不能把力场层面的比较称为逐条高水平 QC 验证。[出版社摘要](https://www.sciencedirect.com/science/article/abs/pii/S2210271X25003974)；[作者预印本全文](https://chemrxiv.org/engage/api-gateway/chemrxiv/assets/orp/resource/item/6763338181d2151a022448e4/original/Paper_CTY_TAD_PESmapping.pdf)。

对 Scope 的推论：跨来源比较应记录方法偏倚、重合和独有通道，而不仅比较路径条数；未发现不能直接当作不存在。

### Schmalz 博士论文：反应分类是新的延伸

*Improved combustion and pyrolysis reaction network exploration with reactive molecular dynamics*，答辩于 2025-04-02，RWTH 2026 入库。第 3 章整合原文，第 4 章研究活性原子成断键局部环境的反应分类；六类模板覆盖 407 个反应中的 201 个。多步合并事件与模板特异性仍限制覆盖；高自旋处理仍是展望，未证明已解决。[机构记录](https://publications.rwth-aachen.de/record/1025143)；[全文，第 4 章](https://publications.rwth-aachen.de/record/1025143/files/1025143.pdf)。

对 Scope 的推论：反应家族标签有助于筛选和归纳，但应允许未分类，并与精确 Reaction Type 身份分开。

### ChemTraYzer 3：软件工程确实前进了

CTY3 首个 beta 与原论文发表于同一时期，不能画成完全由该论文产生的后继软件。b4 的 changelog（2025-02-21）明确增加自动 QC TS 工作流、CREST 构象处理、RRHO 和 MESS 接口。[官方版本记录](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/about/changelog.html)。

官方 API 有 TS、IRC、NEB-TS 任务和错误反应等失败分类；但 `check_irc` 默认 False，关闭时可能把成功优化的 TS 直接关联到预期反应。支持 IRC 与本次计算实际完成 IRC 必须区分。[官方 QM API](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/reference/qm.html)。

本次未找到独立的 CTY3 期刊论文；软件仍标 beta，旧功能尚未全部移植，也未核实自动多自旋面/MECP 工作流。软件发布应与同行评审论文分开列出。[官方首页](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/)；[软件发布](https://zenodo.org/records/14733958)。

对 Scope 的推论：可先评估外部后端适配，优先导入实际执行项目、端点和失败原因；无需先重写作业执行器。尚未安装或运行 CTY3。

### 其他团队延续：尚未核实直接引用原文

- **Roy et al. 2026，Automatic Differentiation for Enhanced Potential Energy Surface Navigation: Improved Minimum and Transition State Search in Molecular Dynamics**，JCTC，DOI [10.1021/acs.jctc.6c00163](https://doi.org/10.1021/acs.jctc.6c00163)。ADfied LAMMPS 提供 Hessian，LMP-Gau 连接 Gaussian，改进力场驻点/TS 搜索收敛。仅核对官方摘要和机构记录；更好的数值导数不等于消除势函数偏差或处理自旋跃迁。[机构记录](https://publications.rwth-aachen.de/record/1033723)。
- **Mudimu et al. 2026，A theoretical analysis of propargyl oxidation by the hydroperoxyl radical**，2026-03-25 ChemRxiv 预印本，DOI [10.26434/chemrxiv.15001234/v1](https://doi.org/10.26434/chemrxiv.15001234/v1)。官方摘要呈现 CTY-TAD→高水平 QC→RRKM/master equation→反应器与灵敏度分析。它接近把候选转为动力学子模型的闭环；本次仅核实未同行评审预印本，不声称已逐项审查计算。[预印本主源](https://chemrxiv.org/doi/full/10.26434/chemrxiv.15001234/v1)。

CTY 系列的 RMD→QC 基础早于 2024；例如 2018 年 [Automated Chemical Kinetic Modeling via Hybrid Reactive Molecular Dynamics and Quantum Chemistry Simulations](https://doi.org/10.1021/acs.jcim.8b00078) 是前史，不能列成后续。[官方功能引文表](https://ltt.pages.git-ce.rwth-aachen.de/ChemTraYzer/about/credits.html)。

## 二级关联中最值得精读的一篇：超图挖掘与量化回写

Stan-Bernhardt et al., **Automated Discovery of Reactive Events via Hypergraph Mining of Ab Initio Atomistic Simulations**，JCTC 22(4)，1674–1686，在线 2026-02-12，DOI [10.1021/acs.jctc.5c01682](https://doi.org/10.1021/acs.jctc.5c01682)。已读全文及补充材料；核查全部 79 条参考文献，没有目标 Schmalz 论文，ref34 引用 AUTOGRAPH。因此这是二级关联，不能加到原论文的直接引用数中。[全文](https://pmc.ncbi.nlm.nih.gov/articles/PMC12937057/)；[实际读取的全文 XML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12937057/fullTextXML)。

其方法以完整、保留计量的定向反应超图为基础，挖掘跨独立模拟出现的反应组合。支持度按包含模式的模拟数统计；条件比较采用 Fisher 检验与多重检验修正。筛选后从轨迹提取端点，经 ωB97X-3c DE-GSM、TS 优化/频率，将精化能量回写网络。体系是 NH₃/CO₂/H₂O，不是烃热解。[主文 §§2–3](https://pmc.ncbi.nlm.nih.gov/articles/PMC12937057/)；[补充材料 §5，S-16–S-17](https://pubs.acs.org/doi/suppl/10.1021/acs.jctc.5c01682/suppl_file/ct5c01682_si_001.pdf)。

这篇将“挑选什么值得量化”推进到可统计比较的反应组合，并实现量化结果回写，因而与 Scope 的长期方向尤其相关。应注意：模式是反应集合共现，不要求一个具体分子按序经历全部步骤；主文/SI 没有报告 IRC。统计显著也不能消除势函数/增强采样偏差；增强采样时间不提供真实动力学时间。量化精化的环境模型限制仍需保留。[全文及结论](https://pmc.ncbi.nlm.nih.gov/articles/PMC12937057/)。

对 Scope 的推论：

1. 多来源分析分别记录 occurrence 数、独立 replicate 支持度和条件效应量；先核实样本独立性与可比性，再考虑显著性。
2. 反应组合挖掘可服务于研究优先级，但保留 Candidate / Continuous MD Support 的既有边界；不把共现当成具体历史。
3. 优先研究“来源事件→所选几何→外部计算→精化结果回填”，同时补足逐次尝试、电子态、端点变化与验证是否实际执行的记录。

## 应用方向：走到模型和实验检验

**降冰片烷，Xiao et al. 2025。** 出版社摘要明确将 DPMD 发现的信息用于添加动力学模型反应，比较流动反应器/光电离分子束质谱的物种浓度，并做生成速率和灵敏度分析。这比只展示 MD 路线更接近检验“加入这些反应有没有改善模型”。本次没有读到完整方法/SI，因此不推断每条加入反应如何获得速率常数。[出版社摘要](https://www.sciencedirect.com/science/article/abs/pii/S001021802500077X)。

对 Scope 的推论：未来机理交接应保留新增/替代反应、参数来源与版本，支持下游比较加入前后的预测差异；不能把 MD 高频直接当作速率常数。

**Maillard 反应，Pan et al. 2026。** 全文把 ReaxFF 路线与 Gaussian DFT 驻点、频率、IRC 能量曲线和 GC-O-MS/Py-GC-MS 实验联系起来，说明该思路已扩展到含氧/含氮复杂反应体系。它在引言直接引用原文，主要是应用推进。[全文记录](https://pmc.ncbi.nlm.nih.gov/articles/PMC13242025/)；[本次实际读取的全文 XML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC13242025/fullTextXML)。

本次方法评估：该文理论级别在方法与讨论中表述不完全一致，实验检出产物也不能唯一证明逐步机理；宜当作应用案例，不直接作为自动验证标准。尚未核对 SI/原始量化输出。

**乙炔 soot precursor，Ganguly et al. 2025。** 原文共同作者 Goudeli 参与，论文也致谢 Schmalz 协助 ChemTraYzer。研究扩展到 1350–1800 K 的前驱体结构、环和 soot 生长，引用原文解释复杂网络的研究价值与规模限制；这属于应用延续，不能据此说逐步 QC 验证得到自动化。[全文](https://ar.copernicus.org/articles/3/185/2025/)。

## 对下一轮阅读与软件研究的排序

1. **超图挖掘论文 + SI**：评估跨 replicate 筛选、计算资源分配和结果回填；它提供的参考价值超过单看引文距离。
2. **ReaxANA + 官方代码**：比较我们的返回/往返证据与其图过滤、节点收缩，重点评估科学信息丢失和参数敏感性。
3. **CTY3 QM API/示例**：评估外部计算适配与结果状态导入，先做一例成功和一例失败的验证，不预先承诺接入。
4. **AUTOGRAPH**：借鉴共同反应物依赖、局部探索与结构联动；3D 本身不作为必须实现的目标。
5. **降冰片烷 DPMD 应用**：理解从新增通道到实验预测改善的验收问题，再设计下游机理交接。

这些是基于本次来源的建议。当前产品若继续维持证据工作台边界，可优先改善证据关联与导入导出，把探索模拟、量化作业、速率求解留给外部工具。文献显示这些层面都在发展；可追溯的身份、验证结果和失败记录仍是值得重点研究的集成问题。

## 边界与验证记录

- 保留直接引用、同团队延续、二级关联三种关系，不将“有引用”与“沿用其算法”画等号。
- 没有发现足够证据支持“原文的人工筛选、多步事件和自旋问题已由一个完整平台统一解决”。这是本次可读证据的范围，不是断言不存在其他工作。
- 本次检索为 OpenAlex、Semantic Scholar、Crossref、论文/机构全文和官方软件文档交叉核实；没有完整访问 Web of Science/Scopus 的实时全部引文。
- ACS/部分出版社全文受限时使用官方摘要、作者预印本或出版商登记参考文献，并在相应条目说明；没有运行任何新 MD、QC、软件 benchmark。
- 仅新增研究笔记，未修改实现、已有研究报告或正式契约；最终检查本地 Markdown 链接及格式。
