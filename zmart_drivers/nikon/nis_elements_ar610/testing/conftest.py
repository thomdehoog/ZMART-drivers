"""Shared pytest setup: a throwaway settings folder, the mock behind the bridge, short waits.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# The repository root, so the driver imports by its full name even when the
# repository has not been installed with pip; and the helpers beside the tests.
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))


def pytest_report_header(config):
    try:
        from diagnostics import header_lines

        return header_lines()
    except Exception as why:  # noqa: BLE001 -- diagnostics must not fail a run
        return [f"nis_elements context: diagnostics unavailable ({why!r})"]


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
    """Point the settings folder at an empty throwaway folder for every test, with no config in use."""
    root = tmp_path_factory.mktemp("zmart_microscopy_root")
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(root))
    from zmart_drivers.nikon.nis_elements_ar610.driver_setup import store

    monkeypatch.setattr(store, "_in_use", None)


@pytest.fixture(autouse=True)
def _clean_limits_gate():
    """Empty the limits gate around every test: it is process-global, keyed by connection."""
    from zmart_drivers.nikon.nis_elements_ar610.dispatcher import limits as gate

    gate._GATE_STATE.clear()
    yield
    gate._GATE_STATE.clear()


@pytest.fixture(autouse=True)
def fast_timing_windows(monkeypatch):
    """Shrink the waiting rule's windows: against the mock the shipped seconds are pure sleep."""
    from zmart_drivers.nikon.nis_elements_ar610.primitives.dll import profile as waiting
    from zmart_drivers.nikon.nis_elements_ar610.primitives.macro import (
        profile as macro_waiting,
    )

    monkeypatch.setattr(waiting, "WINDOW_S", 0.3)
    monkeypatch.setattr(waiting, "POLL_S", 0.01)
    monkeypatch.setattr(waiting, "ANSWER_POLL_S", 0.001)
    monkeypatch.setattr(macro_waiting, "POLL_S", 0.02)


@pytest.fixture
def mock():
    """The mock NIS-Elements, with a calibrated 10x objective in place and a stage that takes 50 ms to arrive."""
    from mock_nis_elements import MockNisElements

    return MockNisElements()


@pytest.fixture
def port(mock):
    """The real bridge server in front of the mock, on a free port."""
    from mock_nis_elements import served

    with served(mock) as port:
        yield port


@pytest.fixture
def connection(port, tmp_path):
    """A connection to the served mock, saving under a throwaway output root."""
    out = tmp_path / "out"
    out.mkdir()
    return {
        "host": "127.0.0.1",
        "port": port,
        "output_root": str(out),
        "request_timeout_s": 5.0,
    }


@pytest.fixture
def scope(connection):
    """The driver connected to the mock; closed afterwards."""
    from zmart_drivers.nikon import nis_elements_ar610 as nis_elements

    scope = nis_elements.connect(**connection)
    yield scope
    scope.close()
