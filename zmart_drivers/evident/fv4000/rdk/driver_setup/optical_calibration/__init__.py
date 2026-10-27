"""The objective calibration: how far the sample shifts, in x, y and focus, from one objective to the next.

Each objective looks at a slightly different spot and focuses at a slightly
different height (what opticians call parcentricity and parfocality). The
calibration records, per nosepiece slot, the stage translation that keeps the
same point of the sample in view after a change of objective. It is measured
once per pair of objectives (``calibrate_objective_pair.py``) and loaded at
every connect (``model.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
