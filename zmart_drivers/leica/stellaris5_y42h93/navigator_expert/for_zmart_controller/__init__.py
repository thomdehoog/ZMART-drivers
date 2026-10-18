"""Everything of the Leica driver that faces the ZMART Controller.

``zmart_driver.py`` holds the ``ZmartDriver`` class that the controller makes when
it connects. ``zmart_controller_plugin.py`` is the older shape of the same
driver, one function per command, kept until the class has run on the
microscope. The folder is a package so that the controller can import the class
file together with the rest of the driver; the file is named in
``zmart_driver.json`` at the top of the driver folder.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
