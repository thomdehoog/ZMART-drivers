"""The connect-time limits handshake: find the saved limits, check them, hand them to the gate.

The gate in ``dispatcher/gate.py`` only keeps the limits of each connection and
refuses what they do not allow. This file does the part that needs this
microscope's saved settings: it finds the operator's ``limits.json``, checks it,
and installs it for the connection. If the file is missing or does not check
out, it installs the limits that ship with the driver instead, and says so
loudly, so a session is never left without limits.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ...dispatcher import gate as _gate
from ...vendor_specific.primitive_handling.commands import limit_rules as _rules
from ...vendor_specific.primitive_handling.commands.limit_rules import NOTEBOOK_POINTER
from .. import store as _machine
from . import config as _limits_config

log = logging.getLogger(__name__)


def _build_gate_from_file(
    client: Any,
    limits_file: Any,
    *,
    source: str,
    is_fallback: bool,
) -> _gate.GateState:
    """Validate one limits.json, apply its envelope, and install the gate.

    Shared by the normal machine-file path and the bundled-defaults fallback.
    Raises on any validation problem so the caller can decide how to fall back.
    """
    # The limits config validates the complete flat file in one read.
    stage_cfg = _limits_config.load(limits_path=limits_file)
    function_limits = _rules.build_limits(
        stage_cfg, source=source, path=limits_file, is_fallback=is_fallback
    )
    state = _gate.GateState(
        limits=function_limits, stage_cfg=_gate.freeze(stage_cfg), error=None
    )
    _gate.install(client, state)
    return state


def _install_default_limits(client: Any, machine: Any, reason: str) -> _gate.GateState:
    """Govern the session with the driver's bundled DEFAULT limits (loud).

    Used when limits loading is deliberately skipped for a connection, or when
    the machine-local ``limits.json`` does not validate. The bundled default
    envelope sits within the physical backstop, so the session stays usable and
    bounded rather than fail-closed — but it is emphatically NOT the operator's
    measured envelope, so this warns clearly and marks the limits as a fallback.
    """
    default_file = machine.bundled_default_path(_machine.LIMITS_FILENAME)
    state = _build_gate_from_file(
        client,
        default_file,
        source="defaults",
        is_fallback=True,
    )
    log.warning(
        "limits fallback: %s — the session is governed by the bundled DEFAULT "
        "limits (%s), NOT this machine's measured envelope. The conservative "
        "defaults may be WIDER than the envelope this "
        "machine's own limits.json intended: do not rely on your published "
        "limits until the file validates again. The hardcoded physical backstop "
        "still bounds every move. Publish measured limits with %s.",
        reason,
        state.limits.describe(),
        NOTEBOOK_POINTER,
    )
    return state


def connect_handshake(
    client: Any,
    *,
    machine: Any = None,
    stage_limits_path: Any = None,
    load: bool = True,
):
    """The connect-time limits handshake: resolve, validate, install. Never raises.

    Steps:

    1. When ``load`` is False, the machine file is skipped and the session is
       governed by the bundled DEFAULT limits (see below). Otherwise the single
       ``limits.json`` resolves through ProgramData. If none has been published,
       the bundled defaults are used directly and are never copied into the
       machine tree. A published file must contain exactly the flat keys shown by the
       limits notebook and finite ranges with min <= max; ``objective_slot``
       is ``[]`` (unrestricted) or a non-empty non-negative allow-list.
       ``stage_limits_path`` overrides the resolution
       with an explicit operator-chosen file (still validated); it is only
       consulted when ``load`` is True — with ``load=False`` the explicit path
       is ignored and the defaults govern.
    2. The envelope must sit WITHIN the hardcoded physical backstop
       (``limits.checks.STAGE_BACKSTOP_UM``).
    3. The validated flat policy is installed directly in the commands-layer
       gate; no second limits schema is created. Unknown, missing, or legacy
       metadata/nesting is rejected rather than silently ignored.
    4. On success: apply the stage envelope (module-global, single instrument
       per process) and install the gate state for *client*.

    Defaults fallback (operator decision): if loading is skipped (``load``
    False) or the machine ``limits.json`` fails to validate, the session is
    NOT left fail-closed. Instead the bundled DEFAULT limits are installed
    (marked ``is_fallback`` and loudly warned). The bundled defaults sit within
    the physical backstop, and the backstop bounds every move regardless, so
    the session stays bounded and usable while the operator fixes the file.
    Note the chosen trade-off: an invalid file is rejected WHOLE, so a
    deliberately NARROW machine envelope is replaced by the (wider) defaults
    until the file validates again — the fallback warning says so. Only if
    even the shipped defaults fail to load does the session go fail-closed
    (every mutating command then refuses with the recorded error).
    """
    machine = machine if machine is not None else _machine.MACHINE

    if not load:
        return _gate.status(
            _install_default_limits(
                client, machine, "limits loading was skipped for this connection"
            )
        )

    try:
        if stage_limits_path is not None:
            limits_file = stage_limits_path
            source = "machine"
        else:
            limits_file = machine.require_machine_local(
                _machine.LIMITS_FILENAME, "the machine limits"
            )
            source = "machine"
        state = _build_gate_from_file(
            client,
            limits_file,
            source=source,
            is_fallback=False,
        )
    except Exception as exc:  # noqa: BLE001 -- config IO / schema; fall back to defaults, never crash connect
        try:
            return _gate.status(
                _install_default_limits(
                    client, machine, f"the machine limits.json did not validate: {exc}"
                )
            )
        except Exception as default_exc:  # noqa: BLE001 -- shipped defaults broken: last-resort fail-closed
            error = (
                f"limits handshake failed and the bundled defaults are unusable "
                f"({default_exc}); machine file error: {exc} — every mutating command "
                f"refuses until the limits files validate (see {NOTEBOOK_POINTER})."
            )
            log.error("%s", error)
            state = _gate.GateState(limits=None, stage_cfg=None, error=error)
            _gate.install(client, state)
            # A previous handshake's module-global stage envelope (if any) is
            # deliberately left in place: it is unreachable for moves, because
            # every mutating wrapper checks this fail-closed gate state first
            # and refuses before the envelope is ever consulted.
            return _gate.status(state)

    log.info(
        "limits handshake ok: %s governs this session",
        state.limits.describe(),
    )
    return _gate.status(state)
