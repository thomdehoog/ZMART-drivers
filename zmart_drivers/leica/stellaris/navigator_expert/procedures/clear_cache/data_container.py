"""LAS X's Data Container, reached as one more of its clients.

LAS X keeps every picture it takes as an element of its Data Container: an
image, inside an experiment (a project). LAS X installs a .NET client for
it beside the CAM API; the driver loads it from there and connects as the
Windows user, who owns LAS X's pictures (any other name is refused access
to them). Nothing of LAS X is copied or changed.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from ...primitives.cam_api.lasx_runtime import _runtime_root

__all__ = [
    "connected_data_container",
    "experiment_is_open",
    "experiment_of",
    "new_experiment",
    "open_experiments",
]


@functools.cache
def connected_data_container() -> Any:
    """LAS X's Data Container; returns the factory its elements are got from."""
    root = _runtime_root()
    os.environ["PATH"] = str(root) + os.pathsep + os.environ["PATH"]
    import clr  # type: ignore[import-not-found]  # noqa: F401
    import System  # type: ignore[import-not-found]

    System.Reflection.Assembly.LoadFile(str(root / "LMSDataContainerWrapper.dll"))
    from LMSDataContainerWrapper import DataContainerClient, DMObjectFactory

    if not DataContainerClient.CreateAndInitInstance(
        System.Environment.UserName, "ZMART"
    ):
        raise RuntimeError("LAS X's Data Container did not accept the connection")
    return DMObjectFactory()


def experiment_of(number: int) -> int | None:
    """The id of the experiment element *number* belongs to; None when it has none.

    An acquisition names the experiment its image goes into; the direct
    route takes the one LAS X's last image went into (seen on the simulator,
    2026-10-14: image 5148, ``HiRes043``, in experiment 84, the id the CAM
    API's own acquire had sent).
    """
    factory = connected_data_container()
    from LMSDataContainerWrapper import DMExperiment

    experiment = DMExperiment.GetParentExperiment(factory.GetElement(number))
    return None if experiment is None else int(experiment.GetElementID())


def open_experiments() -> dict[int, str]:
    """The experiments open in LAS X, as its Open Projects panel lists them: id -> name.

    They are the open experiments directly under the Data Container's root.
    A project deleted in LAS X's window stays in the Data Container, closed
    and under nothing, with its images (STELLARIS, 2026-10-15: project 698),
    so it is not among them.
    """
    root = connected_data_container().GetRootElement()
    return {
        int(child.GetElementID()): str(child.GetName())
        for child in (root.GetChildren() or ())
        if child.IsOpen
    }


def experiment_is_open(number: int) -> bool:
    """Whether experiment *number* is open in LAS X; an image is sent into no other.

    A remembered or found experiment may since have been deleted in LAS X's
    window: it is then still in the Data Container, closed, and an image sent
    into it is seen nowhere (STELLARIS, 2026-10-15: image 2135 into deleted
    project 698). A missing element counts as not open.
    """
    try:
        return number in open_experiments()
    except Exception:  # noqa: BLE001 -- LAS X answers a missing element with an exception
        return False


def new_experiment(name: str) -> int:
    """Create an experiment named *name*, open in LAS X as a project of its own; returns its id.

    As LAS X does itself when it captures with no project open (STELLARIS,
    2026-10-15: "Project", in its program folder). The experiment is the
    Data Container's own, under its root, open, and listed in LAS X's Open
    Projects; it is saved nowhere until LAS X or the driver saves it.
    """
    from LMSDataContainerWrapper import DMExperiment

    connected_data_container()
    made = DMExperiment.CreateNewExperiment(str(_runtime_root() / name))
    if made is None:
        raise RuntimeError(f"LAS X's Data Container did not create experiment {name!r}")
    return int(made.GetElementID())
