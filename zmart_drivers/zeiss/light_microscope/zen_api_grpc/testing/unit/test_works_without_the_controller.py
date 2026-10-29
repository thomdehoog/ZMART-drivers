"""The driver installs and loads on its own: without the ZMART Controller, and without ZEISS's zen_api package.

These run in a subprocess, because the test session already has the
repository on its path and would hide a missing import.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[6]

CHILD = """
import sys
sys.modules["zmart_controller"] = None   # any import of the controller fails
sys.modules["zen_api"] = None            # and so does ZEISS's package
sys.modules["grpclib"] = None
sys.path.insert(0, r"{repo}")
import zmart_drivers.zeiss.light_microscope.zen_api_grpc as zen
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.procedures import changes
assert zen.connect is not None and changes.take_picture is not None
print("loaded-alone")
"""


def test_the_controller_is_an_extra_and_the_zeiss_packages_are_the_zeiss_extra():
    project = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]

    assert not [
        need
        for need in project["dependencies"]
        if need.startswith(("zmart-controller", "zen_api", "grpclib"))
    ]
    zeiss = project["optional-dependencies"]["zeiss"]
    assert any(need.startswith("zen_api @") and "2025.10.1" in need for need in zeiss)
    assert any(need.startswith("grpclib") for need in zeiss)


def test_the_driver_loads_without_the_controller_and_without_zen_api(tmp_path):
    script = tmp_path / "child.py"
    script.write_text(CHILD.format(repo=str(REPO)), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=os.environ.copy(),
    )

    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "loaded-alone" in result.stdout
