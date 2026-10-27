"""The dispatcher runs the driver's primitives over the real bridge protocol, against the mock.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest

from zmart_drivers.nikon.nis_elements_ar610.dispatcher import dispatch
from zmart_drivers.nikon.nis_elements_ar610.dispatcher import limits as gate
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.limits import (
    config,
    limit_rules,
)
from zmart_drivers.nikon.nis_elements_ar610.primitives.dll import (
    client as bridge_client,
)
from zmart_drivers.nikon.nis_elements_ar610.primitives.dll import profile, writes
from zmart_drivers.nikon.nis_elements_ar610.primitives.macro import (
    writes as macro_writes,
)
from zmart_drivers.nikon.nis_elements_ar610.procedures import changes, readings


@pytest.fixture
def client(port):
    """A bridge connection with the bundled default limits installed, cut to the mock's NIS limits."""
    client = bridge_client.connect_bridge("127.0.0.1", port, 5.0)
    nis = readings.get_limits(client)
    limits, _notes = limit_rules.build_limits(
        config.load(), nis, source="defaults", path=None, is_fallback=True
    )
    gate.install(
        client,
        gate.GateState(
            limits=limits, stage_cfg={"stage_um": limits.describe()["envelope_um"]}
        ),
    )
    yield client
    client.close()


def test_the_bridge_answers_with_its_versions(client):
    assert client.info["nis"].startswith("6.10")
    assert readings.ping(client)["protocol"] == client.info["protocol"]


def test_a_reading_comes_from_its_class(client):
    position = readings.get_xyz(client, diagnostics=True)
    camera = readings.get_camera(client, diagnostics=True)

    assert position.value == {"x_um": 1000.0, "y_um": -500.0, "z_um": 500.0}
    assert position.source == "dll"
    assert camera.source == "macro"
    assert camera.value["exposure_ms"] == 33.0


def test_a_reading_asked_of_the_wrong_class_fails_closed(client):
    reading = dispatch.run(profile, client, "xyz", mode="macro")

    assert reading.value is None
    assert isinstance(reading.error, profile.UnsupportedSource)


def test_a_move_is_sent_once_and_confirmed_when_the_stage_arrives(client, mock):
    result = changes.move_xyz(client, 1200.0, -400.0, 510.0)

    assert result["success"] is True and result["confirmed"] is True
    # The bridge reads the stage back right after the request, while the
    # mock's stage is still on its way; the dispatcher's own read-back is
    # what confirms the arrival.
    assert set(result["answered"]) == {"x", "y", "z"}
    assert [c for c in mock.calls if c.startswith("move")] == [
        "move_xyz(1200,-400,510)"
    ]
    assert readings.get_xyz(client) == {"x_um": 1200.0, "y_um": -400.0, "z_um": 510.0}


def test_a_move_outside_the_limits_is_refused_and_nothing_is_sent(client, mock):
    result = changes.move_xyz(client, 99_000.0, 0.0, 500.0)

    assert result["success"] is False
    assert "outside the limits" in result["message"]
    assert mock.calls == []


def test_a_busy_microscope_is_sent_the_move_again(client, mock):
    mock.busy_answers = 1

    result = changes.move_xyz(client, 1100.0, -500.0, 500.0)

    assert result["confirmed"] is True
    assert result["timing"]["attempts"] == 2


def test_a_move_that_never_arrives_is_sent_again_and_then_recorded_as_unconfirmed(
    client, mock
):
    mock.never_arrives = True

    result = changes.move_xyz(client, 1100.0, -500.0, 500.0)

    assert result["success"] is True and result["confirmed"] is False
    assert result["timing"]["confirm_attempts"] == profile.WINDOWS
    assert len([c for c in mock.calls if c.startswith("move")]) == profile.WINDOWS


def test_a_capture_is_sent_once_whatever_happens(client, mock, tmp_path):
    mock.camera_fails = True

    with pytest.raises(RuntimeError, match="camera did not answer"):
        changes.take_picture(client, tmp_path / "a.tif")

    assert mock.calls.count("capture") == 0  # the mock refuses before counting
    mock.camera_fails = False
    answered = changes.take_picture(client, tmp_path / "b.tif")
    assert (
        answered["path"] == str(tmp_path / "b.tif")
        and answered["pixel_size_um"] == 0.65
    )
    assert mock.captures == 1


def test_a_wrong_request_is_a_bad_request_and_is_not_sent_again(client, mock):
    result = changes.select_optical_configuration(client, "no such channel")

    assert result["success"] is False
    assert "ValueError" in result["message"]
    assert result["timing"]["attempts"] == 1


def test_the_exposure_goes_through_the_macro_class_and_reads_back_what_nis_applied(
    client, mock
):
    result = changes.set_exposure(client, 20.4)

    assert result["confirmed"] is True
    assert result["answered"] == {"exposure_ms": 20}
    assert mock.exposure == 20


def test_the_objective_and_the_pfs_are_confirmed_by_their_readings(client, mock):
    assert changes.set_objective(client, 2)["confirmed"] is True
    assert readings.get_objectives(client)["current"] == 2
    assert changes.set_pfs(client, True)["confirmed"] is True
    assert readings.get_pfs(client)["on"] is True


def test_a_lost_connection_fails_the_change_without_sending_it_elsewhere(client, mock):
    client.close()

    result = changes.move_xyz(client, 1100.0, -500.0, 500.0)

    assert result["success"] is False
    assert "closed" in result["message"]
    assert mock.calls == []


def test_the_reply_is_sorted_into_kinds():
    assert (
        profile.classify_error(bridge_client.BridgeLost("gone"))
        is profile.Kind.CONNECTION_LOST
    )
    assert (
        profile.classify_error(ValueError("missing argument"))
        is profile.Kind.BAD_REQUEST
    )
    assert (
        profile.classify_error(RuntimeError("'move' did not start within 5 s"))
        is profile.Kind.TEMPORARY
    )
    assert (
        profile.classify_error(
            RuntimeError("'snap' is still running in NIS after 120 s")
        )
        is profile.Kind.OUTCOME_UNKNOWN
    )
    assert (
        profile.classify_error(RuntimeError("StgMove: DR_NOTINITIALIZED (-7)"))
        is profile.Kind.PERMANENT
    )
    assert profile.sends_again(profile.Kind.TEMPORARY) and not profile.sends_again(
        profile.Kind.OUTCOME_UNKNOWN
    )


def test_the_writes_say_how_close_counts():
    assert writes.within_xyz(1, 2, 3, 0.5)({"x_um": 1.2, "y_um": 2.0, "z_um": 2.6})
    assert not writes.within_xyz(1, 2, 3, 0.5)({"x_um": 1.6, "y_um": 2.0, "z_um": 3.0})
    assert writes.slot_is(2)({"current": 2}) and not writes.slot_is(2)({"current": 1})
    assert writes.pfs_is(True)({"on": True, "status": 1}) and not writes.pfs_is(True)(
        {"on": True, "status": 5}
    )
    assert macro_writes.exposure_within(20.4, 1.0)({"exposure_ms": 20.0})
    assert writes.snap("a.tif").never_resend and writes.autofocus().never_resend
