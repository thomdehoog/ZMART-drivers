"""The named changes through the dispatcher, against the stand-in for LAS X's plain Navigator.

Every change goes the way a workflow's does: the limits gate first, then the
elements to the hardware server, then the read-back from the state tree. The
stand-in applies the elements as LAS X does, so what is checked is the whole
chain: a setting confirmed, a move refused by the limits before anything is
sent, a z move sent as a step and confirmed where it lands, an objective
change, a capture watched to the end, and a tile scan with its put-back.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest
from lasx_stand_in import PlainNavigator, attach
from limits_fixtures import install_permissive_limits

from zmart_drivers.leica.stellaris.navigator.dispatcher import limits as gate
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    reads as direct_reader,
)
from zmart_drivers.leica.stellaris.navigator.procedures import changes
from zmart_drivers.leica.stellaris.navigator.procedures.clear_cache import (
    data_container,
)


@pytest.fixture
def lasx(monkeypatch):
    return attach(monkeypatch, PlainNavigator())


@pytest.fixture
def client(lasx):
    return install_permissive_limits(hardware_server.Client("test"), wide_stage=True)


SCANNING = ("Confocal", "ScanningParameter")


class TestSettings:
    def test_a_setting_is_sent_once_and_confirmed_by_the_read_back(self, lasx, client):
        result = changes.change(client, "zoom", 2.5)

        assert result["success"] and result["confirmed"] is True
        assert result["route"] == "direct"
        assert lasx.commands() == ["SetZoom"]
        assert lasx.live[(*SCANNING, "Zoom")] == 2.5
        assert direct_reader.get_zoom()["current"] == 2.5

    @pytest.mark.parametrize(
        ("name", "value", "path"),
        [
            ("scan_speed", 600, (*SCANNING, "Speed")),
            ("scan_field_rotation", 5.0, ("Confocal", "HWParameter", "ScanRotationAngle")),
            ("line_average", 2, (*SCANNING, "LineAverage")),
            ("pinhole_airy", 1.5, ("Confocal", "HWParameter", "PinholeAU")),
        ],
    )
    def test_each_of_the_five_settings_lands_in_the_state_tree(self, lasx, client, name, value, path):
        result = changes.change(client, name, value)

        assert result["confirmed"] is True, result["message"]
        assert lasx.live[path] == pytest.approx(value)

    def test_a_value_las_x_refuses_fails_at_once_with_its_reason(self, lasx, client):
        result = changes.change(client, "zoom", 100)

        assert result["success"] is False
        assert "out of range" in result["message"]
        assert lasx.commands() == ["SetZoom"], "a permanent refusal is not sent again"

    def test_an_adjusted_value_is_accepted_and_the_read_back_decides(self, lasx, client):
        result = changes.change(client, "pinhole_airy", 100)

        assert result["success"] is True
        assert result["confirmed"] is False, "LAS X set the pinhole to its maximum, not to 100"

    def test_a_setting_the_limits_refuse_is_never_sent(self, lasx):
        client = install_permissive_limits(hardware_server.Client("test"), wide_stage=True)
        gate.install(
            client,
            gate.GateState(
                limits=gate.state_of(client).limits.__class__(
                    {**{k: [] for k in ("x_um", "y_um", "z_galvo_um", "z_wide_um", "objective_slot")},
                     "set_zoom": {"range": [1, 4]}, "set_scan_speed": [], "set_scan_field_rotation": [],
                     "set_line_average": [], "set_pinhole_airy": []},
                    source="test", path=None, is_fallback=False,
                ),
                stage_cfg=None,
                error=None,
            ),
        )

        result = changes.change(client, "zoom", 8)

        assert result["success"] is False and "refused" in result["message"]
        assert lasx.commands() == []

    def test_an_unknown_setting_raises(self, client):
        with pytest.raises(ValueError, match="laser"):
            changes.change(client, "laser", 1)


class TestMoves:
    def test_the_stage_moves_and_is_read_back(self, lasx, client):
        result = changes.move_xy(client, 63600.0, 41400.0)

        assert result["confirmed"] is True
        assert result["position"] == {"x_um": 63600.0, "y_um": 41400.0, "x": 0.0636, "y": 0.0414}
        assert (lasx.x_um, lasx.y_um) == (pytest.approx(63600.0), pytest.approx(41400.0))

    def test_a_move_outside_the_limits_is_refused_before_anything_is_sent(self, lasx, client):
        result = changes.move_xy(client, 500_000.0, 10.0)

        assert result["success"] is False and "backstop" in result["message"]
        assert result["position"] is None
        assert lasx.commands() == []

    def test_a_z_move_is_sent_as_a_step_from_where_the_drive_stands(self, lasx, client):
        lasx.z_um[2] = 100.0

        result = changes.move_z(client, "z-wide", 102.0)

        assert result["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(102.0)
        assert 'PosZ="0.0000020000" RelativeCoordinates="1" ZUseMode="2"' in lasx.sent[-1]

    def test_a_z_move_the_limits_refuse_is_never_sent(self, lasx, client):
        result = changes.move_z(client, "z-galvo", 500.0)

        assert result["success"] is False and "outside galvo limits" in result["message"]
        assert lasx.commands() == []

    def test_a_z_move_that_only_half_happened_is_sent_again_with_the_rest(self, lasx, client, monkeypatch):
        """LAS X's plain Navigator moves z by a step; a second send must not add the whole step again."""
        halves = {"left": 1}

        def half_step(self, e):
            drive, step_um = int(e.get("ZUseMode")), float(e.get("PosZ")) * 1e6
            if halves["left"]:
                halves["left"] -= 1
                step_um /= 2
            self.z_um[drive] += step_um

        monkeypatch.setattr(PlainNavigator, "take_SetZPosition", half_step)

        result = changes.move_z(client, "z-galvo", 4.0, tolerance=0.1)

        assert result["confirmed"] is True
        assert lasx.z_um[1] == pytest.approx(4.0)
        assert ['PosZ="0.0000040000"' in x for x in lasx.sent if "SetZPosition" in x] == [True, False]


class TestTheObjective:
    def objectives(self):
        return direct_reader.get_objectives()

    def test_the_objective_turns_by_the_window_s_own_command_and_is_read_back(self, lasx, client):
        result = changes.set_objective(client, self.objectives(), slot_index=1)

        assert result["confirmed"] is True
        assert lasx.live[("Microscope", "SlotPosition")] == 1
        assert lasx.commands() == ["SetSlotPosition"]

    def test_the_objective_is_found_by_name_or_magnification(self, lasx, client):
        assert changes.find_objective(self.objectives(), magnification=20.0)[0] == 1
        assert changes.find_objective(self.objectives(), name="HC PL APO CS  20.0x0.70 UV")[0] == 1
        assert changes.find_objective(self.objectives(), slot_index=5) == (None, None)

    def test_exactly_one_way_of_choosing_is_taken(self, lasx, client):
        result = changes.set_objective(client, self.objectives(), slot_index=1, magnification=20.0)
        assert result["success"] is False and "exactly one" in result["message"]
        assert lasx.commands() == []


class TestPictures:
    @pytest.fixture(autouse=True)
    def project(self, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)

    def test_a_capture_is_sent_once_and_watched_until_the_scan_is_over(self, lasx, client):
        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "capture"
        assert acq.command_result["confirmed"] is True
        assert lasx.commands() == ["StopAcquiring", "CaptureSingleFrame"]
        assert 'DCParentElementID="2518"' in lasx.sent[-1]
        assert lasx.captures == 1

    def test_a_capture_whose_scan_never_starts_fails(self, lasx, client, monkeypatch):
        monkeypatch.setattr(PlainNavigator, "take_CaptureSingleFrame", lambda self, e: None)

        with pytest.raises(RuntimeError, match="never started"):
            changes.take_picture(client, start_timeout=0.2, poll_interval=0.01)

    def test_a_capture_las_x_refuses_fails_with_its_reason_and_is_not_sent_again(self, lasx, client):
        lasx.answers["CaptureSingleFrame"] = '<Result Error="1">Invalid or deleted element ID used</Result>'

        with pytest.raises(RuntimeError, match="deleted element"):
            changes.take_picture(client, poll_interval=0.01)

        assert lasx.commands().count("CaptureSingleFrame") == 1

    def test_a_capture_without_a_project_sends_nothing(self, lasx, client, monkeypatch):
        def no_project():
            raise RuntimeError("LAS X's Data Container did not give a project for the image")

        monkeypatch.setattr(data_container, "experiment_for_capture", no_project)

        result = changes.capture(client)

        assert result["success"] is False and "project" in result["message"]
        assert lasx.commands() == []

    def test_a_tile_scan_runs_and_puts_the_stage_back_at_the_first_field(self, lasx, client):
        positions = [(63500.0, 41500.0, 0.0), (64139.0, 41500.0, 0.0), (64778.0, 41500.0, 0.0)]

        acq = changes.take_tile_scan(client, positions, poll_interval=0.01)

        assert acq.picture == "tile_scan"
        assert acq.command_result["confirmed"] is True
        sent = lasx.commands()
        assert sent[:9] == [
            "StopAcquiring", "SetStageUseMode", "SetStageIrregularTileScanPositions", "ATL_SuppressAtlasAttachment",
            "ScanMode", "ActivateAutoFocus", "SetStageUseMode", "SetStageIrregularTileScanPositions", "StartAcquiring",
        ]
        assert sent[9:] == ["StageMoveToCoordinates", "SetStageUseMode", "ATL_SuppressAtlasAttachment", "ScanMode", "ActivateAutoFocus"]
        assert acq.command_result["put_back"]["confirmed"] is True
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyz"

    def test_a_tile_scan_whose_scan_never_starts_still_puts_everything_back(self, lasx, client, monkeypatch):
        # As on the LAS X simulator on 2026-10-30: the preparation is taken (the
        # scan mode is xyt), then LAS X cannot start the scan, because no channel
        # is active. What the preparation changed must not be left behind.
        monkeypatch.setattr(PlainNavigator, "take_StartAcquiring", lambda self, e: None)
        positions = [(63500.0, 41500.0, 0.0), (64139.0, 41500.0, 0.0)]

        with pytest.raises(RuntimeError, match="never started"):
            changes.take_tile_scan(client, positions, start_timeout=0.2, poll_interval=0.01)

        assert lasx.commands()[-5:] == ["StageMoveToCoordinates", "SetStageUseMode", "ATL_SuppressAtlasAttachment", "ScanMode", "ActivateAutoFocus"]
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyz"

    def test_a_tile_scan_leaves_z_wide_where_it_is_when_the_z_stack_is_on_z_wide(self, lasx, client):
        # On the simulator (2026-11-01) LCS read a tile's ZPos as the position of the
        # z-stack's drive: with the stack on z-wide, z-wide went from 1522.7 µm to 0.
        lasx.live[("Acquire", "ZPositionMode")] = 2
        lasx.z_um = {1: 0.0, 2: 1522.7}
        positions = [(63500.0, 41500.0, 0.0), (64800.0, 41500.0, 0.0)]

        changes.take_tile_scan(client, positions, poll_interval=0.01)

        assert lasx.z_um[2] == pytest.approx(1522.7)
        tiles = next(x for x in lasx.sent if "SetStageIrregularTileScanPositions" in x)
        assert tiles.count('ZPos="0.0015227000"') == 2
        assert 'PosZ="0.0015227000"' in next(x for x in lasx.sent if "StageMoveToCoordinates" in x)

    def test_a_tile_scan_keeps_z_galvo_s_position_when_the_z_stack_is_on_z_galvo(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 1
        lasx.z_um = {1: 2.0, 2: 1522.7}

        changes.take_tile_scan(client, [(63500.0, 41500.0, 2.0)], poll_interval=0.01)

        assert 'ZPos="0.0000020000"' in next(x for x in lasx.sent if "SetStageIrregularTileScanPositions" in x)
        assert lasx.z_um == {1: pytest.approx(2.0), 2: pytest.approx(1522.7)}

    def test_a_tile_scan_at_another_z_galvo_position_is_refused_when_the_z_stack_is_on_z_wide(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 2
        lasx.z_um = {1: 0.0, 2: 1522.7}

        with pytest.raises(RuntimeError, match="z-wide"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 5.0)], poll_interval=0.01)

        assert lasx.commands() == []

    def test_a_tile_scan_is_refused_when_the_z_stack_s_drive_cannot_be_read(self, lasx, client):
        lasx.live[("Acquire", "ZPositionMode")] = 7

        with pytest.raises(RuntimeError, match="drive"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 0.0)], poll_interval=0.01)

        assert lasx.commands() == []

    def test_a_tile_scan_with_a_position_outside_the_limits_sends_nothing(self, lasx, client):
        with pytest.raises(RuntimeError, match="backstop"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 0.0), (500_000.0, 41500.0, 0.0)])

        assert lasx.commands() == []


class TestTheLargestStepOnTheWay:
    """The changes hand the gate where each drive stands, so the step is checked before anything is sent."""

    @pytest.fixture
    def stepped(self, lasx):
        from limits_fixtures import DEFAULT_STAGE_UM, merged_limits_payload

        from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules

        client = hardware_server.Client("test")
        payload = merged_limits_payload(dict(DEFAULT_STAGE_UM))
        gate.install(client, gate.GateState(
            limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False),
            stage_cfg=None, error=None))
        limit_rules.set_stage_limits(
            x_min=1000.0, x_max=130000.0, y_min=1000.0, y_max=100000.0,
            z_galvo_min=-250.0, z_galvo_max=250.0, z_wide_min=0.0, z_wide_max=8000.0,
        )
        yield client
        gate.uninstall(client)

    def test_a_large_z_wide_move_is_refused_and_nothing_is_sent(self, lasx, stepped):
        result = changes.move_z(stepped, "z-wide", 600.0)

        assert result["success"] is False and "step_z_wide_um" in result["message"]
        assert lasx.commands() == []

    def test_a_small_z_wide_move_goes_through(self, lasx, stepped):
        result = changes.move_z(stepped, "z-wide", 150.0, tolerance=0.1)

        assert result["confirmed"] is True
        assert lasx.z_um[2] == pytest.approx(150.0)

    def test_a_stage_move_hands_the_gate_where_the_stage_stands(self, lasx, stepped):
        assert changes.move_xy(stepped, 64000.0, 41500.0, tolerance=1)["confirmed"] is True



class TestTheZStack:
    def test_the_stack_is_set_drive_range_and_step_and_read_back(self, lasx, client):
        lasx.z_um = {1: 0.0, 2: 1000.0}

        result = changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        assert result["confirmed"] is True
        assert result["z_stack"] == {
            "begin": pytest.approx(995.0), "end": pytest.approx(1005.0), "stepSize": pytest.approx(2.5),
            "size": pytest.approx(10.0), "sections": 5, "mode": "z-wide",
        }
        assert lasx.commands() == ["SetZUseMode", "SetZStackDefinition", "SetStackStepSize"]

    def test_with_a_stack_set_a_picture_takes_the_whole_stack(self, lasx, client, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)
        lasx.z_um = {1: 0.0, 2: 1000.0}
        changes.set_z_stack(client, "z-wide", 995.0, 1005.0, 2.5)

        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "z_stack"
        assert lasx.commands()[-2:] == ["StopAcquiring", "StartAcquiring"]

    def test_without_a_stack_a_picture_is_one_frame(self, lasx, client, monkeypatch):
        monkeypatch.setattr(data_container, "experiment_for_capture", lambda: 2518)

        acq = changes.take_picture(client, poll_interval=0.01)

        assert acq.picture == "capture"
        assert lasx.commands()[-1] == "CaptureSingleFrame"



class TestScanModeAndAutofocus:
    def test_the_scan_mode_is_changed_and_read_back(self, lasx, client):
        result = changes.change(client, "scan_mode", "xyt")

        assert result["confirmed"] is True
        assert lasx.live[("Acquire", "ScanDimensionList", "ScanModeName")] == "xyt"

    def test_autofocus_is_switched_off_and_read_back(self, lasx, client):
        result = changes.change(client, "autofocus", False)

        assert result["confirmed"] is True
        assert lasx.live[("Acquire", "AutoFocusInfo", "IsSeriesScanAutofocusActive")] is False



class TestLasers:
    def test_a_line_not_in_use_is_found_by_its_wavelength_switched_on_and_read_back(self, lasx, client):
        result = changes.set_laser_power(client, 488.0, 5.0)

        assert result["confirmed"] is True
        assert '<BeamPosition BeamPositionLevel="1" BeamPosition="30"/>' in lasx.sent[-1]
        lines = {line["wavelength"]: line for line in direct_reader.get_laser_lines()}
        assert lines[488.0]["intensity"] == pytest.approx(0.05098, abs=0.001)

    def test_zero_switches_the_line_off(self, lasx, client):
        changes.set_laser_power(client, 488.0, 5.0)

        assert changes.set_laser_power(client, 488.0, 0.0)["confirmed"] is True
        assert 488.0 not in {line["wavelength"] for line in direct_reader.get_laser_lines()}

    def test_a_wavelength_no_laser_has_is_refused_and_nothing_is_sent(self, lasx, client):
        result = changes.set_laser_power(client, 500.0, 5.0)

        assert result["success"] is False and "500" in result["message"]
        assert lasx.commands() == []

    def test_a_line_s_shutter_is_closed_and_read_back(self, lasx, client):
        result = changes.set_laser_shutter(client, 405.0, False)

        assert result["confirmed"] is True
        assert direct_reader.get_laser_lines()[0]["shutterOpen"] is False

    def test_a_power_above_the_limit_is_refused_before_anything_is_sent(self, lasx):
        from limits_fixtures import DEFAULT_STAGE_UM, merged_limits_payload

        from zmart_drivers.leica.stellaris.navigator.driver_setup.limits import limit_rules

        limited = hardware_server.Client("test")
        payload = merged_limits_payload(dict(DEFAULT_STAGE_UM))
        gate.install(limited, gate.GateState(
            limits=limit_rules.LeicaLimits(payload, source="test", path=None, is_fallback=False),
            stage_cfg=None, error=None))
        try:
            result = changes.set_laser_power(limited, 405.0, 50.0)
        finally:
            gate.uninstall(limited)

        assert result["success"] is False and "set_laser_power" in result["message"]
        assert lasx.commands() == []



class TestTheAcquisitionSettings:
    @pytest.mark.parametrize(
        ("name", "node"),
        [("frame_average", "FrameAverage"), ("line_accumulation", "LineAccu"), ("frame_accumulation", "FrameAccumulation")],
    )
    def test_averaging_and_accumulation_are_changed_and_read_back(self, lasx, client, name, node):
        assert changes.change(client, name, 2)["confirmed"] is True
        assert lasx.live[(*SCANNING, node)] == 2

    def test_the_format_is_changed_and_read_back(self, lasx, client):
        assert changes.change(client, "format", (256, 256))["confirmed"] is True
        assert lasx.live[(*SCANNING, "CurrentFormat")] == "256 x 256"

    def test_the_resonant_scanner_is_switched_and_read_back(self, lasx, client):
        assert changes.change(client, "resonant_scanner", True)["confirmed"] is True
        assert lasx.live[(*SCANNING, "IsResonantConfocalScanner")] is True

    def test_a_filter_wheel_is_turned_to_a_filter_by_its_name(self, lasx, client):
        result = changes.set_filter_wheel(client, 140, "Substrate")

        assert result["confirmed"] is True
        assert direct_reader.get_filter_wheels()[1]["filterName"].strip() == "Substrate"

    def test_lasx_s_own_galvo_and_scanner_wheels_are_never_turned(self, lasx, client):
        result = changes.set_filter_wheel(client, 200, "Galvo X Pan Mirror")

        assert result["success"] is False and "200" in result["message"]
        assert lasx.commands() == []

    def test_a_filter_the_wheel_does_not_hold_is_refused(self, lasx, client):
        result = changes.set_filter_wheel(client, 140, "Polarizer")

        assert result["success"] is False and "Polarizer" in result["message"]
        assert lasx.commands() == []



class TestChannelColour:
    def test_a_detector_s_colour_is_set_and_read_back(self, lasx, client):
        result = changes.set_channel_colour(client, "HyD S 3", "Green")

        assert result["confirmed"] is True
        assert direct_reader.get_channel_colours()[3] == "Green"

    def test_a_detector_outside_the_spectral_detection_is_refused(self, lasx, client):
        result = changes.set_channel_colour(client, "Trans PMT", "Green")

        assert result["success"] is False and "Trans PMT" in result["message"]
        assert lasx.commands() == []



class TestBidirectional:
    def test_bidirectional_is_switched_in_the_state_tree_and_read_back(self, lasx, client):
        assert changes.change(client, "bidirectional", True)["confirmed"] is True
        assert lasx.live[(*SCANNING, "ScanDirectionX")] == 2
        assert changes.change(client, "bidirectional", False)["confirmed"] is True
        assert lasx.live[(*SCANNING, "ScanDirectionX")] == 1
        assert lasx.commands() == []  # no command: the value is written into the state tree



HW = ("Confocal", "HWParameter")


class TestPinholeAndPhase:
    def test_phase_x_is_written_and_read_back(self, lasx, client):
        assert changes.change(client, "phase_x", -20)["confirmed"] is True
        assert lasx.live[(*HW, "PhaseX")] == -20.0
        assert lasx.commands() == []

    def test_the_pinhole_is_written_in_metres_and_read_back(self, lasx, client):
        assert changes.change(client, "pinhole_um", 250)["confirmed"] is True
        assert lasx.live[(*HW, "Pinhole")] == pytest.approx(0.00025)

    def test_the_unit_and_the_emission_wavelength_are_written_and_read_back(self, lasx, client):
        assert changes.change(client, "pinhole_unit", "um")["confirmed"] is True
        assert lasx.live[(*HW, "PinholeUnit")] == 1
        assert changes.change(client, "emission_wavelength", 520)["confirmed"] is True
        assert lasx.live[(*HW, "EmissionWaveLengthForAiryAnddzCalculation")] == 520

    @pytest.mark.parametrize(("name", "value", "node"), [("pinhole_um", 2000, "Pinhole"), ("pinhole_um", 50, "Pinhole"), ("phase_x", 150, "PhaseX")])
    def test_a_value_outside_lcs_own_range_is_refused_and_nothing_is_written(self, lasx, client, name, value, node):
        before = lasx.live[(*HW, node)]

        result = changes.change(client, name, value)

        assert result["success"] is False and "range" in result["message"]
        assert lasx.live[(*HW, node)] == before

    def test_the_range_is_lcs_own_read_at_the_write(self, lasx, client):
        lasx.live[(*HW, "PinholeMax")] = 0.0005

        assert changes.change(client, "pinhole_um", 600)["success"] is False
        assert changes.change(client, "pinhole_um", 500)["confirmed"] is True



ZSTACK = ("Acquire", "AcquireZStackInfo")


class TestBitDepthAndOptimizedStep:
    def test_the_bit_depth_is_written_and_read_back(self, lasx, client):
        assert changes.change(client, "bit_depth", 12)["confirmed"] is True
        assert lasx.live[(*SCANNING, "Resolution")] == 12
        assert lasx.commands() == []

    def test_a_bit_depth_lcs_does_not_list_is_refused_and_nothing_is_written(self, lasx, client):
        result = changes.change(client, "bit_depth", 10)

        assert result["success"] is False and "8, 12, 16" in result["message"]
        assert lasx.live[(*SCANNING, "Resolution")] == 8

    def test_the_system_optimized_step_takes_lcs_optimal_step_and_is_switched_off_again(self, lasx, client):
        assert changes.change(client, "system_optimized_step", True)["confirmed"] is True
        assert lasx.live[(*ZSTACK, "StackCalculationMode")] == 2
        assert lasx.live[(*ZSTACK, "StepSize")] == lasx.live[(*ZSTACK, "OptimalStepSize")]
        assert changes.change(client, "system_optimized_step", False)["confirmed"] is True
        assert lasx.live[(*ZSTACK, "StackCalculationMode")] == 1



class TestCollar:
    def test_the_collar_is_moved_and_read_back(self, lasx, client):
        assert changes.change(client, "collar", 50)["confirmed"] is True
        assert lasx.live[("Microscope", "CurrentMotCorrPos")] == 50.0
        assert lasx.commands() == ["SetMotCorrPosition"]



MOTCORR = ("Microscope", "MotCorrAutomationInfo")


class TestSmartCorr:
    def test_the_range_is_written_into_the_state_tree_and_read_back(self, lasx, client):
        assert changes.change(client, "smartcorr_range", (20, 80, 5))["confirmed"] is True
        assert (lasx.live[(*MOTCORR, "AutomationBegin")], lasx.live[(*MOTCORR, "AutomationEnd")], lasx.live[(*MOTCORR, "AutomationSteps")]) == (20.0, 80.0, 5)
        assert lasx.commands() == []

    def test_smartcorr_runs_and_answers_where_the_collar_went(self, lasx, client):
        lasx.smartcorr_finds = 40.0

        result = changes.smartcorr(client, project=24)

        assert result["success"] is True and result["confirmed"] is True
        assert result["collar"]["percent"] == 40.0
        assert lasx.commands() == ["StopAcquiring", "StartMotCorrAutomation"]

    def test_the_collar_goes_to_the_objective_s_reference_as_goto_does(self, lasx, client):
        lasx.nodes.objectives[0]["MotCorrRefPos"] = 30.0

        assert changes.collar_to_reference(client)["confirmed"] is True
        assert lasx.live[("Microscope", "CurrentMotCorrPos")] == 30.0

    def test_without_a_reference_nothing_is_sent(self, lasx, client):
        result = changes.collar_to_reference(client)

        assert result["success"] is False and "reference" in result["message"]
        assert lasx.commands() == []



class TestCollarReference:
    def test_set_stores_where_the_collar_stands_for_the_objective_in_the_light_path(self, lasx, client):
        lasx.live[("Microscope", "SlotPosition")] = 1
        lasx.live[("Microscope", "CurrentMotCorrPos")] = 35.0

        assert changes.set_collar_reference(client)["confirmed"] is True
        assert lasx.nodes.objectives[1]["MotCorrRefPos"] == 35.0
        assert lasx.nodes.objectives[0]["MotCorrRefPos"] == -1.0  # the other objectives keep theirs
        assert lasx.commands() == []

    def test_reset_clears_it_and_goto_is_then_refused(self, lasx, client):
        lasx.nodes.objectives[0]["MotCorrRefPos"] = 20.0

        assert changes.reset_collar_reference(client)["confirmed"] is True
        assert lasx.nodes.objectives[0]["MotCorrRefPos"] == -1.0
        assert changes.collar_to_reference(client)["success"] is False
