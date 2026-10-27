"""What the operator sets up once: the store, the orientation measured on the mock, the calibration, the data location.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import numpy as np
import pytest

from zmart_drivers.nikon.ti2.nis_elements.driver_setup import (
    data_location,
    orientation,
    store,
)
from zmart_drivers.nikon.ti2.nis_elements.driver_setup.limits.measure_limits import (
    CornerRecorder,
)
from zmart_drivers.nikon.ti2.nis_elements.driver_setup.optical_calibration import model
from zmart_drivers.nikon.ti2.nis_elements.driver_setup.optical_calibration.calibrate_objective_pair import (
    ObjectivePairRecorder,
)
from zmart_drivers.nikon.ti2.nis_elements.driver_setup.orientation import (
    measure_orientation,
)

ALL_TURNS = [(deg, mirrored) for deg in (0, 90, 180, 270) for mirrored in (False, True)]


# --- the store -------------------------------------------------------------------------


def test_records_are_dated_newest_wins_and_nothing_is_overwritten():
    machine = store.MACHINE
    first = machine.write_record("origin", {"origin": 1})
    second = machine.write_record("origin", {"origin": 2})
    assert first.parent != second.parent and machine.read_record("origin") == {
        "origin": 2
    }
    assert machine.records("origin") == [first.parent, second.parent]
    assert machine.read_record("data_location") is None
    with pytest.raises(ValueError, match="clock moved backwards"):
        machine.write_record(
            "origin", {"origin": 3}, moment=datetime(2000, 1, 1, tzinfo=UTC)
        )


def test_a_config_is_a_folder_of_its_own():
    machine = store.MACHINE
    name = machine.new_configuration_name()
    (machine.api_root() / name).mkdir(parents=True)
    assert machine.configurations() == [name]
    store.use_configuration(name)
    try:
        assert machine.snapshot_root() == machine.api_root() / name
        machine.write_record("origin", {"origin": 1})
    finally:
        store.use_configuration(None)
    assert machine.read_record("origin") is None
    with pytest.raises(ValueError, match="not a config's name"):
        store.use_configuration("somewhere")


# --- the orientation ---------------------------------------------------------------------


@pytest.mark.parametrize("turn", ALL_TURNS)
def test_apply_undoes_each_of_the_eight_turns_and_the_matrix_agrees_with_it(turn):
    """A feature's displacement in the raw picture, through the matrix, is its displacement along the stage's axes."""
    deg, mirrored = turn
    aligned = np.zeros((20, 30))
    aligned[5, 7] = 1  # a feature
    aligned[9, 17] = 2  # another, 10 columns right and 4 rows down
    raw = np.rot90(aligned, k=deg // 90)
    raw = np.fliplr(raw) if mirrored else raw
    turned = orientation.Orientation(deg, mirrored)

    assert np.array_equal(turned.apply(raw), aligned)
    (r1, c1), (r2, c2) = (np.argwhere(raw == 1)[0], np.argwhere(raw == 2)[0])
    matrix = np.asarray(turned.picture_to_stage)
    assert list(matrix @ np.array([c2 - c1, r2 - r1])) == [10, 4]


def test_nearest_orientation_snaps_a_measured_matrix_to_a_whole_turn():
    found = orientation.nearest_orientation([[0.1, -0.98], [1.02, 0.05]])
    assert found == orientation.Orientation(90, False)
    with pytest.raises(ValueError, match="zero column"):
        orientation.nearest_orientation([[0, 0], [1, 0]])


@pytest.mark.parametrize("turn", ALL_TURNS)
def test_the_orientation_is_measured_on_the_mock_with_two_stage_steps(
    scope, mock, turn
):
    mock.turn = turn
    measured = measure_orientation.measure(scope.handle, step_um=10.0)

    assert measured["orientation"] == orientation.Orientation(*turn)
    assert measured["pixel_size_um"] == pytest.approx(0.65, rel=0.1)
    # Back where it started, within the move's tolerance: the mock's stage may
    # still be settling over its last micrometre when this reads it.
    back = mock.get_position()
    assert back["x"] == pytest.approx(1000.0, abs=1.0)
    assert back["y"] == pytest.approx(-500.0, abs=1.0)
    assert back["z"] == pytest.approx(500.0, abs=1.0)


def test_a_blank_field_gives_no_orientation(scope, mock):
    mock.frame = np.zeros((48, 64))
    with pytest.raises(RuntimeError, match="too little texture"):
        measure_orientation.measure(scope.handle, step_um=10.0)


def test_a_measured_orientation_is_adopted_and_loaded_at_the_next_connect(
    scope, mock, connection
):
    from zmart_drivers.nikon.ti2 import nis_elements

    mock.turn = (270, True)
    measure_orientation.measure_and_adopt(scope.handle, step_um=10.0)
    assert scope.handle.orientation == orientation.Orientation(270, True)

    again = nis_elements.connect(**connection)
    try:
        assert again.handle.orientation == orientation.Orientation(270, True)
        assert again.handle.orientation_info["measured"] is True
        assert again.state()["observed"]["setup"]["issues"] == [
            "this microscope's limits are not published; the bundled defaults govern every move",
            "no objective pair is calibrated; a change of objective cannot keep the sample in view",
            "no origin is saved; positions are plain stage coordinates",
            "no data location is set; nothing is saved unless a folder is given",
        ]
    finally:
        again.close()


def test_a_damaged_orientation_stops_the_connection(scope, connection):
    from zmart_drivers.nikon.ti2 import nis_elements

    store.MACHINE.write_record(
        "orientation",
        {"schema_version": 1, "measured": True, "rotation_deg": 45, "mirrored": False},
    )
    with pytest.raises(RuntimeError, match="could not be used"):
        nis_elements.connect(**connection)


# --- the objective calibration ------------------------------------------------------------


def test_a_pair_is_recorded_with_the_feature_centred_under_each_objective_and_adopted(
    scope, mock
):
    pair = ObjectivePairRecorder(scope.handle)
    pair.record()  # under slot 1, at the start position
    pair.change_objective(2)
    scope.move_to(
        1012.5, -503.0, 541.0
    )  # the operator re-centres the feature under slot 2
    pair.record()

    assert pair.translations() == {
        1: {"name": "Plan Apo 10x", "translation_um": [0.0, 0.0, 0.0]},
        2: {"name": "Apo 20x WI", "translation_um": [12.5, -3.0, 41.0]},
    }
    pair.adopt()
    assert scope.handle.translations == {1: (0.0, 0.0, 0.0), 2: (12.5, -3.0, 41.0)}
    assert scope.handle.calibration_info["measured_slots"] == [1, 2]
    with pytest.raises(RuntimeError, match="at least two"):
        ObjectivePairRecorder(scope.handle).translations()


def test_a_calibration_file_is_checked_and_a_later_pair_keeps_the_reference():
    with pytest.raises(ValueError, match="reference"):
        model.validate_calibration(
            {
                "schema_version": 1,
                "objectives": {"2": {"name": "b", "translation_um": [1, 2, 3]}},
            }
        )
    model.adopt_calibration(
        {
            1: {"name": "a", "translation_um": [0, 0, 0]},
            2: {"name": "b", "translation_um": [1, 2, 3]},
        }
    )
    model.adopt_calibration({4: {"name": "d", "translation_um": [5, 6, 7]}})
    translations, info = model.load_objective_calibration()
    assert translations == {1: (0.0, 0.0, 0.0), 2: (1.0, 2.0, 3.0), 4: (5.0, 6.0, 7.0)}
    assert info["loaded"] and info["measured_slots"] == [1, 2, 4]
    assert model.translation_delta_um(translations, 2, 4) == (4.0, 4.0, 4.0)
    assert model.load_objective_calibration(enabled=False) == (
        None,
        {
            "enabled": False,
            "loaded": False,
            "path": None,
            "slots": [],
            "measured_slots": [],
        },
    )


# --- the limits' corners, and the data location --------------------------------------------


def test_the_corners_span_the_x_and_y_limits(scope):
    corners = CornerRecorder(scope.client)
    corners.record(1)
    scope.move_to(1300.0, -200.0, 500.0)
    corners.record(2)
    assert corners.limits()["limits"] == {
        "x_um": {"range": [1000.0, 1300.0]},
        "y_um": {"range": [-500.0, -200.0]},
    }
    with pytest.raises(ValueError, match="numbered"):
        corners.record(5)


def test_the_data_location_is_checked_saved_and_loaded(tmp_path):
    assert data_location.data_location() is None
    with pytest.raises(ValueError, match="does not exist"):
        data_location.set_data_location(tmp_path / "missing")
    with pytest.raises(ValueError, match="not a full path"):
        data_location.set_data_location("data")
    saved = data_location.set_data_location(tmp_path)
    assert json.loads(saved.read_text())["folder"] == str(tmp_path.resolve())
    assert data_location.data_location() == tmp_path.resolve()
