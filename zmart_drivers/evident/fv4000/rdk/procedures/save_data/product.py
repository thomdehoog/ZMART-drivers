"""What a capture produces: the capture itself, the metadata the files are written from, and what was saved.

These are plain records (Python dataclasses: small classes that only hold
named values). ``AcquisitionResult`` is what taking a picture answers;
``AcquisitionMetadata`` and its parts are what the OME files are written from;
``SavedCapture`` is what a save answers.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
After the Leica driver's product records.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AcquisitionResult:
    """One capture FLUOVIEW took: the file it wrote, when, and how the command went."""

    target: str
    execution_type: str
    started_at: float
    finished_at: float
    command_result: dict
    progress: dict | None = None


@dataclass(frozen=True, order=True)
class PlaneIndex:
    """One plane of a capture: its time point, depth and channel, counted from 0."""

    t: int
    z: int
    c: int


@dataclass(frozen=True)
class Objective:
    """The objective a picture was taken with, in OME's terms."""

    model: str
    magnification: float | None = None
    lens_na: float | None = None
    immersion: str | None = None
    correction: str | None = None


@dataclass(frozen=True)
class Detector:
    """One detector; ``type`` is an OME DetectorType (``PMT``, ``Other``...)."""

    id: str
    model: str
    type: str = "Other"


@dataclass(frozen=True)
class Laser:
    """One laser line."""

    id: str
    model: str
    wavelength_nm: float | None = None


@dataclass(frozen=True)
class Instrument:
    """The microscope, as far as it took part in one capture."""

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    type: str | None = None
    objective: Objective | None = None
    detectors: tuple[Detector, ...] = ()
    lasers: tuple[Laser, ...] = ()


@dataclass(frozen=True)
class ChannelMetadata:
    """What one channel is, and how its light was made and caught; the ``*_id`` fields point into the Instrument."""

    index: int
    name: str | None = None
    color: int | None = None
    excitation_wavelength_nm: float | None = None
    detector_id: str | None = None
    detector_gain: float | None = None
    detector_offset: float | None = None
    #: A PMT's high voltage, in volts (FLUOVIEW's HV).
    detector_voltage_v: float | None = None
    laser_id: str | None = None
    laser_attenuation: float | None = None


@dataclass(frozen=True)
class PlaneMetadata:
    """When and where one plane was taken: positions in micrometres, the focus from every z drive together.

    ``actuators`` names where each drive stood for this plane, as
    ``(name, micrometres)`` pairs, so the height can always be taken apart again.
    """

    delta_t_s: float | None = None
    position_x_um: float | None = None
    position_y_um: float | None = None
    position_z_um: float | None = None
    actuators: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class AcquisitionMetadata:
    """Everything the OME files need to describe one capture."""

    size_x: int
    size_y: int
    size_t: int
    size_z: int
    size_c: int
    pixel_type: str
    physical_size_x_um: float | None = None
    physical_size_y_um: float | None = None
    physical_size_z_um: float | None = None
    channels: tuple[ChannelMetadata, ...] = ()
    #: The bits the detector filled (12 on an FV4000 by default); a 12-bit
    #: picture is kept in 16-bit pixels, and this says that only 12 are used.
    significant_bits: int | None = None
    acquisition_date: str | None = None
    instrument: Instrument | None = None
    planes: dict[PlaneIndex, PlaneMetadata] = field(default_factory=dict)
    #: The vendor's facts OME has no field for, as (name, value) pairs.
    annotations: tuple[tuple[str, str], ...] = ()

    def channel(self, index: int) -> ChannelMetadata:
        for channel in self.channels:
            if channel.index == index:
                return channel
        return ChannelMetadata(index=index)


@dataclass(frozen=True)
class SavedCapture:
    """What a save wrote: the store, its projection, and where every plane lies on the sample."""

    store: Path
    planes: list[dict]
    projection: Path | None = None
    vendor_copy: Path | None = None
    #: Reads one saved plane back as a 2-D array, by (t, c, z) as FLUOVIEW numbered them.
    read_plane: Callable[[tuple[int, int, int]], Any] | None = None
