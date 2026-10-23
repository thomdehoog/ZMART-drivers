"""LAS X's Data Container, reached as one more of its clients.

LAS X keeps every picture it takes as an element of its Data Container: an
image, inside an experiment (a project). LAS X installs a .NET client for
it beside the CAM API; the driver loads it from there and connects as the
Windows user, who owns LAS X's pictures (any other name is refused access
to them). Nothing of LAS X is copied or changed.

A capture sent straight to LAS X's hardware server names the experiment its
image goes into. :func:`experiment_for_capture` opens it (or makes it): the
change for a capture hands it to the write (``Write.experiment``), and the
direct route asks it only when it builds the capture's element.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Any

from ...primitives.cam_api.client import _runtime_root
from ...primitives.direct.client import HardwareServerUnavailable
from ...primitives.direct.state_tree import last_image_id

__all__ = [
    "connected_data_container",
    "experiment_for_capture",
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


# =============================================================================
# The experiment a capture goes into
# =============================================================================

#: The name the driver gives a project it makes, followed by its number.
NEW_PROJECT = "ZMART_Experiment"

#: The experiment the last capture's image went into, kept while the driver runs.
remembered = {"experiment": None}


def remembered_experiment_file() -> Path:
    """Where the last experiment found is written down, for the next start of the driver.

    Under the Windows user's own profile (``%LOCALAPPDATA%\\ZMART``), as LAS X's
    Data Container belongs to that user too.
    """
    root = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(root) / "ZMART" / "leica-stellaris-direct-route.json"


def read_remembered_experiment() -> int | None:
    """The experiment written down by an earlier run; None when there is none or it cannot be read."""
    try:
        number = json.loads(remembered_experiment_file().read_text(encoding="utf-8"))[
            "experiment"
        ]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def write_remembered_experiment(number: int) -> None:
    """Write down *number*; a file that cannot be written only costs the next start its memory."""
    file = remembered_experiment_file()
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps({"experiment": number}), encoding="utf-8")
    except OSError:
        pass


def new_project_name(open_projects: dict[int, str]) -> str:
    """``ZMART_Experiment<N>``, N one above the highest such name among *open_projects* (1 for the first)."""
    numbers = [
        int(name[len(NEW_PROJECT) :])
        for name in open_projects.values()
        if name.startswith(NEW_PROJECT) and name[len(NEW_PROJECT) :].isdigit()
    ]
    return f"{NEW_PROJECT}{max(numbers, default=0) + 1}"


def experiment_for_capture() -> int:
    """The Data Container experiment, LAS X's project, a capture's image goes into; opened or made now.

    LAS X's window names it from its project tree, which only the window has;
    the driver chooses it from the Data Container, and only ever a project
    open in LAS X. A project deleted in LAS X's window stays in the Data
    Container, closed, with its images, and an image sent into it is seen
    nowhere (STELLARIS, 2026-10-15: image 2135 into deleted project 698). In
    this order:

    1. The project of the last image LAS X made.
    2. The project remembered from an earlier capture, kept while the driver
       runs and in a file (:func:`remembered_experiment_file`), because the
       last image is often gone by the next capture.
    3. The newest project open in LAS X.
    4. With none open, a new project, ``ZMART_Experiment<N>``, as LAS X makes
       one itself when it captures with no project open.

    Raises ``RuntimeError`` when the hardware server or the Data Container
    cannot answer; the direct route then sends nothing, and the CAM API
    carries the capture.
    """
    try:
        last = last_image_id()
    except HardwareServerUnavailable as why:
        raise RuntimeError(f"no last image from LAS X's state tree: {why}") from why
    try:
        chosen = open_project_for(last)
    except Exception as why:  # noqa: BLE001 -- any Data Container failure refuses, nothing is sent
        raise RuntimeError(
            f"LAS X's Data Container did not give a project for the image: {why}"
        ) from why
    if chosen != remembered["experiment"]:
        write_remembered_experiment(chosen)
    remembered["experiment"] = chosen
    return chosen


def open_project_for(last: int) -> int:
    """The project chosen by the order in :func:`experiment_for_capture`, for last image *last* (0: none)."""
    try:
        found = experiment_of(last) if last else None
    except Exception:  # noqa: BLE001 -- the last image was removed, as the driver does after saving
        found = None
    kept = remembered["experiment"]
    for candidate in (found, kept if kept is not None else read_remembered_experiment()):
        if candidate is not None and experiment_is_open(candidate):
            return candidate
    projects = open_experiments()
    if projects:
        return max(projects)
    return new_experiment(new_project_name(projects))
