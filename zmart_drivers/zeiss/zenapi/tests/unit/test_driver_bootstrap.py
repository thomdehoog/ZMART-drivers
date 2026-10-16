"""The package imports cleanly with only its own roots on sys.path.

Guards against accidental dependence on being launched from a particular
directory, and confirms the package imports by its full name,
``zmart_drivers.zeiss.zenapi``, without the zen_api wheel
(everything vendor-specific is lazily imported).
"""

import subprocess
import sys
from pathlib import Path


def test_imports_with_minimal_syspath():
    here = Path(__file__).resolve()
    # parents: [0]=unit [1]=tests [2]=zenapi [3]=zeiss [4]=zmart_drivers [5]=repo root
    repo_root = here.parents[5]
    code = (
        "import sys;"
        f"sys.path.insert(0, r'{repo_root}');"
        "import zmart_drivers.zeiss.zenapi as zenapi;"
        "assert hasattr(zenapi, 'connect');"
        "assert hasattr(zenapi, 'move_xy');"
        "assert hasattr(zenapi, 'acquire');"
        "print('import-ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "import-ok" in result.stdout
