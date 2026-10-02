"""ZMART Drivers: the drivers that connect the ZMART Controller to real microscopes.

Each driver lives in its own folder, organised by vendor, then by microscope,
then by the vendor interface it uses::

    zmart_drivers/leica/stellaris5_y42h93/navigator_expert/   Leica STELLARIS 5, LAS X
    zmart_drivers/nikon/nis_elements_6_10/                    Nikon Ti2, NIS-Elements 6.10
    zmart_drivers/zeiss/zenapi/                               ZEISS, ZEN API
    zmart_drivers/mesospim/                                   mesoSPIM light-sheet

Importing this package does not import any driver, and no driver is plugged
into the controller by itself. On the microscope computer, plug in the one
driver that belongs to that microscope, once, by its folder or module name::

    import zmart_controller

    zmart_controller.register_driver("zmart_drivers.nikon.nis_elements_6_10")

Each driver folder holds a ``zmart_controller/`` folder with a ``zmart.json``
that names its instrument; that is what the controller reads.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.1.0rc1"
