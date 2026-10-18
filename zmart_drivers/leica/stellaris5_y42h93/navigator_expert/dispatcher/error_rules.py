"""What the driver does for each kind of problem, the same on every microscope.

The vendor interface sorts a failure into a kind (``error_vocabulary.py``).
This file says what the engines do next. It is a small table on purpose, so
that one case is never handled one way by one command and another way by the
next.

Sending a command again is the one decision that can change the experiment,
so it is allowed for a temporary problem and for nothing else. A command that
must reach the microscope only once, such as an acquisition, is never sent
again, whatever kind of problem it ran into (see ``never_resend`` in the
command profiles).

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

from .error_vocabulary import Kind

#: The kinds for which sending the same command again is safe and useful.
_SEND_AGAIN = frozenset({Kind.TEMPORARY})


def sends_again(kind: Kind) -> bool:
    """Whether a command that ran into *kind* may be sent again (within its retry limit)."""
    return kind in _SEND_AGAIN
