"""Tests for the XY limits measured at four corners of the stage.

The operator drives the stage to each safe corner (joystick or LAS X) and
records it in the limits notebook. The procedure reads the stage position
from the API at each corner and computes the inclusive rectangle; it never
touches the LAS X scanning template.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures import (
    measure_limits as adaptive,
)

POINTS = [
    {"x_um": 10_000.0, "y_um": 20_000.0},
    {"x_um": 30_000.0, "y_um": 20_100.0},
    {"x_um": 29_900.0, "y_um": 40_000.0},
    {"x_um": 10_100.0, "y_um": 39_900.0},
]


def test_xy_limits_are_the_four_point_bounding_box():
    assert adaptive.xy_limits_from_points(POINTS) == {
        "x_um": {"range": [10_000.0, 30_000.0]},
        "y_um": {"range": [20_000.0, 40_000.0]},
    }


def test_xy_limits_allow_points_below_the_conservative_default_margin():
    measured = [dict(point) for point in POINTS]
    measured[0]["y_um"] = 964.8553

    limits = adaptive.xy_limits_from_points(measured)

    assert limits["y_um"] == {"range": [964.8553, 40_000.0]}


def test_xy_limits_refuse_points_outside_the_maximum_stage_envelope():
    outside = [dict(point) for point in POINTS]
    outside[0]["x_um"] = -1.0
    with pytest.raises(RuntimeError, match="maximum stage envelope"):
        adaptive.xy_limits_from_points(outside)


def _stage_at(positions):
    """Patch the stage reading to answer *positions* in turn; returns (patch, calls)."""
    answers = iter(positions)
    calls = []

    def get_xy(client, **kwargs):
        calls.append(kwargs)
        return next(answers)

    return patch.object(adaptive._readers, "get_xy", side_effect=get_xy), calls


def test_four_recorded_corners_give_the_limits():
    stage, calls = _stage_at([{**point, "x": 0.0, "y": 0.0} for point in POINTS])
    corners = adaptive.CornerRecorder(object())
    with stage:
        for number in (1, 2, 3, 4):
            corners.record(number)
    captured = corners.limits()

    assert captured["points_um"] == POINTS
    assert captured["limits"] == {
        "x_um": {"range": [10_000.0, 30_000.0]},
        "y_um": {"range": [20_000.0, 40_000.0]},
    }
    assert [call.get("mode") for call in calls] == ["api"] * 4  # saved for good: the API


def test_recording_a_corner_again_replaces_it():
    moved = {"x_um": 9_000.0, "y_um": 20_000.0}
    stage, _ = _stage_at([*POINTS, moved])
    corners = adaptive.CornerRecorder(object())
    with stage:
        for number in (1, 2, 3, 4):
            corners.record(number)
        corners.record(1)
    assert corners.limits()["points_um"][0] == moved
    assert corners.limits()["limits"]["x_um"] == {"range": [9_000.0, 30_000.0]}


def test_the_limits_need_all_four_corners():
    stage, _ = _stage_at(POINTS[:3])
    corners = adaptive.CornerRecorder(object())
    with stage:
        for number in (1, 2, 3):
            corners.record(number)
    with pytest.raises(RuntimeError, match="corner 4 is not recorded"):
        corners.limits()


@pytest.mark.parametrize("number", [0, 5, "1"])
def test_a_corner_is_numbered_1_to_4(number):
    with pytest.raises(ValueError, match="corner number must be 1, 2, 3 or 4"):
        adaptive.CornerRecorder(object()).record(number)


def test_an_unreadable_stage_position_is_refused_not_recorded():
    stage, _ = _stage_at([None])
    corners = adaptive.CornerRecorder(object())
    with stage, pytest.raises(RuntimeError, match="could not read the stage position"):
        corners.record(1)
    with pytest.raises(RuntimeError, match="corner 1 is not recorded"):
        corners.limits()


def test_measuring_the_limits_never_touches_the_scanning_template():
    """Positions are never made in the Navigator Expert, for the setup neither."""
    for name in (
        "capture_adaptive_xy_limits",
        "boundary_points_from_template",
        "save_experiment",
        "parse_scan_positions",
    ):
        assert not hasattr(adaptive, name), name
