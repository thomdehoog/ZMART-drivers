"""The one rule for waiting on LAS X: four windows of three seconds.

Every reading watches both sources for a window, the first answer that counts
as a success wins from whichever source gives it, and an unanswered read is
requested again at the next window. These tests shrink the window so they run
in well under a second each; the rule is the same at any window length.

They pin the two failures the LAS X simulator showed on 2026-10-10: a reading
that gave up in 0.06 s because the API was busy with an abandoned question,
and an idle check that waited forever in log-only mode.
"""

import ast
import pathlib
import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    get as capabilities,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    objective_shift,
    profiles,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    prechecks,
    tuning,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import (
    read as readers,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import delivery

WINDOW_S = 0.5


@pytest.fixture(autouse=True)
def short_windows(monkeypatch):
    """Four windows of 0.5 s, looking every 10 ms, checking every 1 ms."""
    monkeypatch.setattr(tuning, "WINDOW_S", WINDOW_S)
    monkeypatch.setattr(tuning, "POLL_S", 0.01)
    monkeypatch.setattr(tuning, "ANSWER_POLL_S", 0.001)
    monkeypatch.setattr(profiles, "STATE_READERS", profiles.StateReaderProfile())


def settings(job="HiRes", slot=3):
    """Complete job settings: the job's name, an objective slot, and geometry."""
    return {
        "jobName": job,
        "objective": {"slotIndex": slot},
        "imageSize": "100.0 um x 100.0 um",
        "zPosition": {"z-wide": {"position": 12.0}},
    }


def fresh_log(value_by_reader):
    """Patches that make the LAS X log answer *value_by_reader*, fresh."""
    snapshot = SimpleNamespace(now=100.0)
    patches = [
        patch.object(readers.log_reader, "parse_log", return_value=snapshot),
        patch.object(readers.log_reader, "ages", return_value={"xy": 0.1, "jobs": {}}),
    ]
    for name, value in value_by_reader.items():
        patches.append(patch.object(readers.log_reader, name, return_value=value))
    return patches


class Patched:
    """Enter a list of patches as one context."""

    def __init__(self, patches):
        self.patches = patches

    def __enter__(self):
        return [p.__enter__() for p in self.patches]

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.__exit__(*exc)


def test_a_busy_api_is_waited_for_not_skipped():
    """The simulator failure: an earlier question still holds the API.

    The reading must wait for the API to be free and ask, not come back empty
    at once because the log has nothing fresh for the job.
    """
    client = object()
    release = threading.Event()
    earlier_started = threading.Event()
    calls = []

    def api_settings(_client, job_name, **_kwargs):
        calls.append(time.monotonic())
        if len(calls) == 1:
            earlier_started.set()
            release.wait(1.0)
            return None
        return settings(job_name)

    with Patched(fresh_log({"get_job_settings": None})):
        with patch.object(readers.api_reader, "get_job_settings", side_effect=api_settings):
            earlier = threading.Thread(
                target=lambda: readers.get_job_settings(client, "Overview", mode="api"),
                daemon=True,
            )
            earlier.start()
            assert earlier_started.wait(1.0)
            threading.Timer(0.05, release.set).start()
            t0 = time.monotonic()
            reading = readers.get_job_settings(client, "HiRes", diagnostics=True)
            elapsed = time.monotonic() - t0
    assert reading.value == settings("HiRes")
    assert reading.source == "api"
    assert elapsed >= 0.04
    assert len(calls) >= 2


def test_an_incomplete_answer_does_not_win():
    """The log answers first without an objective slot; the API's complete answer wins."""
    incomplete = {"jobName": "HiRes", "imageSize": "100.0 um x 100.0 um"}

    def slow_api(_client, job_name, **_kwargs):
        time.sleep(0.05)
        return settings(job_name)

    with Patched(fresh_log({"get_job_settings": incomplete})):
        with patch.object(readers.api_reader, "get_job_settings", side_effect=slow_api):
            reading = readers.get_job_settings(object(), "HiRes", diagnostics=True)
    assert reading.source == "api"
    assert reading.value["objective"]["slotIndex"] == 3


def test_settings_for_another_job_do_not_win():
    """A reply that belongs to another job is not this job's settings."""
    with Patched(fresh_log({"get_job_settings": None})):
        with patch.object(
            readers.api_reader, "get_job_settings", return_value=settings("Overview")
        ):
            assert readers.get_job_settings(object(), "HiRes") is None


def test_no_head_start_for_the_log():
    """The API answers first with a complete answer; it is taken at once."""
    api_value = {"x_um": 1.0, "y_um": 2.0}

    def slow_log(*_args, **_kwargs):
        time.sleep(0.3)
        return {"x_um": 5.0, "y_um": 6.0}

    with Patched(fresh_log({})):
        with (
            patch.object(readers.log_reader, "get_xy", side_effect=slow_log),
            patch.object(readers.api_reader, "get_xy", return_value=api_value),
        ):
            t0 = time.monotonic()
            reading = readers.get_xy(object(), diagnostics=True)
            elapsed = time.monotonic() - t0
    assert reading.source == "api"
    assert reading.value == api_value
    assert elapsed < 0.25


def test_four_windows_then_unknown():
    """Sources that never succeed: unknown after four windows, one read requested per window."""
    calls = []

    def unanswered(_client, *, deadline, **_kwargs):
        calls.append(time.monotonic())
        time.sleep(max(0.0, deadline - time.monotonic()))
        return None

    with Patched(fresh_log({"get_xy": None})):
        with patch.object(readers.api_reader, "get_xy", side_effect=unanswered):
            t0 = time.monotonic()
            assert readers.get_xy(object()) is None
            elapsed = time.monotonic() - t0
    assert 4 * WINDOW_S <= elapsed < 4 * WINDOW_S + 1.0
    assert len(calls) == 4
    gaps = [later - earlier for earlier, later in zip(calls, calls[1:])]
    assert all(gap >= WINDOW_S * 0.9 for gap in gaps)


def test_an_unanswered_read_is_requested_again_at_the_next_window():
    """LAS X drops the first request: the second goes out at the next window and succeeds."""
    calls = []
    value = {"x_um": 1.0, "y_um": 2.0}

    def drops_first(_client, *, deadline, **_kwargs):
        calls.append(time.monotonic())
        if len(calls) == 1:
            time.sleep(max(0.0, deadline - time.monotonic()))
            return None
        return value

    with Patched(fresh_log({"get_xy": None})):
        with patch.object(readers.api_reader, "get_xy", side_effect=drops_first):
            assert readers.get_xy(object()) == value
    assert len(calls) >= 2
    assert calls[1] - calls[0] >= WINDOW_S * 0.9


def test_an_answer_that_is_not_a_success_is_requested_again_within_the_window():
    """An answer that does not count is asked again after one poll interval, not a window."""
    answers = iter([{"x_um": float("nan"), "y_um": 2.0}, {"x_um": 1.0, "y_um": 2.0}])

    with Patched(fresh_log({"get_xy": None})):
        with patch.object(readers.api_reader, "get_xy", side_effect=lambda *a, **k: next(answers)):
            t0 = time.monotonic()
            assert readers.get_xy(object()) == {"x_um": 1.0, "y_um": 2.0}
            assert time.monotonic() - t0 < WINDOW_S / 2


def test_an_old_log_answer_never_wins():
    """The log reader refuses an answer older than its freshness limit; nothing else answers."""
    with Patched(fresh_log({"get_xy": None})):
        with patch.object(readers.api_reader, "get_xy", return_value=None):
            assert readers.get_xy(object(), mode="hybrid") is None


def test_the_scanner_status_is_an_api_only_reading():
    """Nothing decides on the log's scanner status, so the driver does not read it.

    The idle check and the end-of-acquisition wait both ask the API, which
    answers on demand. The log writes ``Acquire/AcquisitionState`` only when
    the state changes, so it went stale half a second after every scan
    (decided 2026-10-10). Like the job list, the scanner status has no log leg.
    """
    assert capabilities.spec("scan_status").log_fn is None
    assert profiles.StateReaderProfile().scan_status_mode == "api"
    assert not hasattr(profiles.StateReaderProfile(), "scan_status_log_max_age_s")
    assert not hasattr(readers.log_reader, "get_scan_status")
    reading = readers.get_scan_status(object(), mode="log", diagnostics=True)
    assert isinstance(reading.error, capabilities.UnsupportedSource)


def test_the_idle_check_asks_the_api_even_in_a_log_only_run():
    """The simulator hang: a log-only run once asked the log for idle.

    The log writes the scanner status only when it changes, so its "idle"
    goes stale half a second after a scan ends, and the check waited forever.
    Whether a command may fire is decided by the API alone, which answers on
    demand (the README's rule for command-gating reads).
    """
    profiles.STATE_READERS = profiles.StateReaderProfile(
        **{
            field: "log"
            for field in ("xy_mode", "job_settings_mode", "selected_job_mode")
        }
    )
    with patch.object(readers.api_reader, "get_scan_status", return_value="eScanIdle") as api:
        assert prechecks.check_idle(object())["success"] is True
    api.assert_called()


def test_the_idle_check_fails_after_four_windows_of_a_busy_scanner():
    with patch.object(readers.api_reader, "get_scan_status", return_value="eScanRunning"):
        t0 = time.monotonic()
        result = prechecks.check_idle(object())
        elapsed = time.monotonic() - t0
    assert result["success"] is False
    assert 4 * WINDOW_S <= elapsed < 4 * WINDOW_S + 1.0


def test_a_job_switch_keeps_its_objective_while_the_api_is_busy():
    """objective_shift reads the new job's slot after the switch, with the API still busy."""
    client = object()
    release = threading.Event()
    held = threading.Event()

    def api_settings(_client, job_name, **_kwargs):
        if not held.is_set():
            held.set()
            release.wait(1.0)
            return None
        return settings(job_name, slot=3)

    with Patched(fresh_log({"get_job_settings": None})):
        with patch.object(readers.api_reader, "get_job_settings", side_effect=api_settings):
            threading.Thread(
                target=lambda: readers.get_job_settings(client, "Overview", mode="api"),
                daemon=True,
            ).start()
            assert held.wait(1.0)
            threading.Timer(0.05, release.set).start()
            before = {"slot": 3, "translations": {}, "x_um": 0.0, "y_um": 0.0, "z_wide_um": 0.0}
            outcome = objective_shift.compensate_after_change(client, "HiRes", before)
    assert outcome["ok"] is True
    assert outcome["objective_changed"] is False


def test_delivery_is_four_tries_of_one_window_without_a_pause():
    api_obj = MagicMock()
    api_obj.UpdateAwaitReceipt.return_value = False
    with patch("time.sleep") as sleep:
        assert delivery.deliver(api_obj) is False
    assert [c.args for c in api_obj.UpdateAwaitReceipt.call_args_list] == [(WINDOW_S,)] * 4
    sleep.assert_not_called()


def test_delivery_stops_trying_once_received():
    api_obj = MagicMock()
    api_obj.UpdateAwaitReceipt.side_effect = [False, True]
    assert delivery.deliver(api_obj) is True
    assert api_obj.UpdateAwaitReceipt.call_count == 2


def test_commands_and_scan_field_files_deliver_through_the_one_helper():
    """No other code hands a message to LAS X with its own tries or timeout."""
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import change
    from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.scanfields import files

    assert change.deliver is delivery.deliver
    assert files.deliver is delivery.deliver


GUARDED = ("actions", "dispatcher", "vendor_interface")
WAIT_NAME = re.compile(
    r"(timeout|poll_interval|max_retries|max_confirm_attempts|grace|deadline|window_s|retry_delay)",
    re.IGNORECASE,
)
#: The waits that stay outside the rule, by decision, as (file, name).
EXCEPTIONS = {
    # Acquisition is never sent again and watches one acquisition (ACQUIRE).
    ("actions/confirmations.py", "start_timeout"),
    ("actions/confirmations.py", "poll_interval"),
    ("actions/profiles.py", "max_retries"),
    ("actions/profiles.py", "max_confirm_attempts"),
    ("actions/profiles.py", "poll_interval"),
    ("actions/profiles.py", "start_timeout"),
    # LAS X filling in the error report after a command stays at 1 s.
    ("dispatcher/change.py", "ECHO_SETTLE_TIMEOUT_S"),
    # How old a log entry may be to count as current: freshness, not a wait.
    ("actions/profiles.py", "current_window_s"),
    # The rule itself.
    ("dispatcher/tuning.py", "WINDOW_S"),
}


def _literal_number(node):
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, (int, float))
        and not isinstance(node.value, bool)
    )


def _waits_with_their_own_numbers(root):
    """Every literal number given to a wait-shaped name, or slept, outside the rule."""
    found = []
    for folder in GUARDED:
        for path in sorted((root / folder).rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                named = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = node.args
                    positional = args.args[len(args.args) - len(args.defaults) :]
                    named += list(zip((a.arg for a in positional), args.defaults))
                    named += [
                        (a.arg, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d
                    ]
                elif isinstance(node, ast.Call):
                    named += [(kw.arg, kw.value) for kw in node.keywords if kw.arg]
                    func = node.func
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr == "sleep"
                        and node.args
                        and _literal_number(node.args[0])
                    ):
                        found.append(f"{rel}:{node.lineno} sleep({node.args[0].value})")
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    named += [(t.id, node.value) for t in targets if isinstance(t, ast.Name)]
                for name, value in named:
                    if (
                        WAIT_NAME.search(name)
                        and _literal_number(value)
                        and (rel, name) not in EXCEPTIONS
                    ):
                        found.append(f"{rel}:{node.lineno} {name}={value.value}")
    return found


def test_no_wait_on_las_x_has_its_own_number():
    """How long to wait and how often to look come from dispatcher.tuning only.

    A literal timeout, retry count, window, poll interval or sleep for waiting
    on LAS X anywhere else is a second rule; the named exceptions above are
    the decided ones.
    """
    root = pathlib.Path(readers.__file__).resolve().parents[1]
    assert _waits_with_their_own_numbers(root) == []
