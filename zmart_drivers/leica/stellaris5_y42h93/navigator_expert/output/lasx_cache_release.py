"""Let go of a picture in LAS X's cache, once it is saved, through LAS X's own Data Container.

Every picture LAS X takes stays in its Data Container cache (``D:\\Temp`` on
this microscope) until LAS X is restarted, so a long time course fills the
disk twice: once with the cache, once with the data we save. The cache
cannot be emptied from outside. Its files are held open by LAS X's Data
Container server, and LAS X's project still lists the picture, so deleting
them would leave the project pointing at data that is gone.

LAS X can let go of a picture itself: deleting it in LAS X's window makes the
Data Container release its files, and LAS X's I/O manager take it out of the
project, with the file AutoSave wrote of it. :func:`delete` asks for exactly
that delete, through the .NET client every part of LAS X uses to reach its
Data Container (``LMSDataContainerWrapper.dll`` from the LAS X install, the
same folder the CAM API is loaded from). Nothing of LAS X is copied or
changed.

What it takes:

- the picture's element number: the ``<n>`` of the cache files
  ``V2LMSDC_OBJ<n>`` and ``V2LMSDC_MMF<n>`` the picture was read from;
- connecting as the Windows user, who owns LAS X's pictures (any other name is
  refused access to them);
- the picture's name, checked before deleting, so that nothing else is ever
  deleted.

The save calls it last (``cleanup_source``), only once its copy is written and
read back identical. Seen on the LAS X simulator: the picture's pixels leave
the cache, the project file is rewritten without it, and LAS X captures on.
About 0.2 MB of descriptions per picture stays while LAS X's window holds it.
"""

from __future__ import annotations

import functools
import os
from pathlib import Path
from typing import Any


def delete(number: int, name: str) -> None:
    """Have LAS X delete the picture *name*, its Data Container element *number*.

    It is the delete in LAS X's own window. Refused, with nothing deleted,
    when the element is no longer that picture.
    """
    element = connected_data_container().GetElement(number)
    if element.GetName() != name:
        raise RuntimeError(f"LAS X's element {number} is {element.GetName()!r}, not {name!r}; nothing deleted")
    element.Delete()


@functools.cache
def connected_data_container() -> Any:
    """LAS X's Data Container, as one more of its clients; returns the factory its elements are got from."""
    from ..actions import profiles

    root = profiles.LASX_API.runtime_root
    os.environ["PATH"] = root + os.pathsep + os.environ["PATH"]
    import clr  # type: ignore[import-not-found]
    import System  # type: ignore[import-not-found]

    System.Reflection.Assembly.LoadFile(str(Path(root) / "LMSDataContainerWrapper.dll"))
    from LMSDataContainerWrapper import DataContainerClient, DMObjectFactory

    if not DataContainerClient.CreateAndInitInstance(System.Environment.UserName, "ZMART"):
        raise RuntimeError("LAS X's Data Container did not accept the connection")
    return DMObjectFactory()
