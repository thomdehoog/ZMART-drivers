"""The driver plugs into the standalone ZMART Controller as a module.

These tests do what an operator does on the LAS X computer: import the
driver module and hand it to ``zmart_controller.set_instrument``. The driver
then connects to the offline LAS X mock, and the
controller's own ``validate_driver`` checks every answer against its contract.
"""

from __future__ import annotations

import os
from dataclasses import replace

import zmart_controller
from limits_fixtures import hermetic_mock_machine_root
from mock_lasx_api import MockLasxClient

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import zmart_controller_plugin as driver
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.config import profiles
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
    zmart_adapter as adapter,
)


def test_the_module_hands_over_enveloped_commands():
    ops = zmart_controller.utils.driver_functions(driver)

    assert ops["connect"] is adapter.connect
    assert ops["disconnect"] is adapter.disconnect
    for name in zmart_controller.utils.OPS:
        if name != "connect":
            assert ops[name].__wrapped__ is getattr(adapter, name)


def test_the_driver_fits_the_controller_contract_offline(monkeypatch, tmp_path):
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
    problems = zmart_controller.validate_driver(driver, {"output_root": str(tmp_path / "out")})

    assert problems == []


def test_get_info_describes_the_microscope_in_plain_words(monkeypatch, tmp_path):
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", os.environ["ZMART_MICROSCOPY_ROOT"])
    monkeypatch.setenv("APPDATA", os.environ.get("APPDATA", str(tmp_path)))
    monkeypatch.setattr(profiles, "LOG_READER", profiles.LOG_READER)
    hermetic_mock_machine_root()
    monkeypatch.setattr(
        adapter._session, "connect_python_client", lambda **_kw: MockLasxClient(latency=0.0)
    )
    session = zmart_controller.session.set_instrument(
        driver, {"output_root": str(tmp_path / "out")}
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
    # origin the frame is the stage itself, so the canvas is exactly that.
    assert xyz["x"]["canvas"] == [1000.0, 130000.0]
    assert set(xyz["x"]) == {"value", "actuator", "canvas"}
