"""The driver plugs into the ZMART Controller as a ``ZmartControllerPlugin`` class.

These tests do what an operator does on the NIS-Elements computer: point the
controller at the driver's folder and let the controller's own
``validate_driver`` check every answer against its contract. The driver
connects to the mock behind the bridge, so the tests run on any computer.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("zmart_controller", reason="the ZMART Controller is not installed")
pytestmark = pytest.mark.controller

import zmart_controller
from zmart_controller.registry import load_driver
from zmart_controller.validate import check_acquire_answer

from zmart_drivers.nikon.ti2.nis_elements.zmart_controller_plugin import (
    zmart_controller_plugin,
)

#: The folder to register is the driver's: the one that holds zmart_controller_plugin/.
DRIVER_FOLDER = Path(zmart_controller_plugin.__file__).resolve().parents[1]


def test_the_folder_holds_the_two_files_the_controller_reads():
    driver = load_driver(DRIVER_FOLDER)
    assert driver.NAME == "nikon-ti2"
    assert driver.ZmartControllerPlugin is zmart_controller_plugin.ZmartControllerPlugin
    assert (
        driver.CONNECTION["microscope"] == "ti2" and driver.CONNECTION["port"] == 54470
    )


def test_the_class_fits_the_controller_contract_offline(connection):
    assert zmart_controller.validate_driver(DRIVER_FOLDER, connection) == []


def test_the_class_describes_the_microscope_and_what_is_set_up(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        info = session.get_info()["output"]
        xyz = session.get_xyz()["output"]
        state = session.get_state()["output"]
    finally:
        session.disconnect()
    assert "Nikon Ti2" in info["description"] and "DAPI" in info["description"]
    assert set(info["setup"]) == {
        "limits",
        "orientation",
        "calibration",
        "origin",
        "data_location",
    }
    assert info["chooses_its_own_folder"] is False
    assert info["connection_status"]["bridge"].startswith("answering")
    assert xyz["z"]["used_actuator"] == "focus-drive" and xyz["x"]["canvas"] == [
        -50000.0,
        50000.0,
    ]
    assert state["changeable"]["objective"] == 1


def test_a_move_a_setting_and_a_capture_through_the_controller(connection, tmp_path):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        moved = session.set_xyz(1010.0, -500.0, 500.0)["output"]
        applied = session.set_state(
            {"changeable": {"channel": "TxRed", "exposure_ms": 10}}
        )["output"]
        answer = session.acquire(
            "A1", acquisition_type="overview", output_folder=str(tmp_path / "run")
        )
        refused = session.set_state({"changeable": {"zoom": 2}})
    finally:
        session.disconnect()
    assert moved["x"]["position"] == pytest.approx(1010.0)
    assert applied == {"applied": {"channel": "TxRed", "exposure_ms": 10}}
    assert answer["success"] is True
    assert check_acquire_answer(answer, tmp_path / "run") == []
    assert answer["output"]["planes"][0]["x_um"] == pytest.approx(1010.0)
    assert refused["success"] is False and "zoom" in refused["output"]


def test_a_procedure_answers_the_name_of_what_ran_and_an_unknown_one_fails(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        ran = session.run_procedure({"name": "autofocus", "range_um": 20})
        unknown = session.run_procedure({"name": "clean_the_objective"})
        menu = session.get_procedures()["output"]
    finally:
        session.disconnect()
    assert ran == {"success": True, "output": {"ran": "autofocus"}}
    assert unknown["success"] is False and "unknown procedure" in unknown["output"]
    assert set(menu) == {"autofocus", "lock_focus", "save_data", "set_origin"}
