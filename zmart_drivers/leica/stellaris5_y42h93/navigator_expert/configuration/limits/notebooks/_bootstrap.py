"""Import bootstrap only. Must never choose runtime write paths."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[7]

# This notebook folder, so a notebook can archive its own executed copy into the
# machine snapshot (a read path, not a runtime write path).
NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "set_limits.ipynb"

# The repository root, so the driver imports by its full name even when the
# repository has not been installed with pip.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.configuration.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "corners.record(1)",
        "corners.record(2)",
        "corners.record(3)",
        "corners.record(4)",
        "captured_xy = corners.limits()",
        "validated_limits = validate_limits(LIMITS)",
    ),
)
