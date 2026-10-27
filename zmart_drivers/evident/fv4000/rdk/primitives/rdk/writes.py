"""The changes FLUOVIEW can be asked for, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits, the RDK
call that carries it and how that call's payload is filled in, the label in
the log, what to wait for before it is sent (:class:`Expect`: FLUOVIEW idle),
which reading shows that it took effect and how close counts, and whether it
may never be sent again. The dispatcher's ``run()`` does the rest. Nothing
here calls FLUOVIEW or the dispatcher.

The builders: :func:`move_xy`, :func:`move_z`, :func:`move_piezo`,
:func:`set_objective`, :func:`acquire`, :func:`stop`, :func:`set_remote_mode`,
:func:`load_parameter`, :func:`repeat_scan`, and :func:`change` for the
settings, which are one table (``SETTINGS``):

- ``name``: what a person calls it (``"laser_intensity"``); the limits gate
  knows it as ``set_<name>``;
- ``takes``: its arguments, in order, by name;
- ``setting_id``: the RDK's ``settingId`` that carries it, and ``payload``:
  how the arguments become the call's payload;
- ``value``: the argument the limits gate is shown (None: the gate is shown
  nothing of it, it is only allowed or not);
- ``read``: the reading that shows the new value, ``about``: which argument
  names the item that reading is about, and ``accept``: how close the
  read-back must come to the argument; a setting FLUOVIEW cannot read back
  has no ``read`` and is confirmed by nothing;
- ``describe``: the label in the log.

The checks that say how close counts (:func:`within_xy`, :func:`near`,
:func:`exactly`) each take one reading's value and answer True or False, so
each can be tested alone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .parsing import um_to_nm
from .reads import MANUAL_MAIN

# =============================================================================
# A write, and what it waits for
# =============================================================================


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str  # a name in reads.READS
    accept: Callable[[object], bool]  # how close counts
    about: str | None = (
        None  # the channel, laser or group, for a reading about one item
    )
    mode: str | None = None  # the class asked first; None = the profile's
    fallback: str | None = None
    without_limit: bool = False  # waited for as long as it takes, not the windows


@dataclass(frozen=True)
class Write:
    """One change: its call, its limits, and what belongs to it alone."""

    command: str  # its name in the limits (limit_rules.MUTATING_COMMANDS)
    values: dict  # what the limits gate is shown
    request: str  # the RDK call, e.g. "Parameter.setParameter"
    fill: Callable  # fill(payload): writes the values into the call's payload
    description: str
    ready: Expect | None = None  # waited for before every send (FLUOVIEW idle)
    confirm: Expect | None = None  # read back, one window at a time
    without_receipt: bool = False  # never for the RDK: every call answers at once
    never_resend: bool = False


# =============================================================================
# How close counts
# =============================================================================

#: How close a move must come, in micrometres, unless the caller says.
#: Not yet measured on an FV4000: the stage reports nanometres, so these are
#: generous, and a smaller tolerance can be set once the settling is known.
XY_TOLERANCE_UM = 1.0
Z_TOLERANCE_UM = 0.5
PIEZO_TOLERANCE_UM = 0.1


def is_idle(state) -> bool:
    """FLUOVIEW is doing nothing: no scan, no pause between time points."""
    return state == "IDLING"


def is_busy(state) -> bool:
    """FLUOVIEW is doing something: scanning, resting between time points, or paused."""
    return state in ("SCANNING", "RESTING", "WAITING")


def idle(*, without_limit: bool = False) -> Expect:
    """FLUOVIEW idle; ``without_limit`` waits for it as long as it takes instead of the four windows."""
    return Expect("scan_state", is_idle, without_limit=without_limit)


def within_xy(
    x_um: float, y_um: float, tolerance_um: float
) -> Callable[[object], bool]:
    """A stage position within *tolerance_um* of (x_um, y_um) on both axes."""

    def check(position) -> bool:
        try:
            return (
                abs(position["x_um"] - x_um) <= tolerance_um
                and abs(position["y_um"] - y_um) <= tolerance_um
            )
        except (KeyError, TypeError):
            return False

    return check


def near(target: float, tolerance: float) -> Callable[[object], bool]:
    """A number within *tolerance* of *target*."""

    def check(value) -> bool:
        try:
            return abs(float(value) - float(target)) <= tolerance
        except (TypeError, ValueError):
            return False

    return check


def exactly(target) -> Callable[[object], bool]:
    """A value equal to *target* (a text, a whole number or a yes/no)."""

    def check(value) -> bool:
        return value == target

    return check


def contains(member) -> Callable[[object], bool]:
    """A list that holds *member*."""

    def check(value) -> bool:
        return isinstance(value, list) and member in value

    return check


def slot_is(slot: int) -> Callable[[object], bool]:
    """An objective reading whose slot is *slot*."""

    def check(value) -> bool:
        return isinstance(value, dict) and value.get("slot") == slot

    return check


# =============================================================================
# The stage and the focus
# =============================================================================


def move_xy(
    x_um: float, y_um: float, *, escape: bool = False, tolerance_um: float | None = None
) -> Write:
    """Move the XY stage to an absolute position, in micrometres.

    *escape* lets the objective drop away while the stage moves (FLUOVIEW's
    ``escapeEnabled``), which is slower but keeps a tall sample clear of the
    lens. The read-back is the proof; an unconfirmed absolute move is sent
    again after waiting for idle, because it is the same move.
    """
    x_nm, y_nm = um_to_nm(x_um), um_to_nm(y_um)
    tolerance_um = XY_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(payload):
        payload.update(
            settingId="XY_STAGE_POSITION_SETTING",
            x=x_nm,
            y=y_nm,
            escapeEnabled=bool(escape),
        )

    return Write(
        command="move_xy",
        values={"x_um": x_um, "y_um": y_um},
        request="Parameter.setParameter",
        fill=fill,
        description=f"XY -> ({x_um:g}, {y_um:g}) um",
        ready=idle(),
        confirm=Expect("xy", within_xy(x_um, y_um, tolerance_um)),
    )


def move_z(z_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the Z stage (the focus motor) to an absolute position, in micrometres."""
    z_nm = um_to_nm(z_um)
    tolerance_um = Z_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(payload):
        payload.update(settingId="Z_STAGE_POSITION_SETTING", stagePosition=z_nm)

    return Write(
        command="move_z",
        values={"z_um": z_um},
        request="Parameter.setParameter",
        fill=fill,
        description=f"Z -> {z_um:g} um",
        ready=idle(),
        confirm=Expect("z", near(z_um, tolerance_um)),
    )


def move_piezo(z_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the piezo to *z_um* micrometres from FLUOVIEW's Z origin (the piezo counts from there)."""
    z_nm = um_to_nm(z_um)
    tolerance_um = PIEZO_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(payload):
        payload.update(settingId="LSM_Z_PIEZO_POSITION_SETTING", zPosition=z_nm)

    return Write(
        command="move_piezo",
        values={"z_piezo_um": z_um},
        request="Parameter.setParameter",
        fill=fill,
        description=f"piezo -> {z_um:g} um",
        ready=idle(),
        confirm=Expect("z_piezo", near(z_um, tolerance_um)),
    )


# =============================================================================
# The objective
# =============================================================================

#: How the nosepiece slot is sent. The manual's set calls it a string, while
#: its get answers an integer; it is sent as the manual says, a string such as
#: "2". To be checked on an FV4000: if FLUOVIEW refuses it with
#: INVALID_PARAMETERS, change this to ``int``.
REVOLVER_INDEX_AS = str


def set_objective(slot: int, name: str = "") -> Write:
    """Turn the nosepiece to *slot* (counted from 0). The stage is not moved here."""

    def fill(payload):
        payload.update(
            settingId="LSM_MICROSCOPE_OBJECTIVE_LENS_CURRENT_SETTING",
            revolverIndex=REVOLVER_INDEX_AS(slot),
        )

    return Write(
        command="set_objective",
        values={"objective_slot": slot},
        request="Parameter.setParameter",
        fill=fill,
        description=f"objective -> slot {slot}{f' ({name})' if name else ''}",
        ready=idle(),
        confirm=Expect("objective", slot_is(slot)),
    )


# =============================================================================
# Taking a picture, and stopping one
# =============================================================================


def acquire(execution_type: str = MANUAL_MAIN) -> Write:
    """Start the imaging FLUOVIEW has set up (``Protocol.startProtocol``): one picture, stack or series.

    Sent once and never again: a second send would take a second picture.
    It waits for idle as long as it takes, and is confirmed by the scan
    itself (``procedures/changes.py`` watches it), not here.
    """

    def fill(payload):
        payload.update(executionType=execution_type)

    return Write(
        command="acquire",
        values={},
        request="Protocol.startProtocol",
        fill=fill,
        description=f"acquire ({execution_type})",
        ready=idle(without_limit=True),
        confirm=None,
        never_resend=True,
    )


def stop(execution_type: str = MANUAL_MAIN) -> Write:
    """Stop the imaging that is running (``Protocol.stopProtocol``), and see FLUOVIEW go idle."""

    def fill(payload):
        payload.update(executionType=execution_type)

    return Write(
        command="stop",
        values={},
        request="Protocol.stopProtocol",
        fill=fill,
        description=f"stop ({execution_type})",
        confirm=idle(),
    )


def repeat_scan(start: bool) -> Write:
    """Start (``Repeat.start``) or stop (``Repeat.stop``) FLUOVIEW's live, repeating scan."""
    if start:

        def fill(payload):
            payload.update(commandType="", deviceType="")

        return Write(
            command="repeat_scan",
            values={"start": True},
            request="Repeat.start",
            fill=fill,
            description="live scan on",
            ready=idle(),
            # A live scan is not IDLING any more. Which state it shows is not certain:
            # the manual's table allows Repeat.stop only in RESTING or WAITING, "during
            # repeat scan only", which suggests a live scan reports RESTING. So any
            # state but IDLING counts as the live scan running.
            confirm=Expect("scan_state", is_busy),
        )
    return Write(
        command="repeat_scan",
        values={"start": False},
        request="Repeat.stop",
        fill=lambda payload: None,
        description="live scan off",
        confirm=idle(),
    )


# =============================================================================
# The software itself
# =============================================================================


def set_remote_mode(remote: bool) -> Write:
    """Put FLUOVIEW in REMOTE mode (no pop-up dialogs) or back in LOCAL mode (as at the screen)."""

    def fill(payload):
        payload.update(mode="REMOTE" if remote else "LOCAL")

    return Write(
        command="set_remote_mode",
        values={"remote": remote},
        request="Remote.setRemoteMode",
        fill=fill,
        description=f"remote mode {'on' if remote else 'off'}",
    )


def load_parameter(image_path: str) -> Write:
    """Load the imaging conditions saved in the picture *image_path* (``Parameter.loadParameter``).

    This is how a whole imaging setup is recalled on the FV4000: every setting
    the picture was taken with (objective, lasers, channels, z-stack, ...)
    becomes the current one. FLUOVIEW must be idle.
    """

    def fill(payload):
        payload.update(targetName=str(image_path))

    return Write(
        command="load_parameter",
        values={"image_path": str(image_path)},
        request="Parameter.loadParameter",
        fill=fill,
        description=f"settings from {image_path}",
        ready=idle(),
    )


# =============================================================================
# The settings: one table
# =============================================================================


def _nm(value_um):
    return um_to_nm(float(value_um))


def _interval(seconds: float) -> dict:
    """FLUOVIEW's ``interval`` and ``unitOfInterval`` for *seconds*: ``interval * 10**-unit`` seconds.

    The unit is the smallest power of ten that makes the interval a whole number
    (0.5 s is 5 with unit 1), down to milliseconds.
    """
    seconds = float(seconds)
    if seconds < 0:
        raise ValueError("a time-lapse interval cannot be negative")
    for unit in range(4):
        scaled = seconds * 10**unit
        if abs(scaled - round(scaled)) < 1e-9:
            return {"interval": round(scaled), "unitOfInterval": unit}
    return {"interval": round(seconds * 1000), "unitOfInterval": 3}


@dataclass(frozen=True)
class Setting:
    """One row of ``SETTINGS``; the fields are explained in the file's docstring."""

    name: str
    takes: tuple[str, ...]
    setting_id: str
    payload: Callable[[dict], dict]
    describe: Callable[[dict], str]
    value: str | None = None
    read: str | None = None
    about: str | None = None
    accept: Callable[[dict], Callable[[object], bool]] | None = None
    #: The limits policy this setting is checked under; its own name when None.
    policy_values: Callable[[dict], dict] | None = None


def _rows() -> list[Setting]:
    rows = []

    def add(name, takes, setting_id, payload, describe, **rest):
        rows.append(Setting(name, tuple(takes), setting_id, payload, describe, **rest))

    # --- the detector channels ---------------------------------------------------
    add(
        "channel_enable",
        ("channel_id", "enable"),
        "LSM_CHANNEL_ENABLE_SETTING",
        lambda a: {"channelId": a["channel_id"], "enable": bool(a["enable"])},
        lambda a: f"channel {a['channel_id']} {'on' if a['enable'] else 'off'}",
        value="enable",
        read="channel_enabled",
        about="channel_id",
        accept=lambda a: exactly(bool(a["enable"])),
    )
    add(
        "channel_hv",
        ("channel_id", "value"),
        "LSM_CHANNEL_HV_SETTING",
        lambda a: {"channelId": a["channel_id"], "hv": int(a["value"])},
        lambda a: f"channel {a['channel_id']} HV -> {a['value']}",
        value="value",
        read="channel_hv",
        about="channel_id",
        accept=lambda a: exactly(int(a["value"])),
    )
    add(
        "channel_gain",
        ("channel_id", "value"),
        "LSM_CHANNEL_GAIN_SETTING",
        lambda a: {"channelId": a["channel_id"], "gain": float(a["value"])},
        lambda a: f"channel {a['channel_id']} gain -> {a['value']}",
        value="value",
        read="channel_gain",
        about="channel_id",
        accept=lambda a: near(float(a["value"]), 0.01),
    )
    add(
        "channel_offset",
        ("channel_id", "value"),
        "LSM_CHANNEL_OFFSET_SETTING",
        lambda a: {"channelId": a["channel_id"], "offset": int(a["value"])},
        lambda a: f"channel {a['channel_id']} offset -> {a['value']}",
        value="value",
        read="channel_offset",
        about="channel_id",
        accept=lambda a: exactly(int(a["value"])),
    )
    add(
        "channel_digital_gain",
        ("channel_id", "value"),
        "LSM_CHANNEL_DIGITALGAIN_SETTING",
        lambda a: {"channelId": a["channel_id"], "gain": float(a["value"])},
        lambda a: f"channel {a['channel_id']} digital gain -> {a['value']}",
        value="value",
        read="channel_digital_gain",
        about="channel_id",
        accept=lambda a: near(float(a["value"]), 0.01),
    )
    add(
        "channel_laser",
        ("channel_id", "laser_id"),
        "LSM_CHANNEL_LASER_SETTING",
        lambda a: {
            "channelId": a["channel_id"],
            "laser": [
                {
                    "imagingDeviceMode": "IMAGING",
                    "laserScannerMode": "MAIN",
                    "laserId": a["laser_id"],
                }
            ],
        },
        lambda a: f"channel {a['channel_id']} laser -> {a['laser_id']}",
        value="laser_id",
        read="channel_laser",
        about="channel_id",
        accept=lambda a: contains(a["laser_id"]),
    )
    # --- the lasers --------------------------------------------------------------
    add(
        "laser_enable",
        ("laser_id", "enable"),
        "LSM_LASER_ENABLE_SETTING",
        lambda a: {"laserId": a["laser_id"], "enable": bool(a["enable"])},
        lambda a: f"laser {a['laser_id']} {'on' if a['enable'] else 'off'}",
        value="enable",
        read="laser_enabled",
        about="laser_id",
        accept=lambda a: exactly(bool(a["enable"])),
    )
    add(
        "laser_intensity",
        ("laser_id", "value"),
        "LSM_LASER_INTENSITY_SETTING",
        lambda a: {"laserId": a["laser_id"], "laserIntensity": float(a["value"])},
        lambda a: f"laser {a['laser_id']} -> {a['value']} %",
        value="value",
        read="laser_intensity",
        about="laser_id",
        # FLUOVIEW clips a power outside the laser's range to the nearest
        # limit (the manual's note): the read-back then differs, the change
        # is recorded as unconfirmed, and the operator sees what was done.
        accept=lambda a: near(float(a["value"]), 0.05),
    )
    # --- the z-stack --------------------------------------------------------------
    add(
        "z_stack_enable",
        ("enable",),
        "LSM_Z_COORDINATE_ENABLE_SETTING",
        lambda a: {"enable": bool(a["enable"])},
        lambda a: f"z-stack {'on' if a['enable'] else 'off'}",
        value="enable",
        read="z_stack_enabled",
        accept=lambda a: exactly(bool(a["enable"])),
    )
    add(
        "z_stack_slices",
        ("value",),
        "LSM_Z_COORDINATE_SLICE_NUM_SETTING",
        lambda a: {"sliceNum": int(a["value"])},
        lambda a: f"z-stack slices -> {a['value']}",
        value="value",
        read="z_stack_slices",
        accept=lambda a: exactly(int(a["value"])),
    )
    add(
        "z_stack_step",
        ("value_um",),
        "LSM_Z_COORDINATE_STEP_SIZE_SETTING",
        lambda a: {"stepSize": _nm(a["value_um"])},
        lambda a: f"z-stack step -> {a['value_um']} um",
        value="value_um",
        read="z_stack_step_um",
        accept=lambda a: near(float(a["value_um"]), 0.001),
    )
    add(
        "z_stack_start",
        ("value_um",),
        "LSM_Z_COORDINATE_START_SETTING",
        lambda a: {"startPosition": _nm(a["value_um"])},
        lambda a: f"z-stack start -> {a['value_um']} um",
        value="value_um",
        read="z_stack_start_um",
        accept=lambda a: near(float(a["value_um"]), 0.001),
    )
    add(
        "z_stack_end",
        ("value_um",),
        "LSM_Z_COORDINATE_END_SETTING",
        lambda a: {"endPosition": _nm(a["value_um"])},
        lambda a: f"z-stack end -> {a['value_um']} um",
        value="value_um",
        read="z_stack_end_um",
        accept=lambda a: near(float(a["value_um"]), 0.001),
    )
    add(
        "z_stack_range",
        ("value_um",),
        "LSM_Z_COORDINATE_RANGE_SETTING",
        lambda a: {"range": _nm(a["value_um"])},
        lambda a: f"z-stack range -> {a['value_um']} um",
        value="value_um",
        read="z_stack_range_um",
        accept=lambda a: near(float(a["value_um"]), 0.001),
    )
    add(
        "z_origin",
        ("value_um",),
        "LSM_Z_COORDINATE_BASE_SETTING",
        lambda a: {"originPosition": _nm(a["value_um"])},
        lambda a: f"FLUOVIEW z origin -> {a['value_um']} um",
        value="value_um",
        read="z_origin",
        accept=lambda a: near(float(a["value_um"]), 0.001),
    )
    # The manual lists two parameters both called zType (the drive, MOTOR or
    # PIEZO, and the method). A payload cannot carry the same key twice, so
    # the method alone is sent: START_END and RANGE use the motor, PIEZO the piezo.
    add(
        "z_type",
        ("method",),
        "LSM_Z_COORDINATE_TYPE_SETTING",
        lambda a: {"zType": str(a["method"])},
        lambda a: f"z-stack method -> {a['method']}",
        value="method",
        read="z_type",
        accept=lambda a: exactly(str(a["method"])),
    )
    # --- the time lapse -------------------------------------------------------------
    add(
        "time_lapse_enable",
        ("enable",),
        "LSM_TIME_LAPS_ENABLE_SETTING",
        lambda a: {"enable": bool(a["enable"])},
        lambda a: f"time lapse {'on' if a['enable'] else 'off'}",
        value="enable",
        read="time_lapse_enabled",
        accept=lambda a: exactly(bool(a["enable"])),
    )
    add(
        "time_lapse_repeat",
        ("value",),
        "LSM_TIME_LAPS_REPEAT_TIMES_SETTING",
        lambda a: {"repeatCount": int(a["value"])},
        lambda a: f"time points -> {a['value']}",
        value="value",
        read="time_lapse_repeat",
        accept=lambda a: exactly(int(a["value"])),
    )
    add(
        "time_lapse_interval",
        ("value_s",),
        "LSM_TIME_LAPS_INTERVAL_SETTING",
        lambda a: _interval(a["value_s"]),
        lambda a: f"interval -> {a['value_s']} s",
        value="value_s",
        read="time_lapse_interval_s",
        accept=lambda a: near(float(a["value_s"]), 0.0005),
    )
    # --- the scanner ------------------------------------------------------------------
    add(
        "scanner_type",
        ("value",),
        "LSM_SCANNER_TYPE_SETTING",
        lambda a: {"scannerType": str(a["value"])},
        lambda a: f"scanner -> {a['value']}",
        value="value",
        read="scanner_type",
        accept=lambda a: exactly(str(a["value"])),
    )
    add(
        "scanner_mode",
        ("value",),
        "LSM_SCANNER_MODE_SETTING",
        lambda a: {"scannerMode": str(a["value"])},
        lambda a: f"scan direction -> {a['value']}",
        value="value",
        read="scanner_mode",
        accept=lambda a: exactly(str(a["value"])),
    )
    # --- brightness adjustment (FLUOVIEW's per-depth correction) ---------------------
    add(
        "brightness_adjustment_create",
        (),
        "LSM_BRIGHTNESS_ADJUSTMENT_CREATE_OR_UPDATE_SETTING",
        lambda a: {},
        lambda a: "brightness adjustment point recorded",
    )
    add(
        "brightness_adjustment_enable",
        ("enable",),
        "LSM_BRIGHTNESS_ADJUSTMENT_ENABLE_SETTING",
        lambda a: {"enable": bool(a["enable"])},
        lambda a: f"brightness adjustment {'on' if a['enable'] else 'off'}",
        value="enable",
        read="brightness_adjustment_enabled",
        accept=lambda a: exactly(bool(a["enable"])),
    )
    add(
        "brightness_adjustment_delete_all",
        (),
        "LSM_BRIGHTNESS_ADJUSTMENT_DELETE_ALL_SETTING",
        lambda a: {},
        lambda a: "brightness adjustment points deleted",
    )
    # --- multi-area time lapse (MATL): the registered groups and their areas ---------
    add(
        "matl_create",
        ("columns", "rows"),
        "MATL_ROI_CREATE",
        lambda a: {
            "acquisitionType": "LSM_IMAGING",
            "roiType": "DEFINE_MATRIX",
            "column": int(a["columns"]),
            "row": int(a["rows"]),
        },
        lambda a: f"MATL matrix {a['columns']} x {a['rows']} registered",
    )
    add(
        "matl_well_duplicate",
        ("source_well", "target_well"),
        "MATL_ROI_WELL_DUPLICATE",
        lambda a: {
            "srcWellId": str(a["source_well"]),
            "dstWellId": str(a["target_well"]),
        },
        lambda a: f"MATL well {a['source_well']} copied to {a['target_well']}",
    )
    add(
        "matl_delete",
        ("group_id",),
        "MATL_ROI_DELETE",
        lambda a: {"groupId": str(a["group_id"])},
        lambda a: f"MATL group {a['group_id']} deleted",
    )
    add(
        "matl_enable",
        ("group_id", "enable"),
        "MATL_ROI_ENABLE_SETTING",
        lambda a: {"groupId": str(a["group_id"]), "enable": bool(a["enable"])},
        lambda a: f"MATL group {a['group_id']} {'on' if a['enable'] else 'off'}",
        value="enable",
        # FLUOVIEW cannot read this setting back (the manual's section 3.6 marks its
        # getParameter n/a), so the change is confirmed by nothing.
    )
    # A group's position is checked against the stage limits like a move,
    # although registering it moves nothing: the scan will go there.
    add(
        "matl_xy",
        ("group_id", "x_um", "y_um"),
        "MATL_ROI_XY_POSITION_SETTING",
        lambda a: {
            "groupId": str(a["group_id"]),
            "x": _nm(a["x_um"]),
            "y": _nm(a["y_um"]),
        },
        lambda a: f"MATL group {a['group_id']} -> ({a['x_um']:g}, {a['y_um']:g}) um",
        read="matl_xy",
        about="group_id",
        accept=lambda a: within_xy(float(a["x_um"]), float(a["y_um"]), 0.001),
        policy_values=lambda a: {"x_um": float(a["x_um"]), "y_um": float(a["y_um"])},
    )
    add(
        "matl_z",
        ("group_id", "z_um"),
        "MATL_ROI_Z_POSITION_SETTING",
        lambda a: {"groupId": str(a["group_id"]), "z": _nm(a["z_um"])},
        lambda a: f"MATL group {a['group_id']} z -> {a['z_um']:g} um",
        policy_values=lambda a: {"z_um": float(a["z_um"])},
    )
    add(
        "matl_z_origin",
        ("group_id", "z_um"),
        "MATL_ROI_Z_ORIGIN_SETTING",
        lambda a: {"groupId": str(a["group_id"]), "zOrigin": _nm(a["z_um"])},
        lambda a: f"MATL group {a['group_id']} z origin -> {a['z_um']:g} um",
        read="matl_z_origin_um",
        about="group_id",
        accept=lambda a: near(float(a["z_um"]), 0.001),
        policy_values=lambda a: {"z_um": float(a["z_um"])},
    )
    add(
        "matl_area_delete",
        ("group_id", "column", "row"),
        "MATL_ROI_AREA_DELETE_SETTING",
        lambda a: {
            "groupId": str(a["group_id"]),
            "column": int(a["column"]),
            "row": int(a["row"]),
        },
        lambda a: (
            f"MATL group {a['group_id']} area ({a['column']}, {a['row']}) deleted"
        ),
    )
    add(
        "matl_area_enable",
        ("group_id", "column", "row", "enable"),
        "MATL_ROI_AREA_ENABLE_SETTING",
        lambda a: {
            "groupId": str(a["group_id"]),
            "column": int(a["column"]),
            "row": int(a["row"]),
            "enable": bool(a["enable"]),
        },
        lambda a: (
            f"MATL group {a['group_id']} area ({a['column']}, {a['row']}) {'on' if a['enable'] else 'off'}"
        ),
        value="enable",
    )
    add(
        "matl_area_z_range",
        ("group_id", "column", "row"),
        "MATL_ROI_AREA_Z_RANGE_SETTING",
        lambda a: {
            "groupId": str(a["group_id"]),
            "column": int(a["column"]),
            "row": int(a["row"]),
        },
        lambda a: (
            f"MATL group {a['group_id']} area ({a['column']}, {a['row']}) takes the current z-stack"
        ),
    )
    return rows


#: The settings by name.
SETTINGS: dict[str, Setting] = {row.name: row for row in _rows()}

#: The settings a MATL position shares with a stage move: checked under ``set_xyz``.
STAGE_POLICY_SETTINGS = frozenset({"matl_xy", "matl_z", "matl_z_origin"})


def arguments_of(name: str, given: dict) -> dict:
    """The arguments of the setting *name*, checked: every one it takes, and nothing else.

    Raises ``ValueError`` for an unknown setting, a missing argument or one
    the setting does not take, so a wrong call is refused before anything is
    built.
    """
    setting = SETTINGS.get(name)
    if setting is None:
        raise ValueError(
            f"unknown setting {name!r}; the settings are {sorted(SETTINGS)}"
        )
    missing = [arg for arg in setting.takes if arg not in given]
    extra = sorted(set(given) - set(setting.takes))
    if missing or extra:
        raise ValueError(
            f"{name} takes {list(setting.takes)}"
            + (f"; missing {missing}" if missing else "")
            + (f"; it does not take {extra}" if extra else "")
        )
    return {arg: given[arg] for arg in setting.takes}


def change(name: str, **given) -> Write:
    """The write that changes the setting *name* with the arguments *given* (see ``SETTINGS``)."""
    setting = SETTINGS[name]
    arguments = arguments_of(name, given)
    body = setting.payload(arguments)

    def fill(payload):
        payload.update(settingId=setting.setting_id, **body)

    if setting.policy_values is not None:
        values = setting.policy_values(arguments)
    elif setting.value is not None:
        values = {"value": arguments[setting.value]}
    else:
        values = {}
    confirm = None
    if setting.read is not None:
        confirm = Expect(
            setting.read,
            setting.accept(arguments),
            about=str(arguments[setting.about]) if setting.about else None,
        )
    return Write(
        command=f"set_{name}",
        values=values,
        request="Parameter.setParameter",
        fill=fill,
        description=setting.describe(arguments),
        ready=idle(),
        confirm=confirm,
    )
