"""Navigator Expert driver for Leica LAS X.

Use the driver by itself with ``connect()``, which opens LAS X and hands back
a ``Microscope``; the README says what it can do. The ZMART Controller plugs
the driver in through ``zmart_controller_plugin/``, which is built on the same
``Microscope``.

Package layout, the anatomy of a ZMART driver (``docs/driver-anatomy.md`` in
this repository)::

    navigator_expert/
    - for_direct_use/     the driver used by itself: connect() and the Microscope, taking a
                          picture, the state saved with it, and the driver's rolling log
    - zmart_controller_plugin/  what the controller sees: the ZmartControllerPlugin class (zmart_controller_plugin.py),
                          one method per controller command, each a call of the Microscope,
                          and what only the controller asks for (info.py, procedure_menu.py)
    - procedures/         the recipes, one folder each: saving the captures (save_data/),
                          letting LAS X's cache go (clear_cache/), the stage frame
                          (absolute_coordinate_system/) and changing the objective
                          (objective_change_with_compensation/)
    - session/            what the driver remembers about one connection
    - driver_setup/       what is set up once per microscope, one folder per item: limits,
                          origin, orientation, optical_calibration and
                          data_location, each with its defaults, loader, measuring code
                          and notebook; plus the store that finds the saved values
    - dispatcher/         the machinery that runs a command safely, and names no LAS X
                          object: the one door (read and write), the reader, the send and
                          confirm loops and the limits gate
    - vendor_specific/    everything that is specific to LAS X: the connection and its waiting
                          numbers, the primitives (the smallest reads and writes) and
                          primitive_handling/, which says how each command is treated
    - testing/            the mock LAS X API, the offline suite, the hardware validators

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "7.0.0b1"

__all__ = ["__version__", "connect", "Microscope"]

from .for_direct_use.microscope import Microscope, connect
