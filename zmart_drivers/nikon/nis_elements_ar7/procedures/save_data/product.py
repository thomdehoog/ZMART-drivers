"""What a saved picture says about itself: its size, its channels, its planes, and the microscope that took it.

These small records are filled from the staged TIFF and NIS's answer to the
capture (``tiff.py``, ``save.py``), and written into the store's OME-XML
(``ome_xml.py``) in the OME data model's own terms.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Adapted from the Leica driver's ``procedures/save_data/product.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, order=True)
class PlaneIndex:
    """Which plane: its time point, its depth and its channel, each counted from 0."""

    t: int
    z: int
    c: int


@dataclass(frozen=True)
class ChannelMetadata:
    """One channel: its number, its name and its colour (OME's signed 32-bit RGBA), as far as known."""

    index: int
    name: str | None = None
    color: int | None = None


@dataclass(frozen=True)
class PlaneMetadata:
    """Where one plane was taken, in the experiment's coordinates, and each motor's own reading then.

    ``actuators`` are ``(name, micrometres)`` pairs, named as ``get_xyz``
    names them: ``x/motoric``, ``y/motoric``, ``z/motoric``.
    """

    position_x_um: float | None = None
    position_y_um: float | None = None
    position_z_um: float | None = None
    actuators: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class Instrument:
    """The microscope, as far as NIS tells: its maker, its software, and the objective in place."""

    manufacturer: str = "Nikon"
    model: str | None = None
    objective: str | None = None


@dataclass(frozen=True)
class AcquisitionMetadata:
    """Everything the OME-XML says about one capture besides ZMART's own notes."""

    size_x: int
    size_y: int
    size_c: int
    pixel_type: str
    physical_size_x_um: float
    physical_size_y_um: float
    physical_size_z_um: float | None = None
    size_z: int = 1
    size_t: int = 1
    significant_bits: int | None = None
    acquisition_date: str | None = None
    channels: tuple[ChannelMetadata, ...] = ()
    instrument: Instrument | None = None
    planes: dict[PlaneIndex, PlaneMetadata] = field(default_factory=dict)
    #: NIS's facts OME has no field for, as (name, value) pairs.
    annotations: tuple[tuple[str, str], ...] = ()
