"""The recipe of one command: the checks around it, how often it may be sent, how long to wait.

Every change has a ``CommandProfile``, defined beside the change in
``operations/changes/``. The dispatcher runs the loops; the profile says how.
All four callable fields follow the same rule: ``callable(client) -> result``,
with extra parameters pre-bound with ``partial``.

Command wrappers may accept explicit overrides for tests and unusual
hardware sessions, but their default tolerances, polling intervals, and
retry ceilings live in the profile.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from collections.abc import Callable
from dataclasses import dataclass

from ...connection import waiting
from ...connection.waiting import WINDOW_S, WINDOWS
from . import routes as _routes
from .error_classification import _default_error_check
from .limit_rules import NOTEBOOK_POINTER


@dataclass(frozen=True)
class CommandProfile:
    """Complete recipe for a single command's backbone behaviour.

    Each field is either a callable or a tuning parameter. Callables
    follow the contract ``callable(client) -> result dict``. Extra
    parameters are pre-bound with ``partial``. The command function
    binds ``client`` via lambda at call time.

    Attributes:
        pre_check_fn: Pre-flight check. ``callable(client) -> result``.
            None to skip.
        error_check_fn: Post-fire error check. ``callable(client) -> result``.
            Defaults to ``_default_error_check``.
        confirm_fn: Readback confirmation. ``callable(client) -> result``.
            None to skip confirmation. Declarative only - commands always
            override this with a target-bound partial at call time.
        max_retries: Transient error retries inside the fire block. The
            default gives ``WINDOWS`` tries in all, like every send-again.
        max_confirm_attempts: Confirmation windows; ``WINDOWS`` by the rule.
        refire_on_unconfirmed: If True, an unconfirmed readback causes
            the command to be sent again before the next confirmation
            attempt. If False, the dispatcher retries readback only.
            Leica setting commands normally re-fire because the API may
            accept a command while LAS X later settles to a different
            state, or an operator may change the setting manually.
        never_resend: True for a command that must reach the microscope at
            most once (the acquisition). The dispatcher then sends it once,
            with a single confirmation window and no send again, whatever a
            caller asks for (``dispatcher/send_loop.resolve_profile``).
        confirm_poll_s: One confirmation window (seconds), ``WINDOW_S`` by
            the rule. NOT a timeout - the readback is watched for this long,
            then the command re-fires and is watched again up to
            ``max_confirm_attempts``; exhaustion returns unconfirmed, never a
            hard fail.
        confirm_tolerance: Numeric tolerance passed to target readback
            confirmations. None means exact-match or function default.
        poll_interval: Status-check interval for the acquisition watch,
            which stays outside the window rule (it is never sent again).
        poll_timeout: Poll deadline for long-running confirmations.
            None means the confirmation waits until LAS X completes.
        start_timeout: Acquisition-start deadline before the acquire
            confirmation treats the command as failed.
        heartbeat_interval: Status-log cadence during long acquisitions.
        retry_backoff: Base delay in seconds between transient error retries.
            None for immediate retry (no delay).
        retry_escalate: If True, double the delay after each retry
            (exponential backoff: 0s, base, 2×base, 4×base, ...).
            If False, use a fixed delay. Ignored when retry_backoff is None.
        skip_reply_check: If True, skip echo settlement polling after fire.
            Use for commands where a dedicated confirm_fn (e.g. scan
            status polling) is the authoritative completion signal and
            echo waiting is redundant overhead.
        send_without_receipt: If True, use UpdateAsync instead of UpdateAwaitReceipt.
            Use for hardware commands (e.g. stage moves, acquisitions)
            where confirm_fn is the authoritative completion signal.
        success_on_unconfirmed: If True (the default), return success=True with
            confirmed=False when all confirmation attempts are exhausted. The
            command never hard-fails on an unconfirmed readback; only transport
            failure and an echo error-check rejection return success=False. Set
            False only for a command that must hard-fail on unconfirmed - none do.
        transport: How the request is sent to the microscope and how its reply is
            waited for (clear the reply, send, wait for the reply, report an open
            dialog). The dispatcher's engine asks the transport and names no
            microscope object itself.
        waiting: The waiting rule the engines follow: any object with the four
            numbers ``WINDOWS``, ``WINDOW_S``, ``POLL_S`` and ``ANSWER_POLL_S``.
    """

    pre_check_fn: Callable[..., dict] | None = None
    error_check_fn: Callable[..., dict] | None = _default_error_check
    confirm_fn: Callable[..., dict] | None = None
    max_retries: int = WINDOWS - 1
    max_confirm_attempts: int = WINDOWS
    refire_on_unconfirmed: bool = True
    confirm_poll_s: float = WINDOW_S
    confirm_tolerance: float | None = None
    poll_interval: float | None = None
    poll_timeout: float | None = None
    start_timeout: float | None = None
    heartbeat_interval: float | None = None
    retry_backoff: float | None = None
    retry_escalate: bool = False
    skip_reply_check: bool = False
    send_without_receipt: bool = False
    success_on_unconfirmed: bool = True
    # How a request reaches the microscope and how its reply is waited for; see
    # ``transport.py``. Another microscope supplies its own.
    transport: object = _routes.ROUTED
    # The waiting rule (windows, window length, poll times) the engines follow
    # for this command; see ``connection/waiting.py``.
    waiting: object = waiting
    # Where the operator creates the limits files, said when a command is refused
    # because no limits are installed.
    limits_setup_hint: str = NOTEBOOK_POINTER
    # A command that must reach the microscope at most once, because sending it
    # twice would do the work twice (an acquisition would save two data sets).
    # The dispatcher holds it to one send, whatever else a caller asks for.
    never_resend: bool = False

    def __post_init__(self):
        # Async fire blanks the echo, so an echo-based error check would read a
        # cleared echo and report a meaningless success. Couple the two fields.
        if self.send_without_receipt and self.error_check_fn is not None:
            raise ValueError("send_without_receipt=True requires error_check_fn=None")
        # A single confirm window has no 'next attempt' to re-fire before, so a
        # requested re-fire could never run. Forbid the incoherent combination.
        if self.max_confirm_attempts == 1 and self.refire_on_unconfirmed:
            raise ValueError(
                "max_confirm_attempts==1 requires refire_on_unconfirmed=False"
            )
