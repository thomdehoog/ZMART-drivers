"""Stand-ins for the microscope, for testing the setup wizard without LAS X.

Each function puts one stand-in in place through a pytest ``MonkeyPatch``, so it
is undone when the test (or a ``MonkeyPatch.context()`` block) ends. They reuse
the stand-ins the driver's own tests already trust, rather than inventing new
ones:

- ``install_lasx``: the offline LAS X client (``mock_lasx_api.py``), answering
  every connection the driver's own ``connect()`` makes. For the data
  location, the travel limits and the zero point.
- ``install_orientation``: the orientation tests' pictures that shift as the
  stage moves (``unit/test_orientation_measure.py``).
- ``install_calibration``: the calibration tests' physical simulator, two
  objectives with known offsets and focus blur
  (``calibration/integration/test_notebook_on_simulated_scope.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import replace
from pathlib import Path

from limits_fixtures import hermetic_mock_machine_root
from mock_lasx_api import MockLasxClient

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import (
    client as lasx_client,
)
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import (
    profile as reading_settings,
)
from zmart_drivers.leica.stellaris.navigator_expert.primitives.log import (
    reads as log_settings,
)

TESTING = Path(__file__).resolve().parents[1]


def load_test_module(relative: str, name: str):
    """Import another test file by its path, so its stand-ins are shared rather than copied."""
    spec = importlib.util.spec_from_file_location(name, TESTING / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_lasx(mp) -> MockLasxClient:
    """A fresh machine folder, and every LAS X connection answered by one stand-in client."""
    mp.setenv("ZMART_MICROSCOPY_ROOT", os.environ.get("ZMART_MICROSCOPY_ROOT", ""))
    mp.setenv("APPDATA", os.environ.get("APPDATA", ""))
    mp.setattr(log_settings, "LOG_READER", log_settings.LOG_READER)
    mp.setattr(
        reading_settings,
        "STATE_READERS",
        replace(reading_settings.STATE_READERS, selected_job_confirm_fallback="api"),
    )
    hermetic_mock_machine_root()
    # The machine profile reads its folder from the environment when it is
    # made, so it is made again for the new folder.
    mp.setattr(store, "MACHINE", store.MachineProfile())
    client = MockLasxClient(latency=0.0)
    mp.setattr(lasx_client, "connect_python_client", lambda **_kw: client)
    return client


def drive_stage_to(client: MockLasxClient, x_um: float, y_um: float) -> None:
    """What the operator does with the joystick: the stand-in's stage now stands here."""
    client._stage_x = x_um / 1e6
    client._stage_y = y_um / 1e6
    client._sync_stage_to_jobs()


def install_orientation(mp, programdata: Path, *, trusted: bool = True):
    """Pictures that shift with the stage, and the votes of a field with (or without) structure."""
    om = load_test_module(
        "unit/test_orientation_measure.py", "_wizard_orientation_standin"
    )
    machine = store.MachineProfile(programdata_root=programdata)
    mp.setattr(store, "MACHINE", machine)
    om._patch(mp)
    votes = (
        [om._trusted(0.0, 40.0), om._trusted(-40.0, 0.0)]
        if trusted
        else [om._untrusted()] * 2
    )
    om._install_votes(mp, votes * 8)

    # The real driver writes the check picture as OME-Zarr, and saving the
    # orientation looks for it; the stand-in's saver writes a TIFF, so the
    # store is also made under the name the driver gives it.
    from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.orientation import (
        measure_orientation as mo,
    )

    real = mo.acquire_validation_image

    def acquire_and_mark(session):
        image, path = real(session)
        (session.paths.session_dir / "validation" / "check.ome.zarr").mkdir(
            exist_ok=True
        )
        return image, path

    mp.setattr(mo, "acquire_validation_image", acquire_and_mark)
    return machine


def install_calibration(mp, programdata: Path):
    """The physical simulator: answers (module with TRUE_T, simulator, machine profile)."""
    module = load_test_module(
        "calibration/integration/test_notebook_on_simulated_scope.py",
        "_wizard_simulated_scope",
    )
    machine = store.MachineProfile(programdata_root=programdata)
    sim = module._install_simulator(mp, machine)
    return module, sim, machine
