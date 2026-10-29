"""Reading a capture back from FLUOVIEW into one volume, and describing it for the OME files.

After a scan FLUOVIEW holds the picture in the ``.oir`` file it named when
the scan started. :func:`read_capture` opens that file through the RDK's
image-data calls (``primitives/rdk/image_data.py``) and reads every plane
into one array ordered t, c, z, y, x, together with what FLUOVIEW says
about it: the channel names and bit depth, the pixel size, and where each
slice of a z-stack was taken. :func:`metadata_of` turns that into the
``AcquisitionMetadata`` the OME-Zarr writer takes.

A spectral (lambda) series is folded into the channels: each wavelength
step of each detector becomes a channel of its own, named after both.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np

from ...primitives.rdk.image_data import ImageDescription, OpenedImage
from .product import (
    AcquisitionMetadata,
    Laser,
    ChannelMetadata,
    Detector,
    Instrument,
    Objective,
    PlaneIndex,
    PlaneMetadata,
)

log = logging.getLogger(__name__)

#: The bits an FV4000 detector fills unless the picture says otherwise.
DEFAULT_BIT_COUNT = 12


@dataclass
class CaptureData:
    """One capture read back: its pixels and what FLUOVIEW said about them."""

    path: str
    volume: np.ndarray  # t, c, z, y, x
    description: ImageDescription
    channel_names: list[str]
    channel_ids: list[str]
    #: The pixel size in micrometres, (x, y); None when FLUOVIEW did not say.
    pixel_size_um: tuple[float, float] | None
    #: Where each z slice was taken, in micrometres, in FLUOVIEW's absolute coordinates; None for a single plane.
    z_positions_um: list[float] | None
    #: The time of each time point from the first, in seconds; None when FLUOVIEW did not say.
    t_offsets_s: list[float] | None
    #: FLUOVIEW's own answers about the picture (every image property it was asked for), whole.
    fluoview_answers: tuple = ()

    @property
    def bit_count(self) -> int:
        counts = {c.bit_count for c in self.description.channels}
        return max(counts) if counts else DEFAULT_BIT_COUNT


def read_capture(client, path: str) -> CaptureData:
    """Open the picture FLUOVIEW wrote at *path*, read every plane, and answer the volume with its description."""
    with OpenedImage(client, path) as image:
        description = image.description
        size_t, size_z, size_l = (
            description.size("TIMELAPSE"),
            description.size("ZSTACK"),
            description.size("LAMBDA"),
        )
        channels = sorted(description.channels, key=lambda c: c.order)
        if not channels:
            raise RuntimeError(f"{path}: FLUOVIEW reports no channels in the picture")
        volume = np.empty(
            (
                size_t,
                len(channels) * size_l,
                size_z,
                description.height,
                description.width,
            ),
            dtype=description.dtype,
        )
        names, ids = [], []
        for ci, channel in enumerate(channels):
            for lam in range(size_l):
                names.append(
                    channel.name if size_l == 1 else f"{channel.name} lambda {lam}"
                )
                ids.append(channel.id)
                for t in range(size_t):
                    for z in range(size_z):
                        volume[t, ci * size_l + lam, z] = image.read_plane(
                            channel.id, t=t, z=z, lam=lam
                        )
        answers = list(image.answers)
    z_axis = description.axes.get("ZSTACK")
    z_positions = None
    if z_axis is not None and z_axis.size > 1 and z_axis.start is not None:
        # FLUOVIEW gives the first and last slice as absolute focus positions in micrometres.
        # The step alone does not say which way the stack went (the manual tells the direction
        # from start and end), so the slices are spread from start to end whenever both are known.
        if z_axis.end is not None:
            step = (z_axis.end - z_axis.start) / (z_axis.size - 1)
        elif z_axis.step is not None:
            step = z_axis.step
            log.warning(
                "%s: FLUOVIEW gave no end for the z-stack, so the slices are placed upwards from its start",
                path,
            )
        else:
            step = None
        if step is not None:
            z_positions = [z_axis.start + i * step for i in range(z_axis.size)]
    t_axis = description.axes.get("TIMELAPSE")
    t_offsets = None
    if t_axis is not None and t_axis.size > 1 and t_axis.step is not None:
        t_offsets = [i * t_axis.step for i in range(t_axis.size)]
    return CaptureData(
        path=path,
        volume=volume,
        description=description,
        channel_names=names,
        channel_ids=ids,
        pixel_size_um=description.pixel_length_um,
        z_positions_um=z_positions,
        t_offsets_s=t_offsets,
        fluoview_answers=tuple(answers),
    )


def metadata_of(
    capture: CaptureData,
    *,
    started_at: float | None = None,
    objective: dict | None = None,
    version: str | None = None,
    state: dict | None = None,
) -> AcquisitionMetadata:
    """The ``AcquisitionMetadata`` of *capture*: sizes, pixel size, channels, planes, and the instrument as far as it is known.

    The planes carry no position yet: the save places them in the frame the
    workflow drives in (``save.py``). *objective* is ``{"slot", "name"}`` as
    read when the picture was taken. *state* is the driver's state at the
    capture (``get_state``): from it each channel gets its laser, the laser's
    wavelength and power, and the detector's high voltage, gain and offset,
    the fields the OME model has for them. A channel the state does not name
    keeps only what the picture itself says.
    """
    size_t, size_c, size_z, size_y, size_x = capture.volume.shape
    pixel = capture.pixel_size_um
    z_step = None
    if capture.z_positions_um and len(capture.z_positions_um) > 1:
        z_step = abs(capture.z_positions_um[1] - capture.z_positions_um[0])
    settings = _channel_settings(state)
    channels = tuple(
        ChannelMetadata(
            index=index,
            name=name,
            detector_id=f"Detector:{capture.channel_ids[index]}",
            **settings["channels"].get(capture.channel_ids[index], {}),
        )
        for index, name in enumerate(capture.channel_names)
    )
    detectors = tuple(
        Detector(
            id=f"Detector:{channel.id}",
            model=channel.device_name or channel.name,
            type="PMT",
        )
        for channel in capture.description.channels
    )
    planes = {}
    for t in range(size_t):
        for c in range(size_c):
            for z in range(size_z):
                planes[PlaneIndex(t=t, z=z, c=c)] = PlaneMetadata(
                    delta_t_s=capture.t_offsets_s[t]
                    if capture.t_offsets_s
                    else (0.0 if size_t == 1 else None)
                )
    facts = [("fluoview file", capture.path), ("bit count", str(capture.bit_count))]
    if version:
        facts.append(("fluoview version", version))
    for name, axis in capture.description.axes.items():
        facts.append(
            (
                f"axis {name}",
                f"size {axis.size}, start {axis.start}, end {axis.end}, step {axis.step}",
            )
        )
    return AcquisitionMetadata(
        size_x=size_x,
        size_y=size_y,
        size_t=size_t,
        size_z=size_z,
        size_c=size_c,
        pixel_type=str(np.dtype(capture.volume.dtype).name),
        physical_size_x_um=pixel[0] if pixel else None,
        physical_size_y_um=pixel[1] if pixel else None,
        physical_size_z_um=z_step,
        channels=channels,
        significant_bits=capture.bit_count,
        acquisition_date=datetime.fromtimestamp(started_at, tz=UTC).isoformat()
        if started_at
        else None,
        instrument=Instrument(
            manufacturer="Evident",
            model="FLUOVIEW FV4000",
            objective=Objective(
                model=str(objective.get("name") or f"slot {objective.get('slot')}")
            )
            if objective
            else None,
            detectors=detectors,
            lasers=settings["lasers"],
        ),
        planes=planes,
        annotations=tuple(facts),
    )


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _channel_settings(state: dict | None) -> dict:
    """Per channel id, the OME fields the driver's state at the capture can fill, and the lasers they point to.

    The laser power FLUOVIEW reports is a percentage; OME's attenuation is how
    much of the light is held back, so 2 % power is an attenuation of 0.98.
    """
    changeable = (state or {}).get("changeable") or {}
    observed = (state or {}).get("observed") or {}
    lines = {
        str(line.get("id")): line
        for line in observed.get("laser_lines") or []
        if isinstance(line, dict)
    }
    powers = changeable.get("lasers") or {}
    channels, used = {}, {}
    for channel_id, fields in (changeable.get("channels") or {}).items():
        if not isinstance(fields, dict):
            continue
        entry = {
            "detector_voltage_v": _number(fields.get("hv")),
            "detector_gain": _number(fields.get("gain")),
            "detector_offset": _number(fields.get("offset")),
        }
        lasers = [str(laser) for laser in fields.get("lasers") or []]
        if lasers:
            laser = lasers[0]
            line = lines.get(laser, {})
            wavelength = _number(line.get("wavelength_nm"))
            power = _number((powers.get(laser) or {}).get("intensity"))
            entry.update(
                laser_id=f"LightSource:{laser}",
                excitation_wavelength_nm=wavelength,
                laser_attenuation=None if power is None else 1 - power / 100,
            )
            used[laser] = Laser(
                id=f"LightSource:{laser}",
                model=str(line.get("type") or laser),
                wavelength_nm=wavelength,
            )
        channels[str(channel_id)] = {k: v for k, v in entry.items() if v is not None}
    return {"channels": channels, "lasers": tuple(used.values())}
