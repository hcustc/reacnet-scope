"""Optional research capabilities kept outside the ReacNet Scope core.

Species Fate Analysis (:mod:`reacnet_scope.research.species_fate`) and the
Apparent Rate Constant Estimate (:mod:`reacnet_scope.research.kinetics`) are
frozen for expansion and live here so that core modules, the ordinary Dash
mainline and the default CLI never import them at load time (ADR-0034).
Install the ``research`` extra for their third-party dependencies::

    pip install "reacnet-scope[research]"

Submodules are imported explicitly; this package does not import them.
"""

RESEARCH_EXTRA_HINT = (
    "该功能属于可选研究扩展；请安装 `reacnet-scope[research]`"
    "（源码环境：uv sync --extra research）。"
)
