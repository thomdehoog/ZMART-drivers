"""The driver plugs into the standalone ZMART Controller as a module.

These tests do what an operator does on the mesoSPIM computer: import the
driver module and hand it to ``zmart_controller.set_instrument``. The driver
then connects to the mock Remote Scripting server, and the controller's own
``validate_driver`` checks every answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import zmart_controller

from zmart_drivers.mesospim import zmart_controller_plugin as driver
from zmart_drivers.mesospim import mesospim_zmart_adapter as adapter
from zmart_drivers.mesospim.config.profiles import HARDWARE
from zmart_drivers.mesospim.limits import checks as limits


@pytest.fixture
def instrument(server, tmp_path, monkeypatch):
    """The connection dictionary, pointed at the mock server and temporary folders."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    limits.clear_stage_limits()
    yield {
        "host": server.host,
        "port": server.port,
        "output_root": str(tmp_path / "run"),
        "machine_root": str(tmp_path / "machine"),
    }
    limits.clear_stage_limits()


def test_the_module_holds_every_function_the_controller_needs(instrument):
    session = zmart_controller.session.set_instrument(driver, instrument)
    try:
        assert session.context == {"driver": "zmart_drivers.mesospim.zmart_controller_plugin"}
    finally:
        session.disconnect()


def test_connect_fills_in_what_the_connection_leaves_out(instrument):
    handle = adapter.connect({k: v for k, v in instrument.items() if k != "host"})
    try:
        assert handle.connection["host"] == adapter.CONNECTION["host"]
        assert handle.connection["microscope"] == adapter.CONNECTION["microscope"]
    finally:
        adapter.disconnect(handle)


def test_the_driver_fits_the_controller_contract(instrument):
    assert zmart_controller.validate_driver(driver, instrument) == []


def test_every_command_answers_in_the_controller_shape(instrument):
    session = zmart_controller.session.set_instrument(driver, instrument)
    try:
        answers = {
            "set_xyz": session.set_xyz(10.0, 20.0, 5.0),
            "set_state": session.set_state({"changeable": {"intensity": 40.0}}),
            "run_procedure": session.run_procedure({"name": "move_focus", "value": 12.0}),
            "acquire": session.acquire("A1"),
        }
        with pytest.raises(ValueError, match="unknown procedure"):
            session.run_procedure({"name": "no-such-routine"})
    finally:
        session.disconnect()

    for name, answer in answers.items():
        assert set(answer) == {"success", "content"}, name
        assert answer["success"] is True, name
    assert zmart_controller.check_acquire_answer(answers["acquire"]) == []
    assert Path(answers["acquire"]["content"]["files"][0]).is_file()


def test_get_info_describes_the_microscope_in_plain_words(instrument):
    session = zmart_controller.session.set_instrument(driver, instrument)
    try:
        description = session.get_info()["content"]["description"]
        xyz = session.get_xyz()["content"]
    finally:
        session.disconnect()

    assert description.startswith("A mesoSPIM light-sheet microscope")
    assert "{" not in description
    # The laser lines and zooms come from what the server reports, and the
    # focus and rotation bounds from the bundled stage limits, not from the text.
    assert "405 nm, 488 nm, 561 nm, 647 nm" in description
    assert "1x at 6.55 um per pixel" in description
    assert "theta, in degrees, from -360 to 360" in description
    # The canvas is the bundled stage envelope, widened on x by half the widest
    # field: the zoom with the largest pixels over the longer camera side.
    half_field = max(HARDWARE.camera_pixels) * max(p for _, p in HARDWARE.zoom_pixel_size_um) / 2
    assert xyz["x"]["canvas"] == pytest.approx([-half_field, 25000.0 + half_field])
    assert xyz["z"]["canvas"] == pytest.approx([0.0, 25000.0])
    assert set(xyz["x"]) == {"value", "actuator", "canvas"}
