"""The driver plugs into the standalone ZMART Controller as a module.

These tests do what an operator does on the ZEN computer: import the driver
module and hand it to ``zmart_controller.set_instrument``. The driver then connects to the offline fake ZEN API, and the
controller's own ``validate_driver`` checks every answer. No ZEISS package is
needed; ``tests/gateway`` repeats the round trip over the real wheel.
"""

from __future__ import annotations

import pytest
import zmart_controller
from mock_zen_api import build_fake_client

from zmart_drivers.zeiss.zenapi import zmart_controller_plugin as driver
from zmart_drivers.zeiss.zenapi import zen_zmart_adapter as adapter


@pytest.fixture
def instrument(monkeypatch, tmp_path):
    """The connection dictionary; the driver connects to the fake ZEN API and temporary folders."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))

    def _fake_open(connection):
        client, scope = build_fake_client()
        scope.image_output_folder = str(tmp_path / "zen_images")
        return client

    monkeypatch.setattr(adapter, "_open_client", _fake_open)
    return {
        "output_root": str(tmp_path / "out"),
        "machine_root": str(tmp_path / "programdata"),
    }


def test_the_module_holds_every_function_the_controller_needs(instrument):
    session = zmart_controller.session.set_instrument(driver, instrument)
    try:
        assert session.context == {"driver": "zmart_drivers.zeiss.zenapi.zmart_controller_plugin"}
        # The microscope's name came from CONNECTION, since the dictionary left it out.
        assert session._handle.connection["microscope"] == adapter.CONNECTION["microscope"]
    finally:
        session.disconnect()


def test_the_driver_fits_the_controller_contract(instrument):
    assert zmart_controller.validate_driver(driver, instrument) == []


def test_every_command_answers_in_the_controller_shape(instrument):
    session = zmart_controller.session.set_instrument(
        driver, {**instrument, "experiment": "ZMART_Snap"}
    )
    try:
        answers = {
            "get_info": session.get_info(),
            "set_xyz": session.set_xyz(10.0, 20.0, 5.0),
            "set_state": session.set_state({"changeable": {"objective_position": 2}}),
            "run_procedure": session.run_procedure({"name": "stop"}),
            "acquire": session.acquire("A1", acquisition_settings={"timeout_s": 2}),
        }
        with pytest.raises(ValueError, match="unknown procedure"):
            session.run_procedure({"name": "no-such-routine"})
    finally:
        session.disconnect()

    for name, answer in answers.items():
        assert set(answer) == {"success", "content"}, name
        assert answer["success"] is True, name
    assert answers["set_state"]["content"]["applied"] == {"objective_position": 2}
    assert zmart_controller.check_acquire_answer(answers["acquire"]) == []


def test_get_info_describes_the_microscope_in_plain_words(instrument):
    session = zmart_controller.session.set_instrument(driver, instrument)
    try:
        report = session.get_info()["content"]
        xyz = session.get_xyz()["content"]
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
    # The canvas is the travel itself; see the adapter's get_xyz for why.
    assert xyz["x"]["canvas"] == [-60000.0, 60000.0]
    assert set(xyz["x"]) == {"value", "actuator", "canvas"}
