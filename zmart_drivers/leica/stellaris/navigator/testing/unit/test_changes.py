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

    def test_a_tile_scan_with_a_position_outside_the_limits_sends_nothing(self, lasx, client):
        with pytest.raises(RuntimeError, match="backstop"):
            changes.take_tile_scan(client, [(63500.0, 41500.0, 0.0), (500_000.0, 41500.0, 0.0)])

        assert lasx.commands() == []
