"""Reading the pixels of a saved picture back from FLUOVIEW: the RDK's IDA calls.

FLUOVIEW writes every picture it takes to its own folder, as an ``.oir`` file
(or an ``.omp2info`` for a multi-area time lapse). The driver does not read
those files itself: it may run on another computer, and the format is
Evident's. Instead it asks FLUOVIEW for the pixels, through the "IDA" group
of calls (the manual's section 3.2.10 and 5.1), which works wherever the
driver runs.

A picture is opened in layers (the manual's section 3.4): a file holds
*groups* (one, for an ``.oir``), a group holds *levels* of resolution (one,
today), a level holds *areas* (one for a plain picture), and an area holds
the channels and the axes: ``ZSTACK``, ``TIMELAPSE`` and ``LAMBDA``. Each
plane is fetched by channel and by its index on each axis, as 16-bit
pixels in pieces (``getImageBody`` and ``getNextImageBody``).

Every id FLUOVIEW hands out is released again, in the reverse order, as the
manual asks (:class:`OpenedImage` does that on the way out).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .parsing import RdkError, as_bytes, require_ok

log = logging.getLogger(__name__)

#: The three axes FLUOVIEW can lay out a picture along, and the words the IDA calls use for them.
AXES = ("TIMELAPSE", "ZSTACK", "LAMBDA")


def _ida(client, call: str, **payload) -> dict:
    return require_ok(f"IDA.{call}", client.call(f"IDA.{call}", payload or None))


@dataclass
class ChannelInfo:
    """What FLUOVIEW says about one channel of a picture."""

    id: str
    name: str
    bit_count: int
    depth_bytes: int
    color_space: str
    order: int
    device_name: str = ""


@dataclass
class AxisInfo:
    """One axis of a picture: how many steps it has, and where they start, end and step (in µm for ZSTACK, s for TIMELAPSE)."""

    name: str
    size: int
    start: float | None = None
    end: float | None = None
    step: float | None = None


@dataclass
class ImageDescription:
    """Everything the IDA calls say about a picture, read once when it is opened."""

    path: str
    width: int
    height: int
    channels: list[ChannelInfo]
    axes: dict[str, AxisInfo] = field(default_factory=dict)
    pixel_length_um: tuple[float, float] | None = None

    @property
    def dtype(self) -> np.dtype:
        """The pixel type the planes are read as: 16-bit, or 8-bit when every channel is."""
        if self.channels and all(c.depth_bytes == 1 for c in self.channels):
            return np.dtype("<u1")
        return np.dtype("<u2")

    def size(self, axis: str) -> int:
        """How many steps the picture has along *axis* (1 when it has no such axis)."""
        found = self.axes.get(axis)
        return found.size if found is not None else 1


class OpenedImage:
    """A picture opened through the IDA calls; use it as ``with OpenedImage(client, path) as image:``.

    Opening reads the description (:attr:`description`); :meth:`read_plane`
    fetches one plane; leaving the ``with`` block releases every id, in the
    reverse order they were taken.
    """

    def __init__(
        self, client, path: str, *, group: int = 0, level: int = 0, area: int = 0
    ):
        self.client = client
        self.path = str(path)
        self._group_index, self._level, self._area_index = group, level, area
        self.file_id = self.group_id = self.area_id = None
        self.description: ImageDescription | None = None

    def __enter__(self) -> OpenedImage:
        client = self.client
        try:
            self.file_id = _ida(client, "open", filename=self.path)["fileId"]
            self.group_id = _ida(
                client, "getGroup", fileId=self.file_id, group=self._group_index
            )["groupId"]
            self.area_id = _ida(
                client,
                "getArea",
                groupId=self.group_id,
                level=self._level,
                area=self._area_index,
            )["areaId"]
            self.description = self._describe()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Release the area, the group and the file, in that order; each at most once."""
        client = self.client
        for call, key, attr in (
            ("releaseArea", "areaId", "area_id"),
            ("releaseGroup", "groupId", "group_id"),
            ("close", "fileId", "file_id"),
        ):
            value = getattr(self, attr)
            if value is None:
                continue
            setattr(self, attr, None)
            try:
                _ida(client, call, **{key: value})
            except Exception as why:  # noqa: BLE001 -- releasing is best effort; the picture is read already
                log.warning("IDA.%s for %s failed: %s", call, self.path, why)

    # --- the description ----------------------------------------------------------------

    def property(self, key: str, **additional):
        """One property of the area (``getProperty``): its ``value``."""
        payload = {"areaId": self.area_id, "key": key}
        if additional:
            payload["additional"] = additional
        return _ida(self.client, "getProperty", **payload).get("value")

    def _describe(self) -> ImageDescription:
        channel_ids = [str(c) for c in _as_list(self.property("EnabledChannelIdList"))]
        channels = []
        for number, channel_id in enumerate(channel_ids):
            try:
                info = _first(self.property("ChannelInfo", channelId=channel_id)) or {}
            except RdkError as why:
                # "ChannelInfo cannot be used for the image being acquired" (the manual).
                log.debug("ChannelInfo for %s: %s", channel_id, why)
                info = {}
            channels.append(
                ChannelInfo(
                    id=channel_id,
                    name=str(info.get("name") or channel_id),
                    bit_count=int(info.get("bitCount") or 16),
                    depth_bytes=int(info.get("depth") or 2),
                    color_space=str(info.get("colorSpace") or "GRAYSCALE"),
                    order=int(
                        info.get("order") if info.get("order") is not None else number
                    ),
                    device_name=str(info.get("deviceName") or ""),
                )
            )
        axes = {}
        for name in _as_list(self.property("Axes")):
            name = str(name)
            if name not in AXES:
                continue
            axes[name] = AxisInfo(
                name=name,
                size=int(_first(self.property("AxisMaxSize", axisName=name)) or 1),
                start=_number(
                    _first(self.property("AxisStartPosition", axisName=name))
                ),
                end=_number(_first(self.property("AxisEndPosition", axisName=name))),
                step=_number(_first(self.property("AxisStep", axisName=name))),
            )
        pixel_length = None
        try:
            lengths = _as_list(self.property("PixelLength"))
            if len(lengths) >= 2:
                pixel_length = (float(lengths[0]), float(lengths[1]))
        except (RdkError, TypeError, ValueError) as why:
            log.debug("PixelLength: %s", why)
        width, height = self._size_of_first_plane(channel_ids, axes)
        return ImageDescription(
            path=self.path,
            width=width,
            height=height,
            channels=channels,
            axes=axes,
            pixel_length_um=pixel_length,
        )

    def _size_of_first_plane(self, channel_ids, axes) -> tuple[int, int]:
        if not channel_ids:
            return 0, 0
        image_id = self._image_id(channel_ids[0], {name: 0 for name in axes})
        try:
            answer = _ida(self.client, "getImageSize", imageId=image_id)
            return int(answer["width"]), int(answer["height"])
        finally:
            self._release_image(image_id)

    # --- the planes -----------------------------------------------------------------------

    def _image_id(self, channel_id: str, indices: dict[str, int]) -> str:
        axis_info = (
            [
                {"axisName": name, "index": int(indices.get(name, 0))}
                for name in self.description.axes
            ]
            if self.description
            else [
                {"axisName": name, "index": int(index)}
                for name, index in indices.items()
            ]
        )
        if not axis_info:
            # A plain XY picture is still asked for by an axis (the manual's sample: TIMELAPSE, index 0).
            axis_info = [{"axisName": "TIMELAPSE", "index": 0}]
        answer = _ida(
            self.client,
            "getImage",
            areaId=self.area_id,
            channelId=channel_id,
            axisInfo=axis_info,
        )
        image_id = answer["imageId"]
        return str(image_id[0]) if isinstance(image_id, list) else str(image_id)

    def _release_image(self, image_id: str) -> None:
        try:
            _ida(self.client, "releaseImage", imageId=image_id)
        except Exception as why:  # noqa: BLE001
            log.warning("IDA.releaseImage failed: %s", why)

    def read_plane(
        self, channel_id: str, *, t: int = 0, z: int = 0, lam: int = 0
    ) -> np.ndarray:
        """The pixels of one plane as a 2-D array (rows, columns), fetched in pieces until FLUOVIEW says it is whole."""
        description = self.description
        image_id = self._image_id(
            channel_id, {"TIMELAPSE": t, "ZSTACK": z, "LAMBDA": lam}
        )
        try:
            answer = _ida(
                self.client,
                "getImageBody",
                imageId=image_id,
                compressType="NONE",
                rect={
                    "x": 0,
                    "y": 0,
                    "width": description.width,
                    "height": description.height,
                },
            )
            pieces = [as_bytes(answer["data"])]
            while answer.get("continue"):
                answer = _ida(self.client, "getNextImageBody", imageId=image_id)
                pieces.append(as_bytes(answer["data"]))
        finally:
            self._release_image(image_id)
        raw = b"".join(pieces)
        expected = description.width * description.height * description.dtype.itemsize
        if len(raw) != expected:
            raise RuntimeError(
                f"{self.path}: FLUOVIEW sent {len(raw)} bytes for a plane of {expected} "
                f"({description.width} x {description.height}, {description.dtype.itemsize} bytes a pixel)"
            )
        plane = np.frombuffer(raw, dtype=description.dtype).reshape(
            description.height, description.width
        )
        return plane.astype(description.dtype.newbyteorder("="), copy=False)


def can_open(client, path: str) -> bool:
    """Whether FLUOVIEW can open the picture at *path* now: a finished capture can, one not yet written cannot."""
    try:
        file_id = _ida(client, "open", filename=str(path))["fileId"]
    except Exception:  # noqa: BLE001 -- any refusal means not yet
        return False
    try:
        _ida(client, "close", fileId=file_id)
    except Exception as why:  # noqa: BLE001
        log.warning("IDA.close for %s failed: %s", path, why)
    return True


def _as_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], list):
            return value[0]
        return value
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def _first(value):
    """The first element of a list answer (FLUOVIEW wraps most property values in an array)."""
    while isinstance(value, list):
        if not value:
            return None
        value = value[0]
    return value


def _number(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
