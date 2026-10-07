"""ZMART Drivers: the drivers that connect the ZMART Controller to real microscopes.

Each driver lives in its own folder, organised by vendor, then by microscope,
then by the vendor interface it uses::

    zmart_drivers/leica/stellaris5_y42h93/navigator_expert/   Leica STELLARIS 5, LAS X
    zmart_drivers/nikon/nis_elements_6_10/                    Nikon Ti2, NIS-Elements 6.10
    zmart_drivers/zeiss/zenapi/                               ZEISS, ZEN API
    zmart_drivers/mesospim/                                   mesoSPIM light-sheet

Importing this package does not import any driver. On the microscope
computer, import the driver module that belongs to that microscope and hand
it to the controller::

    import zmart_controller
    import zmart_drivers.nikon.nis_elements_6_10.driver as nikon

    zmart_controller.set_instrument(nikon)

Each driver folder holds a ``driver.py`` with one function per controller
command; that module is what the controller is handed.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.1.0rc1"
