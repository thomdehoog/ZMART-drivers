"""The objective calibration: how far the sample shifts, in x, y and focus, between two objectives.

Each objective looks at a slightly different spot (parcentricity) and a
slightly different focal plane (parfocality). The calibration records, per
nosepiece slot, the stage and focus offset that keeps the same point of the
sample in view, relative to one reference objective. ``model.py`` loads and
applies it; ``calibrate_objective_pair.py`` measures it, with the operator
centring the same feature under each objective.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
