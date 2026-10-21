"""Pins what each change definition asks the dispatcher to do.

Every setting the driver can change (zoom, scan speed, laser intensity, ...) is
defined once: which LAS X request to fill in, in which order, what to call the
change in the log, which profile governs its retries and waits, how the result is
checked, and which values the limits gate is shown. These tests call each
definition with sample values and compare that whole description with the one
saved in ``data/change_definitions.json``.

The saved description was recorded from the code before the definitions were
moved into a table, so a change in any entry shows up here as a plain difference.
Run this file as a script to record the description again after a change that is
meant to alter one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
        University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
"""

import json
import sys
from functools import partial
from pathlib import Path

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.procedures.objective_change_with_compensation import compensated_changes
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitive_handling import changes, profiles

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


def _profile_name(profile):
    for name in dir(profiles):
        if getattr(profiles, name) is profile:
            return name
    return repr(profile)


def _describe_callable(fn):
    if fn is None:
        return None
    if isinstance(fn, partial):
        return {"function": fn.func.__name__, "keywords": dict(fn.keywords or {})}
    return {"function": getattr(fn, "__name__", repr(fn))}


class _AllChanges:
    """Every named change: the single commands, and the two that correct the stage."""

    def __getattr__(self, name):
        if name in ("set_objective", "select_job"):
            return getattr(compensated_changes, name)
        return getattr(changes, name)


def record(call):
    """Run ``call(changes, client)`` and describe everything it asked of the dispatcher."""
    asked = {"gate": [], "dispatch": None, "result": None}
    client = RecordingClient()

    def gate(_client, command, values, _setup_hint=None):
        asked["gate"].append([command, values])
        return None

    def dispatch(_client, api_obj, description, profile, **kw):
        model = RecordingModel()
        setup = kw.get("setup_fn")
        if setup is not None:
            setup(model)
        asked["dispatch"] = {
            "request": api_obj.name,
            "description": description,
            "profile": _profile_name(profile),
            "writes": model.writes,
            "confirm": _describe_callable(kw.get("confirm_fn")),
            "error_check": _describe_callable(kw.get("error_check_fn")),
            "max_retries": kw.get("max_retries"),
        }
        return {"success": True, "confirmed": True, "message": description, "logs": []}

    original = (changes._gate.check_refusal, changes.dispatch_change)
    changes._gate.check_refusal, changes.dispatch_change = gate, dispatch
    try:
        result = call(_AllChanges(), client)
    finally:
        changes._gate.check_refusal, changes.dispatch_change = original
    asked["result"] = (
        {"success": result.get("success"), "message": result.get("message")}
        if asked["dispatch"] is None
        else "from the dispatcher"
    )
    return asked


CASES = {
    "zoom": lambda c, k: c.set_zoom(k, "J", 1.5),
    "zoom_with_overrides": lambda c, k: c.set_zoom(
        k, "J", 1.5, max_retries=2, tolerance=0.01
    ),
    "scan_speed": lambda c, k: c.set_scan_speed(k, "J", 400),
    "scan_resonant_on": lambda c, k: c.set_scan_resonant(k, "J", True),
    "scan_mode": lambda c, k: c.set_scan_mode(k, "J", "xyz"),
    "sequential_frame": lambda c, k: c.set_sequential_mode(k, "J", "Frame"),
    "sequential_unknown": lambda c, k: c.set_sequential_mode(k, "J", "Bogus"),
    "sequential_empty": lambda c, k: c.set_sequential_mode(k, "J", " "),
    "scan_field_rotation": lambda c, k: c.set_scan_field_rotation(
        k, "J", 12.5, tolerance=0.2
    ),
    "image_format_text": lambda c, k: c.set_image_format(k, "J", "512 x 512"),
    "image_format_tuple": lambda c, k: c.set_image_format(k, "J", (256, 128)),
    "z_stack_definition_both": lambda c, k: c.set_z_stack_definition(k, "J", -5.0, 5.0),
    "z_stack_definition_zero_begin": lambda c, k: c.set_z_stack_definition(
        k, "J", begin_um=0.0
    ),
    "z_stack_definition_reset": lambda c, k: c.set_z_stack_definition(
        k, "J", None, None, old_begin_um=1, old_end_um=1
    ),
    "z_stack_step_size": lambda c, k: c.set_z_stack_step_size(k, "J", 0.5),
    "z_stack_size": lambda c, k: c.set_z_stack_size(k, "J", 20.0),
    "frame_accumulation": lambda c, k: c.set_frame_accumulation(k, "J", 1, 3),
    "frame_average": lambda c, k: c.set_frame_average(k, "J", 1, 2),
    "line_accumulation": lambda c, k: c.set_line_accumulation(k, "J", 0, 4),
    "line_average": lambda c, k: c.set_line_average(k, "J", 0, 2),
    "pinhole_airy": lambda c, k: c.set_pinhole_airy(k, "J", 0, 1.0, tolerance=0.05),
    "detector_gain": lambda c, k: c.set_detector_gain(k, "J", 0, "HyD1", 55.0),
    "laser_intensity": lambda c, k: c.set_laser_intensity(k, "J", 0, "R1", 2, 0.25),
    "laser_shutter_open": lambda c, k: c.set_laser_shutter(k, "J", 0, "R1", True),
    "laser_shutter_closed": lambda c, k: c.set_laser_shutter(k, "J", 0, "R1", False),
    "filter_wheel_slot": lambda c, k: c.set_filter_wheel_slot(k, "J", 0, "R1", 2, 3),
    "filter_wheel_spectrum": lambda c, k: c.set_filter_wheel_spectrum(
        k, "J", 0, "R1", 2, 520.0, tolerance=1.0
    ),
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
