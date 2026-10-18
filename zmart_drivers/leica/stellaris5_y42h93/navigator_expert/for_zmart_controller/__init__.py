"""Everything of the Leica driver that faces the ZMART Controller.

``zmart_driver.py`` holds the ``ZmartDriver`` class that the controller makes when
it connects. The folder is a package so that the controller can import the class
file together with the rest of the driver; the file is named in
``zmart_driver.json``, which sits beside it in this folder; this is the folder
to register with the controller.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
