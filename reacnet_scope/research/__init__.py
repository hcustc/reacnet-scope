"""Optional research capabilities kept outside the ReacNet Scope core.

Species Fate Analysis (:mod:`reacnet_scope.research.species_fate`) and the
Apparent Rate Constant Estimate (:mod:`reacnet_scope.research.kinetics`) are
frozen for expansion and live here so that core modules, the ordinary Dash
mainline and the default CLI never import them at load time (ADR-0034).
Their third-party dependency (SciPy) is a core dependency, so no extra is
needed; the import boundary keeps load time and the core import graph small.

Submodules are imported explicitly; this package does not import them.
"""

RESEARCH_DEPENDENCY_HINT = (
    "该功能依赖 SciPy（核心依赖）；请检查 scipy 是否完整安装"
    "（源码环境：uv sync --locked）。"
)
