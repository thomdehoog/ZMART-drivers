"""The gateway's client, transfer and profile: one call, its reply, and ZEN's trouble sorted.

The client is real (its loop thread, its waiting); only ZEN behind it is the
stand-in. The profile's sorting decides whether a change is sent again, so
each kind is checked with the gRPC status ZEN answers.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from mock_zen_api import FakeGRPCError, build_fake_client

from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway import (
    client as gateway_client,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.primitives.gateway import (
    profile,
    reads,
    transfer,
    writes,
)


@pytest.fixture
def fake():
    client, scope = build_fake_client()
    yield client, scope
    client.close()


def test_a_reading_is_converted_from_metres_to_micrometres(fake):
    client, scope = fake

    assert reads.get_xy(client) == {
        "x_um": pytest.approx(20000.0),
        "y_um": pytest.approx(15000.0),
    }
    assert reads.get_z(client) == {"z_um": pytest.approx(1000.0)}


def test_the_objective_in_place_is_named_from_the_changer(fake):
    client, scope = fake
    scope.objective_position = 2

    objective = reads.get_objective(client)

    assert objective["position"] == 2
    assert objective["magnification"] == 20.0


def test_no_active_experiment_is_the_answer_idle_not_an_error(fake):
    client, _ = fake

    assert reads.get_activity(client) == {"running": False, "status": None}


def test_a_write_fills_its_message_in_metres():
    message = SimpleNamespace(x=None, y=None)

    writes.move_xy(1500.0, -250.0).fill(message)

    assert message.x == pytest.approx(0.0015)
    assert message.y == pytest.approx(-0.00025)


def test_the_transfer_keeps_zens_answer_and_its_refusal_as_the_reply(fake):
    client, scope = fake
    request = transfer.request_of(client, "stage.move_to")
    writes.move_xy(100.0, 200.0).fill(request.message)

    assert transfer.GATEWAY.send(client, request, "move", asynchronous=False) == (
        True,
        [],
    )
    assert client.reply.error is None

    scope.refusals["stage.move_to"] = [
        FakeGRPCError("PERMISSION_DENIED", "not in API mode")
    ]
    transfer.GATEWAY.send(client, request, "move", asynchronous=False)
    assert client.reply.error.status.name == "PERMISSION_DENIED"


def test_an_unknown_call_is_a_mistake_in_the_driver(fake):
    client, _ = fake
    with pytest.raises(ValueError, match="unknown ZEN call"):
        transfer.request_of(client, "stage.teleport")


@pytest.mark.parametrize(
    "status, kind",
    [
        ("UNAVAILABLE", profile.Kind.TEMPORARY),
        ("DEADLINE_EXCEEDED", profile.Kind.TEMPORARY),
        ("OUT_OF_RANGE", profile.Kind.BAD_REQUEST),
        ("NOT_FOUND", profile.Kind.BAD_REQUEST),
        ("PERMISSION_DENIED", profile.Kind.PERMANENT),
        ("FAILED_PRECONDITION", profile.Kind.PERMANENT),
        ("SOMETHING_NEW", profile.Kind.PERMANENT),
    ],
)
def test_zens_statuses_are_sorted_and_only_temporary_ones_are_sent_again(status, kind):
    found, _ = profile.classify(FakeGRPCError(status, "x"))

    assert found is kind
    assert profile.sends_again(found) is (kind is profile.Kind.TEMPORARY)


def test_a_refusal_the_operator_can_fix_says_how():
    _, detail = profile.classify(
        FakeGRPCError("PERMISSION_DENIED", "Permission denied")
    )

    assert "Unsupervised API Mode" in detail


def test_a_timeout_is_temporary_and_a_lost_connection_is_its_own_kind():
    assert profile.classify(TimeoutError("slow"))[0] is profile.Kind.TEMPORARY
    assert (
        profile.classify(ConnectionResetError("gone"))[0]
        is profile.Kind.CONNECTION_LOST
    )


def test_check_reply_reads_the_reply_the_transfer_kept():
    write = writes.move_xy(0.0, 0.0)
    client = SimpleNamespace(
        reply=transfer.Reply(error=FakeGRPCError("UNAVAILABLE", "busy"))
    )

    checked = profile.check_reply(client, write)

    assert checked["success"] is False and checked["transient"] is True

    client.reply = transfer.Reply(value=SimpleNamespace(position_changed=True))
    assert profile.check_reply(client, write)["success"] is True


def test_the_client_hands_a_calls_error_to_the_caller_and_times_out(fake):
    client, scope = fake
    scope.refusals["focus.get_position"] = [FakeGRPCError("INTERNAL", "boom")]

    with pytest.raises(FakeGRPCError):
        reads.get_z(client)

    class Slow:
        async def get_position(self, request):
            await asyncio.sleep(1.0)

    client._stubs["stage"] = Slow()
    with pytest.raises(TimeoutError):
        client.call("stage", "get_position", None, timeout=0.05)


def test_a_streamed_answer_is_read_item_by_item(fake):
    client, scope = fake
    scope.active_experiment = "x"

    items = list(
        client.stream(
            "experiment",
            "register_on_status_changed",
            SimpleNamespace(experiment_id="x"),
            item_timeout_s=2,
        )
    )

    assert len(items) == 2


def test_a_closed_client_refuses_calls(fake):
    client, _ = fake
    client.close()
    client.close()

    with pytest.raises(RuntimeError, match="closed"):
        reads.get_xy(client)


def test_zeiss_config_ini_is_read_as_zeiss_writes_it(tmp_path):
    ini = tmp_path / "config.ini"
    ini.write_text(
        "[api]\nhost = 10.0.0.5\nport = 5005\ncert_file = root.pem\ncontrol-token = abc\n",
        encoding="utf-8",
    )

    found = gateway_client.read_config_ini(ini)

    assert found == {
        "host": "10.0.0.5",
        "port": 5005,
        "cert_file": str(tmp_path / "root.pem"),
        "token": "abc",
    }


def test_a_connection_without_a_token_is_refused_before_anything_is_opened():
    with pytest.raises(ValueError, match="control token"):
        gateway_client.open_gateway("127.0.0.1", 5002, token="")


def test_a_named_certificate_that_is_missing_is_refused(tmp_path):
    with pytest.raises(FileNotFoundError, match="certificate"):
        gateway_client.find_certificate(tmp_path / "missing.pem")
