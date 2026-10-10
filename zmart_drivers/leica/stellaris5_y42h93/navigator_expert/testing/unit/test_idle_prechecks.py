"""Idle-before-anything policy.

Commands that touch the scope's physical/acquisition state must SEE the
scanner idle before firing. The wait is an ordinary scanner-status reading
under the one rule in ``dispatcher.tuning`` (operator decision, 2026-10-10,
replacing the unbounded wait of 2026-06-11): four windows of ``WINDOW_S``,
after which the command fails instead of waiting forever. It is fail-closed
against Unknown status (Unknown is not idle).
"""

import unittest
from unittest.mock import patch

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import profiles
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import prechecks

IDLE_GUARDED = [
    "MOVE_XY",
    "MOVE_Z",
    "OBJECTIVE",
    "SELECT_JOB",
    "ACQUIRE",
]


class TestIdlePrecheckPolicy(unittest.TestCase):
    def setUp(self):
        self._prior = profiles.STATE_READERS
        profiles.STATE_READERS = profiles.StateReaderProfile(scan_status_mode="api")

    def tearDown(self):
        profiles.STATE_READERS = self._prior

    def _statuses(self, values):
        remaining = list(values)
        return lambda client: remaining.pop(0) if len(remaining) > 1 else remaining[0]

    def test_physical_commands_see_idle_before_firing(self):
        for name in IDLE_GUARDED:
            with self.subTest(profile=name):
                self.assertIs(getattr(profiles, name).pre_check_fn, prechecks.check_idle)

    def test_check_idle_waits_through_a_busy_phase_within_the_windows(self):
        statuses = self._statuses(["eScanRunning"] * 10 + ["eScanIdle"])
        with patch.object(prechecks._readers.api_reader, "get_scan_status", side_effect=statuses):
            result = prechecks.check_idle(object())
        self.assertTrue(result["success"])

    def test_check_idle_unknown_is_not_idle(self):
        statuses = self._statuses(["Unknown", "Unknown", "eScanIdle"])
        with patch.object(prechecks._readers.api_reader, "get_scan_status", side_effect=statuses):
            result = prechecks.check_idle(object())
        self.assertTrue(result["success"])  # waited through Unknown, not past it

    def test_check_idle_fails_when_busy_through_all_four_windows(self):
        with patch.object(
            prechecks._readers.api_reader, "get_scan_status", return_value="eScanRunning"
        ):
            result = prechecks.check_idle(object())
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
