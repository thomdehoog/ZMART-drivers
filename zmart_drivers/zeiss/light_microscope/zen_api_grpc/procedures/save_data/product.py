"""What a capture is before and after it is saved: the records the procedures hand around.

:class:`AcquisitionResult` is what ``changes.take_picture`` answers: which
experiment ran, under which id, what ZEN called the file, and when.
:class:`SavedAcquisition` is what ``save_data`` answers: the files it wrote
and the planes they hold, in the controller's words.

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
    """One capture saved: its files, its planes, and the record written beside it."""

    position_label: str
    acquisition_type: str
    files: list[str]
    planes: list[dict]
    czi: Path
    sidecar: Path
    record: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        """The answer in the controller's words: ``position_label``, ``acquisition_type``, ``files``, ``planes``."""
        return {
            "position_label": self.position_label,
            "acquisition_type": self.acquisition_type,
            "files": list(self.files),
            "planes": [dict(plane) for plane in self.planes],
            "czi": str(self.czi),
            "record": str(self.sidecar),
        }
