"""A stand-in for LAS X's image viewer: the screen mapped onto the picture as LAS X's viewer did on the simulator
(2026-11-03: 1.756 µm a screen pixel, a 512 x 512 picture of 1162.5 µm), the status line's cursor position answered for a
place on the screen, and the clicks, drags and toolbar presses recorded.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from zmart_drivers.leica.stellaris.navigator.procedures import lasx_viewer


class Viewer:
    """LAS X's viewer: the picture at *origin* on the screen, *scale* µm a screen pixel; *gap* a band of rows
    (between two channel panels, say) where the status line shows no position."""

    def __init__(self, scale=1.756, origin=(-700.0, 220.0), pixels=512, size_um=1162.5, gap=None, regions=False):
        self.scale, self.origin, self.pixels, self.size_um, self.gap = scale, origin, pixels, size_um, gap
        self.clicks, self.drags, self.pressed, self.tools, self.right_clicks = [], [], [], set(), []
        self.boxes = {name: "0" for name in ("TextDays", "TextHours", "TextMinutes", "TextSeconds", "TextMilliseconds")}
        self.regions = regions
        self.on_click = None

    def picture(self):
        return {"name": "Preview", "pixels": (self.pixels, self.pixels), "size_um": (self.size_um, self.size_um)}

    def area(self):
        return (-734, 204, -70, 1136)

    def at(self, x, y):
        if self.gap and self.gap[0] <= y <= self.gap[1]:
            return None
        um = ((x - self.origin[0]) * self.scale, (y - self.origin[1]) * self.scale)
        return um if all(0 <= v <= self.size_um for v in um) else None

    def hover(self, x, y):
        return self.at(x, y)

    def tool_on(self, help_text):
        self.tools.add(help_text)

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.on_click:
            self.on_click()

    def right_click(self, x, y):
        self.right_clicks.append((x, y))
        self.regions = True

    def box(self, automation_id):
        return self.boxes[automation_id]

    def set_box(self, automation_id, text):
        self.boxes[automation_id] = text

    def span(self, start, end):
        self.drags.append((start, end))
        self.regions = True

    def is_enabled(self, help_text):
        return self.regions if help_text == lasx_viewer.SELECT_ALL else True

    def press(self, help_text):
        self.pressed.append(help_text)
        if help_text == lasx_viewer.CLEAR_SELECTED:
            self.regions = False
