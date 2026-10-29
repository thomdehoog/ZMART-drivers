"""The direct route: the elements of a change sent in order, LAS X's answer read, and a refusal sorted.

LAS X is stood in for here by a function that returns the answer LAS X would
write. What is checked is what the driver does with a change: the elements
go out in order and stop at the first refused, LAS X's three kinds of answer
end up in the client's reply where the profile's ``check_reply`` reads them,
and a refusal is sorted temporary or permanent, so only a busy LAS X is sent
to again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator.dispatcher.write import (
    TransportUnavailable,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    profile as direct_profile,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import transfer, writes

ACCEPTED = '<Result Error="0"/>'
REFUSED = '<Result Error="1">Zoom 100 is out of range</Result>'
ADJUSTED = '<Result Error="2">Pinhole set to 2.512</Result>'
BARE = "<Result/>"  # how LAS X answers the ATL commands
BUSY = '<Result Error="1">the scanner is busy</Result>'


class Hardware:
    """What LAS X does with the elements sent to it, and what the driver sees of it."""

    def __init__(self, monkeypatch):
        self.sent = []
        self.answers = [ACCEPTED]  # the answers in turn; the last one repeats
        monkeypatch.setattr(hardware_server, "connected_server", lambda: object())
        monkeypatch.setattr(transfer, "send", self.send)

    def send(self, xml):
        self.sent.append(xml)
        answer = self.answers[min(len(self.sent), len(self.answers)) - 1]
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def hardware(monkeypatch):
    return Hardware(monkeypatch)


@pytest.fixture
def client():
    return hardware_server.Client("test")


def send(client, write):
    """*write* through the direct route, as the write loops send it; the route's answer."""
    route = transfer.DIRECT
    request = direct_profile.make_request(client, write)
    route.fill(request, write.fill)
    route.clear_reply(client)
    return route.send(client, request, write.description, asynchronous=False)


def test_a_zoom_becomes_the_element_las_x_takes(hardware, client):
    arrived, notes = send(client, writes.setting("zoom", 1.5))

    assert hardware.sent == ['<Scanner Command="SetZoom" Zoom="1.5" BlockId="0"/>']
    assert (arrived, notes) == (True, [])


@pytest.mark.parametrize(
    ("answer", "accepted", "text"),
    [(ACCEPTED, True, ""), (BARE, True, ""), (ADJUSTED, True, "Pinhole set to 2.512"), (REFUSED, False, "Zoom 100 is out of range")],
)
def test_las_xs_answer_lands_in_the_reply_the_check_reads(hardware, client, answer, accepted, text):
    hardware.answers = [answer]

    send(client, writes.setting("zoom", 1.5))

    assert client.reply["accepted"] is accepted
    assert client.reply["text"] == text
    assert client.reply["element"] == hardware.sent[0]


def test_the_elements_of_a_change_go_out_in_order_and_stop_at_the_first_refused(hardware, client):
    hardware.answers = [ACCEPTED, ACCEPTED, REFUSED]

    _, notes = send(client, writes.tile_scan([(63500.0, 41500.0, 0.0)], 2518))

    assert len(hardware.sent) == 3
    assert 'StartAcquiring' not in "".join(hardware.sent)
    assert client.reply["accepted"] is False
    assert notes == ["Tile scan over 1 positions into project 2518 | LAS X refused element 3 of 9; the rest were not sent"]


def test_a_capture_sends_both_its_elements_when_las_x_takes_them(hardware, client):
    send(client, writes.capture(2518))

    assert [x.split('"')[1] for x in hardware.sent] == ["StopAcquiring", "CaptureSingleFrame"]


def test_an_unreachable_hardware_server_sends_nothing_and_says_so(hardware, client, monkeypatch):
    def unreachable():
        raise hardware_server.HardwareServerUnavailable("LAS X's hardware server is not running here")

    monkeypatch.setattr(hardware_server, "connected_server", unreachable)

    with pytest.raises(TransportUnavailable, match="not running"):
        send(client, writes.setting("zoom", 1.5))
    assert hardware.sent == []


def test_a_request_without_elements_sends_nothing_and_says_so(hardware, client):
    route = transfer.DIRECT
    with pytest.raises(TransportUnavailable, match="no element"):
        route.send(client, writes.Request("Nothing"), "Nothing", asynchronous=False)


def test_a_send_that_las_x_never_answered_raises_and_is_not_sent_again(hardware, client):
    """The element may be held by LAS X and applied later, so the route never sends it twice."""
    hardware.answers = [hardware_server.HardwareServerUnavailable("no answer within four windows")]

    with pytest.raises(hardware_server.HardwareServerUnavailable):
        send(client, writes.setting("zoom", 1.5))

    assert len(hardware.sent) == 1


class TestTheReplyCheck:
    """What the write loops ask the profile after a send: did LAS X take it, and is sending again worthwhile."""

    def test_an_accepted_reply_is_a_success(self, hardware, client):
        send(client, writes.setting("zoom", 1.5))
        assert direct_profile.check_reply(client, writes.setting("zoom", 1.5))["success"] is True

    def test_a_refusal_is_a_permanent_error_with_las_xs_reason(self, hardware, client):
        hardware.answers = [REFUSED]
        write = writes.setting("zoom", 100)
        send(client, write)

        checked = direct_profile.check_reply(client, write)

        assert checked["success"] is False and checked["transient"] is False
        assert checked["error"] == "Zoom 100 is out of range"
        assert "LAS X refused" in checked["logs"][0]["msg"]

    def test_a_busy_las_x_is_a_temporary_error(self, hardware, client):
        hardware.answers = [BUSY]
        write = writes.setting("zoom", 2.0)
        send(client, write)

        checked = direct_profile.check_reply(client, write)

        assert checked["success"] is False and checked["transient"] is True

    def test_no_answer_recorded_is_a_permanent_error(self, client):
        checked = direct_profile.check_reply(client, writes.setting("zoom", 2.0))
        assert checked["success"] is False and checked["transient"] is False
        assert "no answer" in checked["error"]

    @pytest.mark.parametrize(
        ("reason", "temporary"),
        [
            ("Zoom 100 is out of range", False),
            ("Invalid or deleted element ID used", False),
            ("the block is being scanned", True),
            ("Command timed out", True),
            ("something the driver has never seen", False),
            ("", False),
        ],
    )
    def test_how_a_reason_is_sorted(self, reason, temporary):
        assert direct_profile.is_temporary(reason) is temporary
