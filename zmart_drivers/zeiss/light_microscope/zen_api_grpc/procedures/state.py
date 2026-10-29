"""What the instrument is doing now and what can be changed: its state.

The *changeable* part is what ``set_state`` can change: the loaded
experiment (ZEN's unit of imaging settings: channels, exposure, z-stack,
tiles, all at once) and the objective in place. The *observed* part is a
report: the objectives on the changer, the experiments ZEN can load, where
ZEN writes its images, the image size and pixel size, the limits in force,
what is set up, and the changes that were never confirmed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ..dispatcher import limits as _gate
from . import changes, keep_in_view
from . import readings as _readers

log = logging.getLogger(__name__)

#: What set_state can change.
CHANGEABLE = ("experiment", "objective")


def frame_now(handle, objective: dict | None) -> dict | None:
    """The image size (pixels) and pixel size (micrometres) a capture has now, and where the numbers come from.

    The ZEN API does not report them before a capture, so they come from
    the last capture of this session when there was one, else from what
    the orientation setup measured, scaled by the objective's magnification
    (a 20x objective halves the pixel size of a 10x). None when neither is
    known yet.
    """
    known = handle.last_frame or handle.frame
    if not known:
        return None
    size = [int(v) for v in known["image_size_px"]]
    pixel = [float(v) for v in known["pixel_size_um"]]
    measured_at = known.get("magnification")
    now = (objective or {}).get("magnification")
    scaled = False
    if measured_at and now and measured_at != now:
        pixel = [value * float(measured_at) / float(now) for value in pixel]
        scaled = True
    if handle.orientation is not None and handle.orientation.swaps_axes:
        size, pixel = size[::-1], pixel[::-1]
    return {
        "image_size_px": size,
        "pixel_size_um": pixel,
        "from": known.get("from", "the orientation setup")
        + (" scaled by the objective's magnification" if scaled else ""),
    }


def setup_readiness(handle, objective: dict | None, limits: dict | None) -> dict:
    """Whether the setup is complete enough for an experiment, and what is missing, in words."""
    orientation = dict(handle.orientation_info or {})
    calibration = dict(handle.calibration_info or {})
    position = (objective or {}).get("position")
    origin_position = (handle.origin.get("objective") or {}).get("position")
    measured = calibration.get("measured_positions", [])
    issues = []
    if not limits or limits.get("is_fallback") or limits.get("source") != "machine":
        issues.append(
            "this microscope's limits are not published; measure them with driver_setup/limits/notebooks/set_limits.ipynb"
        )
    if not orientation.get("measured"):
        issues.append(
            "the image-to-stage orientation is not measured; run driver_setup/orientation/notebooks/set_orientation.ipynb"
        )
    if not calibration.get("loaded"):
        issues.append("the objective calibration could not be loaded")
    elif position is None:
        issues.append("which objective is in place could not be read")
    elif (
        origin_position is not None
        and position != origin_position
        and position not in measured
    ):
        issues.append(
            f"objective position {position} is not calibrated against the origin's objective {origin_position}; "
            "run driver_setup/optical_calibration/notebooks/calibrate_objective_pair.ipynb"
        )
    if handle.frame is None and handle.last_frame is None:
        issues.append(
            "the image size and pixel size are not known yet; the orientation setup measures them"
        )
    return {
        "ready": not issues,
        "issues": issues,
        "objective_position": position,
        "origin_objective_position": origin_position,
        "orientation": orientation,
        "calibration": calibration,
    }


def get_state(handle) -> dict:
    """The instrument's state: ``changeable`` (the experiment and the objective) and ``observed`` (the report)."""
    handle.require_open()
    objective = _readers.get_objective(handle.client) or {}
    objectives = _readers.get_objectives(handle.client) or []
    experiments = _readers.get_experiments(handle.client)
    limits = _gate.describe(handle.client)
    frame = frame_now(handle, objective)
    experiment = handle.experiment or {}
    return {
        "changeable": {
            "experiment": experiment.get("name"),
            "objective": objective.get("position"),
        },
        "observed": {
            "vendor": "zeiss",
            "microscope": handle.connection.get("microscope"),
            "zen_api": (handle.client.runtime or {}).get("zen_api_version"),
            "experiment": dict(experiment),
            "experiments": experiments,
            "objective_in_place": dict(objective),
            "objectives": objectives,
            "image_output_path": handle.image_output_path,
            # The controller's own keys: the image size and pixel size of a capture now.
            "image_size_px": frame["image_size_px"] if frame else None,
            "pixel_size_um": frame["pixel_size_um"] if frame else None,
            "frame_from": frame["from"] if frame else None,
            "limits": limits,
            "setup": setup_readiness(handle, objective, limits),
            "unconfirmed": list(handle.unconfirmed),
        },
    }


def set_state(handle, state: dict) -> dict:
    """Apply the changeable part: load the experiment, change the objective; report what stuck.

    The experiment must be one ZEN can load, and the objective one on the
    changer; anything else is a wrong request and raises ``ValueError``. A
    change ZEN refused raises ``RuntimeError``. A change that was sent but
    never seen is recorded and the work goes on.
    """
    handle.require_open()
    asked: dict[str, Any] = dict((state or {}).get("changeable") or {})
    unknown = sorted(set(asked) - set(CHANGEABLE))
    if unknown:
        raise ValueError(
            f"no changeable setting {unknown}; the changeable settings are {list(CHANGEABLE)}"
        )
    applied: dict[str, Any] = {}
    noted_before = len(handle.unconfirmed)

    objective = asked.get("objective")
    if objective is not None:
        result = keep_in_view.set_objective(handle, **_objective_choice(objective))
        if not result.get("success"):
            raise RuntimeError(
                f"set_objective({objective!r}) failed: {result.get('message')}"
            )
        applied["objective"] = result["objective"]["position"]

    experiment = asked.get("experiment")
    if experiment is not None:
        if not isinstance(experiment, str) or not experiment.strip():
            raise ValueError(
                f"experiment must be the name of an experiment ZEN can load, not {experiment!r}"
            )
        known = _readers.get_experiments(handle.client)
        if known is not None and experiment not in known:
            raise ValueError(
                f"ZEN has no experiment {experiment!r}; it can load {known}"
            )
        result = changes.load_experiment(handle.client, experiment)
        if not result.get("success"):
            raise RuntimeError(
                f"load_experiment({experiment!r}) failed: {result.get('message')}"
            )
        handle.experiment = {"name": experiment, "id": result["experiment_id"]}
        applied["experiment"] = experiment

    return {"applied": applied, "unconfirmed": list(handle.unconfirmed[noted_before:])}


def _objective_choice(objective) -> dict:
    """How an objective is named in ``set_state``: its position (a whole number) or its name (a text)."""
    if isinstance(objective, bool):
        raise ValueError(
            f"objective must be a changer position or a name, not {objective!r}"
        )
    if isinstance(objective, int):
        return {"position": objective}
    if isinstance(objective, str) and objective.strip():
        return {"name": objective}
    raise ValueError(
        f"objective must be a changer position (such as 2) or an objective's name, not {objective!r}"
    )
