"""LCS's older COM scanner, opened only as far as the pan's steps.

LCS, LAS X's hardware server, has beside its commands (``SendCommand2``) an
older COM automation interface, listed in its registered type library. Its
``Scanner`` object moves the pan as the window's pan pad does:
``MoveInDirection`` takes one step of ``PanPercentage`` percent of the field
shown, in one of the pad's directions, or back to the centre (simulator,
2026-11-03: a 3 % step was 34.89 micrometres with the 10x at zoom 1, a 1 %
step 0.242 micrometres at zoom 48; the window's own arrows kept in step;
nothing was logged and no state-tree value changed). ``PanPercentage`` takes
whole numbers only.

The same object also sets the scan speed, the zoom and the scan size and
starts scans, outside the driver's limits, and a wrong value there can harm
the scanner. So this module keeps the object inside closures and offers only
the pad's eight directions, the centre, and a step of 1 to 3 percent (and
putting back the step it had). LAS X keeps the pan inside its window
whatever is asked (``procedures/pan.py``). Thom de Hoog allowed it on the
simulator first and then on the microscope (3 November 2026).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from . import client as hardware_server

#: The pad's nine directions as the COM scanner numbers them (its ``EDirection``: eTopLeft 0 ... eCenter 8), named as
#: the window's pad names its buttons.
DIRECTIONS = {"up-left": 0, "up": 1, "up-right": 2, "right": 3, "down-right": 4, "down": 5, "down-left": 6, "left": 7, "centre": 8}

#: The steps allowed, in percent of the field shown (LCS keeps 3).
PERCENTS = (1, 2, 3)


class PanSteps:
    """The pan's steps through the COM scanner; nothing else of it is within reach."""

    def __init__(self, com=None):
        com = com if com is not None else hardware_server.attach().Scanner

        def move(direction: int) -> None:
            com.MoveInDirection(direction)

        def read() -> int:
            return int(com.PanPercentage)

        def write(value: int) -> None:
            com.PanPercentage = value

        self.move, self.read, self.write = move, read, write

    def step(self, direction: str) -> None:
        """One step in one of the pad's directions (``"up"``, ``"up-right"`` ...), or the pan back to the ``"centre"``."""
        if not isinstance(direction, str) or direction not in DIRECTIONS:
            raise ValueError(f"the pan steps {sorted(DIRECTIONS)}, not {direction!r}")
        self.move(DIRECTIONS[direction])

    def percent(self) -> int:
        """The step now, in percent of the field shown."""
        return self.read()

    def set_percent(self, value: int, *, putting_back: bool = False) -> None:
        """The step to 1, 2 or 3 percent; with *putting_back*, back to the step read before, whatever it was."""
        allowed = isinstance(value, int) and not isinstance(value, bool) and (putting_back or value in PERCENTS)
        if not allowed:
            raise ValueError(f"the pan's step is {', '.join(map(str, PERCENTS))} percent, not {value!r}")
        self.write(value)
        if self.read() != value:
            raise RuntimeError(f"the pan's step was set to {value} percent, but LCS holds {self.read()}")
