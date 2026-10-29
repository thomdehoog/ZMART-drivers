"""The origin: the stage position that the controller calls (0, 0, 0).

It is set once, in a setup step where the operator works with the driver
directly (``set_origin.py``), and saved to this microscope's configuration as
a new ``origin/<datetime>/origin.json``. Every later connection loads the
newest one (``saved_origin.py``), so all sessions share the same frame until
the origin is saved again.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
