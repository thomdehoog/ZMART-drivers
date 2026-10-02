"""Shared pytest setup: import bootstrap + mock-server/client fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add the repository root so the driver imports by its full name,
# zmart_drivers.mesospim, even when the repository is not pip-installed.
# parents: [0]=tests [1]=mesospim [2]=zmart_drivers [3]=repository root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Add the helpers dir so `import mock_mesospim_server` works.
_HELPERS = Path(__file__).resolve().parent / "helpers"
if str(_HELPERS) not in sys.path:
    sys.path.insert(0, str(_HELPERS))


@pytest.fixture
def server(tmp_path):
    """A running mock command server writing frames under a temp dir."""
    from mock_mesospim_server import MockMesospimServer

    with MockMesospimServer(output_dir=tmp_path / "server_out") as srv:
        yield srv


@pytest.fixture
def client(server):
    """A connected MesospimClient talking to the mock server."""
    from zmart_drivers.mesospim.connection.client import MesospimClient

    c = MesospimClient(server.host, server.port, timeout=3.0)
    c.connect()
    try:
        yield c
    finally:
        c.close()
