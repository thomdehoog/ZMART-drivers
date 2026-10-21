"""The direct writers, offline: an element built, sent to the hardware server, and waited for.

The other direct-route tests stand in for the whole send. These stand in one
level lower, for LAS X's hardware server object and its dialog check, so the
driver's own sending runs: the sender thread, the wait by the rule, what happens
when LAS X never answers or shows a dialog, and that an element is never sent
twice. They also check how a CAM value becomes the text and the enum names the
hardware server takes. Before these, those paths ran only at the microscope or
the simulator.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import threading
from types import SimpleNamespace

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection import (
    hardware_server,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.connection import (
    waiting,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.direct import (
    send_command,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.direct import (
    translations,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.direct.translations import (
    Fields,
    NoTranslation,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.interface import (
    lasx_dialogs,
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
    """A stand-in server, short waiting numbers, and no LAS X window to look at."""
    server = Server()
    forgotten = []
    monkeypatch.setattr(hardware_server, "connected_server", lambda: server)
    monkeypatch.setattr(hardware_server, "forget_connection", lambda: forgotten.append(1))
    monkeypatch.setattr(lasx_dialogs, "answer_warnings", lambda: None)
    monkeypatch.setattr(waiting, "WINDOWS", 2)
    monkeypatch.setattr(waiting, "WINDOW_S", 0.1)
    monkeypatch.setattr(waiting, "POLL_S", 0.01)
    monkeypatch.setitem(send_command.senders, "current", None)
    server.forgotten = forgotten
    yield server
    if server.hold is not None:
        server.hold.set()  # let a held sender thread finish


# --- sending ------------------------------------------------------------------------


def test_an_element_is_sent_and_las_xs_answer_comes_back(server):
    answer = send_command.send('<Scanner Command="SetZoom" Zoom="2"/>')

    assert answer == '<Result Error="0"/>'
    assert server.received == ['<Scanner Command="SetZoom" Zoom="2"/>']


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


def test_an_open_dialog_ends_the_wait_with_its_text(server, monkeypatch):
    server.hold = threading.Event()

    turret = SimpleNamespace(caption="LAS X", text="Turn the turret manually", at="12:00:00")

    def a_dialog():
        raise lasx_dialogs.DialogOpen(turret, "it asks for more than OK")

    monkeypatch.setattr(lasx_dialogs, "answer_warnings", a_dialog)

    with pytest.raises(lasx_dialogs.DialogOpen, match="turret"):
        send_command.send("<a/>")

    assert server.received == ["<a/>"]
    assert send_command.senders["current"] is None


# --- LAS X's answer -------------------------------------------------------------------


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


# --- values as LAS X writes them ---------------------------------------------------


@pytest.mark.parametrize(
    "value, text",
    [(5.0, "5"), (2e-06, "2E-06"), (-1.928e-05, "-1.928E-05"), (0.066025, "0.066025"), (True, "1"), (False, "0"), (3, "3")],
)
def test_a_value_is_written_as_las_x_writes_it(value, text):
    assert send_command.number_text(value) == text


def test_a_beam_route_is_one_position_per_level():
    assert send_command.beam_route_xml("40;2") == (
        '<BeamRoute Version="0"><BeamPosition BeamPositionLevel="0" BeamPosition="40"/>'
        '<BeamPosition BeamPositionLevel="1" BeamPosition="2"/></BeamRoute>'
    )


@pytest.mark.parametrize("route", ["", None])
def test_no_beam_route_is_the_element_without_levels(route):
    assert send_command.beam_route_xml(route) == '<BeamRoute Version="0"/>'


# --- enum values ----------------------------------------------------------------------


class MoveXyMode:
    """A CAM enum type as pythonnet shows it: named members that turn into integers."""

    eMoveXY = 2
    eMoveX = 0
    other = 7


def test_an_enum_written_as_its_own_value_is_named_by_its_text():
    fields = Fields(SimpleNamespace(Units="eMicrons"), {})

    assert fields.enum("Units") == "eMicrons"


def test_an_enum_written_as_a_number_is_named_by_its_type():
    fields = Fields(SimpleNamespace(MoveXyMode=2), {"MoveXyMode": MoveXyMode})

    assert fields.enum("MoveXyMode") == "eMoveXY"


def test_an_enum_number_the_type_does_not_name_falls_back_to_the_drivers_own_table():
    fields = Fields(SimpleNamespace(Units=4), {})

    assert fields.enum("Units") == "eMicrons"


def test_an_enum_number_nobody_names_is_refused():
    fields = Fields(SimpleNamespace(Units=9), {})

    with pytest.raises(NoTranslation, match="no known name"):
        fields.enum("Units")


def test_a_true_or_false_is_not_an_enum():
    with pytest.raises(NoTranslation, match="not an enum"):
        Fields(SimpleNamespace(Units=True), {}).enum("Units")


def test_a_member_that_is_not_a_number_counts_as_none():
    assert translations.member_int("eMoveXY") is None
    assert translations.member_int(None) is None
    assert translations.member_int(2) == 2


def test_an_enum_name_with_no_hardware_server_value_is_refused():
    fields = Fields(SimpleNamespace(ZUseMode="eUseSomethingNew"), {})

    with pytest.raises(NoTranslation, match="no hardware-server value"):
        translations.pick(translations.Z_USE_MODE, fields, "ZUseMode")


def test_a_position_is_turned_into_metres_by_its_unit():
    fields = Fields(SimpleNamespace(XPosition=5000.0, Units="eMicrons"), {})

    assert translations.metres(fields, "XPosition") == pytest.approx(0.005)


def test_only_a_move_of_both_axes_has_a_direct_form():
    both = Fields(SimpleNamespace(MoveXyMode="eMoveXY"), {})
    one = Fields(SimpleNamespace(MoveXyMode="eMoveX"), {})

    assert translations.move_xy_mode(both) == [("MoveStageX", 1), ("MoveStageY", 1)]
    with pytest.raises(NoTranslation, match="only eMoveXY"):
        translations.move_xy_mode(one)


# --- whole elements -------------------------------------------------------------------


class Route:
    def block_id(self, job):
        return {"HiRes": 152}[job]


def test_a_stage_move_becomes_the_element_las_x_logs():
    model = SimpleNamespace(XPosition=63500.0, YPosition=41500.0, Units=4, RelativePosition=False, MoveXyMode=2)

    xml = send_command.element_xml(
        translations.TRANSLATIONS["PyApiMoveHardwareXY"], model, Route(), {"MoveXyMode": MoveXyMode}
    )

    assert xml == (
        '<Scanner Command="SetStagePositionInHwOnly" StagePosX="0.0635" StagePosY="0.0415"'
        ' RelativeCoordinates="0" MoveStageX="1" MoveStageY="1"/>'
    )


def test_a_shutter_change_carries_its_beam_route_as_a_child():
    model = SimpleNamespace(Activate=True, SettingIndex=0, JobName="HiRes", BeamRoute="40;2")

    xml = send_command.element_xml(
        translations.TRANSLATIONS["PyApiSetLaserShutterByJobName"], model, Route(), {}
    )

    assert xml.startswith('<Scanner Command="ActivateShutter" Activate="1" SetMode="2" SingleSettingIndex="0" BlockId="152">')
    assert xml.endswith('<BeamPosition BeamPositionLevel="1" BeamPosition="2"/></BeamRoute></Scanner>')


def test_a_command_without_a_translation_is_refused_before_anything_is_built():
    with pytest.raises(NoTranslation, match="no direct translation"):
        send_command.element_xml(None, SimpleNamespace(), Route(), {})
