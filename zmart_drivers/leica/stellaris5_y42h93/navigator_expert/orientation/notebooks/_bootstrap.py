"""Import bootstrap and shared notebook checkpoint configuration."""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[6]

# This notebook folder, so a notebook can archive its own executed copy into the
# machine snapshot (a read path, not a runtime write path).
NOTEBOOKS_DIR = Path(__file__).resolve().parent
NOTEBOOK_PATH = NOTEBOOKS_DIR / "set_orientation.ipynb"

# The repository root, so the driver imports by its full name even when the
# repository has not been installed with pip.
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.notebook_support import (
    NotebookCheckpoint,  # noqa: E402
)

NOTEBOOK = NotebookCheckpoint(
    NOTEBOOK_PATH,
    required_code=(
        "session = wf.run_notebook_measurement()",
        "validation_path = wf.validate(session)",
    ),
)
