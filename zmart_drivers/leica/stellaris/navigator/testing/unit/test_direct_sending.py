"""The direct route, offline: an element sent to the hardware server, waited for, and LAS X's answer read.

These stand in for LAS X's hardware server object itself, so the driver's own
sending runs: the sender thread, the wait by the rule, what happens when LAS
X never answers (a dialog the element caused is answered by the write loop,
around the send), and that an element is never sent twice.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import threading
import time

import pytest

from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    client as hardware_server,
)
from zmart_drivers.leica.stellaris.navigator.primitives.direct import profile as waiting
from zmart_drivers.leica.stellaris.navigator.primitives.direct import (
    transfer as send_command,
)


class Server:
    """LAS X's hardware server as the sender sees it: ``SendCommand2`` and its answer."""

    def __init__(self):
        self.received = []
        self.answer = '<Result Error="0"/>'
        self.hold = None  # an Event: the server does not answer until it is set
        self.fail = None  # an exception the server raises

    def SendCommand2(self, xml, _):
        self.received.append(xml)
        if self.hold is not None:
            self.hold.wait(5)
        if self.fail is not None:
            raise self.fail
        return self.answer


@pytest.fixture
def server(monkeypatch):
    """A stand-in server and short waiting numbers."""
    server = Server()
    forgotten = []
    monkeypatch.setattr(hardware_server, "connected_server", lambda: server)
    monkeypatch.setattr(hardware_server, "forget_connection", lambda: forgotten.append(1))
    monkeypatch.setattr(waiting, "WINDOWS", 2)
    monkeypatch.setattr(waiting, "WINDOW_S", 0.1)
    monkeypatch.setattr(waiting, "POLL_S", 0.01)
    monkeypatch.setitem(send_command.senders, "current", None)
    server.forgotten = forgotten
    yield server
    if server.hold is not None:
        server.hold.set()  # let a held sender thread finish


def test_an_element_is_sent_and_las_xs_answer_comes_back(server):
    answer = send_command.send('<Scanner Command="SetZoom" Zoom="2" BlockId="0"/>')

    assert answer == '<Result Error="0"/>'
    assert server.received == ['<Scanner Command="SetZoom" Zoom="2" BlockId="0"/>']


def test_the_same_sender_carries_the_next_element(server):
    send_command.send("<a/>")
    first = send_command.senders["current"]
    send_command.send("<b/>")

    assert send_command.senders["current"] is first
    assert server.received == ["<a/>", "<b/>"]


def test_a_server_that_fails_raises_its_error_and_the_connection_is_forgotten(server):
    server.fail = OSError("the hardware server went away")

    with pytest.raises(OSError, match="went away"):
        send_command.send("<a/>")

    assert server.forgotten == [1]


def test_a_server_that_never_answers_is_given_up_on_and_the_element_is_not_sent_again(server):
    server.hold = threading.Event()

    with pytest.raises(hardware_server.HardwareServerUnavailable, match="did not answer"):
        send_command.send("<a/>")

    assert server.received == ["<a/>"]
    assert send_command.senders["current"] is None  # the next element gets a new sender


def test_after_a_server_that_never_answered_the_next_element_goes_through_a_new_sender(server):
    server.hold = threading.Event()
    with pytest.raises(hardware_server.HardwareServerUnavailable):
        send_command.send("<a/>")
    held = server.hold
    server.hold = None

    answer = send_command.send("<b/>")
    held.set()

    assert answer == '<Result Error="0"/>'
    assert "<b/>" in server.received


@pytest.mark.parametrize(
    "answer, accepted",
    [
        ('<Result Error="0"/>', True),
        ('<Result Error="2">Pinhole set to 2.512</Result>', True),
        ("<Result/>", True),
        ('<Result Error="1">Zoom 100 is above the maximum of 48</Result>', False),
        ("", False),
        ("not an answer", False),
    ],
)
def test_which_answers_count_as_accepted(answer, accepted):
    assert send_command.accepted(answer) is accepted


def test_the_reason_inside_an_answer_is_read():
    assert send_command.answer_text('<Result Error="1"> Zoom too high </Result>') == "Zoom too high"
    assert send_command.answer_text('<Result Error="0"/>') == ""


class TestADialogInTheWay:
    """The route's diagnostic: the last dialog LAS X's window showed, from its log."""

    SHOWN = "2026-10-14 09:49:33.502 0007  Info DbTracer 'MessageBox Warning : Please keep minimum distance of 8nm. '\n"
    ANSWERED = "2026-10-14 09:50:46.635 0007  Info DbTracer 'MessageBox Result: OK'\n"

    def test_an_open_dialog_is_reported_when_it_came_after_the_change(self, tmp_path, monkeypatch):
        log = tmp_path / "LAS X.log"
        log.write_text(self.SHOWN, encoding="latin-1")
        monkeypatch.setattr(send_command, "LASX_LOG", log)

        shown = send_command.last_dialog_shown()
        assert shown is not None and "8nm" in shown[1]
        message = send_command.DIRECT.dialog_warning_since(shown[0] - 1, "Zoom")
        assert message is not None and "Zoom | LAS X dialog appears open" in message
        assert send_command.DIRECT.dialog_warning_since(shown[0] + 1, "Zoom") is None

    def test_an_answered_dialog_is_not_reported(self, tmp_path, monkeypatch):
        log = tmp_path / "LAS X.log"
        log.write_text(self.SHOWN + self.ANSWERED, encoding="latin-1")
        monkeypatch.setattr(send_command, "LASX_LOG", log)

        assert send_command.last_dialog_shown() is None
        assert send_command.DIRECT.dialog_warning_since(0.0, "Zoom") is None

    def test_a_log_that_is_not_there_reports_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setattr(send_command, "LASX_LOG", tmp_path / "missing.log")
        assert send_command.DIRECT.dialog_warning_since(time.time() - 60, "Zoom") is None
