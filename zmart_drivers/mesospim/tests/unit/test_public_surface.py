"""The package public surface: imports, version, session helpers."""

from __future__ import annotations

import zmart_drivers.mesospim as mesospim


def test_version_present():
    assert isinstance(mesospim.__version__, str)


def test_all_names_are_importable():
    for name in mesospim.__all__:
        assert hasattr(mesospim, name), f"missing public name: {name}"


def test_key_functions_exposed():
    for name in (
        "connect",
        "close",
        "move_xy",
        "move_z",
        "set_filter",
        "acquire",
        "save",
        "load_stage_config",
    ):
        assert callable(getattr(mesospim, name))


def test_connect_close_via_public_api(server):
    client = mesospim.connect({"host": server.host, "port": server.port})
    try:
        assert mesospim.ping(client)
    finally:
        mesospim.close(client)
