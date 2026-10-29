"""The plain Navigator driver for Leica LAS X.

Drives a Leica STELLARIS through LAS X's own Navigator, without the Navigator
Expert add-in: every reading and every change goes straight to LAS X's
hardware server, the way LAS X's window itself does. Use the driver by itself
with ``connect()``, which reaches LAS X and hands back a ``Microscope``; the
README says what it can do. The ZMART Controller plugs the driver in through
``zmart_controller_plugin/``, which is built on the same ``Microscope``.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    navigator/
    - __init__.py         connect() and Microscope, nothing else
    - methods.py          what the user calls: connect(), the Microscope, its Session, the
                          running log
    - procedures/         chains of steps, one file each, a folder when it needs more: the named
                          changes and readings, the coordinate system, keep_in_view, acquire,
                          state, LAS X's dialogs, clear_cache/ and save_data/
    - dispatcher/         the same in every driver; imports nothing of the driver: dispatch.py
                          (run()), read.py, write.py, limits.py
    - primitives/         one folder for this driver's one way of reaching LAS X, its hardware
                          server: direct/, with its transfer, reads, writes and profile
    - driver_setup/       what is set up once per microscope, one folder per item: limits,
                          origin, orientation, optical_calibration and data_location
    - zmart_controller_plugin/  what the controller sees: the ZmartControllerPlugin class, one
                          method per controller command, each a call of the Microscope
    - testing/            the stand-in for LAS X's hardware server, the offline suite, the
                          check at the microscope

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.1.0b1"

__all__ = ["Microscope", "__version__", "connect"]

from .methods import Microscope, connect
