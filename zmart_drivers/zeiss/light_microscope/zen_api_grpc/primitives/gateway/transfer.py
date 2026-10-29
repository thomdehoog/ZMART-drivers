"""How a request reaches ZEN through the gateway, and how its reply comes back.

Every change the driver makes is one call of a ZEN API service: a request
message is filled in and handed to the service, and ZEN answers with a
response message, or with an error. ZEN's calls are *blocking*: ``MoveTo``
returns when the stage has arrived, ``RunExperiment`` when the experiment has
finished. So the receipt of a request is its answer, and the dispatcher's
read-back afterwards is the second opinion that the change really shows.

This file holds the table of the calls the driver can make (:data:`CALLS`),
the :class:`Request` that carries one, and the :class:`GatewayTransport` the
dispatcher's write loops send through: ``fill``, ``clear_reply``, ``send``,
``await_reply`` and ``dialog_warning_since``, as the vendor contract
(``docs/vendor-contract.md``) lists them. The reply, or the error, is kept on
the client (``client.reply``), where the profile's ``check_reply`` reads it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

#: An acquisition, an autofocus and a surface search take as long as they
#: take; 0 means no limit on the call.
NO_LIMIT_S = 0.0

#: How long a move may take before the call counts as timed out, in seconds.
#: A long stage travel on a slow stage stays well within it.
MOVE_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class Call:
    """One call of a ZEN API service: which service, which method, which request message, and how long it may take."""

    service: str
    method: str
    message: str
    timeout_s: float | None = None


#: Every call a write can name, by the name the write gives (``Write.request``).
CALLS = {
    "stage.move_to": Call(
        "stage", "move_to", "lm.hardware.v2:StageServiceMoveToRequest", MOVE_TIMEOUT_S
    ),
    "focus.move_to": Call(
        "focus", "move_to", "lm.hardware.v2:FocusServiceMoveToRequest", MOVE_TIMEOUT_S
    ),
    "objective.move_to": Call(
        "objective",
        "move_to",
        "lm.hardware.v2:ObjectiveChangerServiceMoveToRequest",
        MOVE_TIMEOUT_S,
    ),
    "experiment.load": Call(
        "experiment", "load", "acquisition.v1beta:ExperimentServiceLoadRequest"
    ),
    "experiment.run_experiment": Call(
        "experiment",
        "run_experiment",
        "acquisition.v1beta:ExperimentServiceRunExperimentRequest",
        NO_LIMIT_S,
    ),
    "experiment.run_snap": Call(
        "experiment",
        "run_snap",
        "acquisition.v1beta:ExperimentServiceRunSnapRequest",
        NO_LIMIT_S,
    ),
    "experiment.stop": Call(
        "experiment", "stop", "acquisition.v1beta:ExperimentServiceStopRequest"
    ),
    "zstack.modify_first_last": Call(
        "zstack",
        "modify_z_stack_first_last",
        "lm.acquisition.v1:ZStackServiceModifyZStackFirstLastRequest",
    ),
    "autofocus.find": Call(
        "autofocus",
        "find_auto_focus",
        "lm.acquisition.v1:ExperimentSwAutofocusServiceFindAutoFocusRequest",
        NO_LIMIT_S,
    ),
    "definite_focus.find_surface": Call(
        "definite_focus",
        "find_surface",
        "lm.acquisition.v1:DefiniteFocusServiceFindSurfaceRequest",
        NO_LIMIT_S,
    ),
    "definite_focus.store": Call(
        "definite_focus",
        "store_focus",
        "lm.acquisition.v1:DefiniteFocusServiceStoreFocusRequest",
    ),
    "definite_focus.recall": Call(
        "definite_focus",
        "recall_focus",
        "lm.acquisition.v1:DefiniteFocusServiceRecallFocusRequest",
    ),
}


class Request:
    """One request on its way to ZEN: the call it makes and the message it carries.

    ``name`` is the call's name in :data:`CALLS`; ``message`` is the request
    message the write fills in; ``call`` says which service answers.
    """

    def __init__(self, name: str, call: Call, message: Any) -> None:
        self.name = name
        self.call = call
        self.message = message


def request_of(client, name: str) -> Request:
    """The request for the call *name*, with an empty message made by the client."""
    call = CALLS.get(name)
    if call is None:
        raise ValueError(f"unknown ZEN call {name!r}; the calls are {sorted(CALLS)}")
    return Request(name, call, client.message(call.message))


@dataclass(frozen=True)
class Reply:
    """What ZEN answered to the last change: its response message, or the error it raised."""

    value: Any = None
    error: BaseException | None = None


class GatewayTransport:
    """The way requests are sent to ZEN, as the dispatcher's write loops ask for it."""

    def fill(self, request: Request, setup_fn) -> None:
        """Write the change's values into the request message."""
        setup_fn(request.message)

    def clear_reply(self, client) -> None:
        """Forget the last reply, so that what is read after the send is the new one."""
        client.reply = None

    def send(self, client, request: Request, description: str, *, asynchronous: bool):
        """Make the call and keep ZEN's answer, or its error, as the reply.

        Returns ``(arrived, notes)``. ``arrived`` is True once the call has
        answered, with a response or with an error: either way the request
        reached ZEN. A connection that is not there raises, and the write
        loops report the send as failed. With *asynchronous* the call is
        started and not waited for; the reply fills in when it answers.
        """
        if asynchronous:
            future = client.begin(
                request.call.service,
                request.call.method,
                request.message,
                timeout=request.call.timeout_s,
            )

            def keep(done):
                error = done.exception()
                client.reply = (
                    Reply(error=error)
                    if error is not None
                    else Reply(value=done.result())
                )

            future.add_done_callback(keep)
            return True, []
        try:
            answer = client.call(
                request.call.service,
                request.call.method,
                request.message,
                timeout=request.call.timeout_s,
            )
        except OSError:
            # The connection itself failed: nothing reached ZEN, and the write
            # loops report the send as failed.
            raise
        except Exception as why:  # noqa: BLE001 -- ZEN's refusal, or a timeout, is the reply; the profile sorts it
            client.reply = Reply(error=why)
            log.debug(
                "%s: ZEN answered with %s: %s", description, type(why).__name__, why
            )
            return True, []
        client.reply = Reply(value=answer)
        return True, []

    def await_reply(self, client) -> bool:
        """Whether the reply is in. A blocking call has already answered by the time this is asked."""
        return client.reply is not None

    def dialog_warning_since(self, command_started_at, description):
        """ZEN's dialogs cannot be seen from outside; there is never a warning to add."""
        return


#: The one transport every change goes through.
GATEWAY = GatewayTransport()
