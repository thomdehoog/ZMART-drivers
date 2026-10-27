"""The NIS-Elements AR 6.10 driver for a Nikon microscope (a Ti2, or any stand).

Use the driver by itself with ``connect()``, which reaches NIS-Elements
through the nis-bridge and hands back a ``Microscope``; the README says what
it can do. The ZMART Controller plugs the driver in through
``zmart_controller_plugin/``, which is built on the same ``Microscope``.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    nis_elements_ar610/
    - __init__.py         connect() and Microscope, nothing else
    - methods.py          what the user calls: connect(), the Microscope, its Session, the
                          running log
    - procedures/         chains of steps, one file each, a folder when it needs more: the named
                          changes and readings, the coordinate system, keep_in_view, acquire,
                          focus, state and save_data/
    - dispatcher/         the same in every driver; imports nothing of the driver: dispatch.py
                          (run()), read.py, write.py, limits.py
    - primitives/         one folder for each way NIS-Elements is reached through the bridge:
                          dll/ (what g5_regprocs.dll exports) and macro/ (what only a NIS
                          macro can ask), each with its transfer, reads, writes and profile
    - driver_setup/       what is set up once per microscope, one folder per item: limits,
                          origin, orientation, optical_calibration and data_location
    - zmart_controller_plugin/  what the controller sees: the ZmartControllerPlugin class, one
                          method per controller command, each a call of the Microscope
    - testing/            the mock NIS-Elements behind the real bridge server, the offline
                          suite, the check at the microscope

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.7.0b9"

__all__ = ["Microscope", "__version__", "connect"]

from .methods import Microscope, connect
