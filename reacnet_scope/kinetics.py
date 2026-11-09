"""Deprecated alias of :mod:`reacnet_scope.research.kinetics` (ADR-0034).

Removed in 0.2.0. Import ``reacnet_scope.research.kinetics`` instead.
"""

import sys
import warnings

warnings.warn(
    "reacnet_scope.kinetics 已移至 reacnet_scope.research.kinetics，"
    "旧路径将在 0.2.0 移除（见 ADR-0034）。",
    DeprecationWarning,
    stacklevel=2,
)

from .research import kinetics as _module  # noqa: E402

sys.modules[__name__] = _module
