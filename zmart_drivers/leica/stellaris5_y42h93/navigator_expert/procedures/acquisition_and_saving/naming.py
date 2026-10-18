"""How a capture's files and folders are named.

A capture is named by its stem, then the time point, channel and plane. This
file also says where in the output folder the data, the metadata, the state
and the projections go.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path

EPOCH = 1767225600  # 2026-01-01 00:00:00 UTC
_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"
_UNSAFE_STEM_RE = re.compile(r"[^A-Za-z0-9_-]")
#: The longest stem a capture is named by. The LAS X computers keep Windows'
#: old limit of 260 characters on a whole path, so a name must leave room for
#: the folders it is saved in and for ``_T000000_C00_Z00000.ome.tiff``.
STEM_LIMIT = 80
_IMAGE_RE = re.compile(
    r"^(?P<stem>[A-Za-z0-9_-]+)_T(?P<t>\d{6})_C(?P<c>\d{2})_Z(?P<z>\d{5})\.ome\.tiff$"
)


def run_hash(start_time: float | None = None) -> str:
    """Return six sortable base36 characters for a timestamp."""

    value = int((time.time() if start_time is None else start_time) - EPOCH)
    if value < 0:
        raise ValueError(f"start_time is before convention epoch {EPOCH}")
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, 36)
        digits.append(_ALPHABET[remainder])
    return ("".join(reversed(digits)) or "0").rjust(6, "0")


@dataclass(frozen=True)
class Naming:
    """The name of one capture's files: the workflow's stem, then which plane.

    ``stem`` is what the workflow called the capture: the acquisition type
    and the position label ``acquire`` was given, for example
    ``overview_K00_M000000_G000000_P000001_V00``. The driver adds only
    the time point, channel and plane (``t``, ``c``, ``z``), so a workflow
    names its files the same way on every microscope. Characters that cannot
    be in a file name become ``_``.
    """

    stem: str
    t: int = 0
    c: int = 0
    z: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.stem, str) or not self.stem:
            raise ValueError("the stem must be a non-empty text")
        if len(self.stem) > STEM_LIMIT:
            raise ValueError(
                f"the stem is longer than {STEM_LIMIT} characters: {self.stem!r}"
            )
        for field, value, maximum in (
            ("t", self.t, 999_999),
            ("c", self.c, 99),
            ("z", self.z, 99_999),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= maximum
            ):
                raise ValueError(
                    f"{field} must be a whole number from 0 through {maximum}"
                )
        object.__setattr__(self, "stem", _UNSAFE_STEM_RE.sub("_", self.stem))


def build_image_name(naming: Naming) -> str:
    """Return the canonical Leica filename for one T/C/Z plane."""

    return f"{naming.stem}_T{naming.t:06d}_C{naming.c:02d}_Z{naming.z:05d}.ome.tiff"


def parse_image_name(filename: str) -> Naming | None:
    """Parse a canonical Leica filename, or return ``None``."""

    match = _IMAGE_RE.fullmatch(filename)
    if match is None:
        return None
    return Naming(
        stem=match.group("stem"),
        t=int(match.group("t")),
        c=int(match.group("c")),
        z=int(match.group("z")),
    )


def free_stem(output_root: Path | str, stem: str) -> str:
    """``stem``, or ``stem_001``, ``stem_002``, ...: the first no earlier capture here used.

    A capture never overwrites another: a second one under the same name, say
    a target taken again, gets the next number instead.
    """

    taken = data_dir(output_root)
    candidate, number = stem, 0
    while (taken / build_image_name(Naming(stem=candidate))).exists() or (
        taken / f"{Naming(stem=candidate).stem}.ome.zarr"
    ).exists():
        number += 1
        candidate = f"{stem}_{number:03d}"
    return candidate


def data_dir(output_root: Path | str) -> Path:
    """Return where the images of a capture go: ``data``.

    The pixels the microscope captured get a folder of their own, which
    leaves room beside it for what is made from them afterwards -- a
    projection, an analysis -- and for the vendor's own copy, without any of
    those having to be told apart from an image by its name.
    """

    return Path(output_root) / "data"


def projections_dir(output_root: Path | str) -> Path:
    """``projections``: what is projected from the captures, beside ``data``."""

    return Path(output_root) / "projections"


def log_dir(output_root: Path | str) -> Path:
    """``log``: what the driver was asked for each capture, and what it did."""

    return Path(output_root) / "log"


def build_projection_name(naming: Naming, method: str) -> str:
    """The projection of one T/C of a capture, saved as OME-TIFF: the image name with the method for Z."""

    return f"{naming.stem}_T{naming.t:06d}_C{naming.c:02d}_{method}.ome.tiff"


def metadata_dir(output_root: Path | str) -> Path:
    """Return where a capture's printed metadata goes: ``data/metadata``."""

    return data_dir(output_root) / "metadata"


def state_dir(output_root: Path | str) -> Path:
    """Return where ZMART's own account of a capture goes.

    ``data/metadata/ZMART_state``, beside ``metadata/vendor`` -- one folder
    per party, so whose account a file is never has to be read off its name.
    """

    return metadata_dir(output_root) / "ZMART_state"


def build_state_name(naming: Naming) -> str:
    """Return the filename for one capture's printed state.

    The image name without the channel and the z-slice, because one state
    describes every plane of the capture it was taken for: those two are what
    a single capture spans, while a new position or timepoint is a new
    capture with a state of its own.
    """

    return f"{naming.stem}_T{naming.t:06d}_ZMART_state.json"
