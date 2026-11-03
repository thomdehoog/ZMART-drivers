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


#: The settings whose value LAS X's window can apply to every setting with a box beside it.
TO_ALL_SETTINGS = ("line_average", "line_accumulation", "frame_average", "frame_accumulation", "pinhole_um")


def to_all_settings(name: str, number: float) -> Write:
    """*number*, the value *name* has now, sent to every setting (``SetMode="1"``), as the window's "apply to all
    settings" box does when ticked (simulator, 2026-11-02); the value goes to the limits as the setting's own."""
    if name not in TO_ALL_SETTINGS:
        raise ValueError(f"LAS X's window applies only {', '.join(TO_ALL_SETTINGS)} to all settings, not {name!r}")
    if name == "pinhole_um":
        return pinhole_um(number, all_settings=True)
    setting = SETTINGS[name]

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="{setting.lasx_command}" {setting.attribute}="{value(number)}" SetMode="1" '
                            f'SingleSettingIndex="{request.setting}" {CURRENT}/>']

    return Write(command=setting.command, values={"value": number}, request=setting.lasx_command, fill=fill,
                 description=f"{setting.describe.format(value=value(number))}, in every setting", ready=idle(),
                 confirm=Expect(setting.reading, setting_shows(setting, number)))


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


def setting_enabled(setting_id: int, on: bool, *, all_settings: bool = False) -> Write:
    """Switch the current setting on or off for scanning, as the window's checkbox does, or every setting, as Bulk
    Options' Disable All Settings does (``SetMode="1"``); read back.

    Off, LAS X takes no picture with it: LCS's ``CanDoScanSingle`` went False
    (simulator, 2026-11-01 and 2026-11-03).
    """
    if not isinstance(on, bool):
        raise ValueError(f"a setting is switched on with True or off with False, not {on!r}")

    def fill(request: Request) -> None:
        request.elements = [
            f'<ATL SettingID="{int(setting_id)}" SetMode="{1 if all_settings else 0}" EnableForScanning="{int(on)}" '
            'Command="ATL_Setting_EnableDisableForScanning" Attribute="0"/>'
        ]

    def shows(shown) -> bool:
        return all(each["enabled"] is on for each in shown["all"]) if all_settings else shown["enabled"] is on

    return Write(
        command="set_setting_enabled",
        values={"value": on},
        request="ATL_Setting_EnableDisableForScanning",
        fill=fill,
        description=f"{'Every setting' if all_settings else 'Setting ' + str(setting_id)} -> {'on' if on else 'off'}",
        ready=idle(),
        confirm=Expect("setting", shows),
    )


def hardware_connected(on: bool) -> Write:
    """The settings toolbar's apply-to-hardware switch, as it sends it (``ATL_ActivateHardwareConnectedMode``; simulator,
    2026-11-03): on, a change to a setting moves the hardware; off, LAS X keeps it in its settings only. Read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<ATL Activate="{int(bool(on))}" Command="ATL_ActivateHardwareConnectedMode" Attribute="0"/>']

    return Write(
        command="apply_to_hardware",
        values={"value": bool(on)},
        request="ATL_ActivateHardwareConnectedMode",
        fill=fill,
        description=f"Changes to the settings -> {'applied to the hardware' if on else 'kept from the hardware'}",
        ready=idle(),
        confirm=Expect("apply_to_hardware", lambda found: found is bool(on)),
    )


def settings_locked(locked: bool) -> Write:
    """Lock every setting's detectors and lines from LAS X's Dye Assistant, as Bulk Options' Lock All Settings does
    (``LockState="1"``, ``SetMode="1"``), or free them all (``LockState="0"``) (simulator, 2026-11-03); read back from
    the current setting's detectors in use."""

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="LockSetting" LockState="{int(bool(locked))}" SetMode="1" SingleSettingIndex="{request.setting}"/>']

    return Write(
        command="lock_all_settings",
        values={"value": bool(locked)},
        request="LockSetting",
        fill=fill,
        description=f"Every setting -> {'locked' if locked else 'free'} for the Dye Assistant",
        ready=idle(),
        confirm=Expect("detectors", lambda found: bool(found) and all(d.get("lockedForDyeAssistant") is bool(locked) for d in found)),
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


def laser_line_lock(line_route, line_index: int, locked: bool) -> Write:
    """Lock one laser line from LAS X's Dye Assistant, or free it, as the lock on its row in the laser panel does
    (``BlockLightSourceLineForDyeAssistant`` alone, ``BlockedState`` 1 locked, 0 free; simulator, 2026-11-03); read back."""
    state = int(bool(locked))

    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="BlockLightSourceLineForDyeAssistant" LineIndex="{int(line_index)}" '
            f'SequentialSettingIndex="{request.setting}" BlockedState="{state}">{beam_route(line_route)}</Scanner>'
        ]

    def shows(found) -> bool:
        held = any(route_of(each["route"]) == tuple(line_route) and each["line_index"] == int(line_index) for each in found or [])
        return held is bool(locked)

    return Write(
        command="lock_laser_line",
        values={"value": bool(locked)},
        request="BlockLightSourceLineForDyeAssistant",
        fill=fill,
        description=f"Laser line {tuple(line_route)}#{int(line_index)} -> {'locked' if locked else 'free'} for the Dye Assistant",
        ready=idle(),
        confirm=Expect("locked_laser_lines", shows),
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


#: TauSeparation's lifetime scale in the window, in nanoseconds (simulator, 2026-11-02); LCS keeps no range of its own.
TAU_LIFETIME_NS = (0.07, 9.37)


#: TauInteraction's donor lifetime scale in the window, in nanoseconds (simulator, 2026-11-03).
TAU_DONOR_NS = (0.1, 11.5)


def tau_donor_lifetime(route, name: str, ns: float) -> Write:
    """TauInteraction's average donor lifetime of the detector *name*, in nanoseconds, as its window sends it; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetTauInteractionDonorLifetime" DonorLifetime="{ns / 1e9!r}">{beam_route(route)}</Scanner>']

    return Write(command="set_tau_donor_lifetime", values={"value": float(ns)}, request="SetTauInteractionDonorLifetime", fill=fill,
                 description=f"Detector {name} donor lifetime -> {ns:g} ns", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: abs((((modes or {}).get(name) or {}).get("donor_lifetime_ns") or -1) - ns) <= 0.01))


#: TauScan's lifetime range as LCS keeps it, in nanoseconds (simulator, 2026-11-03).
TAU_SCAN_NS = (0.05, 11.5)


def tau_scan(route, name: str, images=None, range_ns=None) -> list[Write]:
    """TauScan's number of images and lifetime range ``(begin_ns, end_ns)`` of the detector *name*, with the TauScan
    window's own commands; read back."""
    planned = []

    def write(command, element, description, shows):
        def fill(request: Request) -> None:
            request.elements = [element.replace("{route}", beam_route(route))]

        return Write(command="set_tau_scan", values={"value": description}, request=command, fill=fill,
                     description=f"Detector {name} TauScan {description}", ready=idle(),
                     confirm=Expect("detector_modes", lambda modes: shows(((modes or {}).get(name) or {}).get("tau_scan") or {})))

    if images is not None:
        planned.append(write("SetDetectorTauScanCount", f'<Scanner Command="SetDetectorTauScanCount" ImageCount="{int(images)}">{{route}}</Scanner>',
                             f"images -> {images}", lambda found: found.get("images") == images))
    if range_ns is not None:
        begin, end = range_ns
        planned.append(write(
            "SetDetectorTauScanRange",
            f'<Scanner Command="SetDetectorTauScanRange" GateRangeBegin="{begin / 1e9!r}" GateRangeEnd="{end / 1e9!r}">{{route}}</Scanner>',
            f"range -> {begin:g} to {end:g} ns",
            lambda found: bool(found.get("range_ns")) and abs(found["range_ns"][0] - begin) <= 0.01 and abs(found["range_ns"][1] - end) <= 0.01,
        ))
    return planned


#: TauGating's scale in the window, in nanoseconds (simulator, 2026-11-02).
TAU_GATE_NS = (-1.0, 11.5)
#: The most gates a TauGating channel takes, as the window allows.
MOST_TAU_GATES = 3


def tau_gate_change(command: str, route, name: str, elements: list[str], description: str, shows) -> Write:
    """One change of TauGating's channels or gates of the detector *name*, as the TauGating window sends it; read back."""

    def fill(request: Request) -> None:
        request.elements = [element.replace("{setting}", str(request.setting)).replace("{route}", beam_route(route)) for element in elements]

    return Write(command=command, values={"value": description}, request=elements[0].split('"')[1], fill=fill,
                 description=f"Detector {name} {description}", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: shows(((modes or {}).get(name) or {}).get("gates") or [])))


def checked_gate(begin_ns, end_ns) -> tuple[float, float]:
    """``(begin, end)`` in nanoseconds, within TauGating's scale, begin before end; ValueError otherwise."""
    low, high = TAU_GATE_NS
    if any(isinstance(n, bool) or not isinstance(n, (int, float)) for n in (begin_ns, end_ns)) or not low <= begin_ns < end_ns <= high:
        raise ValueError(f"a gate runs from {low} to {high} ns, begin before end; not {begin_ns!r} to {end_ns!r}")
    return float(begin_ns), float(end_ns)


def near(gate, begin, end) -> bool:
    return abs(gate[0] - begin) <= 0.01 and abs(gate[1] - end) <= 0.01


def tau_gate(route, name: str, channel: int, gate: int, begin_ns: float, end_ns: float) -> Write:
    """TauGating's *gate* (1 the first) of *channel* (1 the first) moved to *begin_ns* .. *end_ns*, as its handles do."""
    begin, end = checked_gate(begin_ns, end_ns)
    element = (f'<Scanner Command="SetDigitalGateConfig" ChannelIndex="{channel - 1}" SetMode="0" SingleSettingIndex="{{setting}}" '
               f'GateIndex1="{gate - 1}" GateBegin1="{begin / 1e9!r}" GateEnd1="{end / 1e9!r}">{{route}}</Scanner>')
    return tau_gate_change("set_tau_gate", route, name, [element], f"channel {channel} gate {gate} -> {begin:g} to {end:g} ns",
                           lambda gates: len(gates) >= channel and len(gates[channel - 1]["gates"]) >= gate
                           and near(gates[channel - 1]["gates"][gate - 1], begin, end))


def tau_gate_added(route, name: str, channel: int, begin_ns: float, end_ns: float) -> Write:
    """A gate added to TauGating's *channel*, as the window's +."""
    begin, end = checked_gate(begin_ns, end_ns)
    element = (f'<Scanner Command="AddDigitalGate" ChannelIndex="{channel - 1}" GateBegin="{begin / 1e9!r}" GateEnd="{end / 1e9!r}" '
               f'SetMode="0" SingleSettingIndex="{{setting}}">{{route}}</Scanner>')
    return tau_gate_change("add_tau_gate", route, name, [element], f"channel {channel} gate added, {begin:g} to {end:g} ns",
                           lambda gates: len(gates) >= channel and any(near(g, begin, end) for g in gates[channel - 1]["gates"]))


def tau_gate_removed(route, name: str, channel: int, gate: int, count: int) -> Write:
    """TauGating's *gate* of *channel* removed, as the window's -."""
    element = (f'<Scanner Command="RemoveDigitalGate" ChannelIndex="{channel - 1}" GateIndex="{gate - 1}" SetMode="0" '
               f'SingleSettingIndex="{{setting}}">{{route}}</Scanner>')
    return tau_gate_change("remove_tau_gate", route, name, [element], f"channel {channel} gate {gate} removed",
                           lambda gates: len(gates) >= channel and len(gates[channel - 1]["gates"]) == count - 1)


def tau_gate_channel_added(route, name: str, count: int, begin_ns: float, end_ns: float) -> Write:
    """A TauGating channel added with one gate, as the window's Add Channel sends it."""
    begin, end = checked_gate(begin_ns, end_ns)
    elements = [
        '<Scanner Command="AddDetectorImageChannel" IsChannelDigital="1">{route}</Scanner>',
        (f'<Scanner Command="AddDigitalGate" ChannelIndex="{count}" GateBegin="{begin / 1e9!r}" GateEnd="{end / 1e9!r}" '
         f'SetMode="0" SingleSettingIndex="{{setting}}">{{route}}</Scanner>'),
    ]
    return tau_gate_change("add_tau_gate_channel", route, name, elements, f"channel added, {begin:g} to {end:g} ns",
                           lambda gates: len(gates) == count + 1)


def tau_gate_channel_removed(route, name: str, channel: int, count: int) -> Write:
    """TauGating's *channel* removed, as the window's Remove Channel."""
    element = f'<Scanner Command="RemoveDetectorImageChannel" ChannelIndex="{channel - 1}">{{route}}</Scanner>'
    return tau_gate_change("remove_tau_gate_channel", route, name, [element], f"channel {channel} removed",
                           lambda gates: len(gates) == count - 1)


def tau_channel_lifetime(route, name: str, channel: int, ns: float) -> Write:
    """The lifetime of TauSeparation's *channel* (1 or 2) of the detector *name*, in nanoseconds, as the window's slider sends it.

    ``SetDetectorTauSeparationConfig`` in seconds; LCS moves the gate between
    the channels itself. Within the window's scale, :data:`TAU_LIFETIME_NS`.
    Read back within 0.01 ns.
    """
    if isinstance(ns, bool) or not isinstance(ns, (int, float)) or not TAU_LIFETIME_NS[0] <= ns <= TAU_LIFETIME_NS[1]:
        raise ValueError(f"a tau lifetime is {TAU_LIFETIME_NS[0]} to {TAU_LIFETIME_NS[1]} ns, as the window's scale runs, not {ns!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetDetectorTauSeparationConfig" TauChannelIndex="{channel - 1}" '
                            f'SeparationLine1="{float(ns) / 1e9!r}">{beam_route(route)}</Scanner>']

    def shows(modes) -> bool:
        lifetimes = (modes or {}).get(name, {}).get("tau_lifetimes_ns") or []
        return len(lifetimes) >= channel and abs(lifetimes[channel - 1] - ns) <= 0.01

    return Write(command="set_tau_channel_lifetime", values={"value": float(ns)}, request="SetDetectorTauSeparationConfig", fill=fill,
                 description=f"Detector {name} tau channel {channel} -> {ns:g} ns", ready=idle(), confirm=Expect("detector_modes", shows))


def find_tau_lifetimes(route, name: str) -> Write:
    """TauSeparation's Find for the detector *name*, as the window's button sends it: LCS looks for the two lifetimes.

    LCS answers it with no verdict; whether it found them, it logs
    (``changes.find_tau_lifetimes`` reads it). Never sent again.
    """

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="FindTauSeparationLifeTimes">{beam_route(route)}</Scanner>']

    return Write(command="find_tau_lifetimes", values={}, request="FindTauSeparationLifeTimes", fill=fill,
                 description=f"Detector {name} tau lifetimes -> found by LAS X", ready=idle(), never_resend=True)


#: The most TauSeparation channels LCS takes (an eighth was ignored; simulator, 2026-11-03), and the fewest.
MOST_TAU_CHANNELS = 7
FEWEST_TAU_CHANNELS = 2


def tau_channel_added(route, name: str, count: int) -> Write:
    """A TauSeparation channel added to the detector *name*, as its window's +; LCS gives it a lifetime, the colour can be set after."""

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="AddDetectorTauSeparationChannel">{beam_route(route)}</Scanner>']

    return Write(command="add_tau_channel", values={"value": count + 1}, request="AddDetectorTauSeparationChannel", fill=fill,
                 description=f"Detector {name} TauSeparation channel added", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: len(((modes or {}).get(name) or {}).get("tau_colours") or []) == count + 1))


def tau_channel_removed(route, name: str, channel: int, count: int) -> Write:
    """TauSeparation's *channel* (1 the first) of the detector *name* removed, as its window's -."""

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="RemoveDetectorTauSeparationChannel" ChannelIndex="{channel - 1}">{beam_route(route)}</Scanner>']

    return Write(command="remove_tau_channel", values={"value": channel}, request="RemoveDetectorTauSeparationChannel", fill=fill,
                 description=f"Detector {name} TauSeparation channel {channel} removed", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: len(((modes or {}).get(name) or {}).get("tau_colours") or []) == count - 1))


def tau_channel_colour(route, name: str, setting_id: int, channel: int, colour: str, locked: bool = True) -> Write:
    """The colour of TauSeparation's *channel* (1 or 2) of the detector *name*, as the window's colour pick sends it; read back.

    ``SetDetectorTauSeparationLut`` names the setting by its ID, not its place
    (simulator, 2026-11-02). When a colour is picked, the window also moves
    the other tau channels' colours on, to keep them apart; the driver changes
    only the channel asked.
    """
    if not isinstance(colour, str) or not colour.strip():
        raise ValueError(f"a tau channel's colour is LAS X's name of a colour table, such as 'Red', not {colour!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="SetDetectorTauSeparationLut" TauChannelIndex="{channel - 1}" SetMode="2" '
                            f'SingleSettingID="{int(setting_id)}" IsUserDefined="{int(bool(locked))}" LutName="{colour}">{beam_route(route)}</Scanner>']

    def shows(modes) -> bool:
        colours = (modes or {}).get(name, {}).get("tau_colours") or []
        return len(colours) >= channel and colours[channel - 1] == colour

    return Write(command="set_tau_channel_colour", values={"value": colour}, request="SetDetectorTauSeparationLut", fill=fill,
                 description=f"Detector {name} tau channel {channel} -> {colour}", ready=idle(), confirm=Expect("detector_modes", shows))


#: The operating modes by name and LCS's code for them (``OperatingMode``), as the window's Operating Mode list names them.
OPERATING_CODES = {"Fast Mode": 4, "Counting": 1, "Analog": 10, "Reflection": 11}


def spectrum_shown(place: int, which: str, on: bool) -> Write:
    """The current setting's panel drawing the dyes' *which* spectrum ("excitation" or "emission"), written as the settings
    toolbar's button does (simulator, 2026-11-03); read back."""
    node = "ShowExcitationSpectrum" if which == "excitation" else "ShowEmissionSpectrum"

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{int(bool(on))}" At="{int(place)}"/>']

    return Write(command="show_spectra", values={"value": bool(on)}, request=node, fill=fill,
                 description=f"{which.capitalize()} spectra -> {'shown' if on else 'hidden'}", ready=idle(),
                 confirm=Expect("spectra", lambda found: bool(found) and found[which] is bool(on)))


def detector_excitation_line(at, name: str, place: int, shown: str) -> Write:
    """The detector *name*'s excitation line, by its *place* in the detector panel's list (0 Auto, then the setting's lines that
    are on), written into its setting's entry (*at*) as the window does (simulator, 2026-11-03); read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="TimegateReferenceLineIndex" Value="{int(place)}" At="{",".join(str(int(n)) for n in at)}"/>']

    return Write(command="set_excitation_line", values={"value": shown}, request="TimegateReferenceLineIndex", fill=fill,
                 description=f"Detector {name} excitation line -> {shown}", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: ((modes or {}).get(name) or {}).get("reference_line") == place))


def detector_dye(at, name: str, dye: str) -> Write:
    """The dye of the detector *name* (LAS X's ``Namespace/Name``), written into its setting's entry (*at*) as the detector
    panel's Dye list does (simulator, 2026-11-03); read back from the detector."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="DyeName" Value="{dye}" At="{",".join(str(int(n)) for n in at)}"/>']

    return Write(command="set_detector_dye", values={"value": dye}, request="DyeName", fill=fill,
                 description=f"Detector {name} dye -> {dye}", ready=idle(),
                 confirm=Expect("detectors", lambda found: any(d.get("name") == name and d.get("dye") == dye for d in found or [])))


def detector_tau_contrast(at, name: str, on: bool) -> Write:
    """TauContrast of the detector *name* on or off, written into its setting's entry (*at*: the setting's, photometer's and
    detector's places) as the detector panel's switch does (simulator, 2026-11-03); read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="IsLifetimeContrastActive" Value="{int(bool(on))}" At="{",".join(str(int(n)) for n in at)}"/>']

    return Write(command="set_detector_tau_contrast", values={"value": bool(on)}, request="IsLifetimeContrastActive", fill=fill,
                 description=f"Detector {name} TauContrast -> {'on' if on else 'off'}", ready=idle(),
                 confirm=Expect("detector_modes", lambda modes: ((modes or {}).get(name) or {}).get("tau_contrast") is bool(on)))


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


def aobs_line(at: int, wavelength: float, node: str, on: bool) -> Write:
    """One AOBS line's Reflection (*node* ``IsReflection``) or Enhanced Dynamics (``IsDoubleModeEnabled``),
    written into LCS's state tree at the line's place *at*, as the AOBS Configuration window does; read back.
    """
    what = {"IsReflection": ("reflection", "Reflection"), "IsDoubleModeEnabled": ("enhanced_dynamics", "Enhanced Dynamics")}[node]

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{int(bool(on))}" At="{int(at)}"/>']

    def shows(lines) -> bool:
        return any(line["at"] == at and line[what[0]] is bool(on) for line in lines or [])

    return Write(command=f"set_aobs_{what[0]}", values={"value": bool(on)}, request=node, fill=fill,
                 description=f"AOBS {wavelength:g} nm {what[1]} -> {'on' if on else 'off'}", ready=idle(),
                 confirm=Expect("aobs_lines", shows))


#: The tau modes by name and LCS's code for them (``DigitalGatingMode``), as the window's TauModes list names them
#: (None, Intensity, TauGating, TauScan, TauSeparation, TauInteraction on the simulator, 2026-11-03; GateScan was not offered).
TAU_CODES = {"None": 0, "Intensity": 2, "TauGating": 3, "TauScan": 5, "TauSeparation": 6, "TauInteraction": 10}


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


def setting_dyes(lines, detectors, others=None, place: int = 0, sequential_mode: int = 0) -> Write:
    """A setting's laser lines with their powers and its detectors with their bands, dyes and colours, in one command, as the
    window sends it when a dye is dropped on a detector (``SetConfocalDefinitionFromDyeAssistant``, ``IsAutoFocusDefinition`` 0;
    simulator, 2026-11-02). The command describes the whole job, one sequence per setting (2026-11-03): sent with fewer, LCS
    cut the job down to them. *lines*: ``(route, line_index, wavelength_nm, percent)``; *detectors*: ``(name, route, begin_nm,
    end_nm, dye, colour)`` for the setting at *place*; *others*: every setting's definition as it stands
    (``get_setting_definitions``), sent again unchanged. LCS rebuilds the setting's light and detection from it: lines and
    detectors left out are switched off. The powers go to the limits; read back from the detectors in use.
    """
    if not lines or not detectors:
        raise ValueError("a setting's dyes need at least one laser line and one detector")
    for _route, _index, nm, percent in lines:
        if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
            raise ValueError(f"a line's power is above 0 and at most 100 percent, not {percent!r} ({nm:g} nm)")
    for name, _route, begin, end, dye, colour in detectors:
        if not begin < end:
            raise ValueError(f"{name}'s band runs from a lower to a higher wavelength, not {begin!r} to {end!r}")
        for text, what in ((dye, "a dye"), (colour, "a colour")):
            if not isinstance(text, str) or not text or any(c in text for c in '<>&"'):
                raise ValueError(f"{what} is named as LAS X names it, not {text!r}")
    def light(entries):
        return "".join(
            f"<LightSource>{beam_route(route)}<LineIndex>{int(index)}</LineIndex><WaveLength>{value(nm)}</WaveLength>"
            f"<Locked>0</Locked><Intensity>{value(percent / 100)}</Intensity></LightSource>"
            for route, index, nm, percent in entries
        )

    def detection(entries):
        return "".join(
            f"<Detector>{beam_route(route)}<Begin>{value(begin)}</Begin><End>{value(end)}</End><Dye>{dye or 'None'}</Dye>"
            f"<DetectorDyeRelation>{int(relation)}</DetectorDyeRelation><DyeWasAdded>0</DyeWasAdded><DyeExcitationMode>0</DyeExcitationMode>"
            f"<Locked>0</Locked><LutName>{colour}</LutName><IsLutUserDefined>{int(bool(locked))}</IsLutUserDefined></Detector>"
            for route, begin, end, dye, relation, colour, locked in entries
        )

    others = others or [{"lines": [], "detectors": []}]
    sequences = []
    for index, setting in enumerate(others if others else [None]):
        if index == place:
            body = light(lines) + detection([(route, begin, end, dye, 2, colour, False) for _name, route, begin, end, dye, colour in detectors])
        else:
            body = light([(l["route"], l["line_index"], l["wavelength"], l["percent"]) for l in setting["lines"]]) + detection(
                [(d["route"], d["begin"], d["end"], d["dye"], d["relation"] or 0, d["colour"], d["colour_locked"]) for d in setting["detectors"]])
        sequences.append(f'<Sequence Index="{index}">{body}</Sequence>')
    element = (
        '<Scanner IsAutoFocusDefinition="0" ActiveSettingIndex="-1" Command="SetConfocalDefinitionFromDyeAssistant">'
        "<Sequences><DoAdjustSequenceCount>1</DoAdjustSequenceCount><MessageSource>DyeDragNDrop</MessageSource>"
        f'<SequentialMode>{int(sequential_mode)}</SequentialMode>{"".join(sequences)}</Sequences></Scanner>'
    )
    wanted = {name: dye for name, _route, _b, _e, dye, _c in detectors}

    def fill(request: Request) -> None:
        request.elements = [element]

    def shows(found) -> bool:
        dyes = {d.get("name"): d.get("dye") for d in found or []}
        return all(dyes.get(name) == dye for name, dye in wanted.items())

    return Write(command="set_dyes", values={"laser_power": [percent for *_rest, percent in lines]}, request="SetConfocalDefinitionFromDyeAssistant",
                 fill=fill, description=f"Setting's dyes -> {', '.join(f'{n}: {d}' for n, d in wanted.items())}", ready=idle(),
                 confirm=Expect("detectors", shows))


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


def bleach_laser_power(place: int, line_index: int, wavelength_nm: float, percent: float) -> Write:
    """The power of the bleach point's line *line_index* of light source *place*, written into the state tree as the
    Bleach Point panel's Intensity does with no command (simulator, 2026-11-03); the power goes to the limits; read back."""
    if isinstance(percent, bool) or not isinstance(percent, (int, float)) or not 0 < percent <= 100:
        raise ValueError(f"the bleach point's laser power is above 0 and at most 100 percent, not {percent!r}")

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="BleachPointLaserPower" Value="{float(percent)!r}" At="{int(place)},{int(line_index)}"/>']

    def shows(bleach) -> bool:
        return any(abs(line["wavelength"] - wavelength_nm) < 0.5 and abs(line["percent"] - percent) <= LASER_TOLERANCE_PERCENT
                   for line in (bleach or {}).get("lines", []))

    return Write(
        command="set_bleach_laser_power", values={"laser_power": [percent]}, request="BleachPointLaserPower", fill=fill,
        description=f"Bleach point's {wavelength_nm:g} nm line -> {percent:g} %", ready=idle(),
        confirm=Expect("bleach_point", shows),
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


def start_live(project: int, *, fast: bool = False) -> Write:
    """Live, as the window's Live button starts it, into *project*: LAS X scans on until it is stopped.

    With *fast*, Fast Live (``Mode="6"``), with its own Scan Settings. Read
    back as the scanner no longer idle. Never sent again, like a capture.
    """
    parent = checked_project(project)
    mode = 6 if fast else 0

    def fill(request: Request) -> None:
        request.elements = [f'<AcquireControl Mode="{mode}" Command="StartLiveAcquiring" DCParentElementID="{parent}"/>']

    return Write(
        command="start_live",
        values={"project": project},
        request="StartLiveAcquiring",
        fill=fill,
        description=f"{'Fast Live' if fast else 'Live'} -> on, into project {project}",
        ready=idle(),
        confirm=Expect("scan_status", lambda status: isinstance(status, str) and not is_idle(status) and status != "Unknown"),
        never_resend=True,
    )


def fluorifier_change(command: str, elements: list[str], description: str, shows) -> Write:
    """A change of the Fluorifier disc written into LCS's state tree, as its window does; read back."""

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(command=command, values={"value": description}, request=elements[0].split('"')[1], fill=fill,
                 description=description, ready=idle(), confirm=Expect("fluorifier", lambda found: bool(found) and shows(found)))


def mfp_auto_select(on: bool) -> Write:
    """The multi-function port (MFP) following the setting (Autoselect) on or off, as the beam path's box; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="IsMultiFunctionPortAutoSelectionEnabled" Value="{int(bool(on))}"/>']

    return Write(command="set_mfp_auto_select", values={"value": bool(on)}, request="IsMultiFunctionPortAutoSelectionEnabled", fill=fill,
                 description=f"MFP Autoselect -> {'on' if on else 'off'}", ready=idle(),
                 confirm=Expect("mfp_auto_select", lambda shown: shown is bool(on)))


def notch_auto_select(on: bool) -> Write:
    """The notch filters chosen with the laser lines (Auto Select) on or off, as the Fluorifier Disc Settings' box."""
    return fluorifier_change("set_notch_auto_select", [f'<StateTree Command="IsNotchFilterAutoSelectionEnabled" Value="{int(bool(on))}"/>'],
                             f"Notch Auto Select -> {'on' if on else 'off'}", lambda found: found["auto_select"] is bool(on))


def polarizer(angle_deg) -> Write:
    """The Fluorifier disc's left wheel turned as a polarizer to *angle_deg*, or, with None, back to holding a notch filter."""
    if angle_deg is None:
        return fluorifier_change("set_polarizer", ['<StateTree Command="IsSpectrumTurnMode" Value="0"/>'], "Polarizer -> off (notch filters)",
                                 lambda found: found["polarizer_deg"] is None)
    if isinstance(angle_deg, bool) or not isinstance(angle_deg, (int, float)):
        raise ValueError(f"the polarizer takes an angle in degrees, or None, not {angle_deg!r}")
    angle = float(angle_deg)
    return fluorifier_change(
        "set_polarizer", ['<StateTree Command="IsSpectrumTurnMode" Value="1"/>', f'<StateTree Command="FilterSpectrumPos" Value="{angle!r}"/>'],
        f"Polarizer -> {angle:g} deg", lambda found: found["polarizer_deg"] is not None and abs(found["polarizer_deg"] - angle) <= 0.1,
    )


def coverslip_offset(um: float) -> Write:
    """The Reflection autofocus's coverslip offset, in micrometres, written in metres as the window does; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="ReflectionOffset" Value="{um / 1e6!r}"/>']

    return Write(command="set_coverslip_offset", values={"value": float(um)}, request="ReflectionOffset", fill=fill,
                 description=f"Coverslip offset -> {um:g} um", ready=idle(),
                 confirm=Expect("coverslip_offset", lambda shown: isinstance(shown, float) and abs(shown - um) <= 0.01))


def find_coverslip(project: int, drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """The Reflection autofocus's "Find Coverslip Reflection", as the window sends it, into *project*: a search on *drive*
    over the autofocus's capture range, both ends to the limits, as an autofocus's. Never sent again."""
    parent = checked_project(project)
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"

    def fill(request: Request) -> None:
        request.elements = [f'<AcquireControl Command="StartFindCoverSlipReflection" DCParentElementID="{parent}"/>']

    return Write(
        command="find_coverslip", values={"project": project, "drive": drive, "axis": axis, "begin_um": begin_um, "end_um": end_um, **(start or {})},
        request="StartFindCoverSlipReflection", fill=fill, description=f"Find coverslip reflection over {begin_um:g} to {end_um:g} um ({drive})",
        ready=idle(without_limit=True), never_resend=True,
    )


def excitation_control(source_place: int, name: str, mode: str) -> Write:
    """The light source *name*'s Excitation Control ("maximum power" or "constant power"), written into LCS's state tree as the
    Laser Overview does; read back."""
    code = {b: a for a, b in direct_reader.EXCITATION_CONTROL.items()}[mode]

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="CurrentExcitationControlMode" Value="{code}" At="{int(source_place)}"/>']

    return Write(command="set_excitation_control", values={"value": mode}, request="CurrentExcitationControlMode", fill=fill,
                 description=f"{name} Excitation Control -> {mode}", ready=idle(),
                 confirm=Expect("excitation_control", lambda found: (found or {}).get(name) == mode))


def laser_output_power(setting_place: int, source_place: int, name: str, percent: float) -> Write:
    """The light source *name*'s own output power in percent, written into the setting at *setting_place*, as the Laser
    Overview's slider does; the power goes to the limits; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="LaserTargetOutputPower" Value="{float(percent)!r}" At="{int(setting_place)},{int(source_place)}"/>']

    return Write(command="set_laser_output_power", values={"value": float(percent)}, request="LaserTargetOutputPower", fill=fill,
                 description=f"{name} output power -> {percent:g} %", ready=idle(),
                 confirm=Expect("laser_output_power", lambda found: abs((found or {}).get(name, -1) - percent) <= 0.05))


def excitation_scan(name: str, value) -> Write:
    """One value of the Λ (excitation) scan, with the Λ panel's own command (Lambda After Stage written into the state
    tree, as the panel does); read back."""
    if name == "after_stage":
        element = f'<StateTree Command="IsExcitationLambdaAfterStageDim" Value="{int(bool(value))}"/>'
    elif name == "max_gap_nm":
        return max_gap(value)
    else:
        command, attribute, text = {
            "begin_nm": ("SetLambdaExcitationBegin", "Begin", lambda v: str(int(v))),
            "end_nm": ("SetLambdaExcitationEnd", "End", lambda v: str(int(v))),
            "step_nm": ("SetLambdaExcitationStepsize", "Stepsize", lambda v: str(int(v))),
            "automatic_sp": ("SetLambdaAutomaticSPUnitMovement", "Movement", lambda v: str(int(bool(v)))),
            "method": ("SetLambdaDetectionBandwidthMode", "Mode", lambda v: str({b: a for a, b in direct_reader.EXCITATION_METHODS.items()}[v])),
            "min_gap_nm": ("SetLambdaMinGap", "MinGap", lambda v: str(int(v))),
            "geometry": ("SetLambdaGeometry", "Geometry", lambda v: str({b: a for a, b in direct_reader.LAMBDA_GEOMETRIES.items()}[v])),
        }[name]
        element = f'<Scanner Command="{command}" {attribute}="{text(value)}"/>'

    def fill(request: Request) -> None:
        request.elements = [element]

    def shows(found) -> bool:
        if not found:
            return False
        shown = found[name]
        return abs(shown - value) < 0.5 if isinstance(value, (int, float)) and not isinstance(value, bool) else shown == value

    return Write(command="set_excitation_scan", values={"value": str(value)}, request=element.split('"')[1], fill=fill,
                 description=f"Λ scan {name} -> {value}", ready=idle(), confirm=Expect("excitation_scan", shows))


def max_gap(value) -> Write:
    """The Λλ scan's maximal distance between excitation and detection: a number ticks its box and sets it, False unticks it."""
    elements = ['<Scanner Command="SetLambdaUseMaxGap" UseMaxGap="0"/>'] if value is False else [
        '<Scanner Command="SetLambdaUseMaxGap" UseMaxGap="1"/>', f'<Scanner Command="SetLambdaMaxGap" MaxGap="{int(value)}"/>']

    def fill(request: Request) -> None:
        request.elements = list(elements)

    expected = None if value is False else float(value)
    return Write(command="set_excitation_scan", values={"value": str(value)}, request=elements[-1].split('"')[1], fill=fill,
                 description=f"Λλ maximal distance -> {value}", ready=idle(),
                 confirm=Expect("excitation_scan", lambda found: bool(found) and found["max_gap_nm"] == expected))


#: The λ scan's detection: the driver's name, LAS X's command and its attribute, and the reading's key.
LAMBDA_SCAN = {
    "begin_nm": ("SetLambdaDetectionBegin", "Begin"),
    "end_nm": ("SetLambdaDetectionEnd", "End"),
    "step_nm": ("SetLambdaDetectionStepsize", "Stepsize"),
    "bandwidth_nm": ("SetLambdaDetectionBandwidth", "Bandwidth"),
}
#: The λ panel's range for the detection's begin and end, in nanometres (simulator, 2026-11-02).
LAMBDA_RANGE_NM = (380, 800)


def lambda_scan(name: str, nm: int) -> Write:
    """One of the λ scan's detection values, in whole nanometres, with the λ panel's own command; read back."""
    command, attribute = LAMBDA_SCAN[name]

    def fill(request: Request) -> None:
        request.elements = [f'<Scanner Command="{command}" {attribute}="{int(nm)}"/>']

    return Write(command="set_lambda_scan", values={"value": int(nm)}, request=command, fill=fill,
                 description=f"λ scan {name[:-3]} -> {nm} nm", ready=idle(),
                 confirm=Expect("lambda_scan", lambda found: bool(found) and abs(found[name] - nm) < 0.5))


def auto_sync(on: bool) -> Write:
    """Auto Sync on, as the Best Focus Setting's X sends it (``ResetAutoFocusToAutoSync``; Series Scan Autofocus goes
    off with it), or off, as the Best Focus Scan Options' Auto Sync box writes it (LCS then copies the current
    setting's light and detectors into the Best Focus Setting). Read back."""
    element = '<Scanner Command="ResetAutoFocusToAutoSync"/>' if on else '<StateTree Command="IsAutoSyncActive" Value="0"/>'

    def fill(request: Request) -> None:
        request.elements = [element]

    return Write(command="set_auto_sync", values={"value": bool(on)}, request=element.split('"')[1], fill=fill,
                 description=f"Auto Sync -> {'on' if on else 'off'}", ready=idle(),
                 confirm=Expect("auto_sync", lambda found: bool(found) and found["on"] is bool(on)))


#: Auto Sync's options by the driver's name: the state-tree name, its own on/off (None: always on), the kind.
AUTO_SYNC_OPTIONS = {
    "format": ("Format", "ChangeFormat", int),
    "scan_frequency": ("Speed", "ChangeSpeed", int),
    "overview_zoom": ("Zoom", "ChangeZoom", float),
    "bidirectional": ("Bidirectional", None, bool),
    "no_average": ("NoAverage", None, bool),
}


def auto_sync_option(scanner: str, option: str, value) -> list[Write]:
    """One of Auto Sync's scan options for *scanner* (``"fov"``, ``"resonant"``), as the Best Focus Scan Options window
    writes it: a number ticks its box and sets it, False unticks it; bidirectional and no_average take True or False."""
    if scanner not in ("fov", "resonant"):
        raise ValueError(f"Auto Sync has the scanners 'fov' and 'resonant', not {scanner!r}")
    if option not in AUTO_SYNC_OPTIONS:
        raise ValueError(f"Auto Sync's options are {sorted(AUTO_SYNC_OPTIONS)}, not {option!r}")
    name, box, kind = AUTO_SYNC_OPTIONS[option]
    suffix = "Resonant" if scanner == "resonant" else ""

    def write(node, text, expected, shown):
        def fill(request: Request) -> None:
            request.elements = [f'<StateTree Command="AutoSync{node}{suffix}" Value="{text}"/>']

        return Write(command="set_auto_sync_options", values={"value": shown}, request=f"AutoSync{node}{suffix}", fill=fill,
                     description=f"Auto Sync ({scanner}) {option.replace('_', ' ')} -> {shown}", ready=idle(),
                     confirm=Expect("auto_sync", lambda found: (found or {}).get(scanner, {}).get(option) == expected))

    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{option} takes True or False, not {value!r}")
        return [write(name, int(value), value, value)]
    if value is False:
        return [write(box, 0, None, "off")]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind is int and value != int(value)):
        raise ValueError(f"{option} takes {'a whole number' if kind is int else 'a number'} or False, not {value!r}")
    shown = kind(value)
    if option == "format" and shown % FAST_LIVE_FORMAT_STEP:
        raise ValueError(f"Auto Sync's Maximal Format goes in steps of {FAST_LIVE_FORMAT_STEP}, as its slider, not {shown}")
    return [replace(write(name, repr(shown), shown, shown), confirm=None), write(box, 1, shown, shown)]


def autofocus_in_time_series(when) -> Write:
    """When the autofocus runs in a time series, as its Timelapse tab: ``"first"``, ``"every"``, or n >= 2 (every n-th cycle).

    Written into LCS's state tree, as the tab does (simulator, 2026-11-02); read back.
    """
    if when in ("first", "every") and isinstance(when, str):
        elements = [f'<StateTree Command="WorkflowTimelapse" Value="{1 if when == "first" else 2}"/>']
    elif isinstance(when, int) and not isinstance(when, bool) and when >= 2:
        elements = ['<StateTree Command="WorkflowTimelapse" Value="3"/>', f'<StateTree Command="WorkflowTimelapseIterator" Value="{when}"/>']
    else:
        raise ValueError(f"the autofocus runs on the 'first' cycle, on 'every' cycle, or every n-th (n of 2 or more), not {when!r}")

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(command="set_autofocus_in_time_series", values={"value": str(when)}, request="WorkflowTimelapse", fill=fill,
                 description=f"Autofocus in a time series -> {when}", ready=idle(),
                 confirm=Expect("autofocus_in_time_series", lambda shown: shown == when))


def z_compensation_change(command: str, element: str, description: str, shows, values=None) -> Write:
    """One command of the Linear Z-Compensation window, as it sends it; read back from the Z-compensation."""

    def fill(request: Request) -> None:
        request.elements = [element]

    return Write(command=command, values=values or {"value": description}, request=element.split('"')[1], fill=fill,
                 description=description, ready=idle(), confirm=Expect("z_compensation", lambda z: bool(z) and shows(z)))


def z_compensation_mode(name: str, on: bool) -> Write:
    """A Z-compensation mode (``excitation``, ``detector``, ``motcorr``) on or off, as the window's checkbox."""
    bit = direct_reader.Z_COMPENSATION_MODES[name]
    return z_compensation_change(
        "set_z_compensation", f'<Scanner Command="ActivateZCompensationMode" Mode="{bit}" Activate="{int(bool(on))}"/>',
        f"Z-compensation {name} -> {'on' if on else 'off'}", lambda z: (name in z["modes"]) is bool(on),
    )


def z_compensation_reference(um: float) -> Write:
    """Z-compensation's reference position, written into LCS's state tree in metres, as the window does."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="ReferencePosition" Value="{um / 1e6!r}"/>']

    return Write(command="set_z_compensation", values={"value": um}, request="ReferencePosition", fill=fill,
                 description=f"Z-compensation reference -> {um:g} um", ready=idle(),
                 confirm=Expect("z_compensation", lambda z: bool(z) and abs(z["reference_um"] - um) <= 0.01))


def z_compensation_add(z_um: float) -> Write:
    """A support point where the focus is (*z_um*), holding the current powers and gains, as the window's Add.

    A point already at that height is not doubled (simulator, 2026-11-02);
    read back as a point at *z_um*.
    """
    return z_compensation_change("add_z_compensation_point", '<Scanner Command="AddIntensityCompensationInterpolationPoint"/>',
                                 f"Z-compensation point -> added at {z_um:g} um", lambda z: any(abs(p["z_um"] - z_um) <= 0.05 for p in z["points"]))


def z_compensation_remove(offset_m, count: int) -> Write:
    """The support point at *offset_m* from the reference removed (Delete), or all with None (Remove All)."""
    if offset_m is None:
        return z_compensation_change("remove_z_compensation_point", '<Scanner Command="RemoveAllIntensityCompensationInterpolationPoints"/>',
                                     "Z-compensation points -> all removed", lambda z: z["points"] == [])
    return z_compensation_change(
        "remove_z_compensation_point", f'<Scanner Command="RemoveIntensityCompensationInterpolationPointAt" ZPosition="{offset_m!r}"/>',
        "Z-compensation point -> removed", lambda z: len(z["points"]) == count - 1,
    )


def z_compensation_move_to(drive: str, offset_m: float, z_um: float, *, start: dict | None = None) -> Write:
    """The focus to a support point, as the window's Move To (the powers and gains left as they are); a z move for the limits."""
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    element = f'<Scanner Command="ActivateIntensityCompensationPositions" ZPosition="{offset_m!r}" WithAotfAndPMTSetting="0"/>'

    def fill(request: Request) -> None:
        request.elements = [element]

    return Write(command="move_to_z_compensation_point", values={axis: z_um, **(start or {})}, request="ActivateIntensityCompensationPositions",
                 fill=fill, description=f"Z -> {z_um:g} um, a Z-compensation point ({drive})", ready=idle(),
                 confirm=Expect("z", z_within(z_um, Z_TOLERANCE_UM), about=drive))


def roi_change(command: str, node: str, written, description: str, shows) -> Write:
    """One value of the ROI scan or bleach mode written into LCS's state tree, as the window's buttons do; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{written}"/>']

    return Write(command=command, values={"value": description}, request=node, fill=fill, description=description,
                 ready=idle(), confirm=Expect("roi_scan", lambda r: bool(r) and shows(r)))


def roi_scan(on: bool) -> Write:
    """ROI scan on or off, as the Acquisition Mode panel's ROI button."""
    return roi_change("set_roi_scan", "EnableRoiScan", int(bool(on)), f"ROI scan -> {'on' if on else 'off'}", lambda r: r["on"] is bool(on))


def roi_sections(first: int, last: int) -> list[Write]:
    """The ROI scan limited to sections *first* to *last* of the stack (ROI 3D), or to all with None."""
    if first is None:
        return [roi_change("set_roi_scan", "Is3DLimitedRoiScanEnabled", 0, "ROI scan -> every section", lambda r: r["sections"] is None)]
    return [
        roi_change("set_roi_scan", "Is3DLimitedRoiScanEnabled", 1, "ROI scan -> some sections", lambda r: r["sections"] is not None),
        roi_change("set_roi_scan", "First3DRoiSection", int(first), f"ROI scan first section -> {first}", lambda r: r["sections"][0] == first),
        roi_change("set_roi_scan", "Last3DRoiSection", int(last), f"ROI scan last section -> {last}", lambda r: r["sections"] == (first, last)),
    ]


def bleach_mode(on: bool) -> Write:
    """The bleach mode on or off, as the Acquisition Mode panel's bleach button."""
    return roi_change("set_bleach_mode", "EnableBleachScan", int(bool(on)), f"Bleach mode -> {'on' if on else 'off'}",
                      lambda r: r["bleach_mode"] is bool(on))


def line_scan(name: str, value) -> Write:
    """One value of the xt line scan written into LCS's state tree, as the t panel does in xt; read back."""
    node, key, text = {
        "lines": ("Lines", "lines", lambda v: str(int(v))),
        "lines_per_page": ("LinesPerPage", "lines_per_page", lambda v: str(int(v))),
        "max_lines_per_page": ("IsMaxLinesActive", "max_lines_per_page", lambda v: str(int(bool(v)))),
        "page_interval_s": ("DelayTime", "page_interval_s", lambda v: repr(float(v))),
    }[name]

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{text(value)}"/>']

    def shows(found) -> bool:
        if not found:
            return False
        return abs(found[key] - value) <= 0.001 if name == "page_interval_s" else found[key] == value

    return Write(command="set_line_scan", values={"value": str(value)}, request=node, fill=fill,
                 description=f"Line scan {name} -> {value}", ready=idle(), confirm=Expect("line_scan", shows))


def time_series_change(name: str, node: str, written, description: str, shows) -> Write:
    """One value of the time series written into LCS's state tree, as the t panel does; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="{node}" Value="{written}"/>']

    return Write(command="set_time_series", values={"value": description}, request=node, fill=fill,
                 description=f"Time series {name} -> {description}", ready=idle(), confirm=Expect("time_series", lambda t: bool(t) and shows(t)))


def time_interval(seconds: float) -> Write:
    """The time series' interval in seconds (the t panel's Time Interval); read back within a millisecond."""
    return time_series_change("interval", "DelayTime", repr(float(seconds)), f"{seconds:g} s",
                              lambda t: abs(t["interval_s"] - seconds) <= 0.001)


def time_frames(frames: int, interval_s: float) -> Write:
    """The time series' number of frames, written as the duration that gives it over *interval_s*: LCS works the
    frames out from the duration, and ignores the frames written themselves (simulator, 2026-11-02)."""
    return time_series_change("frames", "Duration", repr((frames - 1) * float(interval_s)), f"{frames} frames",
                              lambda t: t["frames"] == frames)


def time_minimize(on: bool) -> Write:
    """The t panel's Minimize on or off: on, LCS sets the shortest interval, one frame (simulator, 2026-11-02)."""
    return time_series_change("minimize", "IsMinimized", int(bool(on)), "on" if on else "off",
                              lambda t: t["minimized"] is bool(on))


def time_until_stopped(on: bool) -> Write:
    """The t panel's "Acquire Until Stopped" on or off (off is its Frames); LCS sets the mode itself."""
    return time_series_change("until stopped", "IsEnableAutomaticTSectionsCalculation", int(bool(on)), "on" if on else "off",
                              lambda t: t["until_stopped"] is bool(on))


#: Fast Live's and Auto Sync's Maximal Format: the windows' sliders and arrows step by 8 (simulator, 2026-11-03), within LCS's own range.
FAST_LIVE_FORMAT_STEP = 8
#: Fast Live's Scan Settings by the driver's name: the end of the state-tree name, and its kind.
FAST_LIVE_OPTIONS = {
    "format": ("Format", int),
    "scan_frequency": ("Frequency", int),
    "bidirectional": ("Bidirectional", bool),
    "no_average": ("NoAverage", bool),
    "no_dynamic_signal_enhancement": ("NoDynamicSignalEnhancement", bool),
    "selected_setting_only": ("SelectedSettingOnly", bool),
    "no_tau_sted_xtend": ("NoTauStedXtend", bool),
}


def fast_live_setting(scanner: str, option: str, value) -> list[Write]:
    """One of Fast Live's Scan Settings for *scanner* (``"fov"`` or ``"resonant"``), as its window writes it; read back.

    ``overview_zoom`` takes a zoom (the box ticked) or False (unticked). A
    value of the wrong kind or a format the slider does not offer raises
    ``ValueError``; the range beside each value is checked as it is written.
    """
    if scanner not in ("fov", "resonant"):
        raise ValueError(f"Fast Live has the scanners 'fov' and 'resonant', not {scanner!r}")
    suffix = "Resonant" if scanner == "resonant" else ""

    def write(name, text, key, expected, shown):
        def fill(request: Request) -> None:
            request.elements = [f'<StateTree Command="FastLive{name}{suffix}" Value="{text}"/>']

        return Write(command="set_fast_live", values={"value": shown}, request=f"FastLive{name}{suffix}", fill=fill,
                     description=f"Fast Live ({scanner}) {option.replace('_', ' ')} -> {shown}", ready=idle(),
                     confirm=Expect("fast_live", lambda found: (found or {}).get(scanner, {}).get(key) == expected))

    if option == "overview_zoom":
        if value is False:
            return [write("OverviewZoom", 0, "overview_zoom", None, "off")]
        zoom = float(number(option, value))
        return [
            replace(write("Zoom", repr(zoom), "overview_zoom", zoom, zoom), confirm=None),
            write("OverviewZoom", 1, "overview_zoom", zoom, f"on, {zoom:g}"),
        ]
    if option not in FAST_LIVE_OPTIONS:
        raise ValueError(f"Fast Live's settings are {sorted([*FAST_LIVE_OPTIONS, 'overview_zoom'])}, not {option!r}")
    name, kind = FAST_LIVE_OPTIONS[option]
    if kind is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{option} takes True or False, not {value!r}")
        return [write(name, int(value), option, value, value)]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{option} takes a whole number, not {value!r}")
    if option == "format" and value % FAST_LIVE_FORMAT_STEP:
        raise ValueError(f"Fast Live's Maximal Format goes in steps of {FAST_LIVE_FORMAT_STEP}, as its slider, not {value}")
    return [write(name, value, option, value, value)]


def stop_live() -> Write:
    """Stop the scan, as the window's Live button stops it; read back as the scanner idle."""

    def fill(request: Request) -> None:
        request.elements = ['<AcquireControl Command="StopAcquiring" DCParentElementID="0"/>']

    return Write(
        command="stop_live",
        values={},
        request="StopAcquiring",
        fill=fill,
        description="Live -> off",
        confirm=idle(),
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


def multi_function_port(slot: int, filter_name: str) -> Write:
    """The multi-function port (MFP) at *slot*, which holds *filter_name*, written into LCS's state tree as the window's MFP list
    does (simulator, 2026-11-03); read back as that position."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="CurrentMultiFunctionPortPos" Value="{int(slot)}"/>']

    return Write(command="set_filter_wheel", values={"value": filter_name}, request="CurrentMultiFunctionPortPos", fill=fill,
                 description=f"MFP -> {filter_name}", ready=idle(),
                 confirm=Expect("filter_wheels", lambda wheels: any(w.get("type") == 180 and w.get("filterIndex") == slot for w in wheels or [])))


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


def pinhole_um(value, *, all_settings: bool = False) -> Write:
    """The pinhole's size in micrometres, with the window's own ``SetPinhole`` (in metres); read back.

    Into the current setting only (``SetMode="0"``), as the window sends it; with
    *all_settings*, into every setting (``SetMode="1"``), as its "apply to all
    settings" box does (simulator, 2026-11-02).

    LCS does not keep this command to its range (simulator, 2026-11-02: 2000
    micrometres, its maximum 1000, raised the motor's error and set 2000), so
    the transfer checks LCS's own range before it sends it
    (``transfer.CHECKED_COMMANDS``).
    """
    target = number("pinhole_um", value)

    def fill(request: Request) -> None:
        request.elements = [
            f'<Scanner Command="SetPinhole" PinholeDiameter="{target / 1_000_000!r}" SetMode="{int(all_settings)}" SingleSettingIndex="{request.setting}"/>'
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


def y_line(y_um: float) -> Write:
    """In an xz scan mode, the y-stack's scan line to *y_um*, as the Y-Position box and the Move to buttons write it into
    the state tree (simulator, 2026-11-03); within LCS's range; read back."""

    def fill(request: Request) -> None:
        request.elements = [f'<StateTree Command="ZPosition" Value="{metres(y_um)}"/>']

    return Write(command="move_y_line", values={"value": y_um}, request="ZPosition", fill=fill,
                 description=f"Scan line -> y {y_um:g} um", ready=idle(),
                 confirm=Expect("y_line", lambda found: bool(found) and abs(found["y_um"] - y_um) <= STACK_TOLERANCE_UM))


def z_stack_change(command: str, drive: str, elements: list[str], begin_um: float, end_um: float,
                   description: str, start: dict | None = None) -> Write:
    """A change of the z-stack that leaves it from *begin_um* to *end_um* on *drive*; both ends go to the limits; read back.

    The ends are read back within one step, as LCS stretches them to whole steps.
    """
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"

    def fill(request: Request) -> None:
        request.elements = list(elements)

    def shows(reading) -> bool:
        stack = unwrapped(reading)
        try:
            room = stack["stepSize"] + STACK_TOLERANCE_UM
            return abs(stack["begin"] - begin_um) <= room and abs(stack["end"] - end_um) <= room
        except (KeyError, TypeError):
            return False

    return Write(
        command=command, values={"drive": drive, "axis": axis, "begin_um": begin_um, "end_um": end_um, **(start or {})},
        request=command, fill=fill, description=description, ready=idle(), confirm=Expect("z_stack", shows),
    )


def z_stack_size(drive: str, size_um: float, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """The z-stack's size, as the window's Z Size: LCS grows or shrinks it around its middle (simulator, 2026-11-02).

    *begin_um* and *end_um* are where its ends will be, for the limits and the read-back.
    """
    if isinstance(size_um, bool) or not isinstance(size_um, (int, float)) or size_um <= 0:
        raise ValueError(f"a z-stack's size is a positive number of micrometres, not {size_um!r}")
    return z_stack_change("set_z_stack_size", drive, [f'<Scanner Command="SetZStackSize" ZStackSize="{metres(size_um)}" {CURRENT}/>'],
                          begin_um, end_um, f"Z-stack size -> {size_um:g} um", start)


def z_stack_here(which: str, drive: str, z_um: float, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """The z-stack's begin or end where its drive stands, as the window's Begin and End buttons send it."""
    if which not in ("begin", "end"):
        raise ValueError(f"a z-stack has a begin and an end, not {which!r}")
    is_begin = which == "begin"
    elements = [
        f'<Scanner Command="BeforeBeginOrEndOfStackIsSet" IsBegin="{int(is_begin)}"/>',
        (f'<Scanner Command="SetZStackDefinition" SetBegin="{1 if is_begin else 2}" SetEnd="{2 if is_begin else 1}" '
         f'BeginValue="{metres(z_um) if is_begin else 0}" EndValue="{0 if is_begin else metres(z_um)}" {CURRENT}/>'),
    ]
    return z_stack_change("set_z_stack_here", drive, elements, begin_um, end_um, f"Z-stack {which} -> here, {z_um:g} um", start)


def z_stack_optical_section_step(drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """The z-stack's step from the formula of the optical section, as its Apply to Step Size sends it (``SamplingFactor``
    3, simulator 2026-11-03); LCS fits the end to whole steps, as for any stack, so the ends go to the limits as they are."""
    return z_stack_change("apply_optical_section_step", drive, ['<Scanner Command="SetOptimalStackStepSize" SamplingFactor="3"/>'],
                          begin_um, end_um, "Z-stack step -> the optical section's", start)


def z_stack_recenter(drive: str, begin_um: float, end_um: float, *, start: dict | None = None) -> Write:
    """The z-stack moved, its size kept, around where its drive stands, as the window's Re-Center."""
    return z_stack_change("recenter_z_stack", drive, ['<Scanner Command="ReCenterStackAroundCurrentZ"/>'],
                          begin_um, end_um, "Z-stack -> re-centred here", start)


def z_stack_clear(drive: str, z_um: float, *, start: dict | None = None) -> Write:
    """No z-stack, as the window's bin leaves it: its end and then its begin set where its drive stands.

    The bin sends no command LCS logs (simulator, 2026-11-02); it left the begin
    and the end at the focus, one section. The End and Begin buttons' commands
    do the same. Read back as no stack.
    """
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    elements = [
        '<Scanner Command="BeforeBeginOrEndOfStackIsSet" IsBegin="0"/>',
        f'<Scanner Command="SetZStackDefinition" SetBegin="2" SetEnd="1" BeginValue="0" EndValue="{metres(z_um)}" {CURRENT}/>',
        '<Scanner Command="BeforeBeginOrEndOfStackIsSet" IsBegin="1"/>',
        f'<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="2" BeginValue="{metres(z_um)}" EndValue="0" {CURRENT}/>',
    ]

    def fill(request: Request) -> None:
        request.elements = list(elements)

    return Write(
        command="clear_z_stack", values={"drive": drive, "axis": axis, "begin_um": z_um, "end_um": z_um, **(start or {})},
        request="clear_z_stack", fill=fill, description=f"Z-stack -> cleared, at {z_um:g} um", ready=idle(),
        confirm=Expect("z_stack", lambda reading: unwrapped(reading) is None),
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
