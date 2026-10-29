"""The setup wizard's page in a real browser (Chromium, through Playwright), on the pretend NIS.

The built page in ``driver_setup/wizard/page/`` is opened from the wizard's
own server and clicked through the way an operator would: all five steps,
one after the other, in one window, on the bridge stand-in. What the page
shows is checked, and so is what was saved on disk. Where no browser can
start, these tests are skipped.

Set ``ZMART_WIZARD_SCREENSHOTS`` to a folder to keep a screenshot of every
step of the walk.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``testing/unit/test_setup_wizard_page.py``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from zmart_drivers.nikon.nis_elements_ar7.driver_setup import store
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard.server import WizardServer
from zmart_drivers.nikon.nis_elements_ar7.driver_setup.wizard.steps import nikon_wizard

_INSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"
_SCREENSHOTS = os.environ.get("ZMART_WIZARD_SCREENSHOTS")
GREEN = "rgb(22, 163, 74)"


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


@pytest.fixture
def window(browser, stand_in, key_file):
    wizard = nikon_wizard({"port": stand_in.port})
    server = WizardServer(wizard).start()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    problems, requests = [], []
    page.on("request", lambda r: requests.append(r.url))
    page.on("console", lambda m: m.type == "error" and problems.append(m.text))
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.on(
        "response", lambda r: r.status >= 400 and problems.append(f"{r.status} {r.url}")
    )
    page.set_default_timeout(30000)
    page.goto(server.url)
    page.locator(".step").first.wait_for()
    page.problems, page.requests, page.server_url = problems, requests, server.url
    yield page
    wizard.call("connect", "disconnect", {})
    context.close()
    server.stop()


def shot(page, name: str) -> None:
    if _SCREENSHOTS:
        Path(_SCREENSHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(_SCREENSHOTS) / f"{name}.png"), full_page=True)


def cell(page, title: str):
    heading = page.locator(".side-group-title").filter(
        has_text=re.compile(f"^{re.escape(title)}$")
    )
    return page.locator(".side-group").filter(has=heading)


def press(page, label: str, within=None):
    (within or page).get_by_role("button", name=label, exact=True).first.click()


def says(page, text: str, kind: str = "ok", within=None):
    selector = f".setup-note.{kind}" if kind else ".setup-note"
    (within or page).locator(selector, has_text=text).first.wait_for()


def open_step(page, name: str):
    page.locator(".step", has_text=name).click()


def _turn(saved: dict) -> tuple:
    """The quarter turn and mirror a saved orientation.json holds."""
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
        orientation_from_config,
    )

    orientation = orientation_from_config(saved)
    return orientation.rotate_deg, orientation.mirrored


def badges(page) -> list[str]:
    return page.eval_on_selector_all(
        ".step .step-n", "els => els.map(e => getComputedStyle(e).backgroundColor)"
    )


def connect(page, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    page.get_by_label("Output folder").fill(str(folder))
    press(page, "Connect")
    page.locator(".check-row", has_text="NIS-Elements").wait_for()


def test_an_operator_sets_up_the_microscope_from_start_to_finish(
    window, stand_in, tmp_path
):
    page = window
    stand_in.scope.camera_mount = (270, False)

    # 1. Connect: write the bridge's macro, then connect.
    press(page, "Write the macro")
    says(page, "Run Macro From File")
    connect(page, tmp_path / "data")
    shot(page, "1-connect")
    assert store.machine().read("data_location")["folder"] == str(
        (tmp_path / "data").resolve()
    )

    # 2. Define limits: four corners, the focus, then save.
    open_step(page, "Define limits")
    corners = cell(page, "Import X/Y stage limits")
    corners.wait_for()
    for x, y in ((-500, -400), (500, -400), (-500, 400), (500, 400)):
        stand_in.scope.place(x, y, 3000.0)
        press(page, "Import", corners)
        page.wait_for_function("() => !document.body.classList.contains('busy')")
    for z in (2000.0, 4000.0):
        stand_in.scope.place(0, 0, z)
        press(page, "Import", corners)
        page.wait_for_function("() => !document.body.classList.contains('busy')")
    stand_in.scope.place(0, 0, 3000.0)
    says(page, "set above", within=corners)
    press(page, "Save and adopt", corners)
    says(page, "Adopted.")
    shot(page, "2-limits")
    limits = store.machine().read("limits")
    assert limits["x_um"]["range"] == [-500.0, 500.0] and limits["z_um"]["range"] == [
        2000.0,
        4000.0,
    ]

    # 3. Origin: read, then save.
    open_step(page, "Define coordinate system origin")
    press(page, "Read")
    says(page, "Becomes (0, 0, 0):", kind="")
    press(page, "Save and adopt")
    says(page, "Adopted.")
    shot(page, "3-origin")
    assert store.machine().read("origin")["origin"]["x_um"] == 0.0

    # 4. Orientation: three pictures, the turned picture shown, then save.
    open_step(page, "Image-to-stage orientation")
    page.get_by_label("how far the stage moves, in µm").fill("6")
    press(page, "Start")
    says(page, "Measured: turned 270 degrees clockwise, not mirrored")
    picture = page.locator('img[alt^="the first picture"]')
    picture.wait_for()
    page.wait_for_function(
        "img => img.complete && img.naturalWidth > 0", arg=picture.element_handle()
    )
    press(page, "Save and adopt")
    says(page, "Adopted.")
    shot(page, "4-orientation")
    assert ("270", "False") == tuple(
        str(v) for v in _turn(store.machine().read("orientation"))
    )

    # 5. Hardware: try the EPI shutter, then note it looks right.
    open_step(page, "Hardware settings")
    page.get_by_label("shutter/EPI, a value to try").fill("1")
    row = page.locator(".setup-row", has_text="shutter/EPI")
    press(page, "Try", row)
    row.filter(has_text="now 1").wait_for()
    assert stand_in.scope.shutters["EPI"] == 1, page.locator(
        ".setup-column"
    ).inner_text()
    press(page, "Looks right")
    says(page, "Noted.")
    shot(page, "5-hardware")

    done = page.eval_on_selector_all(
        ".step", "els => els.map(e => e.classList.contains('done'))"
    )
    assert done == [True] * 5
    assert badges(page)[:4] == [GREEN] * 4  # the step you stand on stays blue
    assert page.problems == []
    assert [u for u in page.requests if not u.startswith(page.server_url)] == []


def set_up_before() -> None:
    """Save limits, an origin and an orientation, as an earlier setup would have, so every step opens."""
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup.limits import config
    from zmart_drivers.nikon.nis_elements_ar7.driver_setup.orientation import (
        Orientation,
        save_orientation,
    )

    config.adopt_limits(
        config.build_payload(
            {"x_um": [-50000, 50000], "y_um": [-30000, 30000], "z_um": [0, 10000]}
        )
    )
    store.machine().write(
        "origin", {"origin": {"x_um": 0.0, "y_um": 0.0, "z_um": 0.0, "objective": None}}
    )
    save_orientation(Orientation())


def test_a_guarded_try_is_refused_in_red_and_changes_nothing(
    window, stand_in, tmp_path
):
    page = window
    set_up_before()
    page.reload()
    page.locator(".step").first.wait_for()
    connect(page, tmp_path)
    open_step(page, "Hardware settings")
    page.get_by_label("laser_power/0.1, a value to try").fill("20")
    press(page, "Try", page.locator(".setup-row", has_text="laser_power/0.1"))
    says(page, "guarded", kind="bad")
    assert stand_in.scope.laser_lines["488 nm"][1] == 0.0


def test_every_control_has_a_name_a_screen_reader_can_say(window, tmp_path):
    page = window
    set_up_before()
    page.reload()
    page.locator(".step").first.wait_for()
    connect(page, tmp_path)
    for step in ("Define limits", "Image-to-stage orientation", "Hardware settings"):
        open_step(page, step)
        page.locator(".setup-column .side-group").first.wait_for()
        page.wait_for_function("() => !document.body.textContent.includes('Reading…')")
        unnamed = page.eval_on_selector_all(
            "input, select",
            "els => els.filter(e => !e.getAttribute('aria-label') && !e.labels?.length).map(e => e.outerHTML)",
        )
        assert unnamed == [], (step, unnamed)
