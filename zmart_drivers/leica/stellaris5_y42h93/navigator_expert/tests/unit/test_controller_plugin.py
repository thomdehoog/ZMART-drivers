"""The driver plugs into the standalone ZMART Controller by its folder.

These tests do what an operator does once on the LAS X computer: hand the
driver folder to ``zmart_controller.register_driver``. The controller reads
``zmart_controller/zmart.json``, finds the functions by name, and lists the
instrument. The driver then connects to the offline LAS X mock, and the
controller's own ``validate_driver`` checks every answer against its contract.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest
import zmart_controller
from limits_fixtures import hermetic_mock_machine_root
from mock_lasx_api import MockLasxClient
from zmart_controller import utils

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.config import profiles
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
    zmart_adapter as adapter,
)

DRIVER_DIR = Path(adapter.__file__).resolve().parents[1]
IDENTITY = ("leica", "stellaris5-y42h93", "navigator-expert")


@pytest.fixture
def clean_registry(monkeypatch):
    """An empty controller registry, restored afterwards."""
    monkeypatch.setattr(utils, "REGISTRY", {})
    monkeypatch.setattr(utils, "_discovered", False)
    return utils.REGISTRY


def test_register_by_folder_lists_the_instrument(clean_registry):
    added = zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert added == [adapter.CONNECTION]
    assert IDENTITY in clean_registry
    assert zmart_controller.get_instruments() == [adapter.CONNECTION]


def test_register_by_module_name_finds_the_same_functions(clean_registry):
    zmart_controller.register_driver(
        "zmart_drivers.leica.stellaris5_y42h93.navigator_expert", remember=False
    )
    by_name = dict(clean_registry[IDENTITY]["ops"])
    clean_registry.clear()
    zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert clean_registry[IDENTITY]["ops"] == by_name


def test_remember_false_leaves_the_computer_untouched(clean_registry):
    zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert utils.remembered_drivers() == []
    assert not (Path(os.environ["ZMART_MICROSCOPY_ROOT"]) / "zmart_controller").exists()


def test_the_plugin_hands_over_enveloped_commands(clean_registry):
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    ops = clean_registry[IDENTITY]["ops"]

    assert ops["connect"] is adapter.connect
    assert ops["disconnect"] is adapter.disconnect
    for name in utils.OPS:
        if name != "connect":
            assert ops[name].__wrapped__ is getattr(adapter, name)


def test_the_driver_fits_the_controller_contract_offline(clean_registry, monkeypatch, tmp_path):
    """Connect through the controller to the LAS X mock; validate_driver finds no problems."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", os.environ["ZMART_MICROSCOPY_ROOT"])
    monkeypatch.setenv("APPDATA", os.environ.get("APPDATA", str(tmp_path)))
    monkeypatch.setattr(profiles, "LOG_READER", profiles.LOG_READER)
    monkeypatch.setattr(
        profiles,
        "STATE_READERS",
        replace(profiles.STATE_READERS, selected_job_confirm_source="api"),
    )
    hermetic_mock_machine_root()
    monkeypatch.setattr(
        adapter._session, "connect_python_client", lambda **_kw: MockLasxClient(latency=0.0)
    )
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    instrument = {**zmart_controller.get_instruments()[0], "output_root": str(tmp_path / "out")}

    problems = zmart_controller.validate_driver(instrument)

    assert problems == []


def test_get_info_describes_the_microscope_in_plain_words(clean_registry, monkeypatch, tmp_path):
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", os.environ["ZMART_MICROSCOPY_ROOT"])
    monkeypatch.setenv("APPDATA", os.environ.get("APPDATA", str(tmp_path)))
    monkeypatch.setattr(profiles, "LOG_READER", profiles.LOG_READER)
    hermetic_mock_machine_root()
    monkeypatch.setattr(
        adapter._session, "connect_python_client", lambda **_kw: MockLasxClient(latency=0.0)
    )
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    instrument = {**zmart_controller.get_instruments()[0], "output_root": str(tmp_path / "out")}
    session = zmart_controller.Session(
        clean_registry[IDENTITY]["ops"], adapter.connect(instrument), {}
    )
    try:
        answer = session.get_info()
        xyz = session.get_xyz()["content"]
    finally:
        session.disconnect()

    description = answer["content"]["description"]
    assert answer["success"] is True
    assert description.startswith("A Leica STELLARIS 5 confocal microscope")
    assert "{" not in description
    # The z-galvo bounds come from the limits loaded at connect, not from the text.
    assert "z-galvo travel from -250 to 250 um" in description
    # The fixture limits allow x 1000..130000 um of stage travel; with no saved
    # origin the frame is the stage itself, so the range is exactly that.
    assert xyz["x"]["range"] == [1000.0, 130000.0]
