"""What a capture is before and after it is saved: the records the procedures hand around.

:class:`AcquisitionResult` is what ``changes.take_picture`` answers: which
experiment ran, under which id, what ZEN called the file, and when.
:class:`SavedAcquisition` is what ``save_data`` answers: the OME-Zarr store
it wrote, its projection for a z-stack, and the planes the store holds, in
the controller's words.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AcquisitionResult:
    """One acquisition ZEN ran: the experiment, what ZEN named its file, and when it ran."""

    experiment: str
    experiment_id: str
    output_name: str
    snap: bool
    started_at: float
    finished_at: float
    command_result: dict


@dataclass(frozen=True)
class SavedAcquisition:
    """One capture saved: its store (and projection), its planes, and its record."""

    position_label: str
    acquisition_type: str
    files: list[str]
    planes: list[dict]
    store: Path
    zen_czi: Path | None
    projection: Path | None = None
    record: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The answer in the controller's words (``position_label``, ``acquisition_type``, ``files``, ``planes``), and where the store is.

        ``zen_czi`` is ZEN's own file, still in ZEN's folder; it is not one of
        the capture's files, and is None once deleted.
        """
        return {
            "position_label": self.position_label,
            "acquisition_type": self.acquisition_type,
            "format": "ome-zarr",
            "files": list(self.files),
            "planes": [dict(plane) for plane in self.planes],
            "store": str(self.store),
            "projection": str(self.projection) if self.projection else None,
            "zen_czi": str(self.zen_czi) if self.zen_czi else None,
        }
