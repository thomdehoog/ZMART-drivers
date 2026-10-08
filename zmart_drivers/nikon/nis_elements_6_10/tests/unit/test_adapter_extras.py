"""The controller contract for the extras: piezo actuator, state, procedures, stacks."""

import pytest

from zmart_drivers.nikon.nis_elements_6_10 import nis_zmart_adapter as adapter


@pytest.fixture
def handle(connection):
    h = adapter.connect(connection)
    try:
        yield h
    finally:
        adapter.disconnect(h)


def test_piezo_is_a_second_z_actuator(handle, fake_api):
    assert adapter.get_actuators(handle) == {
        "x": ["motoric"],
        "y": ["motoric"],
        "z": ["motoric", "piezo"],
    }
    adapter.set_origin(handle)  # focus origin = 500.0, piezo origin = 50.0
    rec = adapter.set_xyz(handle, 10, 0, 7, with_actuators={"z": "piezo"})
    # The piezo took the whole step; the focus drive was left alone.
    assert fake_api.piezo_z == pytest.approx(57.0) and fake_api.position["z"] == 500.0
    # The answer is what get_xyz answers: the height in the frame, whichever
    # motor moved it, and every motor's own raw reading beside it.
    assert rec == adapter.get_xyz(handle)
    assert rec["z"]["position"] == pytest.approx(7.0)
    assert rec["z"]["actuators"] == {"motoric": 500.0, "piezo": pytest.approx(57.0)}
    # Asking which motor to read changes nothing in the answer.
    assert adapter.get_xyz(handle, with_actuators={"z": "piezo"}) == rec


def test_the_focus_drive_leaves_room_for_the_piezo(handle, fake_api):
    """A height means the same thing whichever motor reaches it.

    After the piezo has lifted the sample by 7 um, asking the focus drive for
    a height of 7 um must not move anything, and asking it for 0 must bring
    the focus drive down by 7 um rather than back to its origin.
    """
    adapter.set_origin(handle)
    adapter.set_xyz(handle, 0, 0, 7, with_actuators={"z": "piezo"})
    assert adapter.set_xyz(handle, 0, 0, 7)["z"]["position"] == pytest.approx(7.0)
    assert fake_api.position["z"] == pytest.approx(500.0)
    back = adapter.set_xyz(handle, 0, 0, 0)
    assert back["z"]["position"] == pytest.approx(0.0)
    assert back["z"]["actuators"] == {"motoric": pytest.approx(493.0), "piezo": pytest.approx(57.0)}


def test_no_piezo_means_no_piezo_actuator(connection, fake_api):
    fake_api.has_piezo = False
    h = adapter.connect(connection)
    assert adapter.get_actuators(h)["z"] == ["motoric"]
    with pytest.raises(ValueError, match="unknown actuator"):
        adapter.get_xyz(h, with_actuators={"z": "piezo"})
    adapter.disconnect(h)


def test_state_covers_exposure_and_pfs(handle, fake_api):
    state = adapter.get_state(handle)
    assert state["changeable"] == {"objective_position": 4, "exposure_ms": 100.0, "pfs": False}
    assert state["observed"]["pfs"]["present"] is True
    applied = adapter.set_state(handle, {"changeable": {"exposure_ms": 30, "pfs": True}})
    assert applied == {"applied": {"exposure_ms": 30.0, "pfs": True}}
    assert fake_api.pfs_on is True


def test_procedures(handle, fake_api):
    names = set(adapter.get_procedures(handle))
    assert names == {"autofocus", "live", "freeze", "pfs_on", "pfs_off"}
    adapter.set_origin(handle)
    result = adapter.run_procedure(handle, {"name": "autofocus", "range_um": 20})
    assert result["frame_z_um"] == pytest.approx(3.0)
    assert result["focus_um"] == pytest.approx(503.0)
    assert adapter.run_procedure(handle, {"name": "pfs_off"})["pfs"]["on"] is False
    adapter.run_procedure(handle, {"name": "live"})
    assert fake_api.is_live
    with pytest.raises(ValueError, match="unknown procedure"):
        adapter.run_procedure(handle, {"name": "make_coffee"})


def test_acquire_stack_saves_nd2(handle, fake_api, tmp_path):
    adapter.set_origin(handle)
    rec = adapter.acquire(
        handle,
        position_label="p1",
        acquisition_settings={"z_start": -10, "z_end": 10, "z_step": 2, "format": "nd2", "exposure_ms": 12},
    )
    assert len(rec["planes"]) == 11 and fake_api.z_series == (510.0, 490.0, 2.0, 11)
    assert rec["files"] == [str(tmp_path / "out" / "data" / "p1.nd2")]
    assert fake_api.exposure_ms == 12.0


def test_acquire_stack_needs_a_range(handle):
    with pytest.raises(ValueError, match="z_start"):
        adapter.acquire(handle, position_label="p1", acquisition_settings={"z_end": 10})
