"""What the operator sets up once per microscope: limits, origin, orientation, data location; and the bridge's macro.

Each item has its own folder, with its defaults, its loader, the code that
measures or saves it, and a notebook that only calls that code. The saved
values live in the computer's configuration folder (``store.py``), never in
the repository.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
