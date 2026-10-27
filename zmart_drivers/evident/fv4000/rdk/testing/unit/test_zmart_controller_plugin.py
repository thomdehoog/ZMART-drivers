"""The driver plugs into the ZMART Controller as a ``ZmartControllerPlugin`` class, checked by the controller's own validator."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("zmart_controller", reason="the ZMART Controller is not installed")
pytestmark = pytest.mark.controller

import zmart_controller
from mock_fluoview import MockRdkClient
from zmart_controller.registry import load_driver
from zmart_controller.validate import check_acquire_answer

from zmart_drivers.evident.fv4000.rdk.primitives.rdk import (
    client as rdk_client,
)
from zmart_drivers.evident.fv4000.rdk.zmart_controller_plugin import (
    zmart_controller_plugin,
)

#: The folder to register is the driver's: the one that holds zmart_controller_plugin/.
DRIVER_FOLDER = Path(zmart_controller_plugin.__file__).resolve().parents[1]


@pytest.fixture
def connection(mock, published_limits, monkeypatch, tmp_path):
    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    return {"output_root": str(tmp_path / "out")}


def test_the_folder_holds_what_the_controller_reads():
    driver = load_driver(DRIVER_FOLDER)
    assert (
        driver.NAME == "fv4000"
        and driver.ZmartControllerPlugin
        is zmart_controller_plugin.ZmartControllerPlugin
    )
    assert (
        driver.CONNECTION["microscope"] == "fv4000"
        and driver.CONNECTION["port"] == 8080
    )


def test_a_fresh_microscope_fails_the_contract_only_on_the_frame_it_has_not_learned(
    connection,
):
    problems = zmart_controller.validate_driver(DRIVER_FOLDER, connection)
    assert len(problems) == 1 and "image_size_px" in problems[0], problems


def test_the_class_fits_the_controller_contract_once_a_picture_was_taken(
    connection, scope
):
    scope.acquire("setup")
    scope.save()
    scope.close()
    assert zmart_controller.validate_driver(DRIVER_FOLDER, connection) == []


def test_the_class_describes_the_microscope_and_what_is_set_up(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        info = session.get_info()["output"]
        xyz = session.get_xyz()["output"]
        state = session.get_state()["output"]
        procedures = session.get_procedures()["output"]
    finally:
        session.disconnect()
    assert "FV4000" in info["description"] and "UPLXAPO60XO" in info["description"]
    assert set(info["setup"]) == {
        "limits",
        "orientation",
        "calibration",
        "origin",
        "data_location",
        "frame",
    }
    assert (
        info["setup"]["limits"]["done"] is True
        and info["setup"]["origin"]["done"] is False
    )
    assert info["chooses_its_own_folder"] is False
    assert all(
        isinstance(said, str) and said for said in info["connection_status"].values()
    )
    assert xyz["z"]["actuator_readings"].keys() == {"motor", "piezo"}
    assert state["changeable"]["objective"] == 0 and "channels" in state["changeable"]
    assert set(procedures) == {"save_data", "stop_scan", "live_view", "move_piezo"}


def test_an_acquisition_through_the_controller_fits_its_contract(
    connection, mock, tmp_path
):
    mock.scan_s = 0.1
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        session.set_state(
            {"changeable": {"z_stack_enabled": True, "z_stack_slices": 3}}
        )
        answer = session.acquire(
            "A1", acquisition_type="overview", output_folder=str(tmp_path / "run")
        )
    finally:
        session.disconnect()
    assert answer["success"] is True, answer
    assert check_acquire_answer({"success": True, "output": answer["output"]}) == []
    assert (
        answer["output"]["files"][0].endswith("overview_A1.ome.zarr")
        and len(answer["output"]["planes"]) == 6
    )


def test_a_setting_the_microscope_does_not_have_is_refused(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        answer = session.set_state({"changeable": {"zoom": 2.0}})
    finally:
        session.disconnect()
    assert (
        answer["success"] is False
        and "ValueError" in answer["output"]
        and "zoom" in answer["output"]
    )


def test_a_procedure_answers_the_name_of_what_ran(connection, mock):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        answer = session.run_procedure({"name": "live_view", "on": True})
        assert (
            answer == {"success": True, "output": {"ran": "live_view"}}
            and mock.state == "SCANNING"
        )
        assert (
            session.run_procedure({"name": "stop_scan"})["success"] is True
            and mock.state == "IDLING"
        )
        assert (
            session.run_procedure({"name": "move_piezo", "z_um": 10.0})["success"]
            is True
            and mock.piezo_nm == 10_000
        )
        failed = session.run_procedure({"name": "defrost"})
    finally:
        session.disconnect()
    assert failed["success"] is False and "unknown procedure" in failed["output"]
