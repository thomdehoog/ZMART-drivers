"""The real bridge, serving the pretend NIS in this process, with a thread in place of the macro loop.

Inside NIS-Elements, ``start_bridge.mac`` starts the bridge and its loop
calls ``pump()`` every 10 ms on NIS's own thread. Here a thread does the
pumping, the bridge listens on a free port of this computer, and its key is
handed straight to the client. Everything else is the real thing: the real
``server.py``, the real ``NisClient``, and the driver above them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Modelled on the step 1 offline test of thomdehoog/nis-useq-aiagent.
"""

from __future__ import annotations

import threading
import time

from mock_nis import PretendMicroscope, make_nis

from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python import server
from zmart_drivers.nikon.nis_elements_ar7.primitives.nis_python.client import NisClient

KEY = "test-key-0123456789abcdef"


class StandIn:
    """A bridge over a pretend microscope, pumped by a thread until :meth:`close`."""

    def __init__(self, scope: PretendMicroscope | None = None, *, pumping: bool = True):
        self.nis = make_nis(scope)
        self.scope = self.nis.scope
        self.bridge = server.serve(self.nis, KEY, port=0)
        self.port = self.bridge.server.server_address[1]
        self.pumping = threading.Event()
        self._idle = threading.Event()
        self._stop = threading.Event()
        if pumping:
            self.pumping.set()
        self._thread = threading.Thread(
            target=self._loop, name="stand-in macro loop", daemon=True
        )
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            if self.pumping.is_set():
                self._idle.clear()
                self.bridge.pump(wait_s=0.005)
            else:
                self._idle.set()
                time.sleep(0.005)

    def client(self, **kwargs) -> NisClient:
        """A client of this bridge, not yet connected (it connects on its first request)."""
        return NisClient(port=self.port, key=KEY, **kwargs)

    def stop_pumping(self) -> None:
        """What NIS's Stop button does: the loop ends, the bridge stays open. Returns once the last pass is over."""
        self.pumping.clear()
        self._idle.wait(timeout=2)

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        self.bridge.close()
