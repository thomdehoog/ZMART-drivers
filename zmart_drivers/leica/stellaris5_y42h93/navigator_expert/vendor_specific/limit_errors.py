"""What goes wrong with limits, in words every driver shares.

Three small types: ``LimitsError`` (the limits file is malformed or
incomplete), ``LimitViolation`` (a value lies outside what the limits allow)
and ``LimitsStatus`` (where the limits in force came from, as evidence).
Each microscope has its own rules for what is allowed; these types are how
the shared gate talks about them.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from dataclasses import dataclass


class LimitsError(ValueError):
    """The limits file (or an override) is malformed or incomplete."""


class LimitViolation(RuntimeError):
    """A checked value is outside its configured limit."""


@dataclass(frozen=True, slots=True)
class LimitsStatus:
    """Detached public provenance for the installed limits."""

    source: str
    path: str | None
    is_fallback: bool

    def describe(self) -> dict:
        return {
            "source": self.source,
            "path": self.path,
            "is_fallback": self.is_fallback,
        }
