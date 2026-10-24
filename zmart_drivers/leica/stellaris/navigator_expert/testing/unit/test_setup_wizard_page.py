"""The setup wizard's page in a real browser (Chromium, through Playwright).

The built page in ``driver_setup/wizard/page/`` is opened from the wizard's own
server and clicked through the way an operator would. The page is the ZMART
Interface's screen without its top row: the rail of steps on the left, and the
chosen step's boxes beside it. Three kinds of test:

- **The whole setup on stand-ins.** All five real Leica steps, one after the
  other in one window, on the stand-ins of ``helpers/wizard_standins.py``: the
  offline LAS X for the data location, the limits and the zero point, the
  orientation tests' pictures, and the calibration tests' physical simulator.
  What the page shows is checked, and so is what was saved on disk.
- **The rail.** The interface's rules: blue where you stand, green once done,
  orange with a "review" tag when a step it was measured on was saved again
  after it, and locked until every step before it is done.
- **The boxes.** A pretend wizard with canned answers checks the rest: what a
  button came to is said beside it (green, or red with the reason), a busy
  button cannot be pressed twice and the rail's foot says what is happening,
  and the page asks nothing of the internet.

Where no browser can start, these tests are skipped rather than failed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import re
import os
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from wizard_standins import (
    drive_stage_to,
    install_calibration,
    install_lasx,
    install_orientation,
)

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup import store
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard import steps
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.framework import (
    Step,
    StepError,
    Wizard,
)
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.server import (
    WizardServer,
)

# Where a remote test container keeps its browser, when Playwright's own download is missing.
_INSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"

# Screenshots of the real run, for a person to look at; set this to a folder to keep them.
_SCREENSHOTS = os.environ.get("ZMART_WIZARD_SCREENSHOTS")

# The interface's badge colours (light screen): where you stand, done, and to look at again.
BLUE, GREEN, ORANGE = "rgb(2, 132, 199)", "rgb(22, 163, 74)", "rgb(217, 119, 6)"


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            chromium = p.chromium.launch()
        except Exception:  # noqa: BLE001 -- Playwright's own browser is missing; try the installed one
            if not Path(_INSTALLED_CHROMIUM).exists():
                pytest.skip("no browser can start here")
            chromium = p.chromium.launch(executable_path=_INSTALLED_CHROMIUM)
        yield chromium
        chromium.close()


class Window:
    """One wizard served and opened in a browser tab, recording what the page asks for."""

    def __init__(self, browser, wizard, **context):
        self.server = WizardServer(wizard).start()
        self.context = browser.new_context(**context)
        self.page = self.context.new_page()
        self.requests: list[str] = []
        self.problems: list[str] = []
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on(
            "console", lambda m: m.type == "error" and self.problems.append(m.text)
        )
        self.page.on("pageerror", lambda e: self.problems.append(str(e)))
        self.page.on(
            "response",
            lambda r: r.status >= 400 and self.problems.append(f"{r.status} {r.url}"),
        )
        self.page.set_default_timeout(20000)
        self.page.goto(self.server.url)
        self.page.locator(".step").first.wait_for()

    def close(self):
        self.context.close()
        self.server.stop()

    # --- what the operator sees
    def rail(self) -> list[dict]:
        return self.page.eval_on_selector_all(
            ".step",
            "els => els.map(e => ({name: e.querySelector('.step-name').textContent,"
            " num: e.querySelector('.step-n').textContent,"
            " badge: getComputedStyle(e.querySelector('.step-n')).backgroundColor,"
            " done: e.classList.contains('done'), stale: e.classList.contains('stale'),"
            " active: e.classList.contains('active'), locked: e.disabled,"
            " review: !!e.querySelector('.review-tag'), spinning: !!e.querySelector('.spin')}))",
        )

    def title(self) -> str:
        """The heading of the step's first box (shown in capitals; read as written)."""
        return self.page.locator(".channel .side-group-title").first.text_content()

    def open_step(self, name: str):
        self.page.locator(".step", has_text=name).click()
        self.page.locator(".channel .side-group-title", has_text=name).first.wait_for()

    def button(self, label: str):
        return self.page.get_by_role("button", name=label, exact=True)

    def press(self, label: str):
        self.button(label).click()

    def beside(self, label: str):
        """The sentence beside a button: what it came to, or why it could not."""
        return (
            self.page.locator(".side-act")
            .filter(has=self.button(label))
            .locator(".action-hint")
        )

    def wait_beside(self, label: str, text: str, kind: str = "ok"):
        self.page.locator(".side-act").filter(has=self.button(label)).locator(
            f".action-hint.{kind}", has_text=text
        ).wait_for()

    def shot(self, name: str):
        if _SCREENSHOTS:
            Path(_SCREENSHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(path=str(Path(_SCREENSHOTS) / f"{name}.png"))

    def assert_only_local_requests(self):
        outside = [u for u in self.requests if not u.startswith(self.server.url)]
        assert outside == [], outside

    def box(self, title: str):
        """The box whose heading is exactly *title*."""
        heading = self.page.locator(".side-group-title").filter(
            has_text=re.compile(f"^{re.escape(title)}$")
        )
        return self.page.locator(".side-group").filter(has=heading)

    def titles(self) -> list[str]:
        """The headings of the boxes on show, as written."""
        return self.page.eval_on_selector_all(
            ".channel .side-group:not([hidden]) > .side-group-title",
            "els => els.map(e => e.textContent)",
        )

    def picture_in(self, box_title: str):
        return self.box(box_title).locator(".picture")

    def wait_for_picture(self, box_title: str):
        picture = self.picture_in(box_title)
        picture.wait_for()
        self.page.wait_for_function(
            "img => img.complete && img.naturalWidth > 0", arg=picture.element_handle()
        )


# --- the whole setup, on the stand-ins --------------------------------------------------


CORNERS_UM = [
    (20000.0, 15000.0),
    (90000.0, 15000.0),
    (90000.0, 70000.0),
    (20000.0, 70000.0),
]


def test_an_operator_sets_up_the_microscope_from_start_to_finish(
    browser, tmp_path, monkeypatch
):
    lasx = install_lasx(monkeypatch)
    win = Window(browser, steps.leica_wizard(), viewport={"width": 1280, "height": 860})
    try:
        page = win.page
        # The stand-in's machine folder already holds fixture limits and a
        # calibration; the page opens on the first step not yet done.
        rail = win.rail()
        assert [r["name"] for r in rail] == [
            "Data location",
            "Travel limits",
            "Image orientation",
            "Objective calibration",
            "Zero point",
        ]
        assert [r["num"] for r in rail] == ["1", "2", "3", "4", "5"]
        assert win.title() == "Data location"
        assert rail[0]["active"] and rail[0]["badge"] == BLUE
        # The interface's rule: a step opens once every step before it is done.
        assert rail[1]["done"] and rail[1]["locked"] and rail[1]["badge"] == GREEN
        assert rail[2]["locked"] and rail[4]["locked"]
        win.shot("1-data-location")

        # 1. Data location: a wrong folder first, then a good one.
        page.get_by_label("Folder").fill(str(tmp_path / "missing"))
        win.press("Check and save")
        win.wait_beside("Check and save", "does not exist", kind="bad")
        win.shot("1b-data-location-refused")
        data = tmp_path / "ZMART-data"
        data.mkdir()
        page.get_by_label("Folder").fill(str(data))
        win.press("Check and save")
        win.wait_beside("Check and save", "Saved.")
        page.wait_for_function(
            "() => document.querySelectorAll('.step')[0].classList.contains('done')"
        )
        rail = win.rail()
        assert not rail[1]["locked"] and not rail[2]["locked"]

        # 2. Travel limits: drive the stand-in's stage to each corner, press its button.
        win.open_step("Travel limits")
        assert win.button("Save the limits").is_disabled()
        for number, (x, y) in enumerate(CORNERS_UM, start=1):
            drive_stage_to(lasx, x, y)
            win.press(f"Record corner {number}")
            win.wait_beside(f"Record corner {number}", f"X {x:.1f} µm, Y {y:.1f} µm")
            assert win.button("Save the limits").is_disabled() == (number < 4)
        win.shot("2-limits-corners")
        win.press("Save the limits")
        win.wait_beside(
            "Save the limits",
            "Saved. The stage may travel in X from 20000.0 to 90000.0 µm, and in Y from 15000.0 to 70000.0 µm.",
        )
        latest = store.MACHINE.latest_snapshot("limits")
        written = json.loads((latest / "limits.json").read_text(encoding="utf-8"))
        assert written["x_um"]["range"] == [20000.0, 90000.0]

        # 3. Image orientation, on the orientation stand-in for as long as this step lasts.
        win.open_step("Image orientation")
        with pytest.MonkeyPatch.context() as mp:
            machine = install_orientation(mp, tmp_path / "orientation")
            mp.setattr(store, "MACHINE", machine)
            assert win.button("Take a check picture").is_disabled()
            page.get_by_label("Stage move (µm)").fill("30")
            win.press("Measure the orientation")
            win.wait_beside("Measure the orientation", "Found:")
            win.wait_for_picture("Measure")
            assert win.button("Save the orientation").is_disabled()
            win.press("Take a check picture")
            win.wait_beside(
                "Take a check picture", "Does this picture follow the stage?"
            )
            win.wait_for_picture("Check")
            assert win.picture_in("Check").get_attribute("src").endswith(".jpg")
            win.shot("3-orientation-check")
            win.press("Save the orientation")
            win.wait_beside("Save the orientation", "Saved.")
            assert (
                machine.latest_snapshot("orientation") / "orientation.json"
            ).is_file()
            page.wait_for_function(
                "() => document.querySelectorAll('.step')[2].classList.contains('done')"
            )

        # 4. Objective calibration, on the physical simulator; the operator turns the turret.
        win.open_step("Objective calibration")
        with pytest.MonkeyPatch.context() as mp:
            module, sim, machine = install_calibration(mp, tmp_path / "calibration")
            assert win.box("1. Reference focus").is_hidden()
            page.get_by_label("Name of the pair").fill("10x-20x")
            page.get_by_label("Reference slot").fill("1")
            win.press("Start")
            win.wait_beside("Start", "Started.")
            sim.configure_stack(95.0, 105.0)
            win.press("Measure: Reference focus")
            win.wait_beside("Measure: Reference focus", "Reference focus done.")
            # A mistake first: the target focus with the reference objective still in.
            win.press("Measure: Target focus")
            win.wait_beside("Measure: Target focus", "reference objective", kind="bad")
            win.shot("4b-calibration-wrong-objective")
            sim.turret(2)
            sim.zwide = 104.0
            sim.configure_stack(101.0, 111.0)
            win.press("Measure: Target focus")
            win.wait_beside("Measure: Target focus", "Target focus done.")
            sim.turret(1)
            win.press("Measure: Reference position")
            win.wait_beside("Measure: Reference position", "Reference position done.")
            sim.turret(2)
            assert win.button("Save the calibration").is_disabled()
            win.press("Measure: Target position")
            win.wait_beside("Measure: Target position", "Target position done.")
            win.press("Save the calibration")
            win.wait_beside("Save the calibration", "Saved.")
            win.shot("4-calibration-saved")
            published = json.loads(
                machine.calibration_path().read_text(encoding="utf-8")
            )
            for measured, true in zip(
                published["objectives"]["2"]["translation_um"],
                module.TRUE_T[2],
                strict=True,
            ):
                assert abs(measured - true) < 0.51

        # 5. Zero point, back on the LAS X stand-in.
        win.open_step("Zero point")
        drive_stage_to(lasx, 42000.0, 31000.0)
        win.press("Set the zero point here")
        win.wait_beside(
            "Set the zero point here", "The zero point is saved. The setup is complete."
        )
        assert store.MACHINE.read_origin()["origin"]["x_um"] == 42000.0
        page.wait_for_function(
            "() => [...document.querySelectorAll('.step')].every(s => s.classList.contains('done'))"
        )
        win.shot("5-complete")

        win.assert_only_local_requests()
        assert win.problems == []
    finally:
        win.close()


# --- a pretend wizard, for the page's own rules -----------------------------------------


IDS = ["data_location", "limits", "orientation", "calibration", "origin"]
TITLES = [
    "Data location",
    "Travel limits",
    "Image orientation",
    "Objective calibration",
    "Zero point",
]


def _pretend(tmp_path, *, done=(), notes=None, saved=None, overrides=None):
    """The five steps with canned answers; *overrides* replaces single actions.

    *saved* gives a step's save time; calibration needs the orientation, as
    in the real wizard, so an orientation saved after it turns it orange.
    """
    from PIL import Image

    picture = tmp_path / "p.png"
    Image.new("L", (8, 8), 128).save(
        picture
    )  # a real picture, so the browser can draw it
    canned = {
        "data_location": {"save": lambda v: {"saved_to": "x"}},
        "limits": {
            "corner": lambda v: {
                "corner": v["corner"],
                "position": {"x_um": 1000.0 * v["corner"], "y_um": 5.0},
            },
            "save": lambda v: {"x_um": [1000, 4000], "y_um": [5, 5]},
        },
        "orientation": {
            "measure": lambda v: {
                "label": "turned 90°",
                "mirrored": True,
                "image": str(picture),
            },
            "check": lambda v: {"image": str(picture)},
            "save": lambda v: {"saved_to": "x"},
        },
        "calibration": {
            "start": lambda v: {"pair": v["pair_name"]},
            "measure": lambda v: {"part": v["part"]},
            "save": lambda v: {"saved_to": "x"},
        },
        "origin": {"set": lambda v: {"reference": {}, "saved_to": "x"}},
    }
    for (step, action), fn in (overrides or {}).items():
        canned[step][action] = fn
    notes = notes or {}
    saved = saved or {}
    return Wizard(
        "Pretend microscope setup",
        [
            Step(
                i,
                t,
                f"What {t.lower()} is for.",
                canned[i],
                (
                    lambda i=i: {
                        "done": i in done,
                        "note": notes.get(i),
                        "saved_at": saved.get(i),
                    }
                ),
                needs=("orientation",) if i == "calibration" else (),
            )
            for i, t in zip(IDS, TITLES, strict=True)
        ],
    )


@pytest.fixture
def window(browser):
    opened = []

    def open_(wizard, **context):
        win = Window(browser, wizard, **context)
        opened.append(win)
        return win

    yield open_
    for win in opened:
        win.close()


# --- the rail ---------------------------------------------------------------------------


def test_the_page_is_the_rail_and_the_boxes_with_no_top_row_canvas_or_sidebar(
    window, tmp_path
):
    win = window(_pretend(tmp_path))

    assert win.page.locator(".screen > *").count() == 2  # the rail and the stage
    for part in (
        ".wf-box",
        ".wf-select",
        ".brand",
        ".tabs",
        ".canvas-side",
        "canvas",
        "select",
    ):
        assert win.page.locator(part).count() == 0, part
    rail = win.page.locator(".rail").bounding_box()
    stage = win.page.locator(".stage").bounding_box()
    assert rail["x"] == 0 and stage["x"] == rail["width"]
    assert (
        win.page.locator(".steps").bounding_box()["y"] == 0
    )  # the steps start at the top
    win.assert_only_local_requests()
    assert win.problems == []


def test_a_step_opens_only_after_the_ones_before_it_are_done(window, tmp_path):
    win = window(_pretend(tmp_path))

    rail = win.rail()
    assert [r["locked"] for r in rail] == [False, True, True, True, True]
    assert rail[0]["active"] and rail[0]["badge"] == BLUE
    win.page.locator(".step").nth(3).click(force=True)  # a locked step does not open
    assert win.title() == "Data location"
    win.press("Check and save")
    win.wait_beside("Check and save", "Saved.")
    win.page.wait_for_function("() => !document.querySelectorAll('.step')[1].disabled")
    rail = win.rail()
    # Standing on a finished step keeps the blue; it turns green once you move on.
    assert rail[0]["done"] and rail[0]["badge"] == BLUE
    win.open_step("Travel limits")
    assert win.rail()[0]["badge"] == GREEN
    assert win.rail()[2]["locked"]


def test_steps_already_set_up_on_this_computer_are_green_and_say_so(window, tmp_path):
    win = window(
        _pretend(
            tmp_path,
            done={"data_location", "limits"},
            notes={"limits": "Saved on 1 October 2026 at 09:00 (UTC)."},
        )
    )

    rail = win.rail()
    assert [r["done"] for r in rail] == [True, True, False, False, False]
    assert win.title() == "Image orientation"  # opens on the first step not yet done
    assert rail[1]["badge"] == GREEN
    win.open_step("Travel limits")
    assert (
        "Already set up on this computer. Saved on 1 October 2026 at 09:00 (UTC). "
        "You can do this step again to replace it."
    ) in win.page.locator(".channel").inner_text()


def test_a_step_measured_on_an_older_setting_turns_orange_and_says_why(
    window, tmp_path
):
    win = window(
        _pretend(
            tmp_path,
            done=set(IDS[:4]),
            saved={
                "orientation": "2026-10-02T09:00:00+00:00",
                "calibration": "2026-10-01T09:00:00+00:00",
            },
        )
    )

    rail = win.rail()
    assert rail[3]["stale"] and rail[3]["review"] and rail[3]["badge"] == ORANGE
    assert not rail[2]["stale"] and not rail[2]["review"]
    win.open_step("Objective calibration")
    warning = win.page.locator(".side-note.warn")
    assert warning.inner_text() == (
        "The image orientation was saved again after this step. "
        "Do this step again, so that it matches."
    )
    assert (
        win.page.evaluate("e => getComputedStyle(e).color", warning.element_handle())
        == "rgb(194, 65, 12)"
    )


def test_a_step_saved_after_what_it_needs_stays_green(window, tmp_path):
    win = window(
        _pretend(
            tmp_path,
            done=set(IDS[:4]),
            saved={
                "orientation": "2026-10-01T09:00:00+00:00",
                "calibration": "2026-10-02T09:00:00+00:00",
            },
        )
    )

    assert not win.rail()[3]["stale"]
    assert win.page.locator(".side-note.warn").count() == 0


def test_the_rail_turns_while_a_step_works_and_its_foot_says_what_is_happening(
    window, tmp_path
):
    release = threading.Event()

    def slow(values):
        release.wait(10)
        return {"saved_to": "x"}

    win = window(_pretend(tmp_path, overrides={("data_location", "save"): slow}))
    assert win.page.locator("#status-bar").is_hidden()
    win.press("Check and save")

    win.page.locator("#status-bar", has_text="Checking the folder…").wait_for()
    assert win.rail()[0]["spinning"]
    release.set()
    win.wait_beside("Check and save", "Saved.")
    assert win.page.locator("#status-bar").is_hidden()
    assert not win.rail()[0]["spinning"]


# --- the boxes --------------------------------------------------------------------------


def test_each_step_is_boxes_with_a_heading_over_a_white_card(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits", "orientation"}))

    assert win.titles() == [
        "Objective calibration",
        "The pair",
        "Save",
    ]  # the parts appear after Start
    style = win.page.evaluate(
        """() => { const t = getComputedStyle(document.querySelector('.side-group-title'));
                   const b = getComputedStyle(document.querySelector('.side-group-body'));
                   return [t.textTransform, t.fontSize, b.backgroundColor, b.borderRadius]; }"""
    )
    assert style == ["uppercase", "11px", "rgb(255, 255, 255)", "10px"]
    button = win.button("Start")
    assert (
        win.page.evaluate("e => getComputedStyle(e).color", button.element_handle())
        == "rgb(3, 105, 161)"
    )


def test_an_error_is_said_beside_its_button_in_red_and_it_can_be_pressed_again(
    window, tmp_path
):
    attempts = []

    def save(values):
        attempts.append(values["folder"])
        if len(attempts) == 1:
            raise StepError("D:\\ZMART-data does not exist; make the folder first")
        return {"saved_to": "x"}

    win = window(_pretend(tmp_path, overrides={("data_location", "save"): save}))
    win.press("Check and save")

    win.wait_beside("Check and save", "does not exist", kind="bad")
    hint = win.beside("Check and save")
    assert hint.inner_text() == "D:\\ZMART-data does not exist; make the folder first"
    assert (
        win.page.evaluate("e => getComputedStyle(e).color", hint.element_handle())
        == "rgb(185, 28, 28)"
    )
    assert win.button("Check and save").is_enabled()
    assert not win.rail()[0]["done"] and win.rail()[1]["locked"]
    win.press("Check and save")
    win.wait_beside("Check and save", "Saved.")
    assert attempts == ["D:\\ZMART-data", "D:\\ZMART-data"]


def test_while_one_button_works_no_other_can_be_pressed(window, tmp_path):
    release = threading.Event()
    calls = []

    def slow(values):
        calls.append(values["corner"])
        release.wait(10)
        return {"corner": values["corner"], "position": {"x_um": 1.0, "y_um": 2.0}}

    win = window(
        _pretend(
            tmp_path, done={"data_location"}, overrides={("limits", "corner"): slow}
        )
    )
    win.press("Record corner 1")
    win.page.locator("#status-bar").wait_for()

    assert all(win.button(f"Record corner {n}").is_disabled() for n in (1, 2, 3, 4))
    win.button("Record corner 2").click(force=True)
    release.set()
    win.wait_beside("Record corner 1", "X 1.0 µm, Y 2.0 µm")
    assert calls == [1]
    assert win.button("Record corner 2").is_enabled()


def test_the_limits_can_be_saved_only_after_four_corners(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location"}))

    for number in (1, 2, 4, 2):  # in any order, and one again
        assert win.button("Save the limits").is_disabled()
        win.press(f"Record corner {number}")
        win.wait_beside(f"Record corner {number}", "X ")
    assert win.button("Save the limits").is_disabled()
    win.press("Record corner 3")
    win.wait_beside("Record corner 3", "X 3000.0 µm, Y 5.0 µm")
    assert win.button("Save the limits").is_enabled()


def test_the_orientation_buttons_follow_measure_check_save(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits"}))

    assert win.button("Take a check picture").is_disabled()
    assert win.button("Save the orientation").is_disabled()
    assert win.picture_in("Measure").is_hidden()
    win.press("Measure the orientation")
    win.wait_beside(
        "Measure the orientation", "Found: turned 90° (the image is also mirrored)."
    )
    win.wait_for_picture("Measure")
    assert win.button("Take a check picture").is_enabled()
    assert win.button("Save the orientation").is_disabled()
    win.press("Take a check picture")
    win.wait_beside("Take a check picture", "Does this picture follow the stage?")
    assert win.button("Save the orientation").is_enabled()


def test_a_small_picture_is_shown_at_a_readable_size(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits"}))
    win.press("Measure the orientation")
    win.wait_for_picture("Measure")

    assert (
        win.picture_in("Measure").bounding_box()["width"] >= 600
    )  # the file is 8 pixels wide


def test_the_calibration_parts_appear_after_start_and_save_after_the_fourth(
    window, tmp_path
):
    started = []
    overrides = {
        ("calibration", "start"): lambda v: (
            started.append(v) or {"pair": v["pair_name"]}
        )
    }
    win = window(
        _pretend(
            tmp_path,
            done={"data_location", "limits", "orientation"},
            overrides=overrides,
        )
    )

    win.page.get_by_label("Name of the pair").fill("20x-40x")
    win.page.get_by_label("Reference slot").fill("2")
    win.press("Start")
    win.wait_beside("Start", "Started.")
    assert started == [{"pair_name": "20x-40x", "reference_slot": 2}]
    assert win.titles()[2:6] == [
        "1. Reference focus",
        "2. Target focus",
        "3. Reference position",
        "4. Target position",
    ]
    for name in ("Reference focus", "Target focus", "Reference position"):
        win.press(f"Measure: {name}")
        win.wait_beside(f"Measure: {name}", f"{name} done.")
        assert win.button("Save the calibration").is_disabled()
    win.press("Measure: Target position")
    win.wait_beside("Measure: Target position", "Target position done.")
    assert win.button("Save the calibration").is_enabled()


def test_an_error_on_a_long_step_is_said_where_it_can_be_seen(window, tmp_path):
    def refuse(values):
        raise StepError(
            "The target objective is still the reference objective; switch to the target and try again."
        )

    win = window(
        _pretend(
            tmp_path,
            done={"data_location", "limits", "orientation"},
            overrides={("calibration", "measure"): refuse},
        ),
        viewport={"width": 1100, "height": 600},
    )
    win.press("Start")
    win.wait_beside("Start", "Started.")
    win.button("Measure: Target focus").scroll_into_view_if_needed()
    win.press("Measure: Target focus")

    win.wait_beside(
        "Measure: Target focus", "still the reference objective", kind="bad"
    )
    box = win.beside("Measure: Target focus").bounding_box()
    assert box and 0 <= box["y"] and box["y"] + box["height"] <= 600


def test_the_last_step_finishes_the_setup(window, tmp_path):
    win = window(_pretend(tmp_path, done=set(IDS[:4])))

    assert win.title() == "Zero point"
    win.press("Set the zero point here")
    win.wait_beside("Set the zero point here", "The setup is complete.")
    win.page.wait_for_function(
        "() => [...document.querySelectorAll('.step')].every(s => s.classList.contains('done'))"
    )


def test_the_page_fits_a_narrow_window_without_sideways_scrolling(window, tmp_path):
    win = window(_pretend(tmp_path), viewport={"width": 390, "height": 800})

    overflow = win.page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 0
    assert (
        win.page.locator(".rail").is_visible()
        and win.page.locator(".stage").is_visible()
    )


def test_the_page_follows_a_dark_screen(window, tmp_path):
    win = window(_pretend(tmp_path), color_scheme="dark")

    rail = win.page.evaluate(
        "() => getComputedStyle(document.querySelector('.rail')).backgroundColor"
    )
    ink = win.page.evaluate(
        "() => getComputedStyle(document.querySelector('.step-name')).color"
    )
    badge = win.rail()[0]["badge"]
    assert rail == "rgb(16, 27, 44)"
    assert ink == "rgb(125, 211, 252)"  # the active step's name, in the dark accent
    assert badge == "rgb(56, 189, 248)"


def test_every_control_has_a_name_a_screen_reader_can_say(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits", "orientation"}))
    win.press("Start")
    win.wait_beside("Start", "Started.")

    unnamed = win.page.evaluate(
        """() => [...document.querySelectorAll('.stage input, .stage button, .rail button')]
            .filter(e => e.offsetParent !== null)
            .filter(e => !(e.labels && e.labels.length) && !e.textContent.trim() && !e.getAttribute('aria-label'))
            .map(e => e.outerHTML)"""
    )
    assert unnamed == []
