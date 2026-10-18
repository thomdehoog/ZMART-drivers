"""
Unit tests: the direct reader, beside the API reader and the log reader.
================================================================================
The direct reader reads LAS X's live state from its hardware server's state
tree. It is a reader like the other two: the same names, and answers of the
same shape, so a caller cannot tell which source answered except by asking.
Leica agreed (2026-10-13) to the driver reading the stage, the selected job
and the z drives this way; the direct reader reads nothing else and never
writes.

Parity is checked against the API reader on the behavioural mock, whose
state a stand-in tree mirrors.
"""

import pytest
from limits_fixtures import install_permissive_limits
from mock_lasx_api import MockLasxClient
from state_tree_stand_in import StandInNodes, answer_json_read

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions import (
    get as capabilities,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.actions.objectives import (
    objective_by_slot,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    api_reader,
    direct_reader,
    hardware_server,
    state_tree,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface.parsing import (
    make_changeable_copy,
)

#: The jobs' block and element ids as on the simulator (2026-10-13).
JOBS = {"AF Job": (156, 1), "Overview": (154, 2), "HiRes": (152, 3)}


def selection(jobs):
    """Each job's name, whether it is selected and whether it is the autofocus job: what the driver uses."""
    return {job["Name"]: (job["IsSelected"], job["IsAutofocus"]) for job in jobs}


class StandInServer:
    """LAS X's hardware server: its state tree, and its answers to the two JSON reads."""

    def __init__(self, nodes, client=None):
        self.ServerNodes = nodes
        self.client = client
        self.sent = []

    def SendCommand2(self, xml, result):
        self.sent.append(xml)
        answer = answer_json_read(xml, self.client, self.ServerNodes)
        return (
            answer
            if answer is not None
            else '<Result Error="1">not a read the stand-in answers</Result>'
        )


@pytest.fixture
def client():
    return install_permissive_limits(MockLasxClient(), wide_stage=True)


@pytest.fixture
def tree(client, monkeypatch):
    nodes = StandInNodes(
        lambda: JOBS,
        selected=lambda: client._selected_job,
        stage=lambda: (client._stage_x, client._stage_y),
        zdrives=lambda: {
            1: client._hardware_z_um["z-galvo"] * 1e-6,
            2: client._hardware_z_um["z-wide"] * 1e-6,
        },
    )
    monkeypatch.setattr(hardware_server, "attach", lambda: StandInServer(nodes, client))
    hardware_server.forget_connection()
    state_tree.forget_nodes()
    return nodes


class TestParityWithTheApiReader:
    def test_xy_is_the_api_readers_answer(self, client, tree):
        assert direct_reader.get_xy() == api_reader.get_xy(client)

    def test_xy_after_a_stage_move(self, client, tree):
        client._stage_x, client._stage_y = 0.0612, 0.0405
        assert direct_reader.get_xy() == api_reader.get_xy(client)
        assert direct_reader.get_xy()["x_um"] == pytest.approx(61200.0)

    @pytest.mark.parametrize("job", ["HiRes", "Overview", "AF Job"])
    def test_the_selected_job_is_the_api_readers(self, client, tree, job):
        client._selected_job = job
        assert (
            direct_reader.get_selected_job()["Name"]
            == api_reader.get_selected_job(client)["Name"]
        )

    def test_the_selected_job_carries_its_live_block_id(self, client, tree):
        assert direct_reader.get_selected_job() == {
            "Name": "HiRes",
            "ID": 152,
            "IsSelected": True,
            "IsAutofocus": False,
        }

    @pytest.mark.parametrize("job", ["HiRes", "Overview", "AF Job"])
    def test_the_jobs_and_their_selection_are_the_api_readers(self, client, tree, job):
        client._selected_job = job
        assert selection(direct_reader.get_jobs()) == selection(
            api_reader.get_jobs(client)
        )


class TestJobs:
    def test_every_job_in_sequence_order_with_its_live_block_id(self, client, tree):
        assert direct_reader.get_jobs() == [
            {"Name": "AF Job", "ID": 156, "IsSelected": False, "IsAutofocus": True},
            {"Name": "Overview", "ID": 154, "IsSelected": False, "IsAutofocus": False},
            {"Name": "HiRes", "ID": 152, "IsSelected": True, "IsAutofocus": False},
        ]

    def test_the_autofocus_job_is_the_one_navigator_expert_marks(self, client, tree):
        # Each job's own IsAutoFocusOnStart, so it does not depend on the block
        # ids, which an experiment load renumbers.
        tree.autofocus = ("Overview",)
        tree.reload()
        assert [j["Name"] for j in direct_reader.get_jobs() if j["IsAutofocus"]] == [
            "Overview"
        ]

    def test_a_reloaded_experiment_gives_the_new_ids(self, client, tree):
        tree.jobs = lambda: {
            "AF Job": (166, 1),
            "Overview": (164, 2),
            "HiRes": (162, 3),
        }
        tree.reload()
        assert [j["ID"] for j in direct_reader.get_jobs()] == [166, 164, 162]
        assert direct_reader.get_selected_job()["ID"] == 162

    def test_get_job_by_name_as_the_other_readers_name_it(self, client, tree):
        assert direct_reader.get_job_by_name("Overview") == {
            "Name": "Overview",
            "ID": 154,
            "IsSelected": False,
            "IsAutofocus": False,
        }
        assert direct_reader.get_job_by_name("No such job") is None


class TestZ:
    def test_each_drive_in_micrometres(self, client, tree):
        client._hardware_z_um.update({"z-galvo": -2.5, "z-wide": 4999.5})
        assert direct_reader.read_z_um("z-galvo") == pytest.approx(-2.5)
        assert direct_reader.read_z_um("z-wide") == pytest.approx(4999.5)

    def test_read_zwide_um_as_the_log_reader_names_it(self, client, tree):
        client._hardware_z_um["z-wide"] = 123.0
        assert direct_reader.read_zwide_um() == pytest.approx(123.0)

    def test_an_unknown_drive_is_refused(self, tree):
        with pytest.raises(ValueError, match="z-piezo"):
            direct_reader.read_z_um("z-piezo")


class TestTheCurrentSetting:
    """What LAS X is set to now, shaped as the log reader's job settings give it.

    The expected values are the simulator's, read on 2026-10-13 from the
    state tree and, where the log has them, equal to the log's job settings
    for the selected job (HiRes).
    """

    def test_scan_status(self, tree):
        assert direct_reader.get_scan_status() == "eScanIdle"
        tree.status = {"AcquisitionState": 3}
        assert direct_reader.get_scan_status() == "eScanSeries"

    def test_an_unknown_scan_state_is_no_answer(self, tree):
        tree.status = {"AcquisitionState": 9}
        assert direct_reader.get_scan_status() == "Unknown"

    def test_scan_mode(self, tree):
        assert direct_reader.get_scan_mode() == "xyz"

    def test_zoom(self, tree):
        assert direct_reader.get_zoom() == {"current": 1.0, "min": 0.75, "max": 48.0}

    def test_scan_speed(self, tree):
        assert direct_reader.get_scan_speed() == {
            "value": 400,
            "unit": "Hz",
            "isResonant": False,
        }

    def test_format(self, tree):
        assert direct_reader.get_format() == "512 x 512"

    def test_averaging_and_accumulation(self, tree):
        tree.live[("Confocal", "ScanningParameter", "FrameAverage")] = 4
        assert direct_reader.get_averaging() == {
            "lineAverage": 1,
            "lineAccumulation": 1,
            "frameAverage": 4,
            "frameAccumulation": 1,
        }

    def test_pinhole(self, tree):
        pinhole = direct_reader.get_pinhole()
        assert pinhole["airy"] == pytest.approx(
            {"value": 0.9994, "min": 0.2512, "max": 2.5124}, abs=1e-4
        )
        assert pinhole["metres"] == pytest.approx(397.7875e-6)

    def test_the_objective_in_the_light_path(self, tree):
        assert direct_reader.get_objective() == {
            "slotIndex": 0,
            "name": "HC PL APO  CS    10x/0.40 DRY ",
            "magnification": 10.0,
            "numericalAperture": 0.4,
            "immersion": "DRY",
            "objectiveNumber": 506511,
            "isMotCorr": True,
            "motCorrPos": 0.0,
        }

    def test_another_slot_is_another_objective(self, tree):
        tree.live[("Microscope", "SlotPosition")] = 1
        objective = direct_reader.get_objective()
        assert (objective["slotIndex"], objective["magnification"]) == (1, 20.0)

    def test_an_empty_slot_is_refused(self, tree):
        tree.live[("Microscope", "SlotPosition")] = 5
        with pytest.raises(hardware_server.HardwareServerUnavailable, match="slot 5"):
            direct_reader.get_objective()

    def test_the_field_of_view_in_metres_as_the_api_reader_gives_it(self, tree):
        assert direct_reader.get_fov() == pytest.approx((0.0011625, 0.0011625))

    def test_no_z_stack(self, tree):
        assert direct_reader.get_z_stack() is None

    def test_a_z_stack_in_micrometres(self, tree):
        stack = ("Acquire", "AcquireZStackInfo")
        tree.live.update(
            {
                (*stack, "BeginValue"): -10e-6,
                (*stack, "EndValue"): 10e-6,
                (*stack, "SectionsCount"): 21,
                (*stack, "StepSize"): 1e-6,
                ("Acquire", "ZStackUseMode"): 2,
            }
        )
        stack = direct_reader.get_z_stack()
        assert stack.pop("mode") == "z-wide"
        assert stack == pytest.approx(
            {"begin": -10.0, "end": 10.0, "stepSize": 1.0, "size": 20.0, "sections": 21}
        )


class TestLightAndScanning:
    """Shaped as the log reader's job settings give them; simulator values, 2026-10-13."""

    def test_the_laser_lines_that_are_on(self, tree):
        assert direct_reader.get_laser_lines() == [
            {
                "lightSource": "UV",
                "laserName": "Laser 405",
                "beamRoute": "30",
                "lineIndex": 0,
                "wavelength": 405.0,
                "intensity": pytest.approx(0.0196, abs=1e-4),
                "shutterOpen": True,
            },
            {
                "lightSource": "SuperContVisible Light",
                "laserName": "WLL",
                "beamRoute": "10;10",
                "lineIndex": 0,
                "wavelength": 497.0,
                "intensity": pytest.approx(0.0200, abs=1e-4),
                "shutterOpen": True,
            },
        ]

    def test_a_line_turned_down_to_nothing_is_off(self, tree):
        tree.light_sources = tree.light_sources[:1]
        tree.light_sources[0]["AOTF"]["AotfLine"][0]["Intensity"] = 0.0
        assert direct_reader.get_laser_lines() == []

    def test_scan_field_rotation(self, tree):
        assert direct_reader.get_scan_field_rotation() == {
            "value": 0.0,
            "min": -100.0,
            "max": 100.0,
        }

    def test_sequential_mode(self, tree):
        assert direct_reader.get_sequential_mode() == "Line"

    def test_a_sequential_mode_not_yet_seen_is_named_so(self, tree):
        tree.live[("Confocal", "ScanningParameter", "SequentialMode")] = 2
        assert direct_reader.get_sequential_mode() == "unknown (2)"

    def test_no_sequential_scanning(self, tree):
        tree.live[("Confocal", "ScanningParameter", "IsSequentialEnabled")] = False
        assert direct_reader.get_sequential_mode() is None

    def test_pixel_size_in_metres(self, tree):
        assert direct_reader.get_pixel_size() == pytest.approx(
            (2.2705e-6, 2.2705e-6), rel=1e-4
        )

    def test_bit_depth(self, tree):
        assert direct_reader.get_bit_depth() == 8


class TestTheInstrument:
    """Shaped as the log reader's hardware info gives it; simulator values, 2026-10-13."""

    def test_serial_number(self, tree):
        assert direct_reader.get_serial_number() == "STELLARIS SIMULATOR"

    def test_microscope_stand(self, tree):
        assert direct_reader.get_microscope_stand() == "DM Manual-6"

    def test_every_objective_by_slot(self, tree):
        objectives = direct_reader.get_objectives()
        assert [
            (o["slotIndex"], o["magnification"], o["objectiveNumber"])
            for o in objectives
        ] == [
            (0, 10.0, 506511),
            (1, 20.0, 506513),
        ]

    def test_scan_speed_range(self, tree):
        assert direct_reader.get_scan_speed_range() == {
            "Min": 1,
            "Max": 2600,
            "ResonantSpeed": 12000,
        }

    def test_stage_limits_in_metres(self, tree):
        assert direct_reader.get_stage_limits() == {
            "x_min": 0.0,
            "x_max": 0.127,
            "y_min": 0.0,
            "y_max": 0.083,
        }


class TestDetectionAndFilters:
    """Shaped as the log reader gives them; simulator values, 2026-10-13."""

    def test_the_detectors_in_use(self, tree):
        assert direct_reader.get_detectors() == [
            {
                "name": "HyD 2",
                "beamRoute": "40;2",
                "detectionBegin": 410.0,
                "detectionEnd": 487.0,
                "detectionUnit": "nm",
                "dye": "Leica/ALEXA 405",
                "gain": {"value": 2.5, "min": 2.5, "max": 500.0},
                "offset": 0.0,
            },
            {
                "name": "HyD S 3",
                "beamRoute": "40;3",
                "detectionBegin": 502.0,
                "detectionEnd": 779.0,
                "detectionUnit": "nm",
                "dye": "Leica/ALEXA 488",
                "gain": {"value": 2.5, "min": 2.5, "max": 500.0},
                "offset": 0.0,
            },
        ]

    def test_the_installed_detectors_as_the_hardware_info_names_them(self, tree):
        assert direct_reader.get_detection_units() == [
            {
                "Name": "HyD 2",
                "BeamRoute": "40;2",
                "Type": "HyD",
                "CanDoPhotonCounting": True,
                "MinDetectionBandpassWidth": 5.0,
            },
            {
                "Name": "HyD S 3",
                "BeamRoute": "40;3",
                "Type": "SiPM",
                "CanDoPhotonCounting": True,
                "MinDetectionBandpassWidth": 5.0,
            },
            {
                "Name": "Trans PMT",
                "BeamRoute": "10;5",
                "Type": "PMT",
                "CanDoPhotonCounting": False,
                "MinDetectionBandpassWidth": 5.0,
            },
        ]

    def test_a_detector_type_not_yet_seen_is_named_by_its_code(self, tree):
        tree.detection["Spectrophotometer"][1]["DetectionUnitList"]["DetectorHWInfo"][
            0
        ]["Type"] = 9
        assert direct_reader.get_detection_units()[-1]["Type"] == "type 9"

    def test_every_filter_wheel_and_the_multi_function_port(self, tree):
        assert direct_reader.get_filter_wheels() == [
            {
                "type": 200,
                "filterIndex": 0,
                "filterName": "Galvo X Pan Center ",
                "filterNames": ["Galvo X Pan Center ", "Galvo X Pan Mirror "],
            },
            {
                "type": 140,
                "filterIndex": 2,
                "filterName": "Mirror ",
                "filterNames": ["--- ", "Substrate ", "Mirror "],
            },
            {
                "type": 130,
                "filterIndex": 0,
                "filterName": "Empty",
                "filterNames": ["Empty", "NF 448/514/561"],
            },
            {
                "type": 180,
                "filterIndex": 1,
                "filterName": "Substrate ",
                "filterNames": ["--- ", "Substrate ", "SD 470", "DD 470/640", "--- "],
            },
        ]

    def test_the_installed_light_sources_as_the_hardware_info_names_them(self, tree):
        sources = direct_reader.get_light_sources()
        assert [(s["name"], s["BeamRoute"]) for s in sources] == [
            ("UV", "30"),
            ("Visible Light", "10;30"),
            ("SuperContVisible Light", "10;10"),
        ]
        assert sources[1]["LaserLines"] == [
            {
                "LaserName": "Laser 488",
                "Index": 0,
                "MinWavelength": 488.0,
                "MaxWavelength": 488.0,
            },
            {
                "LaserName": "Laser 638",
                "Index": 1,
                "MinWavelength": 638.0,
                "MaxWavelength": 638.0,
            },
            {
                "LaserName": "Laser 730",
                "Index": 2,
                "MinWavelength": 730.0,
                "MaxWavelength": 730.0,
            },
        ]

    def test_a_tunable_laser_gives_each_of_its_lines(self, tree):
        lines = direct_reader.get_light_sources()[2]["LaserLines"]
        assert lines == [
            {
                "LaserName": "WLL",
                "Index": 0,
                "MinWavelength": 485.0,
                "MaxWavelength": 685.0,
            },
            {
                "LaserName": "WLL",
                "Index": 1,
                "MinWavelength": 485.0,
                "MaxWavelength": 685.0,
            },
        ]


class TestTheRest:
    def test_the_field_of_view_at_zoom_one(self, tree):
        tree.live.update(
            {
                ("Confocal", "ScanningParameter", "Zoom"): 2.0,
                ("Acquire", "AcquireImageSizeInfo", "ImageSizeX"): 0.0011625 / 2,
                ("Acquire", "AcquireImageSizeInfo", "ImageSizeY"): 0.0011625 / 2,
            }
        )
        assert direct_reader.get_base_fov() == pytest.approx((0.0011625, 0.0011625))

    def test_afc_installed(self, tree):
        assert direct_reader.get_afc_installed() is False

    def test_ping(self, tree):
        assert direct_reader.ping() is True

    def test_ping_without_the_hardware_server(self):
        assert direct_reader.ping() is False

    def test_the_active_setting(self, tree):
        settings = direct_reader.get_active_settings()
        assert len(settings) == 1
        setting = settings[0]
        assert setting["activeLaserLines"] == direct_reader.get_laser_lines()
        assert setting["activeDetectors"] == direct_reader.get_detectors()
        assert setting["filterWheels"] == direct_reader.get_filter_wheels()
        assert setting["pinholeAiry"] == direct_reader.get_pinhole()["airy"]
        assert {
            k: setting[k] for k in direct_reader.get_averaging()
        } == direct_reader.get_averaging()

    def test_several_sequential_settings_are_not_read_yet(self, tree):
        tree.live[("Confocal", "ScanningParameter", "LineAverageSequentialList")] = (
            1,
            2,
        )
        with pytest.raises(
            hardware_server.HardwareServerUnavailable, match="2 sequential settings"
        ):
            direct_reader.get_active_settings()


class TestTheJobSettings:
    """A job's settings, whole: LAS X's answer, the same JSON the CAM API gives.

    Asked of the hardware server with ``ATL_GetBlockApiInfoAsJsonString`` and
    the job's block id, they are the CAM API's settings for every job, the
    selected one or not, with each filter wheel's spectrum mode (simulator,
    2026-10-14: identical, value for value, for all three jobs). For the
    selected job the z drives are where they truly are, from the state tree:
    the settings keep a drive's last set position, which freezes for the
    drive carrying a z-stack.
    """

    @pytest.mark.parametrize("job", ["HiRes", "Overview", "AF Job"])
    def test_every_job_is_the_api_readers_answer(self, client, tree, job):
        client._selected_job = "Overview" if job == "HiRes" else "HiRes"
        assert direct_reader.get_job_settings(job) == api_reader.get_job_settings(
            client, job
        )

    def test_the_driver_reads_them_as_it_reads_the_api_readers(self, client, tree):
        settings = direct_reader.get_job_settings("HiRes")
        assert capabilities.job_settings_complete(settings, job_name="HiRes")
        flat = make_changeable_copy(settings)
        assert flat["zoom"]["current"] == client._jobs["HiRes"]["zoom"]["current"]

    def test_the_selected_jobs_z_drives_where_they_truly_are(self, client, tree):
        client._hardware_z_um.update({"z-galvo": -2.5, "z-wide": 4999.5})
        flat = make_changeable_copy(direct_reader.get_job_settings("HiRes"))
        assert flat["zPosition"] == pytest.approx({"z-galvo": -2.5, "z-wide": 4999.5})

    def test_another_jobs_z_is_its_own(self, client, tree):
        client._hardware_z_um.update({"z-galvo": -2.5, "z-wide": 4999.5})
        assert (
            direct_reader.get_job_settings("Overview")["zPosition"]
            == api_reader.get_job_settings(client, "Overview")["zPosition"]
        )

    def test_a_wheel_in_spectrum_mode_says_so(self, client, tree):
        wheel = {
            "beamRoute": "",
            "isSpectrumMode": True,
            "name": "UV Lens Wheel",
            "spectrumPosition": 10.0,
            "type": 30,
        }
        client._jobs["HiRes"]["activeSettings"][0]["filterWheels"] = [wheel]
        assert direct_reader.get_job_settings("HiRes")["activeSettings"][0][
            "filterWheels"
        ] == [wheel]

    def test_a_job_not_in_las_x_raises(self, client, tree):
        with pytest.raises(
            hardware_server.HardwareServerUnavailable, match="No Such Job"
        ):
            direct_reader.get_job_settings("No Such Job")

    def test_a_refused_read_raises(self, client, tree):
        tree.jobs = lambda: {
            **JOBS,
            "Ghost": (999, 4),
        }  # in the sequence, unknown to the server
        with pytest.raises(
            hardware_server.HardwareServerUnavailable, match="invalid block identifier"
        ):
            direct_reader.get_job_settings("Ghost")


class TestTheHardwareInfo:
    """The instrument, whole: the hardware server's answer to ``GetConfocalHardwareInfoAsJson``,
    the same JSON the CAM API gives (simulator, 2026-10-14: identical, ``SystemType`` included)."""

    def test_it_is_the_api_readers_answer(self, client, tree):
        assert direct_reader.get_hardware_info() == api_reader.get_hardware_info(client)

    def test_the_turret_as_the_driver_reads_it(self, client, tree):
        assert sorted(objective_by_slot(direct_reader.get_hardware_info())) == sorted(
            objective_by_slot(api_reader.get_hardware_info(client))
        )


class TestWhatIsReadOnce:
    """What LAS X fixes for a session is read once, with the layout; what can change, every time."""

    def test_the_installed_hardware_is_read_once(self, tree):
        first = direct_reader.get_objectives()
        reads = tree.reads
        assert direct_reader.get_objectives() == first
        assert tree.reads == reads

    def test_what_changes_is_read_every_time(self, client, tree):
        direct_reader.get_active_settings()
        detector = tree.detection["Spectrophotometer"][0]["DetectionUnitList"][
            "DetectorHWInfo"
        ][0]
        detector.update(
            {"HighVoltage": 7.5, "WaveLengthEnd": 470.0, "DyeName": "Leica/DAPI"}
        )
        tree.light_sources[0]["AOTF"]["AotfLine"][0]["Intensity"] = 5.0
        tree.filter_wheels["FilterWheel"][1]["CurrentSlotPosition"] = 1
        tree.live[("Microscope", "SlotPosition")] = 1
        settings = direct_reader.get_active_settings()[0]
        hyd = settings["activeDetectors"][0]
        assert (hyd["gain"]["value"], hyd["detectionEnd"], hyd["dye"]) == (
            7.5,
            470.0,
            "Leica/DAPI",
        )
        assert settings["activeLaserLines"][0]["intensity"] == pytest.approx(0.05)
        assert {
            "type": 140,
            "filterIndex": 1,
            "filterName": "Substrate ",
        }.items() <= next(
            w for w in settings["filterWheels"] if w["type"] == 140
        ).items()
        assert direct_reader.get_objective()["magnification"] == 20.0

    def test_a_detector_switched_on_is_seen(self, tree):
        assert [d["name"] for d in direct_reader.get_detectors()] == [
            "HyD 2",
            "HyD S 3",
        ]
        tree.detection["Spectrophotometer"][1]["DetectionUnitList"]["DetectorHWInfo"][
            0
        ]["IsActive"] = True
        assert [d["name"] for d in direct_reader.get_detectors()] == [
            "HyD 2",
            "HyD S 3",
            "Trans PMT",
        ]

    def test_a_changed_layout_is_read_afresh(self, tree):
        assert len(direct_reader.get_light_sources()) == 3
        tree.light_sources = tree.light_sources[:2]
        assert [s["name"] for s in direct_reader.get_light_sources()] == [
            "UV",
            "Visible Light",
        ]

    def test_a_failed_connection_forgets_what_was_read_once(self, tree):
        direct_reader.get_objectives()
        state_tree.forget_nodes()
        reads = tree.reads
        direct_reader.get_objectives()
        assert tree.reads > reads


class TestWhatItReads:
    def test_only_what_it_was_agreed_to_read(self):
        assert direct_reader.READS == (
            "xy",
            "z",
            "selected_job",
            "jobs",
            "scan_status",
            "scan_mode",
            "zoom",
            "scan_speed",
            "format",
            "averaging",
            "pinhole",
            "objective",
            "fov",
            "z_stack",
            "laser_lines",
            "scan_field_rotation",
            "sequential_mode",
            "pixel_size",
            "bit_depth",
            "serial_number",
            "microscope_stand",
            "objectives",
            "scan_speed_range",
            "stage_limits",
            "detectors",
            "detection_units",
            "filter_wheels",
            "light_sources",
            "base_fov",
            "afc_installed",
            "active_settings",
            "job_settings",
            "hardware_info",
        )

    def test_without_the_hardware_server_it_raises(self):
        # The suite's conftest makes the hardware server unreachable.
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_xy()
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_selected_job()
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.read_z_um("z-wide")
        with pytest.raises(hardware_server.HardwareServerUnavailable):
            direct_reader.get_jobs()

    def test_it_never_writes(self, client, tree):
        direct_reader.get_xy()
        direct_reader.get_selected_job()
        direct_reader.read_z_um("z-galvo")
        assert not hasattr(tree, "ChangeNodes")
        assert tree.reads > 0
