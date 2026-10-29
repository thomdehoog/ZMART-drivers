"""Shared setup for the ZEISS driver's offline tests.

Every test gets an empty settings folder of its own, a clean limits gate and
short waiting windows, so no test reads or writes the real
``C:\\ProgramData\\zmart-microscopy`` and none waits seconds on a stand-in.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# The repository root, so the driver imports by its full name uninstalled.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))


@pytest.fixture(autouse=True)
def _hermetic_machine_root(tmp_path_factory, monkeypatch):
    """Point the settings folder at an empty one for every test, with no config in use."""
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)
    yield root


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the limits gate around every test: one test's limits must never govern another's client."""
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.dispatcher import (
        limits as gate,
    )

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def _fast_windows(monkeypatch):
    """Shrink the waiting rule's windows: against a stand-in they are only sleep."""
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway import (
        profile,
    )

    monkeypatch.setattr(profile, "WINDOW_S", 0.2)
    monkeypatch.setattr(profile, "POLL_S", 0.005)
    monkeypatch.setattr(profile, "ANSWER_POLL_S", 0.001)
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc.procedures.save_data import (
        save,
    )

    monkeypatch.setattr(save, "FILE_SETTLE_S", 0.01)
    monkeypatch.setattr(save, "FILE_WAIT_S", 0.3)


@pytest.fixture(autouse=True)
def _no_gateway(monkeypatch):
    """No test reaches a real gateway: opening one is refused unless a test hands in its own."""
    from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods

    def refused(connection):
        raise RuntimeError(
            "no ZEN API Gateway in the offline tests; hand in the stand-in"
        )

    monkeypatch.setattr(methods, "open_connection", refused)


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked ``controller`` when the ZMART Controller is not installed."""
    if importlib.util.find_spec("zmart_controller") is not None:
        return
    skip = pytest.mark.skip(reason="the ZMART Controller is not installed")
    for item in items:
        if item.get_closest_marker("controller"):
            item.add_marker(skip)
