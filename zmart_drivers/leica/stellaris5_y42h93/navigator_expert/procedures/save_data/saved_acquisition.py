"""The manifest a save hands back: which files were written for one capture.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...vendor_specific.primitives.capture.product import PlaneIndex, PositionIndex
from .naming import Naming


@dataclass(frozen=True)
class SavedAcquisition:
    """Manifest for one persisted acquisition product.

    The flat, no-sidecar layout embeds state per-plane, so ``xml_paths`` is
    now empty by default; the field is retained for back-compat with callers
    that still inspect legacy companion-XML manifests.
    """

    image_paths: dict[PlaneIndex, Path]
    naming: Naming
    xml_paths: dict[PositionIndex, Path] = field(default_factory=dict)
    vendor_metadata_paths: tuple[Path, ...] = ()
    state_paths: tuple[Path, ...] = ()
    #: The projections made from the capture, in ``<acquisition>/projections``.
    projection_paths: tuple[Path, ...] = ()
    #: Reads one saved plane back as a 2-D array, by the plane number it had
    #: when it was captured. The saved store holds its planes in another order
    #: (lowest first), so the number the capture gave is the reliable key.
    read_plane: Callable[[PlaneIndex], Any] | None = None
    #: The picture in LAS X's cache this capture was read from (its element
    #: number and name), so the ``clear_cache`` procedure can let go of it.
    release_target: tuple[int, str] | None = None
