"""How a change is seen to have taken effect: reading back, and racing two ways to confirm.

Every confirmation reads the state back for one window and answers
``{"success": bool, "logs": [...]}``; the profile of the change says how many
windows there are and whether the change is sent again between them.
``readback`` is one look at the job's settings; ``race_confirmations`` lets two
ways of confirming (the API and the log) race, and is a pass-through when there
is only one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import logging
import queue
import threading
import time

from ...dispatcher.dispatch import make_log_entry
from ...primitives.cam_api.parsing import make_changeable_copy
from ...procedures import readings as readers

log = logging.getLogger(__name__)


def race_confirmations(api_leg=None, log_leg=None, *, label="", budget_s=None):
    """Compose confirmation legs into one dispatch-compatible callable.

    Every command's confirmation routes through here. A single leg passes
    through UNCHANGED - the returned callable is the leg itself, so the
    dispatch contract (``() -> {"success": bool, "logs": [...]}``) and the
    behavior of every existing single-leg confirmation are bit-identical.

    With two legs, the race returns as soon as the first leg confirms.
    Legs must be target-gated and fail closed on their own; the race adds
    no judgement of its own - it only arbitrates *which admissible evidence
    arrived first* and reports the outcome:

    - the winning leg and elapsed time (result ``logs[]``),
    - a WARNING (driver log + ``logs[]``) when the other leg had already
      failed - source disagreement is surfaced, never hidden,
    - abandonment of a still-pending leg (the CAM read is non-cancellable,
      so abandonment, not cancellation, is the honest option),
    - fail-closed ``success=False`` when no leg confirms or ``budget_s``
      expires. ``budget_s`` is mandatory for a dual-leg race and must fit
      inside one dispatcher confirm attempt.

    The race itself never touches the CAM and takes NO in-flight claim:
    the api leg performs all its CAM access through the routed readers,
    whose per-read in-flight cap already provides the single-flight
    guarantee. (Wrapping the whole leg in the client's claim starved the
    leg's own routed reads - CF-01.) An abandoned api leg can therefore
    hold at most one raw CAM read in flight, and the cap keeps that read
    from being overlapped; select_job additionally sizes the api leg's
    poll window inside the budget so an abandoned leg drains promptly
    (CF-05).
    """
    if log_leg is None:
        return api_leg
    if api_leg is None:
        return log_leg
    if budget_s is None:
        raise ValueError("a dual-leg confirmation race requires budget_s")

    def failed_outcome(level, msg):
        return {"success": False, "logs": [make_log_entry(level, msg)]}

    def run_leg(tag, leg, results):
        try:
            outcome = leg()
        except Exception as exc:
            outcome = failed_outcome(
                "warning",
                f"{label} | {tag} confirmation leg raised: {type(exc).__name__}: {exc}",
            )
        else:
            if not isinstance(outcome, dict):
                outcome = failed_outcome(
                    "warning",
                    f"{label} | {tag} confirmation leg returned "
                    f"{type(outcome).__name__}, expected dict",
                )
        results.put((tag, outcome))

    def run_race():
        results = queue.Queue()
        for tag, leg in (("api", api_leg), ("log", log_leg)):
            threading.Thread(
                target=run_leg,
                args=(tag, leg, results),
                name=f"lasx-confirm-{tag}",
                daemon=True,
            ).start()

        started = time.monotonic()
        deadline = started + budget_s
        outcomes = {}
        winner = None
        while len(outcomes) < 2:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                tag, outcome = results.get(timeout=remaining)
            except queue.Empty:
                break
            outcomes[tag] = outcome
            if outcome.get("success"):
                winner = tag
                break

        if winner is not None:
            loser = "log" if winner == "api" else "api"
            # The loser may have finished in the instant before the win: take
            # it if it is already there, so a completed leg is reported as
            # disagreement, not misreported as abandoned.
            while loser not in outcomes:
                try:
                    tag, outcome = results.get_nowait()
                except queue.Empty:
                    break
                outcomes[tag] = outcome

        elapsed = time.monotonic() - started
        logs = []
        for tag in ("api", "log"):
            if tag in outcomes:
                logs.extend(outcomes[tag].get("logs", []))

        if winner is not None:
            loser = "log" if winner == "api" else "api"
            logs.append(
                make_log_entry(
                    "info", f"{label} | confirmed by {winner} leg ({elapsed:.3f}s)"
                )
            )
            if loser in outcomes:
                msg = f"{label} | {loser} leg had not confirmed when the {winner} leg confirmed"
                log.warning(msg)
                logs.append(make_log_entry("warning", msg))
            else:
                logs.append(
                    make_log_entry(
                        "info", f"{label} | {loser} leg still pending at win; abandoned"
                    )
                )
            return {"success": True, "logs": logs}

        if len(outcomes) < 2:
            pending = [t for t in ("api", "log") if t not in outcomes]
            msg = (
                f"{label} | confirmation race budget {budget_s:.1f}s "
                f"exhausted; still pending: {', '.join(pending)}"
            )
            log.warning(msg)
            logs.append(make_log_entry("warning", msg))
        else:
            logs.append(
                make_log_entry(
                    "info", f"{label} | no confirmation leg confirmed ({elapsed:.3f}s)"
                )
            )
        return {"success": False, "logs": logs}

    return run_race


def readback(client, job_name, *, deadline, observed_after=None, mode=None):
    """Watch the job's settings until *deadline* (``time.monotonic()``); a copy, or None.

    One look in a confirmation's window: both sources the profile allows, the
    first complete settings from either. The caller compares them with its
    target and looks again after ``POLL_S`` while they do not match.
    """
    reading = readers.get_job_settings(
        client,
        job_name,
        mode=mode,
        diagnostics=True,
        deadline=deadline,
    )
    if reading is None:
        return None
    if hasattr(reading, "value"):
        if reading.value is None:
            return None
        if (
            observed_after is not None
            and reading.observed_at is not None
            and reading.observed_at <= observed_after
        ):
            return None
        raw = reading.value
    else:
        raw = reading
    return make_changeable_copy(raw)


def reading_value_after(reading, observed_after):
    """Return a diagnostic reading's value only if it is post-start.

    Tests sometimes patch routed readers with their old plain return shape; those
    values are accepted here so the tests can stay focused on confirmation logic.
    Real routed readers return ``Reading`` and get the timestamp gate.
    """
    if reading is None:
        return None
    if not hasattr(reading, "value"):
        return reading
    if reading.value is None or reading.observed_at is None:
        return None
    if reading.observed_at <= observed_after:
        return None
    return reading.value
