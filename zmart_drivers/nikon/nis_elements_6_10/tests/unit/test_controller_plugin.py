"""The driver plugs into the standalone ZMART Controller by its folder.

These tests do what an operator does once on the NIS-Elements computer: hand
the driver folder to ``zmart_controller.register_driver``. The controller
reads ``zmart_controller/zmart.json``, finds the functions by name, and lists
the instrument. The driver then connects to the real bridge over the fake NIS
API, and the controller's own ``validate_driver`` checks every answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import zmart_controller
from zmart_controller import utils

from zmart_drivers.nikon.nis_elements_6_10 import nis_zmart_adapter as adapter

DRIVER_DIR = Path(adapter.__file__).resolve().parent
IDENTITY = ("nikon", "ti2-simulator", "nis-elements-bridge")


@pytest.fixture
def clean_registry(monkeypatch, tmp_path):
    """An empty controller registry and an empty configuration folder, both restored afterwards."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    monkeypatch.setattr(utils, "REGISTRY", {})
    monkeypatch.setattr(utils, "_discovered", False)
    return utils.REGISTRY


@pytest.fixture
def instrument(clean_registry, bridge, tmp_path):
    """The registered instrument, pointed at the test bridge and temporary folders."""
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    (listed,) = zmart_controller.get_instruments()
    return {
        **listed,
        "port": bridge.server_address[1],
        "output_root": str(tmp_path / "out"),
        "machine_root": str(tmp_path / "programdata"),
    }


def test_register_by_folder_lists_the_instrument(clean_registry):
    added = zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert added == [adapter.CONNECTION]
    assert IDENTITY in clean_registry
    assert utils.remembered_drivers() == []


def test_register_by_module_name_finds_the_same_functions(clean_registry):
    zmart_controller.register_driver("zmart_drivers.nikon.nis_elements_6_10", remember=False)
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
            "get_info": session.get_info(),
            "get_xyz": session.get_xyz(),
            "set_xyz": session.set_xyz(0.0, 0.0, 0.0),
            "set_state": session.set_state({"changeable": {"exposure_ms": 20.0}}),
            "run_procedure": session.run_procedure({"name": "live"}),
            "acquire": session.acquire("prescan", "A1"),
        }
    finally:
        session.disconnect()

    for name, answer in answers.items():
        assert set(answer) == {"success", "report"}, name
        assert answer["success"] is True, name
    assert answers["set_state"]["report"]["applied"]["exposure_ms"] == pytest.approx(20.0)
    assert zmart_controller.check_acquire_answer(answers["acquire"]) == []
    assert Path(answers["acquire"]["report"]["files"][0]).is_file()


def test_failures_are_raised_not_reported(instrument):
    session = zmart_controller.session.set_instrument(instrument)
    try:
        with pytest.raises(ValueError, match="unknown procedure"):
            session.run_procedure({"name": "no-such-routine"})
        with pytest.raises(RuntimeError, match="outside the stage limits"):
            session.set_xyz(1e9, 0.0, 0.0)
    finally:
        session.disconnect()


def test_get_info_describes_the_microscope_in_plain_words(instrument):
    session = zmart_controller.session.set_instrument(instrument)
    try:
        report = session.get_info()["report"]
        xyz = session.get_xyz()["report"]
    finally:
        session.disconnect()

    description = report["description"]
    assert description.startswith("A Nikon microscope driven through NIS-Elements 6.10")
    assert "{" not in description
    # The objectives and the bounds come from what NIS-Elements reports, not from the text.
    for objective in report["objectives"]["objectives"]:
        assert f"{objective['position']}: {objective['name']}" in description
    assert xyz["x"]["range"][1] == pytest.approx(57000.0)
