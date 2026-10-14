"""
Unit tests: LAS X's Navigator canvas, cleared to let go of its hidden copies.
================================================================================
Every capture leaves a hidden copy in LAS X's cache (``TileScan <n>``, about
twice the capture's pixels), held while the Navigator canvas shows its tile;
the canvas's Clear button lets go of them all (simulator, 2026-10-14). The
button sits in the Navigator's action bar; the driver finds it once and
keeps it, presses it only when it is enabled (the canvas shows tiles), and
finds it again when the kept one no longer answers.
"""

import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_interface import (
    lasx_dialogs,
    lasx_navigator,
)


class StandInButton:
    def __init__(self, enabled=True):
        self.enabled, self.pressed, self.gone = enabled, 0, False

    def is_enabled(self):
        if self.gone:
            raise OSError("the element is not available any more")
        return self.enabled

    def press(self):
        if self.gone:
            raise OSError("the element is not available any more")
        self.pressed += 1


@pytest.fixture
def buttons(monkeypatch):
    made = []

    def find():
        made.append(StandInButton())
        return made[-1]

    monkeypatch.setattr(lasx_navigator, "find_clear_button", find)
    lasx_navigator.forget()
    yield made
    lasx_navigator.forget()


def test_a_canvas_with_tiles_is_cleared(buttons):
    assert lasx_navigator.clear_canvas() is True
    assert buttons[0].pressed == 1


def test_an_empty_canvas_is_left_alone(buttons):
    lasx_navigator.clear_canvas()
    buttons[0].enabled = False
    assert lasx_navigator.clear_canvas() is False
    assert buttons[0].pressed == 1


def test_the_button_is_found_once_and_kept(buttons):
    lasx_navigator.clear_canvas()
    lasx_navigator.clear_canvas()
    assert len(buttons) == 1
    assert buttons[0].pressed == 2


def test_a_kept_button_that_no_longer_answers_is_found_again(buttons):
    lasx_navigator.clear_canvas()
    buttons[0].gone = True
    assert lasx_navigator.clear_canvas() is True
    assert len(buttons) == 2
    assert buttons[1].pressed == 1


def test_no_navigator_raises(monkeypatch):
    def nowhere():
        raise lasx_navigator.NavigatorUnavailable("the Navigator is not shown")

    monkeypatch.setattr(lasx_navigator, "find_clear_button", nowhere)
    lasx_navigator.forget()
    with pytest.raises(lasx_navigator.NavigatorUnavailable, match="not shown"):
        lasx_navigator.clear_canvas()


class NullPointer:
    """What comtypes returns when UI Automation finds nothing: not None, but false."""

    def __bool__(self):
        return False

    def FindFirst(self, scope, condition):
        raise ValueError("NULL COM pointer access")


class StandInElement:
    def __init__(self, child):
        self.child = child

    def FindFirst(self, scope, condition):
        return self.child


class StandInAutomation:
    TreeScope_Children = TreeScope_Descendants = 0
    UIA_NamePropertyId = UIA_ClassNamePropertyId = UIA_AutomationIdPropertyId = 0
    UIA_ControlTypePropertyId = UIA_ButtonControlTypeId = 0

    def __init__(self, root):
        self.root = root

    def GetRootElement(self):
        return self.root

    def CreatePropertyCondition(self, *args):
        return args

    def CreateAndCondition(self, *args):
        return args


def test_a_navigator_not_shown_is_unavailable_not_an_error(monkeypatch):
    # LAS X's window found, the Navigator view not (another view is shown).
    automation = StandInAutomation(StandInElement(StandInElement(NullPointer())))
    monkeypatch.setattr(lasx_navigator, "automation", lambda: (automation, automation))
    with pytest.raises(lasx_navigator.NavigatorUnavailable, match="the view Navigator was not found"):
        lasx_navigator.find_clear_button()


def test_no_las_x_window_is_unavailable(monkeypatch):
    automation = StandInAutomation(StandInElement(NullPointer()))
    monkeypatch.setattr(lasx_navigator, "automation", lambda: (automation, automation))
    with pytest.raises(lasx_navigator.NavigatorUnavailable, match="LAS X's window was not found"):
        lasx_navigator.find_clear_button()
