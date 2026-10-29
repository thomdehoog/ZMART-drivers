"""What a capture produces: the acquisition product, and the saved acquisition.

``Exported*`` types are collector -> save inputs and are writer-agnostic.
The manifest of what was saved, ``SavedAcquisition``, is in
this file.

The manifest a save hands back: which files were written for one capture.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .naming import Naming


@dataclass(frozen=True)
class AcquisitionResult:
    """What one picture-taking answered: which kind of picture, when it ran, and the change's result.

    ``picture`` is ``"capture"`` (the window's Capture Image, which LAS X
    names ``Image<n>``) or ``"tile_scan"`` (its Preview over positions,
    ``Mosaic<n>``); the collector finds the picture by that name and by
    when the scan began.
    """

    picture: str
    started_at: float
    finished_at: float
    command_result: dict


@dataclass(frozen=True, order=True)
class PlaneIndex:
    """Canonical flat image plane index."""

    t: int
    z: int
    c: int


@dataclass(frozen=True, order=True)
class PositionIndex:
    """Canonical companion-XML position index."""

    t: int
    v: int = 0


@dataclass(frozen=True)
class PlaneSource:
    """Source for one canonical image plane.

    ``page_index=None`` means the source path is already a single-plane
    image file and can be copied as a whole. An integer page index means
    the plane must be materialized from that page in a multipage TIFF.
    """

    path: Path
    page_index: int | None = None


@dataclass(frozen=True)
class RawPlaneSource:
    """One image plane held as raw, uncompressed pixels inside a larger file.

    LAS X's Data Container cache keeps a picture's pixels with no header: the
    plane is ``shape_yx`` pixels of ``dtype`` (little-endian), its rows packed
    one after the other, starting ``offset`` bytes into ``path``.
    """

    path: Path
    offset: int
    shape_yx: tuple[int, int]
    dtype: str


@dataclass(frozen=True)
class Objective:
    """The objective a picture was taken with, in OME's terms.

    ``immersion`` and ``correction`` are OME 2016-06 enumeration values
    (``Air``, ``Oil``...; ``PlanApo``...), or None when the vendor's own
    words do not say which.
    """

    model: str
    magnification: float | None = None
    lens_na: float | None = None
    immersion: str | None = None
    correction: str | None = None
    refractive_index: float | None = None


@dataclass(frozen=True)
class Detector:
    """One detector; ``type`` is an OME DetectorType (``PMT``, ``Other``...)."""

    id: str
    model: str
    type: str = "Other"


@dataclass(frozen=True)
class Laser:
    """One laser; a white-light laser is ``tuneable`` and has no single wavelength."""

    id: str
    model: str
    wavelength_nm: float | None = None
    tuneable: bool = False


@dataclass(frozen=True)
class Filter:
    """A filter, or a spectral detection band, as the range of light it passes."""

    id: str
    model: str
    type: str | None = None
    cut_in_nm: float | None = None
    cut_out_nm: float | None = None


@dataclass(frozen=True)
class Instrument:
    """The microscope, as far as it took part in one acquisition."""

    manufacturer: str | None = None
    model: str | None = None
    serial_number: str | None = None
    #: An OME MicroscopeType: ``Upright`` or ``Inverted``.
    type: str | None = None
    objective: Objective | None = None
    detectors: tuple[Detector, ...] = ()
    lasers: tuple[Laser, ...] = ()
    filters: tuple[Filter, ...] = ()


@dataclass(frozen=True)
class ChannelMetadata:
    """What one channel is, and how its light was made and caught.

    The ``*_id`` fields point into the acquisition's :class:`Instrument`.
    ``laser_attenuation`` is OME's fraction of the light taken away (the
    vendor's intensity of 2 % is an attenuation of 0.98).
    """

    index: int
    name: str | None = None
    color: int | None = None
    wavelength_nm: float | None = None
    fluor: str | None = None
    excitation_wavelength_nm: float | None = None
    pinhole_um: float | None = None
    acquisition_mode: str | None = None
    detector_id: str | None = None
    detector_gain: float | None = None
    detector_offset: float | None = None
    laser_id: str | None = None
    laser_attenuation: float | None = None
    emission_filter_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaneMetadata:
    """When and where one plane was taken.

    Positions are the microscope's own stage and focus readings in
    micrometres, as its software wrote them beside the picture; ZMART's
    sample frame is in the acquisition's answer and its state. ``x`` and
    ``y`` are the stage's; ``z`` is the focus height, every z drive
    together. ``actuators`` names where each drive stood for this plane,
    as ``(name, micrometres)`` pairs, so the height can always be taken
    apart again.
    """

    delta_t_s: float | None = None
    position_x_um: float | None = None
    position_y_um: float | None = None
    position_z_um: float | None = None
    actuators: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class AcquisitionMetadata:
    """Common denominator needed to write valid, reusable ZMART OME."""

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
    #: The bits the detector filled (8, 12 or 16 on a STELLARIS); a 12-bit
    #: picture is kept in 16-bit pixels, and this says that only 12 are used.
    significant_bits: int | None = None
    #: When the first plane was taken, as an ISO 8601 time in UTC.
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
class VendorMetadataSource:
    """Raw vendor metadata preserved as provenance, not output truth."""

    name: str
    path: Path | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class ExportedPosition:
    """One exported timepoint: canonical plane sources."""

    t: int
    planes: dict[PlaneIndex, PlaneSource | RawPlaneSource]


@dataclass(frozen=True)
class ExportedAcquisition:
    """Stable source product produced by an exporter."""

    source_root: Path
    source_dir: Path
    positions: list[ExportedPosition]
    metadata: AcquisitionMetadata
    method: str
    relative_path: str | None = None
    source_exporter: str = "lasx_data_container"
    vendor_metadata_sources: tuple[VendorMetadataSource, ...] = ()
    #: Which capture LAS X may let go of once it is saved (``clear_cache``): its
    #: element number and its name. None when there is none, and then letting go
    #: is refused. How it is done is the business of ``procedures/clear_cache/clear.py``.
    release_target: tuple[int, str] | None = None

    @property
    def image_files(self) -> list[Path]:
        return list(
            dict.fromkeys(
                src.path
                for pos in self.positions
                for _idx, src in sorted(pos.planes.items())
            )
        )

    @property
    def metadata_files(self) -> list[Path]:
        return list(
            dict.fromkeys(
                src.path for src in self.vendor_metadata_sources if src.path is not None
            )
        )

    @property
    def source_files(self) -> list[Path]:
        return list(dict.fromkeys([*self.image_files, *self.metadata_files]))


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
