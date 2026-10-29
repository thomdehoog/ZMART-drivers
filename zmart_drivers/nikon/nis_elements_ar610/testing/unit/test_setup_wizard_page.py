"""The setup wizard's page in a real browser (Chromium, through Playwright), on the mock NIS-Elements.

The built page in ``driver_setup/wizard/page/`` is opened from the wizard's
own server and clicked through the way an operator would: all five steps,
one after the other in one window, through the real bridge server to the
mock. What the page shows is checked, and so is what was saved on disk.
A few of the page's own rules are checked too: a step stays locked until
the one before it is done, a refusal is said in red, the page fits a narrow
window, and it asks nothing of the internet.

Set ``ZMART_WIZARD_SCREENSHOTS`` to a folder to keep a screenshot of every
screen of the walk. Where no browser can start, these tests are skipped.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from zmart_drivers.nikon.nis_elements_ar610.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.optical_calibration import (
    model,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.orientation import (
    Orientation,
    load_rig_orientation,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard.server import (
    WizardServer,
)
from zmart_drivers.nikon.nis_elements_ar610.driver_setup.wizard.steps import (
    nikon_wizard,
)

# Where a remote test container keeps its browser, when Playwright's own download is missing.
_INSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"

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
    """The Nikon wizard served and opened in a browser tab, recording what the page asks for."""

    def __init__(self, browser, **context):
        self.wizard = nikon_wizard()
        self.server = WizardServer(self.wizard).start()
        self.context = browser.new_context(
            **{"viewport": {"width": 1440, "height": 900}, **context}
        )
        self.page = self.context.new_page()
        self.requests: list[str] = []
        self.problems: list[str] = []
        self.shots = 0
        self.page.on("request", lambda r: self.requests.append(r.url))
        self.page.on(
            "console", lambda m: m.type == "error" and self.problems.append(m.text)
        )
        self.page.on("pageerror", lambda e: self.problems.append(str(e)))
        self.page.set_default_timeout(30000)
        self.page.goto(self.server.url)
        self.page.locator(".step").first.wait_for()

    def close(self):
        self.wizard.call("connect", "disconnect")
        self.context.close()
        self.server.stop()

    def rail(self) -> list[dict]:
        return self.page.eval_on_selector_all(
            ".step",
            "els => els.map(e => ({name: e.querySelector('.step-name').textContent,"
            " done: e.classList.contains('done'), active: e.classList.contains('active'),"
            " locked: e.disabled}))",
        )

    def open_step(self, number: int) -> None:
        self.page.locator(".step").nth(number - 1).click()

    def press(self, name: str) -> None:
        self.page.get_by_role("button", name=name, exact=True).first.click()

    def wait_done(self, number: int) -> None:
        self.page.locator(".step").nth(number - 1).and_(
            self.page.locator(".step.done")
        ).wait_for()

    def shot(self, name: str) -> None:
        if _SCREENSHOTS:
            self.shots += 1
            Path(_SCREENSHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(Path(_SCREENSHOTS) / f"{self.shots:02d}_{name}.png"),
                full_page=True,
            )

    def connect(self, port: int, folder: Path) -> None:
        folder.mkdir(exist_ok=True)
        self.page.get_by_label("Bridge port").fill(str(port))
        self.page.get_by_label("Output folder").fill(str(folder))
        self.press("Connect")
        self.page.get_by_text("Connection checks").wait_for()


@pytest.fixture
def window(browser):
    opened = Window(browser)
    yield opened
    opened.close()


def test_an_operator_sets_up_the_microscope_from_start_to_finish(
    window, connection, mock, tmp_path
):
    page = window.page
    mock.turn = (90, False)
    mock.travel_s = 0.0
    assert [s["name"] for s in window.rail()] == [
        "Connect",
        "Define limits",
        "Define coordinate system origin",
        "Image-to-stage calibration",
        "Objective calibration",
    ]
    assert [s["locked"] for s in window.rail()] == [False, True, True, True, True]
    window.shot("connect")

    # 1. Connect.
    window.connect(connection["port"], tmp_path / "data")
    window.wait_done(1)
    assert page.get_by_text("nis_elements_ar610 0.7.0b9").is_visible()
    window.shot("connected")

    # 2. Define limits: import the four corners, then save.
    window.open_step(2)
    page.get_by_text("From the defaults.").wait_for()
    for x, y in ((-20000, -10000), (20000, -10000), (-20000, 10000), (20000, 10000)):
        mock.move_xyz(x, y, 500)
        page.get_by_role("button", name="Import", exact=True).first.click()
        page.get_by_text(f"x {x:.1f} · y {y:.1f} µm").wait_for()
    page.get_by_text("X -20000 … 20000 · Y -10000 … 10000 set above.").wait_for()
    window.shot("limits")
    window.press("Save and adopt")
    window.wait_done(2)
    saved = store.MACHINE.read_record("limits")
    assert saved["x_um"] == {"range": [-20000.0, 20000.0]}

    # 3. The origin: drive there, Read, Save and adopt.
    mock.move_xyz(1000, -500, 500)
    window.open_step(3)
    window.press("Read")
    page.get_by_text("Becomes (0, 0, 0):").wait_for()
    window.shot("origin")
    window.press("Save and adopt")
    window.wait_done(3)
    assert store.MACHINE.read_record("origin")["origin"]["x_um"] == 1000.0

    # 4. The orientation: Start, take the check picture, then save.
    window.open_step(4)
    page.get_by_label("stage step in micrometres").fill("10")
    window.press("Start")
    page.get_by_text("90° clockwise").wait_for()
    window.press("Take a check picture")
    page.locator("img.setup-picture").wait_for()
    window.shot("orientation")
    window.press("Save and adopt")
    window.wait_done(4)
    turned, _ = load_rig_orientation()
    assert turned == Orientation(90, False)

    # 5. The objective calibration: the 10x as reference, the 20x measured by eye.
    window.open_step(5)
    page.get_by_label("reference objective").select_option("1")
    page.locator(".setup-set").first.click()
    window.press("Record under Plan Apo 10x")
    page.get_by_text("Recorded: x 1000.0 · y -500.0 · z 500.00 µm").wait_for()
    window.press("Turn to Apo 20x WI")
    page.get_by_role("button", name="Apo 20x WI is in place").wait_for()
    mock.move_xyz(1012.5, -503.0, 541.0)
    window.press("Record under Apo 20x WI")
    page.locator(".setup-xyz-value").nth(2).filter(has_text="+41.00").wait_for()
    window.shot("calibration")
    window.press("Save and adopt")
    window.wait_done(5)
    translations, _ = model.load_objective_calibration()
    assert translations[2] == (12.5, -3.0, 41.0)

    assert all(step["done"] for step in window.rail())
    assert window.problems == []
    assert all(url.startswith(window.server.url) for url in window.requests)


def test_a_refusal_is_said_in_red_and_the_operator_can_try_again(
    window, connection, tmp_path
):
    page = window.page
    page.get_by_label("Bridge port").fill("fifty")
    page.get_by_label("Output folder").fill(str(tmp_path))
    window.press("Connect")
    refusal = page.locator(".setup-note.bad")
    refusal.wait_for()
    assert "whole number" in refusal.text_content()
    assert window.rail()[1]["locked"] is True

    window.connect(connection["port"], tmp_path / "data")
    window.wait_done(1)
    assert window.rail()[1]["locked"] is False


def test_the_page_fits_a_narrow_window_without_sideways_scrolling(browser):
    narrow = Window(browser, viewport={"width": 380, "height": 800})
    try:
        overflow = narrow.page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0
    finally:
        narrow.close()
