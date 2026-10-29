"""The driver plugs into the ZMART Controller as a ``ZmartControllerPlugin`` class.

These tests do what an operator does: point the controller at the driver's
folder and let the controller's own ``validate_driver`` check every answer
against its contract, then capture and let ``check_acquire_answer`` check
the files and planes. The driver connects to the stand-in ZEN.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("zmart_controller", reason="the ZMART Controller is not installed")
pytestmark = pytest.mark.controller

import zmart_controller
from mock_zen_api import FakeScope, build_fake_client
from setup_fixtures import provision
from zmart_controller.registry import load_driver

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.zmart_controller_plugin import (
    zmart_controller_plugin,
)

DRIVER_FOLDER = Path(zmart_controller_plugin.__file__).resolve().parents[1]


@pytest.fixture
def connection(monkeypatch, tmp_path):
    """The stand-in ZEN behind the driver, a set-up microscope, and a connection to it."""
    provision()
    scope = FakeScope(image_output_folder=str(tmp_path / "zen_images"))
    monkeypatch.setattr(
        methods, "open_connection", lambda _c: build_fake_client(scope)[0]
    )
    return {
        "password": "pretend",
        "output_root": str(tmp_path / "runs"),
        "experiment": "ZMART_ZStack",
    }


def test_the_folder_holds_the_two_files_the_controller_reads():
    driver = load_driver(DRIVER_FOLDER)

    assert driver.NAME == "zeiss-zen"
    assert driver.ZmartControllerPlugin is zmart_controller_plugin.ZmartControllerPlugin
    assert driver.CONNECTION["password"] is None
    assert set(driver.CONNECTION) == set(methods.DEFAULT_CONNECTION)


def test_the_class_fits_the_controller_contract(connection):
    assert zmart_controller.validate_driver(DRIVER_FOLDER, connection) == []


def test_an_acquisition_answers_files_and_planes_the_controller_accepts(
    connection, tmp_path
):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        folder = tmp_path / "run" / "overview"
        answer = session.acquire(
            "A1", acquisition_type="overview", output_folder=str(folder)
        )
        info = session.get_info()["output"]
    finally:
        session.disconnect()

    assert answer["success"] is True, answer
    assert (
        zmart_controller.check_acquire_answer(
            answer, folder, info["chooses_its_own_folder"]
        )
        == []
    )
    assert len(answer["output"]["planes"]) == 5


def test_the_class_describes_the_microscope_and_what_is_set_up(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        info = session.get_info()["output"]
    finally:
        session.disconnect()

    assert "ZEISS" in info["description"] and "ZMART_ZStack" in info["description"]
    assert set(info["setup"]) == {
        "limits",
        "orientation",
        "calibration",
        "origin",
        "data_location",
    }
    assert info["setup"]["limits"]["done"] is True
    assert info["setup"]["origin"]["done"] is False


def test_a_setting_the_microscope_does_not_have_is_refused(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        answer = session.set_state({"changeable": {"exposure_ms": 20}})
        objective = session.set_state({"changeable": {"objective": 2}})
    finally:
        session.disconnect()

    assert answer["success"] is False and "exposure_ms" in answer["output"]
    assert objective == {"success": True, "output": {"applied": {"objective": 2}}}


def test_a_procedure_answers_the_name_of_what_ran_and_an_unknown_one_fails(connection):
    session = zmart_controller.ZmartController(
        zmart_controller_plugin.ZmartControllerPlugin, connection
    )
    try:
        ran = session.run_procedure({"name": "software_autofocus"})
        unknown = session.run_procedure({"name": "make_coffee"})
    finally:
        session.disconnect()

    assert ran == {"success": True, "output": {"ran": "software_autofocus"}}
    assert unknown["success"] is False and "make_coffee" in unknown["output"]
