"""The ZMART controller contract, end to end through the fake bridge."""

import pytest

from zmart_drivers.nikon.nis_elements_6_10 import nis_zmart_adapter as adapter


@pytest.fixture
def handle(connection):
    h = adapter.connect(connection)
    try:
        yield h
    finally:
        adapter.disconnect(h)


def test_ops_table_is_complete():
    from zmart_controller.zmart_controller import COMMANDS

    assert set(COMMANDS) <= set(adapter.ops_table())
    # The origin is a driver setup step, so it is not offered to the controller.
    assert "set_origin" not in adapter.ops_table()


def test_connect_reads_limits_and_identity(handle):
    assert handle.limits["x"]["max"] == 57000.0
    assert handle.immutable["version"].startswith("6.10")
    assert handle.immutable["devices"]["xy"] is True


def test_origin_shifts_frame_and_persists(connection, fake_api):
    h = adapter.connect(connection)
    assert adapter.get_xyz(h)["x"]["position"] == pytest.approx(39904.7)
    rec = adapter.set_origin(h)
    assert rec["origin"] == {"x": 39904.7, "y": -13433.1, "z": 500.0}
    assert adapter.get_xyz(h)["x"]["position"] == 0.0
    adapter.disconnect(h)

    h2 = adapter.connect(connection)  # a new session restores the persisted origin
    assert h2.origin == rec["origin"]
    adapter.disconnect(h2)


def test_get_xyz_answers_position_unit_actuators_and_canvas(handle, fake_api):
    """Each axis carries the four entries of the controller's contract, in this order."""
    xyz = adapter.get_xyz(handle)
    assert set(xyz) == {"x", "y", "z"}
    for axis in ("x", "y", "z"):
        assert list(xyz[axis]) == ["position", "unit", "actuators", "canvas"]
        assert xyz[axis]["unit"] == "micrometer"
    # Without an origin the frame is the raw stage frame, and every motor's
    # reading is the stage's own number: the fake starts with a piezo at 50 um.
    assert xyz["x"]["actuators"] == {"motoric": pytest.approx(39904.7)}
    assert xyz["y"]["actuators"] == {"motoric": pytest.approx(-13433.1)}
    assert xyz["z"]["actuators"] == {"motoric": 500.0, "piezo": 50.0}
    # The height of z is the focus drive plus the piezo.
    assert xyz["z"]["position"] == pytest.approx(550.0)
    assert xyz["z"]["canvas"] == [0.0, 10000.0]


def test_set_xyz_maps_through_origin_and_answers_like_get_xyz(handle, fake_api):
    adapter.set_origin(handle)
    rec = adapter.set_xyz(handle, 10, 20, -100)
    assert fake_api.position == pytest.approx({"x": 39914.7, "y": -13413.1, "z": 400.0})
    assert rec == adapter.get_xyz(handle)
    assert {axis: rec[axis]["position"] for axis in rec} == pytest.approx(
        {"x": 10.0, "y": 20.0, "z": -100.0}
    )
    # The readings stay the stage's own numbers, not measured from the origin.
    assert rec["z"]["actuators"] == {"motoric": 400.0, "piezo": 50.0}


def test_set_xyz_outside_limits_is_a_runtime_error(handle, fake_api):
    with pytest.raises(RuntimeError, match="set_xyz refused"):
        adapter.set_xyz(handle, 0, 0, 99999)
    assert fake_api.calls == []


def test_state_round_trip(handle, fake_api):
    state = adapter.get_state(handle)
    assert state["changeable"] == {"objective_position": 4, "exposure_ms": 100.0, "pfs": False}
    assert "DAPI" in state["observed"]["optical_configurations"]
    applied = adapter.set_state(
        handle, {"changeable": {"objective_position": 1, "optical_configuration": "DAPI"}}
    )
    assert applied == {"applied": {"optical_configuration": "DAPI", "objective_position": 1}}
    assert fake_api.nosepiece == 1 and fake_api.selected_configuration == "DAPI"


def test_acquire_saves_into_data_folder(handle, fake_api, tmp_path):
    rec = adapter.acquire(
        handle,
        position_label="tile 3/a",
        acquisition_settings={"folder": "overview", "optical_configuration": "FITC"},
    )
    path = tmp_path / "out" / "data" / "overview" / "tile_3_a.tif"
    assert rec["files"] == [str(path)] and path.exists()
    assert "image_files" not in rec
    assert len(rec["planes"]) == 1 and fake_api.selected_configuration == "FITC"
    assert fake_api.open_documents == 0


def test_acquire_bad_format_is_a_value_error(handle):
    with pytest.raises(ValueError, match="unknown format"):
        adapter.acquire(handle, position_label="b", acquisition_settings={"format": "png"})


def test_ops_refuse_after_disconnect(connection):
    h = adapter.connect(connection)
    adapter.disconnect(h)
    with pytest.raises(RuntimeError, match="disconnected"):
        adapter.get_xyz(h)
