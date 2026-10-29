"""How a capture's files are named, and a short session name.

ZEN names the file of an acquisition after the ``output_name`` it is given: a
plain file name without an extension. The driver builds that name from the
acquisition type and the position label the workflow gave (``overview_A1``),
made safe for a file name, and free: a name already used in the folder saved
to, or in ZEN's own image folder, gets ``_001``, ``_002`` and so on, so no
capture ever overwrites another.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass
from pathlib import Path

#: What a name may keep: letters, digits, dot, dash and underscore. Everything else becomes an underscore.
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: The file ZEN writes, and the one the driver writes beside it.
CZI = ".czi"
SIDECAR = ".zmart.json"


def safe_stem(text: str) -> str:
    """*text* as a file name without an extension: unsafe characters replaced, never empty."""
    stem = _UNSAFE.sub("_", str(text).strip()).strip("._-")
    return stem or "capture"


@dataclass(frozen=True)
class Naming:
    """The stem every file of one capture is named after."""

    stem: str

    def czi(self, folder: Path) -> Path:
        return Path(folder) / f"{self.stem}{CZI}"

    def sidecar(self, folder: Path) -> Path:
        return Path(folder) / f"{self.stem}{SIDECAR}"


def free_stem(stem: str, *folders: Path | str | None) -> str:
    """*stem*, or ``<stem>_001`` and so on, so that no file of that name exists in any of *folders*."""
    stem = safe_stem(stem)
    places = [Path(folder) for folder in folders if folder]

    def taken(candidate: str) -> bool:
        return any(
            (place / f"{candidate}{CZI}").exists()
            or (place / f"{candidate}{SIDECAR}").exists()
            for place in places
        )

    if not taken(stem):
        return stem
    number = 1
    while taken(f"{stem}_{number:03d}"):
        number += 1
    return f"{stem}_{number:03d}"


def run_hash() -> str:
    """Six random hexadecimal characters that name one session, in file names and records."""
    return secrets.token_hex(3)
