"""The note kept for a capture whose files are not written yet.

The stage position and the microscope's state can only be known at the moment
of the capture, so they are written down then. ``save_data`` uses the note
later, whenever it is run, so the files describe the capture as it was and not
as things stand by then.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..save_data.naming import Naming


@dataclass
class PendingCapture:
    """One captured picture, waiting to be saved, with the notes taken as it was captured."""

    acq: Any
    label: str
    acquisition_type: str
    naming: Naming
    state: dict
    job: str
    at: dict
    output_root: Path
    session_hash6: str
    asked: dict
    settings: dict
    unconfirmed: list = field(default_factory=list)
