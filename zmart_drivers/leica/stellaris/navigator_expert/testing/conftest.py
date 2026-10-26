"""Shared pytest setup and immutable test-data accessors."""

import importlib.util
import sys
from pathlib import Path

import pytest

# Add the repository root to sys.path so the driver imports by its full name,
# zmart_drivers.leica.stellaris.navigator_expert, even when the
# repository has not been installed with pip.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))

TEST_DATA = Path(__file__).resolve().parent / "data"

import matplotlib  # noqa: E402

# Headless before any test imports matplotlib.pyplot -- calibration's overlay
# plots (calibration/core/common.py:plot_overlay) otherwise pull in whatever
# GUI backend matplotlib auto-selects (Tk here), which needs a working Tk/Tcl
# install the test env doesn't carry. Must run before the first pyplot
# import anywhere in the process, so this is the earliest conftest in the tree.
matplotlib.use("Agg")


@pytest.fixture(autouse=True)
def _hermetic_machine_root(tmp_path_factory, monkeypatch):
    """Point the machine config root at an empty tmp dir for every test.

    The global ``config.machine.MACHINE`` otherwise resolves calibration/limits
    from the real ``C:\\ProgramData\\zmart-microscopy`` tree, which would make
    tests depend on (and potentially write) machine-local state. With an empty
    root, default resolution seeds a deterministic local ProgramData snapshot.
    Tests that need custom snapshots create their own
    ``MachineProfile(programdata_root=...)`` or populate this root.
    """
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    # And no config in use: one chosen in another test would move this test's settings.
    from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)


def pytest_report_header(config):
    """Print the full environment context at the top of every run.

    This header travels with every captured log, so a failure reported from a
    CI runner or another institute's microscope PC carries the exact system
    context (OS, Python, package versions, git rev, LAS X availability) needed
    to triage it. Diagnostics must never break a run, hence the guard.
    See testing/diagnostics.py.
    """
    try:
        from diagnostics import header_lines

        return header_lines()
    except Exception as exc:  # pragma: no cover - diagnostics must not fail a run
        return [f"navigator_expert context: diagnostics unavailable ({exc!r})"]


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked ``controller`` when the ZMART Controller is not installed.

    The driver is also used by itself, without the controller, so its tests
    must pass on such a computer. The tests that check the driver inside the
    controller are then skipped, with the reason, rather than failing.
    run_ci.py runs them as a layer of their own, and ``--controller`` there
    makes a missing controller a failure.
    """
    if importlib.util.find_spec("zmart_controller") is not None:
        return
    skip = pytest.mark.skip(reason="the ZMART Controller is not installed")
    for item in items:
        if item.get_closest_marker("controller"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _writes_through_the_cam_api(monkeypatch):
    """Send every change through the CAM API unless a test asks for the direct route.

    The shipped order is direct first, the CAM API behind it. Most tests are about
    what a change does, not how it travels, and some hand in a stand-in hardware
    server for the reads, which the direct route would then try to write to.
    The tests of the direct route set ``WRITE_ROUTES`` themselves.
    """
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as routes

    monkeypatch.setattr(routes, "WRITE_ROUTES", ("cam",))


@pytest.fixture(autouse=True)
def _no_hardware_server(monkeypatch):
    """Make LAS X's hardware server unreachable for every test.

    The suite runs on microscope PCs with LAS X open, where LAS X's real
    hardware server would answer and a test would read the instrument's z.
    Tests that need the server hand in a stand-in through ``attach``.
    """
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import client as hardware_server

    def unreachable():
        raise hardware_server.HardwareServerUnavailable(
            "no hardware server in the test suite"
        )

    monkeypatch.setattr(hardware_server, "attach", unreachable)
    hardware_server.forget_connection()
    yield
    hardware_server.forget_connection()


@pytest.fixture(autouse=True)
def _no_lasx_window(tmp_path, monkeypatch):
    """Keep every test away from LAS X's own log, windows, Navigator and saved experiments.

    On a microscope PC LAS X is open: a test would read its real ``LAS X.log``
    and could press a real dialog's button. Tests that need either hand in a
    stand-in log path and windows.
    """
    from zmart_drivers.leica.stellaris.navigator_expert.procedures import answer_dialogs

    def no_windows():
        raise answer_dialogs.WindowsUnavailable("no LAS X windows in the test suite")

    from zmart_drivers.leica.stellaris.navigator_expert.procedures.clear_cache import navigator

    def no_automation():
        raise answer_dialogs.WindowsUnavailable(
            "no Windows UI Automation in the test suite"
        )

    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import reads as api_reader

    monkeypatch.setattr(answer_dialogs, "LASX_LOG", tmp_path / "no LAS X.log")
    monkeypatch.setattr(api_reader, "SAVED_EXPERIMENT_FOLDER", str(tmp_path / "ScanningTemplates"))
    monkeypatch.setattr(answer_dialogs, "lasx_windows", no_windows)
    monkeypatch.setattr(navigator, "automation", no_automation)
    navigator.forget()
    answer_dialogs.forget()
    yield
    answer_dialogs.stop_watching()
    answer_dialogs.forget()


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the commands-layer limits-gate registry around every test.

    The gate registry is process-global (keyed by client identity); a state
    installed by one test must never govern another test's client, and the
    adversarial suite depends on starting from the fail-closed empty state.
    """
    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as gate

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def fast_timing_windows(monkeypatch):
    """Shrink the rule's real-time windows for the offline suite.

    The shipped values (``waiting.WINDOW_S = 3`` s per window, ``POLL_S`` and
    ``ANSWER_POLL_S`` between looks, ``transport.ECHO_SETTLE_TIMEOUT_S = 1`` s
    per fire) are real hardware timing; against mocks they are pure sleep.
    The consumers read them at call time, so patching here reaches every
    wait. Shipped values are unchanged; a test that needs a specific window
    patches it itself (``test_waiting_rule``).
    """
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import profile as waiting
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import transfer as transport

    monkeypatch.setattr(waiting, "WINDOW_S", 0.25)
    monkeypatch.setattr(waiting, "POLL_S", 0.005)
    monkeypatch.setattr(waiting, "ANSWER_POLL_S", 0.001)
    monkeypatch.setattr(transport, "ECHO_SETTLE_TIMEOUT_S", 0.05)


@pytest.fixture(autouse=True)
def fresh_live_log(monkeypatch):
    """Give every test its own shared live-log parse.

    The live LAS X log is parsed once per ``POLL_S`` and shared by every
    reader (``log_reader.LIVE_LOG``); without a fresh one per test, a test
    that points the reader at its own files could be handed the previous
    test's snapshot.
    """
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.log import reads as log_reader

    monkeypatch.setattr(log_reader, "LIVE_LOG", log_reader.SharedParse())
