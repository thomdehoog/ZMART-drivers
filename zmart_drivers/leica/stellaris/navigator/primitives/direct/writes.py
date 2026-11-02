"""The changes LAS X's plain Navigator takes, as plain data the dispatcher runs, and the elements that carry them.

A change is one :class:`Write`: its name and values for the limits, the
request that carries it and how that request is filled in, the label in the
log, and what belongs to this change alone: what to wait for before it is
sent (:class:`Expect` the scanner idle), which reading shows that it took
effect and how close counts, and whether it may never be sent again. The
dispatcher's ``run()`` does the rest. Nothing here calls LAS X or the
dispatcher.

A request here is a short list of XML elements, the same elements LAS X's
own window sends to its hardware server, read from ``lcsCommand.log`` while
the window made the change (simulator, 2026-10-15) and checked on the
STELLARIS the same day. With LAS X showing its plain Navigator there is no
CAM API and there are no jobs:

- the current settings are job 0 (``BlockId="0"``): zoom, scan speed, scan
  field rotation, line average and pinhole, and the z drives. Without a
  ``BlockId`` LAS X refuses; job 1 ("Default") holds no settings;
- the stage moves with ``SetStagePositionInHwOnly``;
- the objective changes with the window's own ``SetSlotPosition``;
- a capture is the window's Capture Image, and a tile scan over a list of
  positions its Preview. Each names the LAS X project its picture goes into
  (``DCParentElementID``); the STELLARIS refused a tile scan sent with ``0``.

The builders: :func:`setting` for the five settings (one table,
``SETTINGS``), :func:`move_xy`, :func:`move_z`, :func:`set_objective`,
:func:`capture`, :func:`tile_scan` and :func:`after_tile_scan`. Each hands
the limits gate its ``command`` and ``values``, so nothing moves or changes
before this microscope's limits allowed it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

from . import reads as direct_reader

# =============================================================================
# A write, what it waits for, and the request that carries it
# =============================================================================


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str  # a name the profile knows: "xy", "z", "scan_status", ...
    accept: Callable[[object], bool]  # how close counts
    about: str | None = None  # what the reading is about: a z drive's name
    mode: str | None = None  # the class asked first; None takes the profile's
    fallback: str | None = None
    without_limit: bool = False  # waited for as long as it takes, not the windows


@dataclass(frozen=True)
class Write:
    """One change: its request, its limits, and what belongs to it alone."""

    command: str  # its name in the limits (limit_rules.MUTATING_COMMANDS)
    values: dict  # what the limits gate is shown
    request: str  # the name of the LAS X command the elements carry, for the log
    fill: Callable  # fill(request): writes the elements, just before every send
    description: str
    ready: Expect | None = None  # waited for before every send (the scanner idle)
    confirm: Expect | None = None  # read back, one window at a time
    without_receipt: bool = False  # sent without LAS X's answer being checked
    never_resend: bool = False


class Request:
    """The elements that carry one change, filled in just before each send.

    The transfer sends them one after the other and stops at the first LAS X
    refuses. ``fill`` writes them afresh before every send, so a change that
    depends on where the instrument stands now (a z move, sent as a step
    from the current position) is right also when it is sent again.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.elements: list[str] = []
        #: The setting the elements are addressed to, by its place in the job,
        #: 0 the first: LCS takes ``SingleSettingIndex`` and the like so, whichever
        #: setting is current (simulator, 2026-11-01). ``changes.run`` names the current one.
        self.setting = 0


def is_idle(status) -> bool:
    """The scanner status says idle."""
    return isinstance(status, str) and "Idle" in status


def idle(*, without_limit=False) -> Expect:
    """The scanner idle, read from the hardware server.

    ``without_limit`` waits for it as long as it takes, instead of the four
    windows: a capture is never started into a running scan.
    """
    return Expect("scan_status", is_idle, without_limit=without_limit)


# =============================================================================
# How values are written into an element
# =============================================================================

#: The current settings' job, in LAS X's plain Navigator.
CURRENT = 'BlockId="0"'

#: LAS X's numbers for the z drives (``ZUseMode``).
Z_DRIVES = {"z-galvo": 1, "z-wide": 2}


def metres(um: float) -> str:
    """A length as LAS X writes it in its commands: metres, ten decimals."""
    return f"{um * 1e-6:.10f}"


def value(number: float) -> str:
    """A number as LAS X writes it: ``2.5``, ``600``, ``5``."""
    return f"{number:g}"


# =============================================================================
# The settings: one table
# =============================================================================


@dataclass(frozen=True)
class Setting:
    """Everything that is particular to changing one setting of the current job.

    ``lasx_command`` and ``attribute`` are what the element carries;
    ``per_setting`` adds ``SetMode="2" SingleSettingIndex`` for a value that
    belongs to one setting, the current one (``Request.setting``). ``reading`` names the
    reading that shows the value, ``read`` picks the number out of it, and
    ``tolerance`` says how close the read-back must come (None: exactly).
    """

    name: str
    lasx_command: str
    attribute: str
    per_setting: bool
    reading: str
    read: Callable[[object], float]
    describe: str
    tolerance: float | None = None

    @property
    def command(self) -> str:
        """The setting's name in the limits: ``set_zoom`` for ``zoom``."""
        return f"set_{self.name}"


ZOOM = Setting(
    "zoom", "SetZoom", "Zoom", False, "zoom", lambda v: v["current"], "Zoom -> {value}", 0.1
)
SCAN_SPEED = Setting(
    "scan_speed", "SetScanSpeed", "Speed", False, "scan_speed", lambda v: v["value"],
    "ScanSpeed -> {value}",
)
SCAN_FIELD_ROTATION = Setting(
    "scan_field_rotation", "SetConfocalScanFieldRotation", "Rotation", False,
    "scan_field_rotation", lambda v: v["value"], "Rotation -> {value}", 0.5,
)
LINE_AVERAGE = Setting(
    "line_average", "SetLineAverage", "LineAverage", True, "averaging",
    lambda v: v["lineAverage"], "LineAverage -> {value}",
)
PINHOLE_AIRY = Setting(
    "pinhole_airy", "SetPinholeAU", "PinholeAU", True, "pinhole",
    lambda v: v["airy"]["value"], "PinholeAU -> {value}", 0.05,
)
# Sent to the current scan as LCS's log showed them, and taken there (simulator, 2026-11-01).
FRAME_AVERAGE = Setting(
    "frame_average", "SetFrameAverage", "FrameAverage", True, "averaging",
    lambda v: v["frameAverage"], "FrameAverage -> {value}",
)
LINE_ACCUMULATION = Setting(
    "line_accumulation", "SetLineAccu", "LineAccu", True, "averaging",
    lambda v: v["lineAccumulation"], "LineAccu -> {value}",
)
FRAME_ACCUMULATION = Setting(
    "frame_accumulation", "SetFrameAccu", "FrameAccu", True, "averaging",
    lambda v: v["frameAccumulation"], "FrameAccu -> {value}",
)

#: Every setting the plain Navigator route can change, by its name.
SETTINGS = {
    s.name: s
    for s in (
        ZOOM, SCAN_SPEED, SCAN_FIELD_ROTATION, LINE_AVERAGE, PINHOLE_AIRY,
        FRAME_AVERAGE, LINE_ACCUMULATION, FRAME_ACCUMULATION,
    )
}


def setting_element(setting: Setting, number: float, index: int = 0) -> str:
    """The element that writes *number* into the setting at *index* (a per-setting value) or the scan (the others)."""
    one_setting = f' SetMode="2" SingleSettingIndex="{int(index)}"' if setting.per_setting else ""
    return (
        f'<Scanner Command="{setting.lasx_command}" {setting.attribute}="{value(number)}"'
        f"{one_setting} {CURRENT}/>"
    )


def setting_shows(setting: Setting, target: float, tolerance=None):
    """A reading in which *setting* shows *target*, within *tolerance* (None: the setting's own)."""
    tolerance = setting.tolerance if tolerance is None else tolerance

    def check(reading) -> bool:
        try:
            actual = setting.read(reading)
            if tolerance is None:
                return actual == target
            return abs(actual - target) <= tolerance
        except (KeyError, TypeError):
            return False

    return check


def setting(name: str, number: float, *, tolerance=None) -> Write:
    """The change of the setting *name* (``SETTINGS``) to *number*; ValueError for an unknown name.

    Confirmed by reading the setting back, within its tolerance.
    """
    chosen = SETTINGS.get(name)
    if chosen is None:
        raise ValueError(f"no setting {name!r}; the settings are {sorted(SETTINGS)}")
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise ValueError(f"{name} takes a number, not {number!r}")

    def fill(request: Request) -> None:
        request.elements = [setting_element(chosen, number, request.setting)]

    return Write(
        command=chosen.command,
        values={"value": number},
        request=chosen.lasx_command,
        fill=fill,
        description=chosen.describe.format(value=number),
        ready=idle(),
        confirm=Expect(chosen.reading, setting_shows(chosen, number, tolerance)),
    )


# =============================================================================
# The settings that are not a number: each a write of its own, by name
# =============================================================================


def scan_mode(mode: str) -> Write:
    """Set the scan mode (``xyz``, ``xyt`` ...), as the Scanmode panel does; read back as LAS X names it."""
    if not isinstance(mode, str) or not mode.strip():
        raise ValueError(f"scan_mode takes LAS X's name of a scan mode, such as 'xyz', not {mode!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="ScanMode" ScanMode="{mode}" {CURRENT}/>']

    return Write(
        command="set_scan_mode",
        values={"value": mode},
        request="ScanMode",
        fill=fill,
        description=f"ScanMode -> {mode}",
        ready=idle(),
        # LAS X shows xyz as xyzN and xyt as xyNt (simulator, 2026-10-30).
        confirm=Expect("scan_mode", lambda shown: isinstance(shown, str) and shown.replace("N", "") == mode),
    )


def autofocus(on: bool) -> Write:
    """Switch autofocus on or off, as the Autofocus button does; read back from the state tree."""
    if not isinstance(on, bool):
        raise ValueError(f"autofocus takes True or False, not {on!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="ActivateAutoFocus" Activate="{int(on)}" InitNew="0"/>']

    return Write(
        command="set_autofocus",
        values={"value": on},
        request="ActivateAutoFocus",
        fill=fill,
        description=f"Autofocus -> {'on' if on else 'off'}",
        ready=idle(),
        confirm=Expect("autofocus", lambda shown: shown is on),
    )


def route_of(text) -> tuple[int, ...]:
    """A route as the readings give it, ``"40;3"``, as numbers: ``(40, 3)``."""
    return tuple(int(part) for part in str(text or "").split(";") if part)


def beam_route(route) -> str:
    """A light source's route as LCS takes it, one ``BeamPosition`` per level (10;30 is level 0 at 10, level 1 at 30)."""
    positions = "".join(
        f'<BeamPosition BeamPositionLevel="{level}" BeamPosition="{position}"/>' for level, position in enumerate(route)
    )
    return f'<BeamRoute Version="0">{positions}</BeamRoute>'


#: How close a line's power must come, in percent: one of LCS's steps of 1/255.
LASER_TOLERANCE_PERCENT = 100 / 255


def laser_power(route, line_index: int, wavelength_nm: float, percent: float) -> Write:
    """Set the power of the line *line_index* of the light source on *route* to *percent*; 0 switches the line off.

    Above 0 the line is switched on in the current setting (the 488 line
    became used at 5 % on the simulator, 2026-11-01). Read back from the
    lines that are on, within one of LCS's steps; at 0 the line is no longer
    among them. *wavelength_nm* names the line for the read-back and the limits.
    """
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
        raise ValueError(f"a laser power is a percentage from 0 to 100, not {percent!r}")
    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="SetLaserLineIntensity" LineIndex="{int(line_index)}" LineIntensity="{value(percent / 100)}" '
            f'SetMode="2" SingleSettingIndex="{request.setting}" {CURRENT} IsRoiBackground="0">{beam_route(route)}</Scanner>'
        ]

    def shows(lines) -> bool:
        if not isinstance(lines, list):
            return False
        found = [line for line in lines if line.get("wavelength") == wavelength_nm]
        if percent == 0:
            return not found
        return len(found) == 1 and abs(found[0]["intensity"] * 100 - percent) <= LASER_TOLERANCE_PERCENT

    return Write(
        command="set_laser_power",
        values={"value": percent, "wavelength_nm": wavelength_nm},
        request="SetLaserLineIntensity",
        fill=fill,
        description=f"Laser {wavelength_nm:g} nm -> {percent:g} %",
        ready=idle(),
        confirm=Expect("laser_lines", shows),
    )


def laser_wavelength(route, line_index: int, wavelength_nm: float) -> Write:
    """Tune the line *line_index* of the tunable light source on *route* (the white-light laser) to *wavelength_nm*; read back.

    As the window sends it when a line's wavelength is changed (simulator,
    2026-11-01); the AOBS follows by itself. The range and the distance to
    the other lines are checked before this is built (``changes.tune_laser_line``).
    """
    if isinstance(wavelength_nm, bool) or not isinstance(wavelength_nm, (int, float)):
        raise ValueError(f"a wavelength is a number of nanometres, not {wavelength_nm!r}")
    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="SetLightSourceWavelength" LineIndex="{int(line_index)}" Wavelength="{value(wavelength_nm)}" '
            f'SetMode="2" SingleSettingIndex="{request.setting}" DoAsync="1">{beam_route(route)}</Scanner>'
        ]

    def shows(lines) -> bool:
        return any(
            route_of(line.get("beamRoute")) == tuple(route) and line.get("lineIndex") == int(line_index)
            and abs(line.get("wavelength", 0) - wavelength_nm) < 0.5
            for line in lines or []
        )

    return Write(
        command="set_laser_wavelength",
        values={"value": float(wavelength_nm)},
        request="SetLightSourceWavelength",
        fill=fill,
        description=f"Laser line {line_index} on {route} -> {wavelength_nm:g} nm",
        ready=idle(),
        confirm=Expect("laser_lines", shows),
    )


def laser_line_removed(lines, removed_nm: float) -> Write:
    """The laser lines in use without the one at *removed_nm*, sent as one definition, as removing a line in the window does; read back.

    LCS drops a line left out of the definition: it is off and no longer
    shown (simulator, 2026-11-01). Confirmed when no line is on at *removed_nm*.
    """
    return replace(
        laser_lines_definition(lines, removed_nm),
        command="remove_laser_line",
        values={"value": float(removed_nm)},
        description=f"Laser line at {removed_nm:g} nm removed",
        confirm=Expect("laser_lines", lambda on: not any(abs(line.get("wavelength", 0) - removed_nm) < 0.5 for line in on or [])),
    )


def laser_lines_definition(lines, new_wavelength_nm: float) -> Write:
    """The laser lines in use, ``[(route, line_index, wavelength_nm), ...]``, sent as the window's Add Laser sends them; read back.

    LCS lists a new line at its wavelength with no power (simulator,
    2026-11-01), so nothing can be read back here: the power set after is.
    ``Locked`` 0 for every line: the caller refuses while a line is locked
    for the Dye Assistant.
    """
    listed = "".join(
        f"<LightSource>{beam_route(route)}<LineIndex>{int(index)}</LineIndex>"
        f"<WaveLength>{value(wavelength)}</WaveLength><Locked>0</Locked></LightSource>"
        for route, index, wavelength in lines
    )
    def fill(request: Request) -> None:
        request.elements = [
            '<Scanner IsBleachPointDefinition="0" IsAutoFocusDefinition="0" Command="SetConfocalLaserLinesDefinition">'
            f'<Sequences><Sequence Index="{request.setting}">{listed}</Sequence></Sequences></Scanner>'
        ]

    return Write(
        command="set_laser_lines",
        values={"value": float(new_wavelength_nm)},
        request="SetConfocalLaserLinesDefinition",
        fill=fill,
        description=f"Laser line switched on at {new_wavelength_nm:g} nm",
        ready=idle(),
        confirm=None,
    )


def laser_shutter(route, open_: bool) -> Write:
    """Open or close the shutter of the light source on *route*; read back from its lines that are on."""
    if not isinstance(open_, bool):
        raise ValueError(f"a shutter is open (True) or closed (False), not {open_!r}")
    named = ";".join(str(position) for position in route)

    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="ActivateShutter" Activate="{int(open_)}" SetMode="2" SingleSettingIndex="{request.setting}" '
            f'{CURRENT}>{beam_route(route)}</Scanner>'
        ]

    def shows(lines) -> bool:
        own = [line for line in lines or [] if line.get("beamRoute") == named]
        return bool(own) and all(line.get("shutterOpen") is open_ for line in own)

    return Write(
        command="set_laser_shutter",
        values={"value": open_},
        request="ActivateShutter",
        fill=fill,
        description=f"Shutter {named} -> {'open' if open_ else 'closed'}",
        ready=idle(),
        confirm=Expect("laser_lines", shows),
    )


def image_format(pixels) -> Write:
    """Set the image format to *pixels*, ``(x, y)``; read back as LAS X writes it (``"512 x 512"``)."""
    try:
        x, y = (int(v) for v in pixels)
    except (TypeError, ValueError):
        raise ValueError(f"format takes two whole numbers of pixels, (x, y), not {pixels!r}") from None
    if x <= 0 or y <= 0 or (x, y) != tuple(pixels):
        raise ValueError(f"format takes two whole numbers of pixels above 0, not {pixels!r}")
    shown = f"{x} x {y}"

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetFormat" FormatX="{x}" FormatY="{y}" {CURRENT} IsAutoFocusDefinition="0"/>']

    return Write(
        command="set_format",
        values={"value": shown},
        request="SetFormat",
        fill=fill,
        description=f"Format -> {shown}",
        ready=idle(),
        confirm=Expect("format", lambda reading: reading == shown),
    )


#: How close the collar must read back, in percent.
COLLAR_TOLERANCE_PERCENT = 0.1


def collar(percent) -> Write:
    """Turn the objective's correction collar (motCORR) to *percent*, 0 to 100, as the window's box does; read back.

    LAS X's window sends this when a value is entered in its motCORR box,
    without its Set button, which stores a reference position instead
    (simulator, 2026-11-01).
    """
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
        raise ValueError(f"collar takes 0 to 100 percent, not {percent!r}")

    def fill(request: Request) -> None:
        request.elements = [
            f'<Microscope Command="SetMotCorrPosition" MotCorrPosition="{value(percent)}" '
            f'SetMode="1" SingleSettingIndex="{request.setting}" {CURRENT}/>'
        ]

    return Write(
        command="set_collar",
        values={"value": percent},
        request="SetMotCorrPosition",
        fill=fill,
        description=f"Collar -> {percent:g} %",
        ready=idle(),
        confirm=Expect("collar", lambda shown: abs(shown["percent"] - percent) <= COLLAR_TOLERANCE_PERCENT),
    )


def smartcorr_range(value) -> Write:
    """SmartCORR's range: ``(begin, end, steps)``, the collar positions in percent and how many; written into the state tree.

    The window writes Begin, End and Steps there, with no command
    (simulator, 2026-11-01).
    """
    try:
        begin, end, steps = value
    except (TypeError, ValueError):
        raise ValueError(f"smartcorr_range takes (begin, end, steps), not {value!r}") from None
    for name, percent in (("begin", begin), ("end", end)):
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100:
            raise ValueError(f"smartcorr_range's {name} takes 0 to 100 percent, not {percent!r}")
    if isinstance(steps, bool) or not isinstance(steps, int) or not 2 <= steps <= 100:
        raise ValueError(f"smartcorr_range's steps takes a whole number from 2 to 100, not {steps!r}")
    wanted = {"begin": float(begin), "end": float(end), "steps": steps}

    def fill(request: Request) -> None:
        request.elements = [
            f'<StateTree Command="AutomationBegin" Value="{float(begin)!r}"/>',
            f'<StateTree Command="AutomationEnd" Value="{float(end)!r}"/>',
            f'<StateTree Command="AutomationSteps" Value="{steps}"/>',
        ]

    return Write(
        command="set_smartcorr_range",
        values={"values": [float(begin), float(end)]},
        request="SmartCorrRange",
        fill=fill,
        description=f"SmartCORR range -> {begin:g} to {end:g} %, {steps} steps",
        ready=idle(),
        confirm=Expect("smartcorr", lambda shown: shown == wanted),
    )


def collar_reference(percent) -> Write:
    """Store *percent* as the collar's reference for the objective in the light path, as the window's Set does; None clears it, as its Reset does.

    Written into that objective's ``MotCorrRefPos`` in LCS's state tree, -1
    for none, as the window writes it (simulator, 2026-11-01); read back.
    """
    if percent is not None and (isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 <= percent <= 100):
        raise ValueError(f"the collar's reference takes 0 to 100 percent or None, not {percent!r}")
    written = -1.0 if percent is None else float(percent)

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="MotCorrRefPos" Value="{written!r}"/>']

    def shows(reading) -> bool:
        stored = reading.get("reference") if isinstance(reading, dict) else "unread"
        if percent is None:
            return stored is None
        return stored is not None and stored != "unread" and abs(stored - percent) <= COLLAR_TOLERANCE_PERCENT

    return Write(
        command="set_collar_reference",
        values={"value": written},
        request="MotCorrRefPos",
        fill=fill,
        description=f"Collar reference -> {'none' if percent is None else f'{percent:g} %'}",
        ready=idle(),
        confirm=Expect("collar", shows),
    )


def sequential_mode(mode: str) -> Write:
    """How several settings are taken in turn: ``"line"``, ``"frame"`` or ``"stack"``, as the window's three buttons; read back.

    Written into LCS's state tree, as the window does; LCS rearranges the
    detectors' channels itself (simulator, 2026-11-01).
    """
    codes = {name: code for code, name in direct_reader.SEQUENTIAL_MODES.items()}
    if mode not in codes:
        raise ValueError(f"sequential_mode takes {sorted(codes)}, not {mode!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="SequentialMode" Value="{codes[mode]}"/>']

    return Write(
        command="set_sequential_mode",
        values={"value": mode},
        request="SequentialMode",
        fill=fill,
        description=f"Sequential between {mode}s",
        ready=idle(),
        confirm=Expect("sequential_mode", lambda shown: shown == mode),
    )


def activate_setting(setting_id: int) -> Write:
    """Make the setting *setting_id* the current one, as clicking it in the window does; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<ATL SettingID="{int(setting_id)}" Command="ATL_Setting_Activate" Attribute="2"/>']

    return Write(
        command="activate_setting",
        values={"value": int(setting_id)},
        request="ATL_Setting_Activate",
        fill=fill,
        description=f"Setting {setting_id} made current",
        ready=idle(),
        confirm=Expect("setting", lambda shown: shown["id"] == setting_id),
    )


def add_setting(copy: bool, now: int) -> Write:
    """Add a setting to the current job, as the window's "Add new Setting" does: new, or a copy of the current one.

    LCS makes it the current setting. Confirmed when the job holds one more
    setting than the *now* it held.
    """
    if not isinstance(copy, bool):
        raise ValueError(f"a setting is added new (False) or as a copy (True), not {copy!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<ATL CloneSetting="{int(copy)}" Command="ATL_Setting_Create" Attribute="2"/>']

    return Write(
        command="add_setting",
        values={"value": copy},
        request="ATL_Setting_Create",
        fill=fill,
        description=f"Setting added{' as a copy' if copy else ''}",
        ready=idle(),
        confirm=Expect("setting", lambda shown: len(shown["all"]) == now + 1),
    )


def delete_setting(setting_id: int) -> Write:
    """Delete the setting *setting_id* from the current job, as deleting it in the window does; confirmed when it is gone."""

    def fill(request: Request) -> None:
        request.elements = [f'<ATL SettingID="{int(setting_id)}" Command="ATL_Setting_Delete" Attribute="2"/>']

    return Write(
        command="delete_setting",
        values={"value": int(setting_id)},
        request="ATL_Setting_Delete",
        fill=fill,
        description=f"Setting {setting_id} deleted",
        ready=idle(),
        confirm=Expect("setting", lambda shown: all(s["id"] != setting_id for s in shown["all"])),
    )


def setting_enabled(setting_id: int, on: bool) -> Write:
    """Switch the current setting on or off for scanning, as the window's checkbox does; read back.

    Off, LAS X takes no picture with it: LCS's ``CanDoScanSingle`` went False
    (simulator, 2026-11-01).
    """
    if not isinstance(on, bool):
        raise ValueError(f"a setting is switched on with True or off with False, not {on!r}")

    def fill(request: Request) -> None:
        request.elements = [
            f'<ATL SettingID="{int(setting_id)}" SetMode="0" EnableForScanning="{int(on)}" '
            'Command="ATL_Setting_EnableDisableForScanning" Attribute="0"/>'
        ]

    return Write(
        command="set_setting_enabled",
        values={"value": on},
        request="ATL_Setting_EnableDisableForScanning",
        fill=fill,
        description=f"Setting {setting_id} -> {'on' if on else 'off'}",
        ready=idle(),
        confirm=Expect("setting", lambda shown: shown["enabled"] is on),
    )


#: A setting's name as the driver writes it; the transfer checks it again.
SETTING_NAME = re.compile(r"^[\w ._,()/+#-]{1,64}$")


def setting_name(name: str) -> Write:
    """Rename the current setting, as editing its name in the window does: written into the state tree; read back."""
    if not isinstance(name, str) or not SETTING_NAME.match(name):
        raise ValueError(f"a setting's name is 1 to 64 letters, digits, spaces or _ - . , ( ) / + #, not {name!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="Name" Value="{name}"/>']

    return Write(
        command="set_setting_name",
        values={"value": name},
        request="Name",
        fill=fill,
        description=f"Setting name -> {name}",
        ready=idle(),
        confirm=Expect("setting", lambda shown: shown["name"] == name),
    )


def dye_assistant_lock(detector_route, line_route, line_index: int, locked: bool) -> Write:
    """Lock a detector and its excitation line from LAS X's Dye Assistant, as the window's detector lock does; read back.

    The window sends one element for the detector and one for the line,
    ``BlockedState`` 1 locked, 0 free (simulator, 2026-11-01).
    """
    state = int(bool(locked))

    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="BlockLightSinkDetectorForDyeAssistant" SequentialSettingIndex="{request.setting}" BlockedState="{state}">'
            f"{beam_route(detector_route)}</Scanner>",
            f'<Scanner Command="BlockLightSourceLineForDyeAssistant" LineIndex="{int(line_index)}" '
            f'SequentialSettingIndex="{request.setting}" BlockedState="{state}">{beam_route(line_route)}</Scanner>',
        ]

    def shows(detectors) -> bool:
        return any(
            route_of(d.get("beamRoute")) == tuple(detector_route) and d.get("lockedForDyeAssistant") is bool(locked)
            for d in detectors or []
        )

    return Write(
        command="lock_detector",
        values={"value": bool(locked)},
        request="BlockLightSinkDetectorForDyeAssistant",
        fill=fill,
        description=f"Detector {detector_route} -> {'locked' if locked else 'free'} for the Dye Assistant",
        ready=idle(),
        confirm=Expect("detectors", shows),
    )


def autofocus_change(name: str, elements: list[str], description: str, shows) -> Write:
    """One of the autofocus's settings written into LCS's state tree, as its Best Focus Scan Options do; read back."""

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command=f"set_{name}",
        values={"value": description},
        request=name,
        fill=fill,
        description=f"{name.replace('_', ' ').capitalize()} -> {description}",
        ready=idle(),
        confirm=Expect("autofocus_settings", shows),
    )


def autofocus_method(method: str) -> Write:
    """The autofocus's method, as the window's "Optimized for": ``"contrast 1"``, ``"intensity"``, ``"noise"``, ``"contrast 2"``, ``"reflection"``."""
    codes = {name: code for code, name in direct_reader.AUTOFOCUS_METHODS.items()}
    if method not in codes:
        raise ValueError(f"autofocus_method takes {sorted(codes)}, not {method!r}")
    return autofocus_change("autofocus_method", [f'<StateTree Command="AFAnalyseType" Value="{codes[method]}"/>'], method,
                            lambda shown: shown["method"] == method)


def autofocus_precision(precision: int) -> Write:
    """The autofocus's precision, as the window's slider: 1 (speed) to 5 (accuracy); LAS X works out the steps."""
    if isinstance(precision, bool) or precision not in (1, 2, 3, 4, 5):
        raise ValueError(f"autofocus_precision takes 1 (speed) to 5 (accuracy), not {precision!r}")
    return autofocus_change(
        "autofocus_precision",
        ['<StateTree Command="UseFixSliceCount" Value="0"/>', f'<StateTree Command="Precision" Value="{precision}"/>'],
        str(precision), lambda shown: shown["precision"] == precision and shown["steps"] is None,
    )


def autofocus_steps(steps: int) -> Write:
    """A fixed number of autofocus steps, as the window's "Number of Steps": odd, 3 to 1001 (LAS X turns an even one odd)."""
    if isinstance(steps, bool) or not isinstance(steps, int) or not 3 <= steps <= 1001 or steps % 2 == 0:
        raise ValueError(f"autofocus_steps takes an odd number from 3 to 1001, not {steps!r}")
    return autofocus_change(
        "autofocus_steps",
        ['<StateTree Command="UseFixSliceCount" Value="1"/>', f'<StateTree Command="FixSliceCount" Value="{steps}"/>'],
        str(steps), lambda shown: shown["steps"] == steps,
    )


def autofocus_range_um(range_um) -> Write:
    """The autofocus's capture range in whole micrometres, 1 to 5000 as the window takes it; checked against the z limits when it runs.

    LCS keeps it as a whole number and ignores one written with decimals
    (simulator, 2026-11-02), so only whole micrometres are taken.
    """
    if isinstance(range_um, bool) or not isinstance(range_um, (int, float)) or range_um != int(range_um) or not 1 <= range_um <= 5000:
        raise ValueError(f"autofocus_range_um takes whole micrometres from 1 to 5000, not {range_um!r}")
    return autofocus_change("autofocus_range_um", [f'<StateTree Command="FocusRange" Value="{int(range_um)}"/>'],
                            f"{range_um:g} um", lambda shown: abs(shown["range_um"] - range_um) < 0.01)


def autofocus_drive(drive: str) -> Write:
    """The z drive the autofocus searches with: ``"z-galvo"`` or ``"z-wide"``."""
    if drive not in Z_DRIVES:
        raise ValueError(f"autofocus_drive takes {sorted(Z_DRIVES)}, not {drive!r}")
    return autofocus_change("autofocus_drive", [f'<StateTree Command="ZUseMode" Value="{Z_DRIVES[drive]}"/>'], drive,
                            lambda shown: shown["drive"] == drive)


def detector_change(command: str, request_name: str, element, values: dict, description: str, shows) -> Write:
    """A change of one detector in the current setting, as the window sends it; read back from the detectors in use."""

    def fill(request: Request) -> None:
        request.elements = [element(request.setting)]

    return Write(command=command, values=values, request=request_name, fill=fill, description=description,
                 ready=idle(), confirm=Expect("detectors", shows))


def in_use(detectors, name):
    """The detector *name* among the detectors in use, or None."""
    return next((d for d in detectors or [] if d.get("name") == name), None)


def detector_active(route, name: str, on: bool) -> Write:
    """Switch the detector *name* (on *route*) on or off in the current setting, as the window does; read back."""
    if not isinstance(on, bool):
        raise ValueError(f"a detector is switched on with True or off with False, not {on!r}")
    return detector_change(
        "set_detector_active", "SetDetectorActive",
        lambda setting: (f'<Scanner Command="SetDetectorActive" SetActive="{int(on)}" SequentialSettingIndex="{setting}" SetMode="0" '
                         f'BlockId="0" SuppressUserInteraction="0">{beam_route(route)}</Scanner>'),
        {"value": on}, f"Detector {name} -> {'on' if on else 'off'}",
        lambda detectors: (in_use(detectors, name) is not None) is on,
    )


def detector_gain(route, name: str, gain: float) -> Write:
    """Set the gain of the detector *name* (on *route*) in the current setting, as the window does; read back."""
    if isinstance(gain, bool) or not isinstance(gain, (int, float)):
        raise ValueError(f"a detector's gain is a number, not {gain!r}")
    return detector_change(
        "set_detector_gain", "SetDetectorGain",
        lambda setting: (f'<Scanner Command="SetDetectorGain" DetectorGain="{value(gain)}" SetMode="2" SingleSettingIndex="{setting}" '
                         f'BlockId="0">{beam_route(route)}</Scanner>'),
        {"value": gain}, f"Detector {name} gain -> {gain:g}",
        lambda detectors: (unit := in_use(detectors, name)) is not None and abs(unit["gain"]["value"] - gain) <= 0.05,
    )


def detector_band(route, name: str, begin_nm: float, end_nm: float) -> Write:
    """Set the band of the detector *name* (on *route*) in the current setting, as the window does; read back within 1 nm."""
    if not begin_nm < end_nm:
        raise ValueError(f"a detector's band runs from a lower to a higher wavelength, not {begin_nm!r} to {end_nm!r}")
    positions = "".join(f'<BeamPosition BeamPositionLevel="{level}" BeamPosition="{p}"/>' for level, p in enumerate(route))
    return detector_change(
        "set_detector_band", "SetSpectrophotometerMultiBandRange",
        lambda setting: ('<Scanner Command="SetSpectrophotometerMultiBandRange" DetectorScanType="0" SetterMode="2" SequentialSetMode="2" '
                         f'SingleSettingIndex="{setting}" BlockId="0" BandLeft1="{value(begin_nm)}" BandRight1="{value(end_nm)}">'
                         f'<ActionInitiatorDetector Version="0"/><BandBeamRoute1 Version="0">{positions}</BandBeamRoute1></Scanner>'),
        {"value": [begin_nm, end_nm]}, f"Detector {name} band -> {begin_nm:g} to {end_nm:g} nm",
        lambda detectors: (unit := in_use(detectors, name)) is not None
        and abs(unit["detectionBegin"] - begin_nm) <= 1 and abs(unit["detectionEnd"] - end_nm) <= 1,
    )


def detector_tau(route, name: str, place: int, mode: str) -> Write:
    """Set the tau mode of the detector *name* (on *route*) by its *place* in the detector's list, as the window does; read back."""
    code = TAU_CODES[mode]

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetDigitalGatingModeIndex" GatingModeIndex="{int(place)}" SetMode="0" '
                            f'SingleSettingIndex="{request.setting}">{beam_route(route)}</Scanner>']

    return Write(command="set_detector_tau", values={"value": mode}, request="SetDigitalGatingModeIndex", fill=fill,
                 description=f"Detector {name} tau mode -> {mode}", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: (modes or {}).get(name, {}).get("tau") == code))


#: The operating modes by name and LCS's code for them (``OperatingMode``), as the window's Operating Mode list names them.
OPERATING_CODES = {"Fast Mode": 4, "Counting": 1, "Analog": 10, "Reflection": 11}


def detector_operating_mode(at, place: int, name: str, mode: str) -> Write:
    """The operating mode of the detector *name*, written as its *place* in the detector's list into its setting's entry
    (*at*: the setting's, photometer's and detector's places), as the window does; read back.

    LCS sets the mode itself from the place and applies it to the detector
    (simulator, 2026-11-02: from Counting to Analog, the gain stored in the
    setting came back).
    """
    code = OPERATING_CODES[mode]

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="OperatingModeIndex" Value="{int(place)}" At="{",".join(str(int(n)) for n in at)}"/>']

    return Write(command="set_detector_operating_mode", values={"value": mode}, request="OperatingModeIndex", fill=fill,
                 description=f"Detector {name} operating mode -> {mode}", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: (modes or {}).get(name, {}).get("operating") == code))


#: The tau modes by name and LCS's code for them (``DigitalGatingMode``), as the window's TauModes list names them.
TAU_CODES = {"Intensity": 2, "TauGating": 3, "TauScan": 5, "TauSeparation": 6, "TauInteraction": 10}


def autofocus_scan_change(name: str, elements: list[str], description: str, shows) -> Write:
    """A change of the Best Focus Setting's own scan; read back from it."""

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command=f"set_{name}", values={"value": description}, request=name, fill=fill,
        description=f"{name.replace('_', ' ').capitalize()} -> {description}", ready=idle(),
        confirm=Expect("autofocus_scan", shows),
    )


def autofocus_format(pixels) -> Write:
    """The Best Focus Setting's format, ``(x, y)`` pixels, with the scan's own command for the autofocus (``IsAutoFocusDefinition`` 1)."""
    try:
        x, y = (int(n) for n in pixels)
    except (TypeError, ValueError):
        raise ValueError(f"autofocus_format takes (x, y) pixels, not {pixels!r}") from None
    if x <= 0 or y <= 0:
        raise ValueError(f"autofocus_format takes (x, y) pixels, not {pixels!r}")
    return autofocus_scan_change(
        "autofocus_format",
        [f'<Scanner Command="SetFormat" FormatX="{x}" FormatY="{y}" {CURRENT} IsAutoFocusDefinition="1"/>'],
        f"{x} x {y}", lambda shown: shown["format"] == (x, y),
    )


def autofocus_speed(speed) -> Write:
    """The Best Focus Setting's scan speed, within LCS's own range for it (read at the write); written into the state tree."""
    if isinstance(speed, bool) or not isinstance(speed, int):
        raise ValueError(f"autofocus_speed takes a whole speed, not {speed!r}")
    return autofocus_scan_change("autofocus_speed", [f'<StateTree Command="AutofocusSpeed" Value="{speed}"/>'],
                                 str(speed), lambda shown: shown["speed"] == speed)


def autofocus_bidirectional(on: bool) -> Write:
    """The Best Focus Setting's scan both ways (True) or one way (False); written into the state tree."""
    if not isinstance(on, bool):
        raise ValueError(f"autofocus_bidirectional takes True or False, not {on!r}")
    return autofocus_scan_change("autofocus_bidirectional", [f'<StateTree Command="AutofocusScanDirectionX" Value="{2 if on else 1}"/>'],
                                 "on" if on else "off", lambda shown: shown["bidirectional"] is on)


def autofocus_zoom(zoom) -> Write:
    """The Best Focus Setting's zoom, within LCS's own range for it (read at the write); written into the state tree."""
    if isinstance(zoom, bool) or not isinstance(zoom, (int, float)):
        raise ValueError(f"autofocus_zoom takes a number, not {zoom!r}")
    return autofocus_scan_change("autofocus_zoom", [f'<StateTree Command="AutofocusZoom" Value="{float(zoom)!r}"/>'],
                                 f"{zoom:g}", lambda shown: abs(shown["zoom"] - zoom) <= 0.01)


def autofocus_optics(line_route, line_index: int, wavelength_nm: float, percent: float, detector_route,
                     begin_nm: float, end_nm: float, dye: str, colour: str = "Gray") -> Write:
    """The Best Focus Setting's laser line with its power, and its detector with its band and dye, as the window's drag-in sends them.

    One command, ``SetConfocalDefinitionFromDyeAssistant`` with
    ``IsAutoFocusDefinition`` 1 (simulator, 2026-11-02). Read back from the
    Best Focus Setting's lines. The power goes to the limits as a laser
    power. It sets the power of a line it brings in; a line already there
    keeps its power (simulator, 2026-11-02): :func:`autofocus_laser_power`
    changes that.
    """
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
        raise ValueError(f"the autofocus's laser power is above 0 and at most 100 percent, not {percent!r}")
    if not begin_nm < end_nm:
        raise ValueError(f"a detector's band runs from a lower to a higher wavelength, not {begin_nm!r} to {end_nm!r}")
    if not isinstance(dye, str) or not dye or any(c in dye for c in '<>&"'):
        raise ValueError(f"a dye is named as LAS X names it, such as 'Leica/ALEXA 405', not {dye!r}")
    element = (
        '<Scanner IsAutoFocusDefinition="1" ActiveSettingIndex="-1" Command="SetConfocalDefinitionFromDyeAssistant">'
        "<Sequences><DoAdjustSequenceCount>1</DoAdjustSequenceCount><MessageSource>DyeDragNDrop</MessageSource>"
        '<SequentialMode>0</SequentialMode><Sequence Index="0">'
        f"<LightSource>{beam_route(line_route)}<LineIndex>{int(line_index)}</LineIndex><WaveLength>{value(wavelength_nm)}</WaveLength>"
        f"<Locked>0</Locked><Intensity>{value(percent / 100)}</Intensity></LightSource>"
        f"<Detector>{beam_route(detector_route)}<Begin>{value(begin_nm)}</Begin><End>{value(end_nm)}</End><Dye>{dye}</Dye>"
        "<DetectorDyeRelation>0</DetectorDyeRelation><DyeWasAdded>1</DyeWasAdded><DyeExcitationMode>0</DyeExcitationMode>"
        f"<Locked>0</Locked><LutName>{colour}</LutName><IsLutUserDefined>0</IsLutUserDefined></Detector>"
        "</Sequence></Sequences></Scanner>"
    )

    def fill(request: Request) -> None:
        request.elements = [element]

    def shows(optics) -> bool:
        return any(abs(line["wavelength"] - wavelength_nm) < 0.5 and abs(line["percent"] - percent) <= LASER_TOLERANCE_PERCENT
                   for line in (optics or {}).get("lines", []))

    return Write(
        command="set_autofocus_optics", values={"laser_power": [percent]}, request="SetConfocalDefinitionFromDyeAssistant",
        fill=fill, description=f"Best Focus Setting -> {wavelength_nm:g} nm at {percent:g} %, {dye}", ready=idle(),
        confirm=Expect("autofocus_optics", shows),
    )


def autofocus_laser_power(place: int, line_index: int, wavelength_nm: float, percent: float) -> Write:
    """The power of the Best Focus Setting's line *line_index* of light source *place*, written into the state tree as the window does; read back.

    The window writes it there with no command (simulator, 2026-11-02); the
    power goes to the limits as a laser power.
    """
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
        raise ValueError(f"the autofocus's laser power is above 0 and at most 100 percent, not {percent!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="AutofocusLaserPower" Value="{float(percent)!r}" At="{int(place)},{int(line_index)}"/>']

    def shows(optics) -> bool:
        return any(abs(line["wavelength"] - wavelength_nm) < 0.5 and abs(line["percent"] - percent) <= LASER_TOLERANCE_PERCENT
                   for line in (optics or {}).get("lines", []))

    return Write(
        command="set_autofocus_laser_power", values={"laser_power": [percent]}, request="AutofocusLaserPower", fill=fill,
        description=f"Best Focus Setting's {wavelength_nm:g} nm line -> {percent:g} %", ready=idle(),
        confirm=Expect("autofocus_optics", shows),
    )


def start_autofocus(project: int, drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """LAS X's autofocus, once, as the window's Autofocus button starts it: a focus search into *project*.

    It moves *drive* from *begin_um* to *end_um* (its capture range around
    where it stands), so both ends go to the limits, as a z-stack's do.
    Never sent again, like a capture.
    """
    parent = checked_project(project)
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"

    def fill(request: Request) -> None:
        request.elements = [f'<AcquireControl RestoreCameraRoi="1" Command="StartAutoFocus" DCParentElementID="{parent}"/>']

    return Write(
        command="autofocus_run",
        values={"project": project, "drive": drive, "axis": axis, "begin_um": begin_um, "end_um": end_um, **(start or {})},
        request="StartAutoFocus",
        fill=fill,
        description=f"Autofocus over {begin_um:g} to {end_um:g} um ({drive}) into project {project}",
        ready=idle(without_limit=True),
        never_resend=True,
    )


def smartcorr(project: int) -> Write:
    """SmartCORR, the window's Start SmartCORR: a scan through the collar positions of its range, into *project*.

    LAS X leaves the collar where the picture was best. Never sent again,
    like a capture.
    """
    parent = checked_project(project)
    elements = [
        '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
        f'<AcquireControl Command="StartMotCorrAutomation" DCParentElementID="{parent}"/>',
    ]

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="smartcorr",
        values={"project": project},
        request="StartMotCorrAutomation",
        fill=fill,
        description=f"SmartCORR into project {project}",
        ready=idle(without_limit=True),
        never_resend=True,
    )


def resonant_scanner(on: bool) -> Write:
    """Switch the resonant scanner on or off; read back from the scan speed's ``isResonant``."""
    if not isinstance(on, bool):
        raise ValueError(f"resonant_scanner takes True or False, not {on!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetScannerToResonant" Enable="{int(on)}" {CURRENT}/>']

    def shows(reading) -> bool:
        return isinstance(reading, dict) and reading.get("isResonant") is on

    return Write(
        command="set_resonant_scanner",
        values={"value": on},
        request="SetScannerToResonant",
        fill=fill,
        description=f"Resonant scanner -> {'on' if on else 'off'}",
        ready=idle(),
        confirm=Expect("scan_speed", shows),
    )


def filter_wheel(wheel_type: int, slot: int, filter_name: str) -> Write:
    """Turn the filter wheel of type *wheel_type* to *slot*, which holds *filter_name*; read back as that wheel's slot."""

    def fill(request: Request) -> None:
        request.elements = [
            (
                f'<Scanner Command="FilterWheel_ApiSetSlot" FilterWheelType="{int(wheel_type)}" SlotIndex="{int(slot)}" '
                f'SetMode="2" SingleSettingIndex="{request.setting}" {CURRENT}><BeamRoute Version="0"/></Scanner>'
            )
        ]

    def shows(wheels) -> bool:
        return any(w.get("type") == wheel_type and w.get("filterIndex") == slot for w in wheels or [])

    return Write(
        command="set_filter_wheel",
        values={"value": filter_name, "wheel": wheel_type},
        request="FilterWheel_ApiSetSlot",
        fill=fill,
        description=f"Filter wheel {wheel_type} -> {filter_name}",
        ready=idle(),
        confirm=Expect("filter_wheels", shows),
    )


def channel_colour(route, colour: str) -> Write:
    """Give the channel of the detector on *route* the colour *colour* (LAS X's LUT name, such as ``"Green"``); read back by its channel.

    The command LAS X's window sent (``SetLut``, simulator, 2026-11-01). It
    changes how the channel is shown, not the light or the detector.
    """
    if not isinstance(colour, str) or not colour.strip():
        raise ValueError(f"a channel colour is LAS X's name of a colour table, such as 'Green', not {colour!r}")
    channel = int(route[1])
    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="SetLut" DetectorOrCameraChannel="1" IsUserDefined="0" SequentialSettingIndex="{request.setting}" '
            f'LutName="{colour}">{beam_route(route)}</Scanner>'
        ]

    return Write(
        command="set_channel_colour",
        values={"value": colour, "channel": channel},
        request="SetLut",
        fill=fill,
        description=f"Channel {channel} -> {colour}",
        ready=idle(),
        confirm=Expect("channel_colours", lambda colours: isinstance(colours, dict) and colours.get(channel) == colour),
    )


def bidirectional(on: bool) -> Write:
    """Scan both ways (True) or one way (False): written into LCS's state tree, as LAS X's window does; read back.

    One of the few values the driver writes there (``transfer.STATE_TREE_WRITES``):
    LAS X's window changes it with no command LCS logs, and a loaded setting
    does not change it (simulator, 2026-11-01).
    """
    if not isinstance(on, bool):
        raise ValueError(f"bidirectional takes True or False, not {on!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="ScanDirectionX" Value="{2 if on else 1}"/>']

    return Write(
        command="set_bidirectional",
        values={"value": on},
        request="ScanDirectionX",
        fill=fill,
        description=f"Bidirectional -> {'on' if on else 'off'}",
        ready=idle(),
        confirm=Expect("bidirectional", lambda shown: shown is on),
    )


def number(name: str, value) -> float:
    """*value* as a finite number; ValueError for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{name} takes a number, not {value!r}")
    return float(value)


def tree_write(command: str, node: str, written, values: dict, description: str, confirm: Expect) -> Write:
    """A change written into LCS's state tree, as LAS X's window does: one value, read back.

    The transfer allows only the nodes on its list (``transfer.STATE_TREE_WRITES``)
    and checks the value against LCS's own range before it writes.
    """

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{written}"/>']

    return Write(
        command=command,
        values=values,
        request=node,
        fill=fill,
        description=description,
        ready=idle(),
        confirm=confirm,
    )


#: How close Phase X must read back; LCS keeps it in steps of about 0.05.
PHASE_X_TOLERANCE = 0.1
#: How close the pinhole must read back, in micrometres (200 read back as 199.9).
PINHOLE_TOLERANCE_UM = 0.5


def phase_x(value) -> Write:
    """Phase X, which lines up the two directions of a bidirectional scan; LAS X's range is -100 to 100."""
    target = number("phase_x", value)
    return tree_write(
        "set_phase_x", "PhaseX", repr(target), {"value": target}, f"Phase X -> {target}",
        Expect("phase_x", lambda shown: abs(shown - target) <= PHASE_X_TOLERANCE),
    )


def pinhole_um(value) -> Write:
    """The pinhole's size in micrometres, with the window's own ``SetPinhole`` (in metres); read back.

    LCS does not keep this command to its range (simulator, 2026-11-02: 2000
    micrometres, its maximum 1000, raised the motor's error and set 2000), so
    the transfer checks LCS's own range before it sends it
    (``transfer.CHECKED_COMMANDS``).
    """
    target = number("pinhole_um", value)

    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="SetPinhole" PinholeDiameter="{target / 1_000_000!r}" SetMode="1" SingleSettingIndex="{request.setting}"/>'
        ]

    return Write(
        command="set_pinhole_um",
        values={"value": target},
        request="SetPinhole",
        fill=fill,
        description=f"Pinhole -> {target} um",
        ready=idle(),
        confirm=Expect("pinhole", lambda shown: abs(shown["metres"] * 1_000_000 - target) <= PINHOLE_TOLERANCE_UM),
    )


def pinhole_unit(unit: str) -> Write:
    """The unit LAS X shows the pinhole in: ``"um"`` (micrometres) or ``"airy"`` (Airy units). The pinhole itself stays."""
    codes = {name: code for code, name in direct_reader.PINHOLE_UNITS.items()}
    if unit not in codes:
        raise ValueError(f"pinhole_unit takes {sorted(codes)}, not {unit!r}")
    return tree_write(
        "set_pinhole_unit", "PinholeUnit", codes[unit], {"value": unit}, f"Pinhole unit -> {unit}",
        Expect("pinhole", lambda shown: shown["unit"] == unit),
    )


def emission_wavelength(nm) -> Write:
    """The emission wavelength, in whole nanometres, that LAS X works the Airy unit out for (380 to 800).

    The pinhole stays as it is; its size in Airy units changes with the wavelength.
    """
    if isinstance(nm, bool) or not isinstance(nm, (int, float)) or nm != int(nm):
        raise ValueError(f"emission_wavelength takes whole nanometres, not {nm!r}")
    target = int(nm)
    return tree_write(
        "set_emission_wavelength", "EmissionWaveLengthForAiryAnddzCalculation", target, {"value": target},
        f"Emission for the Airy unit -> {target} nm",
        Expect("pinhole", lambda shown: shown["emission_nm"] == target),
    )


def bit_depth(bits) -> Write:
    """The bits per pixel a capture is digitised with: one of LAS X's list (8, 12 or 16), read at the write."""
    if isinstance(bits, bool) or not isinstance(bits, int):
        raise ValueError(f"bit_depth takes whole bits, such as 8, 12 or 16, not {bits!r}")
    return tree_write(
        "set_bit_depth", "Resolution", bits, {"value": bits}, f"Bit depth -> {bits}",
        Expect("bit_depth", lambda shown: shown == bits),
    )


def system_optimized_step(on: bool) -> Write:
    """The z-stack's step System Optimized (True), as the window's button, or the step size kept (False).

    On, LAS X takes its own optimal step and may stretch the stack's end to
    fit whole steps; off, the step stays as it is then.
    """
    if not isinstance(on, bool):
        raise ValueError(f"system_optimized_step takes True or False, not {on!r}")
    return tree_write(
        "set_system_optimized_step", "StackCalculationMode", 2 if on else 1, {"value": on},
        f"System optimized step -> {'on' if on else 'off'}",
        Expect("system_optimized_step", lambda shown: shown is on),
    )


#: The settings with a write of their own, by name, beside the table of numbers (:data:`SETTINGS`).
NAMED_SETTINGS = {
    "scan_mode": scan_mode,
    "autofocus": autofocus,
    "format": image_format,
    "resonant_scanner": resonant_scanner,
    "bidirectional": bidirectional,
    "phase_x": phase_x,
    "pinhole_um": pinhole_um,
    "pinhole_unit": pinhole_unit,
    "emission_wavelength": emission_wavelength,
    "bit_depth": bit_depth,
    "system_optimized_step": system_optimized_step,
    "collar": collar,
    "smartcorr_range": smartcorr_range,
    "setting_name": setting_name,
    "sequential_mode": sequential_mode,
    "autofocus_method": autofocus_method,
    "autofocus_precision": autofocus_precision,
    "autofocus_steps": autofocus_steps,
    "autofocus_range_um": autofocus_range_um,
    "autofocus_drive": autofocus_drive,
    "autofocus_format": autofocus_format,
    "autofocus_speed": autofocus_speed,
    "autofocus_bidirectional": autofocus_bidirectional,
    "autofocus_zoom": autofocus_zoom,
}


def named_setting(name: str, value) -> Write:
    """The change of the setting *name* to *value*, from the table or from :data:`NAMED_SETTINGS`; ValueError otherwise."""
    if name in NAMED_SETTINGS:
        return NAMED_SETTINGS[name](value)
    if name in SETTINGS:
        return setting(name, value)
    raise ValueError(f"no setting {name!r}; the settings are {sorted([*SETTINGS, *NAMED_SETTINGS])}")


# =============================================================================
# The stage and the z drives
# =============================================================================

#: How close a move must come, in micrometres, unless the caller says.
XY_TOLERANCE_UM = 20.0
Z_TOLERANCE_UM = 1.0


def stage_element(x_um: float, y_um: float) -> str:
    """The element that moves the stage to (*x_um*, *y_um*), as the window does."""
    return (
        f'<Scanner Command="SetStagePositionInHwOnly" StagePosX="{metres(x_um)}" '
        f'StagePosY="{metres(y_um)}" RelativeCoordinates="0" MoveStageX="1" MoveStageY="1"/>'
    )


def within_xy(x_um, y_um, tolerance):
    """A stage position within *tolerance* micrometres of (*x_um*, *y_um*) on both axes."""

    def check(position) -> bool:
        try:
            return (
                abs(position["x_um"] - x_um) <= tolerance
                and abs(position["y_um"] - y_um) <= tolerance
            )
        except (KeyError, TypeError):
            return False

    return check


def move_xy(x_um: float, y_um: float, *, tolerance=None, start: dict | None = None) -> Write:
    """Move the stage to (*x_um*, *y_um*), in micrometres of the stage's own coordinates.

    Sent once the scanner is idle, and read back until the stage is within
    *tolerance* (``XY_TOLERANCE_UM`` when None); an unconfirmed move is sent
    again, it is the same move. *start* (``from_x_um``, ``from_y_um``: where
    the stage stands) goes to the limits with the target, for the step.
    """
    tolerance = XY_TOLERANCE_UM if tolerance is None else tolerance

    def fill(request: Request) -> None:
        request.elements = [stage_element(x_um, y_um)]

    return Write(
        command="move_xy",
        values={"x_um": x_um, "y_um": y_um, **(start or {})},
        request="SetStagePositionInHwOnly",
        fill=fill,
        description=f"MoveXY -> ({x_um:g}, {y_um:g}) um",
        ready=idle(),
        confirm=Expect("xy", within_xy(x_um, y_um, tolerance)),
    )


def z_step_element(drive: str, delta_um: float) -> str:
    """The element that moves *drive* by *delta_um*, the form LAS X took on the STELLARIS."""
    return (
        f'<Scanner Command="SetZPosition" PosZ="{metres(delta_um)}" RelativeCoordinates="1" '
        f'ZUseMode="{Z_DRIVES[drive]}" {CURRENT}/>'
    )


def z_within(target_um, tolerance):
    """A z reading within *tolerance* micrometres of *target_um*."""

    def check(z_um) -> bool:
        try:
            return abs(float(z_um) - target_um) <= tolerance
        except (TypeError, ValueError):
            return False

    return check


def move_z(drive: str, target_um: float, *, tolerance=None, now=None, start: dict | None = None) -> Write:
    """Move the z drive *drive* (``"z-galvo"`` or ``"z-wide"``) to *target_um*.

    LAS X's plain Navigator took a z move as a step from where the drive
    stands (``RelativeCoordinates="1"``; STELLARIS, 2026-10-15), so the
    element is built just before every send from the drive's position then
    (*now*, a function answering it in micrometres; the direct reader when
    None). Read back until the drive is within *tolerance*
    (``Z_TOLERANCE_UM`` when None). *start* (``from_z_galvo_um`` or
    ``from_z_wide_um``) goes to the limits with the target, for the step.
    """
    if drive not in Z_DRIVES:
        raise ValueError(f"no z drive {drive!r}; the drives are {sorted(Z_DRIVES)}")
    tolerance = Z_TOLERANCE_UM if tolerance is None else tolerance
    read_now = now if now is not None else (lambda: direct_reader.read_z_um(drive))

    def fill(request: Request) -> None:
        request.elements = [z_step_element(drive, target_um - read_now())]

    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    return Write(
        command="move_z",
        values={axis: target_um, **(start or {})},
        request="SetZPosition",
        fill=fill,
        description=f"Z -> {target_um:g} um ({drive})",
        ready=idle(),
        confirm=Expect("z", z_within(target_um, tolerance), about=drive),
    )


# =============================================================================
# The z-stack: LAS X's z-stack panel, as the window sent it (simulator, 2026-11-01)
# =============================================================================

#: How close a z-stack's begin must come, in micrometres.
STACK_TOLERANCE_UM = 0.1


def unwrapped(reading):
    """A z-stack reading as a stack, or None: the profile's row answers ``{"stack": ...}``."""
    if isinstance(reading, dict) and "stack" in reading:
        return reading["stack"]
    return reading


def z_stack_drive(drive: str) -> Write:
    """Have the z-stack (and a tile's z) use *drive*, ``"z-galvo"`` or ``"z-wide"``; read back as the drive in use."""
    if drive not in Z_DRIVES:
        raise ValueError(f"no z drive {drive!r}; the drives are {sorted(Z_DRIVES)}")
    elements = [f'<Scanner Command="SetZUseMode" UseMode="{Z_DRIVES[drive]}"/>']

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="set_z_stack_drive",
        values={"drive": drive},
        request="SetZUseMode",
        fill=fill,
        description=f"Z-stack drive -> {drive}",
        ready=idle(),
        confirm=Expect("z_drive_in_use", lambda reading: reading == drive),
    )


def z_stack_range(drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """Set the z-stack from *begin_um* to *end_um* on *drive*, both at once; begin above end runs it downwards.

    Read back as the z-stack: the begin within :data:`STACK_TOLERANCE_UM`,
    and the end within one step, because LCS stretches the end to fit whole
    steps and still answers ``Error="0"`` (1530 um became 1531.6 um with
    2.4 um steps). *start* (where the drive stands) goes to the limits: each
    end is checked like a z move from there.
    """
    if drive not in Z_DRIVES:
        raise ValueError(f"no z drive {drive!r}; the drives are {sorted(Z_DRIVES)}")
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    elements = [
        (
            f'<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="1" '
            f'BeginValue="{metres(begin_um)}" EndValue="{metres(end_um)}" {CURRENT}/>'
        )
    ]

    def fill(request: Request) -> None:
        request.elements = list(elements)

    def shows(reading) -> bool:
        stack = unwrapped(reading)
        if not isinstance(stack, dict) or stack.get("mode") != drive:
            return False
        try:
            same_way = (stack["end"] - stack["begin"]) * (end_um - begin_um) > 0
            return (
                same_way
                and abs(stack["begin"] - begin_um) <= STACK_TOLERANCE_UM
                and abs(stack["end"] - end_um) <= stack["stepSize"] + STACK_TOLERANCE_UM
            )
        except (KeyError, TypeError):
            return False

    return Write(
        command="set_z_stack_range",
        values={"drive": drive, "axis": axis, "begin_um": begin_um, "end_um": end_um, **(start or {})},
        request="SetZStackDefinition",
        fill=fill,
        description=f"Z-stack -> {begin_um:g} to {end_um:g} um ({drive})",
        ready=idle(),
        confirm=Expect("z_stack", shows),
    )


def z_stack_step(step_um: float) -> Write:
    """Set the z-stack's step to *step_um*; the number of sections follows from the range. Read back within 0.05 um."""
    if isinstance(step_um, bool) or not isinstance(step_um, (int, float)) or not step_um > 0:
        raise ValueError(f"a z-stack step is a number of micrometres above 0, not {step_um!r}")
    elements = [f'<Scanner Command="SetStackStepSize" StepSizeInMeter="{metres(step_um)}" {CURRENT}/>']

    def fill(request: Request) -> None:
        request.elements = list(elements)

    def shows(reading) -> bool:
        stack = unwrapped(reading)
        try:
            return abs(stack["stepSize"] - step_um) <= 0.05
        except (KeyError, TypeError):
            return False

    return Write(
        command="set_z_stack_step",
        values={"value": step_um},
        request="SetStackStepSize",
        fill=fill,
        description=f"Z-stack step -> {step_um:g} um",
        ready=idle(),
        confirm=Expect("z_stack", shows),
    )


def z_stack_picture(project: int, drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """Take the whole z-stack LAS X is set to, into *project*: the window's Start.

    Capture Image takes one plane even with a stack set; Start took all of
    them in one picture (simulator, 2026-10-30). The stack's ends go to the
    limits, as for :func:`z_stack_range`, because the scan moves z through
    them. Never sent again, like a capture.
    """
    parent = checked_project(project)
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    elements = [
        '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
        f'<AcquireControl Command="StartAcquiring" DCParentElementID="{parent}"/>',
    ]

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="z_stack_picture",
        values={"project": project, "drive": drive, "axis": axis, "begin_um": begin_um, "end_um": end_um, **(start or {})},
        request="StartAcquiring",
        fill=fill,
        description=f"Z-stack {begin_um:g} to {end_um:g} um ({drive}) into project {project}",
        ready=idle(without_limit=True),
        never_resend=True,
    )


# =============================================================================
# A whole setting as a file: LAS X's Setting panel, save and load (simulator, 2026-11-01)
# =============================================================================


def save_setting(path) -> Write:
    """Save the current setting to the file *path*, as LAS X's Setting panel does; changes nothing on the instrument."""

    def fill(request: Request) -> None:
        request.elements = [f'<ATL Command="ATL_Block_Save_To_XML" FileName="{path}" Attribute="2"/>']

    return Write(
        command="save_setting",
        values={"path": str(path)},
        request="ATL_Block_Save_To_XML",
        fill=fill,
        description=f"Save the setting to {path}",
        ready=idle(),
    )


def load_setting(path, values: dict) -> Write:
    """Load the setting in the file *path*, as LAS X's Setting panel does.

    *values* (``laser_power`` in percent, ``detector_gain``) is what the
    limits are shown of the file before it is loaded. Read back by the
    procedure that loads it, which knows what was meant to change.
    """

    def fill(request: Request) -> None:
        request.elements = [
            (
                f'<ATL Command="ATL_Block_Load_From_XML" FileName="{path}" CreateNewInstedOfReplace="0" '
                'ElementIDAfterLoadedBlock="0" Attribute="2"/>'
            )
        ]

    return Write(
        command="load_setting",
        values={"path": str(path), **values},
        request="ATL_Block_Load_From_XML",
        fill=fill,
        description=f"Load the setting {path}",
        ready=idle(),
    )


# =============================================================================
# The objective
# =============================================================================


def objective_element(slot: int) -> str:
    """The element that turns the objective turret to *slot*, as the window does."""
    return f'<Microscope Command="SetSlotPosition" SlotPosition="{int(slot)}"/>'


def slot_is(slot: int):
    """An objective reading whose slot is *slot*."""

    def check(objective) -> bool:
        try:
            return objective["slotIndex"] == slot
        except (KeyError, TypeError):
            return False

    return check


def set_objective(slot: int, name: str) -> Write:
    """Change the objective to the one in *slot* (called *name*), without moving the stage."""

    def fill(request: Request) -> None:
        request.elements = [objective_element(slot)]

    return Write(
        command="set_objective",
        values={"objective_slot": slot},
        request="SetSlotPosition",
        fill=fill,
        description=f"Objective -> {name} (slot {slot})",
        ready=idle(),
        confirm=Expect("objective", slot_is(slot)),
    )


# =============================================================================
# Taking a picture: the window's Capture Image, and its Preview over positions
# =============================================================================


def checked_project(number) -> int:
    """*number* when it can be a LAS X project; a ValueError otherwise, as no picture may go without one."""
    if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
        raise ValueError(
            f"a picture needs the LAS X project it goes into, not {number!r}"
        )
    return number


def capture_elements(project: int) -> list[str]:
    """One picture with the current settings, into *project*: the window's Capture Image."""
    parent = checked_project(project)
    return [
        '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
        f'<AcquireControl Command="CaptureSingleFrame" DCParentElementID="{parent}" '
        'IsSingleImageCapture="1" SingleScanMode="0" AdditionalValue="1.0000000000" '
        'AdditionalValue2="0"/>',
    ]


def capture(project: int) -> Write:
    """Take one picture with the current settings, into the LAS X project *project*.

    Never sent again, after an error or otherwise: a second send is a second
    picture. Sent once the scanner is idle, however long that takes, and
    with nothing read back: whether the scan ran is watched after the send
    (``procedures/changes.py``).
    """
    elements = capture_elements(project)

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="capture",
        values={"project": project},
        request="CaptureSingleFrame",
        fill=fill,
        description=f"Capture into project {project}",
        ready=idle(without_limit=True),
        never_resend=True,
    )


def tile_elements(positions_um: Iterable[tuple[float, float, float]], tile_z_um: float | None = None) -> str:
    """The positions of a tile scan, as LAS X's window lists them.

    LCS reads a tile's ``ZPos`` as the position of the drive LAS X's z-stack
    uses (simulator, 2026-11-01). *tile_z_um*, when given, is that drive's
    position and is sent for every tile instead of the position's own z.
    """
    tiles = "".join(
        f'<Tile XPos="{metres(x)}" YPos="{metres(y)}" ZPos="{metres(z if tile_z_um is None else tile_z_um)}" '
        'FocusStabilizerOffset="0.0000000000"/>'
        for x, y, z in positions_um
    )
    if not tiles:
        raise ValueError("a tile scan with no position")
    return (
        '<Scanner Command="SetStageIrregularTileScanPositions" FocusOffset="0.0000000000">'
        f"{tiles}</Scanner>"
    )


def tile_scan_elements(
    positions_um: Iterable[tuple[float, float, float]], project: int, tile_z_um: float | None = None
) -> list[str]:
    """The window's Preview over *positions_um* (x, y, z) into *project*: the preparation, then the start.

    LAS X scans the positions in one acquisition into one picture of all the
    fields (``Mosaic<n>``), which it puts in a "TileScans" collection of the
    project (STELLARIS, 2026-10-15). :func:`after_tile_scan` puts back what
    the preparation changed.
    """
    parent = checked_project(project)
    positions = tile_elements(positions_um, tile_z_um)
    return [
        '<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>',
        '<Scanner Command="SetStageUseMode" StageUseMode="7"/>',
        positions,
        '<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="1"/>',
        f'<Scanner Command="ScanMode" ScanMode="xyt" {CURRENT}/>',
        '<Scanner Command="ActivateAutoFocus" Activate="0" InitNew="0"/>',
        '<Scanner Command="SetStageUseMode" StageUseMode="7"/>',
        positions,
        f'<AcquireControl Command="StartAcquiring" DCParentElementID="{parent}"/>',
    ]


def tile_scan(
    positions_um: list[tuple[float, float, float]], project: int, *, tile_z_um: float | None = None, start: dict | None = None
) -> Write:
    """Take a tile scan over *positions_um* (x, y, z-galvo in micrometres) into the LAS X project *project*.

    Every position is shown to the limits first, with its z-galvo. With
    *tile_z_um* the tiles carry that z instead (:func:`tile_elements`).
    Never sent again, like a capture; sent once the scanner is idle,
    however long that takes.
    """
    positions = [tuple(float(v) for v in p) for p in positions_um]
    elements = tile_scan_elements(positions, project, tile_z_um)

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="tile_scan",
        values={"positions_um": positions, "project": project, **(start or {})},
        request="StartAcquiring",
        fill=fill,
        description=f"Tile scan over {len(positions)} positions into project {project}",
        ready=idle(without_limit=True),
        never_resend=True,
    )


def after_tile_scan_elements(x_um: float, y_um: float, z_um: float) -> list[str]:
    """What the window sends after its Preview: the stage back to (*x_um*, *y_um*), *z_um* as LCS reads a tile's z, the rest as it was."""
    return [
        f'<Scanner Command="StageMoveToCoordinates" PosX="{metres(x_um)}" PosY="{metres(y_um)}" '
        f'PosZ="{metres(z_um)}"/>',
        '<Scanner Command="SetStageUseMode" StageUseMode="0"/>',
        '<ATL Command="ATL_SuppressAtlasAttachment" EnableSuppression="0"/>',
        f'<Scanner Command="ScanMode" ScanMode="xyz" {CURRENT}/>',
        '<Scanner Command="ActivateAutoFocus" Activate="1" InitNew="0"/>',
    ]


def after_tile_scan(
    x_um: float, y_um: float, z_um: float, *, tolerance=None, tile_z_um: float | None = None, start: dict | None = None
) -> Write:
    """Put back what a tile scan changed, and the stage to (*x_um*, *y_um*, *z_um*).

    *z_um* is z-galvo, shown to the limits; with *tile_z_um* the element
    carries that z instead, as the tile scan did. Read back until the stage
    is within *tolerance* (``XY_TOLERANCE_UM`` when None); sent again when
    it is not, it is the same put-back.
    """
    tolerance = XY_TOLERANCE_UM if tolerance is None else tolerance
    elements = after_tile_scan_elements(x_um, y_um, z_um if tile_z_um is None else tile_z_um)

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="after_tile_scan",
        values={"x_um": x_um, "y_um": y_um, "z_galvo_um": z_um, **(start or {})},
        request="StageMoveToCoordinates",
        fill=fill,
        description=f"After the tile scan: the stage back to ({x_um:g}, {y_um:g}) um, scan mode xyz",
        ready=idle(),
        confirm=Expect("xy", within_xy(x_um, y_um, tolerance)),
    )
