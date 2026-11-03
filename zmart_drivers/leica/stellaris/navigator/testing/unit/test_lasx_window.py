"""LAS X's window controls whose effect never reaches LCS: the channel colour lock and the z-stack's Set Focus, against a stand-in for the window.

LAS X's colour lock (``LockLut``, on the detector panel's colour button)
sends LCS nothing and changes no value LCS holds (simulator, 2026-11-01), so
the driver presses it in LAS X's window, as the operator does, and reads its
state back from there. These tests check it is pressed only when its state
differs, read back, and refused when LAS X greys it out.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

import pytest

from zmart_drivers.leica.stellaris.navigator.procedures import lasx_window


class Lock:
    """The colour lock as LAS X's window holds it: on or off, enabled or greyed out."""

    def __init__(self, on=False, enabled=True, sticks=True):
        self.on, self.enabled, self.sticks, self.presses = on, enabled, sticks, 0


class Window:
    """LAS X's main window as the driver sees it: its controls by automation id."""

    def __init__(self, lock, automation_id="LockLut"):
        self.lock, self.automation_id = lock, automation_id

    def control(self, automation_id):
        assert automation_id == self.automation_id
        return self.lock

    def is_enabled(self, control):
        return control.enabled

    def is_on(self, control):
        return control.on

    def toggle(self, control):
        control.presses += 1
        if control.sticks:
            control.on = not control.on


@pytest.mark.parametrize("locked", [True, False])
def test_the_lock_is_pressed_once_and_read_back(locked):
    lock = Lock(on=not locked)

    result = lasx_window.lock_channel_colour(locked, window=Window(lock))

    assert result["success"] is True and result["confirmed"] is True
    assert lock.on is locked and lock.presses == 1


def test_a_lock_already_as_asked_is_not_pressed():
    lock = Lock(on=True)

    result = lasx_window.lock_channel_colour(True, window=Window(lock))

    assert result["confirmed"] is True and lock.presses == 0


def test_a_greyed_out_lock_is_refused_and_not_pressed():
    lock = Lock(enabled=False)

    result = lasx_window.lock_channel_colour(True, window=Window(lock))

    assert result["success"] is False and "greyed out" in result["message"]
    assert lock.presses == 0


def test_a_press_the_window_did_not_take_is_not_confirmed():
    lock = Lock(sticks=False)

    result = lasx_window.lock_channel_colour(True, window=Window(lock))

    assert result["success"] is True and result["confirmed"] is False


def test_the_lock_takes_true_or_false_only():
    with pytest.raises(ValueError):
        lasx_window.lock_channel_colour("yes", window=Window(Lock()))


def test_set_focus_is_pressed_once_when_no_plane_is_set():
    button = Lock(on=False)

    result = lasx_window.set_z_stack_focus(True, window=Window(button, "ButtonSetFocus"))

    assert result["confirmed"] is True and button.on is True and button.presses == 1


def test_set_focus_already_set_is_pressed_twice_so_the_plane_moves_to_the_focus_now():
    button = Lock(on=True)

    result = lasx_window.set_z_stack_focus(True, window=Window(button, "ButtonSetFocus"))

    assert result["confirmed"] is True and button.on is True and button.presses == 2


def test_the_focus_plane_is_cleared_with_one_press_or_none():
    button = Lock(on=True)
    assert lasx_window.set_z_stack_focus(False, window=Window(button, "ButtonSetFocus"))["confirmed"] is True
    assert button.on is False and button.presses == 1
    assert lasx_window.set_z_stack_focus(False, window=Window(button, "ButtonSetFocus"))["confirmed"] is True
    assert button.presses == 1


def test_set_focus_greyed_out_without_a_stack_is_refused_and_not_pressed():
    button = Lock(enabled=False)

    result = lasx_window.set_z_stack_focus(True, window=Window(button, "ButtonSetFocus"))

    assert result["success"] is False and "z-stack" in result["message"] and button.presses == 0
