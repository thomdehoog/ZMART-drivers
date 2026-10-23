import importlib.util
import logging
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from zmart_drivers.leica.stellaris.navigator_expert.primitives.direct import profile as reading_settings




def _load_validator():
    path = Path(__file__).resolve().parents[1] / "hardware" / "validate_hardware.py"
    spec = importlib.util.spec_from_file_location("validate_hardware_under_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestValidateHardwareCli(unittest.TestCase):
    def setUp(self):
        self._profile = reading_settings.STATE_READERS
        self.addCleanup(self._restore)

    def _restore(self):
        reading_settings.STATE_READERS = self._profile

    def test_log_tuning_flags_preserve_current_confirm_fallback(self):
        reading_settings.STATE_READERS = reading_settings.StateReaderProfile(
            selected_job_confirm_fallback="log",
        )
        validator = _load_validator()
        args = SimpleNamespace(
            mock=False,
            state_reader_mode=None,
            log_select_cluster_max_age_s=4.0,
            prime_log_select_cluster=False,
        )

        validator._apply_log_select_confirmation(
            args, logging.getLogger("validate-hardware-test")
        )

        self.assertEqual(reading_settings.STATE_READERS.selected_job_confirm_fallback, "log")
        self.assertEqual(reading_settings.STATE_READERS.selected_job_log_cluster_max_age_s, 4.0)

    def test_the_mock_confirms_a_job_change_through_the_api_behind_the_direct_reader(self):
        validator = _load_validator()
        args = SimpleNamespace(
            mock=True,
            state_reader_mode=None,
            log_select_cluster_max_age_s=None,
            prime_log_select_cluster=False,
        )

        validator._apply_log_select_confirmation(
            args, logging.getLogger("validate-hardware-test")
        )

        self.assertEqual(reading_settings.STATE_READERS.selected_job_mode, "direct")
        self.assertEqual(reading_settings.STATE_READERS.selected_job_confirm_fallback, "api")

    def test_the_confirmation_source_is_no_longer_an_option(self):
        validator = _load_validator()

        with self.assertRaises(SystemExit):
            validator.parse_args(["--select-job-confirm-source", "log"])


if __name__ == "__main__":
    unittest.main()
