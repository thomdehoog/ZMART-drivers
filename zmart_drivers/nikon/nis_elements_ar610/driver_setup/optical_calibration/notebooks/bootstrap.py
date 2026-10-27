"""Put the repository on the import path, and name the notebook's save checkpoint. Nothing else."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]

NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "calibrate_objective_pair.ipynb"

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.nikon.nis_elements_ar610.driver_setup.notebook_support import (
    NotebookCheckpoint,
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH, required_code=("pair.record()", "pair.translations()")
)
