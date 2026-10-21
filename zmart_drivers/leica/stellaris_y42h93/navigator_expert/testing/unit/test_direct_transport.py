"""The direct route: a change sent straight to LAS X's hardware server, with the CAM API behind it.

LAS X is stood in for here: the hardware server is a function that returns the answer
LAS X would write, and the state tree gives job ids. What is checked is what the driver
does with a command: the element it builds, how LAS X's three kinds of answer end up in
the reply the driver's error check reads, and the two rules that keep a command from
being done twice.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from types import SimpleNamespace

import pytest

from zmart_drivers.leica.stellaris_y42h93.navigator_expert.dispatcher.transport_chain import (
    TransportChain,
    TransportUnavailable,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.connection import (
    hardware_server,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.operations.sending import (
    direct_transport,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.primitives.api.request import (
    Request,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.primitives.direct import (
    send_command,
)
from zmart_drivers.leica.stellaris_y42h93.navigator_expert.primitives.direct.state_tree import (
    Job,
    JobSequence,
)

ACCEPTED = '<Result Error="0"/>'
REFUSED = '<Result Error="1">Zoom 100 is above the maximum of 48</Result>'
ADJUSTED = '<Result Error="2">Pinhole set to 2.512</Result>'
BARE = "<Result/>"  # how LAS X answers the job-sequence commands


class Hardware:
    """What LAS X does with the elements sent to it, and what the driver sees of it."""

    def __init__(self, monkeypatch):
        self.sent = []
        self.answer = ACCEPTED
        monkeypatch.setattr(hardware_server, "connected_server", lambda: object())
        monkeypatch.setattr(direct_transport, "send", self.send)
        monkeypatch.setattr(
            direct_transport,
            "job_sequence",
            lambda: JobSequence(
                block_id=144,
                selected_element=1,
                jobs={"HiRes": Job(152, 3), "Overview": Job(150, 2)},
            ),
        )

    def send(self, xml):
        self.sent.append(xml)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def hardware(monkeypatch):
    return Hardware(monkeypatch)


@pytest.fixture
def client():
    return SimpleNamespace(
        PyApiCommandEcho=SimpleNamespace(
            Model=SimpleNamespace(HasError=False, Error="", Result=0)
        )
    )


def zoom_request(value=1.5, job="HiRes"):
    return Request(
        "PyApiSetZoomByJobName",
        SimpleNamespace(Model=SimpleNamespace(JobName=job, ZoomValue=value)),
    )


def send_zoom(route, client, **given):
    request = zoom_request(**given)
    route.fill(request, lambda model: None)
    return route.send(client, request, "Zoom", asynchronous=False)


def test_a_zoom_becomes_the_element_las_x_itself_logs(hardware, client):
    send_zoom(direct_transport.DirectTransport(), client)

    assert hardware.sent == ['<Scanner Command="SetZoom" Zoom="1.5" BlockId="152"/>']


def test_the_job_ids_come_from_las_x_now_and_not_from_the_cam_apis_list(
    hardware, client
):
    send_zoom(direct_transport.DirectTransport(), client, job="Overview")

    assert 'BlockId="150"' in hardware.sent[0]


@pytest.mark.parametrize(
    ("answer", "has_error", "result"),
    [(ACCEPTED, False, 1), (BARE, False, 1), (ADJUSTED, False, 1), (REFUSED, True, 2)],
)
def test_las_xs_answer_lands_in_the_reply_the_error_check_reads(
    hardware, client, answer, has_error, result
):
    hardware.answer = answer

    arrived, _ = send_zoom(direct_transport.DirectTransport(), client)

    echo = client.PyApiCommandEcho.Model
    assert arrived is True
    assert (echo.HasError, echo.Result) == (has_error, result)


def test_a_refusal_carries_las_xs_reason(hardware, client):
    hardware.answer = REFUSED

    send_zoom(direct_transport.DirectTransport(), client)

    assert "above the maximum of 48" in client.PyApiCommandEcho.Model.Error


def test_a_command_without_a_translation_sends_nothing_and_says_so(hardware, client):
    request = Request("PyApiNothingLikeThis", SimpleNamespace(Model=SimpleNamespace()))

    with pytest.raises(TransportUnavailable, match="no direct translation"):
        direct_transport.DirectTransport().send(
            client, request, "Nothing", asynchronous=False
        )
    assert hardware.sent == []


def test_a_job_that_las_x_does_not_have_sends_nothing_and_says_so(hardware, client):
    with pytest.raises(TransportUnavailable, match="no job 'Gone'"):
        send_zoom(direct_transport.DirectTransport(), client, job="Gone")
    assert hardware.sent == []


def test_an_unreachable_hardware_server_sends_nothing_and_says_so(
    hardware, client, monkeypatch
):
    def unreachable():
        raise hardware_server.HardwareServerUnavailable(
            "LAS X's hardware server is not running here"
        )

    monkeypatch.setattr(hardware_server, "connected_server", unreachable)

    with pytest.raises(TransportUnavailable, match="not running"):
        send_zoom(direct_transport.DirectTransport(), client)
    assert hardware.sent == []


def chain_with(direct, cam):
    return TransportChain(lambda: [("direct", direct), ("cam", cam)])


class CamRoute:
    """Stands in for the CAM API's transport, which only records that it was asked."""

    def __init__(self):
        self.sent = []

    def fill(self, request, setup_fn):
        setup_fn(request.Model)

    def clear_reply(self, client):
        pass

    def send(self, client, request, label, *, asynchronous):
        self.sent.append(label)
        return True, []

    def await_reply(self, client):
        return True

    def dialog_warning_since(self, started, description):
        return None


def test_when_the_hardware_server_is_unreachable_the_cam_api_carries_the_command(
    hardware, client, monkeypatch
):
    def unreachable():
        raise hardware_server.HardwareServerUnavailable("not running")

    monkeypatch.setattr(hardware_server, "connected_server", unreachable)
    cam = CamRoute()
    chain = chain_with(direct_transport.DirectTransport(), cam)

    request = zoom_request()
    chain.fill(request, lambda model: None)
    chain.send(client, request, "Zoom", asynchronous=False)

    assert cam.sent == ["Zoom"]
    assert chain.carried_by == "cam"


def test_a_refusal_by_las_x_is_not_tried_again_on_the_cam_api(hardware, client):
    hardware.answer = REFUSED
    cam = CamRoute()
    chain = chain_with(direct_transport.DirectTransport(), cam)

    request = zoom_request(100)
    chain.fill(request, lambda model: None)
    chain.send(client, request, "Zoom", asynchronous=False)

    assert cam.sent == []
    assert chain.carried_by == "direct"


def test_a_send_that_las_x_never_answered_is_not_tried_again_on_the_cam_api(
    hardware, client
):
    """The element may be held by LAS X and applied later, so it is never handed on."""
    hardware.answer = hardware_server.HardwareServerUnavailable(
        "no answer within four windows"
    )
    cam = CamRoute()
    chain = chain_with(direct_transport.DirectTransport(), cam)

    request = zoom_request()
    chain.fill(request, lambda model: None)
    with pytest.raises(hardware_server.HardwareServerUnavailable):
        chain.send(client, request, "Zoom", asynchronous=False)

    assert cam.sent == []


def test_the_element_builder_names_a_value_with_no_known_form():
    request = SimpleNamespace(Model=SimpleNamespace(JobName="HiRes", ZoomValue=1.5))
    with pytest.raises(send_command.NoTranslation):
        send_command.element_xml(None, request.Model, object(), {})


class Projects:
    """The Data Container as an acquire sees it: which projects are open, and which one an image is in."""

    def __init__(self, monkeypatch, tmp_path):
        self.open = {}
        self.image_in = {}
        self.made = []
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        monkeypatch.setattr(
            direct_transport, "open_experiments", lambda: dict(self.open)
        )
        monkeypatch.setattr(
            direct_transport, "experiment_is_open", lambda number: number in self.open
        )
        monkeypatch.setattr(
            direct_transport, "experiment_of", lambda image: self.image_in[image]
        )

        def new_experiment(name):
            self.made.append(name)
            self.open[900 + len(self.made)] = name
            return 900 + len(self.made)

        monkeypatch.setattr(direct_transport, "new_experiment", new_experiment)


@pytest.fixture
def projects(monkeypatch, tmp_path):
    return Projects(monkeypatch, tmp_path)


def test_an_acquire_goes_into_the_project_of_the_last_image(projects):
    projects.open = {84: "Project", 90: "Other"}
    projects.image_in = {5148: 84}

    assert direct_transport.DirectTransport().open_project_for(5148) == 84


def test_when_the_last_image_is_gone_the_remembered_project_is_used(projects):
    projects.open = {84: "Project", 90: "Other"}
    transport = direct_transport.DirectTransport()
    transport.experiment = 84

    assert transport.open_project_for(0) == 84


def test_a_project_deleted_in_las_x_is_never_used_even_when_the_last_image_names_it(
    projects,
):
    """A deleted project stays in the Data Container, closed, and an image sent into it is seen nowhere."""
    projects.open = {90: "Open one"}
    projects.image_in = {2135: 698}

    assert direct_transport.DirectTransport().open_project_for(2135) == 90


def test_with_no_remembered_project_the_newest_open_one_is_used(projects):
    projects.open = {84: "Project", 90: "Other"}

    assert direct_transport.DirectTransport().open_project_for(0) == 90


def test_with_no_project_open_a_new_one_is_made_and_numbered_after_the_open_ones(
    projects,
):
    chosen = direct_transport.DirectTransport().open_project_for(0)

    assert projects.made == ["ZMART_Experiment1"]
    assert chosen == 901


def test_a_new_project_is_numbered_one_above_the_open_ones(projects):
    assert (
        direct_transport.new_project_name({1: "ZMART_Experiment3", 2: "Project"})
        == "ZMART_Experiment4"
    )


def test_the_chosen_project_is_written_down_for_the_next_start(projects, monkeypatch):
    projects.open = {84: "Project"}
    projects.image_in = {5148: 84}
    monkeypatch.setattr(direct_transport, "last_image_id", lambda: 5148)

    direct_transport.DirectTransport().images_experiment_id()

    assert direct_transport.read_remembered_experiment() == 84
