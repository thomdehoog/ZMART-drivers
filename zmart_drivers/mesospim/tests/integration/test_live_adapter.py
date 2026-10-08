"""
Live adapter round-trip through a real ``zmart_controller.ZmartController``.
============================================================================
The controller-level analog of ``test_live_roundtrip`` (which drives the flat
driver): this drives the mesoSPIM **adapter** (``mesospim_zmart_adapter``) exactly
as a workflow would -- ``zmart_controller.ZmartController(...) -> get_*/
set_*/acquire`` -- against a live mesoSPIM Remote Scripting server (ideally ``-D``
demo mode). It is the bench check that the neutral controller contract is wired
end to end, not just the driver's own API.

Run it (mesoSPIM ``-D`` with Remote Scripting started on 42000)::

    MESOSPIM_TOKEN=<token> python -m pytest zmart_drivers/mesospim/tests -m integration

Set ``MESOSPIM_ALLOW_ACQUIRE=1`` to include the capture (fires a snap). If nothing
listens on the address every test *skips*. Author: Thom de Hoog (ZMB, UZH). MIT.
"""

from __future__ import annotations

import os
import socket

import pytest
import zmart_controller

from zmart_drivers.mesospim import zmart_controller_plugin as driver

pytestmark = pytest.mark.integration

_HOST = os.environ.get("MESOSPIM_HOST", "127.0.0.1")
_PORT = int(os.environ.get("MESOSPIM_PORT", "42000"))
_TOKEN = os.environ.get("MESOSPIM_TOKEN")  # set when the server requires a token
_ALLOW_ACQUIRE = os.environ.get("MESOSPIM_ALLOW_ACQUIRE") == "1"

_CONN = {
    "host": _HOST,
    "port": _PORT,
    "token": _TOKEN,
}


def _server_listening() -> bool:
    try:
        with socket.create_connection((_HOST, _PORT), timeout=1.0):
            return True
    except OSError:
        return False


@pytest.fixture
def session():
    """A connected controller Session, or skip if no server is reachable.

    The skip is decided by a raw socket probe, NOT by catching connect errors, so
    that a *reachable* server whose adapter fails to connect is a test FAILURE,
    never a silent skip.
    """
    if not _server_listening():
        pytest.skip(f"no live mesoSPIM Remote Scripting server at {_HOST}:{_PORT}")
    sess = zmart_controller.ZmartController(driver, dict(_CONN))
    try:
        yield sess
    finally:
        sess.disconnect()


def test_info_and_actuators(session):
    info = session.get_info()["content"]
    assert info["server"]["app"] == "mesoSPIM-control"
    actuators = session.get_actuators()["content"]
    assert set(actuators) == {"x", "y", "z"}
    assert actuators["x"] == ["motoric"]


def test_get_xyz_and_state_shape(session):
    xyz = session.get_xyz()["content"]
    for axis in ("x", "y", "z"):
        assert list(xyz[axis]) == ["position", "unit", "actuators", "canvas"]
        assert isinstance(xyz[axis]["position"], (int, float))
        assert xyz[axis]["unit"] == "micrometer"
        # One motor per axis, reporting the stage's own number.
        assert set(xyz[axis]["actuators"]) == {"motoric"}
        assert isinstance(xyz[axis]["actuators"]["motoric"], (int, float))
    state = session.get_state()["content"]
    # changeable = the light-path settings; observed = identity + limits (never
    # the run-state, which is unobservable over the bridge).
    assert "laser" in state["changeable"]
    assert state["observed"]["app"] == "mesoSPIM-control"


def test_acquisition_settings(session):
    opts = session.get_acquisition_settings()["content"]
    assert "planes" in opts and "z_step" in opts
    assert opts["format"]["active"] in opts["format"]["options"]


def test_set_xyz_zero_net_motion_confirms(session):
    """Exercise set_xyz + confirm through the adapter with zero net motion.

    The move answers the position read back from the stage, in get_xyz's
    shape; an unconfirmed move would be a failed answer instead.
    """
    xyz = session.get_xyz()["content"]
    x, y, z = (xyz[a]["position"] for a in ("x", "y", "z"))
    answer = session.set_xyz(x, y, z)
    assert answer["success"], answer
    result = answer["content"]
    for axis, asked in zip(("x", "y", "z"), (x, y, z)):
        assert result[axis]["position"] == pytest.approx(asked, abs=1.0)
        assert list(result[axis]) == ["position", "unit", "actuators", "canvas"]


@pytest.mark.skipif(
    not _ALLOW_ACQUIRE,
    reason="set MESOSPIM_ALLOW_ACQUIRE=1 to run the capture (fires a snap)",
)
def test_acquire_through_session(session, tmp_path):
    result = session.acquire("A1")["content"]
    files = result.get("files") or []
    assert files, f"no files in acquire result: {result!r}"
    for path in files:
        assert os.path.isfile(path), f"reported file missing: {path}"
