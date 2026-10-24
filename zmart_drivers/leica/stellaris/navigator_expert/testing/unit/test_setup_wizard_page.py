"""The setup wizard's page in a real browser (Chromium, through Playwright).

The built page in ``driver_setup/wizard/page/`` is opened from the wizard's own
server and clicked through the way an operator would. Two kinds of test:

- **The whole setup on stand-ins.** All five real Leica steps, one after the
  other in one window, on the stand-ins of ``helpers/wizard_standins.py``: the
  offline LAS X for the data location, the limits and the zero point, the
  orientation tests' pictures, and the calibration tests' physical simulator.
  What the page shows is checked, and so is what was saved on disk.
- **The page's behaviour.** A pretend wizard with canned answers checks the
  rules of the page itself: a step opens only after the ones before it, an
  error is shown in words and the button can be pressed again, a busy button
  cannot be pressed twice, a step already set up says so, and the page asks
  nothing of the internet.

Where no browser can start, these tests are skipped rather than failed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
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
        self.page.locator(".rail-step").first.wait_for()

    def close(self):
        self.context.close()
        self.server.stop()

    # what the operator sees
    def rail(self) -> list[dict]:
        return self.page.eval_on_selector_all(
            ".rail-step",
            "els => els.map(e => ({name: e.querySelector('.name').childNodes[0].textContent,"
            " num: e.querySelector('.num').textContent, done: e.classList.contains('done'),"
            " locked: e.disabled, current: e.classList.contains('current'),"
            " note: (e.querySelector('small') || {}).textContent || null}))",
        )

    def title(self) -> str:
        return self.page.locator("#panel h2").inner_text()

    def message(self):
        return self.page.locator(".message")

    def press(self, label: str):
        self.page.get_by_role("button", name=label, exact=True).click()

    def wait_for_message(self, text: str):
        self.page.locator(".message", has_text=text).wait_for()

    def shot(self, name: str):
        if _SCREENSHOTS:
            Path(_SCREENSHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(Path(_SCREENSHOTS) / f"{name}.png"), full_page=True
            )

    def assert_only_local_requests(self):
        outside = [u for u in self.requests if not u.startswith(self.server.url)]
        assert outside == [], outside

    def picture_is_shown(self) -> bool:
        return self.page.eval_on_selector(
            ".picture", "img => !img.hidden && img.complete && img.naturalWidth > 0"
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
        # calibration, so the page opens on the first step not yet done.
        rail = win.rail()
        assert rail[1]["note"] == "Saved on 1 January 2026 at 00:00 (UTC)"
        assert [r["name"] for r in rail] == [
            "Data location",
            "Travel limits",
            "Image orientation",
            "Objective calibration",
            "Zero point",
        ]
        assert win.title() == "Data location"
        assert rail[0]["current"] and not rail[0]["done"]
        # A step that is not done stays locked until the ones before it are;
        # one already set up on this computer (here the fixture limits) can be opened.
        assert rail[1]["done"] and not rail[1]["locked"]
        assert rail[2]["locked"] and rail[4]["locked"]
        win.shot("1-data-location")

        # 1. Data location: a wrong folder first, then a good one.
        page.get_by_label("Folder").fill(str(tmp_path / "missing"))
        win.press("Check and save")
        win.page.locator(".message.error").wait_for()
        assert "does not exist" in win.message().inner_text()
        win.shot("1b-data-location-refused")
        data = tmp_path / "ZMART-data"
        data.mkdir()
        page.get_by_label("Folder").fill(str(data))
        win.press("Check and save")
        win.wait_for_message("Saved.")
        rail = win.rail()
        assert (
            rail[0]["done"] and rail[0]["num"] == "✓" and rail[0]["note"] == str(data)
        )
        assert not rail[1]["locked"]
        win.press("Next step →")

        # 2. Travel limits: drive the stand-in's stage to each corner, press its button.
        assert win.title() == "Travel limits"
        save = page.get_by_role("button", name="Save the limits")
        assert save.is_disabled()
        for number, (x, y) in enumerate(CORNERS_UM, start=1):
            drive_stage_to(lasx, x, y)
            win.press(f"Record corner {number}")
            page.locator(".row", has_text=f"Record corner {number}").locator(
                ".where", has_text=f"X {x:.1f} µm, Y {y:.1f} µm"
            ).wait_for()
            assert save.is_disabled() == (number < 4)
        win.shot("2-limits-corners")
        save.click()
        win.wait_for_message(
            "Saved. The stage may travel in X from 20000.0 to 90000.0 µm, and in Y from 15000.0 to 70000.0 µm."
        )
        latest = store.MACHINE.latest_snapshot("limits")
        written = json.loads((latest / "limits.json").read_text(encoding="utf-8"))
        assert written["x_um"]["range"] == [20000.0, 90000.0]
        win.press("Next step →")

        # 3. Image orientation, on the orientation stand-in for as long as this step lasts.
        assert win.title() == "Image orientation"
        with pytest.MonkeyPatch.context() as mp:
            machine = install_orientation(mp, tmp_path / "orientation")
            assert page.get_by_role("button", name="Take a check picture").is_disabled()
            page.get_by_label("Size of the stage move (µm)").fill("30")
            win.press("Measure the orientation")
            win.wait_for_message("Found:")
            page.wait_for_function(
                "() => document.querySelector('.picture').naturalWidth > 0"
            )
            assert win.picture_is_shown()
            assert page.get_by_role("button", name="Save the orientation").is_disabled()
            win.press("Take a check picture")
            win.wait_for_message("Does this picture follow the stage?")
            page.wait_for_function(
                "() => document.querySelector('.picture').src.endsWith('.jpg')"
            )
            page.wait_for_function(
                "() => document.querySelector('.picture').naturalWidth > 0"
            )
            win.shot("3-orientation-check")
            win.press("Save the orientation")
            win.page.locator(".message.ok", has_text="Saved.").wait_for()
            assert (
                machine.latest_snapshot("orientation") / "orientation.json"
            ).is_file()
        assert win.rail()[2]["done"]
        win.press("Next step →")

        # 4. Objective calibration, on the physical simulator; the operator turns the turret.
        assert win.title() == "Objective calibration"
        with pytest.MonkeyPatch.context() as mp:
            module, sim, machine = install_calibration(mp, tmp_path / "calibration")
            assert not page.locator(".measure").is_visible()
            page.get_by_label("Name of the pair").fill("10x-20x")
            page.get_by_label("Reference objective (turret slot)").fill("1")
            win.press("Start")
            page.locator(".measure").wait_for()
            sim.configure_stack(95.0, 105.0)
            win.press("Measure: Reference focus")
            win.wait_for_message("Reference focus done.")
            # A mistake first: the target focus with the reference objective still in.
            win.press("Measure: Target focus")
            win.page.locator(".message.error").wait_for()
            assert "reference objective" in win.message().inner_text()
            win.shot("4b-calibration-wrong-objective")
            sim.turret(2)
            sim.zwide = 104.0
            sim.configure_stack(101.0, 111.0)
            win.press("Measure: Target focus")
            win.wait_for_message("Target focus done.")
            sim.turret(1)
            win.press("Measure: Reference position")
            win.wait_for_message("Reference position done.")
            sim.turret(2)
            assert page.get_by_role("button", name="Save the calibration").is_disabled()
            win.press("Measure: Target position")
            win.wait_for_message("Target position done.")
            win.shot("4-calibration-measured")
            win.press("Save the calibration")
            win.wait_for_message("Saved.")
            published = json.loads(
                machine.calibration_path().read_text(encoding="utf-8")
            )
            for measured, true in zip(
                published["objectives"]["2"]["translation_um"],
                module.TRUE_T[2],
                strict=True,
            ):
                assert abs(measured - true) < 0.51
        win.press("Next step →")

        # 5. Zero point, back on the LAS X stand-in.
        assert win.title() == "Zero point"
        drive_stage_to(lasx, 42000.0, 31000.0)
        win.press("Set the zero point here")
        win.wait_for_message("The zero point is saved. The setup is complete.")
        assert store.MACHINE.read_origin()["origin"]["x_um"] == 42000.0
        assert not page.get_by_role(
            "button", name="Next step →"
        ).is_visible()  # the last step
        rail = win.rail()
        assert all(r["done"] for r in rail), rail
        win.shot("5-complete")

        win.assert_only_local_requests()
        assert win.problems == []
    finally:
        win.close()


# --- the page's own behaviour, with a pretend wizard ------------------------------------


IDS = ["data_location", "limits", "orientation", "calibration", "origin"]
TITLES = [
    "Data location",
    "Travel limits",
    "Image orientation",
    "Objective calibration",
    "Zero point",
]


def _pretend(tmp_path, *, done=(), notes=None, overrides=None):
    """The five steps with canned answers; *overrides* replaces single actions."""
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
    return Wizard(
        "Pretend microscope setup",
        [
            Step(
                i,
                t,
                f"What {t.lower()} is for.",
                canned[i],
                (lambda i=i: {"done": i in done, "note": notes.get(i)}),
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


def test_a_step_opens_only_after_the_ones_before_it_are_done(window, tmp_path):
    win = window(_pretend(tmp_path))

    rail = win.rail()
    assert [r["num"] for r in rail] == ["1", "2", "3", "4", "5"]
    assert [r["locked"] for r in rail] == [False, True, True, True, True]
    win.page.locator(".rail-step").nth(3).click(
        force=True
    )  # a locked step does not open
    assert win.title() == "Data location"
    assert win.page.locator("#panel .kicker").text_content() == "Step 1 of 5"
    win.assert_only_local_requests()
    assert win.problems == []


def test_a_finished_step_can_be_opened_again_from_the_rail(window, tmp_path):
    win = window(_pretend(tmp_path))
    win.press("Check and save")
    win.wait_for_message("Saved.")
    win.press("Next step →")
    assert win.title() == "Travel limits"

    win.page.locator(".rail-step").first.click()

    assert win.title() == "Data location"
    assert win.rail()[0]["done"] and win.rail()[0]["current"]


def test_steps_already_set_up_on_this_computer_are_done_and_say_so(window, tmp_path):
    win = window(
        _pretend(
            tmp_path,
            done={"data_location", "limits"},
            notes={"limits": "Latest saved: 2026-10-01"},
        )
    )

    rail = win.rail()
    assert [r["done"] for r in rail] == [True, True, False, False, False]
    assert win.title() == "Image orientation"  # opens on the first step not yet done
    assert rail[1]["note"] == "Latest saved: 2026-10-01"
    win.page.locator(".rail-step").nth(1).click()
    assert (
        "Already set up on this computer. Latest saved: 2026-10-01."
        in win.page.locator(".already").inner_text()
    )


def test_an_error_is_shown_in_words_and_the_button_can_be_pressed_again(
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

    error = win.page.locator(".message.error")
    error.wait_for()
    assert error.inner_text() == "D:\\ZMART-data does not exist; make the folder first"
    assert win.page.get_by_role("button", name="Check and save").is_enabled()
    assert not win.rail()[0]["done"] and win.rail()[1]["locked"]
    win.press("Check and save")
    win.page.locator(".message.ok").wait_for()
    assert attempts == ["D:\\ZMART-data", "D:\\ZMART-data"]
    assert win.rail()[0]["done"]


def test_a_busy_button_shows_working_and_cannot_be_pressed_twice(window, tmp_path):
    release = threading.Event()
    calls = []

    def slow(values):
        calls.append(1)
        release.wait(10)
        return {"saved_to": "x"}

    win = window(_pretend(tmp_path, overrides={("data_location", "save"): slow}))
    button = win.page.get_by_role("button", name="Check and save")
    button.click()
    win.wait_for_message("Working…")

    assert button.is_disabled()
    button.click(force=True)  # a second press while busy does nothing
    release.set()
    win.wait_for_message("Saved.")
    assert calls == [1]
    assert button.is_enabled()


def test_the_limits_can_be_saved_only_after_four_corners(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location"}))
    save = win.page.get_by_role("button", name="Save the limits")

    for number in (1, 2, 4, 2):  # in any order, and one again
        assert save.is_disabled()
        win.press(f"Record corner {number}")
        win.page.locator(".row", has_text=f"Record corner {number}").locator(
            ".where", has_text="X "
        ).wait_for()
    assert save.is_disabled()
    win.press("Record corner 3")
    win.page.locator(".row", has_text="Record corner 3").locator(
        ".where", has_text="X 3000.0 µm, Y 5.0 µm"
    ).wait_for()
    assert save.is_enabled()


def test_the_orientation_buttons_follow_measure_check_save(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits"}))
    check = win.page.get_by_role("button", name="Take a check picture")
    save = win.page.get_by_role("button", name="Save the orientation")

    assert check.is_disabled() and save.is_disabled()
    assert win.page.locator(".picture").is_hidden()
    win.press("Measure the orientation")
    win.wait_for_message("Found: turned 90° (the image is also mirrored).")
    win.page.wait_for_function(
        "() => document.querySelector('.picture').naturalWidth > 0"
    )
    assert check.is_enabled() and save.is_disabled()
    win.press("Take a check picture")
    win.wait_for_message("Does this picture follow the stage?")
    assert save.is_enabled()


def test_the_calibration_measurements_appear_after_start_and_save_after_the_fourth(
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
    save = win.page.get_by_role("button", name="Save the calibration")

    assert win.page.locator(".measure").is_hidden()
    win.page.get_by_label("Name of the pair").fill("20x-40x")
    win.page.get_by_label("Reference objective (turret slot)").fill("2")
    win.press("Start")
    win.page.locator(".measure").wait_for()
    assert started == [{"pair_name": "20x-40x", "reference_slot": 2}]
    parts = win.page.locator(".part h3").all_inner_texts()
    assert parts == [
        "1. Reference focus",
        "2. Target focus",
        "3. Reference position",
        "4. Target position",
    ]
    for name in ("Reference focus", "Target focus", "Reference position"):
        win.press(f"Measure: {name}")
        win.wait_for_message(f"{name} done.")
        assert save.is_disabled()
    win.press("Measure: Target position")
    win.wait_for_message("Target position done.")
    assert save.is_enabled()


def test_the_last_step_finishes_the_setup_without_a_next_button(window, tmp_path):
    win = window(_pretend(tmp_path, done=set(IDS[:4])))

    assert win.title() == "Zero point"
    win.press("Set the zero point here")
    win.wait_for_message("The setup is complete.")
    assert all(r["done"] for r in win.rail())
    assert not win.page.get_by_role("button", name="Next step →").is_visible()


def test_the_page_fits_a_narrow_window_without_sideways_scrolling(window, tmp_path):
    win = window(_pretend(tmp_path), viewport={"width": 390, "height": 800})

    overflow = win.page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 0
    assert (
        win.page.locator("#rail").is_visible()
        and win.page.locator("#panel").is_visible()
    )


def test_the_page_follows_a_dark_screen(window, tmp_path):
    win = window(_pretend(tmp_path), color_scheme="dark")

    background = win.page.evaluate(
        "() => getComputedStyle(document.body).backgroundColor"
    )
    ink = win.page.evaluate(
        "() => getComputedStyle(document.querySelector('#panel h2')).color"
    )
    assert background == "rgb(5, 8, 15)"
    assert ink == "rgb(230, 237, 246)"


def test_every_control_has_a_name_a_screen_reader_can_say(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits", "orientation"}))
    win.press("Start")
    win.page.locator(".measure").wait_for()

    unnamed = win.page.evaluate(
        """() => [...document.querySelectorAll('#panel input, #panel button')]
            .filter(e => !e.hidden && e.offsetParent !== null)
            .filter(e => !(e.labels && e.labels.length) && !e.textContent.trim() && !e.getAttribute('aria-label'))
            .map(e => e.outerHTML)"""
    )
    assert unnamed == []


def test_the_answer_appears_beside_the_button_and_in_view_on_a_long_step(
    window, tmp_path
):
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
    win.page.locator(".measure").wait_for()
    part = win.page.locator(".part", has_text="2. Target focus")
    part.get_by_role("button").click()

    error = part.locator(".message.error")  # inside the part whose button was pressed
    error.wait_for()
    assert "still the reference objective" in error.inner_text()
    box = error.bounding_box()
    assert box and 0 <= box["y"] and box["y"] + box["height"] <= 600


def test_a_small_picture_is_shown_at_a_readable_size(window, tmp_path):
    win = window(_pretend(tmp_path, done={"data_location", "limits"}))
    win.press("Measure the orientation")
    win.page.wait_for_function(
        "() => document.querySelector('.picture').naturalWidth > 0"
    )

    shown = win.page.locator(".picture").bounding_box()
    assert shown["width"] >= 600  # the file is 8 pixels wide
