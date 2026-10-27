"""The one way this driver reaches NIS-Elements: NIS's own Python, over a local socket.

A small program, the bridge (``server.py``), runs inside NIS-Elements and
calls NIS's macro functions by name. The driver talks to it through
``client.py``. ``reads.py`` holds the readings, ``writes.py`` the changes,
``transfer.py`` how a change is sent, and ``profile.py`` what the dispatcher
needs to run them. The macro that starts the bridge is written by
``driver_setup/nis_bridge``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
