"""The direct reader: LAS X's live state, read from its hardware server.

The third reader beside :mod:`api_reader` (the CAM API) and
:mod:`log_reader` (LAS X's log). It reads the hardware server's state tree
(:mod:`state_tree`), which holds LAS X's current state, so it is neither
late like the CAM API's answers nor waiting for a log line. Its
functions carry the other readers' names and give answers of the same
shape, so a caller cannot tell which reader answered; the routed readers
(``dispatcher/reader.py``, ``primitive_handling/readings/state.py``) say so in a reading's ``source``.

Measured on the simulator, 2026-10-13: the stage in 1.1 ms (CAM API 20 ms,
worst 57 ms), a new stage position 3.3-4.2 ms after a move (CAM API
22-58 ms), both z drives in 1.1 ms.

Leica agreed (2026-10-13) to the driver reading the stage, the z drives and
the jobs (the active one and the ones there are) this way; Thom added what
LAS X is set to now (scan status and mode, zoom, scan speed, format,
averaging, pinhole, objective, field of view, z-stack, laser lines, scan
field rotation, sequential mode, pixel size, bit depth, detectors, filter
wheels, the active setting) and what the instrument is (serial number,
stand, objectives, scan speed range, stage limits, detectors, light
sources, AFC), read-only.
:data:`READS` names them all; the direct reader reads nothing else, and
never writes.

What LAS X is set to now is the current setting of the selected job: the
tree holds one set of scanning parameters, the one LAS X scans with, where
the CAM API's job settings are per job. On the simulator (2026-10-13) every
one that the log's job settings for the selected job also carry agreed
with them; the CAM API could not be compared, it refused connections then.

A job's settings and the hardware info, whole, are LAS X's answers to two
reads (``ATL_GetBlockApiInfoAsJsonString`` with the job's block id, and
``GetConfocalHardwareInfoAsJson``), in the same JSON the CAM API gives: on
the simulator (2026-10-14) identical to the CAM API's, value for value, for
every job, a filter wheel's spectrum mode and the ``SystemType`` included,
in 0.5-1.5 ms and 20 ms. Only for the selected
job's z drives the state tree says more: where they truly are.

Every function raises :class:`HardwareServerUnavailable` when the hardware
server cannot be reached or does not answer, rather than returning anything
that is not a fresh reading.
"""

from __future__ import annotations

import json
import math

from ...connection.hardware_server import HardwareServerUnavailable, connected_server, forget_connection
from .state_tree import SCAN_STATES, STAGE_PATHS, Z_USE_MODE, child_named, children, job_sequence, layout, named, node_at, node_value, of_name, read, remembered, z_drive_node

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
    "laser_lines",
    "scan_field_rotation",
    "sequential_mode",
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
    "job_settings",
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
    """The stage position, shaped as :func:`api_reader.get_xy` gives it: metres and micrometres."""

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
    """Every job, in the sequence's order, shaped as :func:`log_reader.get_jobs` gives them.

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
    """The selected job, shaped as :func:`log_reader.get_selected_job` gives it.

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
    """Where z-wide is, in micrometres; named as :func:`log_reader.read_zwide_um`."""
    return read_z_um("z-wide")


def get_scan_status() -> str:
    """The scan status, named as :func:`api_reader.get_scan_status` names it (``eScanIdle`` ...).

    ``Acquire/AcquisitionState``; a state the reader does not know is
    ``Unknown``, which the routed readers count as no answer, as the CAM
    API's.
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


def get_pinhole() -> dict:
    """The pinhole: ``airy`` (as the job settings' ``pinholeAiry``: ``value``, ``min``, ``max``) and ``metres``."""
    found = settings(
        "the pinhole",
        {
            "value": (*HARDWARE, "PinholeAU"),
            "min": (*HARDWARE, "PinholeAUMin"),
            "max": (*HARDWARE, "PinholeAUMax"),
            "metres": (*HARDWARE, "Pinhole"),
        },
    )
    metres_open = metres(found.pop("metres"), "Pinhole")
    return {"airy": found, "metres": metres_open}


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
    """The scan field's width and height in metres, as :func:`api_reader.get_fov` gives them."""
    size = settings(
        "the field of view",
        {"x": (*IMAGE_SIZE, "ImageSizeX"), "y": (*IMAGE_SIZE, "ImageSizeY")},
    )
    return metres(size["x"], "ImageSizeX"), metres(size["y"], "ImageSizeY")


def get_z_stack() -> dict | None:
    """The z-stack, as the job settings' raw ``stack`` in micrometres; None without one (one section).

    ``begin``, ``end``, ``stepSize``, ``size``, ``sections`` and ``mode``,
    the drive that carries it (``Acquire/ZStackUseMode``, coded like the
    drives' ``ZUseMode``: 1 z-galvo, 2 z-wide). Only the case without a
    stack has been seen on the simulator so far.
    """
    found = settings(
        "the z-stack",
        {
            "begin": (*Z_STACK, "BeginValue"),
            "end": (*Z_STACK, "EndValue"),
            "stepSize": (*Z_STACK, "StepSize"),
            "sections": (*Z_STACK, "SectionsCount"),
            "mode": ("Acquire", "ZStackUseMode"),
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


#: ``SequentialMode`` values seen, by the name the job settings' ``sequentialMode`` gives them.
SEQUENTIAL_MODES = {0: "Line"}


def get_sequential_mode() -> str | None:
    """How sequential scanning switches settings, as the job settings' ``sequentialMode`` (``"Line"``).

    None when sequential scanning is off. Only line by line (0) has been
    seen on the simulator; another value is ``"unknown (<value>)"`` until
    one is read beside the log.
    """
    found = settings(
        "the sequential mode",
        {
            "enabled": (*SCANNING, "IsSequentialEnabled"),
            "mode": (*SCANNING, "SequentialMode"),
        },
    )
    if not found["enabled"]:
        return None
    return SEQUENTIAL_MODES.get(found["mode"], f"unknown ({found['mode']})")


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
    """The field of view at zoom 1, in metres: :func:`get_fov` times the zoom, as ``derived.base_fov_from_settings``."""
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
    except Exception as why:  # noqa: BLE001 -- the connection is suspect: drop it, raise the one error
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


def get_job_settings(job_name) -> dict:
    """The settings of *job_name*, whole, in the JSON the CAM API gives too.

    For every job, the selected one or not. For the selected job the z drives
    are where they truly are, from the state tree: the settings keep a
    drive's last set position, which freezes for the drive carrying a
    z-stack. Raises :class:`HardwareServerUnavailable` for a job LAS X does
    not have.
    """
    sequence = job_sequence()
    job = sequence.jobs.get(job_name)
    if job is None:
        raise HardwareServerUnavailable(f"no job {job_name!r} in LAS X's job sequence")
    settings = server_answer(
        f'<ATL Command="ATL_GetBlockApiInfoAsJsonString" Attribute="{job.block_id}"/>',
        f"the settings of {job_name!r}",
    )
    if sequence.selected_job == job_name:
        for drive, position in (settings.get("zPosition") or {}).items():
            if drive in Z_USE_MODE:
                position["position"] = read_z_um(drive)
    return settings


def get_hardware_info() -> dict:
    """The instrument, whole: LAS X's description of it, in the JSON the CAM API gives too."""
    return server_answer(
        '<Scanner Command="GetConfocalHardwareInfoAsJson"/>', "the hardware info"
    )
