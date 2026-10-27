"""What every test of the Nikon driver shares: a private settings folder, quick waiting, and the pretend NIS.

Every test runs as if on a fresh computer. The microscope's settings live in
a temporary folder (``ZMART_MICROSCOPY_ROOT``), the limits gate starts
empty, and the waiting rule's windows are short, because the pretend NIS
answers at once and the real windows would only make the suite slow.

The fixtures a test can ask for:

- ``stand_in``: the real bridge serving a pretend microscope, pumped by a
  thread in place of the macro loop (``helpers/bridge_stand_in.py``).
- ``client``: a client of that bridge, already connected.
- ``scope``: the driver connected to it with ``connect()``, as a user would,
  with a temporary output folder; closed after the test.

Tests marked ``controller`` check the driver inside the ZMART Controller, and
are skipped, with the reason, when the controller is not installed: the
driver must also work by itself.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``testing/conftest.py``.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

# The repository root on the path, so the driver imports by its full name
# even when the repository was not installed with pip.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))


@pytest.fixture(autouse=True)
def _hermetic_machine_root(tmp_path_factory, monkeypatch):
    """Keep this computer's real settings out of every test: an empty settings folder each time."""
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)
    monkeypatch.setattr(store, "MACHINE", store.MachineProfile())
    return root


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the limits gate around every test, so one test's limits never govern another's client."""
    from zmart_drivers.nikon.nis_elements_ar7.dispatcher import limits as gate

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def fast_waiting(monkeypatch):
    """Short read-back windows: the pretend stage arrives in a few hundredths of a second."""
    from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import profile

    monkeypatch.setattr(profile.Waiting, "WINDOW_S", 0.3)
    monkeypatch.setattr(profile.Waiting, "POLL_S", 0.005)
    monkeypatch.setattr(profile.Waiting, "ANSWER_POLL_S", 0.001)


@pytest.fixture
def stand_in():
    """The real bridge over a fresh pretend microscope; closed after the test."""
    from bridge_stand_in import StandIn

    bridge = StandIn()
    yield bridge
    bridge.close()


@pytest.fixture
def client(stand_in):
    """A connected client of the stand-in bridge."""
    connected = stand_in.client()
    connected.connect()
    yield connected
    connected.close()


@pytest.fixture
def key_file(stand_in):
    """The stand-in's key, written where the driver looks for it (as the macro would)."""
    from bridge_stand_in import KEY

    from zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge import macro

    path = macro.files()["key"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(KEY, encoding="ascii")
    return path


@pytest.fixture
def output_root(tmp_path):
    folder = tmp_path / "output"
    folder.mkdir()
    return folder


@pytest.fixture
def scope(stand_in, key_file, output_root):
    """The driver, connected to the stand-in with ``connect()``; closed after the test."""
    from zmart_drivers.nikon import nis_elements_ar7

    microscope = nis_elements_ar7.connect(
        port=stand_in.port, output_root=str(output_root)
    )
    yield microscope
    microscope.close()


def pytest_report_header(config):
    """The computer's context at the top of every run, so a report from another computer can be read."""
    try:
        from diagnostics import header_lines

        return header_lines()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover - diagnostics must never fail a run
        return [f"nis_elements_ar7 context: diagnostics unavailable ({exc!r})"]


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked ``controller`` when the ZMART Controller is not installed."""
    if importlib.util.find_spec("zmart_controller") is not None:
        return
    skip = pytest.mark.skip(reason="the ZMART Controller is not installed")
    for item in items:
        if item.get_closest_marker("controller"):
            item.add_marker(skip)
