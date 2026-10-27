"""The changes the ``dll`` class can send, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits gate, the
bridge request that carries it and how its arguments are filled in, the
label in the log, how long to wait for the bridge's answer, which reading
shows that it took effect and how close counts (:class:`Expect`), and whether
it may never be sent again. The dispatcher's ``run()`` does the rest. Nothing
here talks to the bridge.

The builders: :func:`move_xyz`, :func:`select_optical_configuration`,
:func:`set_objective`, :func:`set_pfs`, :func:`autofocus` and :func:`snap`.
The checks that say how close counts (:func:`within_xyz`, :func:`slot_is`,
:func:`pfs_is`) each take one reading's value and answer True or False, so
each can be tested alone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# How long the driver waits for the bridge to answer each kind of request.
# An ordinary request is answered in milliseconds; a capture saves a file and
# a focus sweep moves the focus through its whole range, so each gets its own
# time. The numbers follow the bridge's own settings (nis_bridge/settings.py).
REQUEST_TIMEOUT_S = 30.0
SNAP_TIMEOUT_S = 120.0
FOCUS_TIMEOUT_S = 300.0
PFS_TIMEOUT_S = 60.0

#: How close a move must come, in micrometres, unless the caller says. NIS
#: reports the position it arrived at; the stage settles within this.
XYZ_TOLERANCE_UM = 1.0

#: NIS's image-based focus sweep: the range around the current focus and its
#: speed (0 slow and thorough, up to 90), when a caller does not choose.
AUTOFOCUS_RANGE_UM = 50.0
AUTOFOCUS_SPEED = 30
AUTOFOCUS_MAX_SPEED = 90


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str  # "xyz", "objectives", "pfs", "camera"
    accept: Callable[[object], bool]  # how close counts
    about: str | None = (
        None  # what the reading is about; this driver's readings are about nothing
    )
    mode: str | None = None  # the class asked first; None = the reading's own
    fallback: str | None = None
    without_limit: bool = False  # waited for as long as it takes, not the windows


@dataclass(frozen=True)
class Write:
    """One change: its request, its limits, and what belongs to it alone."""

    command: str  # its name in the limits (limit_rules.MUTATING_COMMANDS)
    values: dict  # what the limits gate is shown
    request: str  # the bridge's request name, such as "move"
    fill: Callable  # fill(args): writes the values into the request's arguments
    description: str
    timeout_s: float = REQUEST_TIMEOUT_S  # how long to wait for the bridge's answer
    ready: Expect | None = None  # waited for before every send; the bridge needs none
    confirm: Expect | None = None  # read back, one window at a time
    without_receipt: bool = False  # the bridge always answers, so never
    never_resend: bool = False


# =============================================================================
# The stage
# =============================================================================


def within_xyz(x_um, y_um, z_um, tolerance_um):
    """A stage position within ``tolerance_um`` of the target on every axis."""

    def check(position) -> bool:
        try:
            return (
                abs(position["x_um"] - x_um) <= tolerance_um
                and abs(position["y_um"] - y_um) <= tolerance_um
                and abs(position["z_um"] - z_um) <= tolerance_um
            )
        except (KeyError, TypeError):
            return False

    return check


def move_xyz(
    x_um: float, y_um: float, z_um: float, *, tolerance_um: float | None = None
) -> Write:
    """Move the stage and the focus drive to an absolute position, in micrometres.

    The bridge moves all three axes in one request and answers with the
    position it read back; the dispatcher confirms the move by reading the
    position again, within ``tolerance_um`` (``XYZ_TOLERANCE_UM`` when None).
    An unconfirmed absolute move is sent again: it is the same move.
    """
    tolerance = XYZ_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(args):
        args.update(x=float(x_um), y=float(y_um), z=float(z_um))

    return Write(
        command="move_xyz",
        values={"x_um": x_um, "y_um": y_um, "z_um": z_um},
        request="move",
        fill=fill,
        description=f"move to ({x_um:g}, {y_um:g}, {z_um:g}) um",
        confirm=Expect("xyz", within_xyz(x_um, y_um, z_um, tolerance)),
    )


# =============================================================================
# The optical configuration, the objective, the focus
# =============================================================================


def select_optical_configuration(name: str) -> Write:
    """Select the optical configuration *name*: the filter cube, light source, exposure of one channel.

    NIS-Elements offers no way to read back which configuration is selected,
    so this change has no read-back: it is sent and accepted, and the driver
    remembers what it selected. Sending it again does no harm, so a
    temporary error is retried.
    """

    def fill(args):
        args.update(name=str(name))

    return Write(
        command="select_optical_configuration",
        values={"name": name},
        request="select_optical_configuration",
        fill=fill,
        description=f"select optical configuration {name!r}",
    )


def slot_is(slot: int):
    """The nosepiece reading shows *slot* as the current objective."""

    def check(objectives) -> bool:
        try:
            return int(objectives["current"]) == int(slot)
        except (KeyError, TypeError, ValueError):
            return False

    return check


def set_objective(slot: int) -> Write:
    """Turn the nosepiece to *slot* (counted from 1), without moving the stage."""

    def fill(args):
        args.update(position=int(slot))

    return Write(
        command="set_objective",
        values={"objective_slot": slot},
        request="set_objective",
        fill=fill,
        description=f"objective to slot {slot}",
        confirm=Expect("objectives", slot_is(slot)),
    )


def pfs_is(on: bool):
    """The PFS reading shows it switched on (and, when on, in focus) or off."""

    def check(pfs) -> bool:
        try:
            if on:
                return bool(pfs["on"]) and pfs.get("status") == 1
            return not pfs["on"]
        except (KeyError, TypeError):
            return False

    return check


def set_pfs(on: bool, *, timeout_s: float = PFS_TIMEOUT_S) -> Write:
    """Switch the Perfect Focus System on (and wait for it to lock) or off.

    Switching on is confirmed by NIS reporting status 1, "on, focused".
    Switching it again is harmless, so an unconfirmed switch is sent again.
    """

    def fill(args):
        args.update(on=bool(on))

    return Write(
        command="set_pfs",
        values={"on": on},
        request="set_pfs",
        fill=fill,
        description=f"PFS {'on' if on else 'off'}",
        timeout_s=timeout_s,
        confirm=Expect("pfs", pfs_is(on)),
    )


def autofocus(
    range_um: float = AUTOFOCUS_RANGE_UM, speed: int = AUTOFOCUS_SPEED
) -> Write:
    """Run NIS's image-based focus sweep over *range_um* around the current focus.

    The sweep moves the focus and ends where the picture was sharpest, so it
    is never sent twice: a second sweep would start from the first one's
    answer. The bridge's answer is the position afterwards; there is nothing
    to read back against.
    """

    def fill(args):
        args.update(range_um=float(range_um), speed=int(speed))

    return Write(
        command="autofocus",
        values={"range_um": range_um, "speed": speed},
        request="autofocus",
        fill=fill,
        description=f"autofocus over {range_um:g} um at speed {speed}",
        timeout_s=FOCUS_TIMEOUT_S,
        never_resend=True,
    )


# =============================================================================
# The capture
# =============================================================================


def snap(path: str) -> Write:
    """Capture one picture with the current settings and save it as a TIFF at *path*.

    NIS captures, saves the file, and closes the picture it opened. The
    bridge answers with the path and the pixel size NIS has for the objective
    in use (None when it has none). A capture is sent once and never again:
    a second send would be a second picture.
    """

    def fill(args):
        args.update(path=str(path))

    return Write(
        command="snap",
        values={"path": str(path)},
        request="snap",
        fill=fill,
        description=f"snap to {path}",
        timeout_s=SNAP_TIMEOUT_S,
        never_resend=True,
    )
