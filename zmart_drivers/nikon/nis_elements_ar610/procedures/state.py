"""What the instrument is doing now and what can be changed: its state.

The *changeable* part is what ``set_state`` can apply: the channel (NIS's
optical configuration), the exposure, the objective and whether the Perfect
Focus System is on. The *observed* part is a read-only report: the stage,
the objectives, the camera, the limits in force, what is set up, and the
changes this session could not confirm.

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
from . import changes, keep_in_view
from . import readings as _readers

log = logging.getLogger(__name__)

#: The settings ``set_state`` can change, in the order it applies them: the
#: objective first, since it changes what a picture shows; the PFS last, so
#: it locks on the final objective.
CHANGEABLE = ("objective", "channel", "exposure_ms", "pfs")

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
    current = objectives.get("current")
    objective_name = (
        objectives["objectives"].get(current) if current is not None else None
    )
    pixel_size, pixel_source = pixel_size_on_sample(handle, camera, objective_name)
    return {
        "changeable": {
            "objective": current,
            "channel": handle.channel,
            "exposure_ms": camera["exposure_ms"] if camera else None,
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
    for name in CHANGEABLE:
        if name not in changeable or changeable[name] is None:
            continue
        value = changeable[name]
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
        elif name == "pfs":
            if not isinstance(value, bool):
                raise ValueError(f"pfs is switched with True or False, not {value!r}")
            result = changes.set_pfs(handle.client, value)
            applied["pfs"] = _checked(
                handle, result, f"set_state: pfs {value}", read=lambda r, v=value: v
            )
    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


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
