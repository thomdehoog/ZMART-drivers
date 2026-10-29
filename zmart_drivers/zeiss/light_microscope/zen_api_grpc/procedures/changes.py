"""The named changes of a ZEN session: move the stage or the focus, change the objective, load an experiment, take a picture.

Each one checks its arguments, builds its change as plain data
(``primitives/gateway/writes.py``) and hands it to the dispatcher's ``run()``,
which asks the limits once, sends it, and reads it back. Then it adds what
the caller needs to the result: ``move_xy`` the target position,
``load_experiment`` the id ZEN gave the experiment, ``acquire`` the name of
the file ZEN wrote. A change refused before anything was sent answers the
same shape, with ``success`` False.

Result contract: ``result["success"]`` means the change was *accepted*, not
that it took effect. A caller that needs proof the change landed checks
``result["confirmed"]``. A picture that was not taken fails: ``take_picture``
raises.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

from ..dispatcher import dispatch
from ..primitives.gateway import profile as gateway_profile
from ..primitives.gateway import writes
from ..primitives.gateway.profile import reply_value
from .save_data.product import AcquisitionResult

log = logging.getLogger(__name__)


def _number(value, label: str) -> float:
    """*value* as a finite number, or ``ValueError`` saying which argument is wrong."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{label} must be a number of micrometres, not {value!r}")
    return float(value)


# =============================================================================
# The stage and the focus
# =============================================================================


def move_xy(
    client,
    x_um: float,
    y_um: float,
    *,
    tolerance_um: float | None = None,
    max_retries: int | None = None,
) -> dict:
    """Move the XY stage to an absolute position, in micrometres of ZEN's stage coordinates.

    The result carries ``position``, the target (not a read-back: ``confirmed``
    says whether the stage was seen there), or None when nothing was sent.
    """
    try:
        change = writes.move_xy(
            _number(x_um, "x"), _number(y_um, "y"), tolerance_um=tolerance_um
        )
    except ValueError as why:
        return dispatch.refused(str(why), position=None)
    result = dispatch.run(gateway_profile, client, change, max_retries=max_retries)
    sent = result["timing"].get("attempts", 0) > 0
    result["position"] = dict(change.values) if sent else None
    return result


def move_z(
    client,
    z_um: float,
    *,
    tolerance_um: float | None = None,
    max_retries: int | None = None,
) -> dict:
    """Move the focus drive to an absolute position, in micrometres of ZEN's focus coordinates."""
    try:
        change = writes.move_z(_number(z_um, "z"), tolerance_um=tolerance_um)
    except ValueError as why:
        return dispatch.refused(str(why))
    return dispatch.run(gateway_profile, client, change, max_retries=max_retries)


# =============================================================================
# The objective
# =============================================================================


def find_objective(
    objectives, *, position=None, name=None, magnification=None
) -> dict | None:
    """The objective on the changer chosen by its position, its name or its magnification; None when there is none."""
    for one in objectives or []:
        if position is not None and one.get("position") == position:
            return one
        if name is not None and (one.get("name") or "").strip() == name.strip():
            return one
        if magnification is not None and one.get("magnification") == magnification:
            return one
    return None


def set_objective(
    client,
    objectives,
    *,
    position=None,
    name=None,
    magnification=None,
    max_retries=None,
    before_fire=None,
) -> dict:
    """Change the objective, by its position, name or magnification (exactly one), without moving the stage.

    *objectives* is the changer's list (the ``objectives`` reading). This is
    the bare change; the one that also keeps the sample in view is
    ``procedures/keep_in_view.py``, which hands in ``before_fire`` to take
    its reading just before the change is sent. ``before_fire`` is called
    with the objective found; it may answer a refusal, and then nothing is sent.
    """
    chosen = [v for v in (position, name, magnification) if v is not None]
    if len(chosen) != 1:
        return dispatch.refused(
            f"give exactly one of position, name or magnification (got {len(chosen)})"
        )
    found = find_objective(
        objectives, position=position, name=name, magnification=magnification
    )
    if found is None:
        available = [(o.get("position"), o.get("name")) for o in objectives or []]
        return dispatch.refused(
            f"no objective with position={position}, name={name}, magnification={magnification}; "
            f"the changer has {available}"
        )
    if before_fire is not None:
        refusal = before_fire(found)
        if refusal:
            refusal.setdefault("timing", dispatch.make_timing(total_s=0.0, attempts=0))
            return refusal
    change = writes.set_objective(int(found["position"]), found.get("name"))
    result = dispatch.run(gateway_profile, client, change, max_retries=max_retries)
    result["objective"] = dict(found)
    return result


# =============================================================================
# Experiments: loading one, its z-stack, taking a picture, stopping
# =============================================================================


def load_experiment(client, name: str, *, max_retries=None) -> dict:
    """Load the experiment called *name* in ZEN; the result carries ``experiment_id`` when it was loaded.

    ZEN gives every load a new id, which the other calls about the experiment
    (its z-stack, taking a picture with it) take.
    """
    if not isinstance(name, str) or not name.strip():
        return dispatch.refused(
            f"an experiment is named by a non-empty text, not {name!r}",
            experiment_id=None,
        )
    result = dispatch.run(
        gateway_profile,
        client,
        writes.load_experiment(name.strip()),
        max_retries=max_retries,
    )
    answer = reply_value(client) if result["success"] else None
    result["experiment_id"] = (
        getattr(answer, "experiment_id", None) if answer is not None else None
    )
    if result["success"] and not result["experiment_id"]:
        result["success"] = False
        result["message"] = (
            f"{result['message']} | ZEN answered without an experiment id"
        )
    return result


def set_z_stack(
    client,
    experiment_id: str,
    experiment_name: str,
    *,
    first_um=None,
    last_um=None,
    interval_um=None,
    max_retries=None,
) -> dict:
    """Change the z-stack of the loaded experiment: first and last slice and step, in micrometres of ZEN's focus; read back."""
    given = {}
    for label, value in (
        ("first_um", first_um),
        ("last_um", last_um),
        ("interval_um", interval_um),
    ):
        if value is not None:
            given[label] = _number(value, label)
    if not given:
        return dispatch.refused("give at least one of first_um, last_um or interval_um")
    if "interval_um" in given and given["interval_um"] <= 0:
        return dispatch.refused(
            f"interval_um must be above 0, not {given['interval_um']}"
        )
    change = writes.set_z_stack(experiment_id, experiment_name, **given)
    return dispatch.run(gateway_profile, client, change, max_retries=max_retries)


def acquire(
    client,
    experiment_id: str,
    experiment_name: str,
    output_name: str,
    *,
    snap: bool = False,
) -> dict:
    """Run the loaded experiment once (or take one snap) and have ZEN write ``<output_name>.czi``.

    The capture is sent once and never again. ZEN's call answers when the
    acquisition is finished; the experiment's status is read back. The
    result carries ``output_name``, the file ZEN says it wrote (without its
    ``.czi``), or None when it was not sent.
    """
    change = writes.acquire(experiment_id, experiment_name, output_name, snap=snap)
    result = dispatch.run(gateway_profile, client, change)
    answer = reply_value(client) if result["success"] else None
    result["output_name"] = (
        getattr(answer, "output_name", None) if answer is not None else None
    )
    if result["success"] and not result["output_name"]:
        # ZEN names the file it wrote; without that name the file cannot be found.
        result["success"] = False
        result["message"] = (
            f"{result['message']} | ZEN answered without the name of the file it wrote"
        )
    return result


def take_picture(
    client,
    experiment_id: str,
    experiment_name: str,
    output_name: str,
    *,
    snap: bool = False,
) -> AcquisitionResult:
    """Acquire and answer when it ran; ``RuntimeError`` when it was not sent or ZEN refused."""
    started_at = time.time()
    result = acquire(client, experiment_id, experiment_name, output_name, snap=snap)
    finished_at = time.time()
    if not result or not result.get("success"):
        raise RuntimeError(
            f"acquire failed: {result.get('message') if result else result}"
        )
    return AcquisitionResult(
        experiment=experiment_name,
        experiment_id=experiment_id,
        output_name=result["output_name"],
        snap=snap,
        started_at=started_at,
        finished_at=finished_at,
        command_result=result,
    )


def stop(client, experiment_id: str = "") -> dict:
    """Stop the experiment *experiment_id*, or whatever ZEN is acquiring when it is empty."""
    return dispatch.run(gateway_profile, client, writes.stop(experiment_id))


# =============================================================================
# Focusing
# =============================================================================


def _focus_from_reply(client, result: dict, field: str) -> dict:
    """Add ``focus_um`` from the reply's *field* (metres) to a focusing result."""
    answer = reply_value(client) if result["success"] else None
    value = getattr(answer, field, None) if answer is not None else None
    result["focus_um"] = float(value) * 1e6 if value is not None else None
    return result


def autofocus(
    client, experiment_id: str, experiment_name: str, *, timeout_s: float | None = None
) -> dict:
    """Run ZEN's software autofocus with the loaded experiment's settings; ``focus_um`` is where it ended, in ZEN's focus coordinates."""
    change = writes.find_autofocus(experiment_id, experiment_name, timeout_s=timeout_s)
    return _focus_from_reply(
        client, dispatch.run(gateway_profile, client, change), "focus_position"
    )


def find_surface(client) -> dict:
    """Have the Definite Focus find the coverslip; ``focus_um`` is where it ended."""
    return _focus_from_reply(
        client,
        dispatch.run(gateway_profile, client, writes.find_surface()),
        "zposition",
    )


def store_focus(client) -> dict:
    """Have the Definite Focus remember the focus where it stands."""
    return dispatch.run(gateway_profile, client, writes.store_focus())


def recall_focus(client) -> dict:
    """Have the Definite Focus return to the remembered focus; ``focus_um`` is where it ended."""
    return _focus_from_reply(
        client,
        dispatch.run(gateway_profile, client, writes.recall_focus()),
        "zposition",
    )


def checked_number(value: Any, label: str) -> float:
    """A number argument, checked the way the changes check theirs; for the procedures above this file."""
    return _number(value, label)
