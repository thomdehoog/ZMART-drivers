"""One change, the same way for every change: the limits first, then the dispatcher.

Every change follows three phases:

    **Phase A** - before anything is sent: check the arguments, show the new
        values to the limits gate (``limits_refusal``), resolve LAS X's enums.
    **Phase B** - ``dispatch_change`` hands the request to the dispatcher's
        ``write`` with the change's profile, which sends it and reads it back.
    **Phase C** - add what the caller needs to the result (``move_xy`` adds
        the target position).

A change that is refused before it is sent answers ``refused(message)``: the
same result shape as a sent change, with ``success`` False and nothing sent.

Result contract: with the profiles' ``success_on_unconfirmed=True``
(deliberate - see ``CommandProfile``), ``result["success"]`` means the change
was *accepted*, not that it took effect. A caller that needs proof the change
landed checks ``result["confirmed"]``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
from dataclasses import replace
from functools import partial

from ...dispatcher import dispatch
from ...dispatcher import limits as gate
from .confirming import race_confirmations
from ...driver_setup.limits.limit_rules import NOTEBOOK_POINTER

log = logging.getLogger(__name__)


def refused(message, *, logged=False, **extra):
    """The result of a change that was not sent: ``success`` False, with the reason.

    ``logged`` also puts the reason in the result's log. ``extra`` adds the
    keys a change's answer always carries, such as ``position=None``.
    """
    return {
        "success": False,
        "confirmed": None,
        "message": message,
        "timing": dispatch.make_timing(total_s=0.0, attempts=0),
        "logs": [dispatch.make_log_entry("error", message)] if logged else [],
        **extra,
    }


def limits_refusal(client, command, values, **extra):
    """The limits gate, asked before anything is sent: None, or the refusal.

    ``command`` is the change's name in ``limit_rules.MUTATING_COMMANDS``, and
    ``values`` what it would set, as the gate reads it.
    """
    message = gate.check_refusal(client, command, values, NOTEBOOK_POINTER)
    if message is None:
        return None
    log.error(message)
    return refused(message, logged=True, **extra)


def binds(fn, name):
    """True when the partial ``fn`` already holds the keyword ``name``."""
    return isinstance(fn, partial) and name in (fn.keywords or {})


def dispatch_change(
    client,
    api_obj,
    description,
    profile,
    *,
    command,
    values,
    setup_fn,
    confirm_fn=None,
    log_confirm_fn=None,
    confirm_race_budget_s=None,
    max_retries=None,
    max_confirm_attempts=None,
    error_check_fn=None,
):
    """Hand one change to the dispatcher's ``write``, with its profile.

    Args:
        client: LAS X API client.
        api_obj: The LAS X request.
        description: What the change does, for the log.
        profile: The change's ``CommandProfile``.
        command: The change's name in ``limit_rules.MUTATING_COMMANDS``.
        values: What the change would set, as the limits gate reads it; the
            dispatcher asks the gate again before anything is sent.
        setup_fn: ``setup_fn(model)`` fills in the request.
        confirm_fn: ``confirm_fn(client)`` reads the change back; None uses
            the profile's (which may be None: no confirmation). One window
            long: ``poll_window`` is set from the profile when the function
            does not hold its own.
        log_confirm_fn: A second way to confirm, from the log. The two race,
            for at most ``confirm_race_budget_s`` seconds.
        max_retries, max_confirm_attempts: Override the profile's numbers.
        error_check_fn: Override the profile's check of LAS X's reply.

    Returns:
        The dispatcher's result.
    """
    confirm = confirm_fn if confirm_fn is not None else profile.confirm_fn
    if (
        confirm is not None
        and profile.confirm_poll_s is not None
        and not binds(confirm, "timeout")
        and not binds(confirm, "poll_window")
    ):
        confirm = partial(confirm, poll_window=profile.confirm_poll_s)

    pre_check_fn = (lambda: profile.pre_check_fn(client)) if profile.pre_check_fn else None
    error_check = error_check_fn if error_check_fn is not None else profile.error_check_fn
    final_confirm_fn = race_confirmations(
        api_leg=(lambda: confirm(client)) if confirm else None,
        log_leg=log_confirm_fn,
        label=description,
        budget_s=confirm_race_budget_s,
    )

    overrides = {
        name: value
        for name, value in (("max_retries", max_retries), ("max_confirm_attempts", max_confirm_attempts))
        if value is not None
    }
    return dispatch.write(
        client,
        api_obj,
        description,
        command=command,
        values=values,
        profile=replace(profile, **overrides) if overrides else profile,
        setup_fn=setup_fn,
        pre_check_fn=pre_check_fn,
        error_check_fn=(lambda: error_check(client)) if error_check else None,
        confirm_fn=final_confirm_fn,
    )
