"""Shared pytest setup and immutable test-data accessors."""

import shutil
import sys
from pathlib import Path

import pytest

# Add the repository root to sys.path so the driver imports by its full name,
# zmart_drivers.leica.stellaris5_y42h93.navigator_expert, even when the
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
GENERAL_WORKFLOW_DATA = TEST_DATA / "general_workflow"
SCANFIELD_PARSING_DATA = TEST_DATA / "scanfield_parsing"


def pytest_report_header(config):
    """Print the full environment context at the top of every run.

    This header travels with every captured log, so a failure reported from a
    CI runner or another institute's microscope PC carries the exact system
    context (OS, Python, package versions, git rev, LAS X availability) needed
    to triage it. Diagnostics must never break a run, hence the guard.
    See testing/_diagnostics.py.
    """
    try:
        from _diagnostics import header_lines

        return header_lines()
    except Exception as exc:  # pragma: no cover - diagnostics must not fail a run
        return [f"navigator_expert context: diagnostics unavailable ({exc!r})"]


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the commands-layer limits-gate registry around every test.

    The gate registry is process-global (keyed by client identity); a state
    installed by one test must never govern another test's client, and the
    adversarial suite depends on starting from the fail-closed empty state.
    """
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import gate

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def fast_timing_windows(monkeypatch):
    """Shrink the rule's real-time windows for the offline suite.

    The shipped values (``tuning.WINDOW_S = 3`` s per window, ``POLL_S`` and
    ``ANSWER_POLL_S`` between looks, ``change.ECHO_SETTLE_TIMEOUT_S = 1`` s
    per fire) are real hardware timing; against mocks they are pure sleep.
    The consumers read them at call time, so patching here reaches every
    wait. Shipped values are unchanged; a test that needs a specific window
    patches it itself (``test_waiting_rule``).
    """
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
        change as dispatch,
    )
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
        tuning as timing,
    )

    monkeypatch.setattr(timing, "WINDOW_S", 0.25)
    monkeypatch.setattr(timing, "POLL_S", 0.005)
    monkeypatch.setattr(timing, "ANSWER_POLL_S", 0.001)
    monkeypatch.setattr(dispatch, "ECHO_SETTLE_TIMEOUT_S", 0.05)


@pytest.fixture(autouse=True)
def fresh_live_log(monkeypatch):
    """Give every test its own shared live-log parse.

    The live LAS X log is parsed once per ``POLL_S`` and shared by every
    reader (``log_reader.LIVE_LOG``); without a fresh one per test, a test
    that points the reader at its own files could be handed the previous
    test's snapshot.
    """
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
        log_reader,
    )

    monkeypatch.setattr(log_reader, "LIVE_LOG", log_reader.SharedParse())


@pytest.fixture
def general_workflow_data(tmp_path):
    """Return a writable temp copy of the canonical offline workflow bundle."""
    if not GENERAL_WORKFLOW_DATA.is_dir():
        pytest.skip(f"test data not found: {GENERAL_WORKFLOW_DATA}")
    dst = tmp_path / "general_workflow"
    shutil.copytree(GENERAL_WORKFLOW_DATA, dst)
    return dst
