"""The origin: the stage position that the driver, and the ZMART Controller, call (0, 0, 0).

It is saved once with ``set_origin()`` (``set_origin.py``), as a new
``origin/<datetime>/origin.json`` in this microscope's settings, and every
later connection loads the newest one (``saved_origin.py``). Until one is
saved, positions are NIS's own stage coordinates.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
