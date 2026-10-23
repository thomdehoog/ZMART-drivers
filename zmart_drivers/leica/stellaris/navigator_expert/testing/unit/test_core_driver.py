"""
Unit tests for the Navigator Expert driver core.
================================================
Offline mock-based tests - no hardware required.

Uses unittest.mock to simulate the LAS X API client and verify the
driver's internal logic: error classification, fire/retry flow,
confirmation polling, timing instrumentation, and set function wiring.

The tests exercise the current dispatch backbone, command profiles,
confirmation polling, and timing/result envelope contracts.

Usage::

    python test_unit.py            # run all
    python test_unit.py -v         # verbose
    python -m pytest test_unit.py  # via pytest
"""

import contextlib
import inspect
import threading
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import dispatch as door
from zmart_drivers.leica.stellaris.navigator_expert.procedures import keep_in_view as compensated_changes
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import profile as error_classification
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import parsing

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import client as session

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import client as lasx_client

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import (
    write as dispatch,
    write as send_loop,
)
from zmart_drivers.leica.stellaris.navigator_expert.procedures import readings as readers
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import profile as errors

from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import transfer as transport_module
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.limits import limit_rules
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as reading_settings
from zmart_drivers.leica.stellaris.navigator_expert.procedures import changes

# =============================================================================
# Helpers - mock factory
# =============================================================================


def make_echo(has_error=False, error="", result_code=1):
    """Create a mock PyApiCommandEcho.Model with given state."""
    echo = MagicMock()
    echo.HasError = has_error
    echo.Error = error
    echo.Result = result_code
    return echo


def make_client(echo=None, scan_status="eScanIdle"):
    """Create a mock LAS X client with a permissive function-limits gate.

    The commands-layer gate fails closed for a never-handshaken client, so
    the factory installs a permissive in-memory state — these tests are
    about command mechanics, not limits (test_limits_adversarial.py owns
    those). Tests that need the fail-closed behavior use a fresh object.
    """
    from limits_fixtures import install_permissive_limits

    client = MagicMock()
    if echo is None:
        echo = make_echo()
    client.PyApiCommandEcho.Model = echo
    status_obj = MagicMock()
    status_obj.__str__ = MagicMock(return_value=scan_status)
    client.PyApiStatus.Model.ScanStatus = status_obj
    install_permissive_limits(client)
    return client


def make_api_obj():
    """Create a mock API object."""
    api_obj = MagicMock()
    api_obj.Model = MagicMock()
    api_obj.UpdateAsync = MagicMock()
    api_obj.UpdateAwaitReceipt = MagicMock(return_value=True)
    api_obj.UpdateSync = MagicMock(return_value=True)
    return api_obj


def _idle_pre_check():
    """Trivial pre-check that always succeeds (scanner idle)."""
    return {"success": True, "logs": []}


class TestLasxApiSessionProfile(unittest.TestCase):
    def test_configure_lasx_api_delay_uses_profile_value(self):
        class FakeLasxApi:
            class PyApiClient:
                DelayInMilliseconds = 0

        profile = lasx_client.LasxApiProfile(delay_ms=123)
        with patch.object(lasx_client, "LASX_API", profile):
            applied = session.configure_lasx_api_delay(FakeLasxApi)

        self.assertEqual(applied, 123)
        self.assertEqual(FakeLasxApi.PyApiClient.DelayInMilliseconds, 123)

    def test_configure_lasx_api_delay_accepts_explicit_override(self):
        class FakeLasxApi:
            class PyApiClient:
                DelayInMilliseconds = 0

        applied = session.configure_lasx_api_delay(FakeLasxApi, delay_ms=0)

        self.assertEqual(applied, 0)
        self.assertEqual(FakeLasxApi.PyApiClient.DelayInMilliseconds, 0)

    def test_configure_lasx_api_delay_supports_connector_module_shape(self):
        class FakeConnector:
            class LasxApiClientPyModel:
                class PyApiClient:
                    DelayInMilliseconds = 0

        applied = session.configure_lasx_api_delay(FakeConnector, delay_ms=250)

        self.assertEqual(applied, 250)
        self.assertEqual(
            FakeConnector.LasxApiClientPyModel.PyApiClient.DelayInMilliseconds,
            250,
        )

    def test_configure_lasx_api_delay_fails_when_property_missing(self):
        class FakeLasxApi:
            pass

        with self.assertRaisesRegex(RuntimeError, "PyApiClient"):
            session.configure_lasx_api_delay(FakeLasxApi, delay_ms=250)


def _make_v6_timing(**kw):
    """Helper to build a driver timing dict."""
    d = {
        "pre_check_s": 0,
        "setup_s": 0,
        "fire_s": 0,
        "check_s": 0,
        "confirm_s": 0,
        "total_s": 0,
        "attempts": 1,
        "confirm_attempts": 0,
        "method": "async",
    }
    d.update(kw)
    return d


def _make_v6_result(success=True, msg="OK", **extra):
    """Helper to build a driver result dict."""
    r = {
        "success": success,
        "confirmed": True if success else None,
        "message": msg,
        "timing": _make_v6_timing(),
        "logs": [],
    }
    r.update(extra)
    return r


# =============================================================================
# 1. _check_api_error
# =============================================================================


class TestCheckApiError(unittest.TestCase):
    def test_success_result_1(self):
        client = make_client(make_echo(has_error=False, result_code=1))
        self.assertIsNone(error_classification._check_api_error(client))

    def test_not_defined_no_error(self):
        client = make_client(make_echo(has_error=False, result_code=0))
        self.assertIsNone(error_classification._check_api_error(client))

    def test_warning_treated_as_success(self):
        client = make_client(
            make_echo(
                has_error=True,
                error="Warning on command: pinhole adjusted",
                result_code=1,
            )
        )
        self.assertIsNone(error_classification._check_api_error(client))

    def test_warning_case_insensitive(self):
        client = make_client(
            make_echo(has_error=True, error="WARNING: value clamped", result_code=2)
        )
        self.assertIsNone(error_classification._check_api_error(client))

    def test_error_result_failure(self):
        client = make_client(
            make_echo(has_error=True, error="Zoom out of range", result_code=2)
        )
        err = error_classification._check_api_error(client)
        self.assertIsNotNone(err)
        self.assertEqual(err["error"], "Zoom out of range")
        self.assertEqual(err["result"], "Failure")
        self.assertEqual(err["result_code"], 2)

    def test_not_implemented(self):
        client = make_client(make_echo(has_error=False, error="", result_code=3))
        err = error_classification._check_api_error(client)
        self.assertIsNotNone(err)
        self.assertEqual(err["result"], "NotImplemented")
        self.assertIn("not implemented", err["error"].lower())

    def test_not_implemented_with_message(self):
        client = make_client(
            make_echo(has_error=True, error="Custom not impl msg", result_code=3)
        )
        err = error_classification._check_api_error(client)
        self.assertEqual(err["error"], "Custom not impl msg")

    def test_has_error_no_warning(self):
        client = make_client(
            make_echo(has_error=True, error="Something broke", result_code=1)
        )
        err = error_classification._check_api_error(client)
        self.assertIsNotNone(err)
        self.assertEqual(err["error"], "Something broke")

    def test_result_enum_exception(self):
        echo = MagicMock()
        echo.HasError = True
        echo.Error = "Some error"
        type(echo).Result = PropertyMock(side_effect=Exception("COM error"))
        client = MagicMock()
        client.PyApiCommandEcho.Model = echo
        err = error_classification._check_api_error(client)
        self.assertIsNotNone(err)
        self.assertEqual(err["result"], "Unknown")


# =============================================================================
# 2. _default_error_check adapter
# =============================================================================


class TestDefaultErrorCheck(unittest.TestCase):
    def test_success_shape(self):
        client = make_client(make_echo(has_error=False, result_code=1))
        result = error_classification._default_error_check(client)
        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])
        self.assertIsNone(result["transient"])
        self.assertIsInstance(result["logs"], list)

    def test_permanent_error_shape(self):
        client = make_client(
            make_echo(has_error=True, error="out of range", result_code=2)
        )
        result = error_classification._default_error_check(client)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "out of range")
        self.assertFalse(result["transient"])
        self.assertGreater(len(result["logs"]), 0)

    def test_transient_error_shape(self):
        client = make_client(
            make_echo(has_error=True, error="block is being scanned", result_code=2)
        )
        result = error_classification._default_error_check(client)
        self.assertFalse(result["success"])
        self.assertTrue(result["transient"])


def _fire_block(*args, **keywords):
    """Run the send step with this microscope's default command profile."""
    keywords.setdefault("profile", reading_settings.WRITING)
    return send_loop.fire_block(*args, **keywords)


def _fire(*args, **keywords):
    """Run the engine with this microscope's default command profile, which the engine no longer supplies itself."""
    keywords.setdefault("profile", reading_settings.WRITING)
    return dispatch.confirm_and_fire(*args, **keywords)


# =============================================================================
# 3. confirm_and_fire - core flow
# =============================================================================


class TestConfirmAndFire(unittest.TestCase):
    def test_success(self):
        client = make_client()
        api_obj = make_api_obj()
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                setup_fn=lambda m: setattr(m, "X", 1),
                max_retries=0,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])
        self.assertEqual(r["message"], "Test")
        self.assertIn("logs", r)
        api_obj.UpdateAwaitReceipt.assert_called_once()

    def test_timing_dict_structure(self):
        client = make_client()
        api_obj = make_api_obj()
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client, api_obj, "Test", max_retries=0, pre_check_fn=_idle_pre_check
            )
        t = r["timing"]
        for key in (
            "pre_check_s",
            "setup_s",
            "fire_s",
            "check_s",
            "confirm_s",
            "total_s",
            "attempts",
            "confirm_attempts",
            "method",
        ):
            self.assertIn(key, t, f"Missing timing key: {key}")
        self.assertEqual(t["method"], "async")
        self.assertEqual(t["attempts"], 1)
        self.assertEqual(t["confirm_attempts"], 0)

    def test_permanent_error_no_retry(self):
        client = make_client()
        api_obj = make_api_obj()
        perm_error = {"error": "out of range", "result": "Failure", "result_code": 2}
        with patch.object(errors, "_check_api_error", return_value=perm_error):
            r = _fire(
                client,
                api_obj,
                "Zoom -> 999",
                max_retries=5,
                pre_check_fn=_idle_pre_check,
                error_check_fn=lambda: errors._default_error_check(client),
            )
        self.assertFalse(r["success"])
        self.assertIn("out of range", r["message"])
        self.assertEqual(r["timing"]["attempts"], 1)

    def test_transient_error_retries(self):
        client = make_client()
        api_obj = make_api_obj()
        trans_error = {
            "error": "block is being scanned",
            "result": "Failure",
            "result_code": 2,
        }
        with patch.object(errors, "_check_api_error", return_value=trans_error):
            r = _fire(
                client,
                api_obj,
                "Zoom -> 5",
                max_retries=2,
                pre_check_fn=_idle_pre_check,
                error_check_fn=lambda: errors._default_error_check(client),
            )
        self.assertFalse(r["success"])
        self.assertIn("being scanned", r["message"])
        self.assertEqual(r["timing"]["attempts"], 3)

    def test_transient_then_success(self):
        client = make_client()
        api_obj = make_api_obj()
        trans_error = {
            "error": "block is being scanned",
            "result": "Failure",
            "result_code": 2,
        }
        call_count = [0]

        def mock_check(c):
            call_count[0] += 1
            return trans_error if call_count[0] == 1 else None

        with patch.object(errors, "_check_api_error", side_effect=mock_check):
            r = _fire(
                client,
                api_obj,
                "Zoom -> 5",
                max_retries=3,
                pre_check_fn=_idle_pre_check,
                error_check_fn=lambda: errors._default_error_check(client),
            )
        self.assertTrue(r["success"])
        self.assertEqual(r["timing"]["attempts"], 2)

    def test_no_retry_on_zero(self):
        client = make_client()
        api_obj = make_api_obj()
        trans_error = {"error": "busy", "result": "Failure", "result_code": 2}
        with patch.object(errors, "_check_api_error", return_value=trans_error):
            r = _fire(
                client,
                api_obj,
                "Test",
                max_retries=0,
                pre_check_fn=_idle_pre_check,
                error_check_fn=lambda: errors._default_error_check(client),
            )
        self.assertFalse(r["success"])
        self.assertEqual(r["timing"]["attempts"], 1)

    def test_setup_fn_called_every_attempt(self):
        client = make_client()
        api_obj = make_api_obj()
        setup_calls = []

        def setup(m):
            setup_calls.append(1)

        trans_error = {"error": "busy", "result": "Failure", "result_code": 2}
        call_count = [0]

        def mock_check(c):
            call_count[0] += 1
            return trans_error if call_count[0] <= 2 else None

        with patch.object(errors, "_check_api_error", side_effect=mock_check):
            _fire(
                client,
                api_obj,
                "Test",
                setup_fn=setup,
                max_retries=3,
                pre_check_fn=_idle_pre_check,
                error_check_fn=lambda: errors._default_error_check(client),
            )
        self.assertEqual(len(setup_calls), 3)

    def test_setup_fn_exception(self):
        client = make_client()
        api_obj = make_api_obj()

        def bad_setup(m):
            raise ValueError("bad value")

        r = _fire(
            client,
            api_obj,
            "Test",
            setup_fn=bad_setup,
            max_retries=0,
            pre_check_fn=_idle_pre_check,
        )
        self.assertFalse(r["success"])
        self.assertIn("Setup exception", r["message"])

    def test_no_setup_fn(self):
        client = make_client()
        api_obj = make_api_obj()
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                setup_fn=None,
                max_retries=0,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])

    def test_pre_check_failure_stops(self):
        """A pre_check_fn that returns failure stops the pipeline."""
        client = make_client()
        api_obj = make_api_obj()

        def failing_pre_check():
            return {"success": False, "logs": []}

        r = _fire(
            client, api_obj, "Test", max_retries=0, pre_check_fn=failing_pre_check
        )
        self.assertFalse(r["success"])
        self.assertIn("Pre-check failed", r["message"])

    def test_confirmed_key_present(self):
        client = make_client()
        api_obj = make_api_obj()
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client, api_obj, "Test", max_retries=0, pre_check_fn=_idle_pre_check
            )
        self.assertIn("confirmed", r)
        self.assertIsNone(r["confirmed"])


# =============================================================================
# 3b. Retry backoff - escalating and fixed delays
# =============================================================================


class TestDispatchPolicy(unittest.TestCase):
    """What the changes hand the dispatcher: their confirmation policy and windows."""

    def test_default_setting_and_acquire_profiles_encode_retry_policy(self):
        """Uniform posture; acquisition is the one command that never re-sends."""
        # Every change is sent by the one write profile: four windows, three
        # sends again after a temporary error, re-fire between windows,
        # unconfirmed-not-fail. Only the acquisition is never-resend.
        writing = reading_settings.WRITING
        self.assertEqual(writing.max_confirm_attempts, 4)
        self.assertEqual(writing.max_retries, 3)
        self.assertTrue(writing.refire_on_unconfirmed)
        self.assertFalse(writing.never_resend)
        for change in (
            writes.change("zoom", "J", value=1.0),
            writes.set_objective("J", 1, "10x"),
            writes.move_z("J", 0.0, "um", "galvo"),
            writes.move_xy(0.0, 0.0, "um"),
            writes.select_job("J"),
        ):
            self.assertFalse(change.never_resend, change.command)
        # The XY move and the acquisition are sent without a receipt; the XY
        # move's readback is the proof.
        self.assertTrue(writes.move_xy(0.0, 0.0, "um").without_receipt)
        self.assertFalse(writes.move_z("J", 0.0, "um", "galvo").without_receipt)
        self.assertFalse(writes.select_job("J").without_receipt)

        # The acquisition is the sole deviation: never sent again, and nothing
        # read back: its scan is watched after the send (confirm_acquire).
        acquire = writes.acquire("J")
        self.assertTrue(acquire.never_resend)
        self.assertTrue(acquire.without_receipt)
        self.assertIsNone(acquire.confirm)
        self.assertEqual(acquire.ready.reading, "scan_status")

        signature = inspect.signature(changes.confirm_acquire)
        self.assertEqual(signature.parameters["start_timeout"].default, 15.0)
        self.assertEqual(signature.parameters["heartbeat_interval"].default, 30.0)
        self.assertEqual(signature.parameters["poll_interval"].default, 0.1)
        self.assertIsNone(signature.parameters["timeout"].default)

        # select_job is the uniform 4x3, not an outlier: one window of the
        # selected_job reading at a time, four of them, re-fire between.
        self.assertEqual(writes.select_job("J").confirm.reading, "selected_job")


class TestWriteProfileGuard(unittest.TestCase):
    """The sending profile and the sent-once rule are coherent."""

    def test_the_one_write_profile_can_send_again_between_windows(self):
        # One window has no 'next attempt' for a re-fire to precede.
        writing = reading_settings.WRITING
        self.assertTrue(writing.max_confirm_attempts > 1 or not writing.refire_on_unconfirmed)

    def test_sent_once_has_one_window_and_no_send_again(self):
        once = send_loop.resolve_profile(reading_settings.WRITING, never_resend=True)
        self.assertEqual((once.max_retries, once.max_confirm_attempts), (0, 1))
        self.assertFalse(once.refire_on_unconfirmed)


# =============================================================================
# 4. Confirmation via confirm_and_fire
# =============================================================================


class TestConfirmation(unittest.TestCase):
    def test_confirm_fn_immediate(self):
        client = make_client()
        api_obj = make_api_obj()
        confirm_fn = MagicMock(return_value={"success": True, "logs": []})
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                confirm_fn=confirm_fn,
                max_retries=0,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])
        self.assertTrue(r["confirmed"])
        confirm_fn.assert_called()

    def test_no_confirm_fn_skips(self):
        client = make_client()
        api_obj = make_api_obj()
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                confirm_fn=None,
                max_retries=0,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])
        self.assertIsNone(r["confirmed"])
        self.assertEqual(r["timing"]["confirm_s"], 0.0)

    def test_confirm_always_fails(self):
        """When confirm_fn always returns failure, the change is a success, unconfirmed."""
        client = make_client()
        api_obj = make_api_obj()
        confirm_fn = MagicMock(return_value={"success": False, "logs": []})
        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Zoom -> 5",
                confirm_fn=confirm_fn,
                max_retries=0,
                max_confirm_attempts=1,
                refire_on_unconfirmed=False,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])  # sent and accepted, only not confirmed
        self.assertFalse(r["confirmed"])
        self.assertIn("unconfirmed", r["message"])

    def test_unconfirmed_command_reports_post_command_dialog(self):
        client = make_client()
        api_obj = make_api_obj()
        confirm_fn = MagicMock(return_value={"success": False, "logs": []})
        snapshot = MagicMock(
            pending_dialog="Please turn turret manually",
            pending_dialog_ts=time.time() + 10.0,
        )

        with (
            patch.object(errors, "_check_api_error", return_value=None),
            patch.object(
                transport_module._log_reader, "parse_msgbox_log", return_value=snapshot
            ),
        ):
            r = _fire(
                client,
                api_obj,
                "Objective -> slot 1",
                confirm_fn=confirm_fn,
                max_retries=0,
                max_confirm_attempts=1,
                refire_on_unconfirmed=False,
                pre_check_fn=_idle_pre_check,
            )

        self.assertFalse(r["confirmed"])
        self.assertIn("LAS X dialog appears open", r["message"])
        self.assertTrue(
            any("LAS X dialog appears open" in entry["msg"] for entry in r["logs"])
        )

    def test_unconfirmed_command_ignores_pre_command_dialog(self):
        client = make_client()
        api_obj = make_api_obj()
        confirm_fn = MagicMock(return_value={"success": False, "logs": []})
        snapshot = MagicMock(
            pending_dialog="Old dialog",
            pending_dialog_ts=0.0,
        )

        with (
            patch.object(errors, "_check_api_error", return_value=None),
            patch.object(
                transport_module._log_reader, "parse_msgbox_log", return_value=snapshot
            ),
        ):
            r = _fire(
                client,
                api_obj,
                "Objective -> slot 1",
                confirm_fn=confirm_fn,
                max_retries=0,
                max_confirm_attempts=1,
                refire_on_unconfirmed=False,
                pre_check_fn=_idle_pre_check,
            )

        self.assertFalse(r["confirmed"])
        self.assertFalse(
            any("LAS X dialog appears open" in entry["msg"] for entry in r["logs"])
        )

    def test_confirm_succeeds_on_retry(self):
        """Confirm fails first, correction re-fires, then confirm succeeds."""
        client = make_client()
        api_obj = make_api_obj()
        call_count = [0]

        def delayed_confirm():
            call_count[0] += 1
            return {"success": call_count[0] >= 2, "logs": []}

        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                confirm_fn=delayed_confirm,
                max_retries=0,
                max_confirm_attempts=3,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])

    def test_readback_retry_does_not_refire_when_policy_says_so(self):
        """A stale Leica readback can retry confirmation without re-firing."""
        client = make_client()
        api_obj = make_api_obj()
        fire_result = {
            "success": True,
            "logs": [],
            "attempts": 1,
            "timing": {
                "pre_check_s": 0.0,
                "setup_s": 0.0,
                "fire_s": 0.0,
                "check_s": 0.0,
            },
        }
        confirm_fn = MagicMock(
            side_effect=[
                {"success": False, "logs": []},
                {"success": False, "logs": []},
                {"success": True, "logs": []},
            ]
        )

        with patch.object(dispatch, "fire_block", return_value=fire_result) as fire:
            r = _fire(
                client,
                api_obj,
                "Set Zoom",
                setup_fn=lambda m: None,
                confirm_fn=confirm_fn,
                max_confirm_attempts=3,
                refire_on_unconfirmed=False,
            )

        self.assertTrue(r["success"])
        self.assertTrue(r["confirmed"])
        self.assertEqual(fire.call_count, 1)
        self.assertEqual(confirm_fn.call_count, 3)

    def test_unconfirmed_readback_can_be_non_fatal_without_refire(self):
        """Settings may be applied even when Leica's state readback is stale."""
        client = make_client()
        api_obj = make_api_obj()
        fire_result = {
            "success": True,
            "logs": [],
            "attempts": 1,
            "timing": {
                "pre_check_s": 0.0,
                "setup_s": 0.0,
                "fire_s": 0.0,
                "check_s": 0.0,
            },
        }
        confirm_fn = MagicMock(return_value={"success": False, "logs": []})

        with patch.object(dispatch, "fire_block", return_value=fire_result) as fire:
            r = _fire(
                client,
                api_obj,
                "Set Zoom",
                setup_fn=lambda m: None,
                confirm_fn=confirm_fn,
                max_confirm_attempts=3,
                refire_on_unconfirmed=False,
            )

        self.assertTrue(r["success"])
        self.assertFalse(r["confirmed"])
        self.assertEqual(fire.call_count, 1)
        self.assertIn("readback unconfirmed", r["message"])
        self.assertTrue(
            any("command was sent successfully" in item["msg"] for item in r["logs"])
        )

    def test_unconfirmed_setting_refire_is_non_fatal(self):
        """Settings may re-fire and still continue when readback disagrees."""
        client = make_client()
        api_obj = make_api_obj()
        fire_result = {
            "success": True,
            "logs": [],
            "attempts": 1,
            "timing": {
                "pre_check_s": 0.0,
                "setup_s": 0.0,
                "fire_s": 0.0,
                "check_s": 0.0,
            },
        }
        confirm_fn = MagicMock(
            return_value={
                "success": False,
                "logs": [],
                "actual": 4.0,
            }
        )

        with patch.object(dispatch, "fire_block", return_value=fire_result) as fire:
            r = _fire(
                client,
                api_obj,
                "Set Zoom",
                setup_fn=lambda m: None,
                confirm_fn=confirm_fn,
                max_confirm_attempts=3,
                refire_on_unconfirmed=True,
            )

        self.assertTrue(r["success"])
        self.assertFalse(r["confirmed"])
        self.assertEqual(fire.call_count, 3)
        self.assertEqual(confirm_fn.call_count, 3)
        self.assertTrue(
            any(
                "last_confirmation={'actual': 4.0}" in item["msg"] for item in r["logs"]
            )
        )

    def test_confirm_fn_exception_handled(self):
        """Exceptions in confirm_fn are caught and treated as failure."""
        client = make_client()
        api_obj = make_api_obj()
        call_count = [0]

        def flaky_confirm():
            call_count[0] += 1
            if call_count[0] < 2:
                raise RuntimeError("COM error")
            return {"success": True, "logs": []}

        with patch.object(errors, "_check_api_error", return_value=None):
            r = _fire(
                client,
                api_obj,
                "Test",
                confirm_fn=flaky_confirm,
                max_retries=0,
                max_confirm_attempts=3,
                pre_check_fn=_idle_pre_check,
            )
        self.assertTrue(r["success"])


# =============================================================================
# 6. Error classification
# =============================================================================


class TestErrorClassification(unittest.TestCase):
    def test_transient_patterns(self):
        for msg in [
            "block is being scanned",
            "cannot be set while scanning",
            "Hardware busy",
            "Resource locked",
            "Connection timeout",
            "Request timed out",
        ]:
            with self.subTest(msg=msg):
                self.assertTrue(error_classification._is_transient_error(msg))

    def test_permanent_patterns(self):
        for msg in [
            "parameter out of range",
            "value is invalid",
            "invalid block identifier",
            "detector not found",
            "Command not defined",
            "has been adjusted",
            "not implemented",
        ]:
            with self.subTest(msg=msg):
                self.assertFalse(error_classification._is_transient_error(msg))

    def test_permanent_wins_over_transient(self):
        self.assertFalse(error_classification._is_transient_error("timeout not found"))
        self.assertFalse(error_classification._is_transient_error("locked out of range"))

    def test_unknown_is_permanent(self):
        self.assertFalse(error_classification._is_transient_error("something unexpected"))

    def test_empty_string(self):
        self.assertFalse(error_classification._is_transient_error(""))

    def test_case_insensitive(self):
        self.assertTrue(error_classification._is_transient_error("BEING SCANNED"))
        self.assertFalse(error_classification._is_transient_error("OUT OF RANGE"))


# =============================================================================
# 7. Confirm functions
# =============================================================================


class TestConfirmFunctions(unittest.TestCase):
    """The checks that say a change took effect, each asked with one readback."""

    def _mock_readback(self, changeable_dict):
        # A readback with the keys every job's settings carry, so the checks
        # that normalise it see a well-formed one; the checks below ask it.
        readback = changeable_dict
        if changeable_dict is not None:
            readback = {
                "zoom": {"current": None},
                "scanSpeed": {"value": None, "isResonant": False},
                "activeSettings": [],
                **changeable_dict,
            }
        self.readback = readback
        return contextlib.nullcontext()

    def _shows(self, name, *, tolerance=None, **given):
        """Whether the change of the setting *name* counts the readback as showing it."""
        return writes.change(name, "J", tolerance=tolerance, **given).confirm.accept(self.readback)

    def _z_shows(self, z_mode, target_um, tolerance=None):
        """Whether the z move's check counts the readback as showing it."""
        return writes.move_z("J", target_um, "um", z_mode, tolerance=tolerance).confirm.accept(self.readback)

    def test_confirm_zoom_pass(self):
        with self._mock_readback({"zoom": {"current": 5.0}}):
            self.assertTrue(self._shows("zoom", value=5.0))

    def test_confirm_zoom_within_tolerance(self):
        with self._mock_readback({"zoom": {"current": 5.05}}):
            self.assertTrue(self._shows("zoom", value=5.0))

    def test_confirm_zoom_fail(self):
        with self._mock_readback({"zoom": {"current": 3.0}}):
            self.assertFalse(self._shows("zoom", value=5.0))

    def test_confirm_zoom_none_readback(self):
        with self._mock_readback(None):
            self.assertFalse(self._shows("zoom", value=5.0))

    def test_confirm_scan_speed(self):
        with self._mock_readback({"scanSpeed": {"value": 600}}):
            self.assertTrue(
                self._shows("scan_speed", value=600)
            )
        with self._mock_readback({"scanSpeed": {"value": 400}}):
            self.assertFalse(
                self._shows("scan_speed", value=600)
            )

    def test_confirm_scan_resonant(self):
        with self._mock_readback({"scanSpeed": {"isResonant": True}}):
            self.assertTrue(
                self._shows("scan_resonant", enable=True)
            )
            self.assertFalse(
                self._shows("scan_resonant", enable=False)
            )

    def test_setting_confirm_defers_reader_mode_to_profile(self):
        # A setting's readback names no read order: the reading's own, from the profile.
        confirm = writes.change("scan_resonant", "J", enable=True).confirm
        self.assertEqual((confirm.reading, confirm.about), ("job_settings", "J"))
        self.assertIsNone(confirm.mode)
        self.assertIsNone(confirm.fallback)

    def test_confirm_scan_mode(self):
        with self._mock_readback({"scanMode": "xyz"}):
            self.assertTrue(
                self._shows("scan_mode", mode="xyz")
            )
            self.assertFalse(
                self._shows("scan_mode", mode="xy")
            )

    def test_confirm_sequential_mode(self):
        with self._mock_readback({"sequentialMode": "Frame"}):
            self.assertTrue(
                self._shows("sequential_mode", mode="Frame")
            )

    def test_confirm_rotation(self):
        with self._mock_readback({"scanFieldRotation": {"value": 45.3}}):
            self.assertTrue(
                self._shows("scan_field_rotation", angle=45.0)
            )
        with self._mock_readback({"scanFieldRotation": {"value": 46.0}}):
            self.assertFalse(
                self._shows("scan_field_rotation", angle=45.0)
            )

    def test_confirm_image_format(self):
        with self._mock_readback({"format": "1024 x 1024"}):
            self.assertTrue(
                self._shows("image_format", format_str=(1024, 1024))
            )
            self.assertFalse(
                self._shows("image_format", format_str=(512, 512))
            )

    def test_confirm_pinhole_airy(self):
        with self._mock_readback(
            {"activeSettings": [{"pinholeAiry": {"value": 1.02}}]}
        ):
            self.assertTrue(
                self._shows("pinhole_airy", setting_index=0, value=1.0)
            )
        with self._mock_readback({"activeSettings": [{"pinholeAiry": {"value": 0.5}}]}):
            self.assertFalse(
                self._shows("pinhole_airy", setting_index=0, value=1.0)
            )

    def test_confirm_frame_accumulation(self):
        with self._mock_readback({"activeSettings": [{"frameAccumulation": 4}]}):
            self.assertTrue(
                self._shows("frame_accumulation", setting_index=0, value=4)
            )
            self.assertFalse(
                self._shows("frame_accumulation", setting_index=0, value=2)
            )

    def test_confirm_frame_average(self):
        with self._mock_readback({"activeSettings": [{"frameAverage": 2}]}):
            self.assertTrue(
                self._shows("frame_average", setting_index=0, value=2)
            )

    def test_confirm_line_accumulation(self):
        with self._mock_readback({"activeSettings": [{"lineAccumulation": 3}]}):
            self.assertTrue(
                self._shows("line_accumulation", setting_index=0, value=3)
            )

    def test_confirm_line_average(self):
        with self._mock_readback({"activeSettings": [{"lineAverage": 8}]}):
            self.assertTrue(
                self._shows("line_average", setting_index=0, value=8)
            )

    def test_confirm_move_xy(self):
        position = {"x_um": 50000, "y_um": 50000, "x_m": 0.05, "y_m": 0.05}
        self.assertTrue(writes.within_xy(50000, 50000, 20.0)(position))
        self.assertTrue(writes.within_xy(50019, 49981, 20.0)(position))
        self.assertFalse(writes.within_xy(50000, 99999, 20.0)(position))
        self.assertFalse(writes.within_xy(50020, 50000, 20.0)(position))  # strictly within
        confirm = writes.move_xy(50000, 50000, "um").confirm
        self.assertEqual(confirm.reading, "xy")
        self.assertTrue(confirm.accept(position))

    # â”€â”€ confirm_move_z â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_confirm_move_z_galvo_pass(self):
        with self._mock_readback({"zPosition": {"z-galvo": 50.0}}):
            self.assertTrue(
                self._z_shows("galvo", 50.0)
            )

    def test_confirm_move_z_galvo_fail(self):
        with self._mock_readback({"zPosition": {"z-galvo": 10.0}}):
            self.assertFalse(
                self._z_shows("galvo", 50.0)
            )

    def test_confirm_move_z_zwide_pass(self):
        with self._mock_readback({"zPosition": {"z-wide": 100.0}}):
            self.assertTrue(
                self._z_shows("zwide", 100.0)
            )

    def test_confirm_move_z_within_tolerance(self):
        with self._mock_readback({"zPosition": {"z-galvo": 50.5}}):
            self.assertTrue(
                self._z_shows("galvo", 50.0, tolerance=1.0)
            )

    def test_confirm_move_z_none_readback(self):
        with self._mock_readback(None):
            self.assertFalse(
                self._z_shows("galvo", 50.0)
            )

    # â”€â”€ z-stack confirm functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_confirm_z_stack_step_size_pass(self):
        with self._mock_readback({"stack": {"stepSize": 2.0}}):
            self.assertTrue(
                self._shows("z_stack_step_size", step_size_um=2.0)
            )

    def test_confirm_z_stack_step_size_fail(self):
        with self._mock_readback({"stack": {"stepSize": 5.0}}):
            self.assertFalse(
                self._shows("z_stack_step_size", step_size_um=2.0)
            )

    def test_confirm_z_stack_step_size_within_tolerance(self):
        with self._mock_readback({"stack": {"stepSize": 2.3}}):
            self.assertTrue(
                self._shows("z_stack_step_size", tolerance=0.5, step_size_um=2.0)
            )

    def test_confirm_z_stack_size_pass(self):
        with self._mock_readback({"stack": {"size": 10.0, "stepSize": 2.0}}):
            self.assertTrue(
                self._shows("z_stack_size", size_um=10.0)
            )

    def test_confirm_z_stack_size_fail(self):
        with self._mock_readback({"stack": {"size": 20.0, "stepSize": 2.0}}):
            self.assertFalse(
                self._shows("z_stack_size", size_um=10.0)
            )

    def test_confirm_z_stack_size_quantised_match(self):
        """Target 9.5 with step 2.0: quantised candidates are 8.0 (n=4) and
        10.0 (n=5). Actual=10.0 should match via quantised path."""
        with self._mock_readback({"stack": {"size": 10.0, "stepSize": 2.0}}):
            self.assertTrue(
                self._shows("z_stack_size", size_um=9.5)
            )

    def test_confirm_z_stack_definition_pass(self):
        with self._mock_readback(
            {"stack": {"begin": -5.0, "end": 5.0, "stepSize": 1.0}}
        ):
            self.assertTrue(
                self._shows("z_stack_definition", begin_um=-5.0, end_um=5.0)
            )

    def test_confirm_z_stack_definition_fail(self):
        with self._mock_readback(
            {"stack": {"begin": -5.0, "end": 5.0, "stepSize": 1.0}}
        ):
            self.assertFalse(
                self._shows("z_stack_definition", begin_um=-10.0, end_um=10.0)
            )

    def test_confirm_z_stack_definition_begin_only(self):
        with self._mock_readback(
            {"stack": {"begin": -5.0, "end": 5.0, "stepSize": 1.0}}
        ):
            self.assertTrue(
                self._shows("z_stack_definition", begin_um=-5.0, end_um=None)
            )

    def test_confirm_z_stack_definition_quantised(self):
        """begin=-5, end=5, step=3 -> raw size=10, centre=0.
        Quantised: n=3->size=9, n=4->size=12.
        Candidates: (-4.5, 4.5) and (-6, 6).
        Actual (-4.5, 4.5) should match."""
        with self._mock_readback(
            {"stack": {"begin": -4.5, "end": 4.5, "stepSize": 3.0}}
        ):
            self.assertTrue(
                self._shows("z_stack_definition", begin_um=-5.0, end_um=5.0)
            )

    # â”€â”€ filter wheel confirm functions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _fw_readback(
        self,
        fw_type="emission",
        beam_route="BR1",
        filter_index=0,
        spectrum_position=525,
    ):
        return self._mock_readback(
            {
                "activeSettings": [
                    {
                        "filterWheels": [
                            {
                                "_beamRoute": beam_route,
                                "type": fw_type,
                                "filterIndex": filter_index,
                                "spectrumPosition": spectrum_position,
                            }
                        ],
                    }
                ],
            }
        )

    def test_confirm_filter_wheel_slot_pass(self):
        with self._fw_readback(filter_index=2):
            self.assertTrue(
                self._shows("filter_wheel_slot", setting_index=0, beam_route="BR1", filter_wheel_type="emission", slot_index=2)
            )

    def test_confirm_filter_wheel_slot_fail(self):
        with self._fw_readback(filter_index=0):
            self.assertFalse(
                self._shows("filter_wheel_slot", setting_index=0, beam_route="BR1", filter_wheel_type="emission", slot_index=2)
            )

    def test_confirm_filter_wheel_slot_wrong_beam_route(self):
        with self._fw_readback(beam_route="BR2", filter_index=2):
            self.assertFalse(
                self._shows("filter_wheel_slot", setting_index=0, beam_route="BR1", filter_wheel_type="emission", slot_index=2)
            )

    def test_confirm_filter_wheel_spectrum_pass(self):
        with self._fw_readback(spectrum_position=525):
            self.assertTrue(
                self._shows("filter_wheel_spectrum", setting_index=0, beam_route="BR1", filter_wheel_type="emission", position=525)
            )

    def test_confirm_filter_wheel_spectrum_fail(self):
        with self._fw_readback(spectrum_position=600):
            self.assertFalse(
                self._shows("filter_wheel_spectrum", setting_index=0, beam_route="BR1", filter_wheel_type="emission", position=525)
            )

    def test_confirm_filter_wheel_spectrum_within_tolerance(self):
        with self._fw_readback(spectrum_position=525.5):
            self.assertTrue(
                self._shows("filter_wheel_spectrum", setting_index=0, beam_route="BR1", filter_wheel_type="emission", tolerance=1, position=525)
            )

    def test_confirm_returns_dict_shape(self):
        """A readback answers {"success": bool, "logs": [...]}, and the last value when it did not show."""
        reading = readers.Reading(value={"x_um": 1.0, "y_um": 2.0}, source="api", observed_at=time.time() + 1.0, age_s=0.0)
        expect = writes.Expect("xy", writes.within_xy(1.0, 2.0, 20.0))
        with patch.object(send_loop.reader, "wait_for", return_value=reading):
            result = send_loop.read_back(reading_settings, None, expect)()
        self.assertEqual(result, {"success": True, "logs": []})
        with patch.object(send_loop.reader, "wait_for", return_value=None):
            result = send_loop.read_back(reading_settings, None, expect)()
        self.assertFalse(result["success"])
        self.assertIsInstance(result["logs"], list)
        self.assertIn("last_value", result)


# =============================================================================
# 8. Set function wiring
# =============================================================================


class TestSetFunctionWiring(unittest.TestCase):
    def _resonant_no_change_client(self):
        class Echo:
            Result = 2
            HasError = True
            Error = (
                "CamCommandSetScannerToResonantByJobName: "
                "the desired state does not differ from the current state"
            )
            Description = ""
            ErrorDescription = ""
            Message = ""
            ErrorMessage = ""
            Warning = ""
            Info = ""
            Details = ""

        client = MagicMock()
        client.PyApiCommandEcho.Model = Echo()
        return client

    def _run_set(self, set_fn, *args, **kwargs):
        """Run a set function with the dispatcher's confirm_and_fire mocked.

        If the first positional arg is None, substitute make_client()
        so that API object attribute lookups (client.PyApiXxx) succeed.
        Returns (captured_info, result).  captured_info includes:
          - description, kwargs, model  (as before)
          - api_obj: the API object the set function resolved
          - client:  the mock client used (for identity checks)
        """
        args = list(args)
        if args and args[0] is None:
            args[0] = make_client()
        captured = {"client": args[0]}

        def mock_fire(client, api_obj, description, **kw):
            captured["description"] = description
            captured["api_obj"] = getattr(
                api_obj, "cam_object", api_obj
            )  # the request, without its name
            captured["kwargs"] = kw
            model = MagicMock()
            if kw.get("setup_fn"):
                kw["setup_fn"](model)
            captured["model"] = model
            return _make_v6_result(msg=description)

        with patch.object(send_loop, "confirm_and_fire", side_effect=mock_fire):
            r = set_fn(*args, **kwargs)
        return captured, r

    def test_set_zoom_model(self):
        info, _ = self._run_set(changes.change, None, "zoom", "HiRes", value=5.0)
        self.assertIs(info["api_obj"], info["client"].PyApiSetZoomByJobName)
        self.assertEqual(info["model"].JobName, "HiRes")
        self.assertEqual(info["model"].ZoomValue, 5.0)

    def test_set_scan_speed_model(self):
        info, _ = self._run_set(changes.change, None, "scan_speed", "HiRes", value=600)
        self.assertIs(info["api_obj"], info["client"].PyApiSetScanSpeedByJobName)
        self.assertEqual(info["model"].ScanSpeed, 600)

    def test_set_scan_resonant_model(self):
        info, _ = self._run_set(changes.change, None, "scan_resonant", "HiRes", enable=True)
        self.assertIs(
            info["api_obj"], info["client"].PyApiSetScannerToResonantByJobName
        )
        self.assertEqual(info["model"].EnableResonant, True)

    def test_scan_resonant_no_change_reply_is_accepted_as_already_so(self):
        client = self._resonant_no_change_client()
        change = writes.change("scan_resonant", "HiRes", enable=False)

        result = reading_settings.check_reply(client, change)

        self.assertTrue(result["success"])
        self.assertIsNone(result["error"])
        self.assertIn("already so", result["logs"][0]["msg"])

    def test_scan_resonant_no_change_is_confirmed_only_when_the_readback_agrees(self):
        # The reply is accepted; the readback then decides, as for any change.
        confirm = writes.change("scan_resonant", "HiRes", enable=False).confirm
        settings = {"zoom": {"current": 1.0}, "activeSettings": []}
        self.assertTrue(confirm.accept({**settings, "scanSpeed": {"value": 400, "isResonant": False}}))
        self.assertFalse(confirm.accept({**settings, "scanSpeed": {"value": 400, "isResonant": True}}))

    def test_the_no_change_reply_is_an_error_for_a_change_that_does_not_accept_it(self):
        client = self._resonant_no_change_client()

        result = reading_settings.check_reply(client, writes.change("zoom", "HiRes", value=2.0))

        self.assertFalse(result["success"])
        self.assertIn("desired state does not differ", result["error"])

    def test_set_scan_mode_model(self):
        info, _ = self._run_set(changes.change, None, "scan_mode", "HiRes", mode="xyz")
        self.assertIs(info["api_obj"], info["client"].PyApiSetScanModeByJobName)
        self.assertEqual(info["model"].ScanModeValue, "xyz")

    def test_set_rotation_model(self):
        info, _ = self._run_set(changes.change, None, "scan_field_rotation", "HiRes", angle=45.0)
        self.assertIs(
            info["api_obj"], info["client"].PyApiSetScanFieldRotationByJobName
        )
        self.assertEqual(info["model"].Rotation, 45.0)

    def test_set_image_format_string(self):
        info, _ = self._run_set(changes.change, None, "image_format", "HiRes", format_str="1024 x 1024")
        self.assertIs(info["api_obj"], info["client"].PyApiSetImageSizeByJobName)
        self.assertEqual(info["model"].ImageWidth, 1024)
        self.assertEqual(info["model"].ImageHeight, 1024)

    def test_set_image_format_tuple(self):
        info, _ = self._run_set(changes.change, None, "image_format", "HiRes", format_str=(512, 768))
        self.assertIs(info["api_obj"], info["client"].PyApiSetImageSizeByJobName)
        self.assertEqual(info["model"].ImageWidth, 512)
        self.assertEqual(info["model"].ImageHeight, 768)

    def test_set_frame_accumulation_model(self):
        info, _ = self._run_set(changes.change, None, "frame_accumulation", "HiRes", setting_index=0, value=4)
        self.assertIs(
            info["api_obj"], info["client"].PyApiSetFrameAccumulationByJobName
        )
        self.assertEqual(info["model"].SettingIndex, 0)
        self.assertEqual(info["model"].FrameAccumulation, 4)

    def test_set_frame_average_model(self):
        info, _ = self._run_set(changes.change, None, "frame_average", "HiRes", setting_index=0, value=2)
        self.assertIs(info["api_obj"], info["client"].PyApiSetFrameAverageByJobName)
        self.assertEqual(info["model"].FrameAverage, 2)

    def test_set_pinhole_airy_model(self):
        info, _ = self._run_set(changes.change, None, "pinhole_airy", "HiRes", setting_index=0, value=1.5)
        self.assertIs(info["api_obj"], info["client"].PyApiSetPinholeAUByJobName)
        self.assertEqual(info["model"].PinholeAiry, 1.5)

    def test_set_detector_gain_model(self):
        info, _ = self._run_set(changes.change, None, "detector_gain", "HiRes", setting_index=0, beam_route="BR1", value=750)
        self.assertIs(info["api_obj"], info["client"].PyApiSetDetectorGainByJobName)
        self.assertEqual(info["model"].BeamRoute, "BR1")
        self.assertEqual(info["model"].GainValue, 750)

    def test_set_laser_intensity_model(self):
        info, _ = self._run_set(changes.change, None, "laser_intensity", "HiRes", setting_index=0, beam_route="BR1", line_index=0, value=0.5)
        self.assertIs(info["api_obj"], info["client"].PyApiSetLaserIntensityByJobName)
        self.assertEqual(info["model"].IntensityValue, 0.5)
        self.assertEqual(info["model"].LaserLineIndex, 0)

    def test_set_laser_shutter_model(self):
        info, _ = self._run_set(changes.change, None, "laser_shutter", "HiRes", setting_index=0, beam_route="BR1", activate=True)
        self.assertIs(info["api_obj"], info["client"].PyApiSetLaserShutterByJobName)
        self.assertEqual(info["model"].Activate, True)

    def test_set_z_stack_step_size_model(self):
        info, _ = self._run_set(changes.change, None, "z_stack_step_size", "HiRes", step_size_um=2.0)
        self.assertIs(
            info["api_obj"], info["client"].PyApiCommandSetZStackStepSizeByJobName
        )
        self.assertAlmostEqual(info["model"].StackStepSize, 2.0e-6, places=10)

    def test_set_z_stack_size_model(self):
        info, _ = self._run_set(changes.change, None, "z_stack_size", "HiRes", size_um=10.0)
        self.assertIs(info["api_obj"], info["client"].PyApiSetZStackSizeByJobName)
        self.assertAlmostEqual(info["model"].StackSize, 10.0e-6, places=10)

    def test_set_zoom_provides_confirm_fn(self):
        info, _ = self._run_set(changes.change, None, "zoom", "HiRes", value=5.0)
        self.assertIs(info["api_obj"], info["client"].PyApiSetZoomByJobName)
        self.assertIsNotNone(info["kwargs"].get("confirm_fn"))

    # â”€â”€ move_z wiring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _run_move_z(self, client, *args, **kwargs):
        """Like _run_set, for move_z; the limits, asked before anything is sent, are wide open here."""
        from limits_fixtures import install_permissive_limits

        client = install_permissive_limits(client or make_client(), wide_stage=True)
        return self._run_set(changes.move_z, client, *args, **kwargs)

    def test_move_z_galvo_model(self):
        info, _ = self._run_move_z(None, "J", 50.0, unit="um", z_mode="galvo")
        self.assertIs(info["api_obj"], info["client"].PyApiMoveZByJobName)
        self.assertEqual(info["model"].JobName, "J")
        self.assertEqual(info["model"].ZPosition, 50.0)
        self.assertFalse(info["model"].RelativePosition)

    def test_move_z_unit_mm(self):
        info, _ = self._run_move_z(None, "J", 0.05, unit="mm", z_mode="galvo")
        self.assertIs(info["api_obj"], info["client"].PyApiMoveZByJobName)
        # ZPosition is stored in the user-supplied unit (not converted)
        self.assertEqual(info["model"].ZPosition, 0.05)

    def test_move_z_unit_m(self):
        info, _ = self._run_move_z(None, "J", 50.0e-6, unit="m", z_mode="galvo")
        self.assertIs(info["api_obj"], info["client"].PyApiMoveZByJobName)
        self.assertEqual(info["model"].ZPosition, 50.0e-6)

    def test_move_z_zwide_mode(self):
        info, _ = self._run_move_z(None, "J", 100.0, unit="um", z_mode="zwide")
        self.assertIs(info["api_obj"], info["client"].PyApiMoveZByJobName)

    def test_move_z_invalid_mode(self):
        r = changes.move_z(make_client(), "J", 50.0, z_mode="invalid")
        self.assertFalse(r["success"])
        self.assertIn("Unknown z_mode", r["message"])

    def test_move_z_provides_confirm_fn(self):
        info, _ = self._run_move_z(None, "J", 50.0)
        self.assertIsNotNone(info["kwargs"].get("confirm_fn"))

    def test_move_z_limit_check_failure(self):
        """A move past the galvo's travel is refused, and nothing is sent."""
        r = changes.move_z(make_client(), "J", 99999.0, z_mode="galvo")
        self.assertFalse(r["success"])

    # â”€â”€ set_z_stack_definition wiring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_set_z_stack_definition_begin_end(self):
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", begin_um=-5.0, end_um=5.0)
        self.assertIs(info["api_obj"], info["client"].PyApiSetZStackDefinitionByJobName)
        self.assertEqual(info["model"].JobName, "J")
        self.assertEqual(info["model"].SetBegin, 1)
        self.assertEqual(info["model"].SetEnd, 1)
        self.assertAlmostEqual(info["model"].BeginValue, -5.0e-6, places=12)
        self.assertAlmostEqual(info["model"].EndValue, 5.0e-6, places=12)

    def test_set_z_stack_definition_begin_only(self):
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", begin_um=10.0)
        self.assertEqual(info["model"].SetBegin, 1)
        self.assertEqual(info["model"].SetEnd, 2)  # ignore

    def test_set_z_stack_definition_end_only(self):
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", end_um=10.0)
        self.assertEqual(info["model"].SetBegin, 2)  # ignore
        self.assertEqual(info["model"].SetEnd, 1)

    def test_set_z_stack_definition_reset_begin(self):
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", old_begin_um=-3.0)
        self.assertEqual(info["model"].SetBegin, 0)  # reset

    def test_set_z_stack_definition_zero_begin(self):
        """begin_um=0.0 is a valid z-position - must not be treated as None."""
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", begin_um=0.0, end_um=10.0)
        self.assertEqual(info["model"].SetBegin, 1)
        self.assertAlmostEqual(info["model"].BeginValue, 0.0, places=12)

    def test_set_z_stack_definition_provides_confirm_fn(self):
        info, _ = self._run_set(changes.change, None, "z_stack_definition", "J", begin_um=-5.0, end_um=5.0)
        self.assertIsNotNone(info["kwargs"].get("confirm_fn"))

    # â”€â”€ filter wheel wiring â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def test_set_filter_wheel_slot_model(self):
        info, _ = self._run_set(changes.change, None, "filter_wheel_slot", "J", setting_index=0, beam_route="BR1", filter_wheel_type="emission", slot_index=2)
        self.assertIs(info["api_obj"], info["client"].PyApiSetFilterWheelSlotByJobName)
        self.assertEqual(info["model"].JobName, "J")
        self.assertEqual(info["model"].SettingIndex, 0)
        self.assertEqual(info["model"].BeamRoute, "BR1")
        self.assertEqual(info["model"].SlotIndex, 2)

    def test_set_filter_wheel_slot_provides_confirm_fn(self):
        info, _ = self._run_set(changes.change, None, "filter_wheel_slot", "J", setting_index=0, beam_route="BR1", filter_wheel_type="emission", slot_index=2)
        self.assertIsNotNone(info["kwargs"].get("confirm_fn"))

    def test_set_filter_wheel_spectrum_model(self):
        info, _ = self._run_set(changes.change, None, "filter_wheel_spectrum", "J", setting_index=0, beam_route="BR1", filter_wheel_type="emission", position=525)
        self.assertIs(
            info["api_obj"], info["client"].PyApiSetFilterWheelSpectrumPositionByJobName
        )
        self.assertEqual(info["model"].JobName, "J")
        self.assertEqual(info["model"].SettingIndex, 0)
        self.assertEqual(info["model"].BeamRoute, "BR1")
        self.assertEqual(info["model"].FilterSpectrumPosition, 525)

    def test_set_filter_wheel_spectrum_provides_confirm_fn(self):
        info, _ = self._run_set(changes.change, None, "filter_wheel_spectrum", "J", setting_index=0, beam_route="BR1", filter_wheel_type="emission", position=525)
        self.assertIsNotNone(info["kwargs"].get("confirm_fn"))


# =============================================================================
# 9. Set function defaults
# =============================================================================


class TestSetFunctionDefaults(unittest.TestCase):
    def _get_kwargs(self, fn):
        captured = {}

        def mock_fire(client, api_obj, desc, **kw):
            captured.update(kw)
            return _make_v6_result(msg=desc)

        with patch.object(send_loop, "confirm_and_fire", side_effect=mock_fire):
            fn(make_client(), "J")
        return captured

    def test_zoom_defaults(self):
        d = self._get_kwargs(lambda c, j: changes.change(c, "zoom", j, value=5.0))
        self.assertEqual(d["profile"].max_retries, reading_settings.WRITING.max_retries)
        self.assertIsNotNone(d["confirm_fn"])

    def test_scan_speed_defaults(self):
        d = self._get_kwargs(lambda c, j: changes.change(c, "scan_speed", j, value=600))
        self.assertEqual(d["profile"].max_retries, reading_settings.WRITING.max_retries)
        self.assertIsNotNone(d["confirm_fn"])


# =============================================================================
# 10-14. Unchanged tests (make_changeable_copy, limits, format, etc.)
# =============================================================================


class TestMakeChangeableCopy(unittest.TestCase):
    def _settings(self, **overrides):
        s = {
            "zoom": {"current": 5.0},
            "scanSpeed": {"value": 600, "isResonant": False},
            "scanMode": "xyz",
            "sequentialMode": "Frame",
            "scanFieldRotation": {"value": 0.0},
            "format": "512 x 512",
            "objective": {"name": "HC PL APO 63x", "magnification": 63},
            "activeSettings": [
                {
                    "index": 0,
                    "name": "Setting 1",
                    "frameAccumulation": 1,
                    "frameAverage": 1,
                    "lineAccumulation": 1,
                    "lineAverage": 1,
                    "pinholeAiry": {"value": 1.0},
                    "activeDetectors": [
                        {"beamRoute": "BR1", "name": "HyD S1", "gain": {"value": 100}}
                    ],
                    "activeLaserLines": [
                        {
                            "beamRoute": "BR1",
                            "lineIndex": 0,
                            "wavelength": 488,
                            "laser": {"name": "OPSL 488"},
                            "intensity": {"value": 0.1},
                            "shutterOpen": True,
                        }
                    ],
                }
            ],
        }
        s.update(overrides)
        return s

    def test_none_returns_none(self):
        self.assertIsNone(parsing.make_changeable_copy(None))

    def test_basic_fields(self):
        ch = parsing.make_changeable_copy(self._settings())
        self.assertEqual(ch["zoom"]["current"], 5.0)
        self.assertEqual(ch["scanSpeed"]["value"], 600)

    def test_objective(self):
        ch = parsing.make_changeable_copy(self._settings())
        self.assertEqual(ch["objective"]["name"], "HC PL APO 63x")

    def test_active_settings(self):
        ch = parsing.make_changeable_copy(self._settings())
        self.assertEqual(
            ch["activeSettings"][0]["activeDetectors"][0]["_beamRoute"], "BR1"
        )

    def test_laser_lines(self):
        ch = parsing.make_changeable_copy(self._settings())
        self.assertEqual(
            ch["activeSettings"][0]["activeLaserLines"][0]["_beamRoute"], "BR1"
        )

    def test_stack_with_z(self):
        ch = parsing.make_changeable_copy(
            self._settings(stack={"begin": -5.0, "end": 5.0, "stepSize": 1.0})
        )
        self.assertAlmostEqual(ch["stack"]["size"], 10.0)

    def test_no_stack_no_z(self):
        ch = parsing.make_changeable_copy(self._settings(scanMode="xy"))
        self.assertNotIn("stack", ch)

    def test_multiple_settings(self):
        settings = self._settings()
        settings["activeSettings"].append(
            {
                "index": 1,
                "name": "Setting 2",
                "frameAccumulation": 2,
                "frameAverage": 4,
                "lineAccumulation": 1,
                "lineAverage": 1,
                "pinholeAiry": {"value": 0.5},
                "activeDetectors": [],
                "activeLaserLines": [],
            }
        )
        ch = parsing.make_changeable_copy(settings)
        self.assertEqual(len(ch["activeSettings"]), 2)


class TestStageLimits(unittest.TestCase):
    def setUp(self):
        limit_rules.set_stage_limits(
            x_min=0,
            x_max=130000,
            y_min=0,
            y_max=100000,
            z_galvo_min=-250,
            z_galvo_max=250,
            z_wide_min=0,
            z_wide_max=8000,
        )

    def test_valid_xy(self):
        limit_rules.check_xy(50000, 50000)

    def test_x_below_min(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_xy(-1, 50000)

    def test_x_above_max(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_xy(130001, 50000)

    def test_y_below_min(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_xy(50000, -1)

    def test_y_above_max(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_xy(50000, 100001)

    def test_z_galvo_valid(self):
        limit_rules.check_z(0, "galvo")

    def test_z_galvo_below(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_z(-251, "galvo")

    def test_z_galvo_above(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_z(251, "galvo")

    def test_z_wide_above(self):
        with self.assertRaises(RuntimeError):
            limit_rules.check_z(8001, "zwide")

    def test_z_unknown_mode_raises(self):
        with self.assertRaises(ValueError):
            limit_rules.check_z(100, "unknown")

    def test_unconfigured_raises(self):
        old = dict(limit_rules._stage_limits)
        limit_rules._stage_limits["x_min"] = None
        try:
            with self.assertRaises(RuntimeError):
                limit_rules.check_xy(0, 0)
        finally:
            limit_rules._stage_limits.update(old)


class TestFormatParsing(unittest.TestCase):
    def test_parse_standard(self):
        self.assertEqual(parsing.parse_format("512 x 512"), (512, 512))

    def test_parse_rectangular(self):
        self.assertEqual(parsing.parse_format("1024 x 768"), (1024, 768))

    def test_roundtrip(self):
        self.assertEqual(parsing.parse_format("2048 x 2048"), (2048, 2048))

    def test_parse_bad_input(self):
        with self.assertRaises(ValueError):
            parsing.parse_format("banana")


class TestSafeFloat(unittest.TestCase):
    def test_int(self):
        self.assertEqual(parsing._safe_float(5), 5.0)

    def test_string(self):
        self.assertEqual(parsing._safe_float("3.14"), 3.14)

    def test_none_default(self):
        self.assertEqual(parsing._safe_float(None, -1), -1)

    def test_bad_string_default(self):
        self.assertEqual(parsing._safe_float("abc", 0), 0)

    def test_none_no_default(self):
        self.assertIsNone(parsing._safe_float(None))


class TestSequentialModeGuard(unittest.TestCase):
    def test_empty_string(self):
        r = changes.change(None, "sequential_mode", "J", mode="")
        self.assertFalse(r["success"])

    def test_whitespace(self):
        self.assertFalse(changes.change(None, "sequential_mode", "J", mode="   ")["success"])

    def test_non_string(self):
        self.assertFalse(changes.change(None, "sequential_mode", "J", mode=123)["success"])

    def test_none(self):
        self.assertFalse(changes.change(None, "sequential_mode", "J", mode=None)["success"])


class TestReadback(unittest.TestCase):
    """One read-back window of a job's settings (``write.read_back``): only answers seen after the change count."""

    SETTINGS = {
        "jobName": "HiRes",
        "objective": {"slotIndex": 3},
        "imageSize": "100.0 um x 100.0 um",
        "zoom": {"current": 5.0},
    }

    def _read_back(self, value, *, observed_at, since=10.0):
        def ask(leg, spec, client, *, until, answer_counts, about, source_kwargs):
            return readers.Reading(value=value, source=leg, observed_at=observed_at, age_s=0.0)

        expect = writes.Expect("job_settings", lambda s: s["zoom"]["current"] == 5.0, about="HiRes", mode="log")
        with patch.object(reading_settings, "ask", ask):
            return send_loop.read_back(reading_settings, None, expect, since=since)()

    def test_success(self):
        self.assertTrue(self._read_back(self.SETTINGS, observed_at=time.time() + 1.0, since=None)["success"])

    def test_failure(self):
        result = self._read_back(None, observed_at=time.time() + 1.0, since=None)
        self.assertFalse(result["success"])
        self.assertIsNone(result["last_value"])

    def test_rejects_reading_from_before_command(self):
        self.assertFalse(self._read_back(self.SETTINGS, observed_at=10.0)["success"])

    def test_accepts_reading_from_after_command(self):
        self.assertTrue(self._read_back(self.SETTINGS, observed_at=10.1)["success"])


# =============================================================================
# 17. Waiting for the scanner idle before a change (write.wait_ready)
# =============================================================================


class TestCheckIdle(unittest.TestCase):
    """The idle wait is an ordinary scanner-status reading under the one rule."""

    def setUp(self):
        self._prior = reading_settings.STATE_READERS
        reading_settings.STATE_READERS = reading_settings.StateReaderProfile(scan_status_mode="api")

    def tearDown(self):
        reading_settings.STATE_READERS = self._prior

    def _statuses(self, *values):
        remaining = list(values)
        return lambda client: remaining.pop(0) if len(remaining) > 1 else remaining[0]

    def _check_idle(self):
        return send_loop.wait_ready(reading_settings, None, writes.idle())()

    def test_idle_returns_immediately(self):
        with patch.object(
            readers.api_reader, "get_scan_status", return_value="eScanIdle"
        ):
            result = self._check_idle()
        self.assertTrue(result["success"])
        self.assertIsInstance(result["logs"], list)

    def test_unknown_is_not_idle(self):
        statuses = self._statuses("Unknown", "Unknown", "eScanIdle")
        with patch.object(readers.api_reader, "get_scan_status", side_effect=statuses):
            result = self._check_idle()
        self.assertTrue(result["success"])

    def test_waits_until_idle(self):
        statuses = self._statuses(
            "eScanRunning", "eScanRunning", "eScanRunning", "eScanIdle"
        )
        with patch.object(readers.api_reader, "get_scan_status", side_effect=statuses):
            result = self._check_idle()
        self.assertTrue(result["success"])

    def test_busy_through_four_windows_fails(self):
        with patch.object(
            readers.api_reader, "get_scan_status", return_value="eScanRunning"
        ):
            result = self._check_idle()
        self.assertFalse(result["success"])
        self.assertTrue(any("not ready" in e["msg"] for e in result["logs"]))


# =============================================================================
# 18. Command envelope consistency
# =============================================================================


class TestMoveXYConsistency(unittest.TestCase):
    def test_move_xy_has_full_timing(self):
        client = make_client()
        limit_rules.set_stage_limits(
            x_min=0,
            x_max=130000,
            y_min=0,
            y_max=100000,
            z_galvo_min=-250,
            z_galvo_max=250,
            z_wide_min=0,
            z_wide_max=8000,
        )
        with (
            patch.object(
                readers.api_reader, "get_scan_status", return_value="eScanIdle"
            ),
            patch.object(errors, "_check_api_error", return_value=None),
            patch.object(
                send_loop,
                "read_back",
                return_value=lambda: {"success": True, "logs": []},
            ),
        ):
            r = changes.move_xy(client, 50000, 50000, unit="um")
        self.assertTrue(r["success"])
        for key in (
            "pre_check_s",
            "setup_s",
            "fire_s",
            "check_s",
            "confirm_s",
            "total_s",
            "attempts",
            "confirm_attempts",
            "method",
        ):
            self.assertIn(key, r["timing"])
        self.assertIn("position", r)

    def test_move_xy_no_confirm_bool(self):
        import inspect

        params = list(inspect.signature(changes.move_xy).parameters.keys())
        self.assertNotIn("confirm", params)
        self.assertIn("max_retries", params)
        self.assertIn("tolerance", params)


class TestGetStageLimits(unittest.TestCase):
    def test_returns_copy(self):
        limit_rules.set_stage_limits(
            x_min=1,
            x_max=2,
            y_min=3,
            y_max=4,
            z_galvo_min=5,
            z_galvo_max=6,
            z_wide_min=7,
            z_wide_max=8,
        )
        limits = limit_rules.get_stage_limits()
        self.assertEqual(limits["x_min"], 1)
        limits["x_min"] = 9999
        self.assertEqual(limit_rules.get_stage_limits()["x_min"], 1)


class TestPing(unittest.TestCase):
    def test_ping_success(self):
        self.assertTrue(readers.ping(make_client()))

    def test_ping_failure(self):
        client = MagicMock()
        client.PyApiPing.UpdateAwaitReceipt.side_effect = Exception("COM error")
        type(client.PyApiStatus.Model).ScanStatus = PropertyMock(
            side_effect=Exception("COM error")
        )
        self.assertFalse(readers.ping(client))


class TestSchemaValidation(unittest.TestCase):
    def test_missing_zoom_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parsing.make_changeable_copy(
                {"scanSpeed": {"value": 600, "isResonant": False}, "activeSettings": []}
            )
        self.assertIn("zoom", str(ctx.exception))

    def test_missing_active_settings_raises(self):
        with self.assertRaises(ValueError) as ctx:
            parsing.make_changeable_copy(
                {"zoom": {"current": 5.0}, "scanSpeed": {"value": 600}}
            )
        self.assertIn("activeSettings", str(ctx.exception))

    def test_valid_settings_pass(self):
        self.assertIsNotNone(
            parsing.make_changeable_copy(
                {
                    "zoom": {"current": 5.0},
                    "scanSpeed": {"value": 600, "isResonant": False},
                    "activeSettings": [],
                }
            )
        )


class TestUnclassifiedErrorLogging(unittest.TestCase):
    def test_unknown_error_logs_warning(self):
        with self.assertLogs("zmart_drivers.leica.stellaris.navigator_expert", level="WARNING") as cm:
            result = error_classification._is_transient_error("totally unexpected error xyz")
        self.assertFalse(result)
        self.assertTrue(any("Unclassified" in msg for msg in cm.output))

    def test_known_patterns_no_warning(self):
        self.assertTrue(error_classification._is_transient_error("block is being scanned"))
        self.assertFalse(error_classification._is_transient_error("out of range"))


# =============================================================================
# 19. acquire, confirm_acquire and select_job
# =============================================================================


class TestDispatchCheckExceptions(unittest.TestCase):
    """pre_check_fn / error_check_fn exceptions become structured failures."""

    def test_raising_pre_check_returns_failure_dict(self):
        result = _fire_block(
            None,
            SimpleNamespace(Model=SimpleNamespace()),
            "TestCmd",
            setup_fn=None,
            pre_check_fn=lambda: (_ for _ in ()).throw(RuntimeError("com fault")),
            error_check_fn=None,
        )
        self.assertFalse(result["success"])
        self.assertTrue(any("Pre-check raised" in e["msg"] for e in result["logs"]))

    def test_raising_error_check_is_transient_failure_not_exception(self):
        api_obj = SimpleNamespace(
            Model=SimpleNamespace(),
            UpdateAsync=lambda: None,
        )
        fake_client = SimpleNamespace(
            PyApiCommandEcho=SimpleNamespace(
                Model=SimpleNamespace(Result=0, HasError=False)
            )
        )
        result = _fire_block(
            fake_client,
            api_obj,
            "TestCmd",
            profile=replace(reading_settings.WRITING, send_without_receipt=True, max_retries=0),
            setup_fn=None,
            pre_check_fn=None,
            error_check_fn=lambda: (_ for _ in ()).throw(
                RuntimeError("echo read fault")
            ),
        )
        self.assertFalse(result["success"])
        self.assertTrue(any("Error check raised" in e["msg"] for e in result["logs"]))


class TestUnitValidation(unittest.TestCase):
    def test_move_xy_unknown_unit_returns_failure_dict(self):
        result = changes.move_xy(None, 1.0, 2.0, unit="nm")
        self.assertFalse(result["success"])
        self.assertIn("unknown unit", result["message"])

    def test_move_z_unknown_unit_returns_failure_dict(self):
        result = changes.move_z(None, "Job", 1.0, unit="nm")
        self.assertFalse(result["success"])
        self.assertIn("unknown unit", result["message"])

    def test_set_objective_requires_exactly_one_selector(self):
        client = make_client()  # gate-permissive; the selector validation is under test
        result = compensated_changes.set_objective(
            client, "Job", {}, slot_index=1, name="HC PL APO 63x"
        )
        self.assertFalse(result["success"])
        self.assertIn("exactly one", result["message"])
        result = compensated_changes.set_objective(client, "Job", {})
        self.assertFalse(result["success"])


class TestQuantisedSizesDirection(unittest.TestCase):
    def test_a_descending_stack_keeps_its_direction(self):
        # begin=10, end=-10: the snapped sizes stay negative.
        sizes = writes.quantised_sizes(-20.0, 3.0)
        self.assertEqual([round(s, 6) for s in sizes], [-18.0, -21.0])

    def test_an_ascending_stack_unchanged(self):
        self.assertEqual([round(s, 6) for s in writes.quantised_sizes(20.0, 3.0)], [18.0, 21.0])


def status(value):
    """A scanner status read after the watch began; None: no answer."""
    if value is None:
        return None
    return readers.Reading(value=value, source="api", observed_at=time.time() + 1.0, age_s=0.0)


class TestConfirmAcquire(unittest.TestCase):
    def test_idle_without_scanning_returns_failure(self):
        """Always idle and no new picture -> the scan never started."""
        with (
            patch.object(readers, "get_scan_status", return_value=status("eScanIdle")),
            patch.object(changes, "_check_api_error", return_value=None),
            patch.object(changes.state_tree, "last_image_id", return_value=10),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, image_before=10, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertFalse(result["success"])
        self.assertIs(result["started"], False)

    def test_a_scan_too_quick_to_see_ran_when_its_picture_arrived(self):
        """Never seen running, but LAS X made a new picture: the scan ran."""
        with (
            patch.object(readers, "get_scan_status", return_value=status("eScanIdle")),
            patch.object(changes, "_check_api_error", return_value=None),
            patch.object(changes.state_tree, "last_image_id", return_value=11),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, image_before=10, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertTrue(result["success"])
        self.assertIs(result["started"], True)

    def test_without_the_picture_number_an_unseen_scan_is_unknown(self):
        """No picture number to compare: not seen running is not proof it never ran."""
        with (
            patch.object(readers, "get_scan_status", return_value=status("eScanIdle")),
            patch.object(changes, "_check_api_error", return_value=None),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, image_before=None, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertFalse(result["success"])
        self.assertIsNone(result["started"])

    def test_scanning_then_idle(self):
        """Non-idle then idle -> success (saw scanning)."""
        call_count = [0]

        def mock_status(client, **_kwargs):
            call_count[0] += 1
            return status("eScanStarted" if call_count[0] <= 2 else "eScanIdle")

        with (
            patch.object(readers, "get_scan_status", side_effect=mock_status),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, timeout=5.0, poll_interval=0.001
            )
        self.assertTrue(result["success"])
        self.assertTrue(result["started"])

    def test_a_status_from_before_the_watch_is_not_evidence(self):
        """Only readings taken after the watch began count."""
        old = readers.Reading(value="eScanRunning", source="api", observed_at=1.0, age_s=0.0)
        with (
            patch.object(readers, "get_scan_status", return_value=old),
            patch.object(changes, "_check_api_error", return_value=None),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertFalse(result["started"])

    def test_failed_status_read_is_not_evidence_of_scanning(self):
        """A failed read (None) then idle forever -> failure, not success.

        A transient read error must not set saw_scanning: that would skip
        the start timeout and let two idle reads confirm an acquisition
        that never ran.
        """
        call_count = [0]

        def mock_status(client, **_kwargs):
            call_count[0] += 1
            return status(None if call_count[0] == 1 else "eScanIdle")

        with (
            patch.object(readers, "get_scan_status", side_effect=mock_status),
            patch.object(changes, "_check_api_error", return_value=None),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertFalse(result["success"])

    def test_unknown_status_is_not_evidence_of_scanning(self):
        """The API reader's 'Unknown' failure sentinel behaves like None."""
        call_count = [0]

        def mock_status(client, **_kwargs):
            call_count[0] += 1
            return status("Unknown" if call_count[0] == 1 else "eScanIdle")

        with (
            patch.object(readers, "get_scan_status", side_effect=mock_status),
            patch.object(changes, "_check_api_error", return_value=None),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, start_timeout=0.0, timeout=1.0, poll_interval=0.001
            )
        self.assertFalse(result["success"])

    def test_unknown_read_breaks_idle_streak_but_not_saw_scanning(self):
        """Scanning, then a failed read between idles -> still succeeds."""
        statuses = iter(
            ["eScanStarted", "Unknown", "eScanIdle", "eScanIdle", "eScanIdle"]
        )

        def mock_status(client, **_kwargs):
            return status(next(statuses, "eScanIdle"))

        with (
            patch.object(readers, "get_scan_status", side_effect=mock_status),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(
                None, timeout=5.0, poll_interval=0.001
            )
        self.assertTrue(result["success"])

    def test_a_permanent_error_before_the_scan_starts_ends_the_watch(self):
        with (
            patch.object(readers, "get_scan_status", return_value=status("eScanIdle")),
            patch.object(changes, "_check_api_error", return_value={"error": "out of range"}),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(None, start_timeout=10.0, timeout=10.0, poll_interval=0.001)
        self.assertFalse(result["success"])
        self.assertFalse(result["started"])
        self.assertTrue(any("permanent" in e["msg"] for e in result["logs"]))

    def test_a_scan_that_runs_past_the_watch_has_started(self):
        with (
            patch.object(readers, "get_scan_status", return_value=status("eScanRunning")),
            patch("time.sleep"),
        ):
            result = changes.confirm_acquire(None, timeout=0.05, poll_interval=0.001)
        self.assertFalse(result["success"])
        self.assertTrue(result["started"])


class TestAcquire(unittest.TestCase):
    """``acquire``: sent once through ``run()``, then the scan is watched."""

    def setUp(self):
        self.client = make_client()
        self.api_obj = make_api_obj()
        self.client.PyApiAcquireJob = self.api_obj

    def _acquire(self, watched, **options):
        with patch.object(changes, "confirm_acquire", return_value=watched) as watch:
            result = changes.acquire(self.client, "HiRes", **options)
        return result, watch

    def test_a_capture_that_ran_is_confirmed(self):
        result, _ = self._acquire({"success": True, "started": True, "logs": []})
        self.assertEqual((result["success"], result["confirmed"]), (True, True))
        self.assertEqual(result["timing"]["confirm_attempts"], 1)
        self.assertEqual(self.api_obj.UpdateAsync.call_count, 1)

    def test_a_capture_whose_start_is_unknown_is_unconfirmed_not_failed(self):
        result, _ = self._acquire({"success": False, "started": None, "logs": []})
        self.assertEqual((result["success"], result["confirmed"]), (True, False))

    def test_a_capture_whose_scan_never_starts_fails(self):
        result, _ = self._acquire({"success": False, "started": False, "logs": []})
        self.assertFalse(result["success"])
        self.assertFalse(result["confirmed"])
        self.assertIn("the scan never started", result["message"])
        self.assertEqual(self.api_obj.UpdateAsync.call_count, 1)  # never sent again

    def test_take_picture_raises_when_the_scan_never_starts(self):
        with (
            patch.object(changes, "confirm_acquire", return_value={"success": False, "started": False, "logs": []}),
            self.assertRaisesRegex(RuntimeError, "acquire failed"),
        ):
            changes.take_picture(self.client, "HiRes")
        self.assertEqual(self.api_obj.UpdateAsync.call_count, 1)

    def test_a_scan_seen_running_but_not_finishing_is_unconfirmed_not_failed(self):
        result, _ = self._acquire({"success": False, "started": True, "logs": []})
        self.assertEqual((result["success"], result["confirmed"]), (True, False))
        self.assertIn("(readback unconfirmed)", result["message"])
        self.assertEqual(self.api_obj.UpdateAsync.call_count, 1)

    def test_an_unconfirmed_capture_names_the_dialog_in_the_way(self):
        with patch.object(
            reading_settings.WRITING.transport, "dialog_warning_since", return_value="LAS X shows 'Stage error'"
        ):
            result, _ = self._acquire({"success": False, "started": False, "logs": []})
        self.assertIn("dialog=", result["message"])
        self.assertIn("Stage error", result["message"])

    def test_the_watch_gets_only_the_numbers_given(self):
        _, watch = self._acquire({"success": True, "started": True, "logs": []}, poll_timeout=5.0, start_timeout=2.0)
        # and the number of LAS X's last picture, None here: no hardware server answers
        self.assertEqual(watch.call_args.kwargs, {"image_before": None, "timeout": 5.0, "start_timeout": 2.0})

    def test_a_capture_that_was_not_sent_is_not_watched(self):
        self.api_obj.UpdateAsync.side_effect = RuntimeError("COM error")
        result, watch = self._acquire({"success": True, "started": True, "logs": []})
        self.assertFalse(result["success"])
        watch.assert_not_called()
        self.assertEqual(self.api_obj.UpdateAsync.call_count, 1)


class TestConfirmSelectJob(unittest.TestCase):
    # The read order of the confirmation is covered by
    # testing/unit/test_select_job_confirm.py.

    def _select(self, profile, **patches):
        client = make_client()
        client.PyApiSelectJobByName = make_api_obj()
        dispatched = {"success": True, "confirmed": True, "message": "sent", "logs": []}
        with (
            patch.object(reading_settings, "STATE_READERS", profile),
            patch.object(changes.dispatch, "run", return_value=dispatched) as run,
            contextlib.ExitStack() as stack,
        ):
            for target, name, kwargs in patches.get("more", []):
                stack.enter_context(patch.object(target, name, **kwargs))
            result = changes.select_job(client, "Overview")
        return result, dispatched, run

    def test_select_job_early_exit_pins_api_mode(self):
        profile = reading_settings.StateReaderProfile(
            selected_job_confirm_fallback="api",
        )
        calls = []

        def fake_get_jobs(client, **kwargs):
            calls.append(kwargs)
            # A stale log would claim Overview is selected. The API says it is
            # not, so select_job must dispatch instead of early-exiting.
            if kwargs.get("mode") != "api":
                return [{"Name": "Overview", "IsSelected": True}]
            return [
                {"Name": "AF Job", "IsSelected": True},
                {"Name": "Overview", "IsSelected": False},
            ]

        result, dispatched, run = self._select(
            profile, more=[(readers, "get_jobs", {"side_effect": fake_get_jobs})]
        )

        self.assertEqual(result, dispatched)
        self.assertEqual(calls[0]["mode"], "api")
        run.assert_called_once()

    def test_select_job_log_confirmation_does_not_api_early_exit(self):
        profile = reading_settings.StateReaderProfile(
            selected_job_confirm_fallback="log",
        )
        get_jobs = MagicMock()
        result, dispatched, run = self._select(
            profile,
            more=[
                (changes, "selected_job_from_log", {"return_value": None}),
                (readers, "get_jobs", {"new": get_jobs}),
            ],
        )

        self.assertEqual(result, dispatched)
        get_jobs.assert_not_called()
        run.assert_called_once()

    def test_select_job_primes_log_cluster_when_profile_enables_it(self):
        profile = reading_settings.StateReaderProfile(
            selected_job_confirm_fallback="log",
            selected_job_log_prime_cluster=True,
        )
        jobs = [
            {"Name": "AF Job", "IsSelected": True},
            {"Name": "Overview", "IsSelected": False},
            {"Name": "HiRes", "IsSelected": False},
        ]
        primed = []

        def fake_settings(client, job_name, **kwargs):
            primed.append((job_name, kwargs.get("mode")))
            return {"jobName": job_name}

        result, dispatched, run = self._select(
            profile,
            more=[
                (changes, "selected_job_from_log", {"return_value": None}),
                (changes, "api_jobs", {"return_value": (jobs, "ok")}),
                (changes.readers, "get_job_settings", {"side_effect": fake_settings}),
            ],
        )

        self.assertEqual(result, dispatched)
        self.assertEqual(
            primed, [("AF Job", "api"), ("Overview", "api"), ("HiRes", "api")]
        )
        confirm = run.call_args.args[2].confirm
        self.assertEqual((confirm.reading, confirm.mode, confirm.fallback), ("selected_job", "direct", "log"))


class TestSelectJobEndToEnd(unittest.TestCase):
    """``select_job`` through the real dispatcher and read loop; only the raw
    CAM reader is faked, and the hardware server cannot be reached.

    The API's job list confirms a switch it saw happen (its answer from before
    the change named another job). It never confirms the A->B->A restore,
    where it named the target before the change: then only the log may, and a
    silent log leaves the change unconfirmed.
    """

    def _client(self, after_fire):
        client = make_client()
        api_obj = make_api_obj()
        client.PyApiSelectJobByName = api_obj
        fired = threading.Event()

        def fire(receipt_timeout):
            # Simulate LAS X processing: echo settles, job switches.
            client.PyApiCommandEcho.Model.Result = 1
            fired.set()
            return True

        api_obj.UpdateAwaitReceipt = MagicMock(side_effect=fire)
        raw_reads_after_fire = []

        def raw_get_jobs(c, **kwargs):
            if fired.is_set():
                raw_reads_after_fire.append(1)
                return after_fire
            return [
                {"Name": "Overview", "IsSelected": True},
                {"Name": "HiRes", "IsSelected": False},
            ]

        return client, raw_get_jobs, raw_reads_after_fire

    def _select_job(self, job_name, after_fire):
        client, raw_get_jobs, raw_reads_after_fire = self._client(after_fire)
        profile = reading_settings.StateReaderProfile(selected_job_confirm_fallback="hybrid")
        with (
            patch.object(reading_settings, "STATE_READERS", profile),
            patch.object(readers.api_reader, "get_jobs", side_effect=raw_get_jobs),
            patch.object(changes, "selected_job_from_log", return_value=None),
        ):
            result = changes.select_job(client, job_name)
        return result, raw_reads_after_fire

    def test_the_api_confirms_a_switch_it_saw_happen(self):
        result, raw_reads_after_fire = self._select_job(
            "HiRes", [{"Name": "Overview", "IsSelected": False}, {"Name": "HiRes", "IsSelected": True}]
        )
        self.assertTrue(result["success"])
        self.assertTrue(
            result.get("confirmed"),
            "hybrid select_job did not confirm although the API could witness the transition",
        )
        self.assertGreater(len(raw_reads_after_fire), 0, "the API was never asked after the change")

    def test_a_stale_api_naming_the_target_before_the_change_never_confirms_it(self):
        result, raw_reads_after_fire = self._select_job(
            "Overview", [{"Name": "Overview", "IsSelected": True}, {"Name": "HiRes", "IsSelected": False}]
        )
        self.assertTrue(result["success"])
        self.assertFalse(result["confirmed"])
        self.assertEqual(raw_reads_after_fire, [], "the API was asked although it cannot witness this change")


# =============================================================================
# 20. _make_log_entry
# =============================================================================


class TestMakeLogEntry(unittest.TestCase):
    def test_shape(self):
        entry = door.make_log_entry("info", "test message")
        self.assertIn("ts", entry)
        self.assertEqual(entry["level"], "info")
        self.assertEqual(entry["msg"], "test message")
        self.assertIsInstance(entry["ts"], float)


if __name__ == "__main__":
    unittest.main()
