"""Put the repository on the import path, so the driver imports by its full name uninstalled."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[7]
NOTEBOOK_PATH = Path(__file__).resolve().parent / "calibrate_objective_pair.ipynb"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.evident.fv4000.rdk.driver_setup.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "summary = objective_pair.measure_parcentricity_target_and_save(session)",
    ),
)
