"""Everything of the Nikon driver that faces the ZMART Controller.

``zmart_controller_plugin.py`` holds the ``ZmartControllerPlugin`` class that
the controller makes when it connects. The folder is a package, so that the
controller can import that file together with the rest of the driver. This is
the folder to register with the controller: ``zmart_controller_plugin.json``
beside it gives the driver's name and its connection settings.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
