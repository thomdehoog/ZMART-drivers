"""The named changes of a session: move the stage, change a setting or the objective, take a picture, take a tile scan.

Each one checks its arguments, builds its change as plain data
(``primitives/direct/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Each hands ``run()``
LAS X's dialogs (``answer_dialogs.py``), answered around every send. Then it
adds what the caller needs to the result (``move_xy`` adds the target
position). A change refused before anything was sent answers the same
shape, with ``success`` False.

A capture and a tile scan are sent once, then the scan is watched
(:func:`confirm_scan`): it must be seen running, then idle twice in a row.
Each names the LAS X project its picture goes into, chosen by
``clear_cache/data_container.experiment_for_capture`` as the Navigator
Expert driver chooses it: the STELLARIS refused a tile scan sent into project
``0`` (2026-10-15). ``take_picture`` and ``take_tile_scan`` are what the
rest of the driver calls.

Result contract: ``result["success"]`` means the change was *accepted*, not
that it took effect. A caller that needs proof the change landed checks
``result["confirmed"]``. The one exception is a capture whose scan never
starts: it fails.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any

from ..dispatcher import dispatch
from ..dispatcher import write as write_loops
from ..dispatcher.dispatch import make_log_entry
from ..primitives.direct import profile as direct_profile
from ..primitives.direct import writes
from ..primitives.direct.client import LASX_LOG
from . import answer_dialogs
from . import readings as readers
from .clear_cache import data_container
from .save_data.product import AcquisitionResult

log = logging.getLogger(__name__)


#: How a command names the setting it changes, by its place in the job. LCS takes
#: it so, whichever setting is current: with Setting 2 current, laser lines sent
#: for index 0 went into Setting 1 (simulator, 2026-11-01); the window sends 1.
ADDRESSED = ('SingleSettingIndex="0"', 'SequentialSettingIndex="0"', '<Sequence Index="0">')


def run(client, change, *, max_retries=None):
    """One change through the dispatcher, with LAS X's dialogs answered around every send.

    A change whose commands name a setting is addressed to the current one,
    by its place in the job (:func:`for_current_setting`).
    """
    addressed = for_current_setting(client, change)
    if isinstance(addressed, str):
        return dispatch.refused(addressed)
    return dispatch.run(direct_profile, client, addressed, max_retries=max_retries, dialogs=answer_dialogs)


def for_current_setting(client, change):
    """*change* addressed to the current setting; a reason, as text, when its place cannot be read.

    A change whose commands name no setting is returned as it is.
    """
    probe = writes.Request(change.request)
    try:
        change.fill(probe)
    except Exception:  # noqa: BLE001 -- a change that cannot fill itself fails in the dispatcher, as before
        return change
    if not any(marker in element for element in probe.elements or [] for marker in ADDRESSED):
        return change
    shown = readers.get_setting(client)
    place = next((i for i, each in enumerate(shown.get("all") or []) if each["id"] == shown["id"]), None)
    if place is None:
        return f"{change.description}: the current setting's place in the job could not be read; nothing was sent"
    if place == 0:
        return change

    def fill(request):
        request.setting = place
        change.fill(request)

    return replace(change, fill=fill)


def activate_setting(client, name, *, max_retries=None) -> dict:
    """Make the setting called *name* the current one, as clicking it in the window does; read back."""
    listed = readers.get_setting(client)["all"]
    chosen = [s for s in listed if s["name"] == name]
    if not chosen:
        return dispatch.refused(f"no setting {name!r}; the settings are {[s['name'] for s in listed]}")
    return run(client, writes.activate_setting(chosen[0]["id"]), max_retries=max_retries)


# =============================================================================
# The stage and the z drives
# =============================================================================


def move_xy(client, x_um, y_um, *, max_retries=None, tolerance=None):
    """Move the stage to (*x_um*, *y_um*), in micrometres of the stage's own coordinates.

    The result's ``position`` is the target (micrometres and metres), not a
    read-back: ``confirmed`` says whether the stage was seen there. It is
    None when nothing was sent.
    """
    here = readers.get_xy(client)
    if not here:
        return dispatch.refused(
            "where the stage stands could not be read, so the step of the move cannot be checked", position=None
        )
    try:
        change = writes.move_xy(
            float(x_um), float(y_um), tolerance=tolerance,
            start={"from_x_um": here["x_um"], "from_y_um": here["y_um"]},
        )
    except (TypeError, ValueError) as why:
        return dispatch.refused(f"a stage move takes two numbers: {why}", position=None)
    result = run(client, change, max_retries=max_retries)
    sent = result["timing"].get("attempts", 0) > 0
    result["position"] = (
        {"x": change.values["x_um"] * 1e-6, "y": change.values["y_um"] * 1e-6, "x_um": change.values["x_um"], "y_um": change.values["y_um"]}
        if sent
        else None
    )
    return result


def move_z(client, drive, z_um, *, max_retries=None, tolerance=None):
    """Move the z drive *drive* (``"z-galvo"`` or ``"z-wide"``) to *z_um*, in micrometres.

    LAS X's plain Navigator moves z by a step, so the step is worked out
    from where the drive stands just before every send (``writes.move_z``).
    """
    axis = {"z-galvo": "z_galvo_um", "z-wide": "z_wide_um"}.get(drive)
    now = readers.read_z_um(client, drive) if axis else None
    if axis and now is None:
        return dispatch.refused(f"where {drive} stands could not be read, so the step of the move cannot be checked")
    try:
        change = writes.move_z(drive, float(z_um), tolerance=tolerance, start={f"from_{axis}": now} if axis else None)
    except (TypeError, ValueError) as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


# =============================================================================
# The settings and the objective
# =============================================================================


def change(client, name: str, value, *, max_retries=None, tolerance=None) -> dict:
    """Change the setting *name* to *value*, and read it back.

    *name* is a row of the table of numbers (``writes.SETTINGS``: zoom,
    scan_speed, ...) or a setting with a write of its own
    (``writes.NAMED_SETTINGS``: scan_mode, autofocus, format, resonant_scanner,
    bidirectional, phase_x, pinhole_um, pinhole_unit, emission_wavelength, bit_depth,
    system_optimized_step, collar, smartcorr_range, setting_name, sequential_mode). A wrong name or a
    value of the wrong kind raises ``ValueError``. The limits gate is
    shown the new value first, and nothing is sent if it refuses.
    *tolerance* overrides how close a number's read-back must come.
    """
    if name in writes.SETTINGS:
        change = writes.setting(name, value, tolerance=tolerance)
    else:
        change = writes.named_setting(name, value)
    return run(client, change, max_retries=max_retries)


def laser_line(client, wavelength_nm):
    """``(route, line_index)`` of the laser line at *wavelength_nm*; RuntimeError when there is none.

    A fixed line is found whether it is on or not; a tunable line (the
    white-light laser's) only when it is on, at that wavelength, since an off
    one has none.
    """
    for source in readers.get_light_sources(client) or []:
        route = writes.route_of(source.get("BeamRoute"))
        for line in source.get("LaserLines", []):
            if line.get("MinWavelength") == line.get("MaxWavelength") == float(wavelength_nm):
                return route, int(line["Index"])
    tuned = tunable_line_on(client, wavelength_nm)
    if tuned is not None:
        return tuned[0], tuned[1]
    raise RuntimeError(f"no laser line at {wavelength_nm:g} nm (a white-light line must be on at that wavelength)")


def lines_in_any_setting(client, route) -> list[dict]:
    """The lines of the laser on *route* in use in any of the job's settings, the current one's included.

    A tunable laser's slot has one wavelength for every setting (simulator,
    2026-11-01): a slot another setting uses is taken, and its wavelength counts.
    """
    current = readers.get_setting(client)["name"]
    lines = [
        {"setting": current, "lineIndex": line["lineIndex"], "wavelength": line["wavelength"]}
        for line in readers.get_laser_lines(client) or [] if writes.route_of(line["beamRoute"]) == route
    ]
    for slot in readers.get_laser_slots(client) or []:
        if writes.route_of(slot["beamRoute"]) == route and slot["setting"] != current:
            lines.append({"setting": slot["setting"], "lineIndex": slot["lineIndex"], "wavelength": slot["wavelength"]})
    return lines


#: The most laser lines one setting can have on, all lasers counted; LAS X refuses a ninth with a
#: message box ("The maximum number of 8 visible laser lines is reached", simulator, 2026-11-01).
MAX_VISIBLE_LINES = 8

#: The least distance, in nm, between two lines of the white-light laser that are on: LAS X
#: holds a warning open otherwise ("Please keep minimum distance of 8nm"), and with it every command.
WLL_MIN_DISTANCE_NM = 8.0


def tunable_line_on(client, wavelength_nm):
    """``(route, line_index, (min_nm, max_nm), other_wavelengths)`` of the tunable line on at *wavelength_nm*, or None."""
    sources = {writes.route_of(s.get("BeamRoute")): s for s in readers.get_light_sources(client) or []}
    on = readers.get_laser_lines(client) or []
    for line in on:
        route = writes.route_of(line["beamRoute"])
        ranges = [(l["MinWavelength"], l["MaxWavelength"]) for l in sources.get(route, {}).get("LaserLines", [])]
        tunable = [r for r in ranges if r[0] != r[1]]
        if tunable and abs(line["wavelength"] - float(wavelength_nm)) < 0.5:
            others = [o["wavelength"] for o in on if writes.route_of(o["beamRoute"]) == route and o["lineIndex"] != line["lineIndex"]]
            return route, int(line["lineIndex"]), (min(r[0] for r in tunable), max(r[1] for r in tunable)), others
    return None


def add_laser_line(client, wavelength_nm, percent=2.0, *, max_retries=None) -> dict:
    """Switch on a line of the white-light laser at *wavelength_nm* with *percent* power, as the window's Add Laser does; read back.

    The lines on now are sent again with the new one, on the laser's next
    free line, as one definition; LCS lists it with no power (simulator,
    2026-11-01), so its power is then set like any line's, through the
    limits (2 % is the window's own starting power). Refused, with nothing
    sent, outside the laser's range, within :data:`WLL_MIN_DISTANCE_NM` of
    its lines that are on, without a free line, or while a line is locked
    for the Dye Assistant (the definition would unlock it), or with a power
    outside 0 to 100 %. The limits are asked for the power when it is set.
    """
    try:
        writes.laser_power((0,), 0, float(wavelength_nm), percent)
    except (ValueError, TypeError) as why:
        return dispatch.refused(f"the line cannot be switched on: {why}")
    on = readers.get_laser_lines(client) or []
    if len(on) >= MAX_VISIBLE_LINES:
        return dispatch.refused(
            f"the setting has {len(on)} laser lines on, LAS X's most ({MAX_VISIBLE_LINES}); remove one first; nothing was sent"
        )
    tunable = [
        s for s in readers.get_light_sources(client) or []
        if any(line["MinWavelength"] != line["MaxWavelength"] for line in s.get("LaserLines", []))
    ]
    if not tunable:
        return dispatch.refused("this microscope has no tunable laser; nothing was sent")
    source = tunable[0]
    route = writes.route_of(source["BeamRoute"])
    low = min(line["MinWavelength"] for line in source["LaserLines"])
    high = max(line["MaxWavelength"] for line in source["LaserLines"])
    if not low <= float(wavelength_nm) <= high:
        return dispatch.refused(f"{wavelength_nm:g} nm is outside the laser's range, {low:g} to {high:g} nm; nothing was sent")
    mine = lines_in_any_setting(client, route)
    near = [line["wavelength"] for line in mine if abs(line["wavelength"] - float(wavelength_nm)) < WLL_MIN_DISTANCE_NM]
    if near:
        return dispatch.refused(f"{wavelength_nm:g} nm is within {WLL_MIN_DISTANCE_NM:g} nm of the line at {near[0]:g} nm; nothing was sent")
    used = {line["lineIndex"] for line in mine}
    free = [line["Index"] for line in source["LaserLines"] if line["Index"] not in used]
    if not free:
        return dispatch.refused("the laser has no free line; switch one off first; nothing was sent")
    if readers.any_laser_line_locked(client):
        return dispatch.refused("a laser line is locked for the Dye Assistant; unlock it first; nothing was sent")
    lines = [(writes.route_of(line["beamRoute"]), line["lineIndex"], line["wavelength"]) for line in on]
    lines.append((route, min(free), float(wavelength_nm)))
    listed = run(client, writes.laser_lines_definition(lines, float(wavelength_nm)), max_retries=max_retries)
    if not listed.get("success"):
        return listed
    return run(client, writes.laser_power(route, min(free), float(wavelength_nm), percent), max_retries=max_retries)


def remove_laser_line(client, wavelength_nm, *, max_retries=None) -> dict:
    """Remove the laser line that is on at *wavelength_nm* from the setting, as the window does; read back.

    The other lines that are on are sent as the definition, without it. A
    line set to 0 % stays listed in the window, dark; this takes it away.
    Refused, with nothing sent, when no line is on at *wavelength_nm*, or
    while a line is locked for the Dye Assistant (the definition would unlock it).
    """
    on = readers.get_laser_lines(client) or []
    gone = [line for line in on if abs(line["wavelength"] - float(wavelength_nm)) < 0.5]
    if not gone:
        return dispatch.refused(f"no laser line is on at {wavelength_nm:g} nm; nothing was sent")
    if readers.any_laser_line_locked(client):
        return dispatch.refused("a laser line is locked for the Dye Assistant; unlock it first; nothing was sent")
    lines = [(writes.route_of(line["beamRoute"]), line["lineIndex"], line["wavelength"]) for line in on if line not in gone]
    return run(client, writes.laser_line_removed(lines, float(wavelength_nm)), max_retries=max_retries)


def tune_laser_line(client, from_nm, to_nm, *, max_retries=None) -> dict:
    """Tune the white-light line that is on at *from_nm* to *to_nm*, as the window does; read back.

    Refused, with nothing sent, when no tunable line is on at *from_nm*, when
    *to_nm* lies outside the laser's range, or when it comes within
    :data:`WLL_MIN_DISTANCE_NM` of another of its lines that is on.
    """
    found = tunable_line_on(client, from_nm)
    if found is None:
        return dispatch.refused(f"no white-light line is on at {from_nm:g} nm; nothing was sent")
    route, index, (low, high), others = found
    current = readers.get_setting(client)["name"]
    sharing = [line for line in lines_in_any_setting(client, route) if line["lineIndex"] == index and line["setting"] != current]
    if sharing:
        return dispatch.refused(
            f"the line at {from_nm:g} nm is also in use in {sharing[0]['setting']}, which shares its wavelength; "
            "tuning it would change it there too; nothing was sent"
        )
    others = [line["wavelength"] for line in lines_in_any_setting(client, route) if line["lineIndex"] != index]
    if not low <= float(to_nm) <= high:
        return dispatch.refused(f"{to_nm:g} nm is outside the laser's range, {low:g} to {high:g} nm; nothing was sent")
    near = [o for o in others if abs(o - float(to_nm)) < WLL_MIN_DISTANCE_NM]
    if near:
        return dispatch.refused(
            f"{to_nm:g} nm is within {WLL_MIN_DISTANCE_NM:g} nm of the line at {near[0]:g} nm; nothing was sent"
        )
    try:
        change = writes.laser_wavelength(route, index, float(to_nm))
    except ValueError as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


def set_laser_power(client, wavelength_nm, percent, *, max_retries=None) -> dict:
    """Set the power of the laser line at *wavelength_nm* to *percent*, switching it on above 0 and off at 0; read back."""
    try:
        route, index = laser_line(client, wavelength_nm)
        change = writes.laser_power(route, index, float(wavelength_nm), percent)
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the laser power cannot be set: {why}")
    return run(client, change, max_retries=max_retries)


def set_laser_shutter(client, wavelength_nm, open_, *, max_retries=None) -> dict:
    """Open or close the shutter of the light source the line at *wavelength_nm* comes from; read back from its lines that are on."""
    try:
        route, _index = laser_line(client, wavelength_nm)
        change = writes.laser_shutter(route, open_)
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the shutter cannot be set: {why}")
    return run(client, change, max_retries=max_retries)


#: The route of LAS X's spectral detection: a detector on 40;n is channel n.
SPECTRAL_DETECTION = 40


def set_channel_colour(client, detector, colour, *, max_retries=None) -> dict:
    """Give the channel of *detector* (as LAS X names it, ``"HyD S 3"``) the colour *colour*; read back.

    Only the detectors of the spectral detection: how the others' channels
    are numbered has not been learned.
    """
    units = {unit.get("Name"): unit for unit in readers.get_detection_units(client) or []}
    unit = units.get(detector)
    route = tuple(int(part) for part in str((unit or {}).get("BeamRoute", "")).split(";") if part)
    if len(route) != 2 or route[0] != SPECTRAL_DETECTION:
        return dispatch.refused(f"{detector!r} is not a detector of the spectral detection; its channel's colour is not set here")
    try:
        change = writes.channel_colour(route, colour)
    except ValueError as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


#: LAS X's own wheels: the galvo and scanner switches (types 200, 160, 170 on the
#: simulator) and the multi-function port. They are never turned by this driver.
INTERNAL_WHEELS = (200, 160, 170)


def set_filter_wheel(client, wheel_type, filter_name, *, max_retries=None) -> dict:
    """Turn the filter wheel of type *wheel_type* to the filter named *filter_name*; read back.

    The filter is found by its name among the wheel's filters (LAS X's names,
    trailing spaces ignored). LAS X's own galvo and scanner wheels and the
    multi-function port are refused.
    """
    from ..primitives.direct.reads import MULTI_FUNCTION_PORT

    if wheel_type in INTERNAL_WHEELS or wheel_type == MULTI_FUNCTION_PORT:
        return dispatch.refused(f"wheel {wheel_type} is one of LAS X's own scanner wheels, never turned by this driver")
    wheel = next((w for w in readers.get_filter_wheels(client) or [] if w.get("type") == wheel_type), None)
    if wheel is None:
        return dispatch.refused(f"there is no filter wheel of type {wheel_type}")
    names = [str(name).strip() for name in wheel.get("filterNames", [])]
    if str(filter_name).strip() not in names:
        return dispatch.refused(f"wheel {wheel_type} holds no filter named {filter_name!r}; it holds {names}")
    change = writes.filter_wheel(wheel_type, names.index(str(filter_name).strip()), str(filter_name).strip())
    return run(client, change, max_retries=max_retries)


def objective_by_slot(objectives) -> dict[int, dict[str, Any]]:
    """``{slot: objective}`` for every occupied slot of the turret (``readings.get_objectives``).

    Empty slots (``objectiveNumber`` 0) are left out: sending one to LAS X
    can open an error dialog that blocks it.
    """
    by_slot = {}
    for objective in objectives or []:
        if not objective.get("objectiveNumber"):
            continue
        slot = objective.get("slotIndex")
        if slot is not None:
            by_slot[int(slot)] = objective
    return by_slot


def find_objective(objectives, slot_index=None, name=None, magnification=None):
    """``(slot, name)`` of the objective with this slot, name or magnification; ``(None, None)`` if none."""
    for objective in objective_by_slot(objectives).values():
        slot = objective.get("slotIndex")
        found = (objective.get("name") or "").strip()
        magnified = objective.get("magnification")
        if slot_index is not None and slot == slot_index:
            return slot, found
        if name is not None and found == name.strip():
            return slot, found
        if magnification is not None and magnified == magnification:
            return slot, found
    return None, None


def set_objective(
    client, objectives, slot_index=None, name=None, magnification=None, *, max_retries=None, before_fire=None
):
    """Change the objective, by its slot, name or magnification (exactly one), without moving the stage.

    This is the bare change. The one that also keeps the sample in view is
    ``procedures/keep_in_view.py``; it passes *before_fire* to take its
    reading just before the change is sent. *before_fire* is called with
    ``{"slot", "name"}``; it may return a refusal, and then nothing is sent.
    A change to an objective of another immersion (dry, water, oil ...) is
    refused, and so is one when the objective in use cannot be read: an
    immersion change needs a person at the microscope.
    """
    selectors = [v for v in (slot_index, name, magnification) if v is not None]
    if len(selectors) != 1:
        return dispatch.refused(
            f"exactly one of slot_index, name, or magnification must be provided (got {len(selectors)})"
        )
    slot, target_name = find_objective(objectives, slot_index=slot_index, name=name, magnification=magnification)
    if slot is None:
        available = [(o.get("slotIndex"), (o.get("name") or "").strip()) for o in objective_by_slot(objectives).values()]
        return dispatch.refused(
            f"Could not find objective: slot_index={slot_index}, name={name}, mag={magnification}. "
            f"Available: {available}"
        )
    refusal = immersion_refusal(client, objectives, slot)
    if refusal:
        return refusal
    if before_fire is not None:
        refusal = before_fire({"slot": slot, "name": target_name})
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    return run(client, writes.set_objective(slot, target_name), max_retries=max_retries)


def immersion_refusal(client, objectives, slot) -> dict | None:
    """A refusal when the objective in *slot* has another immersion than the one in use, or that cannot be told; else None."""
    current = readers.get_objective(client)
    target = objective_by_slot(objectives).get(slot) or {}
    have = str((current or {}).get("immersion") or "").strip().upper()
    want = str(target.get("immersion") or "").strip().upper()
    if not have or not want:
        return dispatch.refused(
            f"the immersion of the objective in use ({have or 'unknown'}) or of slot {slot} "
            f"({want or 'unknown'}) could not be read, so the turret is not turned", logged=True
        )
    if have != want:
        return dispatch.refused(
            f"slot {slot} holds an objective for {want} immersion and the one in use is {have}: "
            f"a change of immersion needs a person at the microscope, so the turret is not turned", logged=True
        )
    return None


# =============================================================================
# Taking a picture
# =============================================================================

#: How many idle scan-status reads in a row prove a scan has finished (a
#: single idle read can fall between two frames). A rule, not a tuning.
IDLE_STREAK_REQUIRED = 2


def picture(client, change, **watching) -> dict:
    """Send *change* (a capture or a tile scan) once, then watch the scan run; the change's result.

    A picture whose scan never starts fails: ``success`` False, and the
    message says so. One that started but was not seen to finish is a
    success with ``confirmed`` False, like any unconfirmed change.
    """
    handed_over = time.time()
    result = run(client, change)
    if not result["success"]:
        return result

    t0 = time.perf_counter()
    watched = confirm_scan(client, **{k: v for k, v in watching.items() if v is not None})
    watch_s = time.perf_counter() - t0
    result["logs"].extend(watched["logs"])
    result["timing"].update(confirm_s=watch_s, total_s=result["timing"]["total_s"] + watch_s, confirm_attempts=1)
    result["confirmed"] = watched["success"]
    if watched["success"]:
        return result
    dialog = write_loops.append_dialog_warning(
        direct_profile.WRITING.transport, result["logs"], handed_over, change.description
    )
    if not watched["started"]:
        result["success"] = False
        result["message"] = f"{change.description} | the scan never started{dialog}"
        return result
    msg = f"{change.description} | UNCONFIRMED: the scan started, but was not seen to finish"
    log.warning(msg)
    result["logs"].append(make_log_entry("warning", msg))
    result["message"] = f"{change.description} (readback unconfirmed){dialog}"
    return result


def capture(client, project=None, **watching) -> dict:
    """Take one picture with the current settings, the whole z-stack when one is set; see :func:`picture`.

    *project* is the LAS X project the picture goes into; None chooses one
    as the Navigator Expert driver does (an open project, or a new
    ``ZMART_Experiment<N>``). With a z-stack set (more than one section) the
    picture is taken with the window's Start, which takes every plane, and
    the result's ``picture`` is ``"z_stack"``; else with Capture Image, one
    plane, ``"capture"``. A stack that cannot be read is taken for none.
    """
    try:
        into = project if project is not None else data_container.experiment_for_capture()
        stack = readers.get_z_stack(client)
        if stack:
            drive = stack["mode"]
            axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
            now = readers.read_z_um(client, drive)
            if now is None:
                raise RuntimeError(f"where {drive} stands could not be read, so the z-stack cannot be checked")
            change = writes.z_stack_picture(into, drive, stack["begin"], stack["end"], start={f"from_{axis}": now})
        else:
            change = writes.capture(into)
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"the picture cannot be taken: {why}", logged=True)
    result = picture(client, change, **watching)
    result["picture"] = "z_stack" if stack else "capture"
    return result


#: LCS's own log, where it writes when an autofocus found no focus (``ValidateFocus failed``, simulator, 2026-11-02).
LCS_LOG = LASX_LOG.with_name("LCS.LOG")


def autofocus(client, project=None, **watching) -> dict:
    """Run LAS X's autofocus once, with its own settings, into *project*; report what it did.

    The search runs on the autofocus's drive over its capture range around
    where that drive stands; both ends are checked against the limits first.
    The result's ``found`` is False when LCS wrote "ValidateFocus failed"
    during the run (the focus then stays where it was), else True; with
    ``z_before_um`` and ``z_after_um`` on that drive, and the settings used.
    """
    try:
        used = readers.get_autofocus_settings(client)
        drive = used["drive"]
        before = readers.read_z_um(client, drive)
        if before is None:
            raise RuntimeError(f"where {drive} stands could not be read")
        axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
        half = used["range_um"] / 2
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.start_autofocus(into, drive, before - half, before + half, start={f"from_{axis}": before})
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"the autofocus cannot be started: {why}", logged=True)
    seen = LCS_LOG.stat().st_size if LCS_LOG.is_file() else 0
    result = picture(client, change, **watching)
    if not result["success"]:
        return result
    failed = False
    if LCS_LOG.is_file():
        with LCS_LOG.open("rb") as log:
            log.seek(seen)
            failed = b"ValidateFocus failed" in log.read()
    result.update(found=not failed, drive=drive, z_before_um=before, z_after_um=readers.read_z_um(client, drive), settings=used)
    return result


def set_autofocus_optics(client, laser_nm, percent, detector, band, dye, *, max_retries=None) -> dict:
    """Give the Best Focus Setting the laser line at *laser_nm* with *percent* power, and *detector* with *band* and *dye*; read back.

    As the window's drag-in for the autofocus. The line is found as for
    ``set_laser_power``; the detector by its name among LAS X's; the power
    goes to the limits as a laser power.
    """
    units = {unit.get("Name"): unit for unit in readers.get_detection_units(client) or []}
    if detector not in units:
        return dispatch.refused(f"no detector {detector!r}; LAS X's are {sorted(units)}")
    try:
        route, index = laser_line(client, laser_nm)
        begin, end = band
        change = writes.autofocus_optics(
            route, index, float(laser_nm), percent, writes.route_of(units[detector]["BeamRoute"]), float(begin), float(end), dye,
        )
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the Best Focus Setting's optics cannot be set: {why}")
    return run(client, change, max_retries=max_retries)


def set_autofocus_laser_power(client, laser_nm, percent, *, max_retries=None) -> dict:
    """Change the power of the Best Focus Setting's line at *laser_nm*, as the window does; read back.

    The line must already be in the Best Focus Setting (``set_autofocus_optics``
    brings one in). The power goes to the limits as a laser power.
    """
    lines = readers.get_autofocus_optics(client)["lines"]
    line = next((each for each in lines if abs(each["wavelength"] - float(laser_nm)) < 0.5), None)
    if line is None:
        return dispatch.refused(
            f"the Best Focus Setting has no line at {laser_nm:g} nm; bring one in with set_autofocus_optics; nothing was sent"
        )
    routes = [writes.route_of(source.get("BeamRoute")) for source in readers.get_light_sources(client) or []]
    place = routes.index(writes.route_of(line["beamRoute"]))
    try:
        change = writes.autofocus_laser_power(place, line["lineIndex"], float(laser_nm), percent)
    except ValueError as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


def smartcorr(client, project=None, **watching) -> dict:
    """Run SmartCORR, the window's Start SmartCORR, into *project*; the result's ``collar`` is where LAS X left it.

    *project* as for :func:`capture`. The scan is watched to its end.
    """
    try:
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.smartcorr(into)
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"SmartCORR cannot be started: {why}", logged=True)
    result = picture(client, change, **watching)
    if result["success"]:
        result["collar"] = readers.get_collar(client)
    return result


def start_live(client, project=None) -> dict:
    """Start Live, as the window's Live button, into *project* (as for :func:`capture`); LAS X scans until :func:`stop_live`."""
    try:
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.start_live(into)
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"Live cannot be started: {why}", logged=True)
    return run(client, change)


def stop_live(client, *, max_retries=None) -> dict:
    """Stop Live, as the window's Live button; read back as the scanner idle."""
    return run(client, writes.stop_live(), max_retries=max_retries)


def add_setting(client, copy=False, *, max_retries=None) -> dict:
    """Add a setting to the current job, new or a copy of the current one, as the window's "Add new Setting" does; read back.

    LCS makes the new setting the current one. With more than one setting,
    the changes through a saved setting (``set_detector`` and the like) are
    refused, because which setting is meant cannot be told.
    """
    try:
        change = writes.add_setting(copy, len(readers.get_setting(client)["all"]))
    except ValueError as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


def delete_setting(client, name, *, max_retries=None) -> dict:
    """Delete the setting called *name* from the current job; read back. The last setting is never deleted."""
    listed = readers.get_setting(client)["all"]
    chosen = [s for s in listed if s["name"] == name]
    if not chosen:
        return dispatch.refused(f"no setting {name!r}; the settings are {[s['name'] for s in listed]}")
    if len(listed) == 1:
        return dispatch.refused(f"{name!r} is the job's only setting; it is not deleted")
    return run(client, writes.delete_setting(chosen[0]["id"]), max_retries=max_retries)


def enable_setting(client, on, *, max_retries=None) -> dict:
    """Switch the current setting on or off for scanning, as the window's checkbox does; read back. Off, LAS X takes no picture."""
    try:
        change = writes.setting_enabled(readers.get_setting(client)["id"], on)
    except ValueError as why:
        return dispatch.refused(str(why))
    return run(client, change, max_retries=max_retries)


def lock_detector(client, folder, detector, locked, *, max_retries=None) -> dict:
    """Lock *detector* and its setting's excitation line from LAS X's Dye Assistant, or free them; read back.

    As the window's detector lock: only a detector in use; its excitation
    line is the one the setting excites at, read from a setting saved now
    into *folder*, among the laser lines that are on.
    """
    from . import setting_files

    in_use = {d["name"]: d for d in readers.get_detectors(client) or []}
    if detector not in in_use:
        return dispatch.refused(f"{detector!r} is not in use; the detectors in use are {sorted(in_use)}")
    wavelength = setting_files.saved_now(client, folder)["excitation_nm"]
    line = next(
        (each for each in readers.get_laser_lines(client) or [] if wavelength is not None and abs(each["wavelength"] - wavelength) < 0.5),
        None,
    )
    if line is None:
        return dispatch.refused(f"the setting's excitation line ({wavelength} nm) is not among the laser lines that are on")
    change = writes.dye_assistant_lock(
        writes.route_of(in_use[detector]["beamRoute"]), writes.route_of(line["beamRoute"]), line["lineIndex"], locked
    )
    return run(client, change, max_retries=max_retries)


def set_collar_reference(client) -> dict:
    """Store where the collar stands as the reference of the objective in the light path, as the window's Set does; read back."""
    return run(client, writes.collar_reference(readers.get_collar(client)["percent"]))


def reset_collar_reference(client) -> dict:
    """Clear the collar's reference of the objective in the light path, as the window's Reset does; read back."""
    return run(client, writes.collar_reference(None))


def collar_to_reference(client) -> dict:
    """Turn the collar to the position stored for the objective in the light path, as the window's GoTo does; read back.

    Refused, with nothing sent, when no position is stored (the window's
    Set stores one).
    """
    reference = readers.get_collar(client)["reference"]
    if reference is None:
        return dispatch.refused("the objective has no stored reference position for the collar; nothing was sent")
    return change(client, "collar", reference)


def set_z_stack(client, drive, begin_um, end_um, step_um, *, max_retries=None) -> dict:
    """Set LAS X's z-stack: its drive, its begin and end, its step, in that order; the result carries ``z_stack`` read back.

    Each is its own change through the limits and its own read-back; the
    first that is refused or fails ends it, and is the result. Each end is
    checked like a z move from where the drive stands.
    """
    axis = {"z-galvo": "z_galvo_um", "z-wide": "z_wide_um"}.get(drive)
    if axis is None:
        return dispatch.refused(f"no z drive {drive!r}; the drives are z-galvo and z-wide")
    now = readers.read_z_um(client, drive)
    if now is None:
        return dispatch.refused(f"where {drive} stands could not be read, so the z-stack cannot be checked")
    try:
        steps = (
            writes.z_stack_drive(drive),
            writes.z_stack_range(drive, float(begin_um), float(end_um), start={f"from_{axis}": now}),
            writes.z_stack_step(float(step_um)),
        )
    except (TypeError, ValueError) as why:
        return dispatch.refused(f"the z-stack cannot be set: {why}")
    confirmed = True
    for change in steps:
        result = run(client, change, max_retries=max_retries)
        if not result["success"]:
            return result
        confirmed = confirmed and result.get("confirmed") is True
    result["confirmed"] = confirmed
    result["z_stack"] = readers.get_z_stack(client)
    return result


def the_stack(client):
    """``(stack, drive, z_um, start)``: the z-stack, its drive, where that drive stands, and that as the limits take it."""
    stack = readers.get_z_stack(client)
    if not stack:
        raise RuntimeError("LAS X has no z-stack set")
    drive = stack["mode"]
    here = readers.read_z_um(client, drive)
    if here is None:
        raise RuntimeError(f"where {drive} stands could not be read")
    axis = "z_galvo_um" if drive == "z-galvo" else "z_wide_um"
    return stack, drive, here, {f"from_{axis}": here}


def around(stack, middle, size):
    """``(begin, end)`` of a stack of *size* around *middle*, running the way *stack* runs."""
    half = size / 2
    return (middle + half, middle - half) if stack["begin"] > stack["end"] else (middle - half, middle + half)


def stack_result(client, change, max_retries=None):
    result = run(client, change, max_retries=max_retries)
    if result["success"]:
        result["z_stack"] = readers.get_z_stack(client)
    return result


def set_z_stack_size(client, size_um, *, max_retries=None) -> dict:
    """Set the z-stack's size around its middle, as the window's Z Size; its new ends checked against the limits first."""
    try:
        stack, drive, _, start = the_stack(client)
        begin, end = around(stack, (stack["begin"] + stack["end"]) / 2, float(size_um))
        change = writes.z_stack_size(drive, float(size_um), begin, end, start=start)
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the z-stack's size cannot be set: {why}")
    return stack_result(client, change, max_retries)


def set_z_stack_here(client, which, *, max_retries=None) -> dict:
    """Set the z-stack's begin or end where its drive stands, as the window's Begin and End buttons."""
    try:
        stack, drive, here, start = the_stack(client)
        begin, end = (here, stack["end"]) if which == "begin" else (stack["begin"], here)
        change = writes.z_stack_here(which, drive, here, begin, end, start=start)
    except (RuntimeError, ValueError) as why:
        return dispatch.refused(f"the z-stack's {which} cannot be set: {why}")
    return stack_result(client, change, max_retries)


def recenter_z_stack(client, *, max_retries=None) -> dict:
    """Move the z-stack, its size kept, around where its drive stands, as the window's Re-Center; its new ends checked first."""
    try:
        stack, drive, here, start = the_stack(client)
        begin, end = around(stack, here, abs(stack["end"] - stack["begin"]))
        change = writes.z_stack_recenter(drive, begin, end, start=start)
    except RuntimeError as why:
        return dispatch.refused(f"the z-stack cannot be re-centred: {why}")
    return stack_result(client, change, max_retries)


#: The places of the z-stack panel's Move to buttons.
STACK_PLACES = ("begin", "end", "center", "focus")


def move_to_z_stack(client, place, *, max_retries=None) -> dict:
    """Move the stack's drive to its begin, end or center, or to the focus plane, as the window's Move to buttons.

    The buttons send no command LCS logs (simulator, 2026-11-02), so the drive
    moves as any z move does, checked against the z limits. ``"focus"`` is the
    plane set with the window's Set Focus, which only the window sets.
    """
    if place not in STACK_PLACES:
        return dispatch.refused(f"the z-stack panel moves to {', '.join(STACK_PLACES)}, not {place!r}")
    if place == "focus":
        focus = readers.get_z_stack_focus(client)
        if not focus or not focus["set"]:
            return dispatch.refused("no focus plane is set; set it with Set Focus in LAS X's z-stack panel")
        return move_z(client, readers.get_z_drive_in_use(client), focus["z_um"], max_retries=max_retries)
    try:
        stack, drive, _, _ = the_stack(client)
    except RuntimeError as why:
        return dispatch.refused(f"the focus cannot move to the z-stack's {place}: {why}")
    target = {"begin": stack["begin"], "end": stack["end"], "center": (stack["begin"] + stack["end"]) / 2}[place]
    return move_z(client, drive, target, max_retries=max_retries)


def clear_z_stack(client, *, max_retries=None) -> dict:
    """Leave no z-stack, as the window's bin: its begin and end both set where its drive stands."""
    try:
        _, drive, here, start = the_stack(client)
    except RuntimeError as why:
        return dispatch.refused(f"the z-stack cannot be cleared: {why}")
    return stack_result(client, writes.z_stack_clear(drive, here, start=start), max_retries)


def set_z_stack_sections(client, sections, *, max_retries=None) -> dict:
    """Set the z-stack's number of sections, by the step that gives it over the stack's size (``SetStackStepSize``).

    The window's Number of Steps has no command, and the count written into
    LCS's state tree was ignored (simulator, 2026-11-02); the step does it.
    ``confirmed`` when the stack has that many sections.
    """
    if isinstance(sections, bool) or not isinstance(sections, int) or sections < 2:
        return dispatch.refused(f"a z-stack has 2 or more sections, not {sections!r}")
    try:
        stack, _, _, _ = the_stack(client)
    except RuntimeError as why:
        return dispatch.refused(f"the z-stack's sections cannot be set: {why}")
    result = stack_result(client, writes.z_stack_step(abs(stack["end"] - stack["begin"]) / (sections - 1)), max_retries)
    if result["success"]:
        result["confirmed"] = bool(result.get("confirmed")) and result["z_stack"]["sections"] == sections
    return result


def tile_z_um(client, positions_um) -> float | None:
    """The z every tile is sent with, or None to send each position's own z-galvo.

    LCS reads a tile's ``ZPos`` as the position of the drive LAS X's z-stack
    uses (``Acquire/ZPositionMode``): with it on z-wide, a tile scan sent
    with z-galvo's positions drove z-wide from 1522.7 µm to 0 (simulator,
    2026-11-01). So with the z-stack on z-wide the tiles carry z-wide's
    position now, and the focus stays where it is. Raises RuntimeError,
    before anything is sent, when the drive cannot be read, or when a
    position asks for another z-galvo than the current one while the
    z-stack is on z-wide, which a tile cannot carry.
    """
    drive = readers.get_z_drive_in_use(client)
    if drive is None:
        raise RuntimeError("the drive LAS X's z-stack uses could not be read, so the tiles' z is not known")
    if drive == "z-galvo":
        return None
    galvo = readers.read_z_um(client, "z-galvo")
    wide = readers.read_z_um(client, "z-wide")
    if galvo is None or wide is None:
        raise RuntimeError("the z drives could not be read, so the tiles' z is not known")
    asked = [float(position[2]) for position in positions_um]
    if any(abs(z - galvo) > writes.Z_TOLERANCE_UM for z in asked):
        raise RuntimeError(
            f"LAS X's z-stack is on z-wide, so every tile is taken at z-wide's position; "
            f"a tile at z-galvo {asked} other than where z-galvo stands ({galvo:.2f} um) cannot be sent"
        )
    return wide


def tile_scan(client, positions_um, project=None, **watching) -> dict:
    """Take a tile scan over *positions_um* (x, y, z-galvo per field, micrometres); see :func:`picture`.

    The tiles carry the z LCS reads them on (:func:`tile_z_um`), noted in
    the result as ``tile_z_um``.
    """
    try:
        tile_z = tile_z_um(client, positions_um)
        here, galvo = readers.get_xy(client), readers.read_z_um(client, "z-galvo")
        if not here or galvo is None:
            raise RuntimeError("where the stage and z-galvo stand could not be read, so the steps cannot be checked")
        into = project if project is not None else data_container.experiment_for_capture()
        change = writes.tile_scan(
            positions_um, into, tile_z_um=tile_z,
            start={"from_x_um": here["x_um"], "from_y_um": here["y_um"], "from_z_galvo_um": galvo},
        )
    except (RuntimeError, ValueError, TypeError) as why:
        return dispatch.refused(f"the tile scan cannot be made: {why}", logged=True)
    result = picture(client, change, **watching)
    result["tile_z_um"] = tile_z
    return result


def after_tile_scan(client, x_um, y_um, z_um, *, max_retries=None, tile_z_um=None, last=None) -> dict:
    """Put back what a tile scan changed: the stage to (*x_um*, *y_um*, *z_um*), scan mode xyz, autofocus on.

    *tile_z_um* is the z the tile scan sent its tiles with, sent again here;
    *last* is the last field ``(x, y, z-galvo)``, where the stage stands, for the step.
    """
    start = None if last is None else {"from_x_um": last[0], "from_y_um": last[1], "from_z_galvo_um": last[2]}
    return run(
        client,
        writes.after_tile_scan(float(x_um), float(y_um), float(z_um), tile_z_um=tile_z_um, start=start),
        max_retries=max_retries,
    )


def take_picture(client: Any, project=None, **watching) -> AcquisitionResult:
    """Take a capture and answer when it ran; RuntimeError when it was not sent or its scan never started."""
    started_at = time.time()
    result = capture(client, project, **watching)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(f"the capture failed: {result.get('message') if result else result}")
    return AcquisitionResult(
        picture=result.get("picture", "capture"), started_at=started_at, finished_at=finished_at, command_result=result
    )


def take_tile_scan(client: Any, positions_um, project=None, **watching) -> AcquisitionResult:
    """Take a tile scan and answer when it ran, then put back what it changed; RuntimeError when it failed.

    The put-back (``after_tile_scan``) returns the stage to the first
    position; a put-back that could not be confirmed is noted in the
    result's ``command_result["put_back"]``, and the caller decides. It is
    sent whenever the tile scan was sent, also when its scan never started:
    LAS X may have taken the preparation (the scan mode xyt, say) before it
    refused to start.
    """
    positions = [tuple(float(v) for v in p) for p in positions_um]
    started_at = time.time()
    result = tile_scan(client, positions, project, **watching)
    finished_at = time.time()
    if result.get("timing", {}).get("attempts", 0) > 0:
        result["put_back"] = after_tile_scan(client, *positions[0], tile_z_um=result.get("tile_z_um"), last=positions[-1])
    if not result.get("success"):
        put_back = result.get("put_back")
        undone = "" if put_back is None else f"; put back: {put_back.get('message')}"
        raise RuntimeError(f"the tile scan failed: {result.get('message')}{undone}")
    return AcquisitionResult(picture="tile_scan", started_at=started_at, finished_at=finished_at, command_result=result)


def confirm_scan(client, *, start_timeout=15.0, heartbeat_interval=30.0, timeout=None, poll_interval=0.1):
    """Watch the scan status until the picture has been taken; reads only, never sends.

    First the scan must be seen running within *start_timeout*. Then
    ``IDLE_STREAK_REQUIRED`` idle reads in a row show it has finished. Only
    readings taken after the watch began count. A scan never seen running
    within *start_timeout* did not start, and the picture fails.

    Returns ``{"success": bool, "started": bool, "logs": [...]}``.
    """
    logs = []
    observed_after = time.time()
    t_start = time.perf_counter()
    last_heartbeat = t_start
    saw_scanning = False
    consecutive_idle = 0
    start_deadline = t_start + start_timeout
    deadline = t_start + (timeout if timeout is not None else 1e9)

    def watched(success):
        return {"success": success, "started": saw_scanning, "logs": logs}

    while time.perf_counter() < deadline:
        reading = readers.get_scan_status(client, diagnostics=True, deadline=time.monotonic() + poll_interval)
        fresh = reading is not None and reading.observed_at is not None and reading.observed_at > observed_after
        status = reading.value if fresh else None
        elapsed = time.perf_counter() - t_start

        if status is None or status == "Unknown":
            # A failed or stale read is evidence of neither scanning nor idle.
            consecutive_idle = 0
        elif "Idle" not in status:
            saw_scanning = True
            consecutive_idle = 0
        else:
            consecutive_idle += 1

        if not saw_scanning and time.perf_counter() > start_deadline:
            msg = (
                f"the scan was never seen running within {start_timeout:.0f}s "
                f"({elapsed:.0f}s in all): either it did not start, or it finished between two looks"
            )
            log.warning(msg)
            logs.append(make_log_entry("warning", msg))
            return watched(False)

        now = time.perf_counter()
        if now - last_heartbeat > heartbeat_interval:
            msg = f"Scanning: {status or 'Unknown'}, {elapsed:.0f}s elapsed"
            log.info(msg)
            logs.append(make_log_entry("info", msg))
            last_heartbeat = now

        if consecutive_idle >= IDLE_STREAK_REQUIRED and saw_scanning:
            return watched(True)

        time.sleep(poll_interval)

    msg = f"the scan was not seen to finish within {time.perf_counter() - t_start:.1f}s"
    log.warning(msg)
    logs.append(make_log_entry("warning", msg))
    return watched(False)
