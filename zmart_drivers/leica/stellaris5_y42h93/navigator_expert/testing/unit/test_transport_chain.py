"""Several ways of sending one command: the first that can send does, and a command is never done twice.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher import send_loop
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.dispatcher.transport_chain import (
    TransportChain,
    TransportUnavailable,
)
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import routes
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling.profiles import command_profiles as profiles


class FakeRoute:
    """A way of sending that records what it was asked, and can refuse before anything leaves."""

    def __init__(self, name, *, unavailable=None, arrived=True, dialog=None):
        self.name, self.unavailable, self.arrived, self.dialog = (
            name,
            unavailable,
            arrived,
            dialog,
        )
        self.sent, self.cleared, self.waited = [], 0, 0

    def fill(self, request, setup_fn):
        self.filled = setup_fn(request)

    def clear_reply(self, client):
        self.cleared += 1

    def send(self, client, request, label, *, asynchronous):
        if self.unavailable:
            raise TransportUnavailable(self.unavailable)
        self.sent.append(label)
        return self.arrived, [f"{self.name} answered a dialog"] if self.dialog else []

    def await_reply(self, client):
        self.waited += 1
        return True

    def dialog_warning_since(self, started, description):
        return self.dialog


def chain_of(*routes):
    return TransportChain(lambda: [(r.name, r) for r in routes])


def test_the_first_route_that_can_send_does():
    direct, cam = FakeRoute("direct"), FakeRoute("cam")
    chain = chain_of(direct, cam)

    arrived, answered = chain.send(object(), object(), "SetZoom", asynchronous=False)

    assert (arrived, answered) == (True, [])
    assert direct.sent == ["SetZoom"] and cam.sent == []
    assert chain.carried_by == "direct"


def test_a_route_that_cannot_send_is_followed_by_the_next_and_the_answer_says_so():
    direct, cam = (
        FakeRoute("direct", unavailable="the hardware server is not running"),
        FakeRoute("cam"),
    )
    chain = chain_of(direct, cam)

    arrived, answered = chain.send(object(), object(), "SetZoom", asynchronous=False)

    assert arrived is True
    assert cam.sent == ["SetZoom"]
    assert chain.carried_by == "cam"
    assert (
        "the direct route could not send (the hardware server is not running)"
        in answered[0]
    )
    assert "sent through the cam route instead" in answered[0]


def test_a_send_that_went_out_but_was_not_acknowledged_is_not_tried_on_another_route():
    direct, cam = FakeRoute("direct", arrived=False), FakeRoute("cam")
    chain = chain_of(direct, cam)

    arrived, _ = chain.send(object(), object(), "SetZoom", asynchronous=False)

    assert arrived is False
    assert direct.sent == ["SetZoom"] and cam.sent == []


def test_when_no_route_can_send_the_error_says_why_for_each():
    chain = chain_of(
        FakeRoute("direct", unavailable="no server"),
        FakeRoute("cam", unavailable="no client"),
    )

    with pytest.raises(
        TransportUnavailable,
        match="direct route could not send \\(no server\\).*cam route could not send \\(no client\\)",
    ):
        chain.send(object(), object(), "SetZoom", asynchronous=False)


def test_a_chain_with_no_route_says_so():
    with pytest.raises(TransportUnavailable, match="none is configured"):
        chain_of().send(object(), object(), "SetZoom", asynchronous=False)


def test_the_reply_and_the_dialog_are_asked_of_the_route_that_carried_the_command():
    direct, cam = (
        FakeRoute("direct", unavailable="down"),
        FakeRoute("cam", dialog="a dialog is open"),
    )
    chain = chain_of(direct, cam)
    chain.send(object(), object(), "SetZoom", asynchronous=False)

    assert chain.await_reply(object()) is True
    assert cam.waited == 1 and direct.waited == 0
    assert chain.dialog_warning_since(0.0, "SetZoom") == "a dialog is open"


def test_each_route_clears_its_own_reply_just_before_it_sends():
    direct, cam = FakeRoute("direct", unavailable="down"), FakeRoute("cam")
    chain_of(direct, cam).send(object(), object(), "SetZoom", asynchronous=False)

    assert direct.cleared == 1 and cam.cleared == 1


def test_the_send_loop_works_through_a_chain_and_reports_the_fallback():
    direct, cam = FakeRoute("direct", unavailable="down"), FakeRoute("cam")
    from dataclasses import replace

    profile = replace(
        profiles.COMMAND_DEFAULT,
        transport=chain_of(direct, cam),
        error_check_fn=None,
        skip_reply_check=True,
    )
    request = type("Request", (), {"Model": object()})()

    result = send_loop.fire_block(
        object(), request, "SetZoom", profile=profile, setup_fn=lambda r: "zoom written"
    )

    assert result["success"] is True
    assert direct.filled == "zoom written"  # filled once, by the preferred route
    assert cam.sent == ["SetZoom"]
    assert any(
        "sent through the cam route instead" in entry["msg"] for entry in result["logs"]
    )


def test_the_vendor_policy_is_read_for_every_command(monkeypatch):
    cam = FakeRoute("cam")
    other = FakeRoute("direct")
    monkeypatch.setitem(routes.ROUTES, "cam", cam)
    monkeypatch.setitem(routes.ROUTES, "direct", other)

    monkeypatch.setattr(routes, "WRITE_ROUTES", ("cam",))
    routes.ROUTED.send(object(), object(), "first", asynchronous=False)
    monkeypatch.setattr(routes, "WRITE_ROUTES", ("direct", "cam"))
    routes.ROUTED.send(object(), object(), "second", asynchronous=False)

    assert cam.sent == ["first"] and other.sent == ["second"]


def test_a_route_name_that_does_not_exist_is_refused_with_the_names_that_do(
    monkeypatch,
):
    monkeypatch.setattr(routes, "WRITE_ROUTES", ("sideways", "cam"))

    with pytest.raises(
        ValueError, match="'sideways'.*routes that exist are \\['cam', 'direct'\\]"
    ):
        routes.write_routes()


def test_every_command_profile_sends_through_the_vendor_routes():
    assert profiles.COMMAND_DEFAULT.transport is routes.ROUTED
    assert profiles.ACQUIRE.transport is routes.ROUTED
