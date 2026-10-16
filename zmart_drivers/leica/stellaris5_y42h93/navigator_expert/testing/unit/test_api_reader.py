"""
Unit tests for vendor_interface.api_reader (offline, no driver, no hardware).
===========================================================================
Exercises the one-request skeleton (``_request``) directly, plus the four
public readers built on it (get_xy, get_jobs, get_hardware_info,
get_job_settings), using small hand-built fakes -- no real CAM client, no
threads. Every test scripts exactly what a check for the answer sees on each
read via a property, so the scenario is deterministic regardless of timing.

Each call makes ONE request. Asking again is the routed readers' business
(``dispatcher.read``, the one rule in ``dispatcher.tuning``), so the stray-
reply hazard shows up across two calls: LAS X delivers read results by
writing into a shared model with no request/response correlation, so a
delayed reply to an *earlier* request can still land during a *later*
request's check (LC-09). ``get_job_settings`` has a validate hook that
catches this (its payload carries ``jobName``, a natural correlating field);
``get_xy``/``get_jobs``/``get_hardware_info`` do not, so they accept a stray
reply as if it were fresh.

    python -m pytest test_api_reader.py
"""

import json
import math
import time
import unittest
from types import SimpleNamespace

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    api_reader as A,
)

SHORT = 0.03


def _soon():
    return time.monotonic() + SHORT


def _client(**extra):
    """A fake CAM client with the PyApiCommand request channel _request
    touches directly, plus whichever reader-specific channel(s) a test needs."""
    return SimpleNamespace(
        PyApiCommand=SimpleNamespace(
            Model=SimpleNamespace(Command=""),
            UpdateAwaitReceipt=lambda timeout: True,
        ),
        **extra,
    )


class TestRequestCore(unittest.TestCase):
    """The one-request skeleton, exercised directly with scripted flush/read."""

    def test_an_answer_is_returned_at_once(self):
        value = A._request(
            _client(), command="Whatever", flush=lambda c: None, read=lambda c: 7, deadline=_soon()
        )
        self.assertEqual(value, 7)

    def test_no_answer_by_the_deadline_is_none(self):
        t0 = time.monotonic()
        value = A._request(
            _client(),
            command="Whatever",
            flush=lambda c: None,
            read=lambda c: None,
            deadline=_soon(),
        )
        self.assertIsNone(value)
        self.assertGreaterEqual(time.monotonic() - t0, SHORT * 0.9)

    def test_a_request_not_received_is_none_without_checking(self):
        reads = []
        client = _client()
        client.PyApiCommand.UpdateAwaitReceipt = lambda timeout: False
        value = A._request(
            client, command="Whatever", flush=lambda c: None, read=reads.append, deadline=_soon()
        )
        self.assertIsNone(value)
        self.assertEqual(reads, [])

    def test_the_check_ends_as_soon_as_the_caller_stops_watching(self):
        t0 = time.monotonic()
        value = A._request(
            _client(),
            command="Whatever",
            flush=lambda c: None,
            read=lambda c: None,
            deadline=time.monotonic() + 5.0,
            should_stop=lambda: time.monotonic() - t0 > 0.01,
        )
        self.assertIsNone(value)
        self.assertLess(time.monotonic() - t0, 1.0)

    def test_with_a_validate_hook_a_stray_reply_is_skipped_and_the_fresh_one_accepted(self):
        reads = iter([{"for": "earlier"}, {"for": "this"}])

        def validate(value):
            return A._ACCEPT if value["for"] == "this" else A._STALE

        value = A._request(
            _client(),
            command="Whatever",
            flush=lambda c: None,
            read=lambda c: next(reads),
            validate=validate,
            deadline=_soon(),
        )
        self.assertEqual(value, {"for": "this"})


class TestGetHardwareInfo(unittest.TestCase):
    def test_returns_parsed_json_on_success(self):
        payload = {"Microscope": {"name": "DM Manual-6"}, "LightSources": []}

        class _Model:
            @property
            def HWInfo(self):
                return json.dumps(payload)

            @HWInfo.setter
            def HWInfo(self, value):
                pass  # flush()'s sentinel reset; ignored, a fresh answer is always ready

        client = _client(PyApiGetConfocalHardwareInfo=SimpleNamespace(Model=_Model()))
        self.assertEqual(A.get_hardware_info(client, deadline=_soon()), payload)

    def test_has_no_correlating_field_so_a_stray_reply_is_silently_accepted(self):
        stale_payload = {"Microscope": {"name": "STALE-FROM-AN-EARLIER-REQUEST"}}

        class _Model:
            def __init__(self):
                self.resets = 0

            @property
            def HWInfo(self):
                return None if self.resets <= 1 else json.dumps(stale_payload)

            @HWInfo.setter
            def HWInfo(self, value):
                if value is None:  # flush()'s sentinel reset marks a new request
                    self.resets += 1

        client = _client(PyApiGetConfocalHardwareInfo=SimpleNamespace(Model=_Model()))
        self.assertIsNone(A.get_hardware_info(client, deadline=_soon()))
        result = A.get_hardware_info(client, deadline=_soon())
        self.assertEqual(result, stale_payload)  # accepted -- nothing marks it as stray


class TestGetXY(unittest.TestCase):
    def test_returns_parsed_position_on_success(self):
        class _Model:
            @property
            def XPosition(self):
                return 0.00005  # 50 um, in meters

            @XPosition.setter
            def XPosition(self, value):
                pass

            @property
            def YPosition(self):
                return 0.00003  # 30 um

            @YPosition.setter
            def YPosition(self, value):
                pass

        client = _client(PyApiGetXY=SimpleNamespace(Model=_Model()))
        result = A.get_xy(client, deadline=_soon())
        self.assertAlmostEqual(result["x_um"], 50.0)
        self.assertAlmostEqual(result["y_um"], 30.0)

    def test_has_no_correlating_field_so_a_stray_reply_is_silently_accepted(self):
        """Concretely, this is the correct_backlash hazard (LC-09): its
        A -> B -> A move revisits the same coordinate, so a stray reading
        from the *first* visit to A can satisfy a check meant to confirm
        the *second* -- the values are identical, and get_xy has nothing to
        tell the two apart."""
        stale_x_um, stale_y_um = 100.0, 200.0

        class _Model:
            def __init__(self):
                self.resets = 0

            @property
            def XPosition(self):
                return float("nan") if self.resets <= 1 else stale_x_um / 1e6

            @XPosition.setter
            def XPosition(self, value):
                if isinstance(value, float) and math.isnan(value):
                    self.resets += 1

            @property
            def YPosition(self):
                return float("nan") if self.resets <= 1 else stale_y_um / 1e6

            @YPosition.setter
            def YPosition(self, value):
                pass  # request already counted by the XPosition reset in the same flush()

        client = _client(PyApiGetXY=SimpleNamespace(Model=_Model()))
        self.assertIsNone(A.get_xy(client, deadline=_soon()))
        result = A.get_xy(client, deadline=_soon())
        self.assertAlmostEqual(result["x_um"], stale_x_um)
        self.assertAlmostEqual(result["y_um"], stale_y_um)


class TestGetJobs(unittest.TestCase):
    def test_returns_parsed_list_on_success(self):
        jobs = [{"Name": "Overview", "IsSelected": True}, {"Name": "HiRes", "IsSelected": False}]

        class _Model:
            @property
            def Jobs(self):
                return json.dumps(jobs)

            @Jobs.setter
            def Jobs(self, value):
                pass

        client = _client(PyApiGetJobsInformation=SimpleNamespace(Model=_Model()))
        self.assertEqual(A.get_jobs(client, deadline=_soon()), jobs)

    def test_has_no_correlating_field_so_a_stray_reply_is_silently_accepted(self):
        stale_jobs = [{"Name": "STALE-FROM-AN-EARLIER-REQUEST", "IsSelected": True}]

        class _Model:
            def __init__(self):
                self.resets = 0

            @property
            def Jobs(self):
                return None if self.resets <= 1 else json.dumps(stale_jobs)

            @Jobs.setter
            def Jobs(self, value):
                if value is None:
                    self.resets += 1

        client = _client(PyApiGetJobsInformation=SimpleNamespace(Model=_Model()))
        self.assertIsNone(A.get_jobs(client, deadline=_soon()))
        self.assertEqual(A.get_jobs(client, deadline=_soon()), stale_jobs)


class TestGetJobSettingsCorrelationGuard(unittest.TestCase):
    """The one reader with a real correlating field: the response carries
    jobName, so its validate hook can tell a stray reply to an earlier
    request apart from the answer to the job actually being asked about now."""

    def _client_with(self, model, *, job_name_received=True):
        return _client(
            PyApiGetJobSettingsByName=SimpleNamespace(
                Model=model, UpdateAwaitReceipt=lambda timeout: job_name_received
            )
        )

    def test_rejects_a_stray_reply_for_a_different_job_and_returns_the_fresh_one(self):
        stale = json.dumps({"jobName": "OLD_JOB", "imageSize": "100.0 um x 100.0 um"})
        fresh = json.dumps({"jobName": "NEW_JOB", "imageSize": "200.0 um x 200.0 um"})

        class _Model:
            def __init__(self):
                self.JobName = ""
                self.reads = 0

            @property
            def Settings(self):
                self.reads += 1
                return stale if self.reads == 1 else fresh

            @Settings.setter
            def Settings(self, value):
                pass

        result = A.get_job_settings(self._client_with(_Model()), "NEW_JOB", deadline=_soon())
        self.assertIsNotNone(result)
        self.assertEqual(result["jobName"], "NEW_JOB")

    def test_all_replies_stray_falls_closed_to_none_not_a_wrong_value(self):
        stale = json.dumps({"jobName": "OLD_JOB"})

        class _Model:
            def __init__(self):
                self.JobName = ""

            @property
            def Settings(self):
                return stale

            @Settings.setter
            def Settings(self, value):
                pass

        self.assertIsNone(
            A.get_job_settings(self._client_with(_Model()), "NEW_JOB", deadline=_soon())
        )

    def test_a_job_name_handover_not_received_leaves_the_request_unanswered(self):
        fresh = json.dumps({"jobName": "NEW_JOB", "imageSize": "200.0 um x 200.0 um"})

        class _Model:
            JobName = ""
            Settings = fresh

        client = self._client_with(_Model(), job_name_received=False)
        self.assertIsNone(A.get_job_settings(client, "NEW_JOB", deadline=_soon()))


if __name__ == "__main__":
    unittest.main()
