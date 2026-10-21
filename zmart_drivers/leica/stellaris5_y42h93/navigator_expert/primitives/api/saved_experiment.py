"""The stacked z drive, read from the experiment LAS X saves.

For the drive that carries a job's z-stack, LAS X stops refreshing the job
settings' ``zPosition``; the hardware server knows where the drive is. When the
hardware server cannot be reached, the experiment is the other place LAS X
keeps it: ``PyApiSaveExperiment`` makes Navigator Expert write the experiment
(``.lrp``) into its ScanningTemplates folder, and each job's ``ZPosition`` is
in it. One save, under one name the driver keeps for this, took 0.1 to 0.5 s
on the simulator (2026-10-21). Nothing is ever loaded from it.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ...connection import waiting
from ..values.parsing import saved_z_um
from .delivery import deliver

#: Navigator Expert's ScanningTemplates folder, where it saves an experiment.
FOLDER = os.path.join(
    os.getenv("APPDATA", ""),
    "Leica Microsystems",
    "LAS X",
    "MatrixScreener6",
    "User_0",
    "ScanningTemplates",
)

#: The name the driver saves under, overwritten at every save.
NAME = "{ScanningTemplate}_ZMART_z"


def read_z_um(client: Any, job_name: str, drive: str) -> float:
    """Save the experiment and answer *job_name*'s ``ZPosition`` for *drive* (µm).

    The save is delivered as every request is (``delivery.deliver``), and the
    file counts once it is newer than before the save and has stopped
    growing, looked at every ``POLL_S`` for one window (``waiting.WINDOW_S``).
    Raises ``RuntimeError`` when LAS X does not take the save or no new file
    appears; a file from before the save is never read.
    """
    lrp = Path(FOLDER) / f"{NAME}.lrp"
    before = lrp.stat().st_mtime_ns if lrp.is_file() else 0
    client.PyApiSaveExperiment.Model.ExperimentName = f"{NAME}.xml"
    if not deliver(client.PyApiSaveExperiment, label="SaveExperiment (for the stacked z drive)"):
        raise RuntimeError("LAS X did not take the experiment save")

    deadline = time.monotonic() + waiting.WINDOW_S
    size = None
    while time.monotonic() < deadline:
        try:
            state = lrp.stat()
            fresh = state.st_mtime_ns > before
            if fresh and state.st_size and state.st_size == size:
                return saved_z_um(lrp.read_text(encoding="utf-8"), job_name, drive)
            size = state.st_size if fresh else None
        except (OSError, ET.ParseError):
            size = None  # not there yet, or still being written
        time.sleep(waiting.POLL_S)
    raise RuntimeError(f"no saved experiment appeared in {FOLDER} within {waiting.WINDOW_S:g} s")
