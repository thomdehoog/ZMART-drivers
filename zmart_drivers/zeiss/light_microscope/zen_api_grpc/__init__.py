"""The ZEISS driver: a ZEISS light microscope driven through ZEN 3.13 and its gRPC ZEN API.

Use the driver by itself with ``connect()``, which opens the connection to
the ZEN API Gateway and hands back a ``Microscope``; the README says what it
can do. The ZMART Controller plugs the driver in through
``zmart_controller_plugin/``, which is built on the same ``Microscope``.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    zen_api_grpc/
    - __init__.py         connect() and Microscope, nothing else
    - methods.py          what the user calls: connect(), the Microscope, its Session, the running log
    - procedures/         chains of steps: the named changes and readings, the coordinate system,
                          keep_in_view, acquire, state, and save_data/
    - dispatcher/         the same in every driver; imports nothing of the driver
    - primitives/         one folder for each way of reaching ZEN: gateway/, the ZEN API over gRPC,
                          with its client, transfer, reads, writes and profile
    - driver_setup/       what is set up once per microscope: limits, origin, orientation,
                          optical_calibration and data_location
    - zmart_controller_plugin/  what the controller sees: the ZmartControllerPlugin class
    - testing/            the stand-in for ZEN, the offline suite, the check at the microscope

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.1.0b1"

__all__ = ["Microscope", "__version__", "connect"]

from .methods import Microscope, connect
