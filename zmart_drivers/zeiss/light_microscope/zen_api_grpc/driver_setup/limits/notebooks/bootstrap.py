"""Put the repository on the import path, so the driver imports by its full name uninstalled."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[7]

# This notebook folder, so a notebook can archive its own executed copy with the snapshot.
NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "set_limits.ipynb"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "corners.record(1)",
        "corners.record(2)",
        "corners.record(3)",
        "corners.record(4)",
        "measured = corners.limits()",
        "validated_limits = validate_limits(LIMITS)",
    ),
)
