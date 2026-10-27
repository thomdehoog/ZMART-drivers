"""The FLUOVIEW FV4000 driver, through Evident's Remote Development Kit (RDK).

Use the driver by itself with ``connect()``, which reaches the FLUOVIEW
software over the network and hands back a ``Microscope``; the README says
what it can do. The ZMART Controller plugs the driver in through
``zmart_controller_plugin/``, which is built on the same ``Microscope``.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    rdk/
    - __init__.py         connect() and Microscope, nothing else
    - methods.py          what the user calls: connect(), the Microscope, its Session, the
                          running log
    - procedures/         chains of steps, one file each, a folder when it needs more: the named
                          changes and readings, the coordinate system, keep_in_view, acquire,
                          state, and save_data/
    - dispatcher/         the same in every driver; imports nothing of the driver: dispatch.py
                          (run()), read.py, write.py, limits.py
    - primitives/         one folder for each way of reaching FLUOVIEW, with its transfer,
                          reads, writes and profile: rdk/ (the RDK, an XML-RPC server)
    - driver_setup/       what is set up once per microscope, one folder per item: limits,
                          origin, orientation, optical_calibration and data_location; and
                          frame.py, what one picture covers, learned from the pictures
    - zmart_controller_plugin/  what the controller sees: the ZmartControllerPlugin class, one
                          method per controller command, each a call of the Microscope
    - testing/            the mock FLUOVIEW, the offline suite, the checks at the microscope

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.7.0b9"

__all__ = ["Microscope", "__version__", "connect"]

from .methods import Microscope, connect
