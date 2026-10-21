"""ZMART Drivers: the drivers that connect the ZMART Controller to real microscopes.

Each driver lives in its own folder, organised by vendor, then by microscope,
then by the vendor interface it uses. This beta holds one driver::

    zmart_drivers/leica/stellaris5_y42h93/navigator_expert/   Leica STELLARIS 5, LAS X

Importing this package does not import the driver. Use the driver by itself
with its ``connect()``, or register its ``for_zmart_controller`` folder with
the ZMART Controller; the driver's README explains both.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.2.0b1"
