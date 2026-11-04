"""A stand-in for LAS X's image viewer: whether ROI regions are drawn (its Select all ROIs enabled), and the Bleach
Point panel's time boxes.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from zmart_drivers.leica.stellaris.navigator.procedures import lasx_viewer


class Viewer:
    """LAS X's viewer: *regions* whether ROI regions are drawn, and the Bleach Point panel's boxes."""

    def __init__(self, regions=False):
        self.boxes = {name: "0" for name in ("TextDays", "TextHours", "TextMinutes", "TextSeconds", "TextMilliseconds")}
        self.regions = regions

    def box(self, automation_id):
        return self.boxes[automation_id]

    def set_box(self, automation_id, text):
        self.boxes[automation_id] = text

    def is_enabled(self, help_text):
        return self.regions if help_text == lasx_viewer.SELECT_ALL else True
