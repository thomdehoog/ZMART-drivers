"""The ZMART controller over the real wheel and the fake gateway.

This is what a workflow does: plug the driver in, move, acquire. Every answer
comes back as ``{"success": ..., "content": ...}``. Setting the origin is not
a controller command; it is a one-time setup step done with the driver
itself, so the one place this file reaches past the controller is to call the
adapter's ``set_origin`` on the session's driver handle.
"""

import pytest
import zmart_controller

from zmart_drivers.zeiss.zenapi import zen_zmart_adapter as adapter
from zmart_drivers.zeiss.zenapi import zmart_controller_plugin as driver


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """An empty configuration folder, for this test only."""
    monkeypatch.setenv("ZMART_MICROSCOPY_ROOT", str(tmp_path / "zmart-microscopy"))


@pytest.fixture
def session(isolated, connection):
    s = zmart_controller.ZmartController(driver, {**connection, "experiment": "ZMART_Snap"})
    try:
        yield s
    finally:
        s.disconnect()


def test_the_driver_fits_the_controller_contract(isolated, connection):
    assert zmart_controller.validate_driver(driver, connection) == []


def test_full_round_trip(session, gateway, tmp_path):
    info = session.get_info()["content"]
    assert info["limits_are_defaults"] is True
    assert info["server"]["zen_api_version"]
    assert info["experiment"] == "ZMART_Snap"

    assert not hasattr(session, "set_origin")  # the controller does not offer it
    adapter.set_origin(session._handle)  # driver setup step, done on the handle
    assert session.get_xyz()["content"]["x"]["position"] == 0.0
    rec = session.set_xyz(250, -250, 12.5)["content"]
    # The answer is the position read back from the gateway, like get_xyz.
    assert {axis: rec[axis]["position"] for axis in rec} == pytest.approx(
        {"x": 250.0, "y": -250.0, "z": 12.5}
    )
    # The origin was the stage's zero, so the motors' own readings match.
    assert rec["x"]["actuators"] == pytest.approx({"motoric": 250.0})
    assert rec["z"]["actuators"] == pytest.approx({"motoric": 12.5})
    assert rec["x"]["unit"] == "micrometer"
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
    assert zmart_controller.check_acquire_answer(answer) == []

    rec = session.acquire(
        position_label="A1",
        acquisition_settings={"experiment": "ZMART_ZStack", "mode": "experiment"},
    )["content"]
    assert rec["mode"] == "experiment" and rec["image_count"] == 5
    # How the images of a whole experiment are laid out is inside the CZI,
    # which the driver does not read yet, so it leaves the planes empty.
    assert rec["planes"] == []

    af = session.run_procedure({"name": "software_autofocus", "timeout_s": 3})["content"]
    assert af["frame_z_um"] == pytest.approx(135.0 - 0.0)  # origin z was 0
    assert session.run_procedure({"name": "live"})["content"]["ran"] == "live"
    assert session.run_procedure({"name": "stop"})["content"]["ran"] == "stop"


def test_move_outside_limits_is_refused_before_zen_is_asked(session, gateway):
    answer = session.set_xyz(0, 0, 50000)
    assert answer["success"] is False
    assert "set_xyz refused" in answer["content"]
    assert not any(c[0].startswith("focus") for c in gateway.zen.calls)
