"""The size of a picture, and of its pixels, as last seen for each objective and optical configuration.

A workflow plans with the image size and the pixel size before anything is
captured, for example how far apart to place the tiles of an overview, and
the ZMART Controller asks every driver for both in ``get_state``. NIS only
tells them for an image that is open. So after every capture the driver
writes down what it saw, keyed by the objective and the optical
configuration it was taken with, and ``get_state`` answers from this record
when those are in place again, also in a later session.

The pixel size itself comes from NIS's calibration of each objective
(*Calibration* in NIS-Elements). An objective NIS has no calibration for is
recorded as uncalibrated, and the driver then refuses to capture with it,
because a picture without a pixel size cannot be placed on the sample.

The record is one small file, saved again only when something in it changes.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .. import store


def key(objective: str | None, optical_configuration: str | None) -> str:
    """How an entry is named: ``"<objective> | <optical configuration>"``."""
    return f"{objective or 'unknown objective'} | {optical_configuration or 'unknown configuration'}"


def entries(machine: store.MachineProfile | None = None) -> dict[str, dict]:
    """Every entry saved, by :func:`key`; empty when none is."""
    try:
        record = (machine or store.machine()).read("pixel_size")
    except (OSError, ValueError):
        return {}
    return dict((record or {}).get("entries") or {})


def lookup(
    objective: str | None, optical_configuration: str | None, *, machine=None
) -> dict | None:
    """The entry for this objective and optical configuration, or None when none was seen."""
    return entries(machine).get(key(objective, optical_configuration))


def remember(
    objective: str | None,
    optical_configuration: str | None,
    *,
    width: int,
    height: int,
    pixel_size_um: float | None,
    machine: store.MachineProfile | None = None,
) -> dict:
    """Record what a capture showed; saved only when it differs from what was recorded. The entry."""
    machine = machine or store.machine()
    known = entries(machine)
    entry: dict[str, Any] = {
        "objective": objective,
        "optical_configuration": optical_configuration,
        "image_size_px": [int(width), int(height)],
        "pixel_size_um": float(pixel_size_um) if pixel_size_um else None,
        "calibrated": bool(pixel_size_um),
    }
    name = key(objective, optical_configuration)
    before = {k: v for k, v in (known.get(name) or {}).items() if k != "seen"}
    if before != entry:
        known[name] = {
            **entry,
            "seen": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        machine.write("pixel_size", {"entries": known})
        return known[name]
    return known[name]
