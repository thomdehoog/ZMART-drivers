"""The driver plugs into the ZMART Controller as a ``ZmartDriver`` class.

These tests do what an operator does on the LAS X computer: point the
controller at the driver's folder, which holds ``zmart_driver.json`` and
``zmart_driver.py``, and let the controller's own ``validate_driver`` check
every answer against its contract. The driver connects to the offline LAS X
mock, so the tests run on any computer.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import zmart_controller
from limits_fixtures import hermetic_mock_machine_root
from mock_lasx_api import MockLasxClient
from zmart_controller.registry import load_driver

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import (
    zmart_controller_plugin as plugin,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert import zmart_driver
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import profiles
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.zmart_adapter import (
    zmart_adapter as adapter,
)

DRIVER_FOLDER = Path(zmart_driver.__file__).resolve().parent


def _offline(monkeypatch, tmp_path):
    """Stand the LAS X mock in for the real CAM API, with a throwaway machine root."""
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
    return {"output_root": str(tmp_path / "out")}


def test_the_folder_holds_the_two_files_the_controller_reads():
    driver = load_driver(DRIVER_FOLDER)

    assert driver.NAME == "stellaris"
    assert driver.ZmartDriver is zmart_driver.ZmartDriver
    assert driver.CONNECTION["microscope"] == "stellaris5-y42h93"


def test_the_class_fits_the_controller_contract_offline(monkeypatch, tmp_path):
    """Point validate_driver at the folder, as register_driver is; it finds no problems."""
    connection = _offline(monkeypatch, tmp_path)

    problems = zmart_controller.validate_driver(DRIVER_FOLDER, connection)

    assert problems == []


def test_the_class_answers_what_the_module_answers(monkeypatch, tmp_path):
    """The class and the older module shape describe the same microscope the same way."""
    connection = _offline(monkeypatch, tmp_path)
    through_class = zmart_controller.ZmartController(zmart_driver.ZmartDriver, connection)
    try:
        as_class = {
            name: getattr(through_class, name)()["content"]
            for name in ("get_actuators", "get_xyz", "get_state", "get_procedures")
        }
        description = through_class.get_info()["content"]["description"]
    finally:
        through_class.disconnect()
    through_module = zmart_controller.ZmartController(plugin, connection)
    try:
        as_module = {
            name: getattr(through_module, name)()["content"]
            for name in ("get_actuators", "get_xyz", "get_state", "get_procedures")
        }
        info = through_module.get_info()["content"]
    finally:
        through_module.disconnect()

    # The module's get_xyz adds one extra on top of the four entries per axis.
    as_module["get_xyz"].pop("objective_translation_um")
    assert as_class == as_module
    assert description == info["description"]
    assert as_class["get_xyz"]["z"]["actuators"].keys() == {"z-wide", "z-galvo"}
