"""The driver inside the ZMART Controller: every command, checked with the controller's own validators.

These tests are marked ``controller``: without the controller installed they
are skipped, because the driver must also work by itself.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.controller

DRIVER_FOLDER = Path(__file__).resolve().parents[2]


@pytest.fixture
def connection(stand_in, key_file, output_root):
    return {"port": stand_in.port, "output_root": str(output_root)}


@pytest.fixture
def controller(connection):
    from zmart_controller.registry import load_driver
    from zmart_controller.zmart_controller import ZmartController

    session = ZmartController(load_driver(DRIVER_FOLDER), connection)
    yield session
    session.disconnect()


def test_the_plugin_file_names_the_driver_and_every_connection_setting():
    from zmart_drivers.nikon.nis_elements_ar7.methods import DEFAULT_CONNECTION

    plugin = json.loads(
        (
            DRIVER_FOLDER / "zmart_controller_plugin" / "zmart_controller_plugin.json"
        ).read_text()
    )
    assert plugin["name"] == "nikon-nis-elements-ar7"
    assert set(plugin["connection"]) == set(DEFAULT_CONNECTION)


def test_validate_driver_finds_no_problem_after_one_capture(
    controller, connection, output_root
):
    from zmart_controller.validate import validate_driver

    assert controller.acquire("first", "overview", str(output_root))["success"]
    assert validate_driver(DRIVER_FOLDER, connection) == []


def test_before_any_capture_only_the_picture_size_is_missing(connection):
    from zmart_controller.validate import validate_driver

    problems = validate_driver(DRIVER_FOLDER, connection)
    assert len(problems) == 1 and "image_size_px" in problems[0]


@pytest.mark.parametrize("given_folder", [True, False])
def test_acquire_passes_the_controllers_check(controller, output_root, given_folder):
    from zmart_controller.validate import check_acquire_answer

    folder = str(output_root / "run") if given_folder else None
    answer = controller.acquire("A1", "overview", folder)
    assert answer["success"], answer
    assert check_acquire_answer(answer, folder or str(output_root)) == []


def test_get_info_describes_the_microscope(controller):
    info = controller.get_info()
    assert info["success"]
    output = info["output"]
    assert "Plan Apo 10x" in output["description"] and "DAPI" in output["description"]
    assert output["chooses_its_own_folder"] is False
    assert set(output["setup"]) == {
        "limits",
        "orientation",
        "origin",
        "data_location",
        "pixel calibration in NIS",
    }
    assert output["connection_status"]["bridge"].startswith("answering")


def test_with_the_microscope_off_the_stage_check_begins_with_failed(
    stand_in, controller
):
    stand_in.scope.hardware_on = False
    status = controller.get_info()["output"]["connection_status"]
    assert status["stage"].startswith("failed")


def test_set_state_applies_and_refuses_through_the_controller(controller):
    applied = controller.set_state(
        {"changeable": {"objective_position": 2, "optical_configuration": "GFP"}}
    )
    assert applied == {
        "success": True,
        "output": {
            "applied": {"objective_position": 2, "optical_configuration": "GFP"}
        },
    }
    refused = controller.set_state({"changeable": {"zoom": 2}})
    assert refused["success"] is False and "zoom" in refused["output"]


def test_set_xyz_answers_like_get_xyz(controller):
    moved = controller.set_xyz(1010.0, 2020.0, 3005.0)
    assert moved["success"]
    assert moved["output"]["x"]["position"] == pytest.approx(1010.0)
    assert controller.get_xyz()["output"]["z"]["position"] == pytest.approx(3005.0)


def test_save_data_runs_and_an_unknown_procedure_fails(controller):
    assert "save_data" in controller.get_procedures()["output"]
    assert controller.run_procedure({"name": "save_data"})["success"]
    unknown = controller.run_procedure({"name": "clear_cache"})
    assert unknown["success"] is False and "unknown procedure" in unknown["output"]
