"""The limits: what the stage, the focus, the objective and the optical configuration may do.

The limits are asked before every change. A microscope whose limits were
never published gets cautious defaults: x and y within NIS's own travel, and
z only 100 um either way from where it stood at connect. A published file is
checked, and cut to NIS's travel. Anything the limits cannot judge is refused.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from zmart_drivers.nikon.nis_elements_ar7.dispatcher import limits as gate
from zmart_drivers.nikon.nis_elements_ar7.dispatcher.limits import (
    LimitsError,
    LimitViolation,
)
from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import config, handshake
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits.limit_rules import (
    NikonLimits,
)
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits.measure_limits import (
    CornerRecorder,
)

TRAVEL = {"x_um": [-100.0, 100.0], "y_um": [-50.0, 50.0], "z_um": [0.0, 1000.0]}


def policy(**changes):
    payload = {
        "x_um": {"range": [-1000, 1000]},
        "y_um": {"range": [-1000, 1000]},
        "z_um": {"range": [-1000, 5000]},
        "objective_position": [],
        "optical_configuration": [],
    }
    payload.update(changes)
    return config.validate_payload(payload)


def test_a_limits_file_is_checked():
    with pytest.raises(ValueError, match="missing"):
        config.validate_payload({"x_um": {"range": [0, 1]}})
    with pytest.raises(ValueError):
        config.validate_payload({**policy(), "x_um": {"range": [5, 1]}})
    with pytest.raises(ValueError):
        config.validate_payload(
            {**policy(), "objective_position": {"allowed": ["two"]}}
        )
    assert config.validate_payload(policy())["objective_position"] == []


def test_the_file_is_cut_to_nis_own_travel():
    limits = NikonLimits(
        policy(),
        travel=TRAVEL,
        z_window=None,
        source="machine",
        path=None,
        is_fallback=False,
    )
    assert limits.stage_um()["x_um"] == [-100.0, 100.0]
    assert limits.stage_um()["z_um"] == [0.0, 1000.0]
    with pytest.raises(LimitViolation):
        limits.check_xy(150, 0)


def test_a_file_outside_nis_travel_is_refused():
    with pytest.raises(LimitsError):
        NikonLimits(
            policy(x_um={"range": [500, 600]}),
            travel=TRAVEL,
            z_window=None,
            source="machine",
            path=None,
            is_fallback=False,
        )


def test_unpublished_limits_let_z_move_only_100_um(client, stand_in):
    status = handshake.connect_handshake(client)
    assert status.ok
    limits = gate.state_of(client).limits
    assert limits.is_fallback and limits.stage_um()["z_um"] == [2900.0, 3100.0]
    with pytest.raises(LimitViolation, match="not published"):
        limits.check_z(3200.0)


def test_published_limits_govern_the_next_connection(client, stand_in):
    path = config.adopt_limits(
        config.build_payload(
            {"x_um": [0, 2000], "y_um": [0, 3000], "z_um": [100, 9000]},
            objective_position=[1, 2],
        )
    )
    assert json.loads(Path(path["limits_path"]).read_text())["objective_position"] == {
        "allowed": [1, 2]
    }
    handshake.connect_handshake(client)
    limits = gate.state_of(client).limits
    assert limits.source == "machine" and not limits.is_fallback
    assert limits.stage_um()["z_um"] == [100.0, 9000.0]
    with pytest.raises(LimitViolation):
        limits.check_command("set_objective", {"objective_position": 3})
    limits.check_command("set_objective", {"objective_position": 2})


def test_a_damaged_limits_file_falls_back_to_the_cautious_defaults(client):
    folder = store.machine().write("limits", {"x_um": "everywhere"}).parent
    assert (folder / "limits.json").exists()
    handshake.connect_handshake(client)
    assert gate.state_of(client).limits.is_fallback


def test_unknown_commands_and_odd_numbers_are_refused():
    limits = NikonLimits(
        policy(),
        travel=None,
        z_window=None,
        source="machine",
        path=None,
        is_fallback=False,
    )
    with pytest.raises(LimitsError):
        limits.check_command("open_shutter", {})
    for odd in (True, "5", float("nan"), float("inf"), None):
        with pytest.raises(LimitViolation):
            limits.check_xy(odd, 0)


def test_no_limits_refuse_every_change(client):
    assert gate.check_refusal(client, "move_xy", {"x_um": 0, "y_um": 0}) is not None
    gate.install(client, gate.GateState(limits=None, stage_cfg=None, error="broken"))
    assert "broken" in gate.check_refusal(client, "move_z", {"z_um": 0})


def test_the_corner_recorder_measures_the_stage_range(client, stand_in):
    recorder = CornerRecorder(client)
    for corner, (x, y) in enumerate(
        ((-10, -20), (30, -20), (30, 40), (-10, 40)), start=1
    ):
        stand_in.scope.place(x, y, 3000.0)
        recorder.record(corner)
    stand_in.scope.place(0, 0, 2500.0)
    recorder.record_z("low")
    stand_in.scope.place(0, 0, 3500.0)
    recorder.record_z("high")
    measured = recorder.limits()
    assert measured["x_um"]["range"] == [-10.0, 30.0]
    assert measured["y_um"]["range"] == [-20.0, 40.0]
    assert measured["z_um"]["range"] == [2500.0, 3500.0]
