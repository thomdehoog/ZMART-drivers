"""The changes the ``dll`` class can send, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits gate, the
bridge request that carries it and how its arguments are filled in, the
label in the log, how long to wait for the bridge's answer, which reading
shows that it took effect and how close counts (:class:`Expect`), and whether
it may never be sent again. The dispatcher's ``run()`` does the rest. Nothing
here talks to the bridge.

The builders: :func:`move_xyz`, :func:`select_optical_configuration`,
:func:`set_objective`, :func:`set_pfs`, :func:`set_pfs_offset`,
:func:`autofocus`, :func:`snap`, :func:`set_light`, :func:`set_shutter`,
:func:`set_filter`, :func:`set_light_path`, :func:`set_port`,
:func:`move_piezo_z`, :func:`center_piezo_z`, :func:`escape` and
:func:`safe_state`.
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


# =============================================================================
# The PFS offset, the lights, the shutters, the filters
# =============================================================================

#: How close a value read back must come, where NIS may round what it was
#: given. Unknown on the instrument, so generous; to be checked on the Ti2.
VOLTAGE_TOLERANCE_V = 0.1
IRIS_TOLERANCE = 0.01
APERTURE_TOLERANCE = 0.5
ZOOM_TOLERANCE = 0.01
DISK_SPEED_TOLERANCE_RPM = 10.0


def _close(value, target, tolerance) -> bool:
    try:
        return abs(float(value) - float(target)) <= tolerance
    except (TypeError, ValueError):
        return False


def pfs_settings_are(offset=None, dish=None):
    """The PFS reading shows the offset and the dish asked for (those that were asked)."""

    def check(pfs) -> bool:
        try:
            return (offset is None or pfs["offset"] == offset) and (
                dish is None or pfs["dish"] == dish
            )
        except (KeyError, TypeError):
            return False

    return check


def set_pfs_offset(offset: int | None = None, dish: str | None = None) -> Write:
    """Set the plane the Perfect Focus System keeps (``offset``, in NIS's units) and the dish (``glass`` or ``plastic``)."""

    def fill(args):
        args.update(
            {
                key: value
                for key, value in (("offset", offset), ("dish", dish))
                if value is not None
            }
        )

    return Write(
        command="set_pfs_offset",
        values={"offset": offset, "dish": dish},
        request="set_pfs_offset",
        fill=fill,
        description=f"PFS offset {offset}, dish {dish}",
        confirm=Expect("pfs", pfs_settings_are(offset, dish)),
    )


def light_is(name, on=None, voltage=None, iris=None):
    """The lights reading shows *name* switched and set as asked (only what was asked)."""

    def check(lights) -> bool:
        try:
            light = lights[name]
            return (
                (on is None or light["on"] is on)
                and (
                    voltage is None
                    or _close(light["voltage"], voltage, VOLTAGE_TOLERANCE_V)
                )
                and (iris is None or _close(light["iris"], iris, IRIS_TOLERANCE))
            )
        except (KeyError, TypeError):
            return False

    return check


def set_light(
    name: str,
    *,
    on: bool | None = None,
    voltage: float | None = None,
    iris: float | None = None,
) -> Write:
    """Switch the lamp or LED *name* on or off, and set its voltage or iris, where NIS controls them."""

    def fill(args):
        args.update(
            name=name,
            **{
                k: v
                for k, v in (("on", on), ("voltage", voltage), ("iris", iris))
                if v is not None
            },
        )

    return Write(
        command="set_light",
        values={"name": name, "on": on, "voltage": voltage, "iris": iris},
        request="set_light",
        fill=fill,
        description=f"light {name!r}: on {on}, voltage {voltage}, iris {iris}",
        confirm=Expect("lights", light_is(name, on, voltage, iris)),
    )


def shutter_is(name, open_):
    def check(shutters) -> bool:
        try:
            return shutters[name] is open_
        except (KeyError, TypeError):
            return False

    return check


def set_shutter(name: str, open_: bool) -> Write:
    """Open or close the shutter *name*."""

    def fill(args):
        args.update(name=name, open=bool(open_))

    return Write(
        command="set_shutter",
        values={"name": name, "open": open_},
        request="set_shutter",
        fill=fill,
        description=f"shutter {name!r} {'open' if open_ else 'closed'}",
        confirm=Expect("shutters", shutter_is(name, open_)),
    )


def filter_at(changer, position):
    def check(filters) -> bool:
        try:
            return filters[changer]["position"] == position
        except (KeyError, TypeError):
            return False

    return check


def set_filter(changer: str, position: int) -> Write:
    """Turn the filter changer *changer* to *position*, counted from 1."""

    def fill(args):
        args.update(changer=changer, position=int(position))

    return Write(
        command="set_filter",
        values={"changer": changer, "position": position},
        request="set_filter",
        fill=fill,
        description=f"filter changer {changer!r} to position {position}",
        confirm=Expect("filters", filter_at(changer, position)),
    )


# =============================================================================
# The light path, the port and the spinning disk
# =============================================================================


def light_path_is(condenser=None, aperture=None, size=None, zoom=None, analyzer=None):
    """The light-path reading shows what was asked (only what was asked)."""

    def check(path) -> bool:
        try:
            return (
                (condenser is None or path["condenser"]["position"] == condenser)
                and (
                    aperture is None
                    or _close(
                        path["apertures"][aperture]["size"], size, APERTURE_TOLERANCE
                    )
                )
                and (zoom is None or _close(path["zoom"]["zoom"], zoom, ZOOM_TOLERANCE))
                and (analyzer is None or path["analyzer"]["inserted"] is analyzer)
            )
        except (KeyError, TypeError):
            return False

    return check


def set_light_path(
    *,
    condenser: int | None = None,
    aperture: str | None = None,
    size: float | None = None,
    zoom: float | None = None,
    analyzer: bool | None = None,
) -> Write:
    """Set the condenser position, an aperture's size (``field``, ``epi_field`` or ``dia``), the zoom, or the analyzer."""
    given = {
        "condenser": condenser,
        "aperture": aperture,
        "size": size,
        "zoom": zoom,
        "analyzer": analyzer,
    }

    def fill(args):
        args.update({key: value for key, value in given.items() if value is not None})

    return Write(
        command="set_light_path",
        values=dict(given),
        request="set_light_path",
        fill=fill,
        description=f"light path: {', '.join(f'{k} {v}' for k, v in given.items() if v is not None)}",
        confirm=Expect(
            "light_path", light_path_is(condenser, aperture, size, zoom, analyzer)
        ),
    )


def port_is(port=None, disk_shutter=None, disk_speed_rpm=None):
    def check(path) -> bool:
        try:
            return (
                (port is None or path["port"]["position"] == port)
                and (
                    disk_shutter is None or path["disk"]["shutter_open"] is disk_shutter
                )
                and (
                    disk_speed_rpm is None
                    or _close(
                        path["disk"]["speed_rpm"],
                        disk_speed_rpm,
                        DISK_SPEED_TOLERANCE_RPM,
                    )
                )
            )
        except (KeyError, TypeError):
            return False

    return check


def set_port(
    *,
    port: int | None = None,
    disk_shutter: bool | None = None,
    disk_speed_rpm: int | None = None,
) -> Write:
    """Send the light to a port, and open or close the spinning disk's shutter or set its speed."""
    given = {
        "port": port,
        "disk_shutter": disk_shutter,
        "disk_speed_rpm": disk_speed_rpm,
    }

    def fill(args):
        args.update({key: value for key, value in given.items() if value is not None})

    return Write(
        command="set_port",
        values=dict(given),
        request="set_port",
        fill=fill,
        description=f"port and disk: {', '.join(f'{k} {v}' for k, v in given.items() if v is not None)}",
        confirm=Expect("light_path", port_is(port, disk_shutter, disk_speed_rpm)),
    )


# =============================================================================
# The piezo Z drive, escape, and the safe state
# =============================================================================


def move_piezo_z(distance_um: float) -> Write:
    """Move the piezo Z drive by *distance_um*, relative to where it is.

    NIS cannot read the piezo's position back, so there is nothing to
    confirm. A relative move sent twice would move twice, so it is never
    sent again.
    """

    def fill(args):
        args.update(distance_um=float(distance_um))

    return Write(
        command="move_piezo_z",
        values={"distance_um": distance_um},
        request="move_piezo_z",
        fill=fill,
        description=f"piezo Z by {distance_um:g} um",
        never_resend=True,
    )


def center_piezo_z() -> Write:
    """Bring the piezo Z drive back to the middle of its travel, keeping the focus."""

    def fill(args):
        args.update(center=True)

    return Write(
        command="center_piezo_z",
        values={},
        request="move_piezo_z",
        fill=fill,
        description="piezo Z to the middle of its travel",
    )


def escape(on: bool) -> Write:
    """Lower the objective away from the sample (``on`` True), or bring it back to the focus it left.

    The bridge answers whether the objective is escaped; there is no reading
    of it to wait for, so its answer is the record.
    """

    def fill(args):
        args.update(on=bool(on))

    return Write(
        command="escape",
        values={"on": on},
        request="escape",
        fill=fill,
        description="escape: the objective lowered away"
        if on
        else "refocus: the objective back to its focus",
    )


def safe_state() -> Write:
    """Switch off everything that sends light to the sample: every laser line, every shutter and the disk shutter, every lamp.

    The bridge tries every part even when one fails, and answers what it
    switched off and what failed. Sending it again does no harm.
    """
    return Write(
        command="safe_state",
        values={},
        request="safe_state",
        fill=lambda args: None,
        description="safe state: every light source off",
    )
