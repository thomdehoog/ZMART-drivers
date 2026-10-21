"""Tests for the focus peak that is found between two slices of a z-stack.

The sharpest slice of a stack is rarely exactly on a slice. A parabola through
the sharpness of that slice and its two neighbours gives a better position, a
fraction of a slice. These tests check that the fraction points the right way
and gives the true peak back.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
        University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com)
"""

import numpy as np
import pytest

from zmart_drivers.leica.stellaris5_y42h93.navigator_expert.driver_setup.optical_calibration.focus import (
    subpixel_peak,
)


def parabola(true_peak, slices=7):
    """Sharpness scores of a stack whose true focus is at `true_peak` slices."""
    positions = np.arange(slices)
    return list(10.0 - (positions - true_peak) ** 2)


@pytest.mark.parametrize("true_peak", [1.1, 2.3, 2.7, 3.0, 4.4])
def test_the_peak_between_slices_is_found_where_it_really_is(true_peak):
    scores = parabola(true_peak)
    brightest = int(np.argmax(scores))

    assert subpixel_peak(scores, brightest) == pytest.approx(true_peak)


def test_a_peak_at_the_edge_of_the_stack_is_not_refined():
    # With only one neighbour there is no parabola to fit, so the slice itself is returned.
    assert subpixel_peak(parabola(0.0), 0) == 0.0
    assert subpixel_peak(parabola(6.0), 6) == 6.0


def test_a_flat_stack_returns_the_slice_it_was_given():
    assert subpixel_peak([5.0, 5.0, 5.0, 5.0], 1) == 1.0
