"""A pretend microscope and its vendor part, plugged into the shared dispatcher.

The dispatcher is meant to know no microscope. This file is the proof: a
second, made-up vendor that hands the dispatcher everything the vendor
contract (``docs/vendor-contract.md``) asks for, and nothing of LAS X. If the
dispatcher ever needs a change to run it, something of LAS X has leaked into
the dispatcher.

The pretend microscope has one stage axis, x, which takes a moment to arrive
after a move, and a reply field that it fills in after each request, as LAS X
does. It can be told to answer "busy" a number of times, to show that a
temporary problem is sent again, and it counts every request that reaches it,
so a test can check that a command meant to be sent once is sent once.

The vendor part follows the same order as the Leica one: a transport, the
waiting numbers, a command profile, the sources a reading can ask, a limits
object, and the error check. ``connect``, ``read_x``, ``move_x`` and
``start_scan`` are the few commands built from them. It is modelled on the
ZMART Controller's mock driver, which pretends to be a whole microscope; this
one pretends just enough to drive the dispatcher.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from zmart_drivers.leica.stellaris_y42h93.navigator_expert.dispatcher import door, gate
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.dispatcher.limit_errors import (
    LimitsStatus,
    LimitViolation,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.dispatcher.reader import (
    Reading,
)

# =============================================================================
# The pretend microscope
# =============================================================================


class PretendMicroscope:
    """One stage axis that takes ``TRAVEL_S`` to arrive, and a reply to each request."""

    TRAVEL_S = 0.05

    def __init__(self):
        self.lock = threading.Lock()
        self.x_from_um = 0.0
        self.x_to_um = 0.0
        self.moved_at = 0.0
        self.reply: str | None = None  # None until answered; then "" or an error
        self.busy_answers = 0  # how many of the next requests are refused as busy
        self.received: list[dict] = []  # every request that reached the microscope

    def take(self, request: dict) -> None:
        """Receive one request, and fill in the reply a moment later, as LAS X does."""
        with self.lock:
            self.received.append(dict(request))
            if self.busy_answers > 0:
                self.busy_answers -= 1
                self.reply = "busy, try again"
                return
            if request["command"] == "move_x":
                self.x_from_um = self.x_now_um()
                self.x_to_um = request["x_um"]
                self.moved_at = time.monotonic()
            self.reply = ""

    def x_now_um(self) -> float:
        """Where the stage is now: on its way, or arrived."""
        moving = (time.monotonic() - self.moved_at) / self.TRAVEL_S
        share = min(max(moving, 0.0), 1.0)
        return self.x_from_um + (self.x_to_um - self.x_from_um) * share


# =============================================================================
# The vendor part: what the dispatcher is handed
# =============================================================================


class Waiting:
    """The waiting rule's four numbers, small, so that the tests run quickly."""

    WINDOWS = 2
    WINDOW_S = 0.3
    POLL_S = 0.01
    ANSWER_POLL_S = 0.002


class PretendTransport:
    """How a request reaches the pretend microscope, and how its reply is awaited."""

    def fill(self, request, setup_fn):
        setup_fn(request)

    def clear_reply(self, client):
        client.reply = None

    def send(self, client, request, description, *, asynchronous):
        client.take(request)
        return True, []

    def await_reply(self, client):
        deadline = time.monotonic() + Waiting.WINDOW_S
        while client.reply is None:
            if time.monotonic() >= deadline:
                return False
            time.sleep(Waiting.ANSWER_POLL_S)
        return True

    def dialog_warning_since(self, command_started_at, description):
        return None  # the pretend microscope has no dialog boxes


@dataclass(frozen=True)
class PretendProfile:
    """The handling of one command: how often to send again, and how long to watch.

    It has no rule of its own for ``never_resend``: that rule belongs to the
    dispatcher, so it must hold for any vendor's profile.
    """

    transport: Any = field(default_factory=PretendTransport)
    waiting: Any = Waiting
    max_retries: int = Waiting.WINDOWS - 1
    max_confirm_attempts: int = Waiting.WINDOWS
    refire_on_unconfirmed: bool = True
    retry_backoff: float | None = None
    retry_escalate: bool = False
    skip_reply_check: bool = False
    send_without_receipt: bool = False
    success_on_unconfirmed: bool = True
    never_resend: bool = False
    limits_setup_hint: str = "the pretend limits in pretend_vendor.connect"


@dataclass(frozen=True)
class PretendDatum:
    """One reading: how to read it, and what counts as an answer."""

    read: Callable[[PretendMicroscope], Any]
    succeeds: Callable[[Any], bool]


class PretendSources:
    """The sources a reading can ask. The pretend microscope has one, ``stage``."""

    waiting = Waiting

    class UnsupportedSource(RuntimeError):
        """The reading has no source for the family asked for."""

    DATUMS = {
        "x": PretendDatum(
            read=lambda scope: scope.x_now_um(),
            succeeds=lambda value: isinstance(value, float),
        ),
    }

    def spec(self, datum):
        return self.DATUMS[datum]

    def mode_for(self, datum):
        return "stage"

    def legs(self, spec, datum, mode):
        if mode != "stage":
            return Reading(None, mode, None, None, self.UnsupportedSource(mode))
        return ("stage",)

    def first_stage(self, spec, mode, fallback):
        return None, mode  # no source is tried alone first

    def success(self, spec, value, about):
        return spec.succeeds(value)

    def ask(self, leg, spec, client, *, until, answer_counts, about, source_kwargs):
        while True:
            seen = time.time()
            reading = Reading(spec.read(client), leg, seen, 0.0)
            if answer_counts(reading) or time.monotonic() >= until:
                return reading
            time.sleep(Waiting.POLL_S)


SOURCES = PretendSources()


class PretendLimits:
    """The pretend stage may travel from 0 to 1000 micrometers."""

    X_RANGE_UM = (0.0, 1000.0)

    def check_command(self, command, values):
        if command == "move_x":
            low, high = self.X_RANGE_UM
            if not low <= values["x_um"] <= high:
                raise LimitViolation(
                    f"x={values['x_um']} um is outside {low}-{high} um"
                )

    def status(self):
        return LimitsStatus(source="pretend", path=None, is_fallback=False)

    def describe(self):
        return self.status().describe()


def error_check(client):
    """Read the reply: an error text means the command failed; "busy" may be sent again."""
    if not client.reply:
        return {"success": True, "logs": []}
    return {
        "success": False,
        "error": client.reply,
        "transient": "busy" in client.reply,
        "logs": [],
    }


# =============================================================================
# The commands, built from the parts above
# =============================================================================


def connect() -> PretendMicroscope:
    """Make a pretend microscope and install its limits in the gate, as a driver does at connect."""
    scope = PretendMicroscope()
    gate.install(scope, gate.GateState(limits=PretendLimits()))
    return scope


def read_x(client, **keywords):
    """Read where the stage is, through the dispatcher's reader; a Reading comes back."""
    return door.read(SOURCES, "x", client, **keywords)


def move_x(client, x_um, *, tolerance_um=0.5):
    """Move the stage to *x_um* and confirm it arrived, through the dispatcher's door."""

    def confirm():
        sent_at = time.time()
        reading = read_x(
            client,
            accept=lambda value: abs(value - x_um) <= tolerance_um,
            observed_after=sent_at,
            deadline=time.monotonic() + Waiting.WINDOW_S,
        )
        arrived = reading is not None and reading.error is None
        return {"success": arrived, "logs": [], "x_um": reading and reading.value}

    return door.write(
        client,
        {"command": "move_x"},
        f"move x to {x_um} um",
        command="move_x",
        values={"x_um": x_um},
        profile=PretendProfile(),
        setup_fn=lambda request: request.update(x_um=x_um),
        error_check_fn=lambda: error_check(client),
        confirm_fn=confirm,
    )


def start_scan(client, **asked):
    """Start a scan: a command that must reach the microscope once, and never again."""
    return door.write(
        client,
        {"command": "start_scan"},
        "start a scan",
        command="start_scan",
        values={},
        profile=PretendProfile(never_resend=True),
        error_check_fn=lambda: error_check(client),
        confirm_fn=lambda: {"success": False, "logs": []},
        **asked,
    )
