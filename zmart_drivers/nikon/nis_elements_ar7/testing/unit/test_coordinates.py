"""Positions in micrometres from the saved origin, and the canvas: where pictures can be taken.

The stage reports NIS's own numbers. The driver subtracts the saved origin,
so that 0, 0, 0 is the spot the operator chose. Moving adds the origin back
before anything is sent, and both targets are put to the limits first, so a
move whose focus is out of bounds does not carry the stage away first.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest

from zmart_drivers.nikon.nis_elements_ar7.procedures import (
    absolute_coordinate_system as frame,
)


def test_positions_are_counted_from_the_origin():
    origin = {"x_um": 100.0, "y_um": -50.0, "z_um": 3000.0, "objective": None}
    assert frame.frame_position(
        {"x_um": 110.0, "y_um": -40.0, "z_um": 3005.0}, origin
    ) == {"x": 10.0, "y": 10.0, "z": 5.0}
    assert frame.stage_target(10.0, 10.0, 5.0, origin) == {
        "x_um": 110.0,
        "y_um": -40.0,
        "z_um": 3005.0,
    }


def test_a_move_lands_where_it_was_asked_and_is_read_back(scope, stand_in):
    stand_in.scope.place(200.0, 300.0, 3000.0)
    scope.set_origin()
    answer = scope.move_to(15.0, -25.0, 10.0)
    assert {a: answer[a]["position"] for a in "xyz"} == pytest.approx(
        {"x": 15.0, "y": -25.0, "z": 10.0}
    )
    assert stand_in.scope.position() == pytest.approx((215.0, 275.0, 3010.0))
    assert answer["x"]["actuator_readings"] == {"motoric": pytest.approx(215.0)}


def test_a_move_with_its_focus_out_of_bounds_moves_nothing(scope, stand_in):
    with pytest.raises(RuntimeError, match="nothing moved"):
        scope.move_to(1100.0, 2100.0, 9000.0)
    assert "StgMoveXY" not in stand_in.scope.calls
    assert stand_in.scope.position() == pytest.approx((1000.0, 2000.0, 3000.0))


def test_a_position_must_be_a_number(scope):
    for bad in ("1", None, True):
        with pytest.raises(ValueError):
            scope.move_to(bad, 0, 3000)


def test_the_canvas_is_the_limits_widened_by_half_a_field(scope):
    before = scope.position()
    assert before["z"]["canvas"] == [2900.0, 3100.0]
    assert before["x"]["canvas"] == [
        -50000.0,
        50000.0,
    ]  # no picture yet, so no field of view is known
    scope.acquire("first")
    after = scope.position()
    half = 64 * 0.65 / 2
    assert after["x"]["canvas"] == pytest.approx([-50000.0 - half, 50000.0 + half])


def test_each_axis_has_one_motor(scope):
    motors = scope.motors()
    assert all(
        motors[axis]["options"] == ["motoric"] and motors[axis]["default"] == "motoric"
        for axis in "xyz"
    )
    with pytest.raises(ValueError):
        scope.position({"x": "piezo"})
