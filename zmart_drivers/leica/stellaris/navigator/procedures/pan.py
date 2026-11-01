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

And it can be moved by a distance, the way the operator moves it: with the
arrows of the window's pan pad, pressed through Windows UI Automation, as
the driver presses OK on LAS X's message boxes (Thom chose this,
2026-11-01). A press moves the pan by LCS's ``PanePercentage`` (3) percent
of the field shown, so the driver first raises the zoom just enough that a
whole number of presses lands on the distance, presses, and sets the zoom
back. It never lowers the zoom below the one it had: a lower zoom pulls the
pan towards the centre. The pad's nine buttons have no names; they are told
apart by where they sit on it.

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

import math
import re
import time
from pathlib import Path

from . import answer_dialogs, setting_files
from . import readings as readers
from .changes import change

#: The largest pan LAS X allows in either direction.
PAN_LIMIT = 0.00775
#: The share of the objective's field at zoom 1 that the largest pan shifts the sample by (measured by Thom).
GALVO_FIELD_FRACTION = 0.667
#: How close to 0 a centred pan must read; with the 10x, 0.1 micrometres.
CENTRED = 1e-6
#: How close a moved pan must come to the distance asked, in micrometres.
MOVE_TOLERANCE_UM = 0.5
#: A distance shorter than this, in micrometres, is not moved.
SMALLEST_MOVE_UM = 0.01
#: The arrow that moves the pan each way along each axis: across to the right is positive, as is up.
ARROWS = {("x", 1): "right", ("x", -1): "left", ("y", 1): "up", ("y", -1): "down"}

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
    scale = micrometres_per_pan(base_fov_um(client))
    return {"x": x, "y": y, "x_um": x * scale, "y_um": y * scale}


def base_fov_um(client) -> float:
    """The objective's field of view at zoom 1, across, in micrometres: the field shown times the zoom."""
    return readers.get_fov(client)[0] * 1e6 * readers.get_zoom(client)["current"]


def click_um(client, zoom: float) -> float:
    """How far one press of an arrow moves the pan at *zoom*, in micrometres: LCS's ``PanePercentage`` of the field shown."""
    percent = readers.get_pane_percentage(client)
    return percent / 100 * base_fov_um(client) / zoom


class WindowPad:
    """The pan pad in LAS X's XY panel, through Windows UI Automation: its arrows pressed by direction."""

    def __init__(self):
        self.client, self.uia = answer_dialogs.automation()
        self.buttons = self.find()

    def find(self) -> dict:
        """The pad's buttons by where they sit: ``right``, ``left``, ``up``, ``down``, the diagonals and ``centre``."""
        c, uia = self.client, self.uia
        tops = uia.GetRootElement().FindAll(c.TreeScope_Children, uia.CreateTrueCondition())
        for i in range(tops.Length):
            window = tops.GetElement(i)
            if window.CurrentName and answer_dialogs.process_name(window.CurrentProcessId) == "LMSApplication.exe":
                break
        else:
            raise answer_dialogs.WindowsUnavailable("LAS X's window was not found")
        pad = window.FindFirst(c.TreeScope_Descendants, uia.CreatePropertyCondition(c.UIA_ClassNamePropertyId, "ScanPanControl"))
        if pad is None:
            raise answer_dialogs.WindowsUnavailable("LAS X's window shows no pan pad (the XY panel is closed?)")
        edge = pad.CurrentBoundingRectangle
        middle = ((edge.left + edge.right) / 2, (edge.top + edge.bottom) / 2)
        found = pad.FindAll(c.TreeScope_Descendants, uia.CreatePropertyCondition(c.UIA_ControlTypePropertyId, c.UIA_ButtonControlTypeId))
        buttons = {}
        for i in range(found.Length):
            button = found.GetElement(i)
            box = button.CurrentBoundingRectangle
            across = (box.left + box.right) / 2 - middle[0]
            down = (box.top + box.bottom) / 2 - middle[1]
            vertical = "" if abs(down) < 8 else ("down" if down > 0 else "up")
            horizontal = "" if abs(across) < 8 else ("right" if across > 0 else "left")
            buttons["-".join(part for part in (vertical, horizontal) if part) or "centre"] = button
        if not {"right", "left", "up", "down"} <= set(buttons):
            raise answer_dialogs.WindowsUnavailable(f"the pan pad's arrows were not found, only {sorted(buttons)}")
        return buttons

    def press(self, arrow: str) -> None:
        pattern = self.buttons[arrow].GetCurrentPattern(self.client.UIA_InvokePatternId)
        pattern.QueryInterface(self.client.IUIAutomationInvokePattern).Invoke()


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


def move(client, folder, x_um: float, y_um: float, *, pad=None) -> dict:
    """Move the pan by *x_um* across (right positive) and *y_um* up (down negative), and read it back.

    Refused before anything is pressed when the pan would go beyond LAS X's
    largest. For each axis the zoom is raised just enough that a whole
    number of presses lands on the distance, the arrow is pressed, and at
    the end the zoom is set back. ``confirmed`` when the pan moved by the
    distance asked, within :data:`MOVE_TOLERANCE_UM`.
    """
    before = read(client, folder)
    scale = micrometres_per_pan(base_fov_um(client))
    for axis, distance in (("x", x_um), ("y", y_um)):
        if abs(before[axis] + distance / scale) > PAN_LIMIT:
            return {
                "success": False,
                "confirmed": False,
                "message": f"a pan {distance:g} um further along {axis} goes beyond LAS X's largest pan "
                f"({PAN_LIMIT * scale:.0f} um from the centre with this objective); nothing was pressed",
            }
    pad = pad if pad is not None else WindowPad()
    zoom = readers.get_zoom(client)
    start = zoom["current"]
    try:
        for axis, distance in (("x", x_um), ("y", y_um)):
            if abs(distance) < SMALLEST_MOVE_UM:
                continue
            presses = math.ceil(abs(distance) / click_um(client, start) - 1e-9)
            wanted = start * presses * click_um(client, start) / abs(distance)
            if wanted > zoom["max"]:
                wanted = zoom["max"]
                presses = round(abs(distance) / click_um(client, wanted))
            if abs(wanted - readers.get_zoom(client)["current"]) > 1e-9:
                raised = change(client, "zoom", wanted)
                if not raised.get("success"):
                    return raised
                presses = round(abs(distance) / click_um(client, readers.get_zoom(client)["current"]))
            for _ in range(presses):
                pad.press(ARROWS[(axis, 1 if distance > 0 else -1)])
                time.sleep(0.2)
    finally:
        if abs(readers.get_zoom(client)["current"] - start) > 1e-9:
            back = change(client, "zoom", start)
    now = read(client, folder)
    moved = (now["x_um"] - before["x_um"], now["y_um"] - before["y_um"])
    landed = abs(moved[0] - x_um) <= MOVE_TOLERANCE_UM and abs(moved[1] - y_um) <= MOVE_TOLERANCE_UM
    zoom_back = abs(readers.get_zoom(client)["current"] - start) <= 1e-6
    result = {"success": zoom_back, "confirmed": landed and zoom_back, "pan": now, "moved_um": moved}
    if not zoom_back:
        result["message"] = f"the zoom did not go back to {start:g}: {back.get('message')}"
    elif not landed:
        result["message"] = f"the pan moved {moved[0]:.2f}, {moved[1]:.2f} um, not {x_um:g}, {y_um:g} um"
    return result
