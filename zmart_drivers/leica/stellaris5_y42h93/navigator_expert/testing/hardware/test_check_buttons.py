"""Pytest gate for the button check, against a stand-in for Navigator Expert's buttons.

``check_buttons.py`` presses Jobs so the canvas goes, lets the driver bring it
back with Experiment, presses Clear, and asks the dialog watcher whether LAS X
shows a dialog. Here Navigator Expert is a small stand-in: two toggle
buttons, Jobs and Experiment, and the canvas shown while Experiment is on.
"""

import check_buttons
import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.interface import lasx_dialogs, lasx_navigator


class StandInNavigator:
    def __init__(self, experiment_on=True, comes_back=True):
        self.experiment_on = experiment_on
        self.comes_back = comes_back
        self.tiles = True  # the canvas shows tiles; Clear is enabled while it does
        self.clear_works = True
        self.cleared = 0

    def press_jobs(self):
        self.experiment_on = False

    def canvas_shown(self):
        return self.experiment_on

    def show_canvas(self):
        if self.experiment_on:
            return False
        if not self.comes_back:
            raise lasx_navigator.NavigatorUnavailable(
                "Experiment was pressed, but the canvas could not be reached"
            )
        self.experiment_on = True
        return True

    def clear_canvas(self):
        if not self.clear_works:
            raise lasx_navigator.NavigatorUnavailable(
                "the Navigator's Clear button did not answer"
            )
        if not self.tiles:
            return False
        self.cleared += 1
        self.tiles = False
        return True


class NoDialogs:
    def dialogs(self):
        return []


@pytest.fixture
def navigator(monkeypatch):
    stand_in = StandInNavigator()
    monkeypatch.setattr(check_buttons, "press_jobs", stand_in.press_jobs)
    monkeypatch.setattr(lasx_navigator, "canvas_shown", stand_in.canvas_shown)
    monkeypatch.setattr(lasx_navigator, "show_canvas", stand_in.show_canvas)
    monkeypatch.setattr(lasx_navigator, "clear_canvas", stand_in.clear_canvas)
    monkeypatch.setattr(lasx_dialogs, "pending", lambda: None)
    monkeypatch.setattr(lasx_dialogs, "lasx_windows", NoDialogs)
    monkeypatch.setattr(check_buttons, "WAIT_S", 0.2)
    return stand_in


def test_every_button_answers_and_the_canvas_ends_shown_and_empty(navigator):
    checks = check_buttons.run_checks()
    assert all(result is True for result in checks.values()), checks
    assert navigator.canvas_shown()
    assert (navigator.cleared, navigator.tiles) == (1, False)


def test_a_jobs_press_that_leaves_the_canvas_shown_fails(navigator, monkeypatch):
    monkeypatch.setattr(check_buttons, "press_jobs", lambda: None)
    checks = check_buttons.run_checks()
    assert str(checks["Jobs pressed: the canvas is gone"]).startswith("NO")


def test_a_canvas_that_does_not_come_back_fails(navigator):
    navigator.comes_back = False
    checks = check_buttons.run_checks()
    assert str(checks["the driver brought the canvas back with Experiment"]).startswith(
        "NO"
    )


def test_a_clear_button_that_does_not_answer_fails(navigator):
    navigator.clear_works = False
    checks = check_buttons.run_checks()
    assert str(checks["Clear found and pressed"]).startswith("NO")


def test_clear_is_not_tried_without_the_canvas(navigator, monkeypatch):
    navigator.comes_back = False
    checks = check_buttons.run_checks()
    assert checks["Clear found and pressed"] == "NO: not tried, the canvas is not shown"
    assert navigator.cleared == 0


def test_an_open_dialog_fails(navigator, monkeypatch):
    monkeypatch.setattr(
        lasx_dialogs,
        "pending",
        lambda: lasx_dialogs.Dialog("now", "Warning", "Please keep minimum distance"),
    )
    checks = check_buttons.run_checks()
    assert str(checks["no LAS X dialog open, in its log or on screen"]).startswith("NO")
