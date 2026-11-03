"""LAS X's image viewer: bleach points and ROI regions drawn with the mouse, as the operator draws them.

LCS, LAS X's hardware server, gets nothing when a bleach point or an ROI
region is drawn: LAS X keeps their places in its data container (simulator,
2026-11-03). So the driver draws them as the operator does, with the mouse,
in LAS X's viewer, with the viewer's own tools (Draw point, Draw rectangle,
Draw ellipse) and clears them with its Select all ROIs and Clear selected
ROIs.

Where to click is found, not guessed. The viewer's status line shows where in
the picture the pointer is ("Cursor: Pos: 584.49 µm, 584.12 µm", from the
picture's top-left corner) and how large the picture is ("x = 512 y = 512",
"Size: 1162.50 µm x 1162.50 µm"). The pointer is put on two places to learn
how the screen maps onto the picture, then on the target; the click is made
only when the status line shows the target there, within half a pixel of the
picture. When the viewer shows the picture so small that a screen pixel is
larger than a picture pixel, the drawing is refused: the viewer is zoomed in,
or shows one picture instead of one per channel, first. A region is drawn
with a click at one corner and one at the other: a drag left it unfinished,
and LAS X dropped it when the tool changed (simulator, 2026-11-03). Positions are pixels of the picture LAS X's viewer shows (x to the
right, y down, from 0).

The mouse's moves and clicks are sent to LAS X's window as messages
(simulator, 2026-11-03: the status line followed them, and a point was drawn
with them); the computer's pointer is not moved, so whoever uses the
computer meanwhile is not disturbed, as long as they leave LAS X's viewer
tools alone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import time

from . import answer_dialogs
from .lasx_window import MainWindow

#: The viewer's tools, by the help text LAS X gives them.
TOOLS = {"point": "Draw point", "rectangle": "Draw rectangle", "ellipse": "Draw ellipse"}
SELECT_ALL, CLEAR_SELECTED = "Select all ROIs", "Clear selected ROIs"

#: The status line, as LAS X writes it (simulator, 2026-11-03).
CURSOR = re.compile(r"Cursor:\s+Pos:\s+(-?[\d.]+) µm, (-?[\d.]+) µm")
PIXELS = re.compile(r"^(.*?)\s+x = (\d+) y = (\d+)")
EXTENT = re.compile(r"Size: ([\d.]+) µm x ([\d.]+) µm")

#: The screen pixels between the two places that learn the mapping, and how long the status line takes to follow.
STEP = 40
SETTLE_S = 0.25


def cursor_in(texts) -> tuple[float, float] | None:
    """Where the status line among *texts* shows the pointer, in µm from the picture's corner; None when it shows none."""
    for text in texts:
        found = CURSOR.search(text)
        if found:
            return float(found.group(1)), float(found.group(2))
    return None


def picture_in(texts) -> dict:
    """The picture the status line among *texts* describes: its ``name`` (``Preview`` for the last scan,
    ``Image001`` ...), ``pixels`` (x, y) and ``size_um`` (x, y)."""
    pixels = next((PIXELS.search(t) for t in texts if PIXELS.search(t)), None)
    extent = next((EXTENT.search(t) for t in texts if EXTENT.search(t)), None)
    if pixels is None or extent is None:
        raise answer_dialogs.WindowsUnavailable("LAS X's viewer shows no picture (its status line has no size)")
    return {"name": pixels.group(1).strip(), "pixels": (int(pixels.group(2)), int(pixels.group(3))),
            "size_um": (float(extent.group(1)), float(extent.group(2)))}


class Viewer:
    """LAS X's viewer: its status line, its toolbar, and the mouse over it."""

    def __init__(self, window=None):
        self.window = window if window is not None else MainWindow()

    def texts(self) -> list[str]:
        c = self.window.client
        found = self.window.element.FindAll(
            c.TreeScope_Descendants, self.window.uia.CreatePropertyCondition(c.UIA_ControlTypePropertyId, c.UIA_TextControlTypeId)
        )
        return [found.GetElement(i).CurrentName or "" for i in range(found.Length)]

    def picture(self) -> dict:
        return picture_in(self.texts())

    def area(self) -> tuple[int, int, int, int]:
        """The viewer's image area on the screen: left, top, right, bottom."""
        core = self.window.first(self.window.client.UIA_ClassNamePropertyId, "ImageViewerCore")
        r = core.CurrentBoundingRectangle
        return r.left, r.top, r.right, r.bottom

    def send(self, message: int, at: tuple[int, int], held: int = 0) -> None:
        """A mouse message to LAS X's window, as if the mouse were at the screen point *at*; the pointer is not moved."""
        import win32api
        import win32gui

        hwnd = self.window.element.CurrentNativeWindowHandle
        x, y = win32gui.ScreenToClient(hwnd, (int(at[0]), int(at[1])))
        win32gui.PostMessage(hwnd, message, held, win32api.MAKELONG(x & 0xFFFF, y & 0xFFFF))

    def hover(self, x: int, y: int) -> tuple[float, float] | None:
        """The status line's position with the mouse at (*x*, *y*) for LAS X; None when it shows none, or does not follow.

        A reading that stays what it was after the mouse moved is stale, and
        is waited on for a while, then taken for none.
        """
        import win32con

        here, before = (int(x), int(y)), cursor_in(self.texts())
        stayed = here == getattr(self, "last", None)
        self.last = here
        self.send(win32con.WM_MOUSEMOVE, here)
        reading = None
        for _ in range(int(1.5 / SETTLE_S)):
            time.sleep(SETTLE_S)
            reading = cursor_in(self.texts())
            if reading is not None and (reading != before or stayed):
                return reading
        return None

    def button(self, help_text: str):
        return self.window.first(self.window.client.UIA_HelpTextPropertyId, help_text)

    def tool_on(self, help_text: str) -> None:
        tool = self.button(help_text)
        if not self.window.is_enabled(tool):
            raise answer_dialogs.WindowsUnavailable(f"LAS X has {help_text} greyed out")
        if not self.window.is_on(tool):
            self.window.toggle(tool)

    def is_enabled(self, help_text: str) -> bool:
        return self.window.is_enabled(self.button(help_text))

    def press(self, help_text: str) -> None:
        self.window.press(self.button(help_text))
        time.sleep(SETTLE_S)

    def click(self, x: int, y: int) -> None:
        """A left click at the screen point (*x*, *y*), sent to LAS X's window."""
        import win32con

        self.send(win32con.WM_MOUSEMOVE, (x, y))
        time.sleep(0.1)
        self.send(win32con.WM_LBUTTONDOWN, (x, y), win32con.MK_LBUTTON)
        time.sleep(0.08)
        self.send(win32con.WM_LBUTTONUP, (x, y))
        time.sleep(SETTLE_S)

    def span(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """A shape from *start* to *end*, as LAS X's region tools take it: a click at one corner, the mouse moved
        across, a click at the other (simulator, 2026-11-03: a drag left the shape unfinished, and LAS X dropped it
        when the tool changed)."""
        import win32con

        self.click(*start)
        for i in range(1, 11):
            self.send(win32con.WM_MOUSEMOVE, (start[0] + (end[0] - start[0]) * i // 10, start[1] + (end[1] - start[1]) * i // 10))
            time.sleep(0.03)
        self.click(*end)


def target_um(viewer, x_px, y_px) -> tuple[float, float, float]:
    """The centre of the picture's pixel (*x_px*, *y_px*) in µm from its corner, and half a pixel; ValueError outside it."""
    shown = viewer.picture()
    (nx, ny), (width, height) = shown["pixels"], shown["size_um"]
    for name, value, count in (("x", x_px, nx), ("y", y_px, ny)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value < count:
            raise ValueError(f"{name} is a whole pixel of the {nx} x {ny} picture LAS X's viewer shows, 0 to {count - 1}, not {value!r}")
    step_x, step_y = width / nx, height / ny
    return (x_px + 0.5) * step_x, (y_px + 0.5) * step_y, min(step_x, step_y) / 2


def locate(viewer, x_px: int, y_px: int) -> tuple[int, int]:
    """The screen point over the picture's pixel (*x_px*, *y_px*), checked on the status line; LookupError when not found."""
    x_um, y_um, half = target_um(viewer, x_px, y_px)
    left, top, right, bottom = viewer.area()
    middle_x, middle_y = (left + right) // 2, (top + bottom) // 2
    width, height = (right - left) // 4, (bottom - top) // 4
    starts = [(middle_x + dx * width, middle_y + dy * height) for dx, dy in ((0, 0), (0, -1), (0, 1), (-1, 0), (1, 0), (-1, -1), (1, 1))]
    for x0, y0 in starts:
        first, second = viewer.hover(x0, y0), viewer.hover(x0 + STEP, y0 + STEP)
        if first is None or second is None:
            continue
        scale_x, scale_y = (second[0] - first[0]) / STEP, (second[1] - first[1]) / STEP
        if scale_x <= 0 or scale_y <= 0:
            continue
        if max(scale_x, scale_y) > 2 * half:
            raise LookupError(
                f"LAS X's viewer shows the picture at {max(scale_x, scale_y):.2f} µm a screen pixel, more than a picture "
                f"pixel ({2 * half:.2f} µm); zoom the viewer in, or show one picture instead of one per channel (its partition "
                "and overlay buttons), so a pixel can be hit"
            )
        guess = (round(x0 + (x_um - first[0]) / scale_x), round(y0 + (y_um - first[1]) / scale_y))
        for _ in range(3):
            if not (left <= guess[0] < right and top <= guess[1] < bottom):
                break
            seen = viewer.hover(*guess)
            if seen is None:
                break
            if abs(seen[0] - x_um) <= half and abs(seen[1] - y_um) <= half:
                return guess
            guess = (round(guess[0] + (x_um - seen[0]) / scale_x), round(guess[1] + (y_um - seen[1]) / scale_y))
    raise LookupError(f"pixel ({x_px}, {y_px}) was not found in LAS X's viewer (is it shown, and not panned away?)")


def draw_point(x_px: int, y_px: int, *, viewer=None) -> dict:
    """A bleach point at the picture's pixel (*x_px*, *y_px*), as the viewer's Draw point and one click draw it."""
    viewer = viewer if viewer is not None else Viewer()
    target_um(viewer, x_px, y_px)
    viewer.tool_on(TOOLS["point"])
    at = locate(viewer, x_px, y_px)
    viewer.click(*at)
    return {"success": True, "at_px": (x_px, y_px)}


def draw_region(shape: str, corner, opposite, *, viewer=None) -> dict:
    """An ROI region, a ``"rectangle"`` or an ``"ellipse"`` within the box from pixel *corner* to pixel *opposite*,
    as the viewer's tool and a click at each corner draw it. ``regions`` is whether the viewer has regions afterwards (its Select all
    ROIs is enabled); LAS X shows no count."""
    if shape not in ("rectangle", "ellipse"):
        raise ValueError(f"an ROI region is a 'rectangle' or an 'ellipse', not {shape!r}")
    viewer = viewer if viewer is not None else Viewer()
    (x0, y0), (x1, y1) = corner, opposite
    target_um(viewer, x0, y0), target_um(viewer, x1, y1)
    if x0 == x1 or y0 == y1:
        raise ValueError(f"the box from {corner} to {opposite} has no area")
    viewer.tool_on(TOOLS[shape])
    start, end = locate(viewer, x0, y0), locate(viewer, x1, y1)
    viewer.span(start, end)
    return {"success": True, "regions": viewer.is_enabled(SELECT_ALL)}


def clear_regions(*, viewer=None) -> dict:
    """Clear every ROI region and bleach point drawn, as the viewer's Select all ROIs and Clear selected ROIs do."""
    viewer = viewer if viewer is not None else Viewer()
    if viewer.is_enabled(SELECT_ALL):
        viewer.press(SELECT_ALL)
        if viewer.is_enabled(CLEAR_SELECTED):
            viewer.press(CLEAR_SELECTED)
    return {"success": True, "confirmed": not viewer.is_enabled(SELECT_ALL)}
