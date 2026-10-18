"""LAS X's Navigator canvas, cleared so that LAS X lets go of the hidden copies it shows.

Every capture leaves a hidden copy in LAS X's cache (``TileScan <n>`` in an
experiment LAS X calls "Hidden": the pixels again, a zoom pyramid and a
histogram, about twice a 512 x 512 capture's pixels). The Navigator canvas
holds it while it shows the capture's tile: releasing the capture itself
(``output/lasx_cache_release``) leaves the copy, and pressing the canvas's
Clear button lets go of them all, while LAS X runs (simulator, 2026-10-14).

No command or API call is known that clears the canvas, so the button is
pressed, through Windows UI Automation. The way to it is walked one child
at a time from LAS X's main window down to the Navigator's tab control
(:data:`NAVIGATOR_PATH`), then searched for inside it: the view
``Navigator`` (a ``ViewStageOverview``), its ``StageOverviewActionBar``,
and in that the button named Clear. With the Navigator shown this takes
0.14 s; with another view shown the walk stops at the tab control in
0.06 s. Searching all of LAS X's window instead took 0.2 s when the
Navigator was shown, but 90 to 158 s when it was not, at every release
(simulator, 2026-10-14). The button is found once and kept, and pressed
only when it is enabled, which it is while the canvas shows tiles. With
another view shown the canvas is not cleared, and the release says so;
clearing it later lets go of every hidden copy left until then. A search
that finds nothing returns comtypes' empty pointer, which is false but not
None. Clearing removes the tiles from LAS X's own Navigator view; nothing
else changes.

Nothing outside LAS X's own process can clear the canvas, so a capture that
will be released first has the canvas shown (:func:`show_canvas`): while
LAS X is idle, before the capture, Navigator Expert's Experiment button is
pressed when it is off (the Jobs/Experiment switch, :data:`SWITCH_PATH`,
0.01 to 0.04 s to reach; the press 0.04 s, simulator 2026-10-14). The
canvas counts as shown when its Clear button can be reached, not when the
switch says Experiment.
"""

from __future__ import annotations

import logging
import threading
import time

from ..dispatcher import tuning
from .lasx_dialogs import WindowsUnavailable, automation

log = logging.getLogger(__name__)

__all__ = ["NavigatorUnavailable", "clear_canvas", "forget", "show_canvas"]


class NavigatorUnavailable(RuntimeError):
    """The Navigator's Clear button cannot be reached (no UI Automation, the Navigator not shown)."""


class ExperimentButton:
    """Navigator Expert's Experiment button, which shows the canvas, through Windows UI Automation."""

    def __init__(self, client, element):
        pattern = element.GetCurrentPattern(client.UIA_TogglePatternId)
        if not pattern:
            raise NavigatorUnavailable(
                "Navigator Expert's Experiment button cannot be pressed"
            )
        self.toggle = pattern.QueryInterface(client.IUIAutomationTogglePattern)

    def is_on(self) -> bool:
        return self.toggle.CurrentToggleState == 1

    def press(self):
        self.toggle.Toggle()


class ClearButton:
    """The Navigator canvas's Clear button, through Windows UI Automation."""

    def __init__(self, client, element):
        self.client, self.element = client, element

    def is_enabled(self) -> bool:
        return bool(self.element.CurrentIsEnabled)

    def press(self):
        pattern = self.element.GetCurrentPattern(self.client.UIA_InvokePatternId)
        pattern.QueryInterface(self.client.IUIAutomationInvokePattern).Invoke()


#: The way from LAS X's main window to the Navigator's tab control, one child
#: at a time: (class, automation id, name), None where LAS X gives none.
NAVIGATOR_PATH = (
    ("Pane", None, "AddIn"),
    ("MainFormView", None, None),
    ("ContainerLDMMatrixView", "LDMMatrix", None),
    ("TabControl", "WorkFlowBar", None),
    ("TabItem", "WorkflowStartScreen", None),
    ("TabControl", "NavigatorTabControl", None),
)


#: The way from LAS X's main window to Navigator Expert's Jobs/Experiment
#: switch, one child at a time, as :data:`NAVIGATOR_PATH`.
SWITCH_PATH = (
    ("Pane", None, "AddIn"),
    ("MainFormView", None, None),
    ("ContainerTopView", "TopModule", None),
    ("TabControl", "Tab", None),
)


def walked(path, missing: str):
    """UI Automation and the element at the end of *path* from LAS X's main window: ``(client, uia, element)``.

    Raises :class:`NavigatorUnavailable` naming the step not found, with *missing* as the likely reason.
    """
    try:
        c, uia = automation()
    except WindowsUnavailable as why:
        raise NavigatorUnavailable(str(why)) from why
    p = uia.CreatePropertyCondition
    found = uia.GetRootElement().FindFirst(
        c.TreeScope_Children,
        uia.CreateAndCondition(
            p(c.UIA_NamePropertyId, "LAS X"), p(c.UIA_ClassNamePropertyId, "Window")
        ),
    )
    if not found:
        raise NavigatorUnavailable("LAS X's window was not found")
    for class_name, automation_id, name in path:
        condition = p(c.UIA_ClassNamePropertyId, class_name)
        if automation_id:
            condition = uia.CreateAndCondition(
                condition, p(c.UIA_AutomationIdPropertyId, automation_id)
            )
        if name:
            condition = uia.CreateAndCondition(condition, p(c.UIA_NamePropertyId, name))
        found = found.FindFirst(c.TreeScope_Children, condition)
        if not found:
            raise NavigatorUnavailable(
                f"LAS X's window holds no {class_name} {automation_id or name or ''} ({missing})"
            )
    return c, uia, found


def find_clear_button() -> ClearButton:
    """The Clear button of the Navigator's action bar, by :data:`NAVIGATOR_PATH`, never a whole-window search."""
    c, uia, found = walked(NAVIGATOR_PATH, "is the Navigator shown?")
    p = uia.CreatePropertyCondition
    for what, condition in (
        (
            "the view Navigator",
            uia.CreateAndCondition(
                p(c.UIA_AutomationIdPropertyId, "Navigator"),
                p(c.UIA_ClassNamePropertyId, "ViewStageOverview"),
            ),
        ),
        ("its action bar", p(c.UIA_ClassNamePropertyId, "StageOverviewActionBar")),
        (
            "its Clear button",
            uia.CreateAndCondition(
                p(c.UIA_ControlTypePropertyId, c.UIA_ButtonControlTypeId),
                p(c.UIA_NamePropertyId, "Clear"),
            ),
        ),
    ):
        found = found.FindFirst(c.TreeScope_Descendants, condition)
        if not found:
            raise NavigatorUnavailable(
                f"{what} was not found in the Navigator's tab (is the Navigator shown?)"
            )
    return ClearButton(c, found)


def find_experiment_button() -> ExperimentButton:
    """Navigator Expert's Experiment button, by :data:`SWITCH_PATH`, never a whole-window search."""
    c, uia, found = walked(
        SWITCH_PATH, "so no Experiment button; is Navigator Expert open?"
    )
    p = uia.CreatePropertyCondition
    switch = found.FindFirst(
        c.TreeScope_Descendants,
        p(c.UIA_ClassNamePropertyId, "ModuleSwitchBetweenLDMAndMatrixView"),
    )
    button = None
    if switch:
        button = switch.FindFirst(
            c.TreeScope_Children,
            uia.CreateAndCondition(
                p(c.UIA_ControlTypePropertyId, c.UIA_ButtonControlTypeId),
                p(c.UIA_NamePropertyId, "Experiment"),
            ),
        )
    if not button:
        raise NavigatorUnavailable(
            "Navigator Expert's Experiment button was not found (is Navigator Expert open?)"
        )
    return ExperimentButton(c, button)


kept = threading.local()


def forget():
    """Find the button afresh at the next clear (a new session, or the tests)."""
    kept.button = None


def canvas_shown() -> bool:
    """Whether the canvas's Clear button can be reached now; the one found is kept for the next clear."""
    try:
        kept.button = find_clear_button()
    except NavigatorUnavailable:
        return False
    return True


def show_canvas() -> bool:
    """Show the Navigator canvas, so that its Clear can be pressed; True when Experiment had to be pressed.

    Meant for LAS X idle, before a capture. The canvas has arrived when its
    Clear button can be reached; that is waited for within the waiting
    rule's window. Raises :class:`NavigatorUnavailable` when the button
    cannot be pressed or the canvas does not come.
    """
    if canvas_shown():
        return False
    experiment = find_experiment_button()
    if not experiment.is_on():
        experiment.press()
    deadline = time.monotonic() + tuning.WINDOW_S
    while not canvas_shown():
        if time.monotonic() >= deadline:
            raise NavigatorUnavailable(
                f"Experiment was pressed, but the canvas could not be reached within {tuning.WINDOW_S:g} s"
            )
        time.sleep(tuning.POLL_S)
    return True


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
                raise NavigatorUnavailable(
                    f"the Navigator's Clear button did not answer: {why}"
                ) from why
    return False
