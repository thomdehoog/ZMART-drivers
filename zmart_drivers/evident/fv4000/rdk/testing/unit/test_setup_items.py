"""What is set up once: the store, the origin, the data location, the orientation, the calibration and the limits measurement."""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from zmart_drivers.evident.fv4000.rdk.driver_setup import data_location, store
from zmart_drivers.evident.fv4000.rdk.driver_setup.limits.measure_limits import (
    TravelRecorder,
    xy_limits_from_points,
    z_limits_from_ends,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.optical_calibration import model
from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation import (
    Orientation,
    load_rig_orientation,
    orientation_config,
    orientation_from_config,
    reorient_array,
)
from zmart_drivers.evident.fv4000.rdk.driver_setup.origin import saved_origin


def _machine():
    return store.MachineProfile(programdata_root=os.environ["ZMART_MICROSCOPY_ROOT"])


# --- the store ------------------------------------------------------------------------------


def test_the_store_keeps_dated_records_and_the_newest_wins():
    machine = _machine()
    first = machine.publish_snapshot(
        datetime(2026, 1, 1, tzinfo=UTC), orientation={"a": 1}
    )
    second = machine.publish_snapshot(
        datetime(2026, 1, 2, tzinfo=UTC), orientation={"a": 2}
    )
    assert machine.snapshots("orientation") == [first, second]
    assert json.loads(machine.orientation_path().read_text())["a"] == 2
    with pytest.raises(ValueError, match="clock moved backwards"):
        machine.publish_snapshot(datetime(2025, 1, 1, tzinfo=UTC), orientation={"a": 0})


def test_an_item_with_a_bundled_default_is_seeded_on_first_use_but_the_limits_are_not():
    machine = _machine()
    assert json.loads(machine.calibration_path().read_text()) == {
        "objectives": {},
        "schema_version": 13,
    }
    assert machine.latest_snapshot("calibration").name.startswith("1970")
    with pytest.raises(FileNotFoundError, match="set_limits.ipynb"):
        machine.limits_path()


def test_configs_are_folders_chosen_when_connecting():
    machine = _machine()
    assert machine.configurations() == []
    name = machine.new_configuration_name(datetime(2026, 10, 26, 19, 15))  # noqa: DTZ001  (a wall-clock moment)
    assert name == "config_20261026_1915"
    (machine.api_root() / name).mkdir(parents=True)
    assert (
        machine.new_configuration_name(datetime(2026, 10, 26, 19, 15))  # noqa: DTZ001  (a wall-clock moment)
        == "config_20261026_1915-2"
    )
    store.use_configuration(name)
    assert machine.snapshot_root() == machine.api_root() / name
    with pytest.raises(ValueError):
        store.use_configuration("yesterday")
    store.use_configuration(None)


def test_the_bundled_defaults_are_where_the_store_says():
    for filename in (
        store.LIMITS_FILENAME,
        store.ORIENTATION_FILENAME,
        store.CALIBRATION_FILENAME,
    ):
        assert _machine().bundled_default_path(filename).is_file(), filename


# --- the origin -----------------------------------------------------------------------------


def test_no_saved_origin_means_fluoview_coordinates_with_a_warning(caplog):
    assert saved_origin.load_saved_origin() is None
    assert "no origin has been saved" in caplog.text


def test_a_saved_origin_loads_whole_and_a_damaged_one_stops_the_connection():
    machine = _machine()
    machine.write_origin(
        {
            "origin": {
                "x_um": 1.0,
                "y_um": 2.0,
                "z_um": 3.0,
                "z_piezo_um": 0.0,
                "objective": {"slot": 1, "name": "20x"},
            }
        }
    )
    assert saved_origin.load_saved_origin() == {
        "x_um": 1.0,
        "y_um": 2.0,
        "z_um": 3.0,
        "z_piezo_um": 0.0,
        "objective": {"slot": 1, "name": "20x"},
    }
    machine.write_origin(
        {"origin": {"x_um": 1.0, "y_um": 2.0, "z_um": 3.0, "z_piezo_um": 0.0}}
    )
    with pytest.raises(RuntimeError, match="objective"):
        saved_origin.load_saved_origin()
    machine.write_origin(
        {
            "origin": {
                "x_um": "far",
                "y_um": 2.0,
                "z_um": 3.0,
                "z_piezo_um": 0.0,
                "objective": None,
            }
        }
    )
    with pytest.raises(RuntimeError, match="x_um"):
        saved_origin.load_saved_origin()
    machine.origin_path().write_text("not json")
    with pytest.raises(RuntimeError, match="could not be read"):
        saved_origin.load_saved_origin()


def test_set_origin_saves_and_the_next_connection_starts_from_it(scope, mock):
    mock.objective_slot = 1
    answer = scope.set_origin()
    assert (
        answer["reference"]["objective"] == {"slot": 1, "name": "UPLXAPO20X"}
        and Path(answer["origin_file"]).is_file()
    )
    assert saved_origin.load_saved_origin()["x_um"] == 10000.0


# --- the data location ----------------------------------------------------------------------


def test_the_data_location_is_checked_saved_and_loaded(tmp_path):
    assert data_location.data_location(_machine()) is None
    assert "not a full path" in data_location.check("relative")[0]
    assert "does not exist" in data_location.check(tmp_path / "missing")[0]
    assert "network share" in data_location.check("//server/share")[0]
    with pytest.raises(ValueError, match="does not exist"):
        data_location.set_data_location(tmp_path / "missing", machine=_machine())
    data_location.set_data_location(tmp_path, machine=_machine())
    assert data_location.data_location(_machine()) == tmp_path.resolve()


# --- the orientation ------------------------------------------------------------------------


def test_the_unmeasured_default_is_no_turn_and_says_so():
    orientation, info = load_rig_orientation()
    assert orientation.is_identity and info["loaded"] and not info["measured"]


def test_a_measured_orientation_loads_and_a_contradictory_file_is_refused():
    machine = _machine()
    machine.publish_snapshot(
        orientation=orientation_config(Orientation(90, True), measured=True)
    )
    orientation, info = load_rig_orientation()
    assert orientation == Orientation(90, True) and info["measured"]
    bad = orientation_config(Orientation(90, True), measured=True)
    bad["sign_convention"] = {"stage_x_from_image": "+X", "stage_y_from_image": "+Y"}
    with pytest.raises(ValueError, match="contradicts"):
        orientation_from_config(bad)
    machine.publish_snapshot(orientation=bad)
    orientation, info = load_rig_orientation()
    assert orientation.is_identity and not info["loaded"] and "error" in info


def test_reorienting_is_lossless_and_a_quarter_turn_swaps_the_axes():
    import numpy as np

    picture = np.arange(12).reshape(3, 4)
    turned = reorient_array(picture, Orientation(90))
    assert turned.shape == (4, 3) and Orientation(90).swaps_axes
    assert sorted(turned.ravel()) == list(range(12))
    with pytest.raises(ValueError):
        Orientation(45)


# --- the calibration ------------------------------------------------------------------------


def test_the_calibration_model_finds_the_reference_and_the_difference_between_two_objectives():
    config = {"schema_version": 13, "objectives": {}}
    model.update_objective(config, 0, name="10x", translation_um=(0, 0, 0))
    model.update_objective(config, 2, name="60x", translation_um=(5, -4, 1.5))
    model.validate_calibration(config)
    assert model.get_reference_slot(config) == 0
    assert model.translation_delta_um({0: (0, 0, 0), 2: (5, -4, 1.5)}, 2, 0) == (
        -5,
        4,
        -1.5,
    )
    with pytest.raises(RuntimeError, match="calibrate_objective_pair"):
        model.translation_delta_um({0: (0, 0, 0)}, 0, 1)
    with pytest.raises(model.OldSchemaError):
        model.prepared_calibration({"schema_version": 3, "objectives": {}})


def test_load_objective_calibration_declined_wanted_and_loaded():
    assert model.load_objective_calibration(enabled=False) == (
        None,
        {
            "enabled": False,
            "loaded": False,
            "name": None,
            "path": None,
            "slots": [],
            "measured_slots": [],
        },
    )
    translations, info = model.load_objective_calibration()
    assert translations == {} and info["loaded"] and info["measured_slots"] == []
    _machine().publish_snapshot(
        calibration={
            "schema_version": 13,
            "objectives": {
                "0": {"name": "10x", "translation_um": [0, 0, 0]},
                "1": {"name": "20x", "translation_um": [1, 2, 3]},
            },
        }
    )
    translations, info = model.load_objective_calibration()
    assert translations == {0: (0.0, 0.0, 0.0), 1: (1.0, 2.0, 3.0)} and info[
        "measured_slots"
    ] == [0, 1]
    _machine().calibration_path().write_text("{")
    translations, info = model.load_objective_calibration()
    assert translations == {} and not info["loaded"]


# --- the limits measurement -----------------------------------------------------------------


def test_the_travel_is_recorded_where_the_stage_stands(client, mock):
    mock.travel_s = 0.0
    travel = TravelRecorder(client)
    with pytest.raises(RuntimeError, match="corner 1"):
        travel.limits()
    for number, (x, y) in enumerate(
        [(1e6, 1e6), (5e7, 1e6), (5e7, 3e7), (1e6, 3e7)], start=1
    ):
        mock._move(x=int(x), y=int(y))
        travel.record_corner(number)
    with pytest.raises(RuntimeError, match="low end"):
        travel.limits()
    mock._move(z=100_000)
    travel.record_z("low")
    mock._move(z=7_000_000)
    travel.record_z("high")
    measured = travel.limits()
    assert measured["limits"] == {
        "x_um": {"range": [1000.0, 50000.0]},
        "y_um": {"range": [1000.0, 30000.0]},
        "z_um": {"range": [100.0, 7000.0]},
    }
    with pytest.raises(ValueError):
        travel.record_corner(5)


def test_measured_ranges_must_span_something_and_sit_within_the_backstop():
    with pytest.raises(ValueError, match="rectangle"):
        xy_limits_from_points([{"x_um": 0, "y_um": 0}] * 4)
    with pytest.raises(RuntimeError, match="backstop"):
        xy_limits_from_points(
            [
                {"x_um": -1e9, "y_um": 0},
                {"x_um": 1, "y_um": 0},
                {"x_um": 1, "y_um": 1},
                {"x_um": 0, "y_um": 1},
            ]
        )
    assert z_limits_from_ends(5.0, -5.0) == {"range": [-5.0, 5.0]}
    with pytest.raises(ValueError):
        z_limits_from_ends(1.0, 1.0)


# --- the frame -------------------------------------------------------------------------------


def test_the_frame_is_learned_from_the_first_picture_and_loaded_at_the_next_connect(
    scope, mock
):
    from zmart_drivers.evident.fv4000.rdk.driver_setup import frame

    assert (
        scope.state()["observed"]["image_size_px"] is None and frame.load_frames() == {}
    )
    assert any(
        "no picture has been taken" in issue
        for issue in scope.state()["observed"]["setup"]["issues"]
    )
    scope.acquire("F1")
    scope.save()
    observed = scope.state()["observed"]
    assert observed["image_size_px"] == [256, 256] and observed["pixel_size_um"] == [
        1.242,
        1.242,
    ]
    assert not any("no picture" in issue for issue in observed["setup"]["issues"])
    loaded = frame.load_frames()
    assert loaded[0]["objective"] == "UPLXAPO10X" and loaded[0]["image_size_px"] == [
        256,
        256,
    ]
    mock.objective_slot = 2
    assert scope.state()["observed"]["image_size_px"] is None, (
        "another objective: not learned yet"
    )


def test_a_frame_record_that_is_damaged_is_skipped_not_fatal():
    from zmart_drivers.evident.fv4000.rdk.driver_setup import frame

    machine = _machine()
    machine.write_frame(
        {
            "frames": {
                "0": {"image_size_px": [512, 512], "pixel_size_um": [0.5, 0.5]},
                "1": {"image_size_px": [0, 512], "pixel_size_um": [0.5, 0.5]},
                "two": "x",
            }
        }
    )
    assert set(frame.load_frames()) == {0}
    machine.write_frame({"frames": "broken"})
    assert frame.load_frames() == {}
    assert frame.frame_as_saved(
        {"image_size_px": [640, 480], "pixel_size_um": [1.0, 2.0]}, Orientation(90)
    ) == ([480, 640], [2.0, 1.0])
    assert frame.frame_as_saved(None, Orientation()) == (None, None)
