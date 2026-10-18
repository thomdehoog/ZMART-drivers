"""Changing the objective or the job without losing the sample.

Changing the objective moves the picture: each lens looks at a slightly
different spot and focus. Before a change, these recipes record where the
stage is. After it, they move the stage by the measured difference between the
two lenses, so the same sample point stays in view. A job change can carry an
objective change with it, so it is handled here too. The measured offsets come
from the objective calibration saved by ``driver_setup/optical_calibration``,
and the coordinates they relate to are explained in
``procedures/absolute_coordinate_system``.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""
