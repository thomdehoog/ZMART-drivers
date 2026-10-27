"""What is set up once per microscope: limits, origin, orientation, objective calibration, data location.

Each item has its own folder here, with its defaults, its loader, the code
that measures it, and a notebook that only calls that code. The saved values
live in the computer's configuration folder (``store.py`` says where), never
in the repository. The run-time procedures read the saved values through each
item's loader; they never import the measuring code.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
