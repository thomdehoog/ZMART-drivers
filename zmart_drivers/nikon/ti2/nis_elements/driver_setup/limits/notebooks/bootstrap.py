"""Put the repository on the import path, and name the notebook's save checkpoint. Nothing else."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[7]

# This notebook folder, so the notebook can archive its own executed copy with the record.
NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "set_limits.ipynb"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.nikon.ti2.nis_elements.driver_setup.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "corners.record(1)",
        "corners.record(2)",
        "validated_limits = validate_limits(LIMITS)",
    ),
)
