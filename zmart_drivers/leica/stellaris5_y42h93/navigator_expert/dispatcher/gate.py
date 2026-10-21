"""The limits gate: before anything is sent, the limits of this connection are asked.

Every change goes through the dispatcher's door, and the door asks this gate
first (:func:`check_refusal`). When the change is not allowed, nothing is sent
and the answer says why. Because the check sits below everything a workflow, a
notebook or a procedure can call, none of them can go around it.

The gate keeps, for each connection, the limits object that the driver
installed when it connected (:func:`install`). It knows no microscope: it asks
that object one question, ``check_command(command, values)``, which raises when
the values are not allowed. What is allowed is the vendor's own rulebook (for
LAS X, ``vendor_specific/primitive_handling/settings/limit_rules.py``), and
finding, checking and installing the operator's limits file is the driver's
connect-time handshake (for LAS X, ``driver_setup/limits/handshake.py``). If
the operator's file cannot be used, the handshake installs the limits that ship
with the driver, so the session stays bounded. If even those cannot be used, the
gate refuses every change and gives the recorded reason. A connection that was
never handed limits refuses too.

The gate assumes one microscope connection per process. It keys each
connection by ``id(client)`` and keeps the client itself, so that the number
can never be reused by a new connection that would then inherit old limits.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .limit_errors import LimitsError, LimitsStatus, LimitViolation

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GateState:
    """Private installed state for one session.

    ``limits`` is the loaded limits object of the vendor interface and ``stage_cfg``
    the validated stage config when the handshake succeeded; on failure both
    are None and ``error`` records exactly what is wrong (fail closed: every
    gated wrapper refuses with this message).
    """

    limits: Any | None = None
    stage_cfg: Mapping[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True, slots=True)
class GateStatus:
    """Detached result returned by the handshake and :func:`state_for`."""

    limits: LimitsStatus | None
    stage_cfg: dict | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.error is None


# id(client) -> (client, GateState). The client reference is deliberately
# strong: it pins the id so a garbage-collected client's id can never be
# recycled onto a new, never-handshaken client that would then inherit stale
# limits. microscope clients are process-lifetime, so this is not a leak in practice.
_GATE_STATE: dict[int, tuple[Any, GateState]] = {}


def freeze(value: Any) -> Any:
    """Recursively freeze the JSON-shaped installed stage snapshot."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a detached ordinary-Python copy for public status consumers."""
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def status(state: GateState) -> GateStatus:
    return GateStatus(
        limits=state.limits.status() if state.limits is not None else None,
        stage_cfg=_thaw(state.stage_cfg) if state.stage_cfg is not None else None,
        error=state.error,
    )


def install(client: Any, state: GateState) -> None:
    _GATE_STATE[id(client)] = (client, state)


def uninstall(client: Any) -> None:
    """Drop a client's gate state (its commands then refuse fail-closed)."""
    _GATE_STATE.pop(id(client), None)


def state_of(client: Any) -> GateState | None:
    entry = _GATE_STATE.get(id(client))
    return entry[1] if entry is not None else None


def state_for(client: Any) -> GateStatus | None:
    """A detached status snapshot, or None when the client never handshook."""
    state = state_of(client)
    return status(state) if state is not None else None


def describe(client: Any) -> dict | None:
    """Provenance of the limits governing *client* (None = refusing, see error).

    The record contains source, path, and fallback status. It is reported by
    the adapter under observed state as evidence.
    """
    state = state_of(client)
    if state is None or state.limits is None:
        return None
    return state.limits.describe()


def check_refusal(
    client: Any, command: str, values: Mapping[str, Any], setup_hint: str | None = None
) -> str | None:
    """Gate one mutating command; returns the refusal message, or None to fire.

    Fail-closed on every failure mode: no handshake ever ran for this client,
    the handshake failed (missing/invalid machine-local limits), or *values*
    violate the file's constraints. The caller (a command wrapper) must not
    fire the native call when this returns a message; wrappers turn it into
    their fail-closed result-dict idiom, the adapter/controller raise.
    *setup_hint*, when given, says where the operator creates the limits files;
    the vendor part knows that, so it hands it in.
    """
    state = state_of(client)
    if state is None:
        return (
            f"{command} refused: no limits are installed for this session — run the "
            "connect-time limits handshake first (the driver does this when it connects)."
            + (
                f" The machine-local limits files are created with {setup_hint}."
                if setup_hint
                else ""
            )
        )
    if state.limits is None:
        return f"{command} refused: {state.error}"
    try:
        state.limits.check_command(command, values)
    except (LimitsError, LimitViolation) as exc:
        return f"{command} refused: {exc}"
    return None
