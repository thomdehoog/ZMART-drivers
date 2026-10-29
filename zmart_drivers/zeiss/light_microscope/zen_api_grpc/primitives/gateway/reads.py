"""The readings the ZEN API can answer, one function each.

Each function asks one ZEN service a question and hands back a plain
dictionary or list, with every distance in micrometres (ZEN speaks metres on
the wire, and this file is where that is converted). Nothing here retries,
waits or decides what counts as an answer: that is the dispatcher's read loop,
which asks these through the profile (``profile.py``). A reading that fails
raises, and the profile carries the error in the ``Reading`` it answers.

A reading about one experiment (its status, its z-stack, its tracks) takes
the experiment's id, which ZEN hands out when the experiment is loaded.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from typing import Any

#: Micrometres per metre: the wire speaks metres, the driver micrometres.
UM_PER_M = 1e6

#: ZEN's answer when no experiment is active and it is asked for "the" active one.
NO_ACTIVE_EXPERIMENT = "FAILED_PRECONDITION"


def get_xy(client) -> dict:
    """Where the XY stage is, in micrometres of ZEN's own stage coordinates."""
    answer = client.call(
        "stage",
        "get_position",
        client.message("lm.hardware.v2:StageServiceGetPositionRequest"),
    )
    return {"x_um": float(answer.x) * UM_PER_M, "y_um": float(answer.y) * UM_PER_M}


def get_z(client) -> dict:
    """Where the focus drive is, in micrometres of ZEN's own focus coordinates."""
    answer = client.call(
        "focus",
        "get_position",
        client.message("lm.hardware.v2:FocusServiceGetPositionRequest"),
    )
    return {"z_um": float(answer.value) * UM_PER_M}


def get_objectives(client) -> list[dict]:
    """The objectives on the changer: ``position``, ``name``, ``magnification``, ``na`` and ``immersion`` each.

    ZEN counts positions from 1. An empty position is not listed.
    """
    answer = client.call(
        "objective",
        "get_objectives",
        client.message("lm.hardware.v2:ObjectiveChangerServiceGetObjectivesRequest"),
    )
    return [
        {
            "position": int(one.position),
            "name": str(one.name),
            "magnification": float(one.magnification),
            "na": float(one.na),
            "immersion": _enum_name(one.immersion_type),
        }
        for one in answer.objectives
    ]


def get_objective(client) -> dict:
    """The objective in place: its ``position`` on the changer, and what the changer knows about it.

    When the position holds no objective ZEN knows, only the position is
    given and the other fields are None.
    """
    answer = client.call(
        "objective",
        "get_position",
        client.message("lm.hardware.v2:ObjectiveChangerServiceGetPositionRequest"),
    )
    position = int(answer.value)
    found = next(
        (one for one in get_objectives(client) if one["position"] == position), None
    )
    return {
        "position": position,
        "name": None,
        "magnification": None,
        "na": None,
        "immersion": None,
        **(found or {}),
    }


def get_experiments(client) -> list[str]:
    """The names of the experiments ZEN can load: the ``.czexp`` files in its experiment folder."""
    answer = client.call(
        "experiment",
        "get_available_experiments",
        client.message(
            "acquisition.v1beta:ExperimentServiceGetAvailableExperimentsRequest"
        ),
    )
    return [str(one.name) for one in answer.experiments]


def get_image_output_path(client) -> str:
    """The folder on the ZEN computer where ZEN writes the CZI file of every acquisition."""
    answer = client.call(
        "experiment",
        "get_image_output_path",
        client.message("acquisition.v1beta:ExperimentServiceGetImageOutputPathRequest"),
    )
    return str(answer.image_output_path)


def status_as_dict(status) -> dict:
    """An ``ExperimentStatus`` message as plain values: whether it runs, and how far it is."""
    elapsed = getattr(status, "total_elapsed_time", None)
    return {
        "running": bool(status.is_experiment_running),
        "acquiring": bool(status.is_acquisition_running),
        "images_acquired": int(status.images_acquired_index),
        "images_count": int(status.images_count),
        "channels_count": int(status.channels_count),
        "zstack_slices_count": int(status.zstack_slices_count),
        "time_points_count": int(status.time_points_count),
        "tiles_count": int(status.tiles_count),
        "scenes_count": int(status.scenes_count),
        "elapsed_s": elapsed.total_seconds()
        if hasattr(elapsed, "total_seconds")
        else None,
    }


def get_experiment_status(client, experiment_id: str) -> dict:
    """How the experiment *experiment_id* is doing: running or finished, and how many images it took.

    ZEN keeps the status of a finished experiment, so this can be asked after
    an acquisition to see that it ran, and how far.
    """
    message = client.message("acquisition.v1beta:ExperimentServiceGetStatusRequest")
    message.experiment_id = experiment_id
    answer = client.call("experiment", "get_status", message)
    return status_as_dict(answer.status)


def get_activity(client) -> dict:
    """Whether anything is being acquired in ZEN now: ``{"running": bool, "status": ...}``.

    ZEN is asked for the status of the active experiment, whichever it is. It
    answers an error, ``FAILED_PRECONDITION``, when nothing is active; that
    is the answer "idle", not a failure.
    """
    message = client.message("acquisition.v1beta:ExperimentServiceGetStatusRequest")
    message.experiment_id = ""
    try:
        answer = client.call("experiment", "get_status", message)
    except Exception as why:
        if _status_name(why) == NO_ACTIVE_EXPERIMENT:
            return {"running": False, "status": None}
        raise
    status = status_as_dict(answer.status)
    return {"running": status["running"] or status["acquiring"], "status": status}


def get_zstack(client, experiment_id: str) -> dict:
    """The z-stack of the experiment: whether it has one, and its first and last slice, step and count, in micrometres.

    The slice positions are focus positions in ZEN's own coordinates, as the
    focus drive reports them.
    """
    asked = client.message("lm.acquisition.v1:ZStackServiceIsZStackExperimentRequest")
    asked.experiment_id = experiment_id
    is_stack = bool(
        client.call("zstack", "is_z_stack_experiment", asked).is_zstack_experiment
    )
    if not is_stack:
        return {
            "is_zstack": False,
            "first_um": None,
            "last_um": None,
            "interval_um": None,
            "slices": 1,
        }
    asked = client.message("lm.acquisition.v1:ZStackServiceGetZStackInfoRequest")
    asked.experiment_id = experiment_id
    info = client.call("zstack", "get_z_stack_info", asked)
    return {
        "is_zstack": True,
        "first_um": float(info.first_slice) * UM_PER_M,
        "last_um": float(info.last_slice) * UM_PER_M,
        "interval_um": float(info.interval) * UM_PER_M,
        "slices": int(info.num_slices),
        "center_mode": bool(info.is_center_mode),
    }


def get_tracks(client, experiment_id: str) -> list[dict]:
    """The experiment's tracks, each with its channels: ``{"active", "channels": [{"name", "active"}]}``."""
    asked = client.message("lm.acquisition.v1:TrackServiceGetTrackInfoRequest")
    asked.experiment_id = experiment_id
    answer = client.call("tracks", "get_track_info", asked)
    return [
        {
            "active": bool(track.is_activated),
            "channels": [
                {"name": str(ch.name), "active": bool(ch.is_activated)}
                for ch in track.channels
            ],
        }
        for track in answer.track_info
    ]


def _enum_name(value: Any) -> str | None:
    """An enum member's name, or None for an absent value."""
    if value is None:
        return None
    name = getattr(value, "name", None)
    return str(name) if name is not None else str(value)


def _status_name(error: BaseException) -> str | None:
    """The gRPC status name an error carries (``"FAILED_PRECONDITION"``), or None."""
    status = getattr(error, "status", None)
    if status is None:
        return None
    name = getattr(status, "name", None)
    return str(name if name is not None else status).upper()


#: Every reading by its name: what the profile's table of readings is made of.
READS = {
    "xy": get_xy,
    "z": get_z,
    "objective": get_objective,
    "objectives": get_objectives,
    "experiments": get_experiments,
    "image_output_path": get_image_output_path,
    "experiment_status": get_experiment_status,
    "activity": get_activity,
    "zstack": get_zstack,
    "tracks": get_tracks,
}
