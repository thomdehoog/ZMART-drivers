"""One CAM command, delivered to LAS X's hardware server instead of the CAM API.

A :class:`DirectCommand` wraps the CAM API's own command object. The driver's
command writes its fields into that object's ``Model`` exactly as on the CAM
route, so enum types and defaults are the CAM API's own. Only delivery
differs: the fields are translated (``translations.py``) into the element the
CAM API itself would have sent, that element goes to the hardware server with
``SendCommand2``, and LAS X's answer fills the route's command echo, which the
driver's error checks read as they read the CAM API's.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import quoteattr

from ..vendor_interface import hardware_server
from .translations import Fields, NoTranslation

ERROR = re.compile(r'<Result\b[^>]*\bError="([^"]*)"')
MESSAGE = re.compile(r"<Result\b[^>]*>(.*)</Result>", re.DOTALL)


def number_text(value) -> str:
    """A value as LAS X writes it: ``5`` for 5.0, ``2E-06``, ``-1.928E-05``, ``0.066025``."""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float):
        text = format(value, ".15g")
        return text.replace("e", "E")
    return str(value)


def beam_route_xml(route: str) -> str:
    """``"40;2"`` as the ``BeamRoute`` element: one ``BeamPosition`` per level.

    An empty route is the element with no levels, ``<BeamRoute Version="0"/>``:
    the CAM API sent it so for a filter wheel without a route (2026-10-12), and
    LAS X's own serializer writes it so (``HwSerializationHelper``).
    """
    positions = [p for p in str(route or "").split(";") if p]
    if not positions:
        return '<BeamRoute Version="0"/>'
    levels = "".join(
        f'<BeamPosition BeamPositionLevel="{level}" BeamPosition={quoteattr(p)}/>'
        for level, p in enumerate(positions)
    )
    return f'<BeamRoute Version="0">{levels}</BeamRoute>'


def send(xml: str) -> str:
    """Send one element to the hardware server; return LAS X's answer. Never sent twice."""
    server = hardware_server.connected_server()
    try:
        return str(server.SendCommand2(xml, ""))
    except Exception:
        hardware_server.forget_connection()
        raise


#: LAS X's answer codes: 0 accepted, 1 refused, 2 accepted with the value
#: adjusted (pinhole 100 airy was set to 2.512 on the simulator). The CAM API
#: reports an adjusted value as accepted and leaves the difference to the
#: read-back; so does the direct route.
ACCEPTED_CODES = ("0", "", "2")


def accepted(answer: str) -> bool:
    match = ERROR.search(answer or "")
    return bool(match) and match[1] in ACCEPTED_CODES


def answer_text(answer: str) -> str:
    """What LAS X wrote inside its answer: the reason for a refusal or an adjustment."""
    match = MESSAGE.search(answer or "")
    return match[1].strip() if match else ""


class DirectCommand:
    """A CAM command object whose delivery goes to the hardware server."""

    def __init__(self, name, cam_object, translation, route, enum_types):
        self.name = name
        self.Model = cam_object.Model
        self.translation = translation
        self.route = route
        self.enum_types = enum_types

    def xml(self) -> str:
        """The element for the fields written so far; raises ``NoTranslation`` when it has none."""
        if self.translation is None:
            raise NoTranslation(f"{self.name} has no direct translation yet")
        t = self.translation
        attributes = "".join(
            f" {name}={quoteattr(number_text(value))}"
            for name, value in t.attributes(
                Fields(self.Model, self.enum_types), self.route
            )
        )
        if t.beam_route:
            return f'<{t.element} Command="{t.command}"{attributes}>{beam_route_xml(self.Model.BeamRoute)}</{t.element}>'
        return f'<{t.element} Command="{t.command}"{attributes}/>'

    def deliver(self) -> bool:
        xml = self.xml()
        answer = send(xml)
        self.route.answered(xml, answer)
        return True

    def UpdateAwaitReceipt(self, timeout):
        """The CAM API's delivery, here: send, and put LAS X's answer in the echo."""
        return self.deliver()

    def UpdateAsync(self):
        self.deliver()
