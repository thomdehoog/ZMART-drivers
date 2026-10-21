"""The driver plugs into the ZMART Controller as a ``ZmartControllerPlugin`` class.

These tests do what an operator does on the LAS X computer: point the
controller at the driver's ``zmart_controller_plugin`` folder, which holds ``zmart_controller_plugin.json`` and
``zmart_controller_plugin.py``, and let the controller's own ``validate_driver`` check
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

from zmart_drivers.leica.stellaris_y42h93.navigator_expert.driver_api import microscope
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.zmart_controller_plugin import zmart_controller_plugin
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.primitives.log import log_settings


# The folder to register is the driver's: the one that holds zmart_controller_plugin/.
DRIVER_FOLDER = Path(zmart_controller_plugin.__file__).resolve().parents[1]

from zmart_drivers.leica.stellaris_y42h93.navigator_expert.connection import (
    lasx_client as _connect_module,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.operations.readings import reading_settings


def _offline(monkeypatch, tmp_path):
    """Stand the LAS X mock in for the real CAM API, with a throwaway machine root."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", os.environ["ZMART_MICROSCOPY_ROOT"])
    monkeypatch.setenv("APPDATA", os.environ.get("APPDATA", str(tmp_path)))
    monkeypatch.setattr(log_settings, "LOG_READER", log_settings.LOG_READER)
    monkeypatch.setattr(
        reading_settings,
        "STATE_READERS",
        replace(reading_settings.STATE_READERS, selected_job_confirm_source="api"),
    )
    hermetic_mock_machine_root()
    monkeypatch.setattr(
        _connect_module,
        "connect_python_client",
        lambda **_kw: MockLasxClient(latency=0.0),
    )
    return {"output_root": str(tmp_path / "out")}


def test_the_folder_holds_the_two_files_the_controller_reads():
    driver = load_driver(DRIVER_FOLDER)

    assert driver.NAME == "stellaris"
    assert driver.ZmartControllerPlugin is zmart_controller_plugin.ZmartControllerPlugin
    assert driver.CONNECTION["microscope"] == "stellaris-y42h93"


def test_the_class_fits_the_controller_contract_offline(monkeypatch, tmp_path):
    """Point validate_driver at the folder, as register_driver is; it finds no problems."""
    connection = _offline(monkeypatch, tmp_path)

    problems = zmart_controller.validate_driver(DRIVER_FOLDER, connection)

    assert problems == []


def test_the_class_describes_the_microscope_and_what_is_set_up(monkeypatch, tmp_path):
    """The class answers the controller's questions, including what is set up once."""
    connection = _offline(monkeypatch, tmp_path)
    through_class = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        as_class = {
            name: getattr(through_class, name)()["output"]
            for name in ("get_actuators", "get_xyz", "get_state", "get_procedures")
        }
        class_info = through_class.get_info()["output"]
    finally:
        through_class.disconnect()

    assert class_info["description"].strip()
    # Where it saves is not part of the setup answer; the controller asks for it elsewhere.
    assert "output_root" not in class_info
    assert set(class_info["setup"]) == {
        "limits",
        "orientation",
        "calibration",
        "origin",
        "data_location",
    }
    for item in class_info["setup"].values():
        assert isinstance(item["done"], bool) and item["says"].strip()
    assert as_class["get_xyz"]["z"]["actuators"].keys() == {"z-wide", "z-galvo"}


def test_the_class_says_it_saves_where_it_is_told(monkeypatch, tmp_path):
    """The driver writes every file itself, from LAS X's cache, so it can choose the folder."""
    connection = _offline(monkeypatch, tmp_path)
    session = zmart_controller.ZmartController(zmart_controller_plugin.ZmartControllerPlugin, connection)
    try:
        info = session.get_info()["output"]
    finally:
        session.disconnect()

    assert info["chooses_its_own_folder"] is False


def test_the_class_hands_the_type_and_the_folder_to_the_capture(monkeypatch, tmp_path):
    connection = _offline(monkeypatch, tmp_path)
    asked = {}

    def capture(handle, **given):
        asked.update(given)
        return {"position_label": given["position_label"]}

    def save_waiting(handle, label):
        return [{"files": [], "planes": []}]

    session = zmart_controller.ZmartController(zmart_controller_plugin.ZmartControllerPlugin, connection)
    try:
        monkeypatch.setattr(microscope.acquire_step, "acquire", capture)
        monkeypatch.setattr(microscope.save_step, "save_waiting", save_waiting)
        answer = session.acquire(
            "A1", acquisition_type="overview", output_folder=str(tmp_path / "run")
        )
    finally:
        session.disconnect()

    assert asked == {
        "position_label": "A1",
        "acquisition_settings": None,
        "acquisition_type": "overview",
        "output_folder": str(tmp_path / "run"),
    }
    assert answer["output"]["acquisition_type"] == "overview"


def test_a_setting_the_microscope_does_not_have_is_refused(monkeypatch, tmp_path):
    """Only the job can be set through the controller; anything else is a wrong request, not ignored."""
    connection = _offline(monkeypatch, tmp_path)
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        answer = session.set_state({"changeable": {"zoom": 2.0}})
    finally:
        session.disconnect()

    assert answer["success"] is False
    assert "ValueError" in answer["output"] and "zoom" in answer["output"]
