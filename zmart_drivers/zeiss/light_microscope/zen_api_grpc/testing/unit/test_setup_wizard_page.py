"""The setup wizard's page in a real browser (Chromium, through Playwright).

The built page in ``driver_setup/wizard/page/`` is opened from the wizard's own
server and clicked through the way an operator would. The page is the ZMART
Interface's screen without its top row, showing the ZMART driver
configuration workflow: the rail of steps on the left, and the chosen step's
column of cells beside it. Two kinds of test:

- **The whole setup on the stand-in ZEN.** All five real ZEISS steps, one
  after the other in one window, on the pretend ZEN of
  ``helpers/mock_zen_api.py``. What the page shows is checked, and so is what
  was saved on disk.
- **The page's own rules.** A pretend wizard with canned answers: the rail's
  colours and locks, the cells of each step, what a button says while it
  works and after, and that the page asks nothing of the internet.

Set ``ZMART_WIZARD_SCREENSHOTS`` to a folder to keep a screenshot of every
screen of the walk. Where no browser can start, these tests are skipped.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from mock_zen_api import FakeScope, build_fake_client

from zmart_drivers.zeiss.light_microscope.zen_api_grpc import methods
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup import store
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard import (
    picture,
    steps,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard.framework import (
    Step,
    StepError,
    Wizard,
)
from zmart_drivers.zeiss.light_microscope.zen_api_grpc.driver_setup.wizard.server import (
    WizardServer,
)

# Where a remote test container keeps its browser, when Playwright's own download is missing.
_INSTALLED_CHROMIUM = "/opt/pw-browsers/chromium"

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
        self.page.on(
            "response",
            lambda r: r.status >= 400 and self.problems.append(f"{r.status} {r.url}"),
        )
        self.page.set_default_timeout(30000)
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
            " badge: getComputedStyle(e.querySelector('.step-n')).backgroundColor,"
            " done: e.classList.contains('done'), stale: e.classList.contains('stale'),"
            " active: e.classList.contains('active'), locked: e.disabled,"
            " review: !!e.querySelector('.review-tag')}))",
        )

    def cells(self) -> list[str]:
        """The headings of the cells on show, as written (shown in capitals)."""
        return self.page.eval_on_selector_all(
            ".setup-column > .side-group > .side-group-title",
            "els => els.map(e => e.textContent)",
        )

    def cell(self, title: str):
        heading = self.page.locator(".side-group-title").filter(
            has_text=re.compile(f"^{re.escape(title)}$")
        )
        return self.page.locator(".side-group").filter(has=heading)

    def part(self, title: str):
        heading = self.page.locator(".setup-part-title").filter(
            has_text=re.compile(f"^\\d?{re.escape(title)}$")
        )
        return self.page.locator(".setup-part").filter(has=heading)

    def open_step(self, name: str):
        self.page.locator(".step", has_text=name).click()

    def button(self, label: str, within=None):
        return (within or self.page).get_by_role("button", name=label, exact=True)

    def press(self, label: str, within=None):
        self.button(label, within).click()

    def says(self, text: str, kind: str = "ok", within=None):
        """Wait for a note in the voice *kind* ("ok", "bad" or "") holding *text*."""
        selector = f".setup-note.{kind}" if kind else ".setup-note"
        (within or self.page).locator(selector, has_text=text).first.wait_for()

    def shot(self, name: str):
        if _SCREENSHOTS:
            self.shots += 1
            Path(_SCREENSHOTS).mkdir(parents=True, exist_ok=True)
            self.page.screenshot(
                path=str(Path(_SCREENSHOTS) / f"{self.shots:02d}-{name}.png")
            )

    def assert_only_local_requests(self):
        outside = [u for u in self.requests if not u.startswith(self.server.url)]
        assert outside == [], outside

    def wait_for_picture(self, alt_start: str):
        picture = self.page.locator(f'img[alt^="{alt_start}"]').first
        picture.wait_for()
        self.page.wait_for_function(
            "img => img.complete && img.naturalWidth > 0", arg=picture.element_handle()
        )


# --- the whole setup, on the stand-in ZEN -------------------------------------------------


@pytest.fixture
def zen(monkeypatch, tmp_path):
    """The pretend ZEN, reached by every connection the wizard opens."""
    scope = FakeScope(image_output_folder=str(tmp_path / "zen_images"))
    Path(scope.image_output_folder).mkdir()

    def stand_in(_connection):
        client, _ = build_fake_client(scope)
        return client

    monkeypatch.setattr(methods, "open_connection", stand_in)
    return scope


def test_an_operator_sets_up_the_microscope_from_start_to_finish(
    browser, tmp_path, zen
):
    pytest.importorskip("pylibCZIrw")
    data = tmp_path / "ZMART-data"
    data.mkdir()
    win = Window(browser, steps.zeiss_wizard())
    try:
        page = win.page
        rail = win.rail()
        assert [r["name"] for r in rail] == [
            "Connect",
            "Define limits",
            "Define coordinate system origin",
            "Image-to-stage calibration",
            "Objective calibration",
        ]
        # A setup always starts by connecting; everything after it waits.
        assert rail[0]["active"] and rail[0]["badge"] == BLUE
        assert all(r["locked"] for r in rail[1:])
        win.cell("Connect to the microscope").wait_for()
        assert page.get_by_label("Port").input_value() == "5002"
        win.says("right-click its icon in the Windows tray", "")
        win.shot("connect-card")

        # 1. Connect, with an output folder that cannot hold pictures first.
        page.get_by_label("Output folder").fill(str(tmp_path / "missing"))
        page.get_by_label("Control token").fill("the-token")
        win.press("Connect")
        win.says("The output folder cannot be used", "bad")
        page.get_by_label("Output folder").fill(str(data))
        win.press("Connect")
        win.cell("Connection checks").wait_for()
        assert page.locator(".session-state", has_text="Connected").is_visible()
        checks = page.locator(".check-row").all_inner_texts()
        assert any("ZEN image folder" in c for c in checks)
        assert any(str(data) in c for c in checks)
        win.shot("connected")
        assert not win.rail()[1]["locked"]

        # 2. Define limits: the four corners and the two focus ends, then Save and adopt.
        win.open_step("Define limits")
        win.cell("Configure").wait_for()
        assert page.locator(".setup-source").inner_text() == "From the defaults."
        win.shot("limits-before")
        imp = win.cell("Import X/Y stage limits")
        assert win.button("Save and adopt", imp).is_disabled()
        for name, (x, y) in zip(
            ["Top left", "Top right", "Bottom left", "Bottom right"],
            [
                (5000.0, 4000.0),
                (90000.0, 4000.0),
                (5000.0, 70000.0),
                (90000.0, 70000.0),
            ],
            strict=True,
        ):
            zen.x_um, zen.y_um = x, y
            imp.locator(".setup-row", has_text=name).get_by_role("button").click()
            imp.locator(".setup-row", has_text=name).locator(
                ".setup-note", has_text=f"x {x:.1f} · y {y:.1f} µm"
            ).wait_for()
        win.says("X 5000 … 90000 · Y 4000 … 70000 set above.", within=imp)
        assert page.get_by_label("X (µm) minimum").input_value() == "5000"
        assert page.get_by_label("Y (µm) maximum").input_value() == "70000"
        focus = win.cell("Import focus limits")
        for name, z in (("One end", 9000.0), ("Other end", 200.0)):
            zen.z_um = z
            focus.locator(".setup-row", has_text=name).get_by_role("button").click()
            focus.locator(".setup-row", has_text=name).locator(
                ".setup-note", has_text=f"z {z:.1f} µm"
            ).wait_for()
        win.says("Focus 200 … 9000 set above.", within=focus)
        assert page.get_by_label("Focus (Z) (µm) minimum").input_value() == "200"
        win.shot("limits-read")
        win.press("Save and adopt", focus)
        win.says("Adopted.", within=focus)
        written = json.loads(
            (store.MACHINE.latest_snapshot("limits") / "limits.json").read_text(
                encoding="utf-8"
            )
        )
        assert written["x_um"]["range"] == [5000.0, 90000.0]
        assert written["z_um"]["range"] == [200.0, 9000.0]
        win.shot("limits-published")

        # 3. Define coordinate system origin: Read, then Save and adopt.
        zen.x_um, zen.y_um, zen.z_um = 42000.0, 31000.0, 1000.0
        win.open_step("Define coordinate system origin")
        win.says("No origin published.", "")
        win.shot("origin-before")
        assert win.button("Save and adopt").is_disabled()
        win.press("Read")
        win.says("Becomes (0, 0, 0):", "")
        assert "42000.00 µm" in page.locator(".setup-readout").inner_text()
        win.shot("origin-read")
        win.press("Save and adopt")
        win.says("Adopted.")
        win.says("Now (0, 0, 0):")
        assert store.MACHINE.read_origin()["origin"]["x_um"] == 42000.0
        win.shot("origin-published")

        # 4. Image-to-stage calibration: three snaps of the pretend sample.
        zen.camera_image_to_stage = ((0, -1), (1, 0))
        win.open_step("Image-to-stage calibration")
        win.cell("Orientation").wait_for()
        page.get_by_label("experiment for the snaps").select_option("ZMART_Snap")
        win.shot("orientation-before")
        win.press("Start")
        win.wait_for_picture("the three snaps")
        win.says("one pixel 0.65", within=win.part("Result"))
        assert win.button("Rerun").is_visible()
        win.shot("orientation-measured")
        win.press("Save and adopt")
        win.says("Adopted.")
        saved = json.loads(
            (
                store.MACHINE.latest_snapshot("orientation") / "orientation.json"
            ).read_text(encoding="utf-8")
        )
        assert saved["measured"] is True
        win.shot("orientation-published")

        # 5. Objective calibration, by eye: record, turn, find the feature again, record.
        win.open_step("Objective calibration")
        win.cell("Presets").wait_for()
        win.shot("optics-before")
        page.get_by_label("reference objective").select_option("1")
        assert page.get_by_label("reference objective").is_disabled()
        page.locator(".setup-set", has_text="vs Plan-Apochromat 20x/0.8").click()
        work = win.cell(
            "Calibration — Plan-Apochromat 10x/0.45 vs Plan-Apochromat 20x/0.8"
        )
        work.wait_for()
        under_reference = win.part("Under the reference (Plan-Apochromat 10x/0.45)")
        under_target = win.part("Under the target (Plan-Apochromat 20x/0.8)")
        # A mistake first: the target recorded with the reference still in place.
        win.press("Record target (Plan-Apochromat 20x/0.8)", under_target)
        win.says("Turn to the target objective first", "bad", within=under_target)
        win.press("Record reference (Plan-Apochromat 10x/0.45)", under_reference)
        under_reference.locator(".setup-readout", has_text="42000.00 µm").wait_for()
        win.press("Turn to Plan-Apochromat 20x/0.8", under_target)
        # Once turned, its button rests: the target is in place.
        under_target.locator(
            "button[disabled]", has_text="Turn to Plan-Apochromat 20x/0.8"
        ).wait_for()
        assert zen.objective_position == 2
        zen.x_um, zen.y_um, zen.z_um = 42004.0, 30998.0, 1003.0
        assert win.button("Save and adopt", work).is_disabled()
        win.press("Record target (Plan-Apochromat 20x/0.8)", under_target)
        under_target.locator(".setup-readout", has_text="42004.00 µm").wait_for()
        assert page.locator(".setup-xyz-value").all_inner_texts() == [
            "+4.00",
            "-2.00",
            "+3.00",
        ]
        assert (
            page.locator(".setup-set", has_text="20x")
            .locator(".setup-set-state")
            .inner_text()
            == "measured"
        )
        win.part("Confirmation").scroll_into_view_if_needed()
        win.shot("optics-result")
        win.press("Save and adopt", work)
        win.says("Adopted.")
        published = json.loads(
            (
                store.MACHINE.latest_snapshot("calibration") / "calibration.json"
            ).read_text(encoding="utf-8")
        )
        assert published["objectives"]["2"]["translation_um"] == pytest.approx(
            [4.0, -2.0, 3.0]
        )
        win.shot("optics-published")

        page.wait_for_function(
            "() => [...document.querySelectorAll('.step')].every(s => s.classList.contains('done'))"
        )
        win.shot("all-done-rail")
        win.assert_only_local_requests()
        assert win.problems == []
    finally:
        win.close()


# --- a pretend wizard, for the page's own rules -----------------------------------------


IDS = ["connect", "limits", "origin", "orientation", "calibration"]
TITLES = [
    "Connect",
    "Define limits",
    "Define coordinate system origin",
    "Image-to-stage calibration",
    "Objective calibration",
]

DOCUMENT = {
    "x_um": {"range": [0.0, 100000.0]},
    "y_um": {"range": [0.0, 75000.0]},
    "z_um": {"range": [0.0, 10000.0]},
    "objective_position": [],
    "experiment": [],
    "set_z_stack": {"range": [100.0, 9000.0]},
}

LENSES = [
    {"position": 1, "name": "10x", "magnification": 10.0},
    {"position": 2, "name": "20x", "magnification": 20.0},
    {"position": 3, "name": "63x", "magnification": 63.0},
]


def _pretend(tmp_path, *, done=(), saved=None, overrides=None, sent=None):
    """The five steps with canned answers; *overrides* replaces single actions.

    *saved* gives a step's save time. Here the calibration is said to need
    the orientation, so that the page's orange "review" rule can be seen;
    the real ZEISS calibration needs nothing before it. *sent* collects what
    the page sent to each action.
    """
    np = pytest.importorskip("numpy")
    shown = tmp_path / "p.png"
    # A real picture, so the browser can draw it.
    picture.write_png(shown, np.full((8, 8), 128, dtype=np.uint8))
    sent = sent if sent is not None else {}
    connected = {"on": False}
    in_place = {"position": 1}

    def keep(name, answer):
        def action(values):
            sent.setdefault(name, []).append(values)
            return answer(values) if callable(answer) else answer

        return action

    def connect(values):
        connected["on"] = True
        return {
            "connected": True,
            "checks": [
                ["driver", "zen_api_grpc 0.1.0b1"],
                ["settings folder", r"C:\ProgramData\zmart-microscopy"],
            ],
        }

    def where(values, x=0.0):
        return {
            "position": in_place["position"],
            "name": "10x",
            "x_um": 100.0 + x,
            "y_um": 200.0,
            "z_um": 300.0,
        }

    def record(values):
        if values["side"] == "reference":
            return where(values)
        return {
            **where(values, 12.0),
            "translation_um": {"x": 12.0, "y": -8.0, "z": 6.0},
        }

    canned = {
        "connect": {
            "card": lambda v: {
                "microscope": "zeiss-zen",
                "api_type": "ZEN API (gRPC), zen_api 2025.10.1, ZEN 3.13",
                "host": "127.0.0.1",
                "port": "5002",
                "client": "ZMART",
                "token_file": None,
                "config": "",
                "output_root": r"D:\ZMART-data",
                "output_root_saved": False,
                "connected": connected["on"],
                "checks": [],
            },
            "connect": keep("connect", connect),
            "disconnect": keep(
                "disconnect",
                lambda v: connected.update(on=False) or {"connected": False},
            ),
        },
        "limits": {
            "document": lambda v: {
                "document": json.loads(json.dumps(DOCUMENT)),
                "source": "defaults",
                "axes": [
                    {"key": "x_um", "label": "X", "unit": "µm"},
                    {"key": "y_um", "label": "Y", "unit": "µm"},
                    {"key": "z_um", "label": "Focus (Z)", "unit": "µm"},
                ],
                "required": ["x_um", "y_um", "z_um"],
                "measured": ["x_um", "y_um"],
                "focus": "z_um",
                "settings": {
                    "objective_position": {
                        "label": "Objective positions",
                        "note": "Changer positions count from 1.",
                    },
                    "experiment": {"label": "Experiments", "note": "By name."},
                    "set_z_stack": {"label": "Z-stack ends (µm)", "note": "Ends."},
                },
                "allowed_only": ["objective_position", "experiment"],
                "words": ["experiment"],
            },
            "corner": keep(
                "corner",
                lambda v: {"x": 1000.0 * (1 + len(sent.get("corner", []))), "y": 500.0},
            ),
            "focus_end": keep(
                "focus_end",
                lambda v: {"end": v["end"], "z": 50.0 if v["end"] == "low" else 4000.0},
            ),
            "publish": keep("limits.publish", {"saved_to": "x"}),
        },
        "origin": {
            "standing": lambda v: {"origin": None},
            "read": keep(
                "read",
                {
                    "actuators": [
                        ["x (stage)", "1.00 µm"],
                        ["objective", "position 1 · 10x"],
                    ],
                    "x_um": 1,
                    "y_um": 2,
                    "z_um": 3,
                },
            ),
            "publish": keep(
                "origin.publish",
                {
                    "actuators": [
                        ["x (stage)", "1.00 µm"],
                        ["objective", "position 1 · 10x"],
                    ],
                    "x_um": 1,
                    "y_um": 2,
                    "z_um": 3,
                },
            ),
        },
        "orientation": {
            "standing": lambda v: {"measured": False},
            "experiments": lambda v: {
                "experiments": ["ZMART_Snap", "ZMART_ZStack"],
                "loaded": None,
            },
            "measure": keep(
                "measure",
                {
                    "accepted": True,
                    "rotation_deg": 90,
                    "reflection": False,
                    "pixel_size_um": [0.65, 0.65],
                    "image_size_px": [128, 96],
                    "clarity": [40.0, 40.0],
                    "description": "The picture is turned 90 degrees against the stage.",
                    "image": str(shown),
                },
            ),
            "publish": keep("orientation.publish", {"saved_to": "x"}),
        },
        "calibration": {
            "lenses": lambda v: {
                "lenses": LENSES,
                "in_place": 1,
                "reference": None,
                "held": {},
            },
            "turn": keep(
                "turn",
                lambda v: (
                    in_place.update(position=v["position"])
                    or {"in_place": v["position"]}
                ),
            ),
            "record": keep("record", record),
            "publish": keep("calibration.publish", {"saved_to": "x"}),
        },
    }
    for (step, action), fn in (overrides or {}).items():
        canned[step][action] = fn
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
                        "done": (i in done) or (i == "connect" and connected["on"]),
                        "note": None,
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


def connect(win):
    win.press("Connect")
    win.cell("Connection checks").wait_for()


# --- the rail and the screen --------------------------------------------------------------


def test_the_page_is_the_rail_and_the_column_with_no_top_row_canvas_or_sidebar(
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
        "#status-bar",
    ):
        assert win.page.locator(part).count() == 0, part
    rail = win.page.locator(".rail").bounding_box()
    column = win.page.locator(".setup-column").bounding_box()
    assert rail["x"] == 0 and rail["width"] == 320
    assert column["x"] == 320 + 14 and column["width"] <= 46 * 16
    win.assert_only_local_requests()
    assert win.problems == []


def test_every_step_waits_for_connect_even_when_set_up_before(window, tmp_path):
    win = window(_pretend(tmp_path, done=set(IDS[1:])))

    rail = win.rail()
    assert rail[0]["active"] and all(r["locked"] for r in rail[1:])
    assert [r["badge"] for r in rail[1:]] == [
        GREEN
    ] * 4  # done on this computer, shown green
    connect(win)
    assert not any(r["locked"] for r in win.rail())


def test_disconnect_locks_the_steps_again(window, tmp_path):
    sent = {}
    win = window(_pretend(tmp_path, sent=sent))
    connect(win)

    win.press("Disconnect")
    win.button("Connect").wait_for()

    assert all(r["locked"] for r in win.rail()[1:])
    assert len(sent["disconnect"]) == 1


def test_a_step_measured_on_an_older_setting_turns_orange_and_says_why(
    window, tmp_path
):
    win = window(
        _pretend(
            tmp_path,
            done=set(IDS[1:]),
            saved={
                "orientation": "2026-10-02T09:00:00+00:00",
                "calibration": "2026-10-01T09:00:00+00:00",
            },
        )
    )
    connect(win)

    rail = win.rail()
    assert rail[4]["stale"] and rail[4]["review"] and rail[4]["badge"] == ORANGE
    assert not rail[3]["stale"]
    win.open_step("Objective calibration")
    win.says(
        "The image-to-stage calibration was saved again after this step. Do this step again, so that it matches.",
        "bad",
    )


def test_while_one_button_works_it_says_so_and_nothing_else_can_be_pressed(
    window, tmp_path
):
    release = threading.Event()

    def slow(values):
        release.wait(10)
        return {"connected": True, "checks": [["driver", "x"]]}

    win = window(_pretend(tmp_path, overrides={("connect", "connect"): slow}))
    win.press("Connect")

    win.button("connecting…").wait_for()
    assert win.button("connecting…").is_disabled()
    assert win.page.evaluate("() => document.body.classList.contains('busy')")
    release.set()
    win.cell("Connection checks").wait_for()
    assert not win.page.evaluate("() => document.body.classList.contains('busy')")


# --- the cells of each step ---------------------------------------------------------------


def test_the_cells_are_a_heading_over_a_white_card(window, tmp_path):
    win = window(_pretend(tmp_path))

    win.cell("Driver configuration").wait_for()
    # What reaches the microscope, then how the driver works with it, each in a box of its own.
    assert win.cells() == ["Connect to the microscope", "Driver configuration"]
    style = win.page.evaluate(
        """() => { const t = getComputedStyle(document.querySelector('.side-group-title'));
                   const b = getComputedStyle(document.querySelector('.side-group-body'));
                   const n = getComputedStyle(document.querySelector('.setup-notebook'));
                   return [t.textTransform, t.fontSize, b.backgroundColor, b.borderRadius, n.backgroundColor]; }"""
    )
    assert style == [
        "uppercase",
        "11px",
        "rgb(255, 255, 255)",
        "10px",
        "rgb(248, 250, 252)",
    ]
    assert win.page.get_by_label("Output folder").input_value() == r"D:\ZMART-data"


def test_a_refusal_is_said_in_red_and_the_button_can_be_pressed_again(window, tmp_path):
    attempts = []

    def refuse_once(values):
        attempts.append(values["output_root"])
        if len(attempts) == 1:
            raise StepError(
                "The output folder cannot be used: D:\\ZMART-data does not exist; make the folder first"
            )
        return {"connected": True, "checks": [["driver", "x"]]}

    win = window(_pretend(tmp_path, overrides={("connect", "connect"): refuse_once}))
    win.press("Connect")

    win.says("does not exist; make the folder first", "bad")
    assert (
        win.page.evaluate(
            "e => getComputedStyle(e).color",
            win.page.locator(".setup-note.bad").element_handle(),
        )
        == "rgb(179, 38, 30)"
    )
    win.press("Connect")
    win.cell("Connection checks").wait_for()
    assert attempts == [r"D:\ZMART-data", r"D:\ZMART-data"]


def test_the_limits_table_sends_what_the_operator_ticked_and_typed(window, tmp_path):
    sent = {}
    win = window(_pretend(tmp_path, sent=sent))
    connect(win)
    win.open_step("Define limits")
    win.cell("Configure").wait_for()

    # The stage and focus axes are always enforced; the others can be ticked on and off.
    assert win.page.get_by_label("apply a limit to X (µm)").is_disabled()
    assert win.page.get_by_label("apply a limit to Focus (Z) (µm)").is_disabled()
    assert win.page.get_by_label("how Objective positions is limited").is_disabled()
    win.page.get_by_label("apply a limit to Objective positions").check()
    win.page.get_by_label("Objective positions, allowed values").fill("1, 2")
    win.page.get_by_label("Objective positions, allowed values").blur()
    win.page.get_by_label("apply a limit to Experiments").check()
    win.page.get_by_label("Experiments, allowed values").fill("ZMART_Snap, 2024")
    win.page.get_by_label("Experiments, allowed values").blur()
    win.page.get_by_label("Z-stack ends (µm) maximum").fill("8000")
    win.page.get_by_label("Z-stack ends (µm) maximum").blur()
    win.press("Save and adopt", win.cell("Configure"))
    win.says("Adopted.", within=win.cell("Configure"))

    document = sent["limits.publish"][0]["document"]
    assert document["objective_position"] == {"allowed": [1, 2]}
    # Experiment names stay names, even one that looks like a number.
    assert document["experiment"] == {"allowed": ["ZMART_Snap", "2024"]}
    assert document["set_z_stack"] == {"range": [100.0, 8000]}
    assert document["x_um"] == {"range": [0.0, 100000.0]}


def test_four_imported_corners_set_x_and_y_above(window, tmp_path):
    win = window(_pretend(tmp_path))
    connect(win)
    win.open_step("Define limits")
    imp = win.cell("Import X/Y stage limits")
    imp.wait_for()

    for name in ("Top left", "Top right", "Bottom left"):
        imp.locator(".setup-row", has_text=name).get_by_role(
            "button", name="Import"
        ).click()
        imp.locator(".setup-row", has_text=name).get_by_role(
            "button", name="Update"
        ).wait_for()
        assert win.button("Save and adopt", imp).is_disabled()
    imp.locator(".setup-row", has_text="Bottom right").get_by_role("button").click()

    win.says("X 2000 … 5000 · Y 500 … 500 set above.", within=imp)
    assert win.page.get_by_label("X (µm) minimum").input_value() == "2000"
    assert win.page.get_by_label("X (µm) maximum").input_value() == "5000"
    assert win.button("Save and adopt", imp).is_enabled()


def test_two_imported_focus_ends_set_the_focus_range_whichever_way_round(
    window, tmp_path
):
    sent = {}
    win = window(_pretend(tmp_path, sent=sent))
    connect(win)
    win.open_step("Define limits")
    focus = win.cell("Import focus limits")
    focus.wait_for()

    focus.locator(".setup-row", has_text="Other end").get_by_role("button").click()
    focus.locator(".setup-row", has_text="Other end").get_by_role(
        "button", name="Update"
    ).wait_for()
    assert win.button("Save and adopt", focus).is_disabled()
    focus.locator(".setup-row", has_text="One end").get_by_role("button").click()

    win.says("Focus 50 … 4000 set above.", within=focus)
    assert win.page.get_by_label("Focus (Z) (µm) maximum").input_value() == "4000"
    win.press("Save and adopt", focus)
    win.says("Adopted.", within=focus)
    assert sent["limits.publish"][0]["focus"] == {"high": 4000.0, "low": 50.0}
    assert [v["end"] for v in sent["focus_end"]] == ["high", "low"]


def test_the_origin_is_read_before_it_can_be_saved(window, tmp_path):
    win = window(_pretend(tmp_path, done={"limits"}))
    connect(win)
    win.open_step("Define coordinate system origin")
    win.says("No origin published.", "")

    assert win.button("Save and adopt").is_disabled()
    win.press("Read")
    win.says("Becomes (0, 0, 0):", "")
    win.press("Save and adopt")
    win.says("Now (0, 0, 0):")
    win.says("Adopted.")


def test_the_orientation_uses_the_chosen_experiment_shows_the_snaps_and_saves(
    window, tmp_path
):
    sent = {}
    win = window(_pretend(tmp_path, done={"limits", "origin"}, sent=sent))
    connect(win)
    win.open_step("Image-to-stage calibration")
    win.cell("Orientation").wait_for()

    assert (
        win.button("Save and adopt").count() == 0
    )  # only Start, until there is a result
    win.page.get_by_label("experiment for the snaps").select_option("ZMART_ZStack")
    win.press("Start")
    win.wait_for_picture("the three snaps")
    win.says("Turned 90° · one pixel 0.6500 × 0.6500 µm · 128 × 96 pixels")
    assert sent["measure"] == [{"experiment": "ZMART_ZStack"}]
    assert (
        win.button("Rerun").is_visible() and win.button("Save and adopt").is_enabled()
    )
    win.press("Save and adopt")
    win.says("Adopted.")


def test_a_failed_measurement_is_said_and_rerun(window, tmp_path):
    def blank(values):
        return {
            "accepted": False,
            "why": "The orientation could not be worked out: the field is blank.",
        }

    win = window(
        _pretend(
            tmp_path,
            done={"limits", "origin"},
            overrides={("orientation", "measure"): blank},
        )
    )
    connect(win)
    win.open_step("Image-to-stage calibration")
    win.press("Start")

    win.says("the field is blank", "bad")
    assert win.button("Rerun").is_visible()
    assert win.button("Save and adopt").is_disabled()


def test_the_calibration_presets_hang_from_the_reference(window, tmp_path):
    sent = {}
    win = window(
        _pretend(tmp_path, done={"limits", "origin", "orientation"}, sent=sent)
    )
    connect(win)
    win.open_step("Objective calibration")
    win.cell("Presets").wait_for()

    win.page.get_by_label("reference objective").select_option("1")
    sets = win.page.locator(".setup-set").all_inner_texts()
    assert [s.split("\n")[0] for s in sets] == ["10x vs 20x", "10x vs 63x"]
    assert all(s.endswith("not measured") for s in sets)
    assert win.page.get_by_label("reference objective").is_disabled()
    win.page.locator(".setup-set", has_text="10x vs 63x").click()
    win.cell("Calibration — 10x vs 63x").wait_for()
    assert [
        t.split("\n")[-1]
        for t in win.page.locator(".setup-part-title").all_inner_texts()
    ] == ["Under the reference (10x)", "Under the target (63x)", "Confirmation"]
    assert win.page.locator(".setup-xyz-axis").all_inner_texts() == ["X", "Y", "Z"]
    assert win.button("Save and adopt").is_disabled()
    # The reference is in place, so it cannot be turned to.
    assert win.button("Turn to 10x").is_disabled()

    win.press("Record reference (10x)")
    win.page.locator(".setup-readout", has_text="100.00 µm").wait_for()
    win.press("Turn to 63x")
    win.page.locator("button[disabled]", has_text="Turn to 63x").wait_for()
    win.press("Record target (63x)")
    win.page.locator(".setup-readout", has_text="112.00 µm").wait_for()
    assert win.page.locator(".setup-xyz-value").all_inner_texts() == [
        "+12.00",
        "-8.00",
        "+6.00",
    ]
    assert sent["turn"] == [{"position": 3}]
    assert sent["record"][-1] == {"reference": 1, "target": 3, "side": "target"}
    win.press("Save and adopt")
    win.says("Adopted.")
    assert sent["calibration.publish"] == [{"reference": 1}]

    win.press("Reset")
    assert win.page.get_by_label("reference objective").is_enabled()
    assert win.page.locator(".setup-set").count() == 0


def test_a_new_reference_is_said_to_start_a_new_calibration(window, tmp_path):
    def held(values):
        return {
            "lenses": LENSES,
            "in_place": 1,
            "reference": 1,
            "held": {"1": [0, 0, 0], "2": [4, -2, 3]},
        }

    win = window(
        _pretend(
            tmp_path,
            done={"limits", "origin", "orientation"},
            overrides={("calibration", "lenses"): held},
        )
    )
    connect(win)
    win.open_step("Objective calibration")
    win.cell("Presets").wait_for()

    # The saved reference is chosen, and its held offsets are the presets.
    assert (
        win.page.locator(".setup-set", has_text="10x vs 20x")
        .inner_text()
        .endswith("held")
    )
    win.press("Reset")
    win.page.get_by_label("reference objective").select_option("2")

    win.says("A different reference starts a new calibration", "bad")


def test_going_back_to_a_step_shows_what_it_measured(window, tmp_path):
    win = window(_pretend(tmp_path, done={"limits"}))
    connect(win)
    win.open_step("Define coordinate system origin")
    win.press("Read")
    win.says("Becomes (0, 0, 0):", "")

    win.open_step("Connect")
    win.open_step("Define coordinate system origin")

    win.says("Becomes (0, 0, 0):", "")
    assert win.button("Save and adopt").is_enabled()


def test_the_page_fits_a_narrow_window_without_sideways_scrolling(window, tmp_path):
    win = window(_pretend(tmp_path), viewport={"width": 390, "height": 800})

    overflow = win.page.evaluate(
        "() => document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 0


def test_the_page_follows_a_dark_screen(window, tmp_path):
    win = window(_pretend(tmp_path), color_scheme="dark")

    rail = win.page.evaluate(
        "() => getComputedStyle(document.querySelector('.rail')).backgroundColor"
    )
    assert rail == "rgb(16, 27, 44)"
    assert win.rail()[0]["badge"] == "rgb(56, 189, 248)"


def test_every_control_has_a_name_a_screen_reader_can_say(window, tmp_path):
    win = window(_pretend(tmp_path))
    connect(win)
    win.open_step("Define limits")
    win.cell("Configure").wait_for()

    unnamed = win.page.evaluate(
        """() => [...document.querySelectorAll('.stage input, .stage select, .stage button, .rail button')]
            .filter(e => e.offsetParent !== null)
            .filter(e => !(e.labels && e.labels.length) && !e.textContent.trim() && !e.getAttribute('aria-label'))
            .map(e => e.outerHTML)"""
    )
    assert unnamed == []
