"""ZMART Drivers: the drivers that connect the ZMART Controller to real microscopes.

Each driver lives in its own folder, organised by vendor, then by microscope,
then by the vendor interface it uses. This beta holds two drivers::

    zmart_drivers/leica/stellaris/navigator_expert/        Leica STELLARIS, LAS X
    zmart_drivers/zeiss/light_microscope/zen_api_grpc/     ZEISS, ZEN 3.13 and its gRPC ZEN API

Importing this package does not import a driver. Use a driver by itself
with its ``connect()``, or register its folder, which holds
``zmart_controller_plugin/``, with the ZMART Controller; each driver's README explains both.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

__version__ = "0.7.0b9"
