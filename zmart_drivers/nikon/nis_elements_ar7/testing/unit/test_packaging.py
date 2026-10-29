"""The driver as it is installed: it loads without the controller, and offers only connect() and Microscope.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[5]


def test_the_driver_loads_and_works_without_the_controller():
    """The ZMART Controller is hidden from a fresh Python, and the driver must still import."""
    code = (
        "import sys; sys.modules['zmart_controller'] = None;"
        "import zmart_drivers.nikon.nis_elements_ar7 as d;"
        "import zmart_drivers.nikon.nis_elements_ar7.zmart_controller_plugin.zmart_controller_plugin as p;"
        "print(d.__version__, sorted(d.__all__))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert "['Microscope', '__version__', 'connect']" in done.stdout


def test_the_notebooks_and_their_bootstraps_ship_with_the_driver():
    driver = REPO_ROOT / "zmart_drivers" / "nikon" / "nis_elements_ar7"
    notebooks = sorted(
        p.relative_to(driver).as_posix() for p in driver.rglob("*.ipynb")
    )
    assert notebooks == [
        "driver_setup/data_location/notebooks/set_data_location.ipynb",
        "driver_setup/limits/notebooks/set_limits.ipynb",
        "driver_setup/orientation/notebooks/set_orientation.ipynb",
    ]
    for notebook in notebooks:
        assert (driver / notebook).with_name("bootstrap.py").is_file()


def test_each_bootstrap_finds_the_repository():
    driver = REPO_ROOT / "zmart_drivers" / "nikon" / "nis_elements_ar7"
    for bootstrap in driver.rglob("notebooks/bootstrap.py"):
        assert "parents[6]" in bootstrap.read_text(encoding="utf-8"), bootstrap
        assert bootstrap.resolve().parents[6] == REPO_ROOT


def test_the_bridge_macro_is_written_by_nis_own_python_with_nothing_installed(tmp_path):
    """On the microscope PC only NIS's own Python may be there; writing start_bridge.mac must need nothing more."""
    code = (
        "import builtins, runpy, sys;"
        "real = builtins.__import__;"
        "blocked = ('numpy', 'tifffile', 'zarr', 'ome_zarr_models', 'pydantic_zarr', 'ome_types', 'zmart_controller');"
        "builtins.__import__ = lambda name, *a, **k: (_ for _ in ()).throw(ImportError(name)) if name.split('.')[0] in blocked else real(name, *a, **k);"
        f"sys.argv = ['nis_bridge', '--folder', {str(tmp_path)!r}];"
        "runpy.run_module('zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge', run_name='__main__')"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert (tmp_path / "start_bridge.mac").is_file()
