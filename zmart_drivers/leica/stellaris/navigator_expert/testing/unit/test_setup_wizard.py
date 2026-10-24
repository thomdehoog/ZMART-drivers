"""The setup wizard's core and server, with no microscope and no LAS X."""

import json
import urllib.request

from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.framework import (
    Step,
    StepError,
    Wizard,
)
from zmart_drivers.leica.stellaris.navigator_expert.driver_setup.wizard.server import (
    PAGE,
    WizardServer,
)


def _wizard(tmp_path):
    picture = tmp_path / "a.png"
    picture.write_bytes(b"png")

    def refuse(_values):
        raise StepError("The stage is still moving.")

    return Wizard("Test", [Step("a", "A", "purpose", {
        "ok": lambda v: {"n": v.get("n"), "image": str(picture)},
        "bad": refuse,
    })])


def test_an_action_that_fails_answers_with_the_reason(tmp_path):
    answer = _wizard(tmp_path).call("a", "bad")
    assert answer == {"ok": False, "error": "The stage is still moving."}


def test_an_unknown_action_is_refused_in_words(tmp_path):
    assert not _wizard(tmp_path).call("a", "nope")["ok"]


def test_the_server_carries_calls_and_hands_out_only_named_pictures(tmp_path):
    server = WizardServer(_wizard(tmp_path)).start()
    try:
        def post(path, body):
            request = urllib.request.Request(server.url + path, json.dumps(body).encode())
            return json.loads(urllib.request.urlopen(request).read())

        answer = post("api/a/ok", {"n": 3})
        assert answer["result"]["n"] == 3
        assert urllib.request.urlopen(server.url + answer["result"]["image"]).read() == b"png"
        for bad in ("../framework.py", "files/unknown.png"):
            try:
                urllib.request.urlopen(server.url + bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as why:
                assert why.code == 404
    finally:
        server.stop()


def test_the_built_page_is_shipped():
    assert (PAGE / "index.html").is_file()
    assert list((PAGE / "assets").glob("*.js"))
