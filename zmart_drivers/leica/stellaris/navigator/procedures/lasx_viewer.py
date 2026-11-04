"""LAS X's image viewer: whether ROI regions are drawn, and a bleach point's bleaching time.

Bleach points and ROI regions are added, read and cleared through LAS X's data
container (``primitives/direct/data_container.py``), with no window. Two things
are still read or set in LAS X's window: whether ROI regions are drawn (the
viewer's Select all ROIs is enabled then), which keeps the pan and the
objective from moving them across the sample, and the bleaching time the
Bleach Point panel shows, which LCS does not hold.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import time

from .lasx_window import MainWindow

#: The Bleach Point panel's time boxes, and how many seconds each counts.
TIME_BOXES = (("TextDays", 86400), ("TextHours", 3600), ("TextMinutes", 60), ("TextSeconds", 1), ("TextMilliseconds", 0.001))
#: The viewer's button that is enabled while ROI regions are drawn.
SELECT_ALL = "Select all ROIs"


class Viewer:
    """LAS X's viewer: its toolbar and the Bleach Point panel's boxes."""

    def __init__(self, window=None):
        self.window = window if window is not None else MainWindow()

    def button(self, help_text: str):
        return self.window.first(self.window.client.UIA_HelpTextPropertyId, help_text)

    def is_enabled(self, help_text: str) -> bool:
        return self.window.is_enabled(self.button(help_text))

    def box(self, automation_id: str) -> str:
        """The text in the window's box *automation_id*."""
        found = self.window.control(automation_id)
        pattern = found.GetCurrentPattern(self.window.client.UIA_ValuePatternId)
        return pattern.QueryInterface(self.window.client.IUIAutomationValuePattern).CurrentValue

    def set_box(self, automation_id: str, text: str) -> None:
        """Type *text* into the window's box *automation_id*, as the operator does: LAS X takes it when the box loses
        focus, so the focus moves to another box after it; no keys are sent."""
        c = self.window.client
        found = self.window.control(automation_id)
        found.SetFocus()
        found.GetCurrentPattern(c.UIA_ValuePatternId).QueryInterface(c.IUIAutomationValuePattern).SetValue(text)
        time.sleep(0.2)
        others = self.window.element.FindAll(c.TreeScope_Descendants, self.window.uia.CreatePropertyCondition(c.UIA_ControlTypePropertyId, c.UIA_EditControlTypeId))
        for i in range(others.Length):
            other = others.GetElement(i)
            if other.CurrentIsEnabled and not self.window.uia.CompareElements(other, found):
                other.SetFocus()
                break
        time.sleep(0.2)


def bleach_time(*, viewer=None) -> float:
    """The bleaching time of the bleach point the Bleach Point panel shows, in seconds, from its boxes."""
    viewer = viewer if viewer is not None else Viewer()
    return sum(float(viewer.box(name) or 0) * seconds for name, seconds in TIME_BOXES)


def set_bleach_time(seconds, *, viewer=None) -> dict:
    """The bleaching time of the bleach point the Bleach Point panel shows, typed into its boxes (days to milliseconds),
    as the operator does; read back from them. LCS holds no bleaching time: LAS X keeps it with the point."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not seconds > 0:
        raise ValueError(f"the bleaching time is a number of seconds above 0, not {seconds!r}")
    viewer = viewer if viewer is not None else Viewer()
    left = round(seconds * 1000)  # in milliseconds, the panel's finest
    for name, unit in TIME_BOXES:
        count, left = divmod(left, round(unit * 1000))
        viewer.set_box(name, str(int(count)))
    shown = bleach_time(viewer=viewer)
    return {"success": True, "confirmed": abs(shown - seconds) < 0.0005, "seconds": shown}
