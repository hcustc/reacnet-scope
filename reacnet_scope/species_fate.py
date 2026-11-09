"""Deprecated alias of :mod:`reacnet_scope.research.species_fate` (ADR-0034).

Removed in 0.2.0. Import ``reacnet_scope.research.species_fate`` instead.
"""

import sys
import warnings

warnings.warn(
    "reacnet_scope.species_fate 已移至 reacnet_scope.research.species_fate，"
    "旧路径将在 0.2.0 移除（见 ADR-0034）。",
    DeprecationWarning,
    stacklevel=2,
)

from .research import species_fate as _module  # noqa: E402

sys.modules[__name__] = _module
