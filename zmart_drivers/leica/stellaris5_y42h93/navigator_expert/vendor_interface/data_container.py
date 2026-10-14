"""LAS X's Data Container, reached as one more of its clients.

Every picture LAS X takes is an element of its Data Container (the service on
port 8892): an image, inside an experiment (a project). The .NET client every
part of LAS X uses to reach it, ``LMSDataContainerWrapper.dll``, sits in the
LAS X install beside the CAM API; the driver loads it from there, connected as
the Windows user, who owns LAS X's pictures (any other name is refused access
to them). Nothing of LAS X is copied or changed.
"""

from __future__ import annotations

import functools
import os
from typing import Any

from .lasx_runtime import _runtime_root

__all__ = ["connected_data_container"]


@functools.cache
def connected_data_container() -> Any:
    """LAS X's Data Container; returns the factory its elements are got from."""
    root = _runtime_root()
    os.environ["PATH"] = str(root) + os.pathsep + os.environ["PATH"]
    import clr  # type: ignore[import-not-found]  # noqa: F401
    import System  # type: ignore[import-not-found]

    System.Reflection.Assembly.LoadFile(str(root / "LMSDataContainerWrapper.dll"))
    from LMSDataContainerWrapper import DataContainerClient, DMObjectFactory

    if not DataContainerClient.CreateAndInitInstance(System.Environment.UserName, "ZMART"):
        raise RuntimeError("LAS X's Data Container did not accept the connection")
    return DMObjectFactory()
