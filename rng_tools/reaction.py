"""RDKit-based SMILES/formula utilities and ReacNetGenerator data parsers.

Provides common building blocks used by multiple analysis workflows:
  - SMILES ↔ formula conversion (with caching)
  - moname file scanning
  - reactionabcd file parsing & canonicalization
  - reaction classification (oxidation / coupling / fragmentation …)
  - net-flux calculation (forward − reverse)
  - species-file frame-level tracking & reaction frame location
  - CSV / text report helpers
"""

from __future__ import annotations

import os
import re
import csv
import json
import datetime
from collections import defaultdict
from typing import Dict, Set, List, Tuple, Optional, Sequence

from rdkit import Chem
from rdkit.Chem import rdMolDescriptors
from rdkit import RDLogger

RDLogger.DisableLog("rdApp.*")


# ═══════════════════════════════════════════════════════════
#  1. 基础 SMILES 工具
# ═══════════════════════════════════════════════════════════

def smiles_to_formula(smi: str) -> Optional[str]:
    """SMILES → 分子式 (Hill order).  None if parse fails."""
    try:
        mol = Chem.MolFromSmiles(smi)
        if mol:
            return rdMolDescriptors.CalcMolFormula(mol)
    except Exception:
        pass
    return None


def canonical_smiles(smi: str) -> Optional[str]:
    """SMILES → RDKit canonical SMILES.  None if parse fails."""
    try:
        mol = Chem.MolFromSmiles(smi)
        if mol:
            return Chem.MolToSmiles(mol, canonical=True)
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════
#  2. moname 文件扫描
# ═══════════════════════════════════════════════════════════

def collect_smiles_for_formulas(
    moname_path: str,
    target_formulas: Set[str],
    *,
    verbose: bool = True,
) -> Dict[str, Dict[str, int]]:
    """扫描 moname 文件, 收集指定分子式的所有原始 SMILES.

    Returns:
        {formula: {orig_smiles: count}}
    """
    if verbose:
        print(f"[moname] 扫描 {os.path.basename(moname_path)}, "
              f"搜索 {', '.join(sorted(target_formulas))} ...")

    result: Dict[str, Dict[str, int]] = {f: defaultdict(int) for f in target_formulas}

    with open(moname_path) as fh:
        for line in fh:
            parts = line.strip().split()
            if not parts:
                continue
            smi = parts[0]
            f = smiles_to_formula(smi)
            if f in result:
                result[f][smi] += 1

    if verbose:
        for f in sorted(target_formulas):
            total = sum(result[f].values())
            n_smi = len(result[f])
            print(f"    {f}: {total} 条, {n_smi} 种 SMILES")

    return result


def build_canonical_map(
    variant_counts: Dict[str, int],
) -> Dict[str, Set[str]]:
    """从 {orig_smiles: count} 构建 {canonical: {orig_smiles, …}}."""
    cmap: Dict[str, Set[str]] = defaultdict(set)
    for smi in variant_counts:
        can = canonical_smiles(smi)
        if can:
            cmap[can].add(smi)
    return dict(cmap)


# ═══════════════════════════════════════════════════════════
#  3. reactionabcd 文件解析
# ═══════════════════════════════════════════════════════════

def build_formula_cache(reac_path: str, *, verbose: bool = True) -> Dict[str, str]:
    """一次性扫描 reactionabcd, 缓存所有 SMILES → formula 映射."""
    if verbose:
        print(f"[cache] 缓存 {os.path.basename(reac_path)} 中所有 SMILES 的分子式 ...")

    all_smiles: Set[str] = set()
    with open(reac_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or '->' not in line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            reaction = parts[1]
            lr = reaction.split('->')
            if len(lr) != 2:
                continue
            for side in lr:
                for s in side.split('+'):
                    all_smiles.add(s.strip())

    cache: Dict[str, str] = {}
    for smi in all_smiles:
        f = smiles_to_formula(smi)
        if f:
            cache[smi] = f

    if verbose:
        print(f"    缓存了 {len(cache)} 种 SMILES")
    return cache


class ParsedReaction:
    """一条解析后的反应."""
    __slots__ = ('count', 'reactant_smiles', 'product_smiles',
                 'reactant_formulas', 'product_formulas',
                 'canonical_rxn', 'formula_rxn')

    def __init__(self, count, reactant_smiles, product_smiles,
                 reactant_formulas, product_formulas):
        self.count = count
        self.reactant_smiles = reactant_smiles
        self.product_smiles = product_smiles
        self.reactant_formulas = reactant_formulas
        self.product_formulas = product_formulas

        # 规范化反应字符串  (惰性计算)
        r_can = sorted(canonical_smiles(s) or s for s in reactant_smiles)
        p_can = sorted(canonical_smiles(s) or s for s in product_smiles)
        self.canonical_rxn = ' + '.join(r_can) + ' -> ' + ' + '.join(p_can)
        self.formula_rxn = (' + '.join(reactant_formulas) + ' -> '
                            + ' + '.join(product_formulas))


def iter_reactions(
    reac_path: str,
    formula_cache: Dict[str, str],
) -> "Generator[ParsedReaction]":
    """迭代 reactionabcd 文件, yield ParsedReaction."""
    with open(reac_path) as fh:
        for line in fh:
            line = line.strip()
            if not line or '->' not in line:
                continue
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            count = int(parts[0])
            lr = parts[1].split('->')
            if len(lr) != 2:
                continue

            r_smi = [s.strip() for s in lr[0].split('+')]
            p_smi = [s.strip() for s in lr[1].split('+')]
            r_f = [formula_cache.get(s, smiles_to_formula(s) or '?') for s in r_smi]
            p_f = [formula_cache.get(s, smiles_to_formula(s) or '?') for s in p_smi]

            yield ParsedReaction(count, r_smi, p_smi, r_f, p_f)


def filter_reactions_by_formula(
    reac_path: str,
    formula_cache: Dict[str, str],
    target_formulas: Set[str],
    *,
    role: str = "reactant",           # "reactant" | "product" | "any"
    verbose: bool = True,
) -> Dict[str, List[ParsedReaction]]:
    """筛选包含目标分子式的反应.

    Returns:
        {formula: [ParsedReaction, …]}
    """
    if verbose:
        print(f"[filter] 筛选含 {', '.join(sorted(target_formulas))} 的反应 (role={role}) ...")

    result: Dict[str, List[ParsedReaction]] = {f: [] for f in target_formulas}
    total = 0

    for rxn in iter_reactions(reac_path, formula_cache):
        total += 1
        for tf in target_formulas:
            if role in ("reactant", "any") and tf in rxn.reactant_formulas:
                result[tf].append(rxn)
            elif role in ("product", "any") and tf in rxn.product_formulas:
                result[tf].append(rxn)

    if verbose:
        print(f"    共 {total} 条反应")
        for tf in sorted(target_formulas):
            print(f"    {tf}: {len(result[tf])} 条匹配")
    return result


# ═══════════════════════════════════════════════════════════
#  4. 反应合并 & 规范化
# ═══════════════════════════════════════════════════════════

def merge_reactions(
    rxn_list: List[ParsedReaction],
    *,
    classifier=None,
) -> List[dict]:
    """合并同一规范反应, 可选附加分类.

    Args:
        classifier: callable(ParsedReaction) -> (category, subcategory) or None

    Returns:
        list of dicts sorted by count desc:
        [{'canonical_rxn', 'formula_rxn', 'count', 'category', 'subcategory'}, …]
    """
    merged: Dict[str, dict] = {}

    for rxn in rxn_list:
        key = rxn.canonical_rxn
        if key not in merged:
            cat, subcat = classifier(rxn) if classifier else ("", "")
            merged[key] = {
                'canonical_rxn': key,
                'formula_rxn': rxn.formula_rxn,
                'count': 0,
                'category': cat,
                'subcategory': subcat,
            }
        merged[key]['count'] += rxn.count

    return sorted(merged.values(), key=lambda x: x['count'], reverse=True)


# ═══════════════════════════════════════════════════════════
#  5. 反应分类器 (用于 radical pathway 分析)
# ═══════════════════════════════════════════════════════════

# 默认物种集
OXIDIZERS  = {"O2", "HO", "HO2", "O", "H2O2", "O3"}
H_SPECIES  = {"H", "HCl", "H2"}


def make_radical_classifier(
    target_formula: str,
    all_target_formulas: Set[str],
    oxidizers: Set[str] = OXIDIZERS,
    h_species: Set[str] = H_SPECIES,
):
    """返回一个 classifier(ParsedReaction) -> (category, subcategory)."""

    def classify(rxn: ParsedReaction) -> Tuple[str, str]:
        rf = rxn.reactant_formulas
        pf = rxn.product_formulas
        others = [f for f in rf if f != target_formula]

        # 异构化
        if len(rf) == 1 and rf[0] == target_formula:
            if len(pf) == 1 and pf[0] == target_formula:
                return "异构化/振动", "单分子重排"
            if len(pf) == 1:
                return "异构化/振动", f"→ {pf[0]}"
            return "碎裂/开环", f"→ {' + '.join(pf)}"

        # 氧化
        ox = [f for f in others if f in oxidizers]
        if ox:
            return "氧化路径", f"+ {ox[0]} → {' + '.join(pf)}"

        # 自偶联
        if target_formula in others:
            return "自偶联", f"2×{target_formula} → {' + '.join(pf)}"

        # 交叉偶联
        cross = [f for f in others if f in all_target_formulas and f != target_formula]
        if cross:
            return "交叉偶联", f"+ {cross[0]} → {' + '.join(pf)}"

        # Cl
        if "Cl" in others:
            return "Cl偶联/取代", f"+ Cl → {' + '.join(pf)}"

        # 加氢
        h = [f for f in others if f in h_species]
        if h:
            return "加氢/夺氢", f"+ {h[0]} → {' + '.join(pf)}"

        # 有机物
        c = [f for f in others if 'C' in f]
        if c:
            return "与有机物反应", f"+ {c[0]} → {' + '.join(pf)}"

        if others:
            return "其他双分子", f"+ {others[0]} → {' + '.join(pf)}"

        return "其他", f"→ {' + '.join(pf)}"

    return classify


# ═══════════════════════════════════════════════════════════
#  6. 净通量计算 (Net Flux)
# ═══════════════════════════════════════════════════════════

def _reverse_canonical(canonical_rxn: str) -> str:
    """A + B -> C + D  ↔  C + D -> A + B  (按 sorted canonical 表示)."""
    parts = canonical_rxn.split(' -> ')
    if len(parts) != 2:
        return canonical_rxn
    r_sorted = ' + '.join(sorted(parts[1].split(' + ')))
    p_sorted = ' + '.join(sorted(parts[0].split(' + ')))
    return f"{r_sorted} -> {p_sorted}"


def compute_net_flux(merged_rows: List[dict]) -> List[dict]:
    """对已合并的反应列表计算净通量.

    对每对正逆反应 (A→B, B→A), 计算:
      net_count = forward_count − reverse_count

    已处理的逆反应不再重复出现.

    Returns:
        list of dicts (同 merge_reactions 格式, 额外含 'gross_forward',
        'gross_reverse', 'net_count' 字段), 按 |net_count| 降序排列.
    """
    # 建索引  canonical_rxn → row
    idx: Dict[str, dict] = {}
    for row in merged_rows:
        idx[row['canonical_rxn']] = row

    seen: Set[str] = set()
    result: List[dict] = []

    for row in merged_rows:
        key = row['canonical_rxn']
        if key in seen:
            continue
        seen.add(key)

        rev_key = _reverse_canonical(key)
        rev_row = idx.get(rev_key)

        fwd = row['count']
        rev = rev_row['count'] if rev_row else 0

        if rev_row:
            seen.add(rev_key)

        net = fwd - rev
        # 保留 net != 0 的条目; 如 net < 0 则翻转方向
        if net == 0:
            continue

        if net > 0:
            out = dict(row)
            out['gross_forward'] = fwd
            out['gross_reverse'] = rev
            out['net_count'] = net
        else:
            # 逆向才是净方向
            out = dict(rev_row) if rev_row else dict(row)
            out['gross_forward'] = rev
            out['gross_reverse'] = fwd
            out['net_count'] = -net

        result.append(out)

    result.sort(key=lambda x: x['net_count'], reverse=True)
    return result


# ═══════════════════════════════════════════════════════════
#  7. species 文件解析 & 帧定位
# ═══════════════════════════════════════════════════════════

def parse_species_file(
    species_path: str,
    target_formulas: Set[str],
    *,
    verbose: bool = True,
) -> Dict[str, List[Tuple[int, int]]]:
    """解析 .species 文件, 提取目标分子式在每帧的计数.

    species 格式:  Timestep N: SMILES count SMILES count …

    Returns:
        {formula: [(timestep, count), …]}  按 timestep 排序
    """
    if verbose:
        print(f"[species] 解析 {os.path.basename(species_path)}, "
              f"追踪 {', '.join(sorted(target_formulas))} ...")

    # {formula: {timestep: total_count}}
    data: Dict[str, Dict[int, int]] = {f: defaultdict(int) for f in target_formulas}

    ts_re = re.compile(r'^Timestep\s+(\d+):(.*)$')

    with open(species_path) as fh:
        for line in fh:
            m = ts_re.match(line.strip())
            if not m:
                continue
            ts = int(m.group(1))
            remainder = m.group(2).strip()

            # 解析  SMILES count SMILES count …
            tokens = remainder.split()
            i = 0
            while i < len(tokens) - 1:
                smi = tokens[i]
                try:
                    cnt = int(tokens[i + 1])
                except ValueError:
                    i += 1
                    continue
                f = smiles_to_formula(smi)
                if f in data:
                    data[f][ts] += cnt
                i += 2

    # 转为排序列表
    result: Dict[str, List[Tuple[int, int]]] = {}
    for f in target_formulas:
        result[f] = sorted(data[f].items())

    if verbose:
        for f in sorted(target_formulas):
            frames = result[f]
            nonzero = [c for _, c in frames if c > 0]
            print(f"    {f}: {len(frames)} 帧, "
                  f"非零帧 {len(nonzero)}, "
                  f"最大计数 {max(nonzero) if nonzero else 0}")

    return result


def locate_reaction_frames(
    species_timeseries: Dict[str, List[Tuple[int, int]]],
    target_formula: str,
) -> Dict[str, List[int]]:
    """基于相邻帧物种计数变化, 定位消耗/生成帧.

    Returns:
        {
          'consumption_frames': [ts, …],  # count 减少的帧
          'production_frames':  [ts, …],  # count 增加的帧
          'first_appear':  ts or None,
          'last_appear':   ts or None,
          'peak_frame':    ts,
          'peak_count':    int,
        }
    """
    ts_data = species_timeseries.get(target_formula, [])
    if not ts_data:
        return {
            'consumption_frames': [], 'production_frames': [],
            'first_appear': None, 'last_appear': None,
            'peak_frame': None, 'peak_count': 0,
        }

    consumption = []
    production = []
    first = None
    last = None
    peak_ts, peak_cnt = ts_data[0]

    for i, (ts, cnt) in enumerate(ts_data):
        if cnt > 0:
            if first is None:
                first = ts
            last = ts
        if cnt > peak_cnt:
            peak_ts, peak_cnt = ts, cnt
        if i > 0:
            prev_cnt = ts_data[i - 1][1]
            delta = cnt - prev_cnt
            if delta < 0:
                consumption.append(ts)
            elif delta > 0:
                production.append(ts)

    return {
        'consumption_frames': consumption,
        'production_frames': production,
        'first_appear': first,
        'last_appear': last,
        'peak_frame': peak_ts,
        'peak_count': peak_cnt,
    }


def save_species_timeseries_csv(
    species_timeseries: Dict[str, List[Tuple[int, int]]],
    csv_path: str,
):
    """保存物种-时间序列 CSV (可用于绘图)."""
    formulas = sorted(species_timeseries.keys())
    if not formulas:
        return

    # 合并所有 timestep
    all_ts = sorted({ts for f in formulas for ts, _ in species_timeseries[f]})
    ts_lookup = {f: dict(species_timeseries[f]) for f in formulas}

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, 'w', newline='') as fh:
        w = csv.writer(fh)
        w.writerow(['timestep'] + formulas)
        for ts in all_ts:
            w.writerow([ts] + [ts_lookup[f].get(ts, 0) for f in formulas])

    print(f"  ✓ 物种时间序列 CSV 已保存: {csv_path}")


def make_timestamped_outdir(base_outdir: str, *, create_latest_symlink: bool = True) -> str:
    """在 `base_outdir` 下创建 timestamped 子目录并返回完整路径。

    - 如果最后一级已经是 timestamp (YYYYmmdd_HHMMSS) 则直接返回该路径。
    - 会尝试创建/更新 `base_outdir/latest` 指向最新子目录（如果允许）。
    """
    base = os.path.abspath(base_outdir)
    os.makedirs(base, exist_ok=True)

    # 如果 base 本身已经是 timestamped，则直接返回
    last = os.path.basename(base)
    if re.match(r"^\d{8}_\d{6}$", last):
        return base

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    newdir = os.path.join(base, ts)
    os.makedirs(newdir, exist_ok=True)

    if create_latest_symlink:
        latest = os.path.join(base, "latest")
        try:
            if os.path.islink(latest) or os.path.exists(latest):
                os.remove(latest)
            os.symlink(newdir, latest)
        except Exception:
            # 不在意无法创建符号链接的环境
            pass

    return newdir


# ═══════════════════════════════════════════════════════════
#  8. 输出工具
# ═══════════════════════════════════════════════════════════

def save_pathway_csv(
    rows: List[dict],
    csv_path: str,
    total_events: int,
    *,
    with_category: bool = False,
    net_flux: bool = False,
):
    """保存分支比 CSV."""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    count_key = 'net_count' if net_flux else 'count'
    fields = ['rank', 'count', 'ratio_pct', 'cumulative_pct']
    if net_flux:
        fields += ['gross_forward', 'gross_reverse']
    if with_category:
        fields += ['category', 'subcategory']
    fields += ['formula_reaction', 'canonical_reaction']

    cum = 0.0
    with open(csv_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i, row in enumerate(rows, 1):
            cnt = row.get(count_key, row.get('count', 0))
            pct = cnt / total_events * 100 if total_events else 0
            cum += pct
            d = {
                'rank': i,
                'count': cnt,
                'ratio_pct': round(pct, 3),
                'cumulative_pct': round(cum, 2),
                'formula_reaction': row['formula_rxn'],
                'canonical_reaction': row['canonical_rxn'],
            }
            if net_flux:
                d['gross_forward'] = row.get('gross_forward', cnt)
                d['gross_reverse'] = row.get('gross_reverse', 0)
            if with_category:
                d['category'] = row.get('category', '')
                d['subcategory'] = row.get('subcategory', '')
            w.writerow(d)

    print(f"  ✓ CSV 已保存: {csv_path}")


def save_smiles_csv(
    variant_counts: Dict[str, int],
    canonical_map: Dict[str, Set[str]],
    csv_path: str,
):
    """保存 SMILES 映射 CSV."""
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['canonical_smiles', 'original_smiles', 'count'])
        for can, variants in sorted(
                canonical_map.items(),
                key=lambda x: sum(variant_counts.get(v, 0) for v in x[1]),
                reverse=True):
            for v in sorted(variants, key=lambda x: variant_counts.get(x, 0), reverse=True):
                w.writerow([can, v, variant_counts[v]])
    print(f"  ✓ SMILES CSV 已保存: {csv_path}")


def print_ranked_table(
    rows: List[dict],
    total_events: int,
    *,
    top_n: int = 30,
    show_category: bool = False,
    show_smiles: bool = False,
    net_flux: bool = False,
):
    """终端打印排名表."""
    count_key = 'net_count' if net_flux else 'count'
    flux_hdr = "  {'净通量':>8}  {'正向':>8}  {'逆向':>8}" if net_flux else ""

    if show_category:
        hdr = f"  {'排名':>4}  {'次数':>8}"
        if net_flux:
            hdr += f"  {'正向':>8}  {'逆向':>8}"
        hdr += f"  {'分支比':>7}  {'累计':>7}  {'类别':<14}  反应(公式)"
    else:
        hdr = f"  {'排名':>4}  {'次数':>8}"
        if net_flux:
            hdr += f"  {'正向':>8}  {'逆向':>8}"
        hdr += f"  {'分支比':>7}  {'累计':>7}  反应(公式)"
    print(hdr)
    sep_len = 50 + (20 if net_flux else 0)
    print(f"  {'─'*4}  {'─'*8}" + (f"  {'─'*8}  {'─'*8}" if net_flux else "")
          + f"  {'─'*7}  {'─'*7}  {'─'*sep_len}")

    cum = 0.0
    for i, row in enumerate(rows[:top_n], 1):
        cnt = row.get(count_key, row.get('count', 0))
        pct = cnt / total_events * 100 if total_events else 0
        cum += pct

        flux_cols = ""
        if net_flux:
            flux_cols = (f"  {row.get('gross_forward', cnt):>8}"
                         f"  {row.get('gross_reverse', 0):>8}")

        if show_category:
            print(f"  {i:>4}  {cnt:>8}{flux_cols}  {pct:>6.2f}%  {cum:>6.1f}%  "
                  f"{row.get('category',''):<14}  {row['formula_rxn']}")
        else:
            print(f"  {i:>4}  {cnt:>8}{flux_cols}  {pct:>6.2f}%  {cum:>6.1f}%  "
                  f"{row['formula_rxn']}")
        if show_smiles:
            print(f"  {'':>40}  SMILES: {row['canonical_rxn']}")

    remaining = len(rows) - top_n
    if remaining > 0:
        print(f"  ... 还有 {remaining} 条 (占比 {100-cum:.1f}%)")


# ═══════════════════════════════════════════════════════════
#  9. 速率常数计算
# ═══════════════════════════════════════════════════════════

AVOGADRO = 6.022140857e23  # mol⁻¹


def _extract_reactant_formulas(formula_rxn: str) -> List[str]:
    """从反应字符串提取反应物分子式列表.

    例: 'Cl + C6H5ClO -> C6H4ClO + HCl' → ['Cl', 'C6H5ClO']
    """
    if '->' not in formula_rxn:
        return []
    return [f.strip() for f in formula_rxn.split('->')[0].split('+')]


def compute_rate_constants(
    rows: List[dict],
    species_ts: Dict[str, List[Tuple[int, int]]],
    *,
    volume_A3: float,
    timestep_ps: float = 0.0001,
    dump_interval: int = 100,
    temperature_K: Optional[float] = None,
    net_flux: bool = False,
) -> Tuple[List[dict], dict]:
    """对每条反应计算速率常数 k.

    物理公式:
      双分子 A + B → products:
        k [L/(mol·s)] = N_rxn × Nₐ × V / (t_total × <N_A> × <N_B>)
      单分子 A → products:
        k [1/s] = N_rxn / (t_total × <N_A>)

    其中 <N_X> 为该物种在模拟时间内的帧平均分子数 (无量纲计数)。

    Args:
        rows:          已合并的反应列表 (merge_reactions 或 compute_net_flux 输出)
        species_ts:    {formula: [(timestep, count), ...]}
                       **需包含所有反应物分子式的时间序列**
        volume_A3:     模拟盒子体积, Å³
        timestep_ps:   LAMMPS dt, 单位 ps (metal units 典型值 0.0001)
        dump_interval: ReacNetGenerator 每隔多少 LAMMPS 步写一帧, 默认 100
        temperature_K: 模拟温度, K (仅用于记录和显示)
        net_flux:      是否使用 net_count 字段

    Returns:
        (rows_with_k, sim_info)
        rows_with_k : 原 rows 各行附加 'k', 'k_unit', 'k_order',
                      'avg_N_A', 'avg_N_B', 'reactant_A', 'reactant_B' 字段
        sim_info    : 模拟条件汇总 dict
    """
    # ── 单位换算 ─────────────────────────────────────────
    V_L = volume_A3 * 1e-27          # Å³ → L  (1 Å³ = 1e-30 m³ = 1e-27 L)
    count_key = 'net_count' if net_flux else 'count'

    # ── 从 species_ts 推断模拟总时间 ──────────────────────
    all_ts = sorted({ts for f_data in species_ts.values() for ts, _ in f_data})
    if len(all_ts) >= 2:
        t_total_ps = (all_ts[-1] - all_ts[0]) * timestep_ps
    elif len(all_ts) == 1:
        t_total_ps = all_ts[0] * timestep_ps
    else:
        t_total_ps = 0.0
    t_total_s = t_total_ps * 1e-12   # ps → s

    # ── 计算各物种帧平均计数 ──────────────────────────────
    avg_counts: Dict[str, float] = {}
    for formula, ts_data in species_ts.items():
        if ts_data:
            avg_counts[formula] = sum(cnt for _, cnt in ts_data) / len(ts_data)
        else:
            avg_counts[formula] = 0.0

    sim_info = {
        't_total_ps':     round(t_total_ps, 3),
        't_total_s':      t_total_s,
        'V_A3':           volume_A3,
        'V_L':            V_L,
        'timestep_ps':    timestep_ps,
        'dump_interval':  dump_interval,
        'temperature_K':  temperature_K,
        'n_frames':       len(all_ts),
    }

    # ── 逐条计算 k ────────────────────────────────────────
    rows_with_k: List[dict] = []
    for row in rows:
        out = dict(row)
        N_rxn = row.get(count_key, row.get('count', 0))
        reactants = _extract_reactant_formulas(row.get('formula_rxn', ''))

        if t_total_s <= 0:
            out.update({'k': None, 'k_unit': 'N/A', 'k_order': len(reactants)})

        elif len(reactants) == 2:
            fA, fB = reactants
            avgA = avg_counts.get(fA, 0.0)
            avgB = avg_counts.get(fB, 0.0)
            out.update({
                'k_order':    2,
                'reactant_A': fA,
                'reactant_B': fB,
                'avg_N_A':    round(avgA, 3),
                'avg_N_B':    round(avgB, 3),
                'k_unit':     'L/(mol·s)',
            })
            if avgA > 0 and avgB > 0:
                out['k'] = N_rxn * AVOGADRO * V_L / (t_total_s * avgA * avgB)
            else:
                out['k'] = None
                out['k_missing'] = [f for f, a in [(fA, avgA), (fB, avgB)] if a == 0]

        elif len(reactants) == 1:
            fA = reactants[0]
            avgA = avg_counts.get(fA, 0.0)
            out.update({
                'k_order':    1,
                'reactant_A': fA,
                'avg_N_A':    round(avgA, 3),
                'k_unit':     '1/s',
            })
            if avgA > 0:
                out['k'] = N_rxn / (t_total_s * avgA)
            else:
                out['k'] = None
                out['k_missing'] = [fA]

        else:
            out.update({'k': None, 'k_unit': 'N/A', 'k_order': len(reactants)})

        rows_with_k.append(out)

    return rows_with_k, sim_info


def collect_all_reactant_formulas(rows: List[dict]) -> Set[str]:
    """从反应列表中收集所有反应物分子式 (用于扩展 species 追踪)."""
    formulas: Set[str] = set()
    for row in rows:
        formulas.update(_extract_reactant_formulas(row.get('formula_rxn', '')))
    return formulas


def save_rate_constants_csv(
    rows_with_k: List[dict],
    csv_path: str,
    total_events: int,
    *,
    sim_info: Optional[dict] = None,
    net_flux: bool = False,
) -> None:
    """保存含速率常数的 CSV.

    Columns:
        rank, count, ratio_pct, k, k_unit, k_order,
        avg_N_A, reactant_A, avg_N_B, reactant_B,
        category, formula_reaction, canonical_reaction
    """
    os.makedirs(os.path.dirname(csv_path) or '.', exist_ok=True)
    count_key = 'net_count' if net_flux else 'count'
    fieldnames = [
        'rank', 'count', 'ratio_pct',
        'gross_forward', 'gross_reverse',
        'k', 'k_unit', 'k_order',
        'avg_N_A', 'reactant_A',
        'avg_N_B', 'reactant_B',
        'category', 'formula_reaction', 'canonical_reaction',
    ]

    cum = 0.0
    with open(csv_path, 'w', newline='', encoding='utf-8') as fh:
        # 元数据注释行
        if sim_info:
            fh.write(f"# t_sim = {sim_info.get('t_total_ps', 'N/A')} ps"
                     f"  V = {sim_info.get('V_A3', 'N/A')} Å³"
                     f"  T = {sim_info.get('temperature_K', 'N/A')} K\n")
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction='ignore')
        w.writeheader()
        for i, row in enumerate(rows_with_k, 1):
            cnt = row.get(count_key, row.get('count', 0))
            pct = cnt / total_events * 100 if total_events else 0
            cum += pct
            k_val = row.get('k')
            w.writerow({
                'rank':               i,
                'count':              cnt,
                'ratio_pct':          round(pct, 3),
                'gross_forward':      row.get('gross_forward', cnt),
                'gross_reverse':      row.get('gross_reverse', 0),
                'k':                  f"{k_val:.6e}" if k_val is not None else 'N/A',
                'k_unit':             row.get('k_unit', ''),
                'k_order':            row.get('k_order', ''),
                'avg_N_A':            row.get('avg_N_A', ''),
                'reactant_A':         row.get('reactant_A', ''),
                'avg_N_B':            row.get('avg_N_B', ''),
                'reactant_B':         row.get('reactant_B', ''),
                'category':           row.get('category', ''),
                'formula_reaction':   row.get('formula_rxn', ''),
                'canonical_reaction': row.get('canonical_rxn', ''),
            })

    print(f"  ✓ 速率常数 CSV 已保存: {csv_path}")
