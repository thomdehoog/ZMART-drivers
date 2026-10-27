"""The driver is a block of its own: it installs and loads without the ZMART Controller.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]

CHILD_SCRIPT = """
import sys
sys.modules["zmart_controller"] = None  # any import of the controller fails, as on a computer without it
sys.path.insert(0, r"{repo}")
import zmart_drivers.nikon.nis_elements_ar610 as nis_elements
from zmart_drivers.nikon.nis_elements_ar610.methods import Microscope
from zmart_drivers.nikon.nis_elements_ar610.procedures import changes
assert nis_elements.connect is not None and Microscope is not None and changes.take_picture is not None
print("loaded-without-the-controller")
"""


def test_the_controller_is_an_extra_not_a_requirement():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert not [
        need for need in project["dependencies"] if need.startswith("zmart-controller")
    ]
    assert [
        need
        for need in project["optional-dependencies"]["controller"]
        if need.startswith("zmart-controller")
    ]
    assert any(
        need.startswith("nis-bridge")
        for need in project["optional-dependencies"]["nikon-ar610"]
    )


def test_the_driver_loads_without_the_controller(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD_SCRIPT.format(repo=str(REPO)))
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "loaded-without-the-controller" in result.stdout
