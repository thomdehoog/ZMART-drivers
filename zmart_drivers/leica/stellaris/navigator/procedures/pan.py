"""The pan: where the galvo has moved LAS X's scan field, read, and set back to the centre.

LAS X's window moves the pan with its arrows, with no command LCS logs and
no value in LCS's state tree. LAS X keeps it in its own setting, where a
saved setting shows it, on the master: ``PanFirstDim`` (across) and
``PanSecondDim`` (down). A loaded setting does not set it, in any of its
modes, nor with the pan on Setting 1 (simulator, 2026-11-01). So the pan is
read from a setting saved now, and is not set to a chosen value.

It can be set back to the centre: at LAS X's smallest zoom the pan has no
room, LAS X pulls it to the centre, and it stays there when the zoom goes
back (simulator, 2026-11-01: from -0.000754 to -0.00000004 at zoom 0.75,
unchanged back at 4.177). Both zoom changes go through the limits and are
read back like any other.

A pan value is a fraction of the galvo's deflection, not a length. Thom de
Hoog measured, on the Navigator Expert route (March to May 2026), that the
largest pan, :data:`PAN_LIMIT`, shifts the sample by
:data:`GALVO_FIELD_FRACTION` of the objective's field of view at zoom 1. So
the shift in micrometres is the pan times that field times
``GALVO_FIELD_FRACTION / PAN_LIMIT``, the same at every zoom. One click of
the window's arrows is 3 % of the field shown (LCS's ``PanePercentage``):
17.4 micrometres with the 10x at zoom 2. The fraction depends on the
microscope's optics and should be measured on each one.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from . import readings as readers
from . import setting_files
from .changes import change

#: The largest pan LAS X allows in either direction.
PAN_LIMIT = 0.00775
#: The share of the objective's field at zoom 1 that the largest pan shifts the sample by (measured by Thom).
GALVO_FIELD_FRACTION = 0.667
#: How close to 0 a centred pan must read; with the 10x, 0.1 micrometres.
CENTRED = 1e-6

MASTER = re.compile(r"<LDM_Block_Sequential_Master>\s*<ATLConfocalSettingDefinition [^>]*>")


def micrometres_per_pan(base_fov_um: float) -> float:
    """How far the sample moves, in micrometres, per unit of pan, for an objective whose field at zoom 1 is *base_fov_um*."""
    return base_fov_um * GALVO_FIELD_FRACTION / PAN_LIMIT


def pan_in(text: str) -> tuple[float, float]:
    """The pan a saved setting holds on its master, across and down; LookupError when it holds none."""
    found = MASTER.search(text)
    head = found[0] if found else ""
    values = dict(re.findall(r'(PanFirstDim|PanSecondDim)="([^"]*)"', head))
    if set(values) != {"PanFirstDim", "PanSecondDim"}:
        raise LookupError("the saved setting holds no pan on its master")
    return float(values["PanFirstDim"]), float(values["PanSecondDim"])


def read(client, folder) -> dict:
    """The pan now: ``x`` and ``y`` as LAS X holds them, and ``x_um`` and ``y_um``, the field's shift in micrometres.

    Read from a setting saved now into *folder*. RuntimeError when LAS X
    does not save it.
    """
    Path(folder).mkdir(parents=True, exist_ok=True)
    path = Path(folder) / f"setting_{time.strftime('%Y%m%d-%H%M%S')}_pan.seq"
    saved = setting_files.save(client, path)
    if not saved.get("success"):
        raise RuntimeError(f"the pan cannot be read: {saved.get('message')}")
    x, y = pan_in(path.read_text(encoding="utf-8"))
    zoom = readers.get_zoom(client)["current"]
    base_fov_um = readers.get_fov(client)[0] * 1e6 * zoom
    scale = micrometres_per_pan(base_fov_um)
    return {"x": x, "y": y, "x_um": x * scale, "y_um": y * scale}


def center(client, folder) -> dict:
    """Set the pan back to the centre: LAS X's smallest zoom, then the zoom it had, then the pan read back.

    ``confirmed`` when the zoom is back and the pan reads 0. When the
    smallest zoom is refused, nothing has changed. When the way back is
    refused, the zoom stays at the smallest and the message says so.
    """
    zoom = readers.get_zoom(client)
    smallest = change(client, "zoom", zoom["min"])
    if not smallest.get("success"):
        return smallest
    back = change(client, "zoom", zoom["current"])
    if not back.get("success"):
        back["message"] = f"the zoom stays at {zoom['min']:g}, not back at {zoom['current']:g}: {back.get('message')}"
        return back
    now = read(client, folder)
    centred = abs(now["x"]) <= CENTRED and abs(now["y"]) <= CENTRED
    result = dict(back, pan=now, confirmed=bool(back.get("confirmed")) and centred)
    if not centred:
        result["message"] = f"the zoom is back, but the pan did not come to the centre: {now['x']:g}, {now['y']:g}"
    return result
