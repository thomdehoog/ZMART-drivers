"""The driver plugs into the standalone ZMART Controller as a module.

These tests do what an operator does on the NIS-Elements computer: import the
driver module and hand it to ``zmart_controller.ZmartController``. The driver
then connects to the real bridge over the fake NIS API, and the controller's
own ``validate_driver`` checks every answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import zmart_controller

from zmart_drivers.nikon.nis_elements_6_10 import nis_zmart_adapter as adapter
from zmart_drivers.nikon.nis_elements_6_10 import zmart_controller_plugin as driver


@pytest.fixture
def instrument(bridge, tmp_path, monkeypatch):
    """The connection dictionary, pointed at the test bridge and temporary folders."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    return {
        "port": bridge.server_address[1],
        "output_root": str(tmp_path / "out"),
        "machine_root": str(tmp_path / "programdata"),
    }


def test_the_module_holds_every_function_the_controller_needs(instrument):
    session = zmart_controller.ZmartController(driver, instrument)
    try:
        assert session.context == {"driver": "zmart_drivers.nikon.nis_elements_6_10.zmart_controller_plugin"}
    finally:
        session.disconnect()


def test_connect_fills_in_what_the_connection_leaves_out(bridge, tmp_path, monkeypatch):
    # The bridge in these tests listens on a port of its own, so only the port
    # is given; the host and the microscope's name come from CONNECTION.
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    handle = adapter.connect({"port": bridge.server_address[1]})
    try:
        assert handle.connection["host"] == adapter.CONNECTION["host"]
        assert handle.connection["microscope"] == adapter.CONNECTION["microscope"]
    finally:
        adapter.disconnect(handle)


def test_the_driver_fits_the_controller_contract(instrument):
    assert zmart_controller.validate_driver(driver, instrument) == []


def test_every_command_answers_in_the_controller_shape(instrument):
    session = zmart_controller.ZmartController(driver, instrument)
    try:
        answers = {
            "get_info": session.get_info(),
            "get_xyz": session.get_xyz(),
            # No origin is saved in these tests, so the frame is the raw stage
            # frame and the height counts the fake's piezo (50 um) as well.
            "set_xyz": session.set_xyz(0.0, 0.0, 550.0),
            "set_state": session.set_state({"changeable": {"exposure_ms": 20.0}}),
            "run_procedure": session.run_procedure({"name": "live"}),
            # The adapter's acquire takes position_label by keyword only, so a
            # module driver must be called with the keyword; see the README.
            "acquire": session.acquire(position_label="A1"),
        }
    finally:
        session.disconnect()

    for name, answer in answers.items():
        assert set(answer) == {"success", "content"}, name
        assert answer["success"] is True, name
    assert answers["set_state"]["content"]["applied"]["exposure_ms"] == pytest.approx(20.0)
    assert zmart_controller.check_acquire_answer(answers["acquire"]) == []
    assert Path(answers["acquire"]["content"]["files"][0]).is_file()


def test_set_xyz_answers_exactly_what_get_xyz_answers(instrument):
    """After a move the answer is read back from the microscope, in get_xyz's shape."""
    session = zmart_controller.ZmartController(driver, instrument)
    try:
        moved = session.set_xyz(100.0, -50.0, 600.0)["content"]
        read = session.get_xyz()["content"]
    finally:
        session.disconnect()

    assert moved == read
    for axis in ("x", "y", "z"):
        assert list(moved[axis]) == ["position", "unit", "actuators", "canvas"]
        assert moved[axis]["unit"] == "micrometer"
    assert moved["x"]["position"] == pytest.approx(100.0)
    assert moved["y"]["position"] == pytest.approx(-50.0)
    assert moved["z"]["position"] == pytest.approx(600.0)
    assert moved["z"]["actuators"] == {"motoric": pytest.approx(550.0), "piezo": 50.0}


def test_failures_are_reported_not_raised(instrument):
    """Through the controller, an error becomes success False with the message as content."""
    session = zmart_controller.ZmartController(driver, instrument)
    try:
        unknown = session.run_procedure({"name": "no-such-routine"})
        too_far = session.set_xyz(1e9, 0.0, 0.0)
    finally:
        session.disconnect()
    assert unknown["success"] is False and "unknown procedure" in unknown["content"]
    assert too_far["success"] is False and "outside the stage limits" in too_far["content"]


def test_get_info_describes_the_microscope_in_plain_words(instrument):
    session = zmart_controller.ZmartController(driver, instrument)
    try:
        report = session.get_info()["content"]
        xyz = session.get_xyz()["content"]
    finally:
        session.disconnect()

    description = report["description"]
    assert description.startswith("A Nikon microscope driven through NIS-Elements 6.10")
    assert "{" not in description
    # The objectives and the bounds come from what NIS-Elements reports, not from the text.
    for objective in report["objectives"]["objectives"]:
        assert f"{objective['position']}: {objective['name']}" in description
    # The canvas is the travel NIS-Elements reports; see the adapter's _canvas.
    assert xyz["x"]["canvas"][1] == pytest.approx(57000.0)
    assert list(xyz["x"]) == ["position", "unit", "actuators", "canvas"]
