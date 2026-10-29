"""Stand-ins for the microscope, for testing the setup wizard without LAS X.

Each function puts one stand-in in place through a pytest ``MonkeyPatch``, so it
is undone when the test (or a ``MonkeyPatch.context()`` block) ends. They reuse
the stand-ins the driver's own tests already trust, rather than inventing new
ones:

- ``install_lasx``: the stand-in for LAS X's plain Navigator
  (``lasx_stand_in.py``), answering every connection the driver's own
  ``connect()`` makes. For the data location, the travel limits and the
  zero point.
- ``install_orientation``: the orientation tests' pictures that shift as the
  stage moves (``unit/test_orientation_measure.py``).
- ``install_calibration``: the calibration tests' physical simulator, two
  objectives with known offsets and focus blur
  (``unit/test_calibration_on_a_simulated_scope.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import hermetic_mock_machine_root

from zmart_drivers.leica.stellaris.navigator.driver_setup import store

TESTING = Path(__file__).resolve().parents[1]


def load_test_module(relative: str, name: str):
    """Import another test file by its path, so its stand-ins are shared rather than copied."""
    spec = importlib.util.spec_from_file_location(name, TESTING / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_lasx(mp) -> PlainNavigator:
    """A fresh machine folder, and every LAS X connection answered by the plain Navigator stand-in."""
    mp.setenv("ZMART_MICROSCOPY_ROOT", os.environ.get("ZMART_MICROSCOPY_ROOT", ""))
    mp.setenv("APPDATA", os.environ.get("APPDATA", ""))
    hermetic_mock_machine_root()
    # The machine profile reads its folder from the environment when it is
    # made, so it is made again for the new folder.
    mp.setattr(store, "MACHINE", store.MachineProfile())
    return attach(mp, PlainNavigator())


def drive_stage_to(lasx: PlainNavigator, x_um: float, y_um: float) -> None:
    """What the operator does with the joystick: the stand-in's stage now stands here."""
    lasx.x_um, lasx.y_um = x_um, y_um


def install_orientation(mp, programdata: Path, *, trusted: bool = True):
    """Pictures that shift with the stage, and the votes of a field with (or without) structure."""
    om = load_test_module("unit/test_orientation_measure.py", "_wizard_orientation_standin")
    machine = store.MachineProfile(programdata_root=programdata)
    mp.setattr(store, "MACHINE", machine)
    om._patch(mp)
    votes = [om._trusted(0.0, 40.0), om._trusted(-40.0, 0.0)] if trusted else [om._untrusted()] * 2
    om._install_votes(mp, votes * 8)

    # The real driver writes the check picture as OME-Zarr, and saving the
    # orientation looks for it; the stand-in's saver writes a TIFF, so the
    # store is also made under the name the driver gives it.
    from zmart_drivers.leica.stellaris.navigator.driver_setup.orientation import (
        measure_orientation as mo,
    )

    real = mo.acquire_validation_image

    def acquire_and_mark(session):
        image, path = real(session)
        (session.paths.session_dir / "validation" / "check.ome.zarr").mkdir(exist_ok=True)
        return image, path

    mp.setattr(mo, "acquire_validation_image", acquire_and_mark)
    return machine


def install_calibration(mp, programdata: Path):
    """The physical simulator: answers (module with TRUE_T, simulator, machine profile)."""
    module = load_test_module("unit/test_calibration_on_a_simulated_scope.py", "_wizard_simulated_scope")
    machine = store.MachineProfile(programdata_root=programdata)
    sim = module._install_simulator(mp, machine)
    return module, sim, machine
