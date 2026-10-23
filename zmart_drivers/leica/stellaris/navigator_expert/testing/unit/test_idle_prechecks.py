"""Idle-before-anything policy.

Commands that touch the scope's physical/acquisition state must SEE the
scanner idle before firing. The wait is an ordinary scanner-status reading
under the one rule in ``cam_api/profile.py`` (operator decision, 2026-10-10,
replacing the unbounded wait of 2026-06-11): four windows of ``WINDOW_S``,
after which the command fails instead of waiting forever. It is fail-closed
against Unknown status (Unknown is not idle).
"""

import unittest
from unittest.mock import patch

from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as reading_settings
from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import write
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes

#: The changes that touch the stage, the lens or the scan: each waits for the scanner idle (its ``ready``).
IDLE_WRITES = {
    "move_xy": writes.move_xy(0.0, 0.0, "um"),
    "move_z": writes.move_z("J", 0.0, "um", "galvo"),
    "set_objective": writes.set_objective("J", 1, "10x"),
    "select_job": writes.select_job("J"),
    "acquire": writes.acquire("J"),
}


class TestIdlePrecheckPolicy(unittest.TestCase):
    def setUp(self):
        self._prior = reading_settings.STATE_READERS
        reading_settings.STATE_READERS = reading_settings.StateReaderProfile(scan_status_mode="api")

    def tearDown(self):
        reading_settings.STATE_READERS = self._prior

    def _statuses(self, values):
        remaining = list(values)
        return lambda client: remaining.pop(0) if len(remaining) > 1 else remaining[0]

    def test_physical_commands_see_idle_before_firing(self):
        for name, change in IDLE_WRITES.items():
            with self.subTest(write=name):
                self.assertEqual(change.ready.reading, "scan_status")
                self.assertIs(change.ready.accept, writes.is_idle)

    def test_idle_is_read_from_a_source_that_answers_on_demand(self):
        # scan_status_mode="api" here; the log, which can still show the idle from before, is never asked.
        self.assertEqual((writes.idle().mode, writes.idle().fallback), ("api", "api"))

    def test_wait_ready_waits_through_a_busy_phase_within_the_windows(self):
        statuses = self._statuses(["eScanRunning"] * 10 + ["eScanIdle"])
        with patch.object(reading_settings.api_reader, "get_scan_status", side_effect=statuses):
            result = write.wait_ready(reading_settings, object(), writes.idle())()
        self.assertTrue(result["success"])

    def test_wait_ready_fails_when_busy_through_all_four_windows(self):
        with patch.object(reading_settings.api_reader, "get_scan_status", return_value="eScanRunning"):
            result = write.wait_ready(reading_settings, object(), writes.idle())()
        self.assertFalse(result["success"])
        self.assertIn("last status: eScanRunning", result["logs"][0]["msg"])

    def test_wait_ready_unknown_is_not_idle(self):
        statuses = self._statuses(["Unknown", "Unknown", "eScanIdle"])
        with patch.object(reading_settings.api_reader, "get_scan_status", side_effect=statuses) as asked:
            result = write.wait_ready(reading_settings, object(), writes.idle())()
        self.assertTrue(result["success"])  # waited through Unknown, not past it
        self.assertEqual(asked.call_count, 3)


if __name__ == "__main__":
    unittest.main()


def test_the_direct_reader_is_asked_again_while_the_scanner_is_busy(monkeypatch):
    """A direct answer that does not count yet ("Busy") is asked again until it does."""
    import time
    from types import SimpleNamespace

    from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as read

    answers = iter(["Busy", "Busy", "Idle"])
    spec = SimpleNamespace(direct_fn=lambda **_k: next(answers))
    monkeypatch.setattr(read.waiting, "POLL_S", 0.001)

    reading = read.watch_direct(
        spec,
        succeeded=lambda s: "Idle" in s,
        deadline=time.monotonic() + 5.0,
        observed_after=None,
        kwargs={},
        waiting_for_change=True,
    )

    assert reading is not None and reading.value == "Idle"


def test_a_capture_waits_for_idle_as_long_as_it_takes(monkeypatch):
    """The capture's idle wait has no limit: busy for longer than the four windows, it waits on."""
    from types import SimpleNamespace

    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import write as write_loops
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes

    answers = iter([(False, "Busy")] * 6 + [(True, "Idle")])
    monkeypatch.setattr(write_loops, "watch", lambda *a, **k: next(answers))
    vendor = SimpleNamespace(waiting=SimpleNamespace(WINDOWS=4, WINDOW_S=3))

    ready = write_loops.wait_ready(vendor, None, writes.acquire("HiRes").ready)

    assert ready()["success"] is True


def test_only_the_capture_waits_without_limit():
    from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes

    assert writes.acquire("HiRes").ready.without_limit is True
    assert writes.move_xy(1.0, 2.0, "um").ready.without_limit is False
    assert writes.select_job("HiRes").ready.without_limit is False
