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

from collections.abc import Callable, Iterable
from dataclasses import dataclass

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
    ``per_setting`` adds ``SetMode="2" SingleSettingIndex="0"`` for a value
    that belongs to the job's first sequential setting. ``reading`` names the
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

#: Every setting the plain Navigator route can change, by its name.
SETTINGS = {
    s.name: s for s in (ZOOM, SCAN_SPEED, SCAN_FIELD_ROTATION, LINE_AVERAGE, PINHOLE_AIRY)
}


def setting_element(setting: Setting, number: float) -> str:
    """The element that writes *number* into the current setting."""
    one_setting = ' SetMode="2" SingleSettingIndex="0"' if setting.per_setting else ""
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
        request.elements = [setting_element(chosen, number)]

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
        f'<Scanner Command="SetZStackDefinition" SetBegin="1" SetEnd="1" '
        f'BeginValue="{metres(begin_um)}" EndValue="{metres(end_um)}" {CURRENT}/>'
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
