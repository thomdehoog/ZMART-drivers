"""The orientation measurement finds how the pretend scanner is mounted, from three pictures and two moves."""

import pytest

from zmart_drivers.evident.fv4000.rdk.driver_setup.orientation import (
    Orientation,
    load_rig_orientation,
    measure_orientation,
)


@pytest.mark.parametrize("mounted", [(0, False), (90, False), (180, True), (270, True)])
def test_the_measurement_recovers_how_the_scanner_is_mounted(scope, mock, mounted):
    mock.orientation = mounted
    home = scope.position()
    result = measure_orientation.measure(scope.handle)
    assert result.problems == [], measure_orientation.describe(result)
    assert result.orientation == Orientation(*mounted)
    after = scope.position()
    assert all(after[a]["position"] == home[a]["position"] for a in ("x", "y", "z")), (
        "the stage is brought back"
    )
    assert scope.handle.unsaved == [], (
        "the setup pictures are not left waiting to be saved"
    )


def test_a_clean_measurement_is_adopted_and_loaded_at_the_next_connect(scope, mock):
    mock.orientation = (90, False)
    result = measure_orientation.measure(scope.handle, step_um=60.0)
    adopted = measure_orientation.adopt_orientation(result)
    assert (
        adopted["orientation"]["rotation_deg"] == 90
        and adopted["orientation"]["measured"] is True
    )
    orientation, info = load_rig_orientation()
    assert orientation == Orientation(90) and info["measured"]


def test_a_measurement_with_problems_is_not_adopted():
    result = measure_orientation.OrientationMeasurement(
        step_um=1.0, pixel_size_um=(1.0, 1.0), home={}, problems=["no beads"]
    )
    with pytest.raises(RuntimeError, match="no beads"):
        measure_orientation.adopt_orientation(result)


def test_the_shifts_of_the_two_moves_give_the_mapping_or_say_why_not():
    assert measure_orientation.mapping_from_shifts((64.0, 1.0), (-2.0, 63.0)) == [
        [1, 0],
        [0, 1],
    ]
    assert measure_orientation.mapping_from_shifts((0.0, -64.0), (64.0, 0.0)) == [
        [0, -1],
        [1, 0],
    ]
    with pytest.raises(ValueError, match="same picture axis"):
        measure_orientation.mapping_from_shifts((64.0, 0.0), (-64.0, 0.0))


def test_phase_correlation_finds_a_known_shift():

    from mock_fluoview import make_field

    field = make_field(3)
    a = field[100:356, 100:356]
    b = field[
        110:366, 130:386
    ]  # the scene moved 30 columns left and 10 rows up in the picture
    (dx, dy), ratio = measure_orientation.phase_shift(a, b)
    assert (dx, dy) == (-30.0, -10.0) and ratio > measure_orientation.MIN_PEAK_RATIO
