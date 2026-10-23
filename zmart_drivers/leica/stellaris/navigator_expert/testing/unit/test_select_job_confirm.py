"""How a job change is confirmed: the read order of the ``selected_job`` reading.

The direct reader answers first: it reads LAS X's live selection. When it
cannot answer, the profile's fallback does (``hybrid``: the CAM API's job list,
then the log's ``CurrentBlock`` event). The FIRST hazard is the real-scope
A->B->A restore: the CAM API's job list can be persistently stale, so an API
that already named the target BEFORE the change cannot witness it. When the
API's answer from before the change is missing, or already the target, the log
alone confirms (``procedures/changes.py``, ``admissible``).

The read order is pinned twice: what ``select_job`` hands the dispatcher (the
write's ``confirm``), and what one read-back window of that order answers with
each source faked (``dispatcher/write.read_back``).
"""

import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import write
from zmart_drivers.leica.stellaris.navigator_expert.dispatcher.read import Reading
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as reading_settings
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct.client import HardwareServerUnavailable
from zmart_drivers.leica.stellaris.navigator_expert.procedures import changes
from zmart_drivers.leica.stellaris.navigator_expert.procedures import readings as readers


class SelectJobCase(unittest.TestCase):
    def setUp(self):
        self._profile = reading_settings.STATE_READERS
        self.addCleanup(self._restore)

    def _restore(self):
        reading_settings.STATE_READERS = self._profile

    def _use(self, **kwargs):
        reading_settings.STATE_READERS = reading_settings.StateReaderProfile(**kwargs)


# =============================================================================
# One read-back window of the read order, each source faked
# =============================================================================


def unreachable():
    raise HardwareServerUnavailable("the hardware server is not reachable")


class TestReadOrder(SelectJobCase):
    """``write.read_back`` of ``writes.select_job(...).confirm``, with the sources faked."""

    def _read_back(self, job_name, *, direct, answers, fallback=None, observed_at=None):
        """One window of the job change's confirmation; ``(result, sources asked after the direct reader)``.

        *direct* is the direct reader's answer (a function); *answers* the
        selected job's name each other source gives (``{"api": ..., "log": ...}``;
        None: no answer). Answers are seen after the change, or at *observed_at*.
        """
        asked = []

        def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
            asked.append(leg)
            name = answers.get(leg)
            # A moment later than now: the clock can read the same twice in a row.
            seen = time.time() + 1.0 if observed_at is None else observed_at
            return Reading(value=None if name is None else {"Name": name}, source=leg, observed_at=seen, age_s=0.0)

        spec = replace(reading_settings.DATUMS["selected_job"], direct_fn=direct)
        since = time.time()
        with (
            patch.dict(reading_settings.DATUMS, {"selected_job": spec}),
            patch.object(reading_settings, "ask", ask),
        ):
            confirm = writes.select_job(job_name, fallback=fallback).confirm
            result = write.read_back(reading_settings, None, confirm, since=since)()
        return result, asked

    def test_the_direct_reader_confirms_a_job_change(self):
        result, asked = self._read_back("HiRes", direct=lambda: {"Name": "HiRes"}, answers={"api": "HiRes"})
        self.assertTrue(result["success"])
        self.assertEqual(asked, [])

    def test_with_the_direct_reader_unavailable_the_api_answers(self):
        result, asked = self._read_back("HiRes", direct=unreachable, answers={"api": "HiRes"})
        self.assertTrue(result["success"])
        self.assertEqual(asked, ["api"])

    def test_a_stale_api_list_leaves_it_to_the_log(self):
        result, asked = self._read_back("HiRes", direct=unreachable, answers={"api": "AF Job", "log": "HiRes"})
        self.assertTrue(result["success"])
        self.assertEqual(asked, ["api", "log"])

    def test_with_the_log_fallback_the_api_is_never_asked(self):
        """The A->B->A case: the API names the target, but only the log may confirm."""
        result, asked = self._read_back(
            "AF Job", direct=unreachable, answers={"api": "AF Job", "log": None}, fallback="log"
        )
        self.assertFalse(result["success"])
        self.assertEqual(set(asked), {"log"})

    def test_a_log_line_from_before_the_change_does_not_confirm_it(self):
        result, _ = self._read_back(
            "HiRes", direct=unreachable, answers={"log": "HiRes"}, fallback="log", observed_at=time.time() - 60.0
        )
        self.assertFalse(result["success"])

    def test_the_direct_reader_naming_another_job_is_not_overruled(self):
        """While the direct reader answers, it alone decides; the fallback is for when it cannot."""
        result, asked = self._read_back("HiRes", direct=lambda: {"Name": "Overview"}, answers={"api": "HiRes"})
        self.assertFalse(result["success"])
        self.assertEqual(asked, [])


# =============================================================================
# The read order select_job hands the dispatcher
# =============================================================================


class TestSelectJobReadOrder(SelectJobCase):
    """What ``select_job`` hands ``dispatch.run``: the direct reader first, then the admissible fallback."""

    def _sent(self, job_name, *, direct_selected=None, baseline=("Overview", "ok")):
        """Run select_job with the dispatcher faked; ``(the write it was handed, the result)``."""
        sent = []

        def run(profile, client, change, **_kwargs):
            sent.append(change)
            return {"success": True, "confirmed": True, "message": change.description, "logs": []}

        name, reason = baseline
        jobs = None if name is None else [{"Name": name, "IsSelected": True}]
        direct = (lambda: {"Name": direct_selected}) if direct_selected else unreachable
        with (
            patch.object(changes.direct_reader, "get_selected_job", side_effect=direct),
            patch.object(changes, "selected_job_from_log", return_value=None),
            patch.object(changes, "api_baseline", return_value=(name, jobs, reason)) as api_baseline,
            patch.object(changes.dispatch, "run", side_effect=run),
        ):
            result = changes.select_job(object(), job_name)
        self.api_baseline_asked = api_baseline.called
        return (sent[0] if sent else None), result

    def test_the_default_order_is_the_direct_reader_then_hybrid(self):
        profile = reading_settings.StateReaderProfile()
        self.assertEqual((profile.selected_job_mode, profile.selected_job_confirm_fallback), ("direct", "hybrid"))
        self.assertFalse(hasattr(profile, "selected_job_confirm_source"))

    def test_a_usable_api_baseline_keeps_the_api_in_the_order(self):
        change, _ = self._sent("HiRes", baseline=("Overview", "ok"))
        self.assertEqual(change.confirm.reading, "selected_job")
        self.assertEqual((change.confirm.mode, change.confirm.fallback), ("direct", "hybrid"))
        self.assertTrue(change.confirm.accept({"Name": "HiRes"}))
        self.assertFalse(change.confirm.accept({"Name": "Overview"}))

    def test_without_an_api_baseline_the_log_confirms(self):
        change, _ = self._sent("HiRes", baseline=(None, "api_timeout"))
        self.assertEqual((change.confirm.mode, change.confirm.fallback), ("direct", "log"))

    def test_an_api_baseline_that_is_already_the_target_leaves_it_to_the_log(self):
        """A->B->A restore: the API named the target before the change, so it cannot witness it."""
        change, result = self._sent("AF Job", baseline=("AF Job", "ok"))
        self.assertEqual((change.confirm.mode, change.confirm.fallback), ("direct", "log"))
        self.assertTrue(any("API reported target already selected" in e["msg"] for e in result["logs"]))

    def test_the_direct_readers_selection_is_the_baseline(self):
        change, _ = self._sent("HiRes", direct_selected="Overview", baseline=(None, "api_timeout"))
        self.assertEqual((change.confirm.mode, change.confirm.fallback), ("direct", "hybrid"))
        self.assertFalse(self.api_baseline_asked)

    def test_the_direct_reader_naming_the_target_sends_nothing(self):
        change, result = self._sent("HiRes", direct_selected="HiRes")
        self.assertIsNone(change)
        self.assertEqual((result["success"], result["confirmed"]), (True, True))
        self.assertEqual(result["timing"]["attempts"], 0)

    def test_the_api_fallback_is_taken_as_named(self):
        self._use(selected_job_confirm_fallback="api")
        with patch.object(readers, "get_jobs", return_value=[{"Name": "Overview", "IsSelected": True}]):
            change, _ = self._sent("HiRes")
        self.assertEqual((change.confirm.mode, change.confirm.fallback), ("direct", "api"))

    def test_a_hybrid_reading_order_follows_the_same_rule(self):
        """A validator run that reads everything hybrid keeps the stale API out too."""
        self._use(selected_job_mode="hybrid")
        change, _ = self._sent("HiRes", baseline=(None, "api_timeout"))
        self.assertEqual(change.confirm.mode, "log")

    def test_the_limits_see_the_job_name_and_the_request_is_select_job(self):
        change, _ = self._sent("HiRes")
        self.assertEqual((change.command, change.values, change.request), ("select_job", {"job_name": "HiRes"}, "PyApiSelectJobByName"))
        self.assertEqual(change.ready.reading, "scan_status")
        self.assertFalse(change.never_resend)


# =============================================================================
# Before the change: is it needed, and what was selected
# =============================================================================


class TestPrepareSelectJob(SelectJobCase):
    def test_api_fallback_noop_proof_from_api(self):
        self._use(selected_job_confirm_fallback="api")
        jobs = [{"Name": "AF Job", "IsSelected": True}]
        with patch.object(readers, "get_jobs", return_value=jobs):
            noop, context = changes.prepare_select_job(None, "AF Job")
        self.assertIsNotNone(noop)
        self.assertTrue(noop["success"])
        self.assertTrue(noop["confirmed"])
        self.assertEqual(context["api_baseline_name"], "AF Job")

    def test_hybrid_api_already_target_is_not_noop_proof(self):
        """The no-op edge: log stale/silent + api already reads target ->
        FIRE (and possibly end unconfirmed). A stale API must never suppress a
        real command, and the baseline records the target."""
        self._use(selected_job_confirm_fallback="hybrid")
        jobs = [{"Name": "AF Job", "IsSelected": True}]
        with (
            patch.object(changes, "selected_job_from_log", return_value=None),
            patch.object(changes, "api_baseline", return_value=("AF Job", jobs, "ok")),
        ):
            noop, context = changes.prepare_select_job(None, "AF Job")
        self.assertIsNone(noop)  # fires despite api==target
        self.assertEqual(context["api_baseline_name"], "AF Job")
        self.assertTrue(context["api_said_selected"])

    def test_hybrid_noop_proof_comes_from_log_state(self):
        self._use(selected_job_confirm_fallback="hybrid")
        jobs = [{"Name": "Overview", "IsSelected": True}]
        with (
            patch.object(readers, "get_jobs", return_value=jobs),
            patch.object(changes, "selected_job_from_log", return_value="AF Job"),
        ):
            noop, _ = changes.prepare_select_job(None, "AF Job")
        self.assertIsNotNone(noop)
        self.assertTrue(noop["confirmed"])

    def test_log_fallback_keeps_log_state_noop(self):
        self._use(selected_job_confirm_fallback="log")
        jobs = [{"Name": "Overview", "IsSelected": True}]
        with (
            patch.object(readers, "get_jobs", return_value=jobs),
            patch.object(changes, "selected_job_from_log", return_value="AF Job"),
        ):
            noop, _ = changes.prepare_select_job(None, "AF Job")
        self.assertIsNotNone(noop)

    def test_log_fallback_does_not_touch_api_when_log_has_no_noop(self):
        self._use(selected_job_confirm_fallback="log")
        with (
            patch.object(changes, "selected_job_from_log", return_value=None),
            patch.object(readers, "get_jobs") as api_jobs,
            patch.object(readers, "get_job_settings") as api_settings,
        ):
            noop, context = changes.prepare_select_job(None, "AF Job")

        self.assertIsNone(noop)
        self.assertEqual(context["api_baseline_reason"], "not_attempted")
        api_jobs.assert_not_called()
        api_settings.assert_not_called()


if __name__ == "__main__":
    unittest.main()
