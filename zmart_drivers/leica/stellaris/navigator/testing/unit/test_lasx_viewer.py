"""The bleaching time in LAS X's Bleach Point panel, against a stand-in for its boxes.

Bleach points and ROI regions themselves are added through LAS X's data container (``test_datacontainer_changes.py``).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import pytest
from viewer_stand_in import Viewer

from zmart_drivers.leica.stellaris.navigator.procedures import lasx_viewer


@pytest.mark.parametrize("seconds, boxes", [
    (3723.5, {"TextDays": "0", "TextHours": "1", "TextMinutes": "2", "TextSeconds": "3", "TextMilliseconds": "500"}),
    (90061.007, {"TextDays": "1", "TextHours": "1", "TextMinutes": "1", "TextSeconds": "1", "TextMilliseconds": "7"}),
])
def test_the_bleaching_time_goes_into_the_panel_s_boxes_and_is_read_back(seconds, boxes):
    viewer = Viewer()

    result = lasx_viewer.set_bleach_time(seconds, viewer=viewer)

    assert viewer.boxes == boxes and result["seconds"] == pytest.approx(seconds)


@pytest.mark.parametrize("seconds", [0, -1, "2 s", True])
def test_a_bleaching_time_that_is_not_a_positive_number_is_refused(seconds):
    viewer = Viewer()

    with pytest.raises(ValueError):
        lasx_viewer.set_bleach_time(seconds, viewer=viewer)
    assert set(viewer.boxes.values()) == {"0"}
