"""
Unit tests: detection edits in a saved experiment (``.lrp``), and the z-drive edit.
================================================================================
The CAM API has no command for a detector's offset or acquisition mode, or a
channel's detection band; LAS X keeps all three in the job's settings in the
saved experiment, and the driver's ``.lrp`` route (save, edit, load) changes
them there. Names and values are LAS X's own: ``Detector`` (``Name``,
``Offset``, ``AcquisitionMode``/``AcquisitionModeName``: Standard 0,
PhotonCounting 1, BrightR 2, from LAS X's own mapping) and ``Spectro/MultiBand``
(``Channel``, ``LeftWorld``/``RightWorld``, nm). Run on a copy of a saved
simulator experiment.
"""

import shutil
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.experimental.lrp_edits import (
    detection,
    z,
)

SAVED = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "z_readback"
    / "_ScanningTemplate_galvo_stack.lrp"
)


@pytest.fixture
def lrp(tmp_path):
    copy = tmp_path / "experiment.lrp"
    shutil.copy(SAVED, copy)
    return copy


def values(lrp, tag, match, attribute, job="HiRes"):
    return detection.job_element_values(lrp, job, tag, match, attribute)


class TestDetectorOffset:
    def test_sets_the_named_detector_in_every_setting_of_the_job(self, lrp):
        assert set(values(lrp, "Detector", {"Name": "HyD 2"}, "Offset")) == {"0"}
        changed = detection.lrp_set_detector_offset(lrp, 2.5, "HiRes", "HyD 2")
        assert changed == len(values(lrp, "Detector", {"Name": "HyD 2"}, "Offset")) > 0
        assert set(values(lrp, "Detector", {"Name": "HyD 2"}, "Offset")) == {"2.5"}
        assert detection.lrp_verify_detector_offset(lrp, 2.5, "HiRes", "HyD 2")

    def test_leaves_other_detectors_and_jobs_alone(self, lrp):
        detection.lrp_set_detector_offset(lrp, 2.5, "HiRes", "HyD 2")
        assert set(values(lrp, "Detector", {"Name": "HyD S 3"}, "Offset")) == {"0"}
        assert set(
            values(lrp, "Detector", {"Name": "HyD 2"}, "Offset", job="Overview")
        ) == {"0"}

    def test_an_unknown_detector_changes_nothing(self, lrp):
        before = lrp.read_bytes()
        assert detection.lrp_set_detector_offset(lrp, 2.5, "HiRes", "HyD 99") == 0
        assert lrp.read_bytes() == before
        assert not detection.lrp_verify_detector_offset(lrp, 2.5, "HiRes", "HyD 99")


class TestDetectorMode:
    def test_standard(self, lrp):
        assert set(
            values(lrp, "Detector", {"Name": "HyD 2"}, "AcquisitionModeName")
        ) == {"PhotonCounting"}
        detection.lrp_set_detector_mode(lrp, "Standard", "HiRes", "HyD 2")
        assert set(values(lrp, "Detector", {"Name": "HyD 2"}, "AcquisitionMode")) == {
            "0"
        }
        assert set(
            values(lrp, "Detector", {"Name": "HyD 2"}, "AcquisitionModeName")
        ) == {"Standard"}
        assert detection.lrp_verify_detector_mode(lrp, "Standard", "HiRes", "HyD 2")

    def test_an_unknown_mode_changes_nothing(self, lrp):
        before = lrp.read_bytes()
        assert detection.lrp_set_detector_mode(lrp, "Turbo", "HiRes", "HyD 2") == 0
        assert lrp.read_bytes() == before


class TestDetectionBand:
    def test_sets_the_channel_band(self, lrp):
        detection.lrp_set_detection_band(lrp, 500, 550, "HiRes", 2)
        assert set(values(lrp, "MultiBand", {"Channel": "2"}, "LeftWorld")) == {"500"}
        assert set(values(lrp, "MultiBand", {"Channel": "2"}, "RightWorld")) == {"550"}
        assert detection.lrp_verify_detection_band(lrp, 500, 550, "HiRes", 2)
        assert set(values(lrp, "MultiBand", {"Channel": "3"}, "LeftWorld")) != {"500"}

    def test_a_band_that_ends_before_it_begins_changes_nothing(self, lrp):
        before = lrp.read_bytes()
        assert detection.lrp_set_detection_band(lrp, 550, 500, "HiRes", 2) == 0
        assert lrp.read_bytes() == before


class TestZUseMode:
    """LAS X writes ZUseMode 1 with ZUseModeName z-galvo and 2 with z-wide
    (every saved experiment in the test data, and SetZPosition's ZUseMode)."""

    def test_z_wide_is_2(self, lrp):
        z.lrp_set_z_use_mode(lrp, "z-wide", "HiRes")
        assert set(values(lrp, "ATLConfocalSettingDefinition", {}, "ZUseMode")) == {"2"}
        assert set(values(lrp, "ATLConfocalSettingDefinition", {}, "ZUseModeName")) == {
            "z-wide"
        }

    def test_z_galvo_is_1(self, lrp):
        z.lrp_set_z_use_mode(lrp, "z-wide", "HiRes")
        z.lrp_set_z_use_mode(lrp, "z-galvo", "HiRes")
        assert set(values(lrp, "ATLConfocalSettingDefinition", {}, "ZUseMode")) == {"1"}
        assert set(values(lrp, "ATLConfocalSettingDefinition", {}, "ZUseModeName")) == {
            "z-galvo"
        }
