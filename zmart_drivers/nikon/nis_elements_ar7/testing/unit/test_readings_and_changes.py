"""Each reading, and each change: sent once the limits allow it, then read back.

A reading answers with NIS's own numbers, or with an error that says why. A
change goes through the dispatcher: the limits are asked first, the change
is sent through the bridge, and the microscope is read until it shows the
change. These tests use the real bridge over the pretend NIS.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest

from zmart_drivers.nikon.nis_elements_ar7.dispatcher import dispatch
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import handshake
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import (
    profile,
    reads,
    server,
)
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import NisRefused
from zmart_drivers.nikon.nis_elements_ar7.procedures import changes, readings


@pytest.fixture
def governed(client):
    """The client with this microscope's limits installed, as connect() does."""
    handshake.connect_handshake(client)
    return client


# --- the readings --------------------------------------------------------------------------


def test_every_reading_answers_with_the_pretend_microscope(client, stand_in):
    assert readings.xy(client) == {"x_um": 1000.0, "y_um": 2000.0}
    assert readings.z(client) == {"z_um": 3000.0}
    assert readings.version(client).startswith("7.")
    travel = readings.stage_limits(client)
    assert travel["x_um"] == [-50000.0, 50000.0] and travel["z_um"] == [0.0, 10000.0]
    assert readings.optical_configurations(client) == ["DAPI", "GFP", "Brightfield"]
    assert readings.selected_optical_configuration(client) is None
    assert readings.objective(client) == {"position": 1, "name": "Plan Apo 10x"}
    assert readings.nosepiece(client)["objectives"] == {
        1: "Plan Apo 10x",
        2: "Plan Apo 20x",
        3: "Plan Apo 60x Oil",
    }
    assert readings.pfs(client)["present"] is True
    assert readings.image_info(client)["open"] is False


def test_every_named_reading_is_offered_through_the_dispatcher(client):
    for name in profile.DATUMS:
        found = dispatch.run(profile, client, name)
        assert found is not None and found.error is None, name


def test_a_reading_with_the_hardware_off_fails_with_the_reason_and_quickly(
    client, stand_in
):
    stand_in.scope.hardware_on = False
    found = dispatch.run(profile, client, "xy")
    assert found.error is not None and "DR_NOTAVAILABLE" in str(found.error)
    with pytest.raises(RuntimeError):
        readings.xy(client)


def test_a_failure_code_is_named():
    assert "DR_NOTAVAILABLE" in reads.dr_name(-4)
    assert (
        reads.is_failure_code(0)
        and reads.is_failure_code(-9)
        and not reads.is_failure_code(1)
    )


def test_an_uncalibrated_image_has_no_pixel_size(client, stand_in):
    from mock_nis import open_operator_image

    stand_in.scope.calibrated = False
    open_operator_image(stand_in.scope)
    calibration = readings.calibration(client)
    assert calibration["calibrated"] is False and calibration["pixel_size_um"] is None
    assert calibration["why"]


# --- the changes ---------------------------------------------------------------------------


def test_a_stage_move_is_sent_and_read_back(governed, stand_in):
    moved = changes.move_xy(governed, 1100.0, 1900.0)
    assert moved["success"] and moved["confirmed"]
    assert readings.xy(governed) == pytest.approx({"x_um": 1100.0, "y_um": 1900.0})


def test_a_focus_move_is_sent_and_read_back(governed):
    moved = changes.move_z(governed, 3020.0)
    assert moved["success"] and moved["confirmed"]


def test_a_move_the_limits_refuse_sends_nothing(governed, stand_in):
    refused = changes.move_z(
        governed, 3500.0
    )  # beyond the 100 um window while limits are unpublished
    assert not refused["success"] and "refused" in refused["message"]
    assert "StgMoveZ" not in stand_in.scope.calls


def test_with_no_limits_installed_every_change_is_refused(client, stand_in):
    refused = changes.move_xy(client, 0.0, 0.0)
    assert not refused["success"] and "limits" in refused["message"]
    assert "StgMoveXY" not in stand_in.scope.calls


def test_a_move_that_never_arrives_is_sent_again_then_reported_unconfirmed(
    governed, stand_in, monkeypatch
):
    """NIS says yes every time, but the stage never moves: the move is tried again, then reported, not hidden."""
    monkeypatch.setattr(stand_in.scope, "_start_move", lambda **_: None)
    moved = changes.move_xy(governed, 1200.0, 2000.0)
    assert moved["success"] is True and moved["confirmed"] is False
    assert stand_in.scope.calls.count("StgMoveXY") > 1


def test_the_focus_stays_while_perfect_focus_holds_it(governed, stand_in):
    stand_in.scope.pfs_on = True
    refused = changes.move_z(governed, 3010.0)
    assert not refused["success"] and "Perfect Focus" in refused["message"]
    assert "StgMoveZ" not in stand_in.scope.calls


def test_an_optical_configuration_is_selected_and_read_back(governed):
    chosen = changes.select_optical_configuration(governed, "GFP")
    assert chosen["success"] and chosen["confirmed"]
    assert readings.selected_optical_configuration(governed) == "GFP"


def test_an_unknown_optical_configuration_is_refused_before_anything_is_sent(
    governed, stand_in
):
    with pytest.raises(ValueError, match="GFP"):
        changes.select_optical_configuration(governed, "Cy7")
    assert "SelectOptConf" not in stand_in.scope.calls


def test_the_objective_is_changed_and_read_back(governed):
    turned = changes.set_objective(governed, 3)
    assert turned["success"] and turned["confirmed"]
    assert readings.objective(governed)["name"] == "Plan Apo 60x Oil"


def test_an_empty_or_missing_nosepiece_position_is_refused(governed):
    for position in (4, 9, 0, "two"):
        with pytest.raises(ValueError):
            changes.set_objective(governed, position)


def test_a_change_nis_never_took_is_sent_again(governed, stand_in, monkeypatch):
    """``queued_timeout`` means NIS never ran the request, so sending it again cannot do it twice."""
    real = governed.send_change
    sent = []

    def first_one_queued(op, fn, args):
        sent.append(fn)
        if len(sent) == 1:
            governed.last_reply = {
                "ok": False,
                "kind": server.KIND_QUEUED_TIMEOUT,
                "error": "never ran",
            }
            raise NisRefused("never ran", server.KIND_QUEUED_TIMEOUT)
        return real(op, fn, args)

    monkeypatch.setattr(governed, "send_change", first_one_queued)
    moved = changes.move_xy(governed, 1050.0, 2000.0)
    assert moved["success"] and moved["confirmed"]
    assert sent == ["StgMoveXY", "StgMoveXY"]


def test_a_change_whose_outcome_is_unknown_is_never_sent_again(governed, monkeypatch):
    """``running_timeout`` means NIS may have done it: sending it again could do it twice."""
    sent = []

    def running(op, fn, args):
        sent.append(fn)
        governed.last_reply = {
            "ok": False,
            "kind": server.KIND_RUNNING_TIMEOUT,
            "error": "unknown",
        }
        raise NisRefused("unknown", server.KIND_RUNNING_TIMEOUT)

    monkeypatch.setattr(governed, "send_change", running)
    moved = changes.move_xy(governed, 1050.0, 2000.0)
    assert not moved["success"]
    assert sent == ["StgMoveXY"]


def test_a_failure_code_from_nis_fails_the_change(governed, stand_in):
    stand_in.scope.answer_next["Stg_SetNosepiecePosition"] = -4
    turned = changes.set_objective(governed, 2)
    assert not turned["success"] and "DR_NOTAVAILABLE" in turned["message"]
