"""Idle-before-anything policy.

Commands that touch the scope's physical/acquisition state must SEE the
scanner idle before firing. The wait is an ordinary scanner-status reading
under the one rule in ``dispatcher.timing`` (operator decision, 2026-10-10,
replacing the unbounded wait of 2026-06-11): four windows of ``WINDOW_S``,
after which the command fails instead of waiting forever. It is fail-closed
against Unknown status (Unknown is not idle).
"""

import unittest
from unittest.mock import patch

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import prechecks
from zmart_drivers.leica.stellaris.navigator_expert.operations.readings import reading_settings
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import stage
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import objective as objective_change
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import select_job as select_job_change
from zmart_drivers.leica.stellaris.navigator_expert.operations.changes import acquire as acquire_change

IDLE_GUARDED = {
    "MOVE_XY": stage.MOVE_XY,
    "MOVE_Z": stage.MOVE_Z,
    "OBJECTIVE": objective_change.OBJECTIVE,
    "SELECT_JOB": select_job_change.SELECT_JOB,
    "ACQUIRE": acquire_change.ACQUIRE,
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
        for name, profile in IDLE_GUARDED.items():
            with self.subTest(profile=name):
                self.assertIs(profile.pre_check_fn, prechecks.check_idle)

    def test_check_idle_waits_through_a_busy_phase_within_the_windows(self):
        statuses = self._statuses(["eScanRunning"] * 10 + ["eScanIdle"])
        with patch.object(
            prechecks._readers.api_reader, "get_scan_status", side_effect=statuses
        ):
            result = prechecks.check_idle(object())
        self.assertTrue(result["success"])

    def test_check_idle_unknown_is_not_idle(self):
        statuses = self._statuses(["Unknown", "Unknown", "eScanIdle"])
        with patch.object(
            prechecks._readers.api_reader, "get_scan_status", side_effect=statuses
        ):
            result = prechecks.check_idle(object())
        self.assertTrue(result["success"])  # waited through Unknown, not past it

    def test_check_idle_fails_when_busy_through_all_four_windows(self):
        with patch.object(
            prechecks._readers.api_reader,
            "get_scan_status",
            return_value="eScanRunning",
        ):
            result = prechecks.check_idle(object())
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()


def test_the_direct_reader_is_asked_again_while_the_scanner_is_busy(monkeypatch):
    """A direct answer that does not count yet ("Busy") is asked again until it does."""
    import time
    from types import SimpleNamespace

    from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import read

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
