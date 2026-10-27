"""The macro that starts the bridge in NIS: plain ASCII, guarded, and working.

NIS refuses without a word a macro it cannot compile, so the macro is kept
plain. The last test runs the macro's three Python lines as NIS would, with
the pretend ``nis`` module, so that a mistake in the template shows here and
not at the microscope.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import socket
import sys
from pathlib import Path

import pytest
from mock_nis import make_nis

from zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge import macro
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import NisClient


def test_the_macro_is_plain_ascii_with_crlf_line_ends(tmp_path):
    path = macro.write_macro(tmp_path)
    raw = path.read_bytes()
    raw.decode("ascii")
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")
    assert "//" not in raw.decode("ascii").replace("://", ""), (
        "no comments: NIS may refuse them"
    )


def test_every_python_line_is_guarded(tmp_path):
    text = macro.write_macro(tmp_path).read_text(encoding="ascii")
    lines = re.findall(r'Python_RunString\("(.*)"\);', text)
    assert len(lines) == 3
    assert "open(" in lines[1] and "if p else" in lines[1], (
        "the loop's line writes the stop file when the bridge is gone"
    )


def test_a_path_with_an_apostrophe_or_outside_ascii_is_refused(tmp_path):
    for odd in ("O'Brien", "Zürich"):
        with pytest.raises(ValueError):
            macro.write_macro(tmp_path / odd)


def test_the_macro_lines_start_pump_and_stop_the_bridge(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    text = macro.write_macro(tmp_path, port=port).read_text(encoding="ascii")
    start, pump, stop = re.findall(r'Python_RunString\("(.*)"\);', text)
    nis = make_nis()
    monkeypatch.setitem(sys.modules, "nis", nis)
    monkeypatch.delitem(sys.modules, "zmart_nis_bridge", raising=False)
    monkeypatch.setenv("TEMP", str(tmp_path))
    files = macro.files(tmp_path)
    try:
        exec(start, {})  # noqa: S102 -- the macro's own line, run as NIS would
        assert any("listening" in line for line in nis.logged)
        key = Path(files["key"]).read_text(encoding="ascii")
        import threading

        done = threading.Event()

        def loop():
            while not done.is_set() and not files["stop"].exists():
                exec(pump, {})  # noqa: S102

        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        client = NisClient(port=port, key=key)
        assert client.read("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0] == 3000.0
        client.shutdown()
        thread.join(5)
        assert files["stop"].exists(), (
            "a requested stop ends the loop through the stop file"
        )
        client.close()
    finally:
        done.set()
        exec(stop, {})  # noqa: S102
    assert any("stopped" in line for line in nis.logged)
    assert not files["stop"].exists()
