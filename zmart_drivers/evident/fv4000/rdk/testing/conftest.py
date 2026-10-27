"""Shared pytest setup: a throwaway settings root, a clean limits gate, short waiting windows, and the pretend FLUOVIEW."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# The repository root, so the driver imports by its full name even when the
# repository has not been installed with pip; and the helpers.
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))

TEST_DATA = Path(__file__).resolve().parent / "data"


def pytest_report_header(config):
    try:
        from diagnostics import header_lines

        return header_lines()
    except Exception as exc:  # pragma: no cover - diagnostics must not fail a run
        return [f"fv4000 rdk context: diagnostics unavailable ({exc!r})"]


def pytest_collection_modifyitems(config, items):
    """Skip the tests marked ``controller`` when the ZMART Controller is not installed."""
    if importlib.util.find_spec("zmart_controller") is not None:
        return
    skip = pytest.mark.skip(reason="the ZMART Controller is not installed")
    for item in items:
        if item.get_closest_marker("controller"):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _hermetic_machine_root(tmp_path_factory, monkeypatch):
    """Point the settings root at an empty folder for every test, with no config in use."""
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    from zmart_drivers.evident.fv4000.rdk.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the gate and the stage travel around every test: one test's limits must never govern another's."""
    from zmart_drivers.evident.fv4000.rdk.dispatcher import limits as gate
    from zmart_drivers.evident.fv4000.rdk.driver_setup.limits import limit_rules

    gate._GATE_STATE.clear()
    limit_rules.clear_stage_limits()
    yield
    gate._GATE_STATE.clear()
    limit_rules.clear_stage_limits()


@pytest.fixture(autouse=True)
def fast_timing_windows(monkeypatch):
    """Shrink the waiting rule's real-time windows: against the mock they are pure sleep."""
    from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client, profile

    monkeypatch.setattr(profile, "WINDOW_S", 0.3)
    monkeypatch.setattr(profile, "POLL_S", 0.01)
    monkeypatch.setattr(profile, "ANSWER_POLL_S", 0.001)
    monkeypatch.setattr(client, "MIN_GAP_S", 0.0)


@pytest.fixture
def mock():
    """A pretend FLUOVIEW, quick: moves arrive in 50 ms and a scan takes 0.3 s."""
    from mock_fluoview import MockFluoview

    return MockFluoview()


@pytest.fixture
def client(mock):
    """The in-process client of the pretend FLUOVIEW, without limits installed."""
    from mock_fluoview import MockRdkClient

    return MockRdkClient(mock)


@pytest.fixture
def governed_client(client):
    """The client with permissive limits installed, for tests about mechanics rather than limits."""
    from limits_fixtures import install_permissive_limits

    return install_permissive_limits(client)


@pytest.fixture
def published_limits(tmp_path, monkeypatch):
    """This microscope's limits published in the throwaway root, as an operator would."""
    import os

    from limits_fixtures import provision_machine_limits

    return provision_machine_limits(os.environ["ZMART_MICROSCOPY_ROOT"])


@pytest.fixture
def scope(mock, published_limits, monkeypatch, tmp_path):
    """A connected ``Microscope`` on the pretend FLUOVIEW, saving under a temporary folder."""
    from mock_fluoview import MockRdkClient

    from zmart_drivers.evident.fv4000.rdk import methods
    from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client

    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    microscope = methods.connect(output_root=str(tmp_path / "out"))
    yield microscope
    microscope.close()
