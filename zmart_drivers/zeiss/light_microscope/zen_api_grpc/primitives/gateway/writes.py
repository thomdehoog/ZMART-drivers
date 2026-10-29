"""The changes ZEN can be asked for, as plain data the dispatcher runs.

A change is one :class:`Write`: its name and values for the limits, the ZEN
call that carries it (``transfer.CALLS``) and how the request message is
filled in, the label in the log, and what belongs to this change alone: what
to wait for before it is sent, which reading shows that it took effect and
how close counts, and whether it may never be sent again. The dispatcher's
``run()`` does the rest. Nothing here calls ZEN or the dispatcher.

ZEN's calls block until they are done, so a move has arrived when its call
answers, and an acquisition has finished. The read-backs here are the second
opinion the anatomy asks for: the stage read within a tolerance, the
objective changer's position, the experiment's status showing it is over.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .reads import UM_PER_M


@dataclass(frozen=True)
class Expect:
    """A reading, and what it must show."""

    reading: str  # "xy", "z", "objective", "experiment_status", "activity", "zstack"
    accept: Callable[[object], bool]  # how close counts
    about: str | None = None  # the experiment id, for a reading about one experiment
    mode: str | None = None  # the class asked first; None takes the profile's
    fallback: str | None = None
    without_limit: bool = False  # waited for as long as it takes, not the windows


@dataclass(frozen=True)
class Write:
    """One change: its call, its limits, and what belongs to it alone."""

    command: str  # its name in the limits (MUTATING_COMMANDS)
    values: dict  # what the limits gate is shown
    request: str  # the call, e.g. "stage.move_to"
    fill: Callable | None  # fill(message): writes the values into the request message
    description: str
    ready: Expect | None = None  # waited for before every send
    confirm: Expect | None = None  # read back, one window at a time
    without_receipt: bool = False
    never_resend: bool = False


#: How close a move must come, in micrometres, unless the caller says.
XY_TOLERANCE_UM = 1.0
Z_TOLERANCE_UM = 0.5


# =============================================================================
# The stage and the focus
# =============================================================================


def within_xy(x_um: float, y_um: float, tolerance_um: float):
    """A stage reading within ``tolerance_um`` of ``(x_um, y_um)`` on both axes."""

    def check(position) -> bool:
        try:
            return (
                abs(position["x_um"] - x_um) <= tolerance_um
                and abs(position["y_um"] - y_um) <= tolerance_um
            )
        except (KeyError, TypeError):
            return False

    return check


def z_within(z_um: float, tolerance_um: float):
    """A focus reading within ``tolerance_um`` of ``z_um``."""

    def check(position) -> bool:
        try:
            return abs(position["z_um"] - z_um) <= tolerance_um
        except (KeyError, TypeError):
            return False

    return check


def move_xy(x_um: float, y_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the XY stage to an absolute position, in micrometres of ZEN's stage coordinates.

    ZEN's ``MoveTo`` answers when the stage has arrived; the read-back then
    checks the position within *tolerance_um* (``XY_TOLERANCE_UM`` when
    None). An unconfirmed absolute move is sent again: it is the same move.
    """
    tolerance = XY_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(message):
        message.x = x_um / UM_PER_M
        message.y = y_um / UM_PER_M

    return Write(
        command="move_xy",
        values={"x_um": x_um, "y_um": y_um},
        request="stage.move_to",
        fill=fill,
        description=f"MoveXY -> ({x_um:.1f}, {y_um:.1f}) um",
        confirm=Expect("xy", within_xy(x_um, y_um, tolerance)),
    )


def move_z(z_um: float, *, tolerance_um: float | None = None) -> Write:
    """Move the focus drive to an absolute position, in micrometres of ZEN's focus coordinates."""
    tolerance = Z_TOLERANCE_UM if tolerance_um is None else tolerance_um

    def fill(message):
        message.value = z_um / UM_PER_M

    return Write(
        command="move_z",
        values={"z_um": z_um},
        request="focus.move_to",
        fill=fill,
        description=f"MoveZ -> {z_um:.2f} um",
        confirm=Expect("z", z_within(z_um, tolerance)),
    )


# =============================================================================
# The objective
# =============================================================================


def objective_is(position: int):
    """An objective reading whose changer position is *position*."""

    def check(objective) -> bool:
        try:
            return int(objective["position"]) == position
        except (KeyError, TypeError, ValueError):
            return False

    return check


def set_objective(position: int, name: str | None) -> Write:
    """Turn the objective changer to *position* (ZEN counts from 1), without moving the stage."""

    def fill(message):
        message.position_index = position

    return Write(
        command="set_objective",
        values={"objective_position": position},
        request="objective.move_to",
        fill=fill,
        description=f"Objective -> position {position}"
        + (f" ({name})" if name else ""),
        confirm=Expect("objective", objective_is(position)),
    )


# =============================================================================
# Experiments: loading one, taking a picture with it, changing its z-stack
# =============================================================================


def load_experiment(name: str) -> Write:
    """Load the experiment called *name* (a ``.czexp`` ZEN knows), so it can be run.

    ZEN answers with the experiment's id, which the transfer keeps as the
    reply; there is nothing to read back, so the reply is the proof.
    """

    def fill(message):
        message.experiment_name = name

    return Write(
        command="load_experiment",
        values={"experiment": name},
        request="experiment.load",
        fill=fill,
        description=f"LoadExperiment '{name}'",
    )


def not_running(activity) -> bool:
    """An activity reading that says nothing is being acquired."""
    try:
        return not activity["running"]
    except (KeyError, TypeError):
        return False


def finished(status) -> bool:
    """An experiment status that says the experiment is over."""
    try:
        return not status["running"] and not status["acquiring"]
    except (KeyError, TypeError):
        return False


def acquire(
    experiment_id: str, experiment_name: str, output_name: str, *, snap: bool = False
) -> Write:
    """Run the loaded experiment once, or take one snap with it, and have ZEN write ``<output_name>.czi``.

    Never sent again, after an error or otherwise: a second send is a second
    picture. Sent only once nothing else is being acquired. ZEN's call answers
    when the acquisition is finished; the experiment's status is read back to
    see that it is over.
    """

    def fill(message):
        message.experiment_id = experiment_id
        message.output_name = output_name

    return Write(
        command="acquire",
        values={"experiment": experiment_name},
        request="experiment.run_snap" if snap else "experiment.run_experiment",
        fill=fill,
        description=f"{'Snap' if snap else 'RunExperiment'} '{experiment_name}' -> {output_name}",
        ready=Expect("activity", not_running),
        confirm=Expect("experiment_status", finished, about=experiment_id),
        never_resend=True,
    )


def stack_is(
    first_um: float | None,
    last_um: float | None,
    interval_um: float | None,
    tolerance_um: float,
):
    """A z-stack reading whose given ends and step are within *tolerance_um* of what was asked."""

    def check(stack) -> bool:
        try:
            if not stack["is_zstack"]:
                return False
            for asked, key in (
                (first_um, "first_um"),
                (last_um, "last_um"),
                (interval_um, "interval_um"),
            ):
                if asked is not None and abs(stack[key] - asked) > tolerance_um:
                    return False
            return True
        except (KeyError, TypeError):
            return False

    return check


def set_z_stack(
    experiment_id: str,
    experiment_name: str,
    *,
    first_um: float | None = None,
    last_um: float | None = None,
    interval_um: float | None = None,
    tolerance_um: float = 0.05,
) -> Write:
    """Change the z-stack of the loaded experiment: its first and last slice and its step, in micrometres.

    A value left None keeps what the experiment has. The positions are focus
    positions in ZEN's own coordinates.
    """
    asked = {
        k: v
        for k, v in (
            ("first_um", first_um),
            ("last_um", last_um),
            ("interval_um", interval_um),
        )
        if v is not None
    }

    def fill(message):
        message.experiment_id = experiment_id
        if first_um is not None:
            message.first = first_um / UM_PER_M
        if last_um is not None:
            message.last = last_um / UM_PER_M
        if interval_um is not None:
            message.interval = interval_um / UM_PER_M

    return Write(
        command="set_z_stack",
        # The ends are focus positions: the limits check them against the focus travel.
        values={
            "experiment": experiment_name,
            "values": [v for v in (first_um, last_um) if v is not None],
        },
        request="zstack.modify_first_last",
        fill=fill,
        description=f"ZStack '{experiment_name}' -> {asked}",
        confirm=Expect(
            "zstack",
            stack_is(first_um, last_um, interval_um, tolerance_um),
            about=experiment_id,
        ),
    )


def stop(experiment_id: str = "") -> Write:
    """Stop the experiment *experiment_id*, or whatever ZEN is acquiring when it is empty."""

    def fill(message):
        message.experiment_id = experiment_id

    return Write(
        command="stop",
        values={},
        request="experiment.stop",
        fill=fill,
        description="Stop acquisition",
        confirm=Expect("activity", not_running),
    )


# =============================================================================
# Focusing: ZEN's software autofocus and the Definite Focus
# =============================================================================


def find_autofocus(
    experiment_id: str, experiment_name: str, *, timeout_s: float | None = None
) -> Write:
    """Run ZEN's software autofocus with the settings of the loaded experiment; the reply holds the focus found.

    Sent once: a second run would search again. The focus drive is read after
    it by the procedure that runs it.
    """

    def fill(message):
        message.experiment_id = experiment_id
        if timeout_s is not None:
            message.timeout = float(timeout_s)

    return Write(
        command="autofocus",
        values={"experiment": experiment_name},
        request="autofocus.find",
        fill=fill,
        description=f"SoftwareAutofocus '{experiment_name}'",
        ready=Expect("activity", not_running),
        never_resend=True,
    )


def find_surface() -> Write:
    """Have the Definite Focus find the coverslip surface; the reply holds the focus position."""
    return Write(
        command="find_surface",
        values={},
        request="definite_focus.find_surface",
        fill=None,
        description="DefiniteFocus FindSurface",
        ready=Expect("activity", not_running),
        never_resend=True,
    )


def store_focus() -> Write:
    """Have the Definite Focus remember the focus where it stands now."""
    return Write(
        command="store_focus",
        values={},
        request="definite_focus.store",
        fill=None,
        description="DefiniteFocus StoreFocus",
    )


def recall_focus() -> Write:
    """Have the Definite Focus go back to the focus it remembered; the reply holds the position."""
    return Write(
        command="recall_focus",
        values={},
        request="definite_focus.recall",
        fill=None,
        description="DefiniteFocus RecallFocus",
        ready=Expect("activity", not_running),
    )
