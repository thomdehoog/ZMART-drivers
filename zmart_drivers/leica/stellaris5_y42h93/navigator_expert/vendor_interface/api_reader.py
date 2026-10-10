"""
Read functions.
===============
Read-only queries against the LAS X Python API: scan status, connection
health, job settings, hardware info, XY stage position, job list, and
LAS X application settings (parsed from the on-disk XML file).

Each query makes ONE **request** (``_request``):

    1. Flush the data model to a sentinel (None or NaN) so fresh data
       can be detected.
    2. Write the command name to ``PyApiCommand.Model.Command``.
    3. Deliver via ``PyApiCommand.UpdateAwaitReceipt()`` so the request is
       received before checking for its answer.
    4. Check the dedicated data-object model every ``ANSWER_POLL_S`` until
       it moves from the sentinel to a real value, until the caller's
       deadline, or until the caller stops watching.

Asking again is not done here. The routed readers in ``dispatcher.read``
follow the one rule in ``dispatcher.tuning``: they request the read again
every ``POLL_S`` while an answer does not count, and at the next window when
a request got no answer.

Using ``UpdateAwaitReceipt`` on ``PyApiCommand`` is cheap (1-4 ms) and
prevents requests from being silently dropped when sent in rapid
succession (e.g. during ``confirm_move_xy``).

``get_lasx_settings`` is the exception: it reads the Navigator Expert
XML configuration file directly from disk (no API round-trip).

These functions do NOT modify microscope state.

Dependency direction:
    - Imports: derived readers, stdlib (json, logging, os, time, xml).
    - Imported by: ``prechecks`` (scan status polling), ``confirmations``
      (readback via ``get_job_settings`` and ``get_xy``),
      ``commands`` (early-exit checks, post-processing readbacks),
      ``__init__`` (re-export).
"""

import json
import logging
import math
import os
import time
import xml.etree.ElementTree as ET

from ..actions import derived
from ..dispatcher import tuning
from .delivery import deliver

log = logging.getLogger(__name__)


# =============================================================================
# Scan status
# =============================================================================


def get_scan_status(client):
    """Read current scanner state string from PyApiStatus."""
    try:
        return str(client.PyApiStatus.Model.ScanStatus)
    except Exception:
        return "Unknown"


# =============================================================================
# Connection health
# =============================================================================


def ping(client):
    """Lightweight connection check. Returns True if LAS X responds."""
    try:
        if deliver(client.PyApiPing, label="ping"):
            return True
        # Transport failure: try fallback
    except Exception:
        pass
    # Fallback: try reading scan status
    try:
        _ = client.PyApiStatus.Model.ScanStatus
        return True
    except Exception:
        return False


# =============================================================================
# Read functions
# =============================================================================

# Verdicts a reader's ``validate`` hook can return to ``_request``.
_ACCEPT, _STALE = "accept", "stale"


def _never_stop():
    return False


def _request(
    client,
    *,
    command,
    flush,
    read,
    validate=None,
    label=None,
    context="",
    deadline=None,
    should_stop=None,
):
    """One CAM read request: flush the sentinel, deliver, check for the answer.

    The vendor quirk this works around: LAS X delivers read results by
    writing into a shared data-object model with no request/response
    correlation, so fresh data is only detectable as a transition away from a
    sentinel flushed before the request -- and a delayed reply to an
    *earlier* request can still land after the flush. Where the payload
    carries a correlating field, the reader's ``validate`` hook marks such a
    stray ``_STALE`` and the check goes on (see ``get_job_settings``).

    This makes ONE request. Asking again is the caller's business: the routed
    readers in ``dispatcher.read`` request the read again every ``POLL_S``
    while an answer does not count, and at the next window when there was
    no answer. Delivery and the check for the answer both end at *deadline*
    (``time.monotonic()``; default one ``WINDOW_S`` from now) or as soon as
    ``should_stop()`` says the caller has stopped watching.

    Returns:
        The answered value, or None when there was no answer.
    """
    label = label or command
    deadline = time.monotonic() + tuning.WINDOW_S if deadline is None else deadline
    should_stop = should_stop or _never_stop
    try:
        flush(client)
        client.PyApiCommand.Model.Command = ""
        client.PyApiCommand.Model.Command = command
        if not client.PyApiCommand.UpdateAwaitReceipt(_remaining(deadline)):
            log.debug("%s: request not received%s", label, context)
            return None
        while not should_stop():
            value = read(client)
            if value is not None:
                verdict = _ACCEPT if validate is None else validate(value)
                if verdict is _ACCEPT:
                    return value
            if time.monotonic() >= deadline:
                break
            time.sleep(tuning.ANSWER_POLL_S)
        log.debug("%s: no answer%s", label, context)
    except Exception as e:
        log.debug("%s failed: %s", label, e)
    return None


def _remaining(deadline):
    """Seconds left until *deadline*, for a CAM call that takes a timeout."""
    return max(0.0, deadline - time.monotonic())


def get_job_settings(client, job_name, *, deadline=None, should_stop=None):
    """Request the full job settings JSON from LAS X, once.

    Uses the dual-dispatch pattern: commits the JobName parameter on the
    dedicated API object, then requests GetJobSettingsByName via the command
    channel and checks until data arrives. A JobName handover LAS X does not
    receive makes this request unanswered, and the caller asks again.
    """

    def flush(c):
        c.PyApiGetJobSettingsByName.Model.JobName = job_name
        if not c.PyApiGetJobSettingsByName.UpdateAwaitReceipt(_remaining(deadline_at)):
            raise RuntimeError(f"JobName '{job_name}' not received")
        c.PyApiGetJobSettingsByName.Model.Settings = None

    def read(c):
        raw = c.PyApiGetJobSettingsByName.Model.Settings
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw

    def validate(parsed):
        # Correlate the response with *this* request: a delayed response for
        # an earlier job can land after our flush and would otherwise be
        # returned as this job's settings.
        if (
            isinstance(parsed, dict)
            and parsed.get("jobName") is not None
            and parsed.get("jobName") != job_name
        ):
            log.debug(
                "get_job_settings: stale response for '%s' while asking for '%s'",
                parsed.get("jobName"),
                job_name,
            )
            return _STALE
        return _ACCEPT

    deadline_at = time.monotonic() + tuning.WINDOW_S if deadline is None else deadline
    return _request(
        client,
        command="GetJobSettingsByName",
        flush=flush,
        read=read,
        validate=validate,
        label="get_job_settings",
        context=f" for '{job_name}'",
        deadline=deadline_at,
        should_stop=should_stop,
    )


def get_hardware_info(client, *, deadline=None, should_stop=None):
    """Request confocal hardware info from LAS X, once."""

    def flush(c):
        try:
            c.PyApiGetConfocalHardwareInfo.Model.HWInfo = None
        except Exception:
            pass

    def read(c):
        raw = c.PyApiGetConfocalHardwareInfo.Model.HWInfo
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw

    return _request(
        client,
        command="GetConfocalHardwareInfo",
        flush=flush,
        read=read,
        label="get_hardware_info",
        deadline=deadline,
        should_stop=should_stop,
    )


def get_xy(client, *, deadline=None, should_stop=None):
    """Request the current XY stage position, once.

    Flushes the model to NaN, requests GetXY, then checks until fresh
    (non-NaN) data arrives.

    Returns:
        dict with x/y in meters and microns, or None when unanswered.
    """

    def flush(c):
        c.PyApiGetXY.Model.XPosition = float("nan")
        c.PyApiGetXY.Model.YPosition = float("nan")

    def read(c):
        x = c.PyApiGetXY.Model.XPosition
        y = c.PyApiGetXY.Model.YPosition
        if math.isnan(x) or math.isnan(y):
            return None
        return {
            "x": x,
            "y": y,
            "x_um": x * 1e6,
            "y_um": y * 1e6,
        }

    return _request(
        client,
        command="GetXY",
        flush=flush,
        read=read,
        label="get_xy",
        deadline=deadline,
        should_stop=should_stop,
    )


def get_jobs(client, *, deadline=None, should_stop=None):
    """Request the list of jobs and their selection status, once."""

    def flush(c):
        try:
            c.PyApiGetJobsInformation.Model.Jobs = None
        except Exception:
            pass

    def read(c):
        raw = c.PyApiGetJobsInformation.Model.Jobs
        if raw is None:
            return None
        return json.loads(raw) if isinstance(raw, str) else raw

    return _request(
        client,
        command="GetJobsInformation",
        flush=flush,
        read=read,
        label="get_jobs",
        deadline=deadline,
        should_stop=should_stop,
    )


def get_job_by_name(client, job_name, **kwargs):
    """Return the metadata dict for a single job, or None if not found.

    Convenience wrapper around get_jobs(). All keyword arguments are
    forwarded to get_jobs().
    """
    return derived.job_by_name(get_jobs(client, **kwargs), job_name)


def get_selected_job(client, **kwargs):
    """Return the metadata dict for the currently selected job, or None.

    Convenience wrapper around get_jobs(). All keyword arguments are
    forwarded to get_jobs().
    """
    return derived.selected_job(get_jobs(client, **kwargs))


def get_fov(client, job_name, **kwargs):
    """Return the scan field size for a job in metres.

    Queries ``get_job_settings`` and parses ``imageSize``.

    Args:
        client: Live LAS X CAM client.
        job_name: Job name to query.
        **kwargs: Forwarded to ``get_job_settings``.

    Returns:
        ``(width_m, height_m)`` tuple, or ``None`` on failure.
    """
    settings = get_job_settings(client, job_name, **kwargs)
    if not settings:
        log.error("get_fov: no settings for job '%s'", job_name)
        return None
    value = derived.fov_from_settings(settings)
    if value is None:
        log.error("get_fov: cannot parse FOV for '%s'", job_name)
    return value


def get_base_fov(client, job_name, **kwargs):
    """Return the objective's full field of view (at zoom 1) in metres.

    Reads the current FOV and zoom from the API, then scales back to
    zoom 1.  This is a fundamental property of the objective and scan
    configuration; it does not change with zoom.

    Args:
        client: Live LAS X CAM client.
        job_name: Job name to query.
        **kwargs: Forwarded to ``get_job_settings``.

    Returns:
        ``(width_m, height_m)`` tuple at zoom 1, or ``None`` on failure.
    """
    settings = get_job_settings(client, job_name, **kwargs)
    if not settings:
        log.error("get_base_fov: no settings for job '%s'", job_name)
        return None
    value = derived.base_fov_from_settings(settings)
    if value is None:
        log.error("get_base_fov: cannot parse FOV for '%s'", job_name)
    return value


# =============================================================================
# LAS X application settings (from XML on disk)
# =============================================================================

_SETTINGS_PATH = os.path.join(
    os.getenv("APPDATA", ""),
    "Leica Microsystems",
    "LAS X",
    "MatrixScreener6",
    "User_0",
    "Settings",
    "{Settings}MatrixScreenerM6.xml",
)


def _xml_text(parent, tag, default=None):
    """Get text content of a child element, or default."""
    el = parent.find(tag)
    if el is not None and el.text and el.text.strip():
        return el.text.strip()
    return default


def _xml_bool(parent, tag, default=False):
    """Get boolean from child element text ('true'/'false')."""
    val = _xml_text(parent, tag)
    if val is None:
        return default
    return val.lower() == "true"


def _xml_int(parent, tag, default=0):
    """Get int from child element text."""
    val = _xml_text(parent, tag)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


def get_lasx_settings(settings_path=None):
    """Read LAS X MatrixScreener / Navigator Expert settings from disk.

    Only parses the sections consumed by the driver: data export,
    export formats, and image orientation.

    Args:
        settings_path: Override path to the XML file. None uses the
            default APPDATA location.

    Returns:
        dict with sections, or None if the file doesn't exist.
    """
    path = settings_path or _SETTINGS_PATH
    if not os.path.exists(path):
        log.warning("LAS X settings file not found: %s", path)
        return None

    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        # Readers never raise: a corrupt/partially-written settings file
        # fails closed like a missing one.
        log.warning("LAS X settings file unparseable: %s (%s)", path, exc)
        return None
    root = tree.getroot()

    nav = root.find("SettingsNavigatorExpert")
    result = {}

    if nav is not None:
        exporter = nav.find("SettingsDataExporter")
        if exporter is not None:
            result["export"] = {
                "auto_export": _xml_bool(exporter, "ExportDataAutomatically"),
                "delete_after_export": _xml_bool(exporter, "DeleteExportedExperiments"),
                "auto_save": _xml_bool(exporter, "UseAutoSave"),
                "save_lif_in_folder": _xml_bool(exporter, "SaveLIFInExperimentFolder"),
            }

        fmt = nav.find("ExportFileFormats")
        if fmt is not None:
            result["export_formats"] = {
                "ome_tif": _xml_bool(fmt, "AsOmeTifFile"),
                "multipage_ome_tif": _xml_bool(fmt, "AsMultiPageOmeTifFile"),
                "multipage_tif": _xml_bool(fmt, "AsMultiPageTifFile"),
                "tif": _xml_bool(fmt, "AsTifFile"),
                "imagej_tif": _xml_bool(fmt, "AsImageJTifFile"),
                "bmp": _xml_bool(fmt, "AsBitmapFile"),
                "jpg": _xml_bool(fmt, "AsJpgFile"),
                "png": _xml_bool(fmt, "AsPngFile"),
                "lif": _xml_bool(fmt, "AsLifFile"),
                "xlef": _xml_bool(fmt, "AsXLefFile"),
                "screenshot": _xml_bool(fmt, "AsScreenShot"),
                "combine_mosaics": _xml_bool(fmt, "CombineMosaics"),
                "enable_edof": _xml_bool(fmt, "EnableEDOF"),
                "compression": _xml_bool(fmt, "EnableImageCompression"),
                "compression_value": _xml_int(fmt, "ImageCompressionValue"),
            }

        img_exp = nav.find("SettingsExportedImage")
        if img_exp is not None:
            result["image_orientation"] = {
                "enable_transform": _xml_bool(img_exp, "EnableImageTransformation"),
                "transformation": _xml_text(img_exp, "ImageTransformation", "RIGHTTOP"),
            }

    return result
