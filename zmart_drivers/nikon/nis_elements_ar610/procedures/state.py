"""What the instrument is doing now and what can be changed: its state.

The *changeable* part is what ``set_state`` can apply: the objective, the
channel (NIS's optical configuration), the devices beyond the stage (the
light path, the port, the filters, the camera, the lights, the lasers and
the shutters, see ``devices.py``), the exposure, and the Perfect Focus
System with its offset. A state read with ``get_state`` can be handed back
to ``set_state`` as it is: only what differs from the microscope now is
changed. The *observed* part is a read-only report: the stage, the
objectives, the camera, the environment and the incubator, the limits in
force, what is set up, and the changes this session could not confirm.

One thing NIS-Elements cannot tell: which optical configuration is selected.
The driver remembers what it selected itself, and reports None until it has
selected one in this session.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ..dispatcher import limits as _gate
from . import changes, devices, keep_in_view
from . import readings as _readers

log = logging.getLogger(__name__)

#: The settings ``set_state`` can change, in the order it applies them: the
#: objective first, since it changes what a picture shows; then the channel,
#: which sets many devices at once; then the devices one by one, so what is
#: asked for them wins over the channel's own; the exposure after them, since
#: a channel brings its own; the PFS last, so it locks on the final objective.
CHANGEABLE = (
    "objective",
    "channel",
    *devices.DEVICES,
    "exposure_ms",
    "pfs_offset",
    "pfs",
)

_MAGNIFICATION = re.compile(r"(\d+(?:\.\d+)?)\s*[xX]\b")


def magnification_of(objective_name: str | None) -> float | None:
    """The magnification in an objective's name, such as 10 from "Plan Apo 10x"; None when it has none."""
    found = _MAGNIFICATION.search(objective_name or "")
    return float(found.group(1)) if found else None


def pixel_size_on_sample(
    handle, camera: dict | None, objective_name: str | None
) -> tuple[list | None, str]:
    """The pixel size on the sample, [x, y] in micrometres, and where the number comes from.

    NIS measures it only when a picture is taken (``Get_Calibration``), so
    the last capture's value is used when there is one. Before the first
    capture it is estimated from the camera's own pixel and the magnification
    in the objective's name; None when neither is known.
    """
    if handle.pixel_size_um:
        return [
            handle.pixel_size_um,
            handle.pixel_size_um,
        ], "measured by NIS-Elements at the last capture"
    magnification = magnification_of(objective_name)
    if camera and magnification:
        estimate = float(camera["pixel_um"]) / magnification
        return [
            estimate,
            estimate,
        ], "estimated from the camera pixel and the objective's magnification"
    return None, "unknown until a picture is taken"


def image_size_px(camera: dict | None) -> list | None:
    """The size of a picture, [width, height] in pixels: the camera's ROI, else its whole sensor."""
    if not camera:
        return None
    roi = camera.get("roi") or {}
    if (
        all(key in roi for key in ("left", "top", "right", "bottom"))
        and roi["right"] > roi["left"]
    ):
        return [int(roi["right"] - roi["left"]), int(roi["bottom"] - roi["top"])]
    return [int(camera["width_px"]), int(camera["height_px"])]


def setup_readiness(handle, limits: dict | None) -> dict:
    """Which of the once-per-microscope items are done, and what is missing, in words."""
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            "this microscope's limits are not published; the bundled defaults govern every move"
        )
    if not orientation.get("measured"):
        issues.append(
            "the camera-to-stage orientation is not measured; pictures are saved as the camera sees them"
        )
    if not calibration.get("measured_slots"):
        issues.append(
            "no objective pair is calibrated; a change of objective cannot keep the sample in view"
        )
    if handle.origin.get("objective") is None:
        issues.append("no origin is saved; positions are plain stage coordinates")
    if handle.data_location is None:
        issues.append(
            "no data location is set; nothing is saved unless a folder is given"
        )
    return {
        "ready": not issues,
        "issues": issues,
        "orientation": orientation,
        "calibration": calibration,
    }


def get_state(handle) -> dict:
    """The instrument state: ``changeable``, what ``set_state`` applies, and ``observed``, a read-only report.

    Every value from NIS-Elements is a fresh reading. A reading that fails is
    reported as None, never as a stop.
    """
    handle.require_open()
    client = handle.client
    position = _readers.get_xyz(client)
    objectives = _readers.get_objectives(client) or {"current": None, "objectives": {}}
    camera = _readers.get_camera(client)
    pfs = _readers.get_pfs(client)
    configurations = _readers.get_optical_configurations(client)
    limits = _gate.describe(client)
    found = devices.get_devices(client)
    current = objectives.get("current")
    objective_name = (
        objectives["objectives"].get(current) if current is not None else None
    )
    pixel_size, pixel_source = pixel_size_on_sample(handle, camera, objective_name)
    return {
        "changeable": {
            "objective": current,
            "channel": handle.channel,
            **{device: found.get(device) for device in devices.DEVICES},
            "exposure_ms": camera["exposure_ms"] if camera else None,
            "pfs_offset": _pfs_offset_state(pfs),
            "pfs": bool(pfs["on"]) if pfs else None,
        },
        "observed": {
            "vendor": "nikon",
            "microscope": handle.connection.get("microscope"),
            "nis_elements": handle.client.info.get("nis"),
            "bridge": handle.client.info.get("bridge"),
            "stage_um": position,
            "objectives": [
                [slot, name] for slot, name in sorted(objectives["objectives"].items())
            ],
            "objective_name": objective_name,
            "optical_configurations": configurations,
            "channel_known": handle.channel is not None,
            "camera": camera,
            "pfs": pfs,
            "lights": _readers.get_lights(client),
            "lasers": _readers.get_lasers(client),
            "filters": _readers.get_filters(client),
            "shutters": _readers.get_shutters(client),
            "light_path": _readers.get_light_path(client),
            "environment": _readers.get_environment(client),
            "incubator": _readers.get_incubator(client),
            "channel_settings": (
                _readers.get_optical_configuration(client, handle.channel)
                if handle.channel is not None
                else None
            ),
            # The controller's own keys: the image size and pixel size of these settings.
            "image_size_px": image_size_px(camera),
            "pixel_size_um": pixel_size,
            "pixel_size_source": pixel_source,
            "limits": limits,
            "setup": setup_readiness(handle, limits),
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def set_state(handle, changeable: dict) -> dict:
    """Apply the changeable settings, each read back, and answer ``{"applied", "unconfirmed"}``.

    A setting this microscope does not have raises ``ValueError``, naming
    the ones it has. A change that NIS refused raises ``RuntimeError``. A
    change that was sent but could not be confirmed is recorded and the
    others go on.
    """
    handle.require_open()
    unknown = sorted(set(changeable) - set(CHANGEABLE))
    if unknown:
        raise ValueError(
            f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}"
        )
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)
    asked_devices = {
        name: changeable[name]
        for name in devices.DEVICES
        if changeable.get(name) is not None
    }
    if asked_devices:
        # Every device name is checked before anything moves, objective included.
        devices.check_names(handle.client, asked_devices)
    for name in CHANGEABLE:
        if name not in changeable or changeable[name] is None:
            continue
        value = changeable[name]
        if name in _SIMPLE and _already(handle, name, value):
            continue
        if name == "objective":
            result = keep_in_view.set_objective(handle, _slot(value))
            applied["objective"] = _checked(
                handle,
                result,
                f"set_state: objective {value}",
                read=lambda r, v=value: _slot(v),
            )
        elif name == "channel":
            known = _readers.get_optical_configurations(handle.client) or []
            if value not in known:
                raise ValueError(
                    f"{value!r} is not an optical configuration of NIS-Elements; the configurations are {known}"
                )
            result = changes.select_optical_configuration(handle.client, value)
            _checked(handle, result, f"set_state: channel {value!r}")
            handle.channel = value
            applied["channel"] = value
        elif name == "exposure_ms":
            result = changes.set_exposure(handle.client, value)
            applied["exposure_ms"] = _checked(
                handle,
                result,
                f"set_state: exposure {value} ms",
                read=lambda r, v=value: (r.get("answered") or {}).get("exposure_ms", v),
            )
        elif name in devices.DEVICES:
            # The devices are applied together, in their own order, at the
            # first one reached; the others are then already done.
            if asked_devices:
                applied.update(devices.apply_devices(handle, asked_devices))
                asked_devices = {}
        elif name == "pfs_offset":
            if not isinstance(value, dict) or not set(value) <= {"offset", "dish"}:
                raise ValueError(
                    f"pfs_offset is given as {{'offset': ..., 'dish': 'glass' or 'plastic'}}, not {value!r}"
                )
            now = _pfs_offset_state(_readers.get_pfs(handle.client)) or {}
            differs = {
                k: v for k, v in value.items() if v is not None and v != now.get(k)
            }
            if differs:
                result = changes.set_pfs_offset(handle.client, **differs)
                _checked(handle, result, f"set_state: pfs offset {differs}")
                applied["pfs_offset"] = differs
        elif name == "pfs":
            if not isinstance(value, bool):
                raise ValueError(f"pfs is switched with True or False, not {value!r}")
            result = changes.set_pfs(handle.client, value)
            applied["pfs"] = _checked(
                handle, result, f"set_state: pfs {value}", read=lambda r, v=value: v
            )
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


#: The single settings that are skipped when the microscope already has them.
_SIMPLE = ("objective", "exposure_ms", "pfs")


def _already(handle, name: str, value) -> bool:
    """Whether the microscope already has *value* for *name*, so there is nothing to send."""
    client = handle.client
    if name == "objective":
        found = _readers.get_objectives(client) or {}
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and found.get("current") == value
        )
    if name == "exposure_ms":
        camera = _readers.get_camera(client)
        return bool(camera) and camera.get("exposure_ms") == value
    pfs = _readers.get_pfs(client)
    return bool(pfs) and isinstance(value, bool) and bool(pfs.get("on")) is value


def _pfs_offset_state(pfs: dict | None) -> dict | None:
    """The PFS offset and dish, in the shape ``set_state`` takes; None without a PFS."""
    if not pfs or not pfs.get("present") or pfs.get("offset") is None:
        return None
    state = {"offset": pfs["offset"]}
    if pfs.get("dish") is not None:
        state["dish"] = pfs["dish"]
    return state


def _slot(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"the objective is chosen by its nosepiece slot, a whole number from 1, not {value!r}"
        )
    return value


def _checked(handle, result: dict, what: str, *, read=None):
    """Raise when a change failed, note it when it was never confirmed, and answer what was applied."""
    if not result.get("success"):
        raise RuntimeError(f"{what} failed: {result.get('message')}")
    if result.get("confirmed") is False:
        handle.note_unconfirmed(what, result)
    return read(result) if read is not None else True
