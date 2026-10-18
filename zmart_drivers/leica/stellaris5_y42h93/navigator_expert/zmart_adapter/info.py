"""Where this session saves: the folder every acquisition goes under."""

from __future__ import annotations

from pathlib import Path
from typing import Any

#: What to run when no data location was set up, said in every refusal.
SET_UP = (
    "set this microscope's data location once with "
    "configuration/data_location/notebooks/set_data_location.ipynb, "
    "or connect with an output_root"
)


def output_root(handle: Any) -> Path:
    """The folder this session saves under: its own ``output_root``, else the set-up data location.

    Raises ``RuntimeError`` saying how to set one up when neither is there:
    data saved somewhere nobody chose is data lost.
    """
    root = handle.connection.get("output_root") or handle.data_location
    if not root:
        raise RuntimeError(
            f"this microscope does not know where to save its data: {SET_UP}"
        )
    return Path(root).expanduser().resolve()
