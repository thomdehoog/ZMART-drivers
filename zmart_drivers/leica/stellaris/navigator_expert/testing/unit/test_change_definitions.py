"""Pins what each change definition asks the dispatcher to do.

Every setting the driver can change (zoom, scan speed, laser intensity, ...) is
defined once: which LAS X request to fill in, in which order, what to call the
change in the log, how the result is read back, and which values the limits
gate is shown. These tests call each definition with sample values and compare
that whole description with the one saved in ``data/change_definitions.json``.

The saved description was first recorded from the code before the definitions
were moved into a table, and recorded again when the settings became one table
(2026-10-21) and when each change became a ``Write`` the dispatcher's ``run()``
sends (2026-10-23): the requests, their fields, the gate's values and the log
text stayed the same, only the description of each confirmation changed. A
change in any entry shows up here as a plain difference.
Run this file as a script to record the description again after a change that is
meant to alter one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
        University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
"""

import json
import sys
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris.navigator_expert.dispatcher import dispatch
from zmart_drivers.leica.stellaris.navigator_expert.primitives.cam_api import writes
from zmart_drivers.leica.stellaris.navigator_expert.procedures import changes


SAVED = Path(__file__).resolve().parents[1] / "data" / "change_definitions.json"


class RecordingModel:
    """Stands in for a LAS X request's model: remembers what is written, in order.

    Reading a field that was never written fails, as an unset .NET field does,
    so the definitions take the same fallback paths they take with no live enum.
    """

    def __init__(self):
        object.__setattr__(self, "writes", [])

    def __setattr__(self, name, value):
        self.writes.append([name, value])

    def __getattr__(self, name):
        raise AttributeError(name)


class RecordingRequest:
    def __init__(self, name):
        self.name = name
        self.Model = RecordingModel()


class RecordingClient:
    """Every attribute is a LAS X request that records its name and its model."""

    def __getattr__(self, name):
        request = RecordingRequest(name)
        object.__setattr__(self, name, request)
        return request


def _describe_check(fn):
    """The named check and the values it holds, such as the setting, its arguments and the tolerance."""
    held = {}
    for name, cell in zip(fn.__code__.co_freevars, fn.__closure__ or ()):
        value = cell.cell_contents
        held[name] = value.name if isinstance(value, writes.Setting) else value
    return {"check": fn.__qualname__.split(".")[0], "holds": held}


def _describe_expect(expect):
    if expect is None:
        return None
    return {
        "reading": expect.reading,
        "about": expect.about,
        "mode": expect.mode,
        "fallback": expect.fallback,
        "accept": _describe_check(expect.accept),
    }


def record(call):
    """Run ``call(client)`` and describe everything it asked of the dispatcher."""
    asked = {"gate": [], "dispatch": None, "result": None}
    client = RecordingClient()

    def run(_profile, _client, write, *, max_retries=None, **_asked):
        # What the dispatcher shows the limits gate before anything is sent.
        asked["gate"].append([write.command, write.values])
        model = RecordingModel()
        write.fill(model)
        asked["dispatch"] = {
            "request": write.request,
            "description": write.description,
            "writes": model.writes,
            "ready": _describe_expect(write.ready),
            "confirm": _describe_expect(write.confirm),
            "accepted_replies": list(write.accepted_replies),
            "without_receipt": write.without_receipt,
            "never_resend": write.never_resend,
            "max_retries": max_retries,
        }
        return {"success": True, "confirmed": True, "message": write.description, "logs": []}

    original = dispatch.run
    dispatch.run = run
    try:
        result = call(client)
    finally:
        dispatch.run = original
    asked["result"] = (
        {"success": result.get("success"), "message": result.get("message")}
        if asked["dispatch"] is None
        else "from the dispatcher"
    )
    return asked


CASES = {
    "zoom": lambda k: changes.change(k, "zoom", "J", value=1.5),
    "zoom_with_overrides": lambda k: changes.change(k, "zoom", "J", value=1.5, max_retries=2, tolerance=0.01),
    "scan_speed": lambda k: changes.change(k, "scan_speed", "J", value=400),
    "scan_resonant_on": lambda k: changes.change(k, "scan_resonant", "J", enable=True),
    "scan_mode": lambda k: changes.change(k, "scan_mode", "J", mode="xyz"),
    "sequential_frame": lambda k: changes.change(k, "sequential_mode", "J", mode="Frame"),
    "sequential_unknown": lambda k: changes.change(k, "sequential_mode", "J", mode="Bogus"),
    "sequential_empty": lambda k: changes.change(k, "sequential_mode", "J", mode=" "),
    "scan_field_rotation": lambda k: changes.change(k, "scan_field_rotation", "J", angle=12.5, tolerance=0.2),
    "image_format_text": lambda k: changes.change(k, "image_format", "J", format_str="512 x 512"),
    "image_format_tuple": lambda k: changes.change(k, "image_format", "J", format_str=(256, 128)),
    "z_stack_definition_both": lambda k: changes.change(k, "z_stack_definition", "J", begin_um=-5.0, end_um=5.0),
    "z_stack_definition_zero_begin": lambda k: changes.change(k, "z_stack_definition", "J", begin_um=0.0),
    "z_stack_definition_reset": lambda k: changes.change(k, "z_stack_definition", "J", begin_um=None, end_um=None, old_begin_um=1, old_end_um=1),
    "z_stack_step_size": lambda k: changes.change(k, "z_stack_step_size", "J", step_size_um=0.5),
    "z_stack_size": lambda k: changes.change(k, "z_stack_size", "J", size_um=20.0),
    "frame_accumulation": lambda k: changes.change(k, "frame_accumulation", "J", setting_index=1, value=3),
    "frame_average": lambda k: changes.change(k, "frame_average", "J", setting_index=1, value=2),
    "line_accumulation": lambda k: changes.change(k, "line_accumulation", "J", setting_index=0, value=4),
    "line_average": lambda k: changes.change(k, "line_average", "J", setting_index=0, value=2),
    "pinhole_airy": lambda k: changes.change(k, "pinhole_airy", "J", setting_index=0, value=1.0, tolerance=0.05),
    "detector_gain": lambda k: changes.change(k, "detector_gain", "J", setting_index=0, beam_route="HyD1", value=55.0),
    "laser_intensity": lambda k: changes.change(k, "laser_intensity", "J", setting_index=0, beam_route="R1", line_index=2, value=0.25),
    "laser_shutter_open": lambda k: changes.change(k, "laser_shutter", "J", setting_index=0, beam_route="R1", activate=True),
    "laser_shutter_closed": lambda k: changes.change(k, "laser_shutter", "J", setting_index=0, beam_route="R1", activate=False),
    "filter_wheel_slot": lambda k: changes.change(k, "filter_wheel_slot", "J", setting_index=0, beam_route="R1", filter_wheel_type=2, slot_index=3),
    "filter_wheel_spectrum": lambda k: changes.change(k, "filter_wheel_spectrum", "J", setting_index=0, beam_route="R1", filter_wheel_type=2, position=520.0, tolerance=1.0),
}


def record_all():
    return {name: record(call) for name, call in CASES.items()}


def _as_saved(recorded):
    """Round-trip through JSON so that tuples and lists compare the way the file stores them."""
    return json.loads(json.dumps(recorded, sort_keys=True))


@pytest.mark.parametrize("name", sorted(CASES))
def test_a_change_definition_asks_the_dispatcher_what_it_always_has(name):
    saved = json.loads(SAVED.read_text(encoding="utf-8"))

    assert _as_saved(record(CASES[name])) == saved[name]


def test_the_saved_description_covers_every_case():
    saved = json.loads(SAVED.read_text(encoding="utf-8"))

    assert sorted(saved) == sorted(CASES)


if __name__ == "__main__":
    SAVED.parent.mkdir(parents=True, exist_ok=True)
    SAVED.write_text(
        json.dumps(_as_saved(record_all()), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"recorded {len(CASES)} cases in {SAVED}", file=sys.stderr)
