"""Everything of the FV4000 driver that faces the ZMART Controller.

``zmart_controller_plugin.py`` holds the ``ZmartControllerPlugin`` class that
the controller makes when it connects. The folder is a package so that the
controller can import the class file together with the rest of the driver;
the driver's name and connection are in ``zmart_controller_plugin.json``
beside it. The folder to register with the controller is the driver's
(``rdk/``), the one that holds this folder.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
