"""The table of hardware settings: one row per setting, with its reading and its change side by side.

Every hardware setting the driver can change is one row here: a shutter's
state, a lamp's voltage, a filter wheel's position, the light path, Perfect
Focus, a laser line's power, the incubator's target gas. Each row names the
NIS function that reads the setting and the one that changes it, so that the
two mirror each other: what the change sets is what the reading reads back.
Each row also says how to find out whether the device is there, and, for
devices NIS knows by name (shutters, lamps, filter wheels), how to list them.

Three parts of ZMART read this one table, so they can never disagree:

- the bridge inside NIS, which lets the driver call these functions and no
  others;
- the driver, which offers each setting that is present, asks the limits
  before changing it, and reads it back afterwards;
- the hardware check inside NIS (``testing/hardware/check_hardware.mac``),
  which tries each row on the real microscope, one at a time, and puts it back.

This file uses only Python's standard library, because NIS-Elements' own
Python loads it straight from its path, with nothing installed.

How a row writes a function's arguments. NIS's macro functions take plain
values and "pointers", places NIS fills in with its answer. In the table:

- ``{"ptr": "int"}`` (and ``double``, ``char``) is such a place;
- ``NAME`` is the device's name, as NIS lists it (a shutter called "EPI");
- ``INDEX`` is a number counted from 0, while the names are being listed;
- ``KIND`` is the number NIS gives one of a fixed set of devices (the four
  diaphragms: 0 is the transmitted-light field diaphragm, and so on);
- ``LASER`` is a laser unit's name, as NIS lists it, and ``LINE`` one of its
  lines, counted from 0;
- ``VALUE`` is the value being set;
- ``TEXT_SIZE`` is the room given to NIS for a text.

Where a function's answer is: ``"return"`` for its return value, or a number
for the argument NIS filled in (0 is the first argument).

What is known, and what is not. The function names and their arguments come
from NIS-Elements AR 7.01.03's own help (the device pages, "p4c8s...", read at
the microscope on 30 October 2026), and NIS 7 accepted every reading's
arguments at the microscope with the hardware switched off. Most readings
give their value as the function's own answer and take only the device's
name, if any. Nothing here has yet been seen to change real hardware: every
row says ``confirmed=False`` until the hardware check has tried it with the
microscope switched on.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

NAME = "$name"
INDEX = "$index"
KIND = "$kind"
LASER = "$laser"
LINE = "$line"
VALUE = "$value"
TEXT_SIZE = 256

INT = {"ptr": "int"}
DOUBLE = {"ptr": "double"}
TEXT = {"ptr": "char"}

#: Which incubator the incubator functions talk to. NIS names an incubator by
#: a key from its Device Manager and has no function that lists the keys; an
#: empty key, which NIS accepts, means the incubator there is.
INCUBATOR = ""

#: The kinds of value a setting takes.
SWITCH = "switch"  # 0 or 1: closed or open, off or on
CHOICE = "choice"  # a position, counted from 1
NUMBER = "number"  # a number, in the setting's unit

#: The Perfect Focus statuses that mean it is holding the focus (as reads.PFS_ON).
PFS_HOLDING = (1, 5, 6)


@dataclass(frozen=True)
class Call:
    """One NIS function, its arguments as a template, and where its answer is."""

    fn: str
    args: tuple = ()
    answer: Any = "return"


@dataclass(frozen=True)
class Setting:
    """One hardware setting: how to find it, read it and change it.

    ``key`` is its name in the driver (``shutter``). ``says`` explains it in
    plain words. ``devices`` says how the devices it belongs to are found:
    ``"one"`` (one device, there or not), ``"named"`` (listed by ``count`` and
    ``name``), ``"fixed"`` (the devices in ``fixed``, each there or not) or
    ``"laser_lines"`` (every line of every laser). ``present`` is asked for a
    single device, or for each fixed one; when it is None, the device counts as present
    when it can be read. ``positions``, for a choice, counts the positions.
    ``on_values``, for a switch whose reading says more than on and off, are
    the readings that mean on. ``tolerance`` is how close a number must read
    back. ``confirms`` is False for a target that the device only moves
    towards (a temperature): its reading is the current value, which lags.
    ``guarded`` settings are refused until this microscope's limits allow
    them, because a wrong value could harm the sample, the microscope or a
    person (lasers, heating, gas).

    Some devices have no "is it there?" function in NIS, and NIS then answers
    a reading of absent hardware with a meaningless number (a spinning disk
    read 7297880 with the hardware off). ``plausible`` is the range a real
    reading falls in: outside it, the device counts as not there.
    ``present_if`` names another setting of the same device: this one is
    there only when that one is (the disk's shutter, when the disk is).
    ``whole`` is True for a number NIS takes only as a whole number (the
    disk's speed, Perfect Focus's offset): NIS refuses 10.0 where it wants 10.
    ``fixed`` lists the devices of a setting whose devices NIS numbers in a
    fixed way rather than by name (``devices="fixed"``): each is
    ``(name in the driver, label, NIS's number)``.
    """

    key: str
    group: str
    says: str
    kind: str
    read: Call
    write: Call
    devices: str = "one"
    present: Call | None = None
    count: Call | None = None
    name: Call | None = None
    positions: Call | None = None
    on_values: tuple | None = None
    unit: str | None = None
    tolerance: float = 0.0
    confirms: bool = True
    guarded: bool = False
    confirmed: bool = False
    plausible: tuple | None = None
    present_if: str | None = None
    whole: bool = False
    fixed: tuple = ()
    functions: tuple = field(init=False, default=())

    def __post_init__(self) -> None:
        used = [
            self.read,
            self.write,
            self.present,
            self.count,
            self.name,
            self.positions,
        ]
        object.__setattr__(
            self, "functions", tuple(c.fn for c in used if c is not None)
        )


# --- how the devices are listed ------------------------------------------------------------

_SHUTTERS = {
    "devices": "named",
    "count": Call("Stg_GetShutterCount"),
    "name": Call("Stg_GetShutterName", (INDEX, TEXT, TEXT_SIZE), 1),
}
# Shutters take a text place and its size; lamps, filter changers and heating
# stages take only the text place (NIS's help, and NIS 7 at the microscope).
_LAMPS = {
    "devices": "named",
    "count": Call("Stg_Light_GetCount"),
    "name": Call("Stg_Light_GetName", (INDEX, TEXT), 1),
}
_FILTERS = {
    "devices": "named",
    "count": Call("Stg_Filter_GetCount"),
    "name": Call("Stg_Filter_GetName", (INDEX, TEXT), 1),
}
_HEAT_STAGES = {
    "devices": "named",
    "count": Call("Stg_HeatStage_GetCount"),
    "name": Call("Stg_HeatStage_GetName", (INDEX, TEXT), 1),
}
#: How the laser units and their lines are listed: the units by index, each
#: unit's name, its number of lines, and each line's name. The settings of a
#: line then name the unit and the line's number.
LASER_NAME = Call("Stg_GetMultiLaserName", (INDEX, TEXT), 1)
LASER_LINE_COUNT = Call("Stg_GetMultiLaserNumLines", (LASER,))
_LASER_LINES = {
    "devices": "laser_lines",
    "count": Call("Stg_GetMultiLaserCount"),
    "name": Call("Stg_GetMultiLaserLineName", (LASER, LINE, TEXT), 2),
}
#: The four diaphragms NIS knows, by the number its aperture functions take.
_DIAPHRAGMS = {
    "devices": "fixed",
    "fixed": (
        ("dia_field", "transmitted-light field diaphragm", 0),
        ("epi_field", "epi-fluorescence field diaphragm", 1),
        ("dia_aperture", "transmitted-light aperture diaphragm", 2),
        ("epi_aperture", "epi-fluorescence aperture diaphragm", 3),
    ),
}


TABLE: tuple[Setting, ...] = (
    # --- light ---------------------------------------------------------------------------
    Setting(
        key="shutter",
        group="light",
        says="a shutter, by its name in NIS: 1 open, 0 closed",
        kind=SWITCH,
        read=Call("Stg_GetShutterStateEx", (NAME,)),
        write=Call("Stg_SetShutterStateEx", (NAME, VALUE)),
        **_SHUTTERS,
    ),
    Setting(
        key="lamp",
        group="light",
        says="a lamp or LED, by its name in NIS: 1 on, 0 off",
        kind=SWITCH,
        read=Call("Stg_Light_GetState", (NAME,)),
        write=Call("Stg_Light_SetState", (NAME, VALUE)),
        **_LAMPS,
    ),
    Setting(
        key="lamp_voltage",
        group="light",
        says="a lamp's brightness, as NIS sets it (its voltage, or an LED's intensity)",
        kind=NUMBER,
        read=Call("Stg_Light_GetVoltage", (NAME,)),
        write=Call("Stg_Light_SetVoltage", (NAME, VALUE)),
        tolerance=0.05,
        **_LAMPS,
    ),
    Setting(
        key="lamp_iris",
        group="light",
        says="a lamp's iris, which sets how much of its light passes",
        kind=NUMBER,
        read=Call("Stg_Light_GetIrisIntensity", (NAME,)),
        write=Call("Stg_Light_SetIrisIntensity", (NAME, VALUE)),
        tolerance=0.05,
        **_LAMPS,
    ),
    Setting(
        key="lamp_nd_filter",
        group="light",
        says="a lamp's own neutral density filter, which dims it: its density, as NIS gives it",
        kind=NUMBER,
        read=Call("Stg_Light_GetNDFilterPosition", (NAME,)),
        write=Call("Stg_Light_SetNDFilterPosition", (NAME, VALUE)),
        positions=Call("Stg_Light_GetNDFilterSteps", (NAME,)),
        tolerance=0.01,
        **_LAMPS,
    ),
    Setting(
        key="nd_filter",
        group="light",
        says="the microscope's neutral density filter, which dims the light: its density, as NIS gives it",
        kind=NUMBER,
        read=Call("Stg_GetNDFilterPosition"),
        write=Call("Stg_SetNDFilterPosition", (VALUE,)),
        present=Call("Stg_IsNDFilterPresent"),
        positions=Call("Stg_GetNDFilterSteps"),
        tolerance=0.01,
    ),
    # --- filters -------------------------------------------------------------------------
    Setting(
        key="filter",
        group="filters",
        says="a filter wheel or filter turret, by its name in NIS: its position, from 1",
        kind=CHOICE,
        read=Call("Stg_Filter_GetPosition", (NAME,)),
        write=Call("Stg_Filter_SetPosition", (NAME, VALUE)),
        positions=Call("Stg_Filter_GetPositionCount", (NAME,)),
        **_FILTERS,
    ),
    # --- the light path and the optics ---------------------------------------------------------
    Setting(
        key="light_path",
        group="optics",
        says="where the light goes: to the eyepieces, a camera port, and so on, as a position from 1",
        kind=CHOICE,
        read=Call("Stg_GetLightPathPosition"),
        write=Call("Stg_SetLightPathPosition", (VALUE,)),
        present=Call("Stg_IsLightPathPresent"),
        positions=Call("Stg_GetLightPathPositions"),
    ),
    Setting(
        key="condenser",
        group="optics",
        says="the condenser turret's position, from 1 (for example brightfield, phase or DIC)",
        kind=CHOICE,
        read=Call("Stg_GetCondenserPosition"),
        write=Call("Stg_SetCondenserPosition", (VALUE,)),
        present=Call("Stg_IsCondenserPresent"),
        positions=Call("Stg_GetCondenserPositions"),
    ),
    Setting(
        key="aperture",
        group="optics",
        says="a diaphragm's opening: the field or aperture diaphragm, for transmitted light or epi-fluorescence",
        kind=NUMBER,
        read=Call("Stg_GetAperturePosition", (KIND,)),
        write=Call("Stg_SetAperturePosition", (KIND, VALUE)),
        present=Call("Stg_IsAperturePresent", (KIND,)),
        tolerance=0.01,
        **_DIAPHRAGMS,
    ),
    Setting(
        key="analyzer",
        group="optics",
        says="the analyzer for polarised light or DIC: 1 in the light path, 0 out",
        kind=SWITCH,
        read=Call("Stg_GetAnalyzerState"),
        write=Call("Stg_SetAnalyzerState", (VALUE,)),
        present=Call("Stg_IsAnalyzerPresent"),
    ),
    Setting(
        key="zoom",
        group="optics",
        says="the zoom's magnification",
        kind=NUMBER,
        read=Call("Stg_GetZoomPosition"),
        write=Call("Stg_SetZoomPosition", (VALUE,)),
        present=Call("Stg_IsZoomPresent"),
        tolerance=0.01,
    ),
    Setting(
        key="relay_lens",
        group="optics",
        says="the relay lens of a Yokogawa CSU-W1 spinning disk, at one of its camera ports, as a position from 1",
        kind=CHOICE,
        read=Call("Stg_GetRelayLensPosition", (KIND,)),
        write=Call("Stg_SetRelayLensPosition", (KIND, VALUE)),
        devices="fixed",
        # NIS's help does not number the ports; these are tried, and a port
        # that answers no position counted from 1 is not there.
        fixed=(("port_1", "camera port 1", 1), ("port_2", "camera port 2", 2)),
        plausible=(1, 10),
    ),
    Setting(
        key="correction_collar",
        group="optics",
        says="the objective's motorised correction collar (for the coverslip's thickness)",
        kind=NUMBER,
        read=Call("Stg_CorrectionCollar_GetPosition", (DOUBLE,), 0),
        write=Call("Stg_CorrectionCollar_SetPosition", (VALUE,)),
        tolerance=0.01,
    ),
    # --- focus ---------------------------------------------------------------------------------
    Setting(
        key="pfs",
        group="focus",
        says="Perfect Focus: 1 holds the focus, 0 lets it go",
        kind=SWITCH,
        read=Call("Stg_GetPFSStatus"),
        write=Call("Stg_SetPFSStatus", (VALUE,)),
        present=Call("Stg_IsPFSPresent"),
        on_values=PFS_HOLDING,
    ),
    Setting(
        key="pfs_offset",
        group="focus",
        says="Perfect Focus's offset: which plane it holds, above or below the coverslip",
        kind=NUMBER,
        read=Call("Stg_GetPFSLensOffsetPosition"),
        write=Call("Stg_SetPFSLensOffsetPosition", (VALUE,)),
        present=Call("Stg_IsPFSPresent"),
        tolerance=1.0,
        whole=True,
    ),
    # --- spinning disk and TIRF ------------------------------------------------------------
    Setting(
        key="disk_speed",
        group="spinning disk",
        says="the spinning disk's speed",
        kind=NUMBER,
        read=Call("Stg_GetDiskSpeed"),
        write=Call("Stg_SetDiskSpeed", (VALUE,)),
        tolerance=1.0,
        whole=True,
        # No "is it there?" function: a real disk turns at most a few thousand rpm.
        plausible=(0, 20000),
    ),
    Setting(
        key="disk_shutter",
        group="spinning disk",
        says="the spinning-disk unit's own shutter: 1 open, 0 closed",
        kind=SWITCH,
        read=Call("Stg_GetCSUShutterState"),
        write=Call("Stg_SetCSUShutterState", (VALUE,)),
        present_if="disk_speed",
    ),
    Setting(
        key="tirf_position",
        group="TIRF",
        says="the TIRF illuminator's position, which sets the angle of the laser at the coverslip",
        kind=NUMBER,
        read=Call("Stg_GetTIRFPosition"),
        write=Call("Stg_SetTIRFPosition", (VALUE,)),
        tolerance=0.01,
        guarded=True,
    ),
    # --- lasers ----------------------------------------------------------------------------------
    Setting(
        key="laser_line",
        group="lasers",
        says="one line of a laser unit (the unit's name, a dot, and the line from 0): 1 used, 0 not used",
        kind=SWITCH,
        read=Call("Stg_GetMultiLaserLineUsed", (LASER, LINE)),
        write=Call("Stg_SetMultiLaserLineUsed", (LASER, LINE, VALUE)),
        guarded=True,
        **_LASER_LINES,
    ),
    Setting(
        key="laser_power",
        group="lasers",
        says="the power of one line of a laser unit (the unit's name, a dot, and the line from 0), in percent",
        kind=NUMBER,
        read=Call("Stg_GetMultiLaserPower", (LASER, LINE)),
        write=Call("Stg_SetMultiLaserPower", (LASER, LINE, VALUE)),
        unit="%",
        tolerance=0.5,
        whole=True,  # NIS takes the power as a whole number
        guarded=True,
        **_LASER_LINES,
    ),
    # --- the sample's environment ----------------------------------------------------------
    Setting(
        key="stage_temperature",
        group="environment",
        says="a heating stage's target temperature; the reading is its current temperature",
        kind=NUMBER,
        read=Call("Stg_HeatStage_GetCurrentTemperature", (NAME,)),
        write=Call("Stg_HeatStage_SetTemperature", (NAME, VALUE)),
        unit="degrees C",
        confirms=False,
        guarded=True,
        # NIS answered 7298120 for a heating stage with the hardware off.
        plausible=(-50, 150),
        **_HEAT_STAGES,
    ),
    Setting(
        key="incubator_gas",
        group="environment",
        says="the incubator's target CO2 (or other gas), in percent",
        kind=NUMBER,
        read=Call("Stg_Incubator_GetTargetGas", (INCUBATOR, DOUBLE), 1),
        write=Call("Stg_Incubator_SetTargetGas", (INCUBATOR, VALUE)),
        unit="%",
        tolerance=0.05,
        guarded=True,
        # NIS answers "done, 0 %" even with no incubator there, so a target of 0 counts as none.
        plausible=(0.01, 100),
    ),
    Setting(
        key="incubator_humidity",
        group="environment",
        says="the incubator's target humidity, in percent",
        kind=NUMBER,
        read=Call("Stg_Incubator_GetTargetHumidity", (INCUBATOR, DOUBLE), 1),
        write=Call("Stg_Incubator_SetTargetHumidity", (INCUBATOR, VALUE)),
        unit="%",
        tolerance=0.5,
        guarded=True,
        plausible=(0.01, 100),
    ),
)

#: The settings by their key.
SETTINGS = {setting.key: setting for setting in TABLE}

#: Every NIS function a row may read through, and every one it may change something with.
READ_FUNCTIONS = frozenset(
    c.fn
    for s in TABLE
    for c in (s.read, s.present, s.count, s.name, s.positions)
    if c is not None
)
WRITE_FUNCTIONS = frozenset(s.write.fn for s in TABLE)


# --- using the table ---------------------------------------------------------------------------
#
# *call* is how a function reaches NIS: ``call(fn, args)`` answers
# ``{"return": ..., "values": [...]}``. Inside NIS it is the bridge's own
# ``call``; in the driver it is the client's ``read``. So the same code reads
# the same way in both places.


def setting(key: str) -> Setting:
    """The row for *key*; ``ValueError`` naming the settings there are."""
    found = SETTINGS.get(key)
    if found is None:
        raise ValueError(
            f"no hardware setting {key!r}; the settings are {sorted(SETTINGS)}"
        )
    return found


def tokens_for(row: Setting, instance: str | None) -> dict:
    """What fills a row's ``NAME``, ``KIND``, ``LASER`` and ``LINE`` for one of its devices."""
    if row.devices == "one":
        return {}
    if row.devices == "named":
        if not instance:
            raise ValueError(
                f"{row.key} needs the device's name, such as {row.key}/EPI"
            )
        return {NAME: instance}
    if row.devices == "fixed":
        for name, _, number in row.fixed:
            if instance == name:
                return {KIND: number}
        raise ValueError(
            f"{row.key} needs one of {[name for name, _, _ in row.fixed]}, not {instance!r}"
        )
    # A laser line is named by its unit's name, a dot, and the line's number:
    # "LU-N4.1". The unit's own name may hold dots, so the last one counts.
    laser, _, line = str(instance or "").rpartition(".")
    if not laser or not line.isdigit():
        raise ValueError(
            f"{row.key} needs the laser unit's name and the line, such as {row.key}/LU-N4.1"
        )
    return {LASER: laser, LINE: int(line)}


def arguments(template: tuple, tokens: dict, value: Any = None) -> list:
    """The template with its tokens filled in."""
    filled = []
    for arg in template:
        if arg == VALUE:
            filled.append(value)
        elif isinstance(arg, str) and arg in tokens:
            filled.append(tokens[arg])
        else:
            filled.append(dict(arg) if isinstance(arg, dict) else arg)
    return filled


class NisFailed(RuntimeError):
    """NIS answered a function of the table with one of its failure codes (0 or below)."""

    def __init__(self, fn: str, code: Any) -> None:
        super().__init__(
            f"{fn} answered {code}: NIS could not do it (the device may not be there)"
        )
        self.fn = fn
        self.code = code


def _is_code(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def answer_of(request: Call, found: dict) -> Any:
    """The answer of *request*, from what NIS returned; ``NisFailed`` for a failure code.

    When the answer is an argument NIS filled in, the return value is NIS's
    code for how the call went, and 0 or below is a failure. When the answer
    is the return value itself (a count, a position, a state), a value below
    0 is a failure code: no count, position or state is negative.
    """
    returned = found.get("return")
    if request.answer == "return":
        if _is_code(returned) and returned < 0:
            raise NisFailed(request.fn, returned)
        return returned
    if _is_code(returned) and returned < 1:
        raise NisFailed(request.fn, returned)
    return found["values"][request.answer]


def ask(call: Callable, request: Call, tokens: dict | None = None) -> Any:
    """Call one function of the table and take its answer; ``NisFailed`` when NIS could not."""
    return answer_of(request, call(request.fn, arguments(request.args, tokens or {})))


def as_value(row: Setting, raw: Any) -> Any:
    """A reading as the setting's kind: 0 or 1 for a switch, a whole number for a choice, else a number."""
    if row.kind == SWITCH:
        if row.on_values is not None:
            return 1 if int(raw) in row.on_values else 0
        return 1 if int(raw) else 0
    if row.kind == CHOICE:
        return int(raw)
    return float(raw)


def read(call: Callable, row: Setting, instance: str | None = None) -> Any:
    """The setting's value now, for one of its devices."""
    return as_value(row, ask(call, row.read, tokens_for(row, instance)))


def write_arguments(row: Setting, instance: str | None, value: Any) -> list:
    """The arguments of the row's change for one device and one value."""
    return arguments(row.write.args, tokens_for(row, instance), sent_value(row, value))


def sent_value(row: Setting, value: Any) -> Any:
    """*value* as NIS is sent it: rounded to a whole number for a setting that takes only those."""
    if row.whole and isinstance(value, (int, float)) and not isinstance(value, bool):
        return round(value)
    return value


def matches(row: Setting, value: Any, reading: Any) -> bool:
    """Whether *reading* shows *value* set: equal, or within the tolerance for a number."""
    try:
        if row.kind == NUMBER:
            return abs(float(reading) - float(value)) <= row.tolerance
        return int(reading) == int(value)
    except (TypeError, ValueError):
        return False


def checked_value(row: Setting, value: Any, positions: int | None = None) -> Any:
    """*value* as the setting takes it; ``ValueError`` saying what it may be."""
    if row.kind == SWITCH:
        if value in (True, False, 0, 1):
            return int(value)
        raise ValueError(f"{row.key} is 1 or 0 ({row.says}), not {value!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{row.key} takes a number, not {value!r}")  # noqa: TRY004 -- every wrong value is a ValueError
    if row.kind == CHOICE:
        if (
            int(value) != value
            or value < 1
            or (positions is not None and value > positions)
        ):
            room = f"1 to {positions}" if positions else "a position counted from 1"
            raise ValueError(f"{row.key} takes {room}, not {value!r}")
        return int(value)
    if not math.isfinite(float(value)):
        raise ValueError(f"{row.key} takes a finite number, not {value!r}")
    return float(value)


def instances(call: Callable, row: Setting) -> list[dict]:
    """The devices of this row that NIS has: ``[{"instance", "label"}]``; empty when there are none.

    A device listed by name is there when NIS names it. A single device is
    there when its ``present`` function says so, or, without one, when it
    can be read. Any error asking NIS means "not there" for that device.
    """
    if row.devices == "one":
        there = _there(call, row, None)
        return [{"instance": None, "label": None}] if there else []
    if row.devices == "fixed":
        return [
            {"instance": name, "label": label}
            for name, label, _ in row.fixed
            if _there(call, row, name)
        ]
    found = []
    try:
        count = int(ask(call, row.count) or 0)
    except NisFailed:
        return []  # NIS cannot count them: there are none on this microscope
    if row.devices == "named":
        for index in range(max(count, 0)):
            name = str(ask(call, row.name, {INDEX: index})).strip()
            if name and (row.plausible is None or _there(call, row, name)):
                found.append({"instance": name, "label": name})
        return found
    for index in range(max(count, 0)):
        laser = str(ask(call, LASER_NAME, {INDEX: index})).strip()
        if not laser:
            continue
        lines = int(ask(call, LASER_LINE_COUNT, {LASER: laser}) or 0)
        for line in range(max(lines, 0)):
            label = str(ask(call, row.name, {LASER: laser, LINE: line})).strip()
            found.append(
                {
                    "instance": f"{laser}.{line}",
                    "label": label or f"{laser} line {line}",
                }
            )
    return found


def _there(call: Callable, row: Setting, instance: str | None) -> bool:
    """Whether one single or fixed device is there: its "is it there?" function, or a sensible reading.

    Some devices have no "is it there?" function in NIS; for those, a reading
    that NIS gives and that falls in the setting's ``plausible`` range counts
    as there. Any error asking NIS means "not there".
    """
    tokens = tokens_for(row, instance)
    try:
        if row.present is not None:
            return int(ask(call, row.present, tokens)) > 0
        value = read(call, row, instance)
    except Exception:  # noqa: BLE001 -- a device NIS cannot answer for is not offered
        return False
    return row.plausible is None or row.plausible[0] <= value <= row.plausible[1]


def discover(call: Callable) -> dict:
    """Every setting of the table, as NIS has it now.

    ``{key: {"group", "says", "kind", "unit", "guarded", "devices": [{"instance", "label",
    "positions", "value"}], "error"}}``. A setting whose devices cannot be
    listed has no devices and says why under ``error``.
    """
    found = {}
    for row in TABLE:
        entry = {
            "group": row.group,
            "says": row.says,
            "kind": row.kind,
            "unit": row.unit,
            "guarded": row.guarded,
            "devices": [],
            "error": None,
        }
        if row.present_if is not None and not (found.get(row.present_if) or {}).get(
            "devices"
        ):
            found[row.key] = entry  # the device it belongs to is not there
            continue
        try:
            for device in instances(call, row):
                tokens = tokens_for(row, device["instance"])
                positions = None
                if row.positions is not None:
                    try:
                        positions = int(ask(call, row.positions, tokens))
                    except Exception:  # noqa: BLE001 -- the count of positions is only a help
                        positions = None
                try:
                    value = read(call, row, device["instance"])
                except Exception as why:  # noqa: BLE001 -- reported with the device
                    value = None
                    device["error"] = str(why)
                entry["devices"].append(
                    {**device, "positions": positions, "value": value}
                )
        except Exception as why:  # noqa: BLE001 -- reported with the setting
            entry["error"] = f"{type(why).__name__}: {why}"
        found[row.key] = entry
    return found


#: The functions the listing of laser lines needs besides a row's own.
EXTRA_READ_FUNCTIONS = frozenset({LASER_NAME.fn, LASER_LINE_COUNT.fn})
READ_FUNCTIONS = READ_FUNCTIONS | EXTRA_READ_FUNCTIONS
