"""The ZMART controller session over the real wheel and the fake gateway.

This is what a workflow does: plug the driver in, pick the instrument, move,
acquire. Every answer comes back as ``{"success": ..., "content": ...}``.
Setting the origin is not a controller command; it is a one-time setup step
done with the driver itself, so the one place this file reaches past the
controller is to call the adapter's ``set_origin`` on the session's driver
handle.
"""

from pathlib import Path

import pytest
import zmart_controller
from zmart_controller import utils

from zmart_drivers.zeiss.zenapi import zen_zmart_adapter as adapter

DRIVER_DIR = Path(adapter.__file__).resolve().parent


@pytest.fixture
def registered(monkeypatch, tmp_path):
    """The driver plugged in by its folder, for this test only."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))
    monkeypatch.setattr(utils, "REGISTRY", {})
    monkeypatch.setattr(utils, "_discovered", False)
    zmart_controller.register_driver(DRIVER_DIR, remember=False)


@pytest.fixture
def session(registered, connection):
    s = zmart_controller.session.set_instrument({**connection, "experiment": "ZMART_Snap"})
    try:
        yield s
    finally:
        s.disconnect()


def test_instrument_is_listed(registered):
    assert any(
        i["vendor"] == "zeiss" and i["api"] == "zen-api" for i in zmart_controller.get_instruments()
    )


def test_the_driver_fits_the_controller_contract(registered, connection):
    assert zmart_controller.validate_driver(connection) == []


def test_full_round_trip(session, gateway, tmp_path):
    info = session.get_info()["content"]
    assert info["limits_are_defaults"] is True
    assert info["server"]["zen_api_version"]
    assert info["experiment"] == "ZMART_Snap"

    assert not hasattr(session, "set_origin")  # the controller does not offer it
    adapter.set_origin(session._handle)  # driver setup step, done on the handle
    assert session.get_xyz()["content"]["x"]["value"] == 0.0
    rec = session.set_xyz(250, -250, 12.5)["content"]
    assert rec["confirmed"] == pytest.approx({"x": 250.0, "y": -250.0, "z": 12.5})
    assert gateway.zen.x_m == pytest.approx(250e-6)

    state = session.get_state()["content"]
    assert state["changeable"]["experiment"] == "ZMART_Snap"
    assert "ZMART_ZStack" in state["observed"]["available_experiments"]
    session.set_state({"changeable": {"objective_position": 3}})
    assert gateway.zen.objective_position == 3

    settings = session.get_acquisition_settings()["content"]
    assert settings["mode"]["options"] == ["snap", "experiment"]
    answer = session.acquire(position_label="A1", acquisition_settings={"folder": "overview"})
    assert answer["success"] is True
    rec = answer["content"]
    assert rec["copied"] is True
    assert rec["files"] == [str(tmp_path / "out" / "data" / "overview" / "overview_A1.czi")]
    assert rec["position"] == pytest.approx({"x": 250.0, "y": -250.0, "z": 12.5})

    rec = session.acquire(
        position_label="A1",
        acquisition_settings={"experiment": "ZMART_ZStack", "mode": "experiment"},
    )["content"]
    assert rec["mode"] == "experiment" and rec["planes"] == 5

    af = session.run_procedure({"name": "software_autofocus", "timeout_s": 3})["content"]
    assert af["frame_z_um"] == pytest.approx(135.0 - 0.0)  # origin z was 0
    assert session.run_procedure({"name": "live"})["content"]["ran"] == "live"
    assert session.run_procedure({"name": "stop"})["content"]["ran"] == "stop"


def test_move_outside_limits_is_refused_before_zen_is_asked(session, gateway):
    with pytest.raises(RuntimeError, match="set_xyz refused"):
        session.set_xyz(0, 0, 50000)
    assert not any(c[0].startswith("focus") for c in gateway.zen.calls)
