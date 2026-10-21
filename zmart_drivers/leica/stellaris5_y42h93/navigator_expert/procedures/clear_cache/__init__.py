"""Clearing LAS X's cache: a procedure of its own, never part of acquiring or saving.

Every picture LAS X takes stays in its cache until it is told to let go, so a
long run slowly fills the disk. Letting go is a deliberate step, taken when
the operator or the workflow decides it, and only for pictures whose saved
copy is known to be complete. Acquiring and saving never do it for you.
"""

from __future__ import annotations

import logging

from . import release

log = logging.getLogger(__name__)


def clear(pending: list[tuple[int, str]]) -> dict:
    """Have LAS X let go of the pictures in *pending*, and say what happened.

    *pending* holds one ``(element number, picture name)`` pair for each
    saved capture whose picture is still in LAS X's cache. Each picture is
    checked by name just before it is let go of, so nothing else is ever
    deleted. A picture that cannot be let go of is left in the list and
    reported, so a later call can try again; the others are still cleared.
    The list is updated in place to hold only what is left.

    Returns ``{"cleared": [names], "failed": {name: reason}}``.
    """
    cleared: list[str] = []
    failed: dict[str, str] = {}
    if not pending:
        return {"cleared": cleared, "failed": failed}
    release.prepare()
    for number, name in list(pending):
        try:
            release.delete(number, name)
        except Exception as why:  # noqa: BLE001 -- one stubborn picture must not stop the rest
            log.warning("could not clear %s from LAS X's cache: %s", name, why)
            failed[name] = str(why)
        else:
            pending.remove((number, name))
            cleared.append(name)
    return {"cleared": cleared, "failed": failed}
