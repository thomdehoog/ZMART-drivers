"""How a capture is saved: with the measured orientation, and with which projection.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageSaveProfile:
    """Default image persistence policy.

    Normal saves apply the active camera-to-stage orientation. The orientation
    measurement is the only workflow that explicitly requests raw pixels.

    The pixels are collected from LAS X's own cache, read as the picture is
    taken; LAS X's AutoSave is not used, so it stays off (Thom, 2026-10-14).
    """

    apply_orientation: bool = True
    #: What a capture with more than one plane also gets, in
    #: ``<acquisition>/projections`` (``output.save.PROJECTIONS``): its
    #: ``"max"`` projection over z, or ``"none"``.
    projection: str = "max"


IMAGE_SAVE = ImageSaveProfile()
