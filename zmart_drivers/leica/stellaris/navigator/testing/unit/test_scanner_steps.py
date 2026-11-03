"""LCS's older COM scanner, opened only as far as the pan's steps: only those reach it, at 1 or 3 percent.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator.primitives.direct import scanner


class Com:
    """The COM scanner: what reaches it is recorded."""

    def __init__(self):
        self.moves, self.PanPercentage = [], 3

    def MoveInDirection(self, direction):  # noqa: N802 -- the COM object's own name
        self.moves.append(direction)


def test_the_steps_go_to_the_pads_directions_and_the_centre():
    com = Com()
    steps = scanner.PanSteps(com)

    for direction in ("up-left", "up", "up-right", "right", "down-right", "down", "down-left", "left", "centre"):
        steps.step(direction)

    assert com.moves == [0, 1, 2, 3, 4, 5, 6, 7, 8]


@pytest.mark.parametrize("direction", ["sideways", 3, "Scanspeed"])
def test_any_other_direction_is_refused(direction):
    com = Com()

    with pytest.raises(ValueError):
        scanner.PanSteps(com).step(direction)
    assert com.moves == []


@pytest.mark.parametrize("value", [0, 4, 5, 1.5, True])
def test_a_step_other_than_1_to_3_percent_is_refused(value):
    com = Com()

    with pytest.raises(ValueError):
        scanner.PanSteps(com).set_percent(value)
    assert com.PanPercentage == 3


def test_the_step_it_had_is_put_back_whatever_it_was():
    com = Com()
    com.PanPercentage = 5
    steps = scanner.PanSteps(com)
    before = steps.percent()
    steps.set_percent(1)

    steps.set_percent(before, putting_back=True)

    assert com.PanPercentage == 5


def test_nothing_else_of_the_scanner_is_within_reach():
    steps = scanner.PanSteps(Com())

    assert not any(hasattr(steps, name) for name in ("Scanspeed", "Zoom", "StartSingleScan", "MoveInDirection", "com", "scanner"))
