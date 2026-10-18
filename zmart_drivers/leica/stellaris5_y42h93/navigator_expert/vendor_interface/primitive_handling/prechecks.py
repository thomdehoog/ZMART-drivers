"""
Pre-flight check functions.
============================
Functions that run before a command fires to ensure preconditions are met.
Each function owns its own polling loop internally — the backbone never
sleeps or polls. It calls the function once and gets back a result dict.

Currently contains only ``check_idle`` (wait for scanner idle, by the one
rule in ``tuning``). Future
pre-flight checks (e.g. wait for temperature stability, wait for stage
settled) follow the same contract: ``callable(client) → result dict``.
Extra parameters are pre-bound with ``partial`` at profile definition
time; the command function binds ``client`` via lambda.

Import restrictions: only runtime utilities, readers, and stdlib. Nothing
from command wrappers, profiles, or confirmations.
"""

import logging
import time

from . import state as _readers
from ...dispatcher import timing as _timing
from ...dispatcher.writer import _make_log_entry

log = logging.getLogger(__name__)


def check_idle(client):
    """Wait for the scanner to be idle, by the one rule; result dict.

    The scanner status is an ordinary reading under the one rule, four
    windows of three seconds, asked of the direct reader when the profile
    reads the status directly, else (and when the direct reader fails) of
    the API alone. Every other reading of the status falls back to the API
    raced against the log; this one decides whether a command may fire,
    which is for a source that answers on demand to decide (the README's
    rule for command-gating reads). The log writes the status only when it
    changes, so its "idle" goes stale half a second after a scan ends, and
    a log-only run would otherwise fail every move. A scanner that stays
    busy fails the check after four windows instead of waiting forever.

    Returns:
        {"success": True, "logs": [...]} once the scanner reads idle,
        {"success": False, "logs": [...]} when it did not within four windows.
    """
    t0 = time.perf_counter()
    reading = _readers.get_scan_status(
        client,
        mode=_readers.on_demand_mode("scan_status"),
        fallback="api",
        diagnostics=True,
        accept=lambda s: "Idle" in s,
    )
    if reading is not None and reading.error is None:
        return {"success": True, "logs": []}
    status = None if reading is None else reading.value
    msg = (
        f"Scanner not idle after {time.perf_counter() - t0:.1f}s "
        f"({_timing.WINDOWS} windows of {_timing.WINDOW_S}s, last status: {status or 'unknown'})"
    )
    log.warning(msg)
    return {"success": False, "logs": [_make_log_entry("warning", msg)]}
