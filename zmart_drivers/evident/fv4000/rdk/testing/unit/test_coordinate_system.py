"""The frame arithmetic, and the stage read and moved in the frame the controller uses."""

import pytest
from limits_fixtures import provision_machine_limits

from zmart_drivers.evident.fv4000.rdk.driver_setup.origin.saved_origin import (
    empty_origin,
)
from zmart_drivers.evident.fv4000.rdk.procedures import (
    absolute_coordinate_system as frame,
)

ORIGIN = {
    "x_um": 100.0,
    "y_um": 200.0,
    "z_um": 50.0,
    "z_piezo_um": 0.0,
    "objective": {"slot": 0, "name": "10x"},
}


def test_a_frame_position_is_reading_minus_origin_minus_the_objective_offset():
    snapshot = {"x_um": 110.0, "y_um": 190.0, "z_um": 53.0}
    assert frame.frame_position(snapshot, ORIGIN, (0.0, 0.0, 0.0)) == {
        "x": 10.0,
        "y": -10.0,
        "z": 3.0,
    }
    assert frame.frame_position(snapshot, ORIGIN, (1.0, 2.0, 3.0)) == {
        "x": 9.0,
        "y": -12.0,
        "z": 0.0,
    }


def test_a_stage_target_is_the_frame_position_put_back():
    at = frame.stage_target(10.0, -10.0, 3.0, ORIGIN, (1.0, 2.0, 3.0))
    assert at == (111.0, 192.0, 56.0)
    assert frame.frame_position(
        {"x_um": at[0], "y_um": at[1], "z_um": at[2]}, ORIGIN, (1.0, 2.0, 3.0)
    ) == {"x": 10.0, "y": -10.0, "z": 3.0}


def test_the_canvas_is_the_travel_moved_by_the_origin():
    stage = {
        "x": [0.0, 1000.0],
        "y": [0.0, 500.0],
        "z": [0.0, 100.0],
        "z_piezo": [-1.0, 1.0],
    }
    assert frame.canvas_in_frame(stage, ORIGIN) == {
        "x_um": [-100.0, 900.0],
        "y_um": [-200.0, 300.0],
        "z_um": [-50.0, 50.0],
    }


def test_the_objective_offset_is_zero_for_the_origins_objective_and_the_calibrations_difference_otherwise():
    translations = {0: (0.0, 0.0, 0.0), 2: (5.0, -4.0, 1.5)}
    assert frame.delta_between_objectives_um(ORIGIN, translations, {"slot": 0}) == (
        0.0,
        0.0,
        0.0,
    )
    assert frame.delta_between_objectives_um(ORIGIN, translations, {"slot": 2}) == (
        5.0,
        -4.0,
        1.5,
    )
    assert frame.delta_between_objectives_um(empty_origin(), None, {"slot": 2}) == (
        0.0,
        0.0,
        0.0,
    )
    with pytest.raises(RuntimeError, match="no calibration translation covers"):
        frame.delta_between_objectives_um(ORIGIN, translations, {"slot": 1})
    with pytest.raises(RuntimeError, match="unknown"):
        frame.delta_between_objectives_um(ORIGIN, translations, None)


def test_get_xyz_reports_each_axis_in_the_controllers_shape(scope):
    answer = scope.position()
    for axis in ("x", "y", "z"):
        assert set(answer[axis]) == {
            "position",
            "unit",
            "used_actuator",
            "actuator_readings",
            "canvas",
        }
        assert answer[axis]["unit"] == "micrometer"
    assert answer["x"]["actuator_readings"] == {"stage": 10000.0}
    assert answer["z"]["actuator_readings"] == {"motor": 2500.0, "piezo": 0.0}
    assert answer["z"]["used_actuator"] == "motor"
    assert answer["x"]["canvas"] == [0.0, 60000.0] and answer[
        "objective_translation_um"
    ] == [0.0, 0.0, 0.0]


def test_get_actuators_lists_the_stage_and_the_motor(scope):
    motors = scope.motors()
    assert motors == {
        "x": {"options": ["stage"], "default": "stage"},
        "y": {"options": ["stage"], "default": "stage"},
        "z": {"options": ["motor"], "default": "motor"},
    }
    with pytest.raises(ValueError, match="unknown actuator"):
        scope.position(with_actuators={"z": "piezo"})


def test_set_xyz_moves_in_the_frame_and_reads_back_from_fluoview(scope, mock):
    scope.set_origin()
    answer = scope.move_to(25.0, -30.0, 4.0)
    assert (
        answer["x"]["position"],
        answer["y"]["position"],
        answer["z"]["position"],
    ) == (25.0, -30.0, 4.0)
    assert mock.at_rest() == (10_025_000, 7_970_000, 2_504_000)


def test_a_move_outside_the_limits_raises_and_moves_nothing(scope, mock):
    with pytest.raises(RuntimeError, match="refused"):
        scope.move_to(70000.0, 0.0, 0.0)
    assert mock.x_nm() == 10_000_000


def test_a_move_across_objectives_is_refused_without_a_calibration_and_compensated_with_one(
    scope, mock
):
    mock.travel_s = 0.0  # exact arithmetic is checked here, not the travel time
    scope.set_origin()
    mock.objective_slot = 2  # changed in FLUOVIEW, behind the driver's back
    with pytest.raises(RuntimeError, match="no calibration translation covers"):
        scope.move_to(0.0, 0.0, 0.0)
    scope.handle.translations = {0: (0.0, 0.0, 0.0), 2: (10.0, -6.0, 2.0)}
    answer = scope.move_to(0.0, 0.0, 0.0)
    assert mock.at_rest() == (10_010_000, 7_994_000, 2_502_000)
    assert (
        answer["x"]["position"],
        answer["y"]["position"],
        answer["z"]["position"],
    ) == (0.0, 0.0, 0.0)
    assert answer["objective_translation_um"] == [10.0, -6.0, 2.0]


def test_a_read_under_an_uncalibrated_objective_warns_but_answers(scope, mock, caplog):
    scope.set_origin()
    mock.objective_slot = 1
    answer = scope.position()
    assert answer["x"]["position"] == 0.0 and "UNCOMPENSATED" in caplog.text


def test_without_limits_the_canvas_is_the_position_alone(
    client, mock, monkeypatch, tmp_path
):
    from mock_fluoview import MockRdkClient

    from zmart_drivers.evident.fv4000.rdk import methods
    from zmart_drivers.evident.fv4000.rdk.dispatcher import limits as gate
    from zmart_drivers.evident.fv4000.rdk.primitives.rdk import client as rdk_client

    monkeypatch.setattr(
        rdk_client, "connect_client", lambda host, port, **_: MockRdkClient(mock)
    )
    provision_machine_limits(
        tmp_path
    )  # elsewhere: this session finds none and runs on the defaults
    scope = methods.connect(output_root=str(tmp_path))
    gate.uninstall(scope.client)
    assert scope.position()["x"]["canvas"] == [10000.0, 10000.0]
