"""The setup wizard's core, server and window, with no microscope and no LAS X.

The Leica steps themselves are tested in ``test_setup_wizard_steps.py``, and the
page in a real browser in ``test_setup_wizard_page.py``. Here the parts around
them are checked: that a failing action answers with a sentence, that only one
action talks to the microscope at a time, that the server hands out nothing but
the page and the pictures the driver named, that the window closes the server
behind it, and that the wizard stands on the driver alone.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import ast
import http.client
import json
import re
import sys
import threading
import types
import urllib.error
import urllib.request

import pytest

from zmart_drivers.leica.stellaris.navigator.driver_setup.wizard import launcher
from zmart_drivers.leica.stellaris.navigator.driver_setup.wizard.framework import (
    Step,
    StepError,
    Wizard,
)
from zmart_drivers.leica.stellaris.navigator.driver_setup.wizard.server import (
    PAGE,
    WizardServer,
)

WIZARD_DIR = PAGE.parent


def _wizard(tmp_path, **more_actions):
    picture = tmp_path / "a.png"
    picture.write_bytes(b"png")

    def refuse(_values):
        raise StepError("The stage is still moving.")

    def crash(_values):
        raise ZeroDivisionError("division by zero")

    return Wizard(
        "Test",
        [
            Step(
                "a",
                "A",
                "purpose",
                {
                    "ok": lambda v: {"n": v.get("n"), "image": str(picture)},
                    "bad": refuse,
                    "crash": crash,
                    **more_actions,
                },
            )
        ],
    )


@pytest.fixture
def served(tmp_path):
    server = WizardServer(_wizard(tmp_path)).start()
    yield server
    server.stop()


def _post(server, path, body: bytes):
    request = urllib.request.Request(
        server.url + path, body, {"Content-Type": "application/json"}
    )
    try:
        return urllib.request.urlopen(request).status, json.loads(
            urllib.request.urlopen(request).read()
        )
    except urllib.error.HTTPError as why:
        return why.code, json.loads(why.read() or b"null")


def _raw_get(server, path: str) -> int:
    """Send *path* exactly as written, without the client tidying it up first."""
    host, port = server.url.split("//")[1].rstrip("/").split(":")
    connection = http.client.HTTPConnection(host, int(port))
    connection.putrequest("GET", path, skip_host=False, skip_accept_encoding=True)
    connection.endheaders()
    status = connection.getresponse().status
    connection.close()
    return status


# --- the core -------------------------------------------------------------------------


def test_an_action_that_fails_answers_with_the_reason(tmp_path):
    assert _wizard(tmp_path).call("a", "bad") == {
        "ok": False,
        "error": "The stage is still moving.",
    }


def test_an_unexpected_error_is_answered_too_and_does_not_stop_the_wizard(tmp_path):
    wizard = _wizard(tmp_path)

    assert wizard.call("a", "crash") == {
        "ok": False,
        "error": "ZeroDivisionError: division by zero",
    }
    assert wizard.call("a", "ok", {"n": 1})["ok"]


def test_an_unknown_step_or_action_is_refused_in_words(tmp_path):
    wizard = _wizard(tmp_path)

    assert wizard.call("a", "nope") == {
        "ok": False,
        "error": "There is no 'nope' in step 'a'.",
    }
    assert wizard.call("z", "ok")["ok"] is False


def test_only_one_action_talks_to_the_microscope_at_a_time(tmp_path):
    inside, release = threading.Event(), threading.Event()

    def slow(_values):
        inside.set()
        release.wait(5)
        return {}

    wizard = _wizard(tmp_path, slow=slow)
    first = threading.Thread(target=wizard.call, args=("a", "slow"))
    first.start()
    inside.wait(5)
    try:
        second = wizard.call("a", "ok")
    finally:
        release.set()
        first.join()

    assert second["ok"] is False
    assert "still busy" in second["error"]
    assert wizard.call("a", "ok")["ok"]  # free again afterwards


def test_a_status_that_cannot_be_read_is_shown_and_not_fatal():
    def broken():
        raise OSError("disk unplugged")

    outline = Wizard("T", [Step("a", "A", "p", status=broken)]).outline()

    assert outline["steps"][0]["status"]["done"] is False
    assert "disk unplugged" in outline["steps"][0]["status"]["note"]


def test_the_values_an_action_receives_are_its_own_copy(tmp_path):
    seen = {}

    def keep(values):
        values["changed"] = True
        seen.update(values)
        return {}

    sent = {"n": 1}
    _wizard(tmp_path, keep=keep).call("a", "keep", sent)

    assert sent == {"n": 1}


# --- the server -----------------------------------------------------------------------


def test_the_server_listens_only_on_this_computer(served):
    assert served.url.startswith("http://127.0.0.1:")


def test_the_server_carries_calls_and_hands_out_named_pictures(served):
    status, answer = _post(served, "api/a/ok", b'{"n": 3}')

    assert status == 200
    assert answer["result"]["n"] == 3
    assert answer["result"]["image"].startswith("files/")
    assert (
        urllib.request.urlopen(served.url + answer["result"]["image"]).read() == b"png"
    )


def test_a_failure_travels_to_the_page_as_a_sentence(served):
    assert _post(served, "api/a/bad", b"{}") == (
        200,
        {"ok": False, "error": "The stage is still moving."},
    )


def test_a_request_that_is_not_json_is_refused(served):
    status, answer = _post(served, "api/a/ok", b"{not json")

    assert status == 400
    assert answer["ok"] is False


@pytest.mark.parametrize("path", ["api/a", "api/a/ok/extra", "elsewhere/a/ok"])
def test_a_call_to_an_unknown_address_is_refused(served, path):
    request = urllib.request.Request(served.url + path, b"{}")
    with pytest.raises(urllib.error.HTTPError) as why:
        urllib.request.urlopen(request)
    assert why.value.code == 404


@pytest.mark.parametrize(
    "path",
    [
        "/../framework.py",
        "/../../store.py",
        "/%2e%2e/framework.py",
        "/assets/../../server.py",
        "/files/unknown.png",
        "/files/../../server.py",
        "/no-such-page.html",
    ],
)
def test_nothing_but_the_page_and_named_pictures_can_be_fetched(served, path):
    assert _raw_get(served, path) == 404


def test_the_page_and_its_files_are_served(served):
    page = urllib.request.urlopen(served.url).read().decode()
    assets = re.findall(r'(?:src|href)="\./(assets/[^"]+)"', page)

    assert assets, page
    for asset in assets:
        reply = urllib.request.urlopen(served.url + asset)
        assert reply.status == 200
        assert reply.headers["Content-Type"] in (
            "text/javascript",
            "application/javascript",
            "text/css",
        )


def test_the_server_stops(tmp_path):
    server = WizardServer(_wizard(tmp_path)).start()
    url = server.url
    server.stop()

    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(url, timeout=2)


# --- the window -----------------------------------------------------------------------


def _fake_webview(monkeypatch, *, on_start=None):
    """A stand-in for pywebview: records the window it was asked for, and 'closes' at once."""
    made = {}
    module = types.ModuleType("webview")

    def create_window(title, url, **kwargs):
        made.update(title=title, url=url, **kwargs)

    def start():
        made["page"] = urllib.request.urlopen(made["url"]).read()
        if on_start:
            on_start()

    module.create_window = create_window
    module.start = start
    monkeypatch.setitem(sys.modules, "webview", module)
    return made


def test_the_wizard_opens_in_a_pywebview_window_showing_the_page(monkeypatch, tmp_path):
    made = _fake_webview(monkeypatch)

    assert launcher.run(_wizard(tmp_path)) == 0

    assert made["title"] == "Test"
    assert made["url"].startswith("http://127.0.0.1:")
    assert b'class="rail"' in made["page"] and b'id="steps"' in made["page"]


def test_closing_the_window_stops_the_server(monkeypatch, tmp_path):
    made = _fake_webview(monkeypatch)

    launcher.run(_wizard(tmp_path))

    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(made["url"], timeout=2)


def test_a_window_that_fails_still_stops_the_server(monkeypatch, tmp_path):
    def fail():
        raise RuntimeError("WebView2 missing")

    made = _fake_webview(monkeypatch, on_start=fail)

    with pytest.raises(RuntimeError):
        launcher.run(_wizard(tmp_path))
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(made["url"], timeout=2)


def test_without_pywebview_the_operator_is_told_what_to_do(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setitem(sys.modules, "webview", None)  # makes `import webview` fail

    assert launcher.run(_wizard(tmp_path)) == 1

    said = capsys.readouterr().err
    assert "pywebview" in said and "--browser" in said


def test_browser_mode_opens_the_page_in_the_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(launcher.webbrowser, "open", lambda url: opened.append(url))

    def ctrl_c():
        assert (
            urllib.request.urlopen(opened[0]).status == 200
        )  # the page is up while waiting
        raise KeyboardInterrupt

    monkeypatch.setattr(launcher, "_wait_until_stopped", ctrl_c)

    assert launcher.run(Wizard("T", []), how="browser") == 0

    assert opened and opened[0].startswith("http://127.0.0.1:")
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(opened[0], timeout=2)


def test_the_command_line_chooses_how_the_wizard_opens(monkeypatch):
    chosen = []
    monkeypatch.setattr(
        launcher,
        "run",
        lambda wizard, how, port: chosen.append((wizard.title, how, port)) or 0,
    )

    for argv in ([], ["--browser"], ["--no-window", "--port", "8765"]):
        assert launcher.main(argv) == 0

    assert [c[1:] for c in chosen] == [("window", 0), ("browser", 0), ("serve", 8765)]
    assert all("Leica" in title for title, *_ in chosen)


# --- self-contained: the driver, and nothing else of ZMART -----------------------------


OTHER_ZMART_PACKAGES = (
    "zmart_controller",
    "zmart_interface",
    "zmart_analysis",
    "zmart_viewer",
)


def test_the_wizard_imports_no_other_zmart_package():
    for path in sorted(WIZARD_DIR.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                assert not name.startswith(OTHER_ZMART_PACKAGES), (
                    f"{path.name} imports {name}"
                )


def test_the_page_names_no_other_zmart_part_and_loads_nothing_from_the_internet():
    page_src = WIZARD_DIR / "page-src"
    sources = [
        *PAGE.rglob("*.*"),
        *(page_src / "src").rglob("*.*"),
        page_src / "index.html",
        page_src / "package.json",
    ]
    for path in sources:
        text = path.read_text(encoding="utf-8")
        assert not re.search(
            r"zmart[-_](interface|controller|analysis|viewer)", text, re.IGNORECASE
        ), path
    for path in PAGE.rglob("*.*"):
        assert not re.search(
            r"""(src|href)\s*=\s*["']?(https?:)?//""", path.read_text(encoding="utf-8")
        ), path
        assert 'fetch("http' not in path.read_text(encoding="utf-8"), path


def test_the_page_needs_no_package_to_run():
    """The page is built from TypeScript, but only build tools are needed for that."""
    package = json.loads(
        (WIZARD_DIR / "page-src" / "package.json").read_text(encoding="utf-8")
    )

    assert package.get("dependencies", {}) == {}


def test_the_built_page_is_shipped_and_up_to_date_with_its_source():
    built = PAGE / "index.html"
    assert built.is_file()
    assert list((PAGE / "assets").glob("*.js")) and list(
        (PAGE / "assets").glob("*.css")
    )
    # Every sentence of the step views reaches the built page; a source edited
    # without `npm run build` would leave the built page behind.
    script = "".join(
        p.read_text(encoding="utf-8") for p in (PAGE / "assets").glob("*.js")
    )
    sources = sorted((WIZARD_DIR / "page-src" / "src").rglob("*.ts"))
    sentences = [
        sentence
        for path in sources
        for sentence in re.findall(
            r'"([A-Z][^"`$]{40,})"', path.read_text(encoding="utf-8")
        )
    ]
    assert len(sentences) >= 5
    for sentence in sentences:
        assert sentence in script, f"the built page lacks: {sentence[:60]}..."
