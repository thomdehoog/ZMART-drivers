"""Shared pytest setup for the plain Navigator driver's offline suite.

Every test runs with a throwaway configuration folder, no hardware server
(tests that need LAS X hand in the stand-in, ``helpers/lasx_stand_in.py``),
no LAS X window, an empty limits gate and short waiting windows. The tests
marked ``controller`` check the driver inside the ZMART Controller and are
skipped where the controller is not installed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# The repository root, so the driver imports by its full name,
# zmart_drivers.leica.stellaris.navigator, even when the repository has not
# been installed with pip.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))

TEST_DATA = Path(__file__).resolve().parent / "data"


@pytest.fixture(autouse=True)
def _hermetic_machine_root(tmp_path_factory, monkeypatch):
    """Point the configuration folder at an empty temporary folder for every test.

    The global ``MACHINE`` otherwise reads this computer's real
    ``C:\\ProgramData\\zmart-microscopy`` tree, which would make tests depend
    on (and possibly write) a microscope's settings.
    """
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    # And no config in use: one chosen in another test would move this test's settings.
    from zmart_drivers.leica.stellaris.navigator.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)


def pytest_report_header(config):
    """Print the environment context at the top of every run; diagnostics never break a run."""
    try:
        from diagnostics import header_lines

        return header_lines()
    except Exception as exc:  # pragma: no cover - diagnostics must not fail a run
        return [f"navigator context: diagnostics unavailable ({exc!r})"]


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked ``controller`` when the ZMART Controller is not installed."""
    if importlib.util.find_spec("zmart_controller") is not None:
        return
    skip = pytest.mark.skip(reason="the ZMART Controller is not installed")
    for item in items:
        if item.get_closest_marker("controller"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _no_hardware_server(monkeypatch):
    """Make LAS X's hardware server unreachable for every test.

    The suite runs on microscope PCs with LAS X open, where the real
    hardware server would answer and a test would move the instrument.
    Tests that need the server hand in the stand-in (``lasx_stand_in.attach``).
    """
    from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
        client as hardware_server,
    )
    from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
        state_tree,
        transfer,
    )

    def unreachable():
        raise hardware_server.HardwareServerUnavailable("no hardware server in the test suite")

    monkeypatch.setattr(hardware_server, "attach", unreachable)
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    monkeypatch.setitem(transfer.senders, "current", None)
    yield
    hardware_server.forget_connection()
    state_tree.forget_nodes()


@pytest.fixture(autouse=True)
def _no_lasx_window(tmp_path, monkeypatch):
    """Keep every test away from LAS X's own log and windows.

    On a microscope PC LAS X is open: a test would read its real ``LAS X.log``
    and could press a real dialog's button. Tests that need either hand in a
    stand-in log path and windows.
    """
    from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
        client as hardware_server,
    )
    from zmart_drivers.leica.stellaris.navigator.primitives.direct import transfer
    from zmart_drivers.leica.stellaris.navigator.procedures import answer_dialogs

    def no_windows():
        raise answer_dialogs.WindowsUnavailable("no LAS X windows in the test suite")

    monkeypatch.setattr(hardware_server, "LASX_LOG", tmp_path / "no LAS X.log")
    monkeypatch.setattr(transfer, "LASX_LOG", tmp_path / "no LAS X.log")
    monkeypatch.setattr(answer_dialogs, "LASX_LOG", tmp_path / "no LAS X.log")
    monkeypatch.setattr(answer_dialogs, "lasx_windows", no_windows)
    answer_dialogs.forget()
    yield
    answer_dialogs.stop_watching()
    answer_dialogs.forget()


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the limits gate around every test: a state installed by one test must never govern another's client."""
    from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as gate

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def fast_timing_windows(monkeypatch):
    """Shrink the rule's real-time windows for the offline suite.

    The shipped values (``WINDOW_S`` 3 s per window, ``POLL_S`` between looks)
    are real hardware timing; against the stand-in they are pure sleep. The
    consumers read them at call time, so patching here reaches every wait.
    """
    from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
        profile as waiting,
    )

    monkeypatch.setattr(waiting, "WINDOW_S", 0.25)
    monkeypatch.setattr(waiting, "POLL_S", 0.005)
    monkeypatch.setattr(waiting, "ANSWER_POLL_S", 0.001)


@pytest.fixture(autouse=True)
def _no_collected_pictures():
    """Forget the cache pictures collected by earlier tests, so each test's cache is new."""
    from zmart_drivers.leica.stellaris.navigator.procedures.save_data import collect

    collect._collected.clear()
    yield
    collect._collected.clear()
