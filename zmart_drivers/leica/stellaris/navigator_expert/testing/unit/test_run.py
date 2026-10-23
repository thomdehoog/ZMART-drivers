"""The dispatcher's one entry, ``run()``: a reading by its name, or a change as plain data.

The pretend vendor (``testing/helpers/pretend_vendor.py``) is handed in: it
has the four things ``run()`` asks of a vendor for a change
(``limits_setup_hint``, ``make_request``, ``check_reply``, ``WRITING``), and
its changes are plain records with the fields of a write. So these tests also show that ``run()``
needs nothing of LAS X: how a change waits until the microscope is ready, how
it is read back one window at a time and sent again between windows, that
a change that may never be sent again is sent once, and how the dialogs it is
handed are answered around every send.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import contextlib
import time
from types import SimpleNamespace

import pretend_vendor
import pytest

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import dispatch
from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import limits as gate


class PretendRun(pretend_vendor.PretendSources):
    """The pretend vendor, counting the replies it checks."""

    def __init__(self):
        self.replies_checked = 0

    def check_reply(self, client, write):
        self.replies_checked += 1
        return super().check_reply(client, write)


def expect(accept):
    """A reading of the pretend stage's x, and what it must show."""
    return SimpleNamespace(reading="x", accept=accept, about=None, mode=None, fallback=None)


def move_x(x_um, **fields):
    """The pretend stage's move as a write: confirmed when x is within half a micrometre."""
    return SimpleNamespace(
        **{
            "command": "move_x",
            "values": {"x_um": x_um},
            "request": "move_x",
            "fill": lambda request: request.update(x_um=x_um),
            "description": f"move x to {x_um} um",
            "ready": None,
            "confirm": expect(lambda value: abs(value - x_um) <= 0.5),
            "without_receipt": False,
            "never_resend": False,
            **fields,
        }
    )


@pytest.fixture
def scope():
    scope = pretend_vendor.connect()
    yield scope
    gate.uninstall(scope)


@pytest.fixture
def vendor():
    return PretendRun()


def test_a_reading_is_run_by_its_name(scope, vendor):
    reading = dispatch.run(vendor, scope, "x")

    assert (reading.value, reading.source, reading.error) == (0.0, "stage", None)


def test_a_change_is_sent_once_and_confirmed_in_its_first_window(scope, vendor):
    result = dispatch.run(vendor, scope, move_x(250.0))

    assert (result["success"], result["confirmed"]) == (True, True)
    assert result["timing"]["attempts"] == 1
    assert [r["x_um"] for r in scope.received] == [250.0]
    assert "route" in result
    assert vendor.replies_checked == 1


def test_a_refused_change_never_makes_a_request(scope, vendor):
    made = []
    vendor.make_request = lambda client, write: made.append(write)

    result = dispatch.run(vendor, scope, move_x(5000.0))

    assert result == {
        "success": False,
        "confirmed": None,
        "message": result["message"],
        "timing": dispatch.make_timing(total_s=0.0, attempts=0),
        "logs": result["logs"],
    }
    assert "outside" in result["message"] and result["logs"][0]["level"] == "error"
    assert made == [] and scope.received == []


def test_a_change_not_seen_is_sent_again_in_every_window_and_reported_unconfirmed(scope, vendor):
    never = move_x(250.0, confirm=expect(lambda value: False))

    result = dispatch.run(vendor, scope, never)

    windows = pretend_vendor.Waiting.WINDOWS
    assert (result["success"], result["confirmed"]) == (True, False)
    assert len(scope.received) == windows
    assert result["timing"]["confirm_attempts"] == windows
    assert "readback unconfirmed" in result["message"]
    # Each window says what it read last.
    misses = [e["msg"] for e in result["logs"] if "did not show the change" in e["msg"]]
    assert len(misses) == windows and "last read 250.0" in misses[-1]


def test_a_change_that_may_never_be_sent_again_is_sent_once(scope, vendor):
    once = move_x(250.0, confirm=expect(lambda value: False), never_resend=True)

    result = dispatch.run(vendor, scope, once, max_retries=5)

    assert (result["success"], result["confirmed"]) == (True, False)
    assert len(scope.received) == 1
    assert result["timing"]["confirm_attempts"] == 1


def test_a_change_waits_until_the_microscope_is_ready(scope, vendor):
    ready = move_x(250.0, ready=expect(lambda value: value == 0.0))

    result = dispatch.run(vendor, scope, ready)

    assert result["confirmed"] is True
    assert result["timing"]["pre_check_s"] > 0.0


def test_a_microscope_never_ready_is_sent_nothing(scope, vendor):
    blocked = move_x(250.0, ready=expect(lambda value: False))

    result = dispatch.run(vendor, scope, blocked)

    assert result["success"] is False
    assert "Pre-check failed" in result["message"]
    assert "x not ready" in result["logs"][0]["msg"]
    assert scope.received == []


def test_a_change_sent_without_a_receipt_has_no_reply_checked(scope, vendor):
    result = dispatch.run(vendor, scope, move_x(250.0, without_receipt=True))

    assert result["confirmed"] is True
    assert vendor.replies_checked == 0


def test_a_busy_reply_is_sent_again(scope, vendor):
    scope.busy_answers = 1

    result = dispatch.run(vendor, scope, move_x(40.0))

    assert result["confirmed"] is True
    assert len(scope.received) == 2


class PretendDialogs:
    """The pretend microscope's dialogs, as ``run()`` is handed them: a warning OK answers, or one left open.

    It notes how many requests the microscope had received when it answered,
    and how many arrived while it watched.
    """

    def __init__(self, scope, *, warning=None, left_open=None, opens_while_sending=None):
        self.scope = scope
        self.warning, self.left_open, self.opens_while_sending = warning, left_open, opens_while_sending
        self.history, self.answered_after, self.sent_while_watching = [], [], 0

    def answer_warnings(self):
        if self.scope.received and self.opens_while_sending:
            self.left_open = self.opens_while_sending  # the request made LAS X show it
        if self.left_open:
            raise RuntimeError(f"dialog open, left for the operator: {self.left_open}")
        if self.warning:
            self.answered_after.append(len(self.scope.received))
            self.history.append((time.monotonic(), SimpleNamespace(message=f"answered {self.warning!r} with OK")))
            self.warning = None

    @contextlib.contextmanager
    def watching(self):
        before = len(self.scope.received)
        yield
        self.sent_while_watching += len(self.scope.received) - before

    def answered_since(self, moment):
        return [note for at, note in self.history if at >= moment]


def test_a_change_handed_dialogs_answers_a_warning_before_it_is_sent(scope, vendor):
    dialogs = PretendDialogs(scope, warning="Please keep minimum distance")

    result = dispatch.run(vendor, scope, move_x(250.0), dialogs=dialogs)

    assert (result["success"], result["confirmed"]) == (True, True)
    assert dialogs.answered_after == [0]  # answered before anything was sent
    assert dialogs.sent_while_watching == 1  # and watched for while it was
    answered = [e for e in result["logs"] if "with OK" in e["msg"]]
    assert [e["level"] for e in answered] == ["warning"]


def test_a_dialog_left_for_the_operator_stops_the_send(scope, vendor):
    dialogs = PretendDialogs(scope, left_open="Turn the turret by hand")

    result = dispatch.run(vendor, scope, move_x(250.0), dialogs=dialogs)

    assert result["success"] is False
    assert result["message"] == (
        "move x to 250.0 um | Fire exception: dialog open, left for the operator: Turn the turret by hand"
    )
    assert scope.received == []


def test_a_send_that_fails_while_a_dialog_is_in_the_way_fails_for_the_dialog(scope, vendor, monkeypatch):
    dialogs = PretendDialogs(scope, opens_while_sending="Turn the turret by hand")

    def no_answer(client, request, description, *, asynchronous):
        client.take(request)
        raise TimeoutError("the microscope did not answer")

    monkeypatch.setattr(vendor.WRITING.transport, "send", no_answer)

    result = dispatch.run(vendor, scope, move_x(250.0), dialogs=dialogs)

    assert result["success"] is False
    assert "Fire exception: dialog open, left for the operator: Turn the turret by hand" in result["message"]
    assert len(scope.received) == 1
