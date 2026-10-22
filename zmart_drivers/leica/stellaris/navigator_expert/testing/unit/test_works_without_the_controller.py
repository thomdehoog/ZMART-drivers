"""The driver is a block of its own: it installs and loads without the ZMART Controller.

The controller is an extra (``zmart-drivers[leica,controller]``): a workflow
that drives the microscope through the controller adds it, one that uses the
driver directly does not need it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[6]

CHILD_SCRIPT = """
import sys
# Any import of the controller fails, as on a computer without it.
sys.modules["zmart_controller"] = None
sys.path.insert(0, r"{repo}")
import zmart_drivers.leica.stellaris.navigator_expert as navigator_expert
from zmart_drivers.leica.stellaris.navigator_expert.driver_api.microscope import Microscope
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import acquire
assert navigator_expert.connect is not None and Microscope is not None and acquire.take_picture is not None
print("loaded-without-the-controller")
"""


def test_the_controller_is_an_extra_not_a_requirement():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert not [need for need in project["dependencies"] if need.startswith("zmart-controller")]
    assert [need for need in project["optional-dependencies"]["controller"] if need.startswith("zmart-controller")]


def test_the_driver_loads_without_the_controller(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD_SCRIPT.format(repo=str(REPO)))

    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=60, env=os.environ.copy()
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "loaded-without-the-controller" in result.stdout
