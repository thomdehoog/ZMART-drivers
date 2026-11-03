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

from ..primitives.direct import scanner
from . import answer_dialogs, lasx_window, setting_files
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


def click_um(client, zoom: float, percent: float | None = None) -> float:
    """How far one press of an arrow moves the pan at *zoom*, in micrometres: *percent* of the field shown, LCS's
    ``PanePercentage`` (the window's arrows) when not given."""
    percent = percent if percent is not None else readers.get_pane_percentage(client)
    return percent / 100 * base_fov_um(client) / zoom


class WindowPad:
    """The pan pad in LAS X's XY panel, through Windows UI Automation: its arrows pressed by direction."""

    def __init__(self):
        self.window = lasx_window.MainWindow()
        self.buttons = self.find()

    def find(self) -> dict:
        """The pad's buttons by where they sit: ``right``, ``left``, ``up``, ``down``, the diagonals and ``centre``."""
        c, uia = self.window.client, self.window.uia
        pad = self.window.first(c.UIA_ClassNamePropertyId, "ScanPanControl")
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

    #: The window's arrows step by LCS's ``PanePercentage``.
    percent = None

    def press(self, arrow: str) -> None:
        self.window.press(self.buttons[arrow])

    def centre(self) -> None:
        """The pad's middle button, which centres the pan."""
        if "centre" not in self.buttons:
            raise answer_dialogs.WindowsUnavailable("the pan pad's middle button was not found")
        self.press("centre")

    def close(self) -> None:
        """Nothing to put back: the window's arrows keep LCS's step."""


def window_step(client) -> dict:
    """The step of the window's arrows: ``percent`` of the field shown (LCS's ``PanePercentage``), and ``um`` at the zoom now."""
    percent = readers.get_pane_percentage(client)
    return {"percent": percent, "um": click_um(client, readers.get_zoom(client)["current"], percent)}


class ScannerRefused(RuntimeError):
    """The pan's steps through LCS's COM scanner are refused here; nothing was sent."""


class ScannerPad:
    """The pan's steps through LCS's older COM scanner instead of the window's arrows: for the simulator only.

    Each step is *percent* (1 to 3, 1 by default) of the field shown
    (:mod:`..primitives.direct.scanner`); the window's arrows take 3 %. Refused unless LCS names the system the
    simulator, and while LAS X scans. The step LCS had is put back by
    :meth:`close`, which :func:`move` and :func:`center_by_scanner` call
    whatever happens. :func:`move` checks the first step against the formula.
    """

    checks_first_step = True

    def __init__(self, client, *, percent: int = 1, steps=None):
        system = readers.get_system(client)
        if not system["simulator"]:
            raise ScannerRefused(
                f"the pan through LCS's COM scanner is for the simulator only; LCS runs {system['serial']!r} "
                f"({system['hardware_tree']}); nothing was sent"
            )
        status = readers.get_scan_status(client)
        if status != "eScanIdle":
            raise ScannerRefused(f"LAS X is scanning ({status}); the pan is not stepped now; nothing was sent")
        self.steps = steps if steps is not None else scanner.PanSteps()
        self.before = self.steps.percent()
        self.percent = percent
        self.steps.set_percent(percent)

    def press(self, arrow: str) -> None:
        self.steps.step(arrow)

    def centre(self) -> None:
        self.steps.step("centre")

    def close(self) -> None:
        self.steps.set_percent(self.before, putting_back=True)


#: Where each of the pad's directions moves the field: across (right positive) and up (positive).
VECTORS = {"up-left": (-1, 1), "up": (0, 1), "up-right": (1, 1), "right": (1, 0), "down-right": (1, -1),
           "down": (0, -1), "down-left": (-1, -1), "left": (-1, 0)}


def step(client, folder, direction: str, count: int, pad) -> dict:
    """Step the pan *count* times toward *direction* (one of the pad's eight) with *pad*, or back to the ``"centre"``.

    The end is worked out first with the formula (the pad's percent of the
    field shown a step, at the zoom now) and refused beyond LAS X's largest
    pan; the first step is checked against the formula, and on a wrong one
    the pan is centred; the step LCS had is put back. ``confirmed`` when the
    pan reads the end, within :data:`MOVE_TOLERANCE_UM`.
    """
    if direction == "centre":
        return center_by_pad(client, folder, pad)
    try:
        if direction not in VECTORS:
            raise ValueError(f"the pan steps {sorted(VECTORS)} or to the 'centre', not {direction!r}")
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 100:
            raise ValueError(f"the steps are a whole number from 1 to 100, not {count!r}")
        before = read(client, folder)
        size = click_um(client, readers.get_zoom(client)["current"], pad.percent)
        across, up = VECTORS[direction]
        clipped = clip(client, before["x_um"] + across * count * size, before["y_um"] + up * count * size)
        end = clipped["to_um"]
        first = clip(client, before["x_um"] + across * size, before["y_um"] + up * size)["to_um"]
        expected = (first[0] - before["x_um"], first[1] - before["y_um"])
        for n in range(count):
            pad.press(direction)
            time.sleep(0.2)
            if n == 0:
                now = read(client, folder)
                moved = (now["x_um"] - before["x_um"], now["y_um"] - before["y_um"])
                if max(abs(moved[0] - expected[0]), abs(moved[1] - expected[1])) > max(0.2 * size, 0.05):
                    pad.centre()
                    return {"success": False, "confirmed": False, "pan": read(client, folder),
                            "message": f"the first step moved the pan {moved[0]:.3f}, {moved[1]:.3f} um, not the "
                            f"{expected[0]:.3f}, {expected[1]:.3f} um the formula gives; the pan was put back to the centre"}
    finally:
        pad.close()
    now = read(client, folder)
    landed = abs(now["x_um"] - end[0]) <= MOVE_TOLERANCE_UM and abs(now["y_um"] - end[1]) <= MOVE_TOLERANCE_UM
    result = {"success": True, "confirmed": landed, "pan": now, "step_um": size}
    if any(clipped["over_um"]):
        result["clipped"], result["message"] = clipped, clipped_note(clipped)
    if not landed:
        result["message"] = f"the pan is at {now['x_um']:.2f}, {now['y_um']:.2f} um, not {end[0]:.2f}, {end[1]:.2f} um"
    return result


def step_size(client, pad) -> dict:
    """The step the COM scanner held: ``percent``, and ``um``, how far a step moves the pan at the zoom now."""
    try:
        held = pad.before
    finally:
        pad.close()
    return {"percent": held, "um": click_um(client, readers.get_zoom(client)["current"], held)}


def center_by_pad(client, folder, pad) -> dict:
    """Set the pan back to the centre with the pad's middle button, the zoom untouched; the pad closed; read back."""
    try:
        pad.centre()
    finally:
        pad.close()
    now = read(client, folder)
    centred = abs(now["x"]) <= CENTRED and abs(now["y"]) <= CENTRED
    result = {"success": True, "confirmed": centred, "pan": now}
    if not centred:
        result["message"] = f"the pan did not come to the centre: {now['x']:g}, {now['y']:g}"
    return result


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


def set_to(client, folder, x_um: float, y_um: float, *, pad=None) -> dict:
    """Set the pan to *x_um* right and *y_um* up of the centre (negative: left, down), and read it back.

    The pan is read now and moved by the difference, with :func:`move`. A
    place beyond LAS X's window at the zoom now is brought to the closest
    place within it (:func:`clip`); the result's ``clipped`` says how far
    over it was. ``confirmed`` when the pan reads the place it went to,
    within :data:`MOVE_TOLERANCE_UM`.
    """
    now = read(client, folder)
    clipped = clip(client, x_um, y_um)
    to = clipped["to_um"]
    result = move(client, folder, to[0] - now["x_um"], to[1] - now["y_um"], pad=pad)
    if result.get("success") and "pan" in result:
        there = result["pan"]
        landed = abs(there["x_um"] - to[0]) <= MOVE_TOLERANCE_UM and abs(there["y_um"] - to[1]) <= MOVE_TOLERANCE_UM
        result["confirmed"] = bool(result.get("confirmed")) and landed
        if any(clipped["over_um"]):
            result["clipped"], result["message"] = clipped, clipped_note(clipped)
        if not landed:
            result["message"] = f"the pan is at {there['x_um']:.2f}, {there['y_um']:.2f} um, not {to[0]:.2f}, {to[1]:.2f} um"
    return result


def window_um(client, zoom: float | None = None) -> float:
    """How far from the centre LAS X lets the pan go on each axis at *zoom* (the zoom now when not given), in micrometres.

    LAS X keeps the galvo inside a window that grows with the zoom: the
    largest pan times ``1 - smallest zoom / zoom``, so none at the smallest
    zoom. Beyond it LAS X clips the pan to the edge without a word (simulator,
    2026-11-03: 0.001937 at zoom 1, 0.004844 at 2, 0.006297 at 4, as the
    formula gives). That clip is the scanner's guard. None with the
    resonant scanner, whose window shrank with the zoom on the simulator and
    is not known yet.
    """
    if readers.get_scan_speed(client).get("isResonant"):
        return None  # the resonant scanner's window follows another rule (simulator, 2026-11-03): not known yet
    zooms = readers.get_zoom(client)
    zoom = zooms["current"] if zoom is None else zoom
    return max(0.0, PAN_LIMIT * (1 - zooms["min"] / zoom)) * micrometres_per_pan(base_fov_um(client))


def clip(client, x_um: float, y_um: float) -> dict:
    """The closest place to *x_um*, *y_um* (micrometres from the centre) within LAS X's window at the zoom now.

    ``asked_um``, ``to_um`` (each axis brought to the window's edge when it
    is beyond), ``over_um`` (how far each axis was beyond; 0 within) and
    ``window_um``. The closest place is where LAS X itself would clip to.
    """
    window = window_um(client)
    if window is None:  # not known: LAS X clips, and the read-back after the move says where the pan ended
        return {"asked_um": (x_um, y_um), "to_um": (x_um, y_um), "over_um": (0.0, 0.0), "window_um": None}
    to = tuple(max(-window, min(window, value)) for value in (x_um, y_um))
    return {"asked_um": (x_um, y_um), "to_um": to, "over_um": (x_um - to[0], y_um - to[1]), "window_um": window}


def measure_window(client, folder, pad, *, zooms=(1.0, 2.0)) -> dict:
    """Measure LAS X's window and the arrows' step with *pad*, and compare them with the numbers in the driver.

    At each zoom the pan is centred, stepped once to the right (the step's
    size in pan units) and then on until LAS X stops it (the edge). The edge
    gives the largest pan, ``edge / (1 - smallest zoom / zoom)``; the step
    gives the field fraction, ``percent / 100 * largest / (zoom * step)``,
    which is how LAS X itself defines its arrows (on a sample it is checked
    with pictures). The pan is centred and the zoom set back afterwards.
    ``matches_the_code`` when both are within 1 % of :data:`PAN_LIMIT` and
    :data:`GALVO_FIELD_FRACTION`. Refused with the resonant scanner.
    """
    if readers.get_scan_speed(client).get("isResonant"):
        pad.close()
        return {"success": False, "confirmed": False, "message": "the window is measured with the galvo scanner, not the resonant one"}
    percent = pad.percent if pad.percent is not None else readers.get_pane_percentage(client)
    start = readers.get_zoom(client)
    measured = []
    try:
        for zoom in zooms:
            pad.centre()
            raised = change(client, "zoom", zoom)
            if not raised.get("success"):
                return raised
            before = read(client, folder)["x"]
            pad.press("right")
            time.sleep(0.2)
            step_pan = read(client, folder)["x"] - before
            expected = PAN_LIMIT * (1 - start["min"] / zoom)
            for _ in range(math.ceil(expected / step_pan) + 3 if step_pan > 0 else 0):
                pad.press("right")
                time.sleep(0.05)
            edge = read(client, folder)["x"]
            for _ in range(3):
                pad.press("right")
            stopped = abs(read(client, folder)["x"] - edge) <= 1e-9
            largest = edge / (1 - start["min"] / zoom)
            measured.append({"zoom": zoom, "step_pan": step_pan, "edge_pan": edge, "stopped": stopped, "largest_pan": largest,
                             "field_fraction": percent / 100 * largest / (zoom * step_pan) if step_pan > 0 else None})
    finally:
        pad.centre()
        if abs(readers.get_zoom(client)["current"] - start["current"]) > 1e-9:
            change(client, "zoom", start["current"])
        pad.close()
    largest = sum(m["largest_pan"] for m in measured) / len(measured)
    fraction = sum(m["field_fraction"] for m in measured) / len(measured)
    matches = (all(m["stopped"] for m in measured) and abs(largest / PAN_LIMIT - 1) <= 0.01
               and abs(fraction / GALVO_FIELD_FRACTION - 1) <= 0.01)
    return {"success": True, "confirmed": all(m["stopped"] for m in measured), "largest_pan": largest,
            "field_fraction": fraction, "matches_the_code": matches, "measured": measured,
            "in_the_code": {"largest_pan": PAN_LIMIT, "field_fraction": GALVO_FIELD_FRACTION}}


def clipped_note(clipped: dict) -> str:
    """What a clip did, in words."""
    (ax, ay), (tx, ty), window = clipped["asked_um"], clipped["to_um"], clipped["window_um"]
    return (f"{ax:g}, {ay:g} um is beyond the {window:.1f} um LAS X allows from the centre at this zoom; "
            f"the pan went to the closest place, {tx:.2f}, {ty:.2f} um")


def first_step_off(client, folder, before, axis, expected_um) -> str | None:
    """Why the first step was not the formula's (*expected_um* along *axis*, nothing across); None when it was."""
    now = read(client, folder)
    other = "y" if axis == "x" else "x"
    moved, across = now[f"{axis}_um"] - before[f"{axis}_um"], now[f"{other}_um"] - before[f"{other}_um"]
    if abs(moved - expected_um) > max(0.2 * abs(expected_um), 0.05) or abs(across) > 0.05:
        return f"the first step moved the pan {moved:.3f} um along {axis} and {across:.3f} um across, not the {expected_um:.3f} um the formula gives"
    return None


def move(client, folder, x_um: float, y_um: float, *, pad=None) -> dict:
    """Move the pan by *x_um* across (right positive) and *y_um* up (down negative), and read it back.

    A pan that would end beyond LAS X's window at the zoom now (the zoom it
    ends at) goes to the closest place within it instead (:func:`clip`), and
    the result's ``clipped`` says what was asked and how far over it was. For
    each axis the zoom is raised just enough that a whole number of presses
    lands on the distance, the arrow is pressed, and at the end the zoom is
    set back. ``confirmed`` when the pan moved by the distance it went,
    within :data:`MOVE_TOLERANCE_UM`.
    """
    before = read(client, folder)
    clipped = clip(client, before["x_um"] + x_um, before["y_um"] + y_um)
    x_um, y_um = clipped["to_um"][0] - before["x_um"], clipped["to_um"][1] - before["y_um"]
    pad = pad if pad is not None else WindowPad()
    percent = getattr(pad, "percent", None)
    check_first = getattr(pad, "checks_first_step", False)
    zoom = readers.get_zoom(client)
    start = zoom["current"]
    try:
        for axis, distance in (("x", x_um), ("y", y_um)):
            if abs(distance) < SMALLEST_MOVE_UM:
                continue
            presses = math.ceil(abs(distance) / click_um(client, start, percent) - 1e-9)
            wanted = start * presses * click_um(client, start, percent) / abs(distance)
            if wanted > zoom["max"]:
                wanted = zoom["max"]
                presses = round(abs(distance) / click_um(client, wanted, percent))
            if abs(wanted - readers.get_zoom(client)["current"]) > 1e-9:
                raised = change(client, "zoom", wanted)
                if not raised.get("success"):
                    return raised
                presses = round(abs(distance) / click_um(client, readers.get_zoom(client)["current"], percent))
            sign = 1 if distance > 0 else -1
            for _ in range(presses):
                pad.press(ARROWS[(axis, sign)])
                time.sleep(0.2)
                if check_first:
                    check_first = False
                    wrong = first_step_off(client, folder, before, axis, sign * click_um(client, readers.get_zoom(client)["current"], percent))
                    if wrong:
                        pad.centre()
                        return {"success": False, "confirmed": False, "pan": read(client, folder),
                                "message": f"{wrong}; the pan was put back to the centre and stepped no further"}
    finally:
        if abs(readers.get_zoom(client)["current"] - start) > 1e-9:
            back = change(client, "zoom", start)
        if hasattr(pad, "close"):
            pad.close()
    now = read(client, folder)
    moved = (now["x_um"] - before["x_um"], now["y_um"] - before["y_um"])
    landed = abs(moved[0] - x_um) <= MOVE_TOLERANCE_UM and abs(moved[1] - y_um) <= MOVE_TOLERANCE_UM
    zoom_back = abs(readers.get_zoom(client)["current"] - start) <= 1e-6
    result = {"success": zoom_back, "confirmed": landed and zoom_back, "pan": now, "moved_um": moved}
    if any(clipped["over_um"]):
        result["clipped"], result["message"] = clipped, clipped_note(clipped)
    if not zoom_back:
        result["message"] = f"the zoom did not go back to {start:g}: {back.get('message')}"
    elif not landed:
        result["message"] = f"the pan moved {moved[0]:.2f}, {moved[1]:.2f} um, not {x_um:g}, {y_um:g} um"
    return result
