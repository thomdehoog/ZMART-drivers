"""Put the repository on the import path, so the driver imports by its full name uninstalled."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[7]

NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "set_limits.ipynb"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.evident.fv4000.rdk.driver_setup.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "travel.record_corner(1)",
        "travel.record_corner(2)",
        "travel.record_corner(3)",
        "travel.record_corner(4)",
        'travel.record_z("low")',
        'travel.record_z("high")',
        "measured = travel.limits()",
        "validated_limits = validate_limits(LIMITS)",
    ),
)
