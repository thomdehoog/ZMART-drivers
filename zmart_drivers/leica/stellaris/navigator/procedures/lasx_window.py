"""LAS X's main window, through Windows UI Automation: the few controls whose effect never reaches LCS.

Almost everything the driver changes goes to LCS, LAS X's hardware server,
as a command or a state-tree value. A few of the window's controls send
LCS nothing: the pan pad's arrows, the colour lock on the detector panel's
colour button (``LockLut``; simulator, 2026-11-01), and the z-stack panel's
Set Focus (``ButtonSetFocus``: LCS ignores the focus plane written into its
state tree, but holds the one the button sets; 2026-11-03). For these the
driver presses the control in LAS X's window, as the operator does, through
Windows UI Automation, as it presses OK on LAS X's message boxes; and it
reads the control's state back from the window.

The colour lock acts on the detector LAS X's detector panel shows, and only
in the window: it keeps LAS X, its Dye Assistant say, from changing that
channel's colour. The driver's own ``set_channel_colour`` goes to LCS and
is not held by it. LAS X greys the lock out when the setting is switched
off; then it is refused, and nothing is pressed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from . import answer_dialogs


class MainWindow:
    """LAS X's main window and its controls, through this thread's UI Automation."""

    def __init__(self):
        self.client, self.uia = answer_dialogs.automation()
        self.element = self.find()

    def find(self):
        c, uia = self.client, self.uia
        tops = uia.GetRootElement().FindAll(c.TreeScope_Children, uia.CreateTrueCondition())
        for i in range(tops.Length):
            window = tops.GetElement(i)
            if window.CurrentName and answer_dialogs.process_name(window.CurrentProcessId) == "LMSApplication.exe":
                return window
        raise answer_dialogs.WindowsUnavailable("LAS X's window was not found")

    def first(self, property_id, value):
        """The first control in the window whose property *property_id* is *value*; WindowsUnavailable when there is none."""
        found = self.element.FindFirst(self.client.TreeScope_Descendants, self.uia.CreatePropertyCondition(property_id, value))
        if not found:  # UI Automation answers an empty pointer, not None, when nothing is there
            raise answer_dialogs.WindowsUnavailable(f"LAS X's window shows no {value!r} (its panel is closed?)")
        return found

    def control(self, automation_id: str):
        """The control with *automation_id*."""
        return self.first(self.client.UIA_AutomationIdPropertyId, automation_id)

    def is_enabled(self, control) -> bool:
        return bool(control.CurrentIsEnabled)

    def is_on(self, control) -> bool:
        pattern = control.GetCurrentPattern(self.client.UIA_TogglePatternId)
        return pattern.QueryInterface(self.client.IUIAutomationTogglePattern).CurrentToggleState == 1

    def toggle(self, control) -> None:
        pattern = control.GetCurrentPattern(self.client.UIA_TogglePatternId)
        pattern.QueryInterface(self.client.IUIAutomationTogglePattern).Toggle()

    def press(self, control) -> None:
        pattern = control.GetCurrentPattern(self.client.UIA_InvokePatternId)
        pattern.QueryInterface(self.client.IUIAutomationInvokePattern).Invoke()


#: The colour lock on the detector panel's colour button.
COLOUR_LOCK = "LockLut"


def lock_channel_colour(locked: bool, *, window=None) -> dict:
    """Lock or free the colour of the channel LAS X's detector panel shows, as the window's colour lock does; read back.

    Pressed only when the lock is not already as asked. Refused, with nothing
    pressed, when LAS X greys it out. ``confirmed`` when the window shows it
    as asked afterwards.
    """
    if not isinstance(locked, bool):
        raise ValueError(f"the colour lock takes True or False, not {locked!r}")
    window = window if window is not None else MainWindow()
    lock = window.control(COLOUR_LOCK)
    if not window.is_enabled(lock):
        return {
            "success": False,
            "confirmed": False,
            "message": "LAS X has the colour lock greyed out (is the setting switched on?); nothing was pressed",
        }
    if window.is_on(lock) is not locked:
        window.toggle(lock)
    shown = window.is_on(lock)
    result = {"success": True, "confirmed": shown is locked, "locked": shown}
    if shown is not locked:
        result["message"] = f"the colour lock was pressed, but LAS X shows it {'locked' if shown else 'free'}"
    return result


#: The z-stack panel's Set Focus.
SET_FOCUS = "ButtonSetFocus"


def set_z_stack_focus(on: bool, *, window=None) -> dict:
    """Set the z-stack's focus plane where the focus is now, or clear it, as the z-stack panel's Set Focus does; read back.

    When a plane is set already, the button is pressed twice, off and on, so
    the plane moves to where the focus is now. Refused, with nothing pressed,
    when LAS X greys the button out (before a stack's begin is set). ``confirmed`` when the
    window shows the button as asked afterwards.
    """
    if not isinstance(on, bool):
        raise ValueError(f"Set Focus takes True (set it here) or False (clear it), not {on!r}")
    window = window if window is not None else MainWindow()
    button = window.control(SET_FOCUS)
    if not window.is_enabled(button):
        return {"success": False, "confirmed": False,
                "message": "LAS X has Set Focus greyed out (is the z-stack's begin set?); nothing was pressed"}
    if window.is_on(button):
        window.toggle(button)
    if on:
        window.toggle(button)
    shown = window.is_on(button)
    result = {"success": True, "confirmed": shown is on}
    if shown is not on:
        result["message"] = f"Set Focus was pressed, but LAS X shows the focus plane {'set' if shown else 'cleared'}"
    return result
