"""The changes NIS-Elements can be asked for through the bridge, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits gate, how
it travels (one NIS function as a ``write``, or the whole capture as a
``snap``) and how its arguments are filled in, the label in the log, and what
belongs to this change alone: which reading shows that it took effect and how
close counts (:class:`Expect`), and whether it may never be sent again. The
dispatcher's ``run()`` does the rest. Nothing here calls NIS or the dispatcher.

Each change has one name everywhere, here, in the limits and in the log:
``move_xy``, ``move_z``, ``select_optical_configuration``, ``set_objective``
and ``capture``. The checks that say how close counts (:func:`within_xy`,
:func:`z_within`, :func:`names`, :func:`position_is`) each take one reading's
value and answer True or False.

The moves pass ``0`` as their last argument: absolute coordinates, as step 0
of the NIS 7 study sent them. Should a microscope read that argument
differently, the read-back refuses to confirm the move, and the driver says so
rather than carrying on from a wrong position.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import devices

#: How close a move must come, in micrometres, unless the caller says.
XY_TOLERANCE_UM = 1.0
Z_TOLERANCE_UM = 0.5

#: The last argument of ``StgMoveXY`` and ``StgMoveZ``: absolute coordinates.
ABSOLUTE = 0


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str
    accept: Callable[[object], bool]
    about: str | None = None
    mode: str | None = None
    fallback: str | None = None
    without_limit: bool = False


@dataclass(frozen=True)
class Write:
    """One change: how it travels, its limits, and what belongs to it alone.

    ``op`` is ``write`` (one NIS function, ``request``) or ``snap`` (the whole
    capture, done by the bridge). ``dr_code`` says whether the function answers
    a ``DR_`` code, which the error check reads.
    """

    command: str
    values: dict
    request: str
    fill: Callable
    description: str
    op: str = "write"
    ready: Expect | None = None
    confirm: Expect | None = None
    without_receipt: bool = False
    never_resend: bool = False
    dr_code: bool = True


# =============================================================================
# What counts as the change having happened
# =============================================================================


def within_xy(x_um, y_um, tolerance_um):
    """A stage reading within ``tolerance_um`` of ``(x_um, y_um)`` on both axes."""

    def check(position) -> bool:
        try:
            return (
                abs(position["x_um"] - x_um) <= tolerance_um
                and abs(position["y_um"] - y_um) <= tolerance_um
            )
        except (KeyError, TypeError):
            return False

    return check


def z_within(z_um, tolerance_um):
    """A focus reading within ``tolerance_um`` of ``z_um``."""

    def check(position) -> bool:
        try:
            return abs(position["z_um"] - z_um) <= tolerance_um
        except (KeyError, TypeError):
            return False

    return check


def names(name):
    """A selected optical configuration with ``name`` among its texts."""

    def check(selected) -> bool:
        try:
            return name in selected["texts"]
        except (KeyError, TypeError):
            return False

    return check


def position_is(position):
    """A nosepiece reading whose objective is in ``position``."""

    def check(nosepiece) -> bool:
        try:
            return nosepiece["position"] == position
        except (KeyError, TypeError):
            return False

    return check


# =============================================================================
# The changes
# =============================================================================


def move_xy(x_um: float, y_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the stage to ``(x_um, y_um)``, absolute, and read it back within the tolerance."""
    tolerance = XY_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(request):
        request.args = [float(x_um), float(y_um), ABSOLUTE]

    return Write(
        command="move_xy",
        values={"x_um": x_um, "y_um": y_um},
        request="StgMoveXY",
        fill=fill,
        description=f"StgMoveXY -> ({x_um:g}, {y_um:g}) um",
        confirm=Expect("xy", within_xy(x_um, y_um, tolerance)),
    )


def move_z(z_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the focus drive to ``z_um``, absolute, and read it back within the tolerance."""
    tolerance = Z_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(request):
        request.args = [float(z_um), ABSOLUTE]

    return Write(
        command="move_z",
        values={"z_um": z_um},
        request="StgMoveZ",
        fill=fill,
        description=f"StgMoveZ -> {z_um:g} um",
        confirm=Expect("z", z_within(z_um, tolerance)),
    )


def select_optical_configuration(name: str) -> Write:
    """Select the optical configuration ``name``, and read the selection back. Sent once.

    It is sent once because NIS may leave the selected name blank (step 0 saw
    three empty texts with the microscope off): sending it again would not
    help, and the change is then recorded as unconfirmed.
    """

    def fill(request):
        request.args = [str(name)]

    return Write(
        command="select_optical_configuration",
        values={"optical_configuration": name},
        request="SelectOptConf",
        fill=fill,
        description=f"SelectOptConf '{name}'",
        confirm=Expect("selected_optical_configuration", names(name)),
        never_resend=True,
        dr_code=False,
    )


def set_objective(position: int) -> Write:
    """Turn the nosepiece to ``position`` (counted from 1), and read it back."""

    def fill(request):
        request.args = [int(position)]

    return Write(
        command="set_objective",
        values={"objective_position": position},
        request="Stg_SetNosepiecePosition",
        fill=fill,
        description=f"Stg_SetNosepiecePosition -> {position}",
        confirm=Expect("nosepiece", position_is(position)),
    )


def capture(path: str) -> Write:
    """Take one picture and save it as a TIFF at ``path``: the bridge's ``snap``. Never sent again.

    The snap's own answer is the proof (a new document appeared, and the file
    was written), so there is no reading to confirm it with.
    """

    def fill(request):
        request.args = [str(path)]

    return Write(
        command="capture",
        values={"path": path},
        request="snap",
        op="snap",
        fill=fill,
        description=f"Capture -> {path}",
        never_resend=True,
        dr_code=False,
    )


def set_device(key: str, instance: str | None, value) -> Write:
    """Set one hardware setting of the table (``devices.py``) to *value*, and read it back.

    The value must already be checked (``devices.checked_value``). The
    reading that confirms it is the row's own reading, ``device/<key>`` or
    ``device/<key>/<device>``, the mirror of the change. A setting that only
    moves towards its target (a temperature) is sent and not read back.
    Setting the same value twice does no harm, so a setting is sent again
    when it was not seen to take effect.
    """
    row = devices.setting(key)
    arguments = devices.write_arguments(row, instance, value)
    reading = f"device/{key}" + (f"/{instance}" if instance else "")

    def fill(request):
        request.args = list(arguments)

    def shows(found) -> bool:
        return devices.matches(row, value, found)

    return Write(
        command="set_device",
        values={"device": key, "instance": instance, "value": value},
        request=row.write.fn,
        fill=fill,
        description=f"{row.write.fn} -> {reading.removeprefix('device/')} = {value}",
        confirm=Expect(reading, shows) if row.confirms else None,
    )
