"""The frame arithmetic, checked on paper, and the origin saved and loaded.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest

from zmart_drivers.nikon.ti2.nis_elements.driver_setup.origin import saved_origin
from zmart_drivers.nikon.ti2.nis_elements.driver_setup.store import MACHINE
from zmart_drivers.nikon.ti2.nis_elements.procedures import (
    absolute_coordinate_system as frame,
)

ORIGIN = {
    "x_um": 1000.0,
    "y_um": -500.0,
    "z_um": 500.0,
    "objective": {"slot": 1, "name": "Plan Apo 10x"},
}


def test_a_frame_position_is_the_reading_minus_the_origin_minus_the_objective_offset():
    snapshot = {"x_um": 1100.0, "y_um": -480.0, "z_um": 530.0}
    assert frame.frame_position(snapshot, ORIGIN, (10.0, -5.0, 2.0)) == {
        "x": 90.0,
        "y": 25.0,
        "z": 28.0,
    }
    assert frame.stage_target(90.0, 25.0, 28.0, ORIGIN, (10.0, -5.0, 2.0)) == (
        1100.0,
        -480.0,
        530.0,
    )


def test_the_canvas_is_the_envelope_shifted_by_the_origin():
    canvas = frame.canvas_in_frame(
        {"x": [-50000, 50000], "y": [-30000, 30000], "z": [0, 9000]}, ORIGIN
    )
    assert canvas == {
        "x_um": [-51000.0, 49000.0],
        "y_um": [-29500.0, 30500.0],
        "z_um": [-500.0, 8500.0],
    }


def test_the_objective_offset_is_zero_for_the_same_objective_and_the_difference_of_the_table_otherwise():
    table = {1: (0.0, 0.0, 0.0), 2: (12.5, -3.0, 41.0)}
    assert frame.delta_between_objectives_um(ORIGIN, table, {"slot": 1}) == (
        0.0,
        0.0,
        0.0,
    )
    assert frame.delta_between_objectives_um(ORIGIN, table, {"slot": 2}) == (
        12.5,
        -3.0,
        41.0,
    )
    assert frame.delta_between_objectives_um(ORIGIN, None, {"slot": 2}) == (
        0.0,
        0.0,
        0.0,
    )
    with pytest.raises(RuntimeError, match="no objective calibration covers"):
        frame.delta_between_objectives_um(ORIGIN, table, {"slot": 4})
    with pytest.raises(RuntimeError, match="unknown"):
        frame.delta_between_objectives_um(ORIGIN, table, None)


def test_the_origin_is_saved_and_loaded_and_a_damaged_one_stops_the_connection(scope):
    saved = scope.set_origin()
    assert saved["origin"] == {"x": 0.0, "y": 0.0, "z": 0.0}
    assert scope.position()["x"]["position"] == pytest.approx(0.0, abs=1e-6)
    loaded = saved_origin.load_saved_origin()
    assert loaded == {
        "x_um": 1000.0,
        "y_um": -500.0,
        "z_um": 500.0,
        "objective": {"slot": 1, "name": "Plan Apo 10x"},
    }

    MACHINE.latest_path("origin").write_text(
        '{"origin": {"x_um": 1}}', encoding="utf-8"
    )
    with pytest.raises(RuntimeError, match="no usable value"):
        saved_origin.load_saved_origin()


def test_without_a_saved_origin_positions_are_plain_stage_coordinates():
    assert saved_origin.load_saved_origin() is None
    assert saved_origin.empty_origin()["objective"] is None
