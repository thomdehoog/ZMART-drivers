"""At the microscope: the buttons the driver presses in LAS X, pressed and checked. No capture, no move.

The driver presses two of Navigator Expert's buttons through Windows UI
Automation (``vendor_specific/primitives/interface/lasx_navigator.py``): Experiment, which shows
the Navigator canvas before a capture that will be released, and the
canvas's Clear, which makes LAS X let go of the hidden copies its captures
leave in its cache. Nothing else can do either, so if LAS X lays its window
out differently, these presses are what stops working.

This check leaves the canvas on purpose, by pressing Jobs, and then lets the
driver find its own way back, as it does before a capture. It then presses
Clear. Whether Clear had an effect cannot be seen on the button: on the
STELLARIS it stays enabled after a press, also on an empty canvas
(2026-10-15; on the simulator it turned grey). Its effect is what matters,
LAS X letting go of the hidden copies in its cache, and that is measured by
the ten captures of ``check_cache_release.py``, which the CI runs after this
check. Last, it asks the dialog watcher (``lasx_dialogs.py``)
whether LAS X shows a dialog, in its log and on screen: an open dialog
holds every command, so a run should not start with one.

It ends with the canvas shown, as the cache checks after it need. The
report is written as ``check_buttons.json``; the last line says
``ALL CHECKS PASSED`` or which check failed.

Usage, in the driver's environment on the microscope PC::

    python check_buttons.py --report-dir D:/ZMART-check
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific import tuning
from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.vendor_specific.primitives.interface import lasx_dialogs, lasx_navigator

#: How long a press may take to show on screen: one window, by the driver's one waiting rule.
WAIT_S = tuning.WINDOW_S


def press_jobs() -> None:
    """Press Navigator Expert's Jobs button, the other half of the Jobs/Experiment switch.

    The driver itself never presses Jobs; the check does, so that the canvas
    is gone and the driver has to bring it back.
    """
    c, uia, found = lasx_navigator.walked(
        lasx_navigator.SWITCH_PATH, "so no Jobs button; is Navigator Expert open?"
    )
    p = uia.CreatePropertyCondition
    switch = found.FindFirst(
        c.TreeScope_Descendants,
        p(c.UIA_ClassNamePropertyId, "ModuleSwitchBetweenLDMAndMatrixView"),
    )
    button = (
        switch.FindFirst(
            c.TreeScope_Children,
            uia.CreateAndCondition(
                p(c.UIA_ControlTypePropertyId, c.UIA_ButtonControlTypeId),
                p(c.UIA_NamePropertyId, "Jobs"),
            ),
        )
        if switch
        else None
    )
    if not button:
        raise lasx_navigator.NavigatorUnavailable(
            "Navigator Expert's Jobs button was not found"
        )
    toggle = button.GetCurrentPattern(c.UIA_TogglePatternId).QueryInterface(
        c.IUIAutomationTogglePattern
    )
    if toggle.CurrentToggleState != 1:
        toggle.Toggle()


def until(condition, seconds: float) -> bool:
    """Whether *condition* became true within *seconds*, asked every poll interval."""
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(tuning.POLL_S)
    return True


def run_checks() -> dict:
    """Every check, by name: True when it passed, or a text saying what happened instead."""
    checks: dict = {}

    def check(name, test):
        try:
            result = test()
        except Exception as why:  # noqa: BLE001 -- reported, not raised
            result = f"NO: {type(why).__name__}: {why}"
        checks[name] = result
        return result is True

    def jobs_hides_the_canvas():
        press_jobs()
        return (
            True
            if until(lambda: not lasx_navigator.canvas_shown(), WAIT_S)
            else "NO: the canvas stayed shown after Jobs"
        )

    def experiment_brings_it_back():
        pressed = lasx_navigator.show_canvas()
        if not pressed:
            return "NO: the driver did not press Experiment, though the canvas was gone"
        return (
            True
            if lasx_navigator.canvas_shown()
            else "NO: Experiment pressed, the canvas not reachable"
        )

    def shown_is_left_alone():
        return (
            True
            if lasx_navigator.show_canvas() is False
            else "NO: Experiment pressed again on a shown canvas"
        )

    def clear_is_pressed():
        # The press itself: the driver found the button and pressed it, or it
        # says the canvas showed nothing to clear. Its effect: check_cache_release.py.
        pressed = lasx_navigator.clear_canvas()
        return True if isinstance(pressed, bool) else f"NO: Clear answered {pressed!r}"

    def no_dialog():
        in_log = lasx_dialogs.pending()
        on_screen = lasx_dialogs.lasx_windows().dialogs()
        if in_log is not None or on_screen:
            return f"NO: in the log {in_log!r}, on screen {len(on_screen)} dialog(s)"
        return True

    if check("Jobs pressed: the canvas is gone", jobs_hides_the_canvas):
        check(
            "the driver brought the canvas back with Experiment",
            experiment_brings_it_back,
        )
        check("a shown canvas is left alone", shown_is_left_alone)
    if lasx_navigator.canvas_shown():
        check("Clear found and pressed", clear_is_pressed)
    else:
        checks["Clear found and pressed"] = "NO: not tried, the canvas is not shown"
    check("no LAS X dialog open, in its log or on screen", no_dialog)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--report-dir", default=".", help="where the report is written (default: here)"
    )
    asked = parser.parse_args()
    checks = run_checks()
    failed = [name for name, result in checks.items() if result is not True]
    report = {
        "checks": checks,
        "verdict": "ALL CHECKS PASSED" if not failed else f"FAILED: {failed}",
    }
    out = Path(asked.report_dir) / "check_buttons.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\n{report['verdict']}  (report: {out})")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
