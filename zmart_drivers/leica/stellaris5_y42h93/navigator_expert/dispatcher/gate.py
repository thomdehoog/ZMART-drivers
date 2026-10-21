"""Limits gate for the commands layer.

Maintainer decision (MAINTAINER_DECISIONS.md §7): limits are enforced as low
as possible — at the command wrapper that populates the native function's
model — so nothing built on top (adapter, controller, workflows, notebooks)
can interfere with or bypass them. The rules themselves (what is allowed:
``LeicaLimits`` and the stage checks) live in ``vendor_specific/primitive_handling/settings/limit_rules.py``; this
module owns the *moment* of enforcement — per-connection state and refusal. It
knows no microscope: the installed limits object answers ``check_command``.
The wrappers in ``actions/`` call :func:`check_refusal` before anything fires.

Wrapper -> key mapping
----------------------
The operator-facing ``limits.json`` is flat. Every constrained value explicitly
uses ``range`` or ``allowed``; setter names with ``[]`` explicitly show that
those setters are unrestricted. The internal mapping below is
only the command chokepoint vocabulary—it is not the JSON shape.

- each configurable ``set_*`` wrapper           -> its same-named flat entry
- ``set_objective``                             -> ``objective_slot``
  (``[]`` — the default — is unrestricted: which slots exist is hardware
  knowledge the wrapper checks live; a written allow-list fences automation)
- ``select_job``                                -> handshake/state gate only
- ``move_xy`` / ``move_z``                      -> ``set_xyz``
- ``acquire``                                   -> ``acquire``

``set_origin`` fires no native command, and procedure effects flow through the
same gated movement/acquisition wrappers. They therefore need no JSON entries.

State model
-----------
This module keeps, for each connection, the limits that govern it, and refuses
any command they do not allow. It does not know where the limits come from.
The connect-time handshake that finds the operator's ``limits.json``, checks it
and installs it lives in ``driver_setup/limits/handshake.py``. If that file is
missing or invalid, the handshake installs the limits that ship with the driver,
so the session stays bounded. Only if even those are unusable does the session
go fail-closed, and every gated command then refuses with the recorded reason
(which names the notebook that creates the files:
``driver_setup/limits/notebooks/set_limits.ipynb``). A connection that never
shook hands at all still refuses fail-closed.

Single-writer invariant: like command dispatch (``confirm_loop.py``) and the
stage-envelope module global (``vendor_specific/primitive_handling/settings/limit_rules.py``), this registry assumes
ONE instrument per process. The registry is keyed by ``id(client)`` and holds
a strong reference to the client (microscope clients are process-lifetime), so an id
can never be recycled onto a different client. A second ``connect_handshake``
for the same client rebinds its state; the module-global stage envelope is
rebound by whichever handshake ran last.

Deviating from the old shared limits spec's "hang it off the driver handle"
guidance (the since-dissolved ``shared/`` package) is deliberate (plan
amendment PR-04): command wrappers receive the ``client``, not a handle, and
the enforcement must live below anything a caller can skip.
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
            + (f" The machine-local limits files are created with {setup_hint}." if setup_hint else "")
        )
    if state.limits is None:
        return f"{command} refused: {state.error}"
    try:
        state.limits.check_command(command, values)
    except (LimitsError, LimitViolation) as exc:
        return f"{command} refused: {exc}"
    return None
