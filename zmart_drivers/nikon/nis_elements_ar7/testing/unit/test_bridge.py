"""The bridge: the small server that runs inside NIS-Elements, and the client the driver talks to it with.

These tests run the real ``server.py`` over the pretend NIS, pumped by a
thread in place of NIS's macro loop. They check what the NIS 7 study showed
matters: every request runs on the pumping thread, every failure comes back
as a reply (never a dialog or a hang), a stranger cannot use the bridge, and
a stopped macro loop is told apart from a slow NIS.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from bridge_stand_in import KEY, StandIn
from mock_nis import make_nis

from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import server
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import (
    BridgeUnavailable,
    NisClient,
    NisRefused,
)


def raw(port: int, request: dict | bytes) -> dict | None:
    """Send one line to the bridge as anyone could, and read one reply line (None when it hangs up)."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        line = (
            request
            if isinstance(request, bytes)
            else (json.dumps(request) + "\n").encode()
        )
        sock.sendall(line)
        answer = sock.makefile("rb").readline()
    return json.loads(answer) if answer else None


def test_a_reading_round_trips_and_runs_on_the_pumping_thread(stand_in, client):
    seen = []
    original = stand_in.bridge.run

    def recording(request):
        seen.append(threading.current_thread().name)
        return original(request)

    stand_in.bridge.run = recording
    answer = client.read("StgGetPosXY", [{"ptr": "double"}, {"ptr": "double"}])
    assert answer["values"] == [1000.0, 2000.0]
    assert seen == ["stand-in macro loop"]


def test_ping_reports_the_version_and_how_lately_the_loop_ran(client):
    answer = client.ping()
    assert answer["bridge"] == server.VERSION
    assert answer["since_last_pump_s"] < 1.0


def test_a_failing_nis_function_comes_back_as_a_reply(client):
    with pytest.raises(NisRefused) as refused:
        client.read("StgGetPosXY", [{"ptr": "double"}])  # one argument too few
    assert "Bad number of parameters" in str(refused.value)
    assert client.ping()["bridge"] == server.VERSION  # and the bridge still answers


def test_a_wrong_key_is_refused_and_the_connection_closed(stand_in):
    stranger = NisClient(port=stand_in.port, key="not-the-key")
    with pytest.raises(BridgeUnavailable, match="key"):
        stranger.ping()
    assert not stranger.connected


def test_a_request_without_a_key_runs_nothing(stand_in):
    reply = raw(stand_in.port, {"op": "write", "fn": "StgMoveXY", "args": [0, 0, 0]})
    assert reply["kind"] == server.KIND_NOT_AUTHORISED
    assert "StgMoveXY" not in stand_in.scope.calls


def test_a_web_page_is_cut_off(stand_in):
    assert raw(stand_in.port, b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n") is None
    assert stand_in.scope.calls == []


def test_only_the_named_readings_can_be_read(stand_in, client):
    with pytest.raises(NisRefused):
        client.read("Capture")
    reply = raw(stand_in.port, {"key": KEY, "op": "read", "fn": "Capture", "args": []})
    assert reply["kind"] == server.KIND_NOT_ALLOWED
    reply = raw(
        stand_in.port, {"key": KEY, "op": "write", "fn": "Get_InfoStr", "args": []}
    )
    assert reply["kind"] == server.KIND_NOT_ALLOWED
    assert "Capture" not in stand_in.scope.calls


def test_a_move_beyond_nis_own_travel_is_refused_by_the_bridge(stand_in):
    reply = raw(
        stand_in.port,
        {"key": KEY, "op": "write", "fn": "StgMoveXY", "args": [1e9, 0, 0]},
    )
    assert reply["ok"] is False and "travel" in reply["error"]
    reply = raw(
        stand_in.port, {"key": KEY, "op": "write", "fn": "StgMoveZ", "args": [-5.0, 0]}
    )
    assert reply["ok"] is False and "travel" in reply["error"]
    assert (
        "StgMoveXY" not in stand_in.scope.calls
        and "StgMoveZ" not in stand_in.scope.calls
    )


def test_a_request_nis_never_takes_comes_back_as_queued_timeout(monkeypatch):
    monkeypatch.setattr(server, "REQUEST_TIMEOUT_S", 0.3)
    stand_in = StandIn(pumping=False)
    try:
        stand_in.bridge.last_pump = (
            time.monotonic() + 60
        )  # pretend the loop ran a moment ago
        reply = raw(
            stand_in.port,
            {
                "key": KEY,
                "op": "read",
                "fn": "StgGetPosZ",
                "args": [{"ptr": "double"}, 0],
            },
        )
        assert reply["kind"] == server.KIND_QUEUED_TIMEOUT
        stand_in.pumping.set()
        time.sleep(0.1)
        assert (
            "StgGetPosZ" not in stand_in.scope.calls
        )  # cancelled, so it never runs late
    finally:
        stand_in.close()


def test_a_stopped_macro_loop_is_told_apart_and_the_client_reconnects(
    monkeypatch, stand_in, client
):
    monkeypatch.setattr(server, "NOT_PUMPING_S", 0.2)
    stand_in.stop_pumping()
    time.sleep(0.3)
    with pytest.raises(BridgeUnavailable, match="macro loop"):
        client.read("StgGetPosZ", [{"ptr": "double"}, 0])
    assert not client.connected
    stand_in.pumping.set()
    time.sleep(0.05)
    assert client.read("StgGetPosZ", [{"ptr": "double"}, 0])["values"][0] == 3000.0


def test_nothing_listening_is_said_plainly():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(BridgeUnavailable, match="start_bridge.mac"):
        NisClient(port=port, key=KEY).ping()


def test_closing_answers_a_queued_request(stand_in):
    stand_in.stop_pumping()
    stand_in.bridge.last_pump = time.monotonic() + 60
    replies = []
    thread = threading.Thread(
        target=lambda: replies.append(
            raw(
                stand_in.port,
                {"key": KEY, "op": "read", "fn": "GetOptConfCount", "args": []},
            )
        )
    )
    thread.start()
    time.sleep(0.2)
    stand_in.bridge.close()
    thread.join(5)
    assert replies and replies[0]["kind"] == server.KIND_CLOSED


# --- what the macro runs: start, pump, stop ----------------------------------------------


def test_start_writes_a_key_and_a_second_start_replaces_the_first(tmp_path):
    stop_file, key_file = tmp_path / "stop", tmp_path / "bridge" / "key"
    stop_file.write_text("")  # left by an earlier run
    nis = make_nis()
    try:
        status = server.start(
            str(stop_file),
            str(key_file),
            port=0,
            log_path=str(tmp_path / "log.txt"),
            nis_module=nis,
        )
        assert "listening" in status
        assert not stop_file.exists(), (
            "a stop file left from before would end the new loop at once"
        )
        first = server._registry()["bridge"]
        first_key = key_file.read_text()
        server.start(
            str(stop_file),
            str(key_file),
            port=0,
            log_path=str(tmp_path / "log.txt"),
            nis_module=nis,
        )
        assert first.closed
        assert key_file.read_text() != first_key
        server.pump()
        assert not stop_file.exists()
        server._registry()["bridge"].stop_requested = True
        server.pump()
        assert stop_file.exists(), (
            "a requested stop ends the macro's loop through the stop file"
        )
    finally:
        assert "stopped" in server.stop()
    assert not stop_file.exists()
    assert server.stop() == "ZMART bridge was not running"


def test_the_shutdown_request_ends_the_loop(stand_in, client):
    client.shutdown()
    assert stand_in.bridge.stop_requested


def test_importing_the_server_changes_nothing():
    """The bridge is opened only by the macro's ``start``: importing the file opens no port and starts no thread."""
    code = (
        "import sys, threading, importlib.util;"
        f"spec = importlib.util.spec_from_file_location('server', {str(Path(server.__file__))!r});"
        "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module);"
        "import __main__;"
        "assert not hasattr(__main__, '_zmart_nis_bridge');"
        "assert threading.active_count() == 1;"
        "print('quiet')"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert done.stdout.strip() == "quiet", done.stderr
