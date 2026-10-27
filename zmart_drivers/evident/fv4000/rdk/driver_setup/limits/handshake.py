"""The connect-time limits handshake: find the saved limits, check them, hand them to the gate.

The gate in ``dispatcher/limits.py`` only keeps the limits of each connection
and refuses what they do not allow. This file does the part that needs this
microscope's saved settings: it finds the operator's ``limits.json``, checks
it, and installs it for the connection. If the file is missing or does not
check out, it installs the limits that ship with the driver instead, and says
so loudly, so a session is never left without limits. Only if even those
cannot be read does the session refuse every change.

Author: Thom de Hoog, Center for Microscopy and Image Analysis (ZMB),
University of Zurich (thom.dehoog@zmb.uzh.ch, thomdehoog@gmail.com).
"""

from __future__ import annotations

import logging
from typing import Any

from ...dispatcher import limits as _gate
from ...primitives.rdk.profile import limits_setup_hint
from .. import store as _machine
from . import config as _limits_config
from . import limit_rules as _rules

log = logging.getLogger(__name__)


def _build_gate_from_file(
    client: Any, limits_file: Any, *, source: str, is_fallback: bool
) -> _gate.GateState:
    """Check one limits.json, apply its travel and install it for *client*; raises when it is not acceptable."""
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
    """Govern the session with the bundled defaults, and warn: they are not this microscope's measured travel."""
    state = _build_gate_from_file(
        client,
        machine.bundled_default_path(_machine.LIMITS_FILENAME),
        source="defaults",
        is_fallback=True,
    )
    log.warning(
        "limits fallback: %s. The session is governed by the bundled DEFAULT limits (%s), not this "
        "microscope's measured travel. They are placeholders around FLUOVIEW's zero and may be wider or "
        "narrower than the stage: measure and publish the limits with %s.",
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
    """Find, check and install this microscope's limits for *client*. Never raises.

    With *load* False the saved file is skipped and the defaults govern. A
    saved file that does not validate, or that reaches outside the physical
    backstop, is replaced whole by the defaults, with a warning. Answers the
    gate's status, which says where the limits in force came from.
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
            else machine.limits_path()
        )
        state = _build_gate_from_file(
            client, limits_file, source="machine", is_fallback=False
        )
    except Exception as exc:  # noqa: BLE001 -- a file problem falls back to the defaults, never stops the connect
        try:
            return _gate.status(
                _install_default_limits(
                    client,
                    machine,
                    f"this microscope's limits.json could not be used: {exc}",
                )
            )
        except Exception as default_exc:  # noqa: BLE001 -- even the shipped defaults are broken: refuse everything
            error = (
                f"the limits handshake failed and the bundled defaults are unusable ({default_exc}); the "
                f"microscope's file: {exc}. Every change refuses until the limits files validate (see {limits_setup_hint})."
            )
            log.error("%s", error)
            state = _gate.GateState(limits=None, stage_cfg=None, error=error)
            _gate.install(client, state)
            return _gate.status(state)
    log.info("limits handshake ok: %s governs this session", state.limits.describe())
    return _gate.status(state)
