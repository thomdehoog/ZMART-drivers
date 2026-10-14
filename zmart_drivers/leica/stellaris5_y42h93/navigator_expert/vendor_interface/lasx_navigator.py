"""LAS X's Navigator canvas, cleared so that LAS X lets go of the hidden copies it shows.

Every capture leaves a hidden copy in LAS X's cache (``TileScan <n>`` in an
experiment LAS X calls "Hidden": the pixels again, a zoom pyramid and a
histogram, about twice a 512 x 512 capture's pixels). The Navigator canvas
holds it while it shows the capture's tile: releasing the capture itself
(``output/lasx_cache_release``) leaves the copy, and pressing the canvas's
Clear button lets go of them all, while LAS X runs (simulator, 2026-10-14).

No command or API call is known that clears the canvas, so the button is
pressed, through Windows UI
Automation: the view ``Navigator`` (a ``ViewStageOverview``), in it the
``StageOverviewActionBar``, in that the button named Clear (0.2 s to find
on the simulator; a whole-window search took 21 s). It is found once and
kept, and pressed only when it is enabled, which it is while the canvas
shows tiles. UI Automation finds it only while LAS X shows the Navigator;
with another view shown the canvas is not cleared, and the release says so.
A search that finds nothing returns comtypes' empty pointer, which is false
but not None. Clearing removes the tiles from LAS X's own Navigator view;
nothing else changes.
"""

from __future__ import annotations

import logging
import threading

from .lasx_dialogs import WindowsUnavailable, automation

log = logging.getLogger(__name__)

__all__ = ["NavigatorUnavailable", "clear_canvas", "forget"]


class NavigatorUnavailable(RuntimeError):
    """The Navigator's Clear button cannot be reached (no UI Automation, the Navigator not shown)."""


class ClearButton:
    """The Navigator canvas's Clear button, through Windows UI Automation."""

    def __init__(self, client, element):
        self.client, self.element = client, element

    def is_enabled(self) -> bool:
        return bool(self.element.CurrentIsEnabled)

    def press(self):
        pattern = self.element.GetCurrentPattern(self.client.UIA_InvokePatternId)
        pattern.QueryInterface(self.client.IUIAutomationInvokePattern).Invoke()


def find_clear_button() -> ClearButton:
    """The Clear button of the Navigator's action bar, found by the narrow path."""
    try:
        c, uia = automation()
    except WindowsUnavailable as why:
        raise NavigatorUnavailable(str(why)) from why
    p = uia.CreatePropertyCondition
    found = uia.GetRootElement().FindFirst(
        c.TreeScope_Children,
        uia.CreateAndCondition(p(c.UIA_NamePropertyId, "LAS X"), p(c.UIA_ClassNamePropertyId, "Window")),
    )
    if not found:
        raise NavigatorUnavailable("LAS X's window was not found")
    for what, condition in (
        ("the view Navigator", uia.CreateAndCondition(
            p(c.UIA_AutomationIdPropertyId, "Navigator"), p(c.UIA_ClassNamePropertyId, "ViewStageOverview"))),
        ("its action bar", p(c.UIA_ClassNamePropertyId, "StageOverviewActionBar")),
        ("its Clear button", uia.CreateAndCondition(
            p(c.UIA_ControlTypePropertyId, c.UIA_ButtonControlTypeId), p(c.UIA_NamePropertyId, "Clear"))),
    ):
        found = found.FindFirst(c.TreeScope_Descendants, condition)
        if not found:
            raise NavigatorUnavailable(f"{what} was not found in LAS X's window (is the Navigator shown?)")
    return ClearButton(c, found)


kept = threading.local()


def forget():
    """Find the button afresh at the next clear (a new session, or the tests)."""
    kept.button = None


def clear_canvas() -> bool:
    """Clear the Navigator canvas; True when it was cleared, False when it showed no tiles.

    Raises :class:`NavigatorUnavailable` when the button cannot be reached.
    """
    for attempt in (1, 2):
        button = getattr(kept, "button", None)
        if button is None:
            button = kept.button = find_clear_button()
        try:
            if not button.is_enabled():
                return False
            button.press()
            return True
        except NavigatorUnavailable:
            raise
        except Exception as why:  # noqa: BLE001 -- a kept element that no longer answers is found again
            kept.button = None
            if attempt == 2:
                raise NavigatorUnavailable(f"the Navigator's Clear button did not answer: {why}") from why
    return False
