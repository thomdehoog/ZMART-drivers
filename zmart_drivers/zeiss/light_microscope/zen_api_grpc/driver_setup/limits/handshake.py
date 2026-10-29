"""The connect-time limits handshake: find the saved limits, check them, hand them to the gate.

The gate in ``dispatcher/limits.py`` only keeps the limits of each connection
and refuses what they do not allow. This file does the part that needs this
microscope's saved settings: it finds the operator's ``limits.json``, checks
it, and installs it for the connection. If the file is missing or does not
check out, it installs the limits that ship with the driver instead, and
says so loudly, so a session is never left without limits.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ...dispatcher import limits as _gate
from ...primitives.gateway.profile import limits_setup_hint
from .. import store as _machine
from . import config as _limits_config
from . import limit_rules as _rules

log = logging.getLogger(__name__)


def _build_gate_from_file(
    client: Any, limits_file: Any, *, source: str, is_fallback: bool
) -> _gate.GateState:
    """Check one limits.json, apply its envelope, and install the gate; raises on any problem."""
    stage_cfg = _limits_config.load(limits_path=limits_file)
    limits = _rules.build_limits(
        stage_cfg, source=source, path=limits_file, is_fallback=is_fallback
    )
    state = _gate.GateState(
        limits=limits, stage_cfg=_gate.freeze(stage_cfg), error=None
    )
    _gate.install(client, state)
    return state


def _install_default_limits(client: Any, machine: Any, reason: str) -> _gate.GateState:
    """Govern the session with the bundled default limits, loudly.

    The defaults are placeholders, not this microscope's measured travel:
    they may be wider or narrower than the real stage. The backstop still
    bounds every move. The warning says to publish measured limits.
    """
    default_file = machine.bundled_default_path(_machine.LIMITS_FILENAME)
    state = _build_gate_from_file(
        client, default_file, source="defaults", is_fallback=True
    )
    log.warning(
        "limits fallback: %s. The session is governed by the bundled DEFAULT limits (%s), which are "
        "placeholders and NOT this microscope's measured travel. Publish measured limits with %s.",
        reason,
        state.limits.describe(),
        limits_setup_hint,
    )
    return state


def connect_handshake(
    client: Any,
    *,
    machine: Any = None,
    stage_limits_path: Any = None,
    load: bool = True,
):
    """Resolve, check and install this connection's limits. Never raises.

    With *load* False the saved file is skipped and the defaults govern. Else
    the newest published ``limits.json`` is loaded (or *stage_limits_path*
    when given); a file that does not check out is replaced by the defaults,
    with a warning. Only when even the defaults cannot be loaded does the
    session refuse every change, with the recorded reason.
    """
    machine = machine if machine is not None else _machine.MACHINE
    if not load:
        return _gate.status(
            _install_default_limits(
                client, machine, "limits loading was skipped for this connection"
            )
        )
    try:
        limits_file = (
            stage_limits_path
            if stage_limits_path is not None
            else machine.require_machine_local(
                _machine.LIMITS_FILENAME, "the machine limits"
            )
        )
        state = _build_gate_from_file(
            client, limits_file, source="machine", is_fallback=False
        )
    except Exception as why:  # noqa: BLE001 -- a file problem falls back to the defaults, never crashes connect
        try:
            return _gate.status(
                _install_default_limits(
                    client, machine, f"the machine limits.json did not validate: {why}"
                )
            )
        except Exception as default_why:  # noqa: BLE001 -- shipped defaults broken: fail closed
            error = (
                f"the limits handshake failed and the bundled defaults are unusable ({default_why}); "
                f"machine file error: {why}. Every change refuses until the limits files validate (see {limits_setup_hint})."
            )
            log.error("%s", error)
            state = _gate.GateState(limits=None, stage_cfg=None, error=error)
            _gate.install(client, state)
            return _gate.status(state)
    log.info("limits handshake ok: %s governs this session", state.limits.describe())
    return _gate.status(state)
