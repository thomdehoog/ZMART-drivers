"""The driver plugs into the standalone ZMART Controller by its folder.

These tests do what an operator does once on the ZEN computer: hand the
driver folder to ``zmart_controller.register_driver``. The controller reads
``zmart_controller/zmart.json``, finds the functions by name, and lists the
instrument. The driver then connects to the offline fake ZEN API, and the
controller's own ``validate_driver`` checks every answer. No ZEISS package is
needed; ``tests/gateway`` repeats the round trip over the real wheel.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import zmart_controller
from mock_zen_api import build_fake_client
from zmart_controller import utils

from zmart_drivers.zeiss.zenapi import zen_zmart_adapter as adapter

DRIVER_DIR = Path(adapter.__file__).resolve().parent
IDENTITY = ("zeiss", "zen-lm", "zen-api")


@pytest.fixture
def clean_registry(monkeypatch, tmp_path):
    """An empty controller registry and an empty configuration folder, both restored afterwards."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    monkeypatch.setattr(utils, "REGISTRY", {})
    monkeypatch.setattr(utils, "_discovered", False)
    return utils.REGISTRY


@pytest.fixture
def instrument(clean_registry, monkeypatch, tmp_path):
    """The registered instrument, connected to the fake ZEN API and temporary folders."""

    def _fake_open(connection):
        client, scope = build_fake_client()
        scope.image_output_folder = str(tmp_path / "zen_images")
        return client

    monkeypatch.setattr(adapter, "_open_client", _fake_open)
    zmart_controller.register_driver(DRIVER_DIR, remember=False)
    (listed,) = zmart_controller.get_instruments()
    return {
        **listed,
        "output_root": str(tmp_path / "out"),
        "machine_root": str(tmp_path / "programdata"),
    }


def test_register_by_folder_lists_the_instrument(clean_registry):
    added = zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert added == [adapter.CONNECTION]
    assert IDENTITY in clean_registry
    assert utils.remembered_drivers() == []


def test_register_by_module_name_finds_the_same_functions(clean_registry):
    zmart_controller.register_driver("zmart_drivers.zeiss.zenapi", remember=False)
    by_name = dict(clean_registry[IDENTITY]["ops"])
    clean_registry.clear()
    zmart_controller.register_driver(DRIVER_DIR, remember=False)

    assert clean_registry[IDENTITY]["ops"] == by_name


def test_the_driver_fits_the_controller_contract(instrument):
    assert zmart_controller.validate_driver(instrument) == []


def test_every_command_answers_in_the_controller_shape(instrument):
    session = zmart_controller.session.set_instrument({**instrument, "experiment": "ZMART_Snap"})
    try:
        answers = {
            "get_info": session.get_info(),
            "set_xyz": session.set_xyz(10.0, 20.0, 5.0),
            "set_state": session.set_state({"changeable": {"objective_position": 2}}),
            "run_procedure": session.run_procedure({"name": "stop"}),
        }
        with pytest.raises(ValueError, match="unknown procedure"):
            session.run_procedure({"name": "no-such-routine"})
    finally:
        session.disconnect()

    for name, answer in answers.items():
        assert set(answer) == {"success", "report"}, name
        assert answer["success"] is True, name
    assert answers["set_state"]["report"]["applied"] == {"objective_position": 2}


def test_get_info_describes_the_microscope_in_plain_words(instrument):
    session = zmart_controller.session.set_instrument(instrument)
    try:
        report = session.get_info()["report"]
        xyz = session.get_xyz()["report"]
    finally:
        session.disconnect()

    description = report["description"]
    assert description.startswith("A ZEISS microscope driven through ZEN")
    assert "{" not in description
    # A first connect copies generic limits; the description says so.
    assert "still the generic defaults" in description
    for objective in report["objectives"]:
        assert f"{objective['index']}: {objective['name']}" in description
    # The generic default limits are x -60000..60000 um; with no origin saved
    # the frame is the stage itself.
    assert xyz["x"]["range"] == [-60000.0, 60000.0]
