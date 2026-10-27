"""Write ``start_bridge.mac``, the NIS macro that starts the bridge on this computer.

NIS-Elements runs macros, and one line of macro starts Python. The macro
that starts the bridge needs the full path of the bridge (``server.py``) on
this computer, so it is written once per computer, by this program::

    python -m zmart_drivers.nikon.nis_elements_ar7.driver_setup.nis_bridge

It writes ``start_bridge.mac`` into the bridge folder of this computer's
settings (``C:\\ProgramData\\zmart-microscopy\\nikon\\bridge\\``) and prints
where it is. In NIS-Elements, run it with *Macro > Run Macro From File*. The
macro then keeps running, on purpose: its loop is what runs the driver's
requests on NIS's own thread. Stop it with the macro's Stop button in NIS,
or from the driver (``Microscope.stop_bridge()``). There is no stop macro,
because NIS runs one macro at a time, so it could never run while the
bridge runs.

Write it again after updating the driver: the bridge running in NIS must be
the same version as the driver, and the driver refuses another.

Why the macro looks the way it does, from NIS 6.10 and step 1 of the NIS 7 study:

- NIS refuses, without a word, a macro it cannot compile; comments and string
  variables were enough to cause that. So the macro holds plain calls and
  literals only.
- Every Python line is guarded as in step 1: when the bridge did not load,
  the line writes the stop file, so the loop ends instead of failing at every pass.
- The Python strings use forward slashes, and the file is plain ASCII. A path
  with an apostrophe or a letter outside ASCII is refused here, rather than
  making a macro NIS might read wrongly. ``ExistFile`` gets its path with
  doubled backslashes, as step 1's macro did.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
Based on ``step1.mac`` of thomdehoog/nis-useq-aiagent (branch ``_nis-7-step1``)
and the NIS 6.10 driver's ``bridge/install.py``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ...primitives.nis_python import server
from .. import store

SERVER_FILE = Path(server.__file__).resolve()
MACRO_NAME = "start_bridge.mac"
STOP_FILE_NAME = "bridge.stop"
KEY_FILE_NAME = "key.txt"

TEMPLATE = """WaitText(1, "ZMART bridge - starting");
Python_RunString("import importlib.util, sys; s = '{stop}'; spec = importlib.util.spec_from_file_location('zmart_nis_bridge', '{server}'); m = importlib.util.module_from_spec(spec); sys.modules['zmart_nis_bridge'] = m; spec.loader.exec_module(m); import nis; nis.log(m.start(stop_file=s, key_file='{key}', port={port}))");
WaitText(1, "ZMART bridge - running on port {port}. Stop it with the macro Stop button.");
while (ExistFile("{stop_macro}") == 0)
{{
    Python_RunString("import sys; m = sys.modules.get('zmart_nis_bridge'); p = getattr(m, 'pump', None); p() if p else open('{stop}', 'w').close()");
    Wait(0.01);
}}
Python_RunString("import sys, nis; m = sys.modules.get('zmart_nis_bridge'); f = getattr(m, 'stop', None); nis.log(f()) if f else None");
WaitText(2, "ZMART bridge - stopped");
"""


def python_path(path: Path) -> str:
    """*path* as it is written inside a macro's Python line: forward slashes; refused when it cannot be written safely."""
    text = str(path).replace("\\", "/")
    if "'" in text or '"' in text:
        raise ValueError(
            f"{path} holds a quote, which would end the macro's text; put the driver in a folder without one"
        )
    if not text.isascii():
        raise ValueError(
            f"{path} holds a letter outside plain ASCII, which NIS may read wrongly; put the driver in a folder with a plain name"
        )
    return text


def macro_path(path: Path) -> str:
    """*path* as a macro's own string writes it: backslashes doubled, as ``step1.mac`` did."""
    return python_path(path).replace("/", "\\\\")


def render(server_file: Path, stop_file: Path, key_file: Path, port: int) -> str:
    """The text of ``start_bridge.mac`` for these paths."""
    return TEMPLATE.format(
        server=python_path(server_file),
        stop=python_path(stop_file),
        stop_macro=macro_path(stop_file),
        key=python_path(key_file),
        port=int(port),
    )


def files(folder: Path | None = None) -> dict[str, Path]:
    """Where the macro, the stop file and the key go: the bridge folder of this computer, unless given."""
    folder = Path(folder) if folder else store.bridge_folder()
    return {
        "folder": folder,
        "macro": folder / MACRO_NAME,
        "stop": folder / STOP_FILE_NAME,
        "key": folder / KEY_FILE_NAME,
    }


def write_macro(folder: Path | None = None, port: int = server.PORT) -> Path:
    """Write ``start_bridge.mac`` and return its path."""
    where = files(folder)
    where["folder"].mkdir(parents=True, exist_ok=True)
    text = render(SERVER_FILE, where["stop"], where["key"], port)
    # Plain ASCII, with the CRLF line ends NIS's own editor writes.
    where["macro"].write_text(text, encoding="ascii", newline="\r\n")
    return where["macro"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write start_bridge.mac, the NIS macro that starts the ZMART bridge."
    )
    parser.add_argument(
        "--folder",
        help="the folder to write it into (default: this computer's ZMART bridge folder)",
    )
    parser.add_argument(
        "--port", type=int, default=server.PORT, help="the port the bridge listens on"
    )
    args = parser.parse_args(argv)
    path = write_macro(Path(args.folder) if args.folder else None, args.port)
    print(f"Wrote {path}")
    print(
        "In NIS-Elements: Macro > Run Macro From File..., and pick it. Leave it running while you use the driver."
    )
    return 0
