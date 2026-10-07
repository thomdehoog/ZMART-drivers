"""The driver plugs into the standalone ZMART Controller by its folder.

These tests do what an operator does once on the mesoSPIM computer: hand the
driver folder to ``zmart_controller.register_driver``. The controller reads
``zmart_controller/zmart.json``, finds the functions by name, and lists the
instrument. The driver then connects to the mock Remote Scripting server, and
the controller's own ``validate_driver`` checks every answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import zmart_controller
from zmart_controller import utils

from zmart_drivers.mesospim import mesospim_zmart_adapter as adapter
from zmart_drivers.mesospim.limits import checks as limits

DRIVER_DIR = Path(adapter.__file__).resolve().parent
IDENTITY = ("mesospim", "mesospim-01", "remote-scripting")


@pytest.fixture
def clean_registry(monkeypatch, tmp_path):
    """An empty controller registry and an empty configuration folder, both restored afterwards."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    monkeypatch.setattr(utils, "REGISTRY", {})
    monkeypatch.setattr(utils, "_discovered", False)
    limits.clear_stage_limits()
    yield utils.REGISTRY
    limits.clear_stage_limits()


@pytest.fixture
def instrument(clean_registry, server, tmp_path):
    """The registered instrument, pointed at the mock server and temporary folders."""
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    (listed,) = zmart_controller.get_instruments()
    return {
        **listed,
        "host": server.host,
        "port": server.port,
        "output_root": str(tmp_path / "run"),
        "machine_root": str(tmp_path / "machine"),
    }


def test_register_by_folder_lists_the_instrument(clean_registry):
    added = zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert added == [adapter.CONNECTION]
    assert IDENTITY in clean_registry
    assert utils.remembered_drivers() == []


def test_register_by_module_name_finds_the_same_functions(clean_registry):
    zmart_controller.register_driver("zmart_drivers.mesospim", remember=False)
    by_name = dict(clean_registry[IDENTITY]["ops"])
    clean_registry.clear()
    zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert clean_registry[IDENTITY]["ops"] == by_name


def test_the_driver_fits_the_controller_contract(instrument):
    assert zmart_controller.validate_driver(instrument) == []


def test_every_command_answers_in_the_controller_shape(instrument):
    session = zmart_controller.session.set_instrument(instrument)
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
    session = zmart_controller.session.set_instrument(instrument)
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
    assert xyz["x"]["range"] == [0.0, 25000.0]
