"""Let go of a picture in LAS X's cache, once it is saved, through LAS X's own Data Container.

Every picture LAS X takes stays in its Data Container cache (the folder the
registry names; ``D:\\Temp2`` on the STELLARIS Y42H93) until LAS X is
restarted, so a long time course fills the disk twice: once with the cache,
once with the data we save. The cache cannot be emptied from outside: LAS X
holds its files open, and LAS X's project still lists the picture, so
deleting them would leave the project pointing at data that is gone.

LAS X can let go of a picture itself, as when it is deleted in LAS X's
window: its files leave the cache, and the project no longer lists it, nor
the file AutoSave wrote of it. :func:`delete` asks for that delete through
the Data Container's .NET client from the LAS X install
(``vendor_interface.primitives.capture.data_container``). Nothing of LAS X is copied or changed.

What it takes:

- the picture's element number: the ``<n>`` of the cache files
  ``V2LMSDC_OBJ<n>`` and ``V2LMSDC_MMF<n>`` the picture was read from;
- connecting as the Windows user, who owns LAS X's pictures (any other name is
  refused access to them);
- the picture's name, checked before deleting, so that nothing else is ever
  deleted.

The save calls it last (``cleanup_cache``), only once its copy is written and
read back identical. Seen on the LAS X simulator: the picture's pixels leave
the cache, the project file is rewritten without it, and LAS X captures on.
About 0.2 MB of descriptions per picture stays while LAS X's window holds it.

Every capture also leaves a hidden copy, ``TileScan <n>`` (the pixels again,
a zoom pyramid and a histogram: about twice a 512 x 512 capture's pixels),
which the Navigator canvas holds while it shows the capture's tile. So the
release also clears the canvas (``vendor_interface.primitives.interface.lasx_navigator``), which
lets go of the hidden copies too (simulator, 2026-10-14). The canvas can be
cleared only while LAS X shows it, so :func:`prepare` shows it before the
capture, while LAS X is idle. A canvas that cannot be shown or cleared is
logged; the capture is taken and released all the same.
"""

from __future__ import annotations

import logging

from ..vendor_interface.primitives.interface import lasx_navigator
from ..vendor_interface.primitives.capture.data_container import (
    connected_data_container,
)

log = logging.getLogger(__name__)


def prepare() -> None:
    """Show the Navigator canvas before a capture that will be released, so that its Clear can be pressed after."""
    try:
        if lasx_navigator.show_canvas():
            log.info(
                "Navigator Expert's Experiment tab shown, so the canvas can be cleared after the capture"
            )
    except lasx_navigator.NavigatorUnavailable as why:
        log.warning(
            "the Navigator canvas could not be shown, so this capture's hidden copy may stay in LAS X's cache: %s",
            why,
        )


def delete(number: int, name: str) -> None:
    """Have LAS X delete the picture *name*, its Data Container element *number*.

    It is the delete in LAS X's own window. Refused, with nothing deleted,
    when the element is no longer that picture. Then the Navigator canvas is
    cleared, which lets go of the picture's hidden copy.
    """
    element = connected_data_container().GetElement(number)
    if element.GetName() != name:
        raise RuntimeError(
            f"LAS X's element {number} is {element.GetName()!r}, not {name!r}; nothing deleted"
        )
    element.Delete()
    try:
        lasx_navigator.clear_canvas()
    except lasx_navigator.NavigatorUnavailable as why:
        log.warning(
            "the Navigator canvas was not cleared, so %r's hidden copy stays in LAS X's cache: %s",
            name,
            why,
        )
