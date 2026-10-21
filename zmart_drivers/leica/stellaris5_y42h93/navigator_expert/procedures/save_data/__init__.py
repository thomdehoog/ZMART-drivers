"""Saving a capture: a procedure of its own, separate from taking the picture.

Taking a picture leaves it in LAS X's cache, together with everything we
noted at that moment: where the stage stood, which job ran, the state of the
microscope. This procedure takes that note (a :class:`PendingCapture`),
reads the picture from LAS X's cache, and writes it to disk as an image
store or as OME-TIFF files, with a log of what was asked and done.

Saving is kept apart from clearing LAS X's cache (``procedures/clear_cache``):
a picture is only ever let go of after it has been saved, and only when the
operator chooses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...actions.profiles import IMAGE_SAVE
from ...output import command_log as _log
from ...output import save as _save
from ...output.naming import Naming


@dataclass
class PendingCapture:
    """One captured picture, waiting to be saved, with the notes taken as it was captured.

    The notes matter: where the stage stood and what the microscope's state
    was can only be known at the moment of the capture, so they are written
    down then and used later, whenever the picture is saved.
    """

    acq: Any
    label: str
    naming: Naming
    state: dict
    job: str
    at: dict
    output_root: Path
    session_hash6: str
    asked: dict
    settings: dict
    steps: dict
    backlash_rounds: int
    apply_backlash: bool
    unconfirmed: list = field(default_factory=list)


def save_pending(
    client: Any,
    pending: PendingCapture,
    *,
    orientation: Any,
    where_the_planes_are: Callable[[list[int]], Callable[[int], dict]],
) -> tuple[dict, Any]:
    """Save *pending* and answer ``(record, saved)``.

    The record is what the caller reports: the files, the planes (which file
    holds which channel, depth and time, and where on the sample it was
    taken) and the path of the log. *where_the_planes_are* answers, for the
    depths of a capture, where on the sample each one was taken. Raises when
    the picture cannot be read from LAS X's cache or the copy does not read
    back the same; the caller then keeps the capture pending, to try again.
    """
    p = pending
    saved = _save.save(
        client,
        p.acq,
        p.output_root,
        p.naming,
        lineage={
            "position_label": p.label,
            "stem": p.naming.stem,
            "job": p.job,
            "session_hash6": p.session_hash6,
        },
        state=p.state,
        # Rig image->stage orientation, applied to the saved planes behind the
        # scenes so the workflow only ever sees stage-aligned images.
        orientation=orientation,
        format=IMAGE_SAVE.format,
        projection=IMAGE_SAVE.projection,
        # Where on the sample each plane was taken, the same answer the
        # record gives below, so a store lies where its planes say.
        placement=where_the_planes_are,
    )

    written = sorted(saved.image_paths.items())
    depths = [int(getattr(index, "z", ordinal)) for ordinal, (index, _path) in enumerate(written)]
    taken_at = where_the_planes_are(depths)
    # Depth 0 is the lowest slice, as in a store, whichever way LAS X swept.
    upward = sorted(set(depths), key=lambda z: taken_at(z)["z_um"])
    planes = [
        {
            "t": int(getattr(index, "t", 0)),
            "z": upward.index(depths[ordinal]),
            "c": int(getattr(index, "c", ordinal)),
            # Where the pixels are, and where inside them. A flat OME-TIFF
            # holds exactly the one plane t/c/z name; were this a store, the
            # same three would index into it and only the path would change.
            "path": str(path),
            **taken_at(depths[ordinal]),
        }
        for ordinal, (index, path) in enumerate(written)
    ]
    vendor_metadata = [str(path) for path in getattr(saved, "vendor_metadata_paths", ())]
    printed = [str(path) for path in getattr(saved, "state_paths", ())]
    projections = [str(path) for path in getattr(saved, "projection_paths", ())]
    files = list(dict.fromkeys([*(plane["path"] for plane in planes), *projections, *vendor_metadata, *printed]))
    log = _log.write(p.output_root, p.naming, {
        "position_label": p.label,
        "stem": p.naming.stem,
        "asked": dict(p.asked or {}),
        "settings": p.settings,
        "steps": p.steps,
        "capture": {
            "started": _when(p.acq.started_at),
            "finished": _when(p.acq.finished_at),
            "answered": getattr(p.acq, "command_result", None),
        },
        "unconfirmed": list(p.unconfirmed),
        # Paths from where the run starts, so they stay true when the
        # capture is moved into a run folder of the same shape.
        "planes": [{**plane, "path": _from(p.output_root, plane["path"])} for plane in planes],
        "files": [_from(p.output_root, path) for path in files],
    })
    record = {
        "position_label": p.label,
        "trigger": p.settings["trigger"],
        "job": p.job,
        "format": IMAGE_SAVE.format,
        "settle": "backlash-corrected" if p.apply_backlash else "direct",
        "backlash_rounds": p.backlash_rounds if p.apply_backlash else 0,
        # Every file this acquisition saved, under the name the ZMART
        # Controller's contract fixes, so a workflow finds them on any
        # microscope: the images, then the vendor's metadata, then the state.
        "files": [*files, str(log)],
        # The manifest a workflow needs to tell channels from z and t, and
        # where on the sample each plane was taken.
        "planes": planes,
        "vendor_metadata": vendor_metadata,
        # What the driver printed about this capture: the state, beside the
        # images in ``data/metadata``. A client moving a record moves this too.
        "metadata": printed,
        # The changes made for this capture that were sent but never
        # confirmed by a readback (the backlash takeup). Empty when everything
        # confirmed. The capture itself went ahead.
        "unconfirmed": list(p.unconfirmed),
        # The driver's log of this capture: what it was asked, and what it did.
        "log": [str(log)],
        # Projected from a capture with more than one plane, beside its data.
        "projections": projections,
    }
    return record, saved


def _from(root: Path, path: str) -> str:
    """*path* from *root*, with forward slashes; as it is when it lies elsewhere."""
    try:
        return Path(path).relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _when(seconds: float | None) -> str | None:
    """A time from the clock as an ISO 8601 moment in UTC; None when there is none."""
    if not isinstance(seconds, (int, float)):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
