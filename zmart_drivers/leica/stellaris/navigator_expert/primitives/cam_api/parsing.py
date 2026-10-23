"""Parsers for the strings and dicts LAS X hands back, and what they mean.

Pure functions with no API-object knowledge: safe type conversion, format
strings ("512 x 512"), the micron-unit mojibake repair, and the tile
geometry derived from raw job settings. Shared by the readers, the command
wrappers, and acquisition — all the places LAS X text arrives.

What a reading means: a job by its name, the selected job, the field of
view, where a z-stack's slices sit and which drive makes it, and where a z
drive is (:func:`z_um_from_settings`, which for the drive carrying the job's
z-stack asks LAS X's hardware server, or the saved experiment it is handed).

``make_changeable_copy`` is the largest parser here: it transforms the raw
job-settings JSON from ``get_job_settings`` into a flat, navigable dict
(validated required keys; ``_beamRoute``/``_lineIndex``/``_index`` markers so
detectors, lasers, and filter wheels can be located without re-parsing; the
``stack``/``zPosition``/``time`` sections when present). The checks of
the changes in ``cam_api/writes.py`` read their readback through it.
"""

import logging
import re
import xml.etree.ElementTree as ET

from ..direct.client import HardwareServerUnavailable, read_hardware_z_um

log = logging.getLogger(__name__)


def _safe_float(val, default=None):
    """Convert val to float. Returns default on failure or None input."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _hw_get(d, key, default=None):
    """Safe dict/object getter for hardware info navigation."""
    try:
        if isinstance(d, dict):
            return d.get(key, default)
        return getattr(d, key, default)
    except Exception:
        return default


def parse_format(format_str):
    """Parse '512 x 512' into ``(512, 512)``."""
    parts = format_str.split("x")
    if len(parts) != 2:
        raise ValueError(f"Cannot parse format: '{format_str}'")
    return int(parts[0].strip()), int(parts[1].strip())


# =============================================================================
# Image / tile geometry
# =============================================================================


def normalize_unit_mojibake(text):
    """Map LAS X micron-unit spellings — including mojibake — to ASCII ``um``.

    LAS X emits size strings like ``'290.63 µm x 290.63 µm'``; on some
    delivery paths the UTF-8 ``µ`` (0xC2 0xB5) is re-decoded as Latin-1 and
    arrives as ``'Âµm'``. Normalize ``'Âµm'``, ``'µm'``, and Greek-mu
    ``'μm'`` to plain ``'um'`` so unit parsers see one shape. Canonical home
    of this vendor quirk — parsers must not re-handle ``'Â'`` themselves.
    """
    return (
        text.replace("Âµm", "um")  # 'Âµm': UTF-8 'µm' re-decoded as Latin-1
        .replace("µm", "um")  # 'µm': micro sign
        .replace("μm", "um")  # 'μm': Greek small letter mu
    )


def parse_tile_geometry(settings):
    """Extract complete tile geometry from raw job settings.

    Parses ``imageSize``, ``pixelSize``, and ``format`` and returns
    tile dimensions, pixel size, pixel count, and the bounding box
    of the current tile centered on the stage position.

    Args:
        settings: Raw job settings dict from ``get_job_settings()``.

    Returns:
        dict with keys::

            tile_w_um, tile_h_um    — tile dimensions in µm
            pixel_w_nm, pixel_h_nm  — pixel size in nm
            pixel_w_um, pixel_h_um  — pixel size in µm
            pixels_x, pixels_y      — pixel count per axis
            bbox                    — {x_min, x_max, y_min, y_max} in µm

    Raises:
        ValueError: If ``imageSize`` is missing or unparseable.
    """

    def _parse_dim_um(raw):
        """Parse 'W unit x H unit' → (w_um, h_um). Supports mm, µm, nm."""
        raw = normalize_unit_mojibake(raw)
        if "mm" in raw:
            scale = 1000.0
        elif "nm" in raw:
            scale = 0.001
        else:  # µm (default)
            scale = 1.0
        cleaned = re.sub(r"[nmu]*m", "", raw)
        parts = cleaned.split("x")
        if len(parts) != 2:
            return None, None
        try:
            return float(parts[0].strip()) * scale, float(parts[1].strip()) * scale
        except ValueError:
            return None, None

    # ── tile size (µm) ──
    raw_size = settings.get("imageSize", "")
    tile_w, tile_h = _parse_dim_um(raw_size)
    if tile_w is None:
        raise ValueError(f"Cannot parse imageSize: '{raw_size}'")

    # ── pixel size (µm, converted to nm for output) ──
    raw_px = settings.get("pixelSize", "")
    pixel_w_um, pixel_h_um = _parse_dim_um(raw_px)
    pixel_w_nm = pixel_w_um * 1000 if pixel_w_um is not None else None
    pixel_h_nm = pixel_h_um * 1000 if pixel_h_um is not None else None

    # ── format (pixel count) ──
    raw_fmt = settings.get("format", "")
    fmt_parts = raw_fmt.split("x")
    if len(fmt_parts) == 2:
        pixels_x = int(fmt_parts[0].strip())
        pixels_y = int(fmt_parts[1].strip())
    else:
        pixels_x = None
        pixels_y = None

    # ── bounding box (µm) ──
    stage = settings.get("xyStage", {})
    cx = float(stage.get("posX", 0))
    cy = float(stage.get("posY", 0))
    bbox = {
        "x_min": cx - tile_w / 2,
        "x_max": cx + tile_w / 2,
        "y_min": cy - tile_h / 2,
        "y_max": cy + tile_h / 2,
    }

    return {
        "tile_w_um": tile_w,
        "tile_h_um": tile_h,
        "pixel_w_nm": pixel_w_nm,
        "pixel_h_nm": pixel_h_nm,
        "pixel_w_um": pixel_w_um,
        "pixel_h_um": pixel_h_um,
        "pixels_x": pixels_x,
        "pixels_y": pixels_y,
        "bbox": bbox,
    }


# =============================================================================
# Raw job settings -> flat navigable dict
# =============================================================================

_REQUIRED_SETTINGS_KEYS = ["zoom", "scanSpeed", "activeSettings"]


def stack_from_settings(settings):
    """The z-stack section of raw job settings, normalized — or None.

    THE one stack parser: ``make_changeable_copy`` uses it for its
    ``stack`` section, and OME metadata generation reads it directly (a
    degraded settings dict can fail the full schema guard while its
    stack is still perfectly usable). Returns None when the settings
    carry no stack dict.
    """
    if not isinstance(settings, dict):
        return None
    stack_raw = settings.get("stack")
    if not isinstance(stack_raw, dict):
        return None
    begin = _safe_float(stack_raw.get("begin"))
    end = _safe_float(stack_raw.get("end"))
    step_size = _safe_float(stack_raw.get("stepSize"))
    size = _safe_float(stack_raw.get("size"))
    sections = stack_raw.get("sections")  # number of z-planes
    # Raw LAS X names the drive "mode"; a normalised copy coming through
    # again already says "zDrive". Accepting both keeps this idempotent.
    z_drive = stack_raw.get("mode", stack_raw.get("zDrive"))  # e.g. "z-galvo", "z-wide"
    # Compute size from begin/end if not provided
    if size is None and begin is not None and end is not None:
        size = abs(end - begin)
    return {
        "begin": begin,
        "end": end,
        "stepSize": step_size,
        "size": size,
        "sections": sections,
        "zDrive": z_drive,
    }


def make_changeable_copy(settings):
    """Transform raw job settings JSON into a flat, navigable dict.

    Validates that required keys exist. Raises ValueError on schema mismatch.
    """
    if settings is None:
        return None

    # Schema validation — fail loud on missing required keys
    missing = [k for k in _REQUIRED_SETTINGS_KEYS if k not in settings]
    if missing:
        raise ValueError(
            f"Job settings missing required keys: {missing}. "
            f"LAS X version mismatch or corrupt settings JSON. "
            f"Available keys: {list(settings.keys())}"
        )

    ch = {}

    # Direct fields
    ch["zoom"] = settings.get("zoom", {"current": None})
    ch["scanSpeed"] = settings.get("scanSpeed", {"value": None, "isResonant": False})
    ch["scanMode"] = settings.get("scanMode", "")
    ch["sequentialMode"] = settings.get("sequentialMode", "")
    ch["scanFieldRotation"] = settings.get("scanFieldRotation", {"value": 0.0})
    ch["format"] = settings.get("format", "")
    ch["objective"] = settings.get("objective", {"name": "", "magnification": 0})

    # Active settings
    active = []
    for s in settings.get("activeSettings", []):
        entry = {
            "_index": s.get("index", s.get("_index", 0)),
            "_name": s.get("name", s.get("_name", "")),
            "frameAccumulation": s.get("frameAccumulation", 1),
            "frameAverage": s.get("frameAverage", 1),
            "lineAccumulation": s.get("lineAccumulation", 1),
            "lineAverage": s.get("lineAverage", 1),
            "pinholeAiry": s.get("pinholeAiry", {"value": 1.0}),
        }
        # Detectors with _beamRoute
        detectors = []
        for d in s.get("activeDetectors", []):
            det = dict(d)
            det["_beamRoute"] = d.get("beamRoute", d.get("_beamRoute", ""))
            detectors.append(det)
        entry["activeDetectors"] = detectors

        # Laser lines with _beamRoute and _lineIndex
        lasers = []
        for laser_raw in s.get("activeLaserLines", []):
            laser = dict(laser_raw)
            laser["_beamRoute"] = laser_raw.get(
                "beamRoute", laser_raw.get("_beamRoute", "")
            )
            laser["_lineIndex"] = laser_raw.get(
                "lineIndex", laser_raw.get("_lineIndex", 0)
            )
            lasers.append(laser)
        entry["activeLaserLines"] = lasers

        # Filter wheels
        if "filterWheels" in s:
            fws = []
            for fw_raw in s["filterWheels"]:
                fw = dict(fw_raw)
                fw["_beamRoute"] = fw_raw.get("beamRoute", fw_raw.get("_beamRoute", ""))
                fws.append(fw)
            entry["filterWheels"] = fws

        active.append(entry)
    ch["activeSettings"] = active

    # Z-stack — only include if scan mode involves Z
    scan_mode = ch.get("scanMode", "")
    has_z = "z" in scan_mode.lower() if scan_mode else False

    stack = stack_from_settings(settings)
    if stack is not None:
        ch["stack"] = stack
    elif has_z:
        ch["stack"] = {
            "begin": None,
            "end": None,
            "stepSize": None,
            "size": None,
            "sections": None,
            "zDrive": None,
        }

    # Z position (z-galvo / z-wide readback)
    zp_raw = settings.get("zPosition")
    if zp_raw and isinstance(zp_raw, dict):
        ch["zPosition"] = {}
        for key, entry in zp_raw.items():
            # Raw LAS X nests the value as {"position": v}; a bare number is
            # a normalised copy coming through again (the confirmation holds
            # one), so accepting both keeps this idempotent.
            ch["zPosition"][key] = (
                _safe_float(entry.get("position"))
                if isinstance(entry, dict)
                else _safe_float(entry)
            )

    # Time series
    time_raw = settings.get("time")
    if time_raw and isinstance(time_raw, dict):
        ch["time"] = dict(time_raw)

    return ch

def saved_z_um(lrp_text: str, job_name: str, drive: str) -> float:
    """*job_name*'s ``ZPosition`` for *drive* (µm), from the text of an experiment LAS X saved (``.lrp``).

    Each job's Master setting holds one ``ZPosition``, in metres, for the drive
    its ``ZUseModeName`` names (``"z-galvo"`` or ``"z-wide"``): the position
    last commanded through the job. A job that is not there, or whose
    position is for the other drive, is refused rather than answered under
    the wrong label.
    """
    for block in ET.fromstring(lrp_text).iter("LDM_Block_Sequence_Block"):
        sequential = block.find(".//LDM_Block_Sequential")
        if sequential is None or sequential.get("BlockName") != job_name:
            continue
        master = block.find(".//LDM_Block_Sequential_Master/ATLConfocalSettingDefinition")
        held = None if master is None else master.get("ZUseModeName")
        if held != drive:
            raise RuntimeError(
                f"the saved experiment holds {job_name!r}'s ZPosition for {held}, not for {drive!r}"
            )
        return float(master.get("ZPosition")) * 1e6
    raise RuntimeError(f"the saved experiment has no job {job_name!r}")


def job_by_name(jobs, job_name):
    """Return a job dict from a job list by LAS X job name."""
    if not jobs:
        return None
    for job in jobs:
        if job.get("Name") == job_name:
            return job
    return None


def selected_job(jobs):
    """Return the single selected job dict, or None if absent/ambiguous."""
    if not jobs:
        return None
    selected = [job for job in jobs if job.get("IsSelected") is True]
    return selected[0] if len(selected) == 1 else None


def fov_from_settings(settings):
    """Return current field of view in metres from LAS X job settings."""
    if not settings:
        return None
    try:
        geo = parse_tile_geometry(settings)
        return (geo["tile_w_um"] * 1e-6, geo["tile_h_um"] * 1e-6)
    except (KeyError, TypeError, ValueError):
        return None


def base_fov_from_settings(settings):
    """Return zoom-1 field of view in metres from LAS X job settings."""
    if not settings:
        return None
    try:
        geo = parse_tile_geometry(settings)
        zoom_info = settings.get("zoom") or {}
        # Sub-unity zoom is legal hardware state (range starts at 0.75x);
        # only None/0 fall back to 1. Clamping <1 to 1 corrupted the base
        # FOV by up to ~33% at 0.75x.
        current_zoom = float(zoom_info.get("current", 1) or 1)
        return (
            geo["tile_w_um"] * 1e-6 * current_zoom,
            geo["tile_h_um"] * 1e-6 * current_zoom,
        )
    except (KeyError, TypeError, ValueError):
        return None


def settings_geometry_ready(settings):
    """Return True when job settings carry a populated ``imageSize``.

    LAS X transiently dumps settings with a blank ``imageSize`` while the
    engine repopulates geometry after a zoom or format change. Both reader
    backends use this to skip those not-yet-fresh dumps.
    """
    return bool(settings.get("imageSize"))


def stack_slices_um(settings, expected: int) -> list[float] | None:
    """Where each slice of the job's z-stack sits, in um of the drive that made it.

    ``begin``/``end``/``sections``, evenly spaced, which keeps a reversed
    stack. ``stepSize`` is not used: LAS X rounds it for display. The
    slices are positions of the stack's own drive (:func:`stack_drive`):
    absolute z-wide for a z-wide stack, galvo heights for a z-galvo one.

    ``None`` when the job takes no stack, or when what it says does not
    describe the *expected* planes that came back -- a guessed position would
    place a picture somewhere nobody imaged.
    """
    try:
        stack = (make_changeable_copy(settings) or {}).get("stack")
    except Exception:  # noqa: BLE001 — a settings shape we cannot read says nothing
        stack = None
    raw = settings.get("stack") if isinstance(settings, dict) else None
    if not isinstance(stack, dict) or any(stack.get(k) is None for k in _STACK_NEEDS):
        stack = raw if isinstance(raw, dict) else None
    if not isinstance(stack, dict) or any(stack.get(k) is None for k in _STACK_NEEDS):
        return None

    try:
        begin, end = float(stack["begin"]), float(stack["end"])
        sections = int(stack["sections"])
    except (TypeError, ValueError):
        return None
    if sections != expected or sections < 1:
        return None
    if sections == 1:
        return [begin]
    step = (end - begin) / (sections - 1)
    return [begin + step * index for index in range(sections)]


def stack_drive(settings) -> str | None:
    """Which z drive makes the job's stack: ``"z-wide"``, ``"z-galvo"``, or None when unsaid."""
    try:
        stack = (make_changeable_copy(settings) or {}).get("stack") or {}
    except Exception:  # noqa: BLE001 — a settings shape we cannot read says nothing
        stack = {}
    raw = (settings.get("stack") if isinstance(settings, dict) else None) or {}
    drive = stack.get("zDrive") or raw.get("mode")
    return drive if drive in ("z-wide", "z-galvo") else None


#: What a stack block must carry before it can say where a slice was taken.
_STACK_NEEDS = ("begin", "end", "sections")


def z_um_from_settings(settings, key, *, saved=None, direct=True):
    """Return one z drive's position (µm) for the job whose settings these are.

    The settings' ``zPosition`` is the job's stored z reference. LAS X keeps
    it current for a drive that carries no z-stack and stops refreshing it
    for the drive the job's stack is on (measured on the simulator
    2026-08-25/26). So for that one drive the value here is stale by
    construction. With the direct reader (*direct*, the reading settings'
    ``z_mode``), this function reads it from LAS X's hardware server, which
    reports where the drive truly is, in milliseconds. Without it, or when the
    hardware server cannot be reached, it reads the experiment LAS X saves:
    *saved* (``saved(key)`` in µm; ``reads.read_saved_z_um`` for the job, see
    ``procedures/readings.saved_z``), the position last commanded through the
    job. The free drive costs nothing extra: the decision is a lookup on the
    ``stack`` block already in *settings*. A stacked drive that neither
    answers is refused rather than answered with the stale number.

    Canonical home of the z-readback shape quirk: LAS X sometimes nests the
    value as ``{'position': ...}`` rather than a bare float, so after
    flattening the API JSON via :func:`make_changeable_copy` and reading
    ``zPosition[key]``, the dict-shape guard unwraps ``'position'``. Raises
    ``RuntimeError`` when ``zPosition`` or the per-drive value is
    unavailable (almost always means the job is not selected or the
    LAS X version does not expose Z readback in this shape).
    """
    ch = make_changeable_copy(settings)
    if ch and (ch.get("stack") or {}).get("zDrive") == key:
        unreachable = "the direct reader is not used"
        if direct:
            try:
                return read_hardware_z_um(key)
            except HardwareServerUnavailable as why:
                unreachable = f"LAS X's hardware server could not be reached ({why})"
        if saved is None:
            raise RuntimeError(
                f"{key} carries the job's z-stack and {unreachable}; reading the saved "
                f"experiment needs the client and the job"
            )
        log.info("%s carries the z-stack and %s; reading the saved experiment", key, unreachable)
        try:
            return saved(key)
        except RuntimeError as why:
            raise RuntimeError(
                f"{key} carries the job's z-stack, so its settings value is stale; "
                f"{unreachable}, and the saved experiment did not answer ({why}) - no current Z available"
            ) from why
    if not ch or "zPosition" not in ch:
        raise RuntimeError("zPosition not in job settings - LAS X version mismatch?")
    val = ch["zPosition"].get(key)
    if isinstance(val, dict):
        val = val.get("position")
    if val is None:
        raise RuntimeError(f"{key} readback missing; got {ch['zPosition']!r}")
    return float(val)


def zwide_um_from_settings(settings, *, saved=None):
    """Return the z-wide position (µm); see :func:`z_um_from_settings`."""
    return z_um_from_settings(settings, "z-wide", saved=saved)
