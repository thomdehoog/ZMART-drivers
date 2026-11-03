"""The direct reader: LAS X's live state, read from its hardware server.

This is the plain Navigator driver's one reader. It reads the hardware
server's state tree (:mod:`state_tree`), which holds what LAS X is set to
and doing now: the stage, both z drives, the scan status and mode, zoom,
scan speed, format, averaging, pinhole, objective, field of view, z-stack,
laser lines, scan field rotation, sequential mode, pixel size, bit depth,
detectors, filter wheels, and what the instrument is (serial number, stand,
objectives, scan speed range, stage limits, detectors, light sources, AFC).
:data:`READS` names them all; the reader reads nothing else, and never
writes.

Measured on the simulator, 2026-10-13: the stage in 1.1 ms, a new stage
position 3.3-4.2 ms after a move, both z drives in 1.1 ms. With LAS X
showing its plain Navigator every one of these answered on the STELLARIS
(2026-10-15, ``navigator_ci.py``); the job list is empty there, because
jobs belong to Navigator Expert, and :func:`get_jobs` is kept so a session
can tell which Navigator LAS X shows.

Every function raises :class:`HardwareServerUnavailable` when the hardware
server cannot be reached or does not answer, rather than returning anything
that is not a fresh reading.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import math

from .client import HardwareServerUnavailable, connected_server, forget_connection
from .state_tree import (
    SCAN_STATES,
    STAGE_PATHS,
    Z_USE_MODE,
    child_named,
    children,
    job_sequence,
    layout,
    named,
    node_at,
    node_value,
    of_name,
    read,
    remembered,
    z_drive_node,
)

#: What the direct reader reads.
READS = (
    "xy",
    "z",
    "selected_job",
    "jobs",
    "scan_status",
    "scan_mode",
    "zoom",
    "scan_speed",
    "format",
    "averaging",
    "pinhole",
    "objective",
    "fov",
    "z_stack",
    "z_drive_in_use",
    "z_stack_focus",
    "autofocus",
    "light_sources",
    "filter_wheels",
    "detectors",
    "channel_colours",
    "detection_units",
    "optimal_format",
    "bidirectional",
    "phase_x",
    "system_optimized_step",
    "pane_percentage",
    "collar",
    "smartcorr",
    "setting",
    "sequential_mode",
    "laser_lines",
    "scan_field_rotation",
    "laser_line_locked",
    "laser_slots",
    "autofocus_settings",
    "autofocus_scan",
    "detector_modes",
    "aobs_lines",
    "fast_live",
    "time_series",
    "line_scan",
    "roi_scan",
    "z_compensation",
    "autofocus_in_time_series",
    "auto_sync",
    "lambda_scan",
    "excitation_scan",
    "laser_output_power",
    "coverslip_offset",
    "fluorifier",
    "mfp_auto_select",
    "autofocus_optics",
    "pixel_size",
    "bit_depth",
    "serial_number",
    "microscope_stand",
    "objectives",
    "scan_speed_range",
    "stage_limits",
    "detectors",
    "detection_units",
    "filter_wheels",
    "light_sources",
    "base_fov",
    "afc_installed",
    "active_settings",
    "hardware_info",
)

SCANNING = ("Confocal", "ScanningParameter")
LIGHT_SOURCES = ("Confocal", "LightSourceList")
HARDWARE = ("Confocal", "HWParameter")
Z_STACK = ("Acquire", "AcquireZStackInfo")
IMAGE_SIZE = ("Acquire", "AcquireImageSizeInfo")


def settings(what, paths):
    """The values of the nodes at *paths* (a dict of name -> path), read in one go."""

    def reading(nodes):
        return {
            name: node_value(nodes, node_at(nodes, path))
            for name, path in paths.items()
        }

    return read(what, reading)


def session_facts(what, paths):
    """Like :func:`settings`, for values LAS X fixes for a session; read once, then remembered.

    Forgotten with the remembered node ids (``state_tree.forget_nodes``),
    which a failed read does, as after a LAS X restart.
    """
    key = ("facts", what)
    if key not in remembered:
        remembered[key] = settings(what, paths)
    return dict(remembered[key])


def metres(value, what):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise HardwareServerUnavailable(f"the state tree answered {what} = {value!r}")
    return float(value)


def get_xy() -> dict:
    """The stage position, in metres and micrometres."""

    def reading(nodes):
        x = metres(
            node_value(nodes, node_at(nodes, STAGE_PATHS["x"])), "Stage/PositionX"
        )
        y = metres(
            node_value(nodes, node_at(nodes, STAGE_PATHS["y"])), "Stage/PositionY"
        )
        return {"x": x, "y": y, "x_um": x * 1e6, "y_um": y * 1e6}

    return read("the stage position", reading)


def get_jobs() -> list[dict]:
    """Every job, in the sequence's order, as the Navigator Expert driver lists them.

    Per job ``Name``, ``ID`` (its live block id, which an experiment load
    changes while the CAM API's job list keeps the old one), ``IsSelected``
    and ``IsAutofocus``: whether it is Navigator Expert's autofocus job, from
    the job's own ``IsAutoFocusOnStart``, the field the log reader reads in
    the matrix summary too. It is read beside the job's name, so it does not
    depend on the block ids. On the STELLARIS (2026-10-15) it was set for the
    AF job alone, as the CAM API's ``IsAutofocus`` said; on the simulator
    (2026-10-13) it had been unset for the AF job too. Not the API's
    ``ScanMode`` per job, which the driver does not use from this list.
    """
    sequence = job_sequence()
    ordered = sorted(sequence.jobs.items(), key=lambda item: item[1].element_id)
    return [
        {
            "Name": name,
            "ID": job.block_id,
            "IsSelected": job.element_id == sequence.selected_element,
            "IsAutofocus": job.autofocus,
        }
        for name, job in ordered
    ]


def get_job_by_name(job_name) -> dict | None:
    """One job of :func:`get_jobs` by its name; None when LAS X has no such job."""
    return next((job for job in get_jobs() if job["Name"] == job_name), None)


def get_selected_job() -> dict | None:
    """The selected job, as the Navigator Expert driver gives it.

    ``Name``, ``ID`` (the live block id) and ``IsSelected``; None when LAS X
    has no job selected.
    """
    return next((job for job in get_jobs() if job["IsSelected"]), None)


def read_z_um(drive: str) -> float:
    """Where one z drive truly is, in micrometres; *drive* is ``"z-galvo"`` or ``"z-wide"``.

    The drive's ``CurrentHardwarePosition``, in the same frame as the job
    settings' ``zPosition``, and current also for the drive that carries a
    job's z-stack, whose job settings stop refreshing.
    """
    if drive not in Z_USE_MODE:
        raise ValueError(
            f"no z drive {drive!r}; the direct reader knows {sorted(Z_USE_MODE)}"
        )

    def reading(nodes):
        return metres(node_value(nodes, z_drive_node(nodes, drive)), drive) * 1e6

    return read(f"where {drive} is", reading)


def read_zwide_um() -> float:
    """Where z-wide is, in micrometres; kept under the name the other Leica driver uses."""
    return read_z_um("z-wide")


def get_scan_status() -> str:
    """The scan status, named as LAS X names it (``eScanIdle`` ...).

    ``Acquire/AcquisitionState``; a state the reader does not know is
    ``Unknown``, which the named readings count as no answer.
    """
    state = settings("the scan status", {"state": ("Acquire", "AcquisitionState")})[
        "state"
    ]
    return (
        SCAN_STATES[state]
        if isinstance(state, int) and 0 <= state < len(SCAN_STATES)
        else "Unknown"
    )


def get_scan_mode() -> str:
    """The scan mode (``xyz``, ``xyt`` ...), as the job settings' ``scanMode``."""
    return settings(
        "the scan mode", {"mode": ("Acquire", "ScanDimensionList", "ScanModeName")}
    )["mode"]


def get_zoom() -> dict:
    """The zoom with its range, as the job settings' ``zoom``: ``current``, ``min``, ``max``."""
    return settings(
        "the zoom",
        {
            "current": (*SCANNING, "Zoom"),
            "min": (*SCANNING, "ZoomMin"),
            "max": (*SCANNING, "ZoomMax"),
        },
    )


def get_scan_speed() -> dict:
    """The scan speed, as the job settings' ``scanSpeed``: ``value``, ``unit``, ``isResonant``."""
    return settings(
        "the scan speed",
        {
            "value": (*SCANNING, "Speed"),
            "unit": (*SCANNING, "SpeedUnit"),
            "isResonant": (*SCANNING, "IsResonantConfocalScanner"),
        },
    )


def get_format() -> str:
    """The image format in pixels, as the job settings' ``format`` (``"512 x 512"``)."""
    return settings("the format", {"format": (*SCANNING, "CurrentFormat")})["format"]


def get_averaging() -> dict:
    """Line and frame averaging and accumulation, named as the job settings' active setting names them."""
    return settings(
        "the averaging",
        {
            "lineAverage": (*SCANNING, "LineAverage"),
            "lineAccumulation": (*SCANNING, "LineAccu"),
            "frameAverage": (*SCANNING, "FrameAverage"),
            "frameAccumulation": (*SCANNING, "FrameAccumulation"),
        },
    )


#: How LCS names the unit LAS X shows the pinhole in (``PinholeUnit``).
PINHOLE_UNITS = {1: "um", 2: "airy"}


def get_pinhole() -> dict:
    """The pinhole: ``airy`` (as the job settings' ``pinholeAiry``: ``value``, ``min``, ``max``), ``metres``,
    ``unit`` (``"um"`` or ``"airy"``, as LAS X shows it) and ``emission_nm``, the wavelength the Airy unit is worked out for.
    """
    found = settings(
        "the pinhole",
        {
            "value": (*HARDWARE, "PinholeAU"),
            "min": (*HARDWARE, "PinholeAUMin"),
            "max": (*HARDWARE, "PinholeAUMax"),
            "metres": (*HARDWARE, "Pinhole"),
            "unit": (*HARDWARE, "PinholeUnit"),
            "emission_nm": (*HARDWARE, "EmissionWaveLengthForAiryAnddzCalculation"),
        },
    )
    metres_open = metres(found.pop("metres"), "Pinhole")
    unit = found.pop("unit")
    if unit not in PINHOLE_UNITS:
        raise HardwareServerUnavailable(f"the state tree answered PinholeUnit = {unit!r}")
    emission_nm = metres(found.pop("emission_nm"), "EmissionWaveLengthForAiryAnddzCalculation")
    return {"airy": found, "metres": metres_open, "unit": PINHOLE_UNITS[unit], "emission_nm": emission_nm}


def get_phase_x() -> float:
    """Phase X, which lines up the two directions of a bidirectional scan, as LAS X shows it."""
    return metres(settings("phase X", {"x": (*HARDWARE, "PhaseX")})["x"], "PhaseX")


def get_objective() -> dict:
    """The objective in the light path, as the job settings' ``objective``.

    ``Microscope/SlotPosition`` is its slot, counted from 0 like the job
    settings' ``slotIndex``; ``Microscope/ObjectiveList`` holds one
    ``ObjectiveInfo`` per slot, in slot order, read once (:func:`objective_layout`).
    ``motCorrPos`` is the correction collar's position now. Raises
    :class:`HardwareServerUnavailable` for a slot the list has no objective for.
    """

    def reading(nodes):
        slot = node_value(nodes, node_at(nodes, ("Microscope", "SlotPosition")))
        turret = layout(nodes, OBJECTIVES, objective_layout)
        if not isinstance(slot, int) or not 0 <= slot < len(turret):
            raise HardwareServerUnavailable(
                f"the state tree has no objective in slot {slot} ({len(turret)} listed)"
            )
        return turret[slot] | {
            "slotIndex": slot,
            "motCorrPos": node_value(
                nodes, node_at(nodes, ("Microscope", "CurrentMotCorrPos"))
            ),
        }

    return read("the objective", reading)


OBJECTIVES = ("Microscope", "ObjectiveList")

#: The job settings' ``objective`` keys, and the ``ObjectiveInfo`` node each is read from.
OBJECTIVE_FIELDS = {
    "name": "Name",
    "magnification": "Magnification",
    "numericalAperture": "NumericalAperture",
    "immersion": "Immersion",
    "objectiveNumber": "OrderNumber",
    "isMotCorr": "IsMotCorrApplicable",
}


def get_fov() -> tuple[float, float]:
    """The scan field's width and height in metres, as LAS X reports them."""
    size = settings(
        "the field of view",
        {"x": (*IMAGE_SIZE, "ImageSizeX"), "y": (*IMAGE_SIZE, "ImageSizeY")},
    )
    return metres(size["x"], "ImageSizeX"), metres(size["y"], "ImageSizeY")


def get_z_stack() -> dict | None:
    """The z-stack, as the job settings' raw ``stack`` in micrometres; None without one (one section).

    ``begin``, ``end``, ``stepSize``, ``size``, ``sections`` and ``mode``,
    the drive that carries it (``Acquire/ZPositionMode``, coded like the
    drives' ``ZUseMode``: 1 z-galvo, 2 z-wide). On the simulator
    (2026-10-30) ``ZPositionMode`` followed the drive chosen in LAS X's
    z-stack panel, while ``Acquire/ZStackUseMode`` stayed 1 for either drive.
    """
    found = settings(
        "the z-stack",
        {
            "begin": (*Z_STACK, "BeginValue"),
            "end": (*Z_STACK, "EndValue"),
            "stepSize": (*Z_STACK, "StepSize"),
            "sections": (*Z_STACK, "SectionsCount"),
            "mode": ("Acquire", "ZPositionMode"),
        },
    )
    if not isinstance(found["sections"], int) or found["sections"] <= 1:
        return None
    drives = {code: drive for drive, code in Z_USE_MODE.items()}
    if found["mode"] not in drives:
        raise HardwareServerUnavailable(
            f"the state tree names z-stack drive {found['mode']!r}"
        )
    begin = metres(found["begin"], "BeginValue") * 1e6
    end = metres(found["end"], "EndValue") * 1e6
    return {
        "begin": begin,
        "end": end,
        "stepSize": metres(found["stepSize"], "StepSize") * 1e6,
        "size": abs(end - begin),
        "sections": found["sections"],
        "mode": drives[found["mode"]],
    }


def get_z_stack_focus() -> dict:
    """The z-stack's focus plane, set with the window's Set Focus: ``z_um`` and whether it is ``set``.

    ``Acquire/AcquireZStackInfo/AFPlanePosition`` and ``IsAFPlanePosition``
    (simulator, 2026-11-02). Only the window sets it: written into the state
    tree, LCS ignored it.
    """
    found = settings("the z-stack's focus plane", {"at": (*Z_STACK, "AFPlanePosition"), "set": (*Z_STACK, "IsAFPlanePosition")})
    return {"z_um": metres(found["at"], "AFPlanePosition") * 1e6, "set": bool(found["set"])}


def get_z_drive_in_use() -> str:
    """The z drive LAS X's z-stack panel uses, ``"z-galvo"`` or ``"z-wide"`` (``Acquire/ZPositionMode``).

    It matters also without a stack: LCS reads a tile's ``ZPos`` as the
    position of this drive (simulator, 2026-11-01). Raises
    :class:`HardwareServerUnavailable` for a code it does not know.
    """
    mode = settings("the z-stack's drive", {"mode": ("Acquire", "ZPositionMode")})["mode"]
    drives = {code: drive for drive, code in Z_USE_MODE.items()}
    if mode not in drives:
        raise HardwareServerUnavailable(f"the state tree names z-stack drive {mode!r}")
    return drives[mode]


def get_autofocus() -> bool:
    """Whether autofocus is switched on, as LAS X's Autofocus button shows it.

    ``Acquire/AutoFocusInfo/IsSeriesScanAutofocusActive``, which followed
    ``ActivateAutoFocus`` on the simulator (2026-11-01).
    """
    value = settings("autofocus", {"on": ("Acquire", "AutoFocusInfo", "IsSeriesScanAutofocusActive")})["on"]
    if not isinstance(value, bool):
        raise HardwareServerUnavailable(f"the state tree answered autofocus = {value!r}")
    return value


def get_channel_colours() -> dict[int, str]:
    """Each channel's colour (LAS X's LUT name), by channel: ``{3: "Gray", 4: "Yellow", ...}``.

    ``Acquire/LUT/LUT_Entry``: a detector of the spectral detection on route
    ``40;n`` is channel *n* (simulator, 2026-11-01).
    """

    def reading(nodes):
        found = {}
        for entry in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "LUT"))), "LUT_Entry"):
            channel = node_value(nodes, named(nodes, entry, "Channel"))
            name = node_value(nodes, named(nodes, entry, "LUTName"))
            if isinstance(channel, int) and isinstance(name, str):
                found[channel] = name
        return found

    return read("the channel colours", reading)


def get_bidirectional() -> bool:
    """Whether the scan is bidirectional: ``Confocal/ScanningParameter/ScanDirectionX`` 2 (1 is unidirectional)."""
    value = settings("the scan direction", {"x": ("Confocal", "ScanningParameter", "ScanDirectionX")})["x"]
    if value not in (1, 2):
        raise HardwareServerUnavailable(f"the state tree answered ScanDirectionX = {value!r}")
    return value == 2


def get_optimal_format() -> tuple[int, int] | None:
    """The image format LAS X computes as optimal for the current zoom and objective, ``(x, y)``; None when it has none.

    What LAS X's "Optimize XY Format" button sets: ``OptimizedFormatX`` and
    ``OptimizedFormatY`` of the current block, valid while
    ``IsOptimizeFormatValid`` (simulator, 2026-11-01: 2824 x 2824).
    """
    block = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo")
    found = settings(
        "the optimal format",
        {"x": (*block, "OptimizedFormatX"), "y": (*block, "OptimizedFormatY"), "valid": (*block, "IsOptimizeFormatValid")},
    )
    if found["valid"] is not True or not all(isinstance(found[k], int) and found[k] > 0 for k in ("x", "y")):
        return None
    return found["x"], found["y"]


def light_layout(nodes, node, kids):
    """Per light source: what is fixed for a session, read once, and the nodes of what can change.

    ``{"name", "route", "lasers": [(name, min, max wavelength)], "shutter":
    node, "lines": [(wavelength node, index, intensity node)]}``. A tunable
    laser's line wavelength can change, so it stays a node.
    """
    sources = []
    for source in of_name(nodes, kids, "LightSource"):
        sources.append(
            {
                "name": node_value(nodes, named(nodes, source, "LightSourceName")),
                "route": beam_route(
                    node_value(nodes, named(nodes, source, "BeamRoute"))
                ),
                "lasers": [
                    (
                        node_value(nodes, named(nodes, laser, "LaserName")),
                        node_value(nodes, named(nodes, laser, "WavelengthMin")),
                        node_value(nodes, named(nodes, laser, "WavelengthMax")),
                    )
                    for laser in of_name(
                        nodes,
                        children(nodes, named(nodes, source, "LaserList")),
                        "Laser",
                    )
                ],
                "shutter": named(
                    nodes, named(nodes, source, "Shutter"), "IsShutterActive"
                ),
                "lines": [
                    (
                        named(nodes, line, "WaveLength"),
                        node_value(nodes, named(nodes, line, "LineIndex")),
                        named(nodes, line, "Intensity"),
                    )
                    for line in of_name(
                        nodes, children(nodes, named(nodes, source, "AOTF")), "AotfLine"
                    )
                ],
            }
        )
    return sources


def beam_route(value):
    """LAS X's beam route as the CAM API and the log write it: ``(10, 30)`` is ``"10;30"``."""
    return (
        ";".join(str(part) for part in value)
        if isinstance(value, (tuple, list))
        else str(value)
    )


def laser_of_line(lasers, index):
    """The laser an AOTF line belongs to: one laser per line, or one tunable laser for all its lines."""
    return lasers[index] if len(lasers) > 1 and 0 <= index < len(lasers) else lasers[0]


def get_laser_lines() -> list[dict]:
    """The laser lines that are on, as the job settings' ``activeLaserLines`` give them.

    A line is on when its AOTF intensity is above zero (on the simulator,
    2026-10-13, exactly the log's active lines: 405 nm at 1.96 % and 497 nm
    at 2.0 %). ``intensity`` is a fraction of full power, as the log's;
    ``lightSource`` is the source's name (``UV``, ``SuperContVisible
    Light``) and ``laserName`` its laser's (``Laser 405``, ``WLL``), which
    the log writes as ``Laser (WLL)``.
    """

    def reading(nodes):
        lines = []
        for source in layout(nodes, LIGHT_SOURCES, light_layout):
            for wavelength, line_index, intensity in source["lines"]:
                percent = node_value(nodes, intensity)
                if isinstance(percent, (int, float)) and percent > 0:
                    lines.append(
                        {
                            "lightSource": source["name"],
                            "laserName": laser_of_line(source["lasers"], line_index)[0],
                            "beamRoute": source["route"],
                            "lineIndex": line_index,
                            "wavelength": node_value(nodes, wavelength),
                            "intensity": percent / 100.0,
                            "shutterOpen": bool(node_value(nodes, source["shutter"])),
                        }
                    )
        return lines

    return read("the laser lines", reading)


def get_scan_field_rotation() -> dict:
    """The scan field's rotation in degrees, as the job settings' ``scanFieldRotation``: ``value``, ``min``, ``max``."""
    return settings(
        "the scan field rotation",
        {
            "value": (*HARDWARE, "ScanRotationAngle"),
            "min": (*HARDWARE, "ScanRotationAngleMin"),
            "max": (*HARDWARE, "ScanRotationAngleMax"),
        },
    )


def get_pixel_size() -> tuple[float, float]:
    """A pixel's width and height in metres: the field of view over the format, as the job settings' ``pixelSize``."""
    found = settings(
        "the pixel size",
        {
            "x": (*IMAGE_SIZE, "ImageSizeX"),
            "y": (*IMAGE_SIZE, "ImageSizeY"),
            "columns": (*SCANNING, "FormatX"),
            "rows": (*SCANNING, "FormatY"),
        },
    )
    if not all(isinstance(found[k], int) and found[k] > 0 for k in ("columns", "rows")):
        raise HardwareServerUnavailable(
            f"the state tree answered format {found['columns']} x {found['rows']}"
        )
    return (
        metres(found["x"], "ImageSizeX") / found["columns"],
        metres(found["y"], "ImageSizeY") / found["rows"],
    )


def get_system_optimized_step() -> bool:
    """Whether the z-stack's step is System Optimized: LCS's ``UseSystemOptimizedCalculation``."""
    value = settings("the step mode", {"on": (*Z_STACK, "UseSystemOptimizedCalculation")})["on"]
    if not isinstance(value, bool):
        raise HardwareServerUnavailable(f"the state tree answered UseSystemOptimizedCalculation = {value!r}")
    return value


def get_collar() -> dict:
    """The objective's correction collar (motCORR): ``percent`` where it stands, ``motorised`` whether LAS X can turn it,
    and ``reference``, the position the window's Set stored for the objective in the light path (None when none is).
    """

    def reading(nodes):
        slot = node_value(nodes, node_at(nodes, ("Microscope", "SlotPosition")))
        infos = of_name(nodes, children(nodes, node_at(nodes, OBJECTIVES)), "ObjectiveInfo")
        if not isinstance(slot, int) or not 0 <= slot < len(infos):
            raise HardwareServerUnavailable(f"the state tree has no objective in slot {slot} ({len(infos)} listed)")
        reference = node_value(nodes, named(nodes, infos[slot], "MotCorrRefPos"))
        return {
            "percent": metres(node_value(nodes, node_at(nodes, ("Microscope", "CurrentMotCorrPos"))), "CurrentMotCorrPos"),
            "motorised": bool(node_value(nodes, node_at(nodes, ("Microscope", "CanDoMotCorr")))),
            "reference": None if reference is None or reference < 0 else float(reference),
        }

    return read("the collar", reading)


#: The sequential mode, by LCS's code: settings taken in turn between lines, frames or stacks.
SEQUENTIAL_MODES = {0: "line", 1: "frame", 2: "stack"}


def get_sequential_mode() -> str:
    """How several settings are taken in turn: ``"line"``, ``"frame"`` or ``"stack"`` (``ScanningParameter/SequentialMode``)."""
    code = settings("the sequential mode", {"m": (*SCANNING, "SequentialMode")})["m"]
    if code not in SEQUENTIAL_MODES:
        raise HardwareServerUnavailable(f"the state tree answered SequentialMode = {code!r}")
    return SEQUENTIAL_MODES[code]


def get_setting() -> dict:
    """The current setting: its ``id``, its ``name``, whether it is ``enabled`` for scanning (the window's checkbox),
    and ``all``, every setting of the current job in order, each with its ``id``, ``name`` and ``enabled``.

    A deleted setting's entry stays in the state tree with no values; it is left out.
    """
    fields = (("id", "SettingID"), ("name", "Name"), ("enabled", "IsEnabledForScanning"))

    def reading(nodes):
        current = {key: node_value(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentSetting", node))) for key, node in fields}
        listed = []
        for info in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock"))), "SettingInfo"):
            each = {key: node_value(nodes, named(nodes, info, node)) for key, node in fields}
            if each["id"] is not None:
                listed.append({"id": each["id"], "name": each["name"], "enabled": bool(each["enabled"])})
        return {"id": current["id"], "name": current["name"], "enabled": bool(current["enabled"]), "all": listed}

    return read("the setting", reading)


def get_smartcorr() -> dict:
    """SmartCORR's range: ``begin`` and ``end`` in percent of the collar, and ``steps``."""
    found = settings(
        "SmartCORR's range",
        {name: ("Microscope", "MotCorrAutomationInfo", f"Automation{name.title()}") for name in ("begin", "end", "steps")},
    )
    return {"begin": float(found["begin"]), "end": float(found["end"]), "steps": found["steps"]}


def get_pane_percentage() -> float:
    """How far one press of the window's pan arrows moves the pan, in percent of the field shown (``PanePercentage``)."""
    return metres(settings("the pan step", {"p": (*SCANNING, "PanePercentage")})["p"], "PanePercentage")


def get_bit_depth() -> int:
    """The bits per pixel a capture is digitised with (8, 12 or 16)."""
    return settings("the bit depth", {"bits": (*SCANNING, "Resolution")})["bits"]


def get_serial_number() -> str:
    """The instrument's serial number, as the hardware info's ``SerialNumber``."""
    return session_facts(
        "the serial number",
        {"serial": ("Acquire", "ActiveDataSource", "CurrentSerialNumber")},
    )["serial"]


def get_microscope_stand() -> str:
    """The microscope stand's name, as the hardware info's ``Microscope.name``."""
    return session_facts(
        "the microscope stand", {"stand": ("Microscope", "CurrentMicStandName")}
    )["stand"]


def objective_layout(nodes, node, kids):
    """Per slot, in order: each of :data:`OBJECTIVE_FIELDS`, read once (the turret is fixed for a session)."""
    return [
        {
            key: node_value(nodes, named(nodes, info, field))
            for key, field in OBJECTIVE_FIELDS.items()
        }
        for info in of_name(nodes, kids, "ObjectiveInfo")
    ]


def get_objectives() -> list[dict]:
    """Every objective on the turret, by slot, as the hardware info's ``Microscope.objectives``."""

    def reading(nodes):
        return [
            objective | {"slotIndex": slot}
            for slot, objective in enumerate(
                layout(nodes, OBJECTIVES, objective_layout)
            )
        ]

    return read("the objectives", reading)


def get_scan_speed_range() -> dict:
    """The scan speeds, as the hardware info's ``ScanSpeed``: ``Min``, ``Max`` and ``ResonantSpeed`` (Hz)."""
    return session_facts(
        "the scan speed range",
        {
            "Min": (*SCANNING, "SpeedMin"),
            "Max": (*SCANNING, "SpeedMax"),
            "ResonantSpeed": ("Confocal", "FastLiveParametersResonant", "ScanSpeedMax"),
        },
    )


def get_stage_limits() -> dict:
    """How far LAS X lets the stage travel, in metres: ``x_min``, ``x_max``, ``y_min``, ``y_max``.

    LAS X's own limits (``Stage/DynamicLimit*``), in the frame of
    :func:`get_xy`. Neither the CAM API nor the log reports them; the
    driver's own limits (``limits.json``) stay what it checks a move against.
    """
    return settings(
        "the stage limits",
        {
            "x_min": ("Stage", "DynamicLimitXmin"),
            "x_max": ("Stage", "DynamicLimitXmax"),
            "y_min": ("Stage", "DynamicLimitYmin"),
            "y_max": ("Stage", "DynamicLimitYmax"),
        },
    )


DETECTION = ("Confocal", "SpectralDetection")

#: The detector ``Type`` codes seen on the simulator, by the hardware info's name for them.
DETECTOR_TYPES = {3: "PMT", 4: "HyD", 6: "SiPM"}

#: Per detector, what is fixed for a session (read once, with the layout).
FIXED_DETECTOR_FIELDS = (
    "Name",
    "BeamRoute",
    "Type",
    "CanDoPhotonCounting",
    "WaveLengthRangeMin",
)

#: Per detector, what can change (read every time): whether it is in use, its band, dye, gain and offset.
CHANGING_DETECTOR_FIELDS = (
    "IsBlockedForDyeAssistant",
    "WaveLengthBegin",
    "WaveLengthEnd",
    "DyeName",
    "HighVoltage",
    "HighVoltageMin",
    "HighVoltageMax",
    "Offset",
)


def detection_layout(nodes, node, kids):
    """Per detector, every spectrophotometer's in order: its fixed fields read, the nodes of the rest.

    ``{"fixed": {field: value}, "active": node, "changing": {field: node}}``.
    """
    return [
        {
            "fixed": {
                field: node_value(nodes, named(nodes, unit, field))
                for field in FIXED_DETECTOR_FIELDS
            },
            "active": named(nodes, unit, "IsActive"),
            "changing": {
                field: named(nodes, unit, field) for field in CHANGING_DETECTOR_FIELDS
            },
        }
        for spectrophotometer in of_name(nodes, kids, "Spectrophotometer")
        for unit in of_name(
            nodes,
            children(nodes, named(nodes, spectrophotometer, "DetectionUnitList")),
            "DetectorHWInfo",
        )
    ]


def get_detectors() -> list[dict]:
    """The detectors in use, as the job settings' ``activeDetectors`` give them.

    ``name``, ``beamRoute``, the detection band (``detectionBegin``,
    ``detectionEnd`` in ``nm``), ``dye``, ``gain`` (``value``, ``min``,
    ``max``) and ``offset``. The log's gain range is narrower (2.5 to 2.5 on
    the simulator) than the detector's (2.5 to 500), which this gives; the
    log's reference laser line is not read.
    """

    def reading(nodes):
        in_use = [
            unit["fixed"]
            | {
                field: node_value(nodes, value)
                for field, value in unit["changing"].items()
            }
            for unit in layout(nodes, DETECTION, detection_layout)
            if node_value(nodes, unit["active"])
        ]
        return [
            {
                "name": unit["Name"],
                "beamRoute": beam_route(unit["BeamRoute"]),
                "detectionBegin": unit["WaveLengthBegin"],
                "detectionEnd": unit["WaveLengthEnd"],
                "detectionUnit": "nm",
                "dye": unit["DyeName"],
                "gain": {
                    "value": unit["HighVoltage"],
                    "min": unit["HighVoltageMin"],
                    "max": unit["HighVoltageMax"],
                },
                "offset": unit["Offset"],
                "lockedForDyeAssistant": bool(unit["IsBlockedForDyeAssistant"]),
            }
            for unit in in_use
        ]

    return read("the detectors", reading)


def get_detection_units() -> list[dict]:
    """Every detector, as the hardware info's ``LightSinks`` ``DetectionUnits`` name them.

    ``Name``, ``BeamRoute``, ``Type`` (PMT, HyD, SiPM; a code not yet seen
    is ``"type <code>"``), ``CanDoPhotonCounting`` and
    ``MinDetectionBandpassWidth``, flat rather than grouped by light sink.
    """

    def reading(nodes):
        return [
            {
                "Name": unit["Name"],
                "BeamRoute": beam_route(unit["BeamRoute"]),
                "Type": DETECTOR_TYPES.get(unit["Type"], f"type {unit['Type']}"),
                "CanDoPhotonCounting": unit["CanDoPhotonCounting"],
                "MinDetectionBandpassWidth": unit["WaveLengthRangeMin"],
            }
            for unit in (
                each["fixed"] for each in layout(nodes, DETECTION, detection_layout)
            )
        ]

    return read("the detection units", reading)


FILTER_WHEELS = (*HARDWARE, "FilterWheelList")

#: The multi-function port's type code, as the CAM API and the log list it among the wheels.
MULTI_FUNCTION_PORT = 180


def wheel_layout(nodes, node, kids):
    """Per wheel (any child with a ``FilterWheelType``): its type and filter names, read once, and its slot's node."""
    wheels = []
    for kid in kids:
        kind = child_named(nodes, kid, "FilterWheelType")
        if kind is None:
            continue
        wheels.append(
            {
                "type": node_value(nodes, kind),
                "names": [
                    node_value(nodes, named(nodes, each, "FilterName"))
                    for each in of_name(nodes, children(nodes, kid), "Filter")
                ],
                "slot": named(nodes, kid, "CurrentSlotPosition"),
            }
        )
    return wheels


def installed_filter_wheels():
    """Every wheel's type and filter names, then the multi-function port's: fixed for a session."""

    def reading(nodes):
        return [
            {"type": w["type"], "names": w["names"]}
            for w in layout(nodes, FILTER_WHEELS, wheel_layout)
        ]

    port = session_facts(
        "the multi-function port", {"names": (*HARDWARE, "MultiFunctionPortNameList")}
    )
    return read("the filter wheels", reading) + [
        {"type": MULTI_FUNCTION_PORT, "names": list(port["names"] or ())}
    ]


def filter_in_path(names, slot):
    return names[slot] if isinstance(slot, int) and 0 <= slot < len(names) else None


def get_filter_wheels() -> list[dict]:
    """Every filter wheel, then the multi-function port, with the filter in the light path.

    ``type`` (LAS X's code, the one the CAM API and the log give),
    ``filterIndex`` and ``filterName`` as the job settings' ``filterWheels``,
    ``filterNames`` as the hardware info's ``FilterWheels``. The tree names
    no wheel, and it also lists LAS X's internal wheels (types 200, 160 and
    170 on the simulator), which the CAM API and the log leave out.
    """

    def reading(nodes):
        return [
            node_value(nodes, wheel["slot"])
            for wheel in layout(nodes, FILTER_WHEELS, wheel_layout)
        ] + [
            node_value(
                nodes, node_at(nodes, (*HARDWARE, "CurrentMultiFunctionPortPos"))
            )
        ]

    slots = read("the filter wheels", reading)
    return [
        {
            "type": wheel["type"],
            "filterIndex": slot,
            "filterName": filter_in_path(wheel["names"], slot),
            "filterNames": wheel["names"],
        }
        for wheel, slot in zip(installed_filter_wheels(), slots, strict=True)
    ]


#: The autofocus's methods, by LCS's ``AFAnalyseType`` (the window's "Optimized for", simulator, 2026-11-02).
AUTOFOCUS_METHODS = {1: "contrast 1", 2: "intensity", 3: "noise", 4: "contrast 2", 5: "reflection"}


def get_autofocus_settings() -> dict:
    """The autofocus's settings (the window's Best Focus Scan Options): ``drive``, ``range_um``, ``method``,
    ``precision`` (1 speed to 5 accuracy), and ``steps`` (a fixed number of steps, or None when the precision decides).
    """
    found = settings(
        "the autofocus's settings",
        {name: ("Acquire", "AutoFocusInfo", node) for name, node in (
            ("drive", "ZUseMode"), ("range", "FocusRange"), ("method", "AFAnalyseType"),
            ("precision", "Precision"), ("fixed", "UseFixSliceCount"), ("steps", "FixSliceCount"),
        )},
    )
    drives = {code: drive for drive, code in Z_USE_MODE.items()}
    if found["drive"] not in drives:
        raise HardwareServerUnavailable(f"the state tree names autofocus drive {found['drive']!r}")
    return {
        "drive": drives[found["drive"]],
        "range_um": float(found["range"]),
        "method": AUTOFOCUS_METHODS.get(found["method"], f"unknown ({found['method']})"),
        "precision": found["precision"],
        "steps": found["steps"] if found["fixed"] else None,
    }


AUTOFOCUS_SETTING = ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo", "SequentialAutofocusSetting")


def get_detector_modes() -> dict:
    """Each detector's modes in the current setting, by name: ``tau`` and ``operating`` (LCS's codes now),
    ``tau_offered`` and ``operating_offered`` (codes, by place), ``tau_available`` (the tau
    modes the window does not grey out now), ``tau_colours`` and ``tau_lifetimes_ns``
    (TauSeparation's two channels' colours and lifetimes; empty in another tau mode), and ``at`` (the setting's, the
    photometer's and the detector's places in LCS's state tree).

    LCS keeps them per setting (``SequentialSettingParameter[n]``, the current
    setting's place). The window names a tau mode by its place in its list
    (``SetDigitalGatingModeIndex``), and writes an operating mode's place into
    the setting's ``OperatingModeIndex`` (simulator, 2026-11-02).
    """

    def reading(nodes):
        current = node_value(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentSetting", "SettingID")))
        ids = [
            node_value(nodes, named(nodes, info, "SettingID"))
            for info in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock"))), "SettingInfo")
            if node_value(nodes, named(nodes, info, "SettingID")) is not None
        ]
        place = ids.index(current) if current in ids else 0
        params = of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo"))), "SequentialSettingParameter")
        names = [unit["fixed"]["Name"] for unit in layout(nodes, DETECTION, detection_layout)]
        units = []
        detection = child_named(nodes, params[place], "SpectralDetection_SequentialInfo") if place < len(params) else None
        for spectro_place, spectro in enumerate(of_name(nodes, children(nodes, detection), "Spectrophotometer_SequentialInfo") if detection else []):
            listed = named(nodes, spectro, "DetectionUnitList_SequentialInfo")
            for unit_place, unit in enumerate(of_name(nodes, children(nodes, listed), "DetectorHWInfo_SequentialInfo")):
                units.append(((place, spectro_place, unit_place), unit))

        def listed_codes(unit, list_name, entry_name, code_name):
            holder = child_named(nodes, unit, list_name)
            entries = of_name(nodes, children(nodes, holder), entry_name) if holder else []
            return [node_value(nodes, named(nodes, entry, code_name)) for entry in entries]

        # Each detector's most TauScan images, from the detector itself (it follows the mode; simulator, 2026-11-03).
        most = {}
        for spectro in of_name(nodes, children(nodes, node_at(nodes, DETECTION)), "Spectrophotometer"):
            for hw in of_name(nodes, children(nodes, child_named(nodes, spectro, "DetectionUnitList")), "DetectorHWInfo"):
                found_most = child_named(nodes, hw, "TauScanMaxImageCount")
                most[node_value(nodes, named(nodes, hw, "Name"))] = node_value(nodes, found_most) if found_most is not None else None

        def tau_scan(unit, most_images):
            """TauScan's ``images``, ``range_ns`` (begin, end) and ``most_images``; None in another tau mode."""
            if node_value(nodes, named(nodes, unit, "DigitalGatingMode")) != 5:
                return None
            begin, end = (node_value(nodes, named(nodes, unit, n)) for n in ("TauScanRangeBegin", "TauScanRangeEnd"))
            return {"images": node_value(nodes, named(nodes, unit, "TauScanImageCount")), "range_ns": (begin * 1e9, end * 1e9),
                    "most_images": most_images}

        def tau_gates(unit):
            """TauGating's channels: each ``on`` and its ``gates`` as (begin, end) in nanoseconds; empty in another tau mode."""
            if node_value(nodes, named(nodes, unit, "DigitalGatingMode")) != 3:
                return []
            holder = child_named(nodes, unit, "DetectorChannelList_SeqInfo")
            found = []
            for channel in of_name(nodes, children(nodes, holder), "DetectorChannel") if holder else []:
                gates = [
                    (node_value(nodes, named(nodes, gate, "GateBegin")) * 1e9, node_value(nodes, named(nodes, gate, "GateEnd")) * 1e9)
                    for gate in of_name(nodes, children(nodes, channel), "Gate")
                    if node_value(nodes, named(nodes, gate, "GateBegin")) is not None
                ]
                found.append({"on": bool(node_value(nodes, named(nodes, channel, "IsChannelActive"))), "gates": gates})
            return found

        def tau_channels(unit):
            holder = child_named(nodes, unit, "TauSeparationDefinition_SeqInfo")
            return of_name(nodes, children(nodes, holder), "TauSeparationChannel") if holder else []

        found = {}
        for name, (at, unit) in zip(names, units):
            operating = child_named(nodes, unit, "OperatingMode")
            found[name] = {
                "tau": node_value(nodes, named(nodes, unit, "DigitalGatingMode")),
                "tau_offered": listed_codes(unit, "DigitalGatingModeList_SeqInfo", "DigitalGatingMode", "DigitalGatingMode"),
                "tau_available": [
                    code for code, enabled in zip(
                        listed_codes(unit, "DigitalGatingModeList_SeqInfo", "DigitalGatingMode", "DigitalGatingMode"),
                        listed_codes(unit, "DigitalGatingModeList_SeqInfo", "DigitalGatingMode", "IsEnabled"),
                    ) if enabled
                ],
                "operating": node_value(nodes, operating) if operating is not None else None,
                "operating_offered": listed_codes(unit, "DetectionUnitOperatingModeList_SeqInfo", "DetectionUnitOperatingMode", "OperatingMode"),
                "tau_colours": listed_codes(unit, "TauSeparationDefinition_SeqInfo", "TauSeparationChannel", "LutName"),
                "gates": tau_gates(unit),
                "tau_scan": tau_scan(unit, most.get(name)),
                "donor_lifetime_ns": (
                    node_value(nodes, named(nodes, unit, "TauInteractionDonorLifetime")) * 1e9
                    if node_value(nodes, named(nodes, unit, "DigitalGatingMode")) == 10 else None
                ),
                "tau_lifetimes_ns": [
                    node_value(nodes, named(nodes, child_named(nodes, channel, "TauSeparationLine"), "TauLinePosition")) * 1e9
                    for channel in tau_channels(unit)
                ],
                "at": at,
            }
        return found

    return read("the detectors' modes", reading)


def get_autofocus_scan() -> dict:
    """The Best Focus Setting's own scan: ``format`` (x, y), ``speed``, ``bidirectional``, ``zoom``; its ``enabled`` and ``id``."""
    found = settings(
        "the Best Focus Setting",
        {name: (*AUTOFOCUS_SETTING, node) for name, node in (
            ("x", "FormatX"), ("y", "FormatY"), ("speed", "Speed"), ("direction", "ScanDirectionX"),
            ("zoom", "Zoom"), ("enabled", "IsEnabledForScanning"), ("id", "SettingId"),
        )},
    )
    return {
        "format": (found["x"], found["y"]), "speed": found["speed"], "bidirectional": found["direction"] == 2,
        "zoom": float(found["zoom"]), "enabled": bool(found["enabled"]), "id": found["id"],
    }


def get_autofocus_optics() -> dict:
    """The Best Focus Setting's laser lines with power: ``lines``, each ``beamRoute``, ``lineIndex``, ``wavelength``, ``percent``."""

    def reading(nodes):
        sources = [s["route"] for s in layout(nodes, LIGHT_SOURCES, light_layout)]
        listed = child_named(nodes, node_at(nodes, AUTOFOCUS_SETTING), "LightSourceList_SequentialInfo")
        lines = []
        for place, source in enumerate(of_name(nodes, children(nodes, listed), "LightSource_SequentialInfo") if listed else []):
            laser = child_named(nodes, source, "UnifiedLaser_SequentialInfo")
            for line in of_name(nodes, children(nodes, laser), "UnifiedLaserLine_SequentialInfo") if laser else []:
                power = node_value(nodes, named(nodes, line, "Intensity"))
                if isinstance(power, (int, float)) and power > 0 and place < len(sources):
                    lines.append({
                        "beamRoute": sources[place], "lineIndex": node_value(nodes, named(nodes, line, "LineIndex")),
                        "wavelength": node_value(nodes, named(nodes, line, "WaveLength")), "percent": float(power),
                    })
        return {"lines": lines}

    return read("the Best Focus Setting's optics", reading)


def get_mfp_auto_select() -> bool:
    """Whether the multi-function port (MFP) follows the setting (Autoselect; ``HWParameter/IsMultiFunctionPortAutoSelectionEnabled``)."""
    return bool(settings("the MFP's Autoselect", {"on": ("Confocal", "HWParameter", "IsMultiFunctionPortAutoSelectionEnabled")})["on"])


def get_fluorifier() -> dict:
    """The Fluorifier disc: ``auto_select`` (the notch filters follow the laser lines) and ``polarizer_deg`` (the left
    wheel's angle when it is turned as a polarizer; None when it holds a notch filter). The filters themselves are
    filter wheels 130 and 131 (:func:`get_filter_wheels`)."""
    w = ("Confocal", "HWParameter", "FilterWheelList", "PolarizationFW")
    found = settings("the Fluorifier disc", {
        "auto": ("Confocal", "HWParameter", "IsNotchFilterAutoSelectionEnabled"), "turn": (*w, "IsSpectrumTurnMode"), "angle": (*w, "FilterSpectrumPos"),
    })
    return {"auto_select": bool(found["auto"]), "polarizer_deg": float(found["angle"]) if found["turn"] else None}


def get_coverslip_offset() -> float:
    """The Reflection autofocus's coverslip offset, in micrometres (``Acquire/AutoFocusInfo/ReflectionOffset``, metres)."""
    return metres(settings("the coverslip offset", {"o": ("Acquire", "AutoFocusInfo", "ReflectionOffset")})["o"], "ReflectionOffset") * 1e6


def current_setting_place(nodes) -> int:
    """The current setting's place among the job's settings, 0 the first, as LCS orders ``SequentialSettingParameter``."""
    current = node_value(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentSetting", "SettingID")))
    ids = [
        node_value(nodes, named(nodes, info, "SettingID"))
        for info in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock"))), "SettingInfo")
        if node_value(nodes, named(nodes, info, "SettingID")) is not None
    ]
    return ids.index(current) if current in ids else 0


def get_laser_output_power() -> dict:
    """Each tunable light source's own output power in the current setting, in percent, by LAS X's name for the
    source: the Laser Overview's slider, which the window offers for the white-light laser only (85 on the
    simulator; the fixed lasers read 0 or -1 there and are left out).

    ``SequentialSettingParameter[n]/.../UnifiedLaserLine_SequentialInfo/LaserTargetOutputPower``, the same on
    every line of a source (-1 for a laser without one; simulator, 2026-11-02).
    """

    def reading(nodes):
        sources = layout(nodes, LIGHT_SOURCES, light_layout)
        names = [source["name"] for source in sources]
        tunable = [any(low != high for _name, low, high in source["lasers"]) for source in sources]
        params = of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo"))), "SequentialSettingParameter")
        place = current_setting_place(nodes)
        listed = child_named(nodes, params[place], "LightSourceList_SequentialInfo") if place < len(params) else None
        found = {}
        for i, source in enumerate(of_name(nodes, children(nodes, listed), "LightSource_SequentialInfo") if listed else []):
            laser = child_named(nodes, source, "UnifiedLaser_SequentialInfo")
            lines = of_name(nodes, children(nodes, laser), "UnifiedLaserLine_SequentialInfo") if laser else []
            power = node_value(nodes, named(nodes, lines[0], "LaserTargetOutputPower")) if lines else None
            if isinstance(power, (int, float)) and power >= 0 and i < len(names) and tunable[i]:
                found[names[i]] = float(power)
        return found

    return read("the lasers' output power", reading)


#: The Λλ scan's side of the detection, by LCS's code (``LambdaLambdaDetectionRangeGeometry``; simulator, 2026-11-03).
LAMBDA_GEOMETRIES = {0: "behind", 1: "ahead", 2: "independent"}
#: The Λ scan's methods for the detection band, by LCS's code (``DetectionBandWidthMode``).
EXCITATION_METHODS = {4: "right fixed", 5: "constant"}


def get_excitation_scan() -> dict:
    """The Λ (excitation) scan, as LAS X's Λ panel shows it: ``begin_nm``, ``end_nm``, ``step_nm``, ``automatic_sp`` (the
    detection follows the excitation), ``method`` ("right fixed" or "constant"), ``min_gap_nm`` (between excitation and
    detection), ``after_stage`` (Lambda After Stage), and for the Λλ scan ``geometry`` (the detection "behind", "ahead" or
    "independent" of the excitation) and ``max_gap_nm`` (None when off). ``Confocal/LambdaDefinition`` (simulator, 2026-11-03)."""
    l = ("Confocal", "LambdaDefinition")
    found = settings("the Λ scan", {k: (*l, n) for k, n in (
        ("begin", "ExcitationBegin"), ("end", "ExcitationEnd"), ("step", "ExcitationStepSize"), ("auto", "IsAutomaticSPUnitMovementActive"),
        ("method", "DetectionBandWidthMode"), ("gap", "LambdaMinGapExctToDetct"), ("after", "IsExcitationLambdaAfterStageDim"),
        ("geometry", "LambdaLambdaDetectionRangeGeometry"), ("use_max", "UseLambdaMaxGapExctToDetct"), ("max", "LambdaMaxGapExctToDetct"))})
    return {
        "begin_nm": float(found["begin"]), "end_nm": float(found["end"]), "step_nm": float(found["step"]),
        "automatic_sp": bool(found["auto"]), "method": EXCITATION_METHODS.get(found["method"], found["method"]),
        "min_gap_nm": float(found["gap"]), "after_stage": bool(found["after"]),
        "geometry": LAMBDA_GEOMETRIES.get(found["geometry"], found["geometry"]),
        "max_gap_nm": float(found["max"]) if found["use_max"] else None,
    }


def get_lambda_scan() -> dict:
    """The λ scan's detection, as LAS X's λ panel shows it: ``begin_nm``, ``end_nm``, ``step_nm``, ``bandwidth_nm``
    (``Confocal/LambdaDefinition``). LCS's count of steps beside them did not follow the changes on the simulator
    (2026-11-02), so it is not given."""
    l = ("Confocal", "LambdaDefinition")
    found = settings("the λ scan", {k: (*l, n) for k, n in (
        ("begin_nm", "DetectionBegin"), ("end_nm", "DetectionEnd"), ("step_nm", "DetectionStepSize"),
        ("bandwidth_nm", "DetectionBandWidth"))})
    return {k: float(v) for k, v in found.items()}


#: Auto Sync's scan options, by scanner, where LCS keeps them.
AUTO_SYNC = {"fov": "AutoFocusSettingConfocalScanOptions", "resonant": "AutoFocusSettingConfocalScanOptionsResonant"}


def get_auto_sync() -> dict:
    """Auto Sync (the autofocus using the current setting's light and detectors) and its scan options:
    ``on``, and by scanner (``"fov"``, ``"resonant"``) ``format``, ``scan_frequency``, ``overview_zoom`` (each
    None when its box is unticked), ``bidirectional``, ``no_average``. ``Acquire/AutoFocusInfo/IsAutoSyncActive``
    and ``Acquire/AutoFocusSettingConfocalScanOptions`` (simulator, 2026-11-02).
    """
    found = {"on": bool(settings("Auto Sync", {"on": ("Acquire", "AutoFocusInfo", "IsAutoSyncActive")})["on"])}
    for scanner, parent in AUTO_SYNC.items():
        v = settings(f"Auto Sync's {scanner} options", {k: ("Acquire", parent, k) for k in (
            "ChangeFormat", "WantedPixelSizeX", "ChangeSpeed", "WantedSpeed", "ChangeFieldOfView", "SuggestedZoomReduction",
            "UseBiDir", "NoAccuAndOrAverage")})
        found[scanner] = {
            "format": v["WantedPixelSizeX"] if v["ChangeFormat"] else None,
            "scan_frequency": v["WantedSpeed"] if v["ChangeSpeed"] else None,
            "overview_zoom": float(v["SuggestedZoomReduction"]) if v["ChangeFieldOfView"] else None,
            "bidirectional": bool(v["UseBiDir"]),
            "no_average": bool(v["NoAccuAndOrAverage"]),
        }
    return found


def get_autofocus_in_time_series():
    """When the autofocus runs in a time series, as its Timelapse tab shows it: ``"first"`` (the first cycle only),
    ``"every"`` (every cycle), or n (every n-th cycle). ``Acquire/AutoFocusInfo/WorkflowTimelapse`` and its iterator.
    """
    found = settings("the autofocus in a time series", {
        "when": ("Acquire", "AutoFocusInfo", "WorkflowTimelapse"), "n": ("Acquire", "AutoFocusInfo", "WorkflowTimelapseIterator"),
    })
    return {1: "first", 2: "every"}.get(found["when"], found["n"])


#: Linear Z-compensation's modes by name and LCS's bit for them.
Z_COMPENSATION_MODES = {"excitation": 1, "detector": 2, "motcorr": 4}


def get_z_compensation() -> dict:
    """Linear Z-compensation in the current setting: ``modes`` on (``excitation``, ``detector``, ``motcorr``),
    ``reference_um``, and ``points``, each with ``z_um`` (the reference plus the point's offset) and ``values``
    (each laser line's power and detector's gain held there, by LAS X's name).

    ``Confocal/ZCompensation`` (``CurrentMode``, a sum of the modes' bits;
    ``ReferencePosition``) and the setting's ``ZCompensation_SeqInfo``
    (simulator, 2026-11-02).
    """

    def reading(nodes):
        mode = node_value(nodes, node_at(nodes, ("Confocal", "ZCompensation", "CurrentMode"))) or 0
        reference = node_value(nodes, node_at(nodes, ("Confocal", "ZCompensation", "ReferencePosition")))
        current = node_value(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentSetting", "SettingID")))
        ids = [
            node_value(nodes, named(nodes, info, "SettingID"))
            for info in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock"))), "SettingInfo")
            if node_value(nodes, named(nodes, info, "SettingID")) is not None
        ]
        place = ids.index(current) if current in ids else 0
        params = of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo"))), "SequentialSettingParameter")
        holder = child_named(nodes, params[place], "ZCompensation_SeqInfo") if place < len(params) else None
        points = []
        for position in of_name(nodes, children(nodes, holder), "ZCompensationPosition") if holder else []:
            offset = node_value(nodes, named(nodes, position, "ZPosition"))
            if offset is None:
                continue
            values = {
                node_value(nodes, named(nodes, value, "Name")): node_value(nodes, named(nodes, value, "Value"))
                for value in of_name(nodes, children(nodes, position), "ZCompensationValue")
            }
            points.append({"z_um": (reference + offset) * 1e6, "offset_m": offset, "values": values})
        return {
            "modes": [name for name, bit in Z_COMPENSATION_MODES.items() if mode & bit],
            "reference_um": reference * 1e6,
            "points": points,
        }

    return read("the Z-compensation", reading)


def get_roi_scan() -> dict:
    """ROI scan and the bleach mode, as the Acquisition Mode panel's buttons show them: ``on`` (ROI scan),
    ``sections`` (first and last section of the stack it is limited to, or None), ``bleach_mode``; and
    ``roi_possible`` and ``bleach_possible``, whether the window offers each now (``Confocal/ROIScan``).
    """
    r = ("Confocal", "ROIScan")
    found = settings("the ROI scan", {
        "on": (*r, "EnableRoiScan"), "limited": (*r, "Is3DLimitedRoiScanEnabled"), "first": (*r, "First3DRoiSection"),
        "last": (*r, "Last3DRoiSection"), "bleach": (*r, "EnableBleachScan"), "can_roi": (*r, "CanDoROIScan"),
        "can_bleach": (*r, "CanDoBleach"),
    })
    return {
        "on": bool(found["on"]),
        "sections": (found["first"], found["last"]) if found["on"] and found["limited"] else None,
        "bleach_mode": bool(found["bleach"]),
        "roi_possible": bool(found["can_roi"]),
        "bleach_possible": bool(found["can_bleach"]),
    }


def get_line_scan() -> dict:
    """The xt line scan, as LAS X's t panel shows it in xt: ``lines``, ``lines_per_page``, ``pages``, ``max_lines_per_page``
    (MAX), ``page_interval_s``, ``line_time_s`` (``Acquire/AcquireTStackInfo``; simulator, 2026-11-03)."""
    t = ("Acquire", "AcquireTStackInfo")
    found = settings("the line scan", {k: (*t, n) for k, n in (
        ("lines", "Lines"), ("per_page", "LinesPerPage"), ("pages", "Pages"), ("max", "IsMaxLinesActive"), ("interval", "DelayTime"),
        ("line", "LineTime"))})
    return {"lines": found["lines"], "lines_per_page": found["per_page"], "pages": found["pages"], "max_lines_per_page": bool(found["max"]),
            "page_interval_s": float(found["interval"]), "line_time_s": float(found["line"])}


def get_time_series() -> dict:
    """The time series, as LAS X's t panel shows it: ``frames``, ``interval_s``, ``duration_s``, ``until_stopped``, ``minimized``,
    and ``shortest_interval_s`` (one frame, at the fastest; LCS keeps the interval at least that long).

    ``Acquire/AcquireTStackInfo`` (simulator, 2026-11-02): ``RepeatCount``,
    ``DelayTime``, ``Duration``, ``IsEnableAutomaticTSectionsCalculation``
    ("Acquire Until Stopped"), ``MaxFrameRate``.
    """
    t = ("Acquire", "AcquireTStackInfo")
    found = settings("the time series", {
        "frames": (*t, "RepeatCount"), "interval": (*t, "DelayTime"), "duration": (*t, "Duration"),
        "until": (*t, "IsEnableAutomaticTSectionsCalculation"), "rate": (*t, "MaxFrameRate"), "minimized": (*t, "IsMinimized"),
    })
    return {
        "frames": found["frames"],
        "interval_s": float(found["interval"]),
        "duration_s": float(found["duration"]),
        "until_stopped": bool(found["until"]),
        "minimized": bool(found["minimized"]),
        "shortest_interval_s": round(1 / found["rate"], 6) if found["rate"] else None,
    }


#: Fast Live's two scanners and where LCS keeps their Scan Settings.
FAST_LIVE = {"fov": "FastLiveParameters", "resonant": "FastLiveParametersResonant"}


def get_fast_live() -> dict:
    """Fast Live's Scan Settings, by scanner (``"fov"``, ``"resonant"``), as its window shows them.

    ``format`` (Maximal Format), ``scan_frequency`` (Minimal Scan Frequency),
    ``overview_zoom`` (its zoom, None when off), ``bidirectional``,
    ``no_average``, ``no_dynamic_signal_enhancement``, ``selected_setting_only``
    and ``no_tau_sted_xtend``; from ``Confocal/FastLiveParameters`` and
    ``FastLiveParametersResonant`` (simulator, 2026-11-02).
    """
    found = {}
    for scanner, parent in FAST_LIVE.items():
        nodes = {
            "format": "ScanFormat", "scan_frequency": "ScanSpeed", "adjust": "AdjustZoom", "zoom": "Zoom",
            "bidirectional": "Bidirectional", "no_average": "NoAccumulationAverage",
            "no_dynamic_signal_enhancement": "NoRollingAverage", "selected_setting_only": "SelectedSettingOnly",
            "no_tau_sted_xtend": "NoTauSTEDDeconvolution",
        }
        values = settings(f"Fast Live's {scanner} settings", {key: ("Confocal", parent, node) for key, node in nodes.items()})
        adjust, zoom = values.pop("adjust"), values.pop("zoom")
        found[scanner] = {
            "format": values["format"], "scan_frequency": values["scan_frequency"],
            "overview_zoom": float(zoom) if adjust else None,
            **{key: bool(values[key]) for key in ("bidirectional", "no_average", "no_dynamic_signal_enhancement", "selected_setting_only", "no_tau_sted_xtend")},
        }
    return found


def get_aobs_lines() -> list[dict]:
    """The AOBS's lines in use, as its Configuration window shows them: ``wavelength``, ``reflection``,
    ``enhanced_dynamics``, ``percent`` (the share of the line passed on), and ``at``, the line's place.

    ``Confocal/AOBSLineList/AOBSLine``, eight places; only the white-light
    laser's lines go through it. ``enhanced_dynamics_possible`` is not part of
    a line; :func:`aobs_offers_enhanced_dynamics` answers it.
    """

    def reading(nodes):
        lines = of_name(nodes, children(nodes, node_at(nodes, ("Confocal", "AOBSLineList"))), "AOBSLine")
        found = []
        for place, line in enumerate(lines):
            if not node_value(nodes, named(nodes, line, "IsUsed")):
                continue
            found.append({
                "wavelength": node_value(nodes, named(nodes, line, "LaserLine")),
                "reflection": bool(node_value(nodes, named(nodes, line, "IsReflection"))),
                "enhanced_dynamics": bool(node_value(nodes, named(nodes, line, "IsDoubleModeEnabled"))),
                "percent": node_value(nodes, named(nodes, line, "Intensity")),
                "at": place,
            })
        return found

    return read("the AOBS's lines", reading)


def aobs_offers_enhanced_dynamics() -> bool:
    """Whether this microscope's AOBS has Enhanced Dynamics (``Confocal/AOBSLineList/CandoEnhancedDynamics``)."""
    return bool(settings("Enhanced Dynamics", {"can": ("Confocal", "AOBSLineList", "CandoEnhancedDynamics")})["can"])


def get_laser_slots() -> list[dict]:
    """The laser lines in use in each of the job's settings: ``setting`` (its name), ``beamRoute``, ``lineIndex``, ``wavelength``.

    A tunable laser's line is a slot whose wavelength every setting shares;
    each setting has its own power for it (``CurrentBlock/ConfocalSequentialInfo/
    SequentialSettingParameter[n]``, simulator, 2026-11-01). A line is in use
    in a setting when its power there is above 0.
    """

    def reading(nodes):
        sources = [s["route"] for s in layout(nodes, LIGHT_SOURCES, light_layout)]
        names = [
            node_value(nodes, named(nodes, info, "Name"))
            for info in of_name(nodes, children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock"))), "SettingInfo")
            if node_value(nodes, named(nodes, info, "SettingID")) is not None
        ]
        params = of_name(
            nodes,
            children(nodes, node_at(nodes, ("Acquire", "ATL", "CurrentBlock", "ConfocalSequentialInfo"))),
            "SequentialSettingParameter",
        )
        used = []
        for name, param in zip(names, params):
            listed = child_named(nodes, param, "LightSourceList_SequentialInfo")
            for place, source in enumerate(of_name(nodes, children(nodes, listed), "LightSource_SequentialInfo") if listed else []):
                laser = child_named(nodes, source, "UnifiedLaser_SequentialInfo")
                for line in of_name(nodes, children(nodes, laser), "UnifiedLaserLine_SequentialInfo") if laser else []:
                    power = node_value(nodes, named(nodes, line, "Intensity"))
                    if isinstance(power, (int, float)) and power > 0 and place < len(sources):
                        used.append({
                            "setting": name,
                            "beamRoute": sources[place],
                            "lineIndex": node_value(nodes, named(nodes, line, "LineIndex")),
                            "wavelength": node_value(nodes, named(nodes, line, "WaveLength")),
                        })
        return used

    return read("the laser lines of every setting", reading)


def any_laser_line_locked() -> bool:
    """Whether any laser line is locked for LAS X's Dye Assistant (a light source's ``LightSourceLineUsage/IsBlocked``)."""

    def reading(nodes):
        for source in of_name(nodes, children(nodes, node_at(nodes, ("Confocal", "LightSourceList"))), "LightSource"):
            for usage in of_name(nodes, children(nodes, source), "LightSourceLineUsage"):
                blocked = child_named(nodes, usage, "IsBlocked")
                if blocked is not None and node_value(nodes, blocked):
                    return True
        return False

    return read("the laser lines' locks", reading)


def get_light_sources() -> list[dict]:
    """Every light source and its laser lines, as the hardware info's ``LightSources``.

    ``name``, ``BeamRoute`` and ``LaserLines`` (``LaserName``, ``Index``,
    ``MinWavelength``, ``MaxWavelength``): one line per AOTF line, each of
    a tunable laser's lines carrying the laser's range (the white-light
    laser's eight on the simulator).
    """

    def reading(nodes):
        sources = []
        for source in layout(nodes, LIGHT_SOURCES, light_layout):
            lines = []
            for _wavelength, line_index, _intensity in source["lines"]:
                name, low, high = laser_of_line(source["lasers"], line_index)
                lines.append(
                    {
                        "LaserName": name,
                        "Index": line_index,
                        "MinWavelength": low,
                        "MaxWavelength": high,
                    }
                )
            sources.append(
                {
                    "name": source["name"],
                    "BeamRoute": source["route"],
                    "LaserLines": lines,
                }
            )
        return sources

    return read("the light sources", reading)


def get_base_fov() -> tuple[float, float]:
    """The field of view at zoom 1, in metres: the field of view times the zoom."""
    found = settings(
        "the field of view at zoom 1",
        {
            "x": (*IMAGE_SIZE, "ImageSizeX"),
            "y": (*IMAGE_SIZE, "ImageSizeY"),
            "zoom": (*SCANNING, "Zoom"),
        },
    )
    zoom = float(found["zoom"] or 1)
    return metres(found["x"], "ImageSizeX") * zoom, metres(
        found["y"], "ImageSizeY"
    ) * zoom


def get_afc_installed() -> bool:
    """Whether the instrument has adaptive focus control, as the hardware info's ``Microscope.AfcInstalled``."""
    return bool(
        session_facts("AFC", {"afc": ("Confocal", "ServiceOnlyParameter", "CanDoAFC")})[
            "afc"
        ]
    )


def ping() -> bool:
    """Whether the hardware server answers; never raises."""
    try:
        settings("a ping", {"state": ("Acquire", "AcquisitionState")})
    except HardwareServerUnavailable:
        return False
    return True


def get_active_settings() -> list[dict]:
    """The settings LAS X scans with, as the job settings' ``activeSettings``.

    One setting: its laser lines, detectors, filter wheels, averaging and
    pinhole (``pinholeAiry``); the setting's own ``name``, ``id`` and
    ``index`` are not read. With several sequential settings the tree holds
    only the current one's lasers and detectors, so several raise
    :class:`HardwareServerUnavailable` rather than answer for one of them.
    """
    count = settings(
        "the sequential settings",
        {"averages": (*SCANNING, "LineAverageSequentialList")},
    )["averages"]
    if not isinstance(count, tuple) or len(count) != 1:
        raise HardwareServerUnavailable(
            f"{len(count) if isinstance(count, tuple) else count} sequential settings; "
            "the direct reader reads one"
        )
    return [
        {
            "activeLaserLines": get_laser_lines(),
            "activeDetectors": get_detectors(),
            "filterWheels": get_filter_wheels(),
            **get_averaging(),
            "pinholeAiry": get_pinhole()["airy"],
        }
    ]


def server_answer(command: str, what: str) -> dict:
    """The JSON LAS X answers to a read *command*.

    LAS X answers ``<Result Error="">{...}</Result>``; any other answer raises
    :class:`HardwareServerUnavailable` with LAS X's words.
    """
    try:
        answer = str(connected_server().SendCommand2(command, ""))
    except HardwareServerUnavailable:
        raise
    except Exception as why:
        forget_connection()
        raise HardwareServerUnavailable(
            f"the hardware server did not answer {what}: {why}"
        ) from why
    if not answer.startswith('<Result Error="">'):
        raise HardwareServerUnavailable(f"LAS X refused {what}: {answer[:200]}")
    try:
        return json.loads(answer[answer.index(">") + 1 : answer.rindex("<")])
    except ValueError as why:
        raise HardwareServerUnavailable(
            f"LAS X's answer to {what} is not JSON: {answer[:200]}"
        ) from why


def get_hardware_info() -> dict:
    """The instrument, whole: LAS X's description of it, as JSON (the same the CAM API gives)."""
    return server_answer(
        '<Scanner Command="GetConfocalHardwareInfoAsJson"/>', "the hardware info"
    )
